"""Per-host manager: heartbeat singletons, metrics API, and background poller.

Initialized by either app.py (central console) or kiosk_server.py
(per-host manager). Provides shared singletons and API registration
that work identically in both contexts.

When running as a per-host Manager (kiosk_server), this module also:
  - Accepts container heartbeats via ``POST /api/checkin``
  - Relays a single host-level heartbeat UP to the SuperManager

The node_resolver is pluggable:
  - app.py supplies a resolver that reads from .env / test.env
  - kiosk_server.py supplies a resolver that reads from config.json NODE_IPS
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Callable

from scripts.webui import heartbeat

_subscription_mgr: heartbeat.SubscriptionManager | None = None
_metric_cache: heartbeat.MetricCache | None = None
_node_resolver: Callable[[str], str | None] | None = None
_auth_validator: Callable[[str], bool] | None = None

_COLLECTOR_MAP: dict[str, Any] = {
    "wifi": heartbeat.collect_wifi_metrics,
    "bridge": heartbeat.collect_bridge_metrics,
    "router": heartbeat.collect_router_metrics,
    "mesh": heartbeat.collect_mesh_metrics,
    "batman": heartbeat.collect_batman_metrics,
}


def init(
    node_resolver: Callable[[str], str | None],
    auth_validator: Callable[[str], bool] | None = None,
) -> None:
    """Initialize manager singletons with a pluggable node resolver.

    auth_validator: optional callback that takes the X-Callhome-Token header
    value and returns True if the request is authorized.  When None, mutation
    endpoints are open (appropriate for single-user/local-only deployments).

    Safe to call multiple times (resets state on each call).
    """
    global _subscription_mgr, _metric_cache, _node_resolver, _auth_validator
    _subscription_mgr = heartbeat.SubscriptionManager()
    _metric_cache = heartbeat.MetricCache()
    _node_resolver = node_resolver
    _auth_validator = auth_validator


def reset() -> None:
    """Reset all singletons. Used by tests for clean state between runs."""
    global _subscription_mgr, _metric_cache, _node_resolver, _auth_validator
    _subscription_mgr = None
    _metric_cache = None
    _node_resolver = None
    _auth_validator = None
    _container_checkins.clear()


def get_subscription_manager() -> heartbeat.SubscriptionManager:
    """Return the global SubscriptionManager (fails if init() not called)."""
    if _subscription_mgr is None:
        raise RuntimeError("manager.init() has not been called")
    return _subscription_mgr


def get_metric_cache() -> heartbeat.MetricCache:
    """Return the global MetricCache (fails if init() not called)."""
    if _metric_cache is None:
        raise RuntimeError("manager.init() has not been called")
    return _metric_cache


def resolve_node_ip(node_id: str) -> str | None:
    """Resolve a node_id to an IP via the configured resolver."""
    if _node_resolver is None:
        return None
    return _node_resolver(node_id)


def _get_host_ip() -> str:
    """Retrieve HOST_IP from app storage or environment."""
    import os
    try:
        from nicegui import app
        ip = app.storage.general.get("host_ip", "")
        if ip:
            return ip
    except (ImportError, RuntimeError):
        pass
    return os.environ.get("HOST_IP", "")


def _get_mesh_key() -> str:
    """Retrieve MESH_KEY from app storage or environment."""
    import os
    try:
        from nicegui import app
        key = app.storage.general.get("mesh_key", "")
        if key:
            return key
    except (ImportError, RuntimeError):
        pass
    return os.environ.get("MESH_KEY", "")


def _get_host_name() -> str:
    """Retrieve HOST_NAME from app storage or environment."""
    import os
    try:
        from nicegui import app
        name = app.storage.general.get("host_name", "")
        if name:
            return name
    except (ImportError, RuntimeError):
        pass
    return os.environ.get("HOST_NAME", "")


def _get_management_server() -> str:
    """Retrieve MANAGEMENT_SERVER (SuperManager URL) from app storage."""
    try:
        from nicegui import app
        return app.storage.general.get("management_server", "")
    except (ImportError, RuntimeError):
        return ""


def _get_callhome_public_key() -> str:
    """Retrieve CALLHOME_PUBLIC_KEY from app storage or environment."""
    import os
    try:
        from nicegui import app
        key = app.storage.general.get("callhome_public_key", "")
        if key:
            return key
    except (ImportError, RuntimeError):
        pass
    return os.environ.get("CALLHOME_PUBLIC_KEY", "")


# ── Container heartbeat store ────────────────────────────────────────
# Containers on this physical unit POST their health here.
# The relay loop bundles these into the host-level heartbeat sent
# UP to the SuperManager.

_container_checkins: dict[str, dict] = {}


def get_container_checkins() -> dict[str, dict]:
    """Return the current container heartbeat store (for tests)."""
    return _container_checkins


def clear_container_checkins() -> None:
    """Clear the container heartbeat store (for tests)."""
    _container_checkins.clear()


# ── Background poller ────────────────────────────────────────────────


async def _heartbeat_poller() -> None:
    """Background task that collects metrics for active subscriptions."""
    log = logging.getLogger("vm_builds.heartbeat")
    mgr = get_subscription_manager()
    cache = get_metric_cache()

    while True:
        try:
            mgr.cleanup_expired()
            active = mgr.get_active_nodes()
            for node_id, metric_type in active:
                ip = resolve_node_ip(node_id)
                if not ip:
                    continue
                collector = _COLLECTOR_MAP.get(metric_type)
                if not collector:
                    continue
                try:
                    result = await asyncio.to_thread(collector, ip)
                    result.node_id = node_id
                    cache.store(result)
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    log.warning("Collector %s for %s failed: %s", metric_type, node_id, exc)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            log.error("Heartbeat poller error: %s", exc)
        await asyncio.sleep(5)


# ── Host-level relay to SuperManager ─────────────────────────────────


def _collect_host_metrics(host_ip: str) -> dict:
    """Collect disk/memory/uptime/guests from the Proxmox host via SSH."""
    metrics: dict[str, Any] = {
        "disk_usage_pct": 0,
        "memory_usage_pct": 0,
        "uptime_seconds": 0,
        "services": [],
    }
    ok, out = heartbeat._ssh_exec(
        host_ip,
        "df / | awk 'NR==2{print $5}'; "
        "free | awk 'NR==2{printf \"%.0f\\n\", $3/$2*100}'; "
        "awk '{print int($1)}' /proc/uptime",
        timeout=10,
    )
    if ok:
        lines = out.strip().splitlines()
        if len(lines) >= 3:
            try:
                metrics["disk_usage_pct"] = int(lines[0].replace("%", ""))
                metrics["memory_usage_pct"] = int(lines[1])
                metrics["uptime_seconds"] = int(lines[2])
            except (ValueError, IndexError):
                pass

    ok_ct, ct_out = heartbeat._ssh_exec(
        host_ip, "pct list 2>/dev/null || true", timeout=10,
    )
    if ok_ct and ct_out:
        for line in ct_out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                status = parts[1].lower()
                metrics["services"].append(f"ct:{parts[0]}:{parts[2]}:{status}")

    ok_vm, vm_out = heartbeat._ssh_exec(
        host_ip, "qm list 2>/dev/null || true", timeout=10,
    )
    if ok_vm and vm_out:
        for line in vm_out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                status = parts[1].lower()
                metrics["services"].append(f"vm:{parts[0]}:{parts[2]}:{status}")

    return metrics


def build_relay_payload(
    host_name: str,
    host_ip: str,
    host_metrics: dict,
    container_checkins: dict[str, dict],
) -> dict:
    """Build the host-level heartbeat payload for the SuperManager.

    This is a standard NodeCheckin payload so the SuperManager's existing
    ``/api/checkin`` handler works unchanged. Container health is nested
    inside ``container_health.extensions.containers``.
    """
    containers_summary: dict[str, dict] = {}
    for ct_name, ct_data in container_checkins.items():
        payload = ct_data.get("payload", {})
        containers_summary[ct_name] = {
            "ready": bool(
                payload.get("container_health", {}).get("ready", False)
                if payload.get("container_health") else False
            ),
            "disk_pct": payload.get("disk_usage_pct", 0),
            "mem_pct": payload.get("memory_usage_pct", 0),
            "uptime": payload.get("uptime_seconds", 0),
            "last_seen": ct_data.get("received_at", ""),
        }

    return {
        "node_id": host_name,
        "hostname": host_name,
        "local_ips": [host_ip] if host_ip else [],
        "uptime_seconds": host_metrics.get("uptime_seconds", 0),
        "disk_usage_pct": host_metrics.get("disk_usage_pct", 0),
        "memory_usage_pct": host_metrics.get("memory_usage_pct", 0),
        "services": host_metrics.get("services", []),
        "version": "1.0",
        "container_health": {
            "container_id": host_name,
            "ready": True,
            "systemd_services": {},
            "listening_ports": [],
            "extensions": {
                "containers": containers_summary,
            },
        },
    }


async def handle_container_checkin(request: "StarletteRequest") -> "JSONResponse":
    """Accept heartbeats from containers on this physical unit.

    Module-level so it's importable for testing. Only mounted as a route
    when running as a per-host Manager (MANAGEMENT_SERVER is set).
    """
    from starlette.responses import JSONResponse as _JSONResp
    try:
        body = await request.json()
    except (ValueError, TypeError, _json.JSONDecodeError):
        return _JSONResp({"error": "Invalid JSON"}, status_code=400)
    hostname = body.get("hostname", body.get("node_id", ""))
    if not hostname:
        return _JSONResp({"error": "Missing hostname/node_id"}, status_code=400)
    _container_checkins[hostname] = {
        "payload": body,
        "received_at": datetime.now().isoformat(),
    }
    return _JSONResp({"status": "ok"})


def _post_to_supermanager(url: str, payload: dict, token: str = "") -> bool:
    """POST a heartbeat payload to the SuperManager. Returns True on success."""
    log = logging.getLogger("vm_builds.relay")
    body = _json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Callhome-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as exc:
        log.warning("Relay HTTP error %s: %s", exc.code, exc.read().decode()[:200])
        return False
    except (urllib.error.URLError, OSError) as exc:
        log.warning("Relay network error: %s", exc)
        return False


async def _relay_heartbeat() -> None:
    """Background task: collect host metrics + relay UP to SuperManager.

    Runs every 30s. Only active when MANAGEMENT_SERVER is configured
    (i.e., running as a per-host Manager, not as the SuperManager itself).
    """
    log = logging.getLogger("vm_builds.relay")
    await asyncio.sleep(5)

    while True:
        try:
            mgmt_server = _get_management_server()
            if not mgmt_server:
                await asyncio.sleep(30)
                continue

            host_name = _get_host_name()
            host_ip = _get_host_ip()
            if not host_name or not host_ip:
                log.warning("HOST_NAME or HOST_IP not configured, skipping relay")
                await asyncio.sleep(30)
                continue

            host_metrics = await asyncio.to_thread(_collect_host_metrics, host_ip)

            payload = build_relay_payload(
                host_name, host_ip, host_metrics, _container_checkins,
            )

            token = _get_callhome_public_key()
            url = f"{mgmt_server.rstrip('/')}/api/checkin"
            ok = await asyncio.to_thread(_post_to_supermanager, url, payload, token)
            if ok:
                log.debug("Relayed heartbeat for %s to %s", host_name, mgmt_server)
            else:
                log.warning("Failed to relay heartbeat for %s to %s", host_name, mgmt_server)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            log.error("Relay heartbeat error: %s", exc)
        await asyncio.sleep(30)


def start_poller() -> None:
    """Launch background tasks: heartbeat poller + relay (if Manager)."""
    asyncio.create_task(_heartbeat_poller())
    mgmt_server = _get_management_server()
    if mgmt_server:
        asyncio.create_task(_relay_heartbeat())


# ── API registration ─────────────────────────────────────────────────


def _check_mutation_auth(request: Any) -> Any | None:
    """Validate auth on mutation endpoints. Returns JSONResponse on failure."""
    from starlette.responses import JSONResponse
    if _auth_validator is None:
        return None
    token = request.headers.get("x-callhome-token", "")
    if not _auth_validator(token):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    return None


def register_api(starlette_app: Any) -> None:
    """Register heartbeat REST endpoints on a Starlette/NiceGUI app.

    Works with both the full app.py and the kiosk_server.py entry points.
    """
    import json as _json

    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    mgr = get_subscription_manager()
    cache = get_metric_cache()

    async def _api_heartbeat_subscribe(request: StarletteRequest) -> JSONResponse:
        try:
            body = await request.json()
            node_id = body["node_id"]
            metric_type = body.get("metric_type", "wifi")
            ttl = float(body.get("ttl", 30))
        except (KeyError, TypeError, _json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"error": f"Invalid payload: {exc}"}, status_code=400)

        if metric_type not in _COLLECTOR_MAP:
            return JSONResponse(
                {"error": f"Unknown metric_type: {metric_type}"},
                status_code=400,
            )

        ip = resolve_node_ip(node_id)
        if not ip:
            return JSONResponse(
                {"error": f"Unknown node: {node_id}"},
                status_code=404,
            )

        sub = mgr.subscribe(node_id, metric_type, ttl)
        return JSONResponse({
            "subscription_id": sub.subscription_id,
            "node_id": sub.node_id,
            "metric_type": sub.metric_type,
            "expires_at": sub.expires_at.isoformat(timespec="seconds"),
        })

    async def _api_heartbeat_unsubscribe(request: StarletteRequest) -> JSONResponse:
        sub_id = request.path_params.get("subscription_id", "")
        removed = mgr.unsubscribe(sub_id)
        return JSONResponse({"removed": removed})

    async def _api_heartbeat_metrics(request: StarletteRequest) -> JSONResponse:
        node_id = request.path_params.get("node_id", "")
        metric_type = request.path_params.get("metric_type", "")
        cached = cache.get(node_id, metric_type)
        if cached is None:
            return JSONResponse({"error": "No data available"}, status_code=404)
        return JSONResponse({
            "node_id": cached.node_id,
            "metric_type": cached.metric_type,
            "data": cached.data,
            "collected_at": cached.collected_at,
            "success": cached.success,
            "error": cached.error,
        })

    async def _api_heartbeat_subscriptions(request: StarletteRequest) -> JSONResponse:
        subs = mgr.list_subscriptions()
        return JSONResponse([
            {
                "subscription_id": s.subscription_id,
                "node_id": s.node_id,
                "metric_type": s.metric_type,
                "expires_at": s.expires_at.isoformat(timespec="seconds"),
            }
            for s in subs
        ])

    async def _api_bridge_restart_wifi(request: StarletteRequest) -> JSONResponse:
        """Restart WiFi on bridge nodes via SSH."""
        auth_err = _check_mutation_auth(request)
        if auth_err:
            return auth_err
        from scripts.webui.data import get_bridge_nodes
        body = {}
        try:
            body = await request.json()
        except (ValueError, TypeError):
            pass
        target = body.get("target", "all")
        nodes = get_bridge_nodes()
        if target == "sta":
            nodes = [n for n in nodes if n["default_role"] == "sta"]

        results = {}
        for node in nodes:
            ip = resolve_node_ip(node["node_id"])
            if not ip:
                results[node["node_id"]] = {"success": False, "error": "IP not resolved"}
                continue
            ok, output = heartbeat._ssh_exec(ip, "/usr/local/bin/wifi_setup.sh restart", timeout=15)
            results[node["node_id"]] = {"success": ok, "output": output[:200]}
        return JSONResponse(results)

    async def _api_batman_enable(request: StarletteRequest) -> JSONResponse:
        auth_err = _check_mutation_auth(request)
        if auth_err:
            return auth_err
        return await _batman_toggle(request, "enable")

    async def _api_batman_disable(request: StarletteRequest) -> JSONResponse:
        auth_err = _check_mutation_auth(request)
        if auth_err:
            return auth_err
        return await _batman_toggle(request, "disable")

    async def _batman_toggle(request: StarletteRequest, action: str) -> JSONResponse:
        """Enable or disable batman-adv on all mesh/bridge nodes."""
        import asyncio as _asyncio
        import hashlib
        import hmac as _hmac

        mesh_key = _get_mesh_key()
        if not mesh_key:
            return JSONResponse(
                {"error": "MESH_KEY not configured"}, status_code=500,
            )

        from scripts.webui.data import get_mesh_nodes, get_bridge_nodes
        mesh_ap, mesh_stas = get_mesh_nodes()
        bridge_nodes_list = get_bridge_nodes()
        all_node_ids = [mesh_ap] + mesh_stas + [n["node_id"] for n in bridge_nodes_list]

        token = _hmac.new(
            mesh_key.encode(), f"{action}_batman".encode(), hashlib.sha256,
        ).hexdigest()

        results = {}
        for node_id in all_node_ids:
            ip = resolve_node_ip(node_id)
            if not ip:
                results[node_id] = {"success": False, "error": "IP not resolved"}
                continue
            ok, out = await _asyncio.to_thread(
                heartbeat._ssh_exec, ip,
                f"/usr/local/bin/batman_trigger.sh {action} {token}",
                timeout=30,
            )
            results[node_id] = {"success": ok, "output": out[:300]}

        if action == "enable":
            await _asyncio.sleep(10)
            for node_id in all_node_ids:
                ip = resolve_node_ip(node_id)
                if not ip:
                    continue
                ok, out = await _asyncio.to_thread(
                    heartbeat._ssh_exec, ip,
                    "/usr/local/bin/batman_trigger.sh status",
                    timeout=10,
                )
                if ok:
                    results[node_id]["status_check"] = out[:300]

        ok_count = sum(1 for r in results.values() if r.get("success"))
        return JSONResponse({
            "action": action,
            "total": len(all_node_ids),
            "succeeded": ok_count,
            "results": results,
        })

    async def _api_batman_status(request: StarletteRequest) -> JSONResponse:
        """Return batman status from all mesh nodes."""
        import asyncio as _asyncio
        from scripts.webui.data import get_mesh_nodes, get_bridge_nodes
        mesh_ap, mesh_stas = get_mesh_nodes()
        bridge_nodes_list = get_bridge_nodes()
        all_node_ids = [mesh_ap] + mesh_stas + [n["node_id"] for n in bridge_nodes_list]

        statuses = {}
        for node_id in all_node_ids:
            ip = resolve_node_ip(node_id)
            if not ip:
                statuses[node_id] = {"active": False, "error": "IP not resolved"}
                continue
            ok, out = await _asyncio.to_thread(
                heartbeat._ssh_exec, ip,
                "/usr/local/bin/batman_trigger.sh status",
                timeout=10,
            )
            if ok:
                active = "BATMAN=active" in out
                originators = heartbeat._parse_batman_originators(out)
                interfaces = heartbeat._parse_batman_interfaces(
                    out.split("---INTERFACES---")[1] if "---INTERFACES---" in out else ""
                )
                statuses[node_id] = {
                    "active": active,
                    "originators": originators,
                    "interfaces": interfaces,
                }
            else:
                statuses[node_id] = {"active": False, "error": out[:200]}
        return JSONResponse(statuses)

    # ── WiFi mode management endpoints ──────────────────────────

    async def _api_wifi_mode(request: StarletteRequest) -> JSONResponse:
        """Switch a WiFi container between AP and STA mode."""
        auth_err = _check_mutation_auth(request)
        if auth_err:
            return auth_err
        node_id = request.path_params.get("node", "")
        mode = request.path_params.get("mode", "")
        if mode not in ("ap", "sta"):
            return JSONResponse(
                {"error": f"Invalid mode: {mode} (expected ap or sta)"},
                status_code=400,
            )
        ip = resolve_node_ip(node_id)
        if not ip:
            return JSONResponse(
                {"error": f"Unknown node: {node_id}"}, status_code=404,
            )
        import asyncio as _asyncio
        ok, out = await _asyncio.to_thread(
            heartbeat._ssh_exec, ip,
            f"/usr/local/bin/wifi_setup.sh switch-mode {mode}",
            timeout=30,
        )
        return JSONResponse({
            "node_id": node_id, "mode": mode,
            "success": ok, "output": out[:300],
        })

    async def _api_wifi_status(request: StarletteRequest) -> JSONResponse:
        """Query WiFi status from a container via wifi_setup.sh status."""
        node_id = request.path_params.get("node", "")
        ip = resolve_node_ip(node_id)
        if not ip:
            return JSONResponse(
                {"error": f"Unknown node: {node_id}"}, status_code=404,
            )
        import asyncio as _asyncio
        ok, out = await _asyncio.to_thread(
            heartbeat._ssh_exec, ip,
            "/usr/local/bin/wifi_setup.sh status",
            timeout=10,
        )
        if not ok:
            return JSONResponse(
                {"node_id": node_id, "error": out[:200]}, status_code=502,
            )
        status: dict[str, str] = {}
        for line in out.strip().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                status[k.strip().lower()] = v.strip()
        return JSONResponse({"node_id": node_id, **status})

    starlette_app.routes.insert(0, Route(
        "/api/wifi/mode/{node}/{mode}",
        _api_wifi_mode, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/wifi/status/{node}",
        _api_wifi_status, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/enable", _api_batman_enable, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/disable", _api_batman_disable, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/status", _api_batman_status, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/bridge/restart-wifi", _api_bridge_restart_wifi, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/heartbeat/subscribe", _api_heartbeat_subscribe, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/heartbeat/subscribe/{subscription_id}",
        _api_heartbeat_unsubscribe, methods=["DELETE"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/heartbeat/{node_id}/{metric_type}",
        _api_heartbeat_metrics, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/heartbeat/subscriptions",
        _api_heartbeat_subscriptions, methods=["GET"],
    ))

    # ── Container / VM management endpoints ──────────────────────

    async def _api_guests(request: StarletteRequest) -> JSONResponse:
        """List all containers and VMs on the Proxmox host."""
        import asyncio as _asyncio
        host_ip = _get_host_ip()
        if not host_ip:
            return JSONResponse(
                {"error": "HOST_IP not configured"}, status_code=500,
            )

        guests: list[dict] = []
        ok_ct, ct_out = await _asyncio.to_thread(
            heartbeat._ssh_exec, host_ip, "pct list 2>/dev/null || true",
            timeout=10,
        )
        if ok_ct and ct_out:
            for line in ct_out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3:
                    guests.append({
                        "vmid": parts[0], "type": "lxc",
                        "status": parts[1], "name": parts[2],
                    })

        ok_vm, vm_out = await _asyncio.to_thread(
            heartbeat._ssh_exec, host_ip, "qm list 2>/dev/null || true",
            timeout=10,
        )
        if ok_vm and vm_out:
            for line in vm_out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3:
                    guests.append({
                        "vmid": parts[0], "type": "qemu",
                        "status": parts[1], "name": parts[2],
                    })

        return JSONResponse({"guests": guests})

    async def _api_guest_action(request: StarletteRequest) -> JSONResponse:
        """Start, stop, or restart a container or VM."""
        vmid = request.path_params.get("vmid", "")
        action = request.path_params.get("action", "")
        if not vmid.isdigit():
            return JSONResponse(
                {"error": f"Invalid VMID: {vmid}"}, status_code=400,
            )
        if action not in ("start", "stop", "restart"):
            return JSONResponse(
                {"error": f"Invalid action: {action}"}, status_code=400,
            )
        auth_err = _check_mutation_auth(request)
        if auth_err:
            return auth_err
        import asyncio as _asyncio
        host_ip = _get_host_ip()
        if not host_ip:
            return JSONResponse(
                {"error": "HOST_IP not configured"}, status_code=500,
            )

        ok_type, type_out = await _asyncio.to_thread(
            heartbeat._ssh_exec, host_ip,
            f"pct status {vmid} 2>/dev/null && echo LXC || qm status {vmid} 2>/dev/null && echo QEMU",
            timeout=10,
        )
        is_lxc = "LXC" in type_out if ok_type else False
        tool = "pct" if is_lxc else "qm"

        if action == "restart":
            cmd = f"{tool} stop {vmid} 2>/dev/null; sleep 2; {tool} start {vmid}"
        else:
            cmd = f"{tool} {action} {vmid}"

        ok, out = await _asyncio.to_thread(
            heartbeat._ssh_exec, host_ip, cmd, timeout=30,
        )
        return JSONResponse({
            "vmid": vmid, "action": action,
            "success": ok, "output": out[:300],
        })

    # ── Container heartbeat ingestion (Manager-only) ────────────
    # Only register /api/checkin when running as a per-host Manager
    # (MANAGEMENT_SERVER is set). The SuperManager (app.py) has its own
    # /api/checkin handler that writes to nodes.json.

    if _get_management_server():
        starlette_app.routes.insert(0, Route(
            "/api/checkin", handle_container_checkin, methods=["POST"],
        ))

    starlette_app.routes.insert(0, Route(
        "/api/guests", _api_guests, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/guests/{vmid}/{action}",
        _api_guest_action, methods=["POST"],
    ))

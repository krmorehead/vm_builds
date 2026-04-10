"""Manager hierarchy for the 4-tier fleet architecture.

Inheritance:
    BaseManager — heartbeat polling, metric cache, SSH helper
        NodeManager — single-host ops, local container batman, guest mgmt
            ClusterManager — subnet-scoped fleet view, event broadcast
                (SuperManager is app.py calling ClusterManager with global visibility)

kiosk_server.py instantiates NodeManager (default) or ClusterManager
(when IS_CLUSTER_MANAGER=true in config.json).
app.py instantiates ClusterManager with a fleet-level node resolver.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
from datetime import datetime
from typing import Any, Callable

from scripts.webui import heartbeat
from scripts.webui.data import event_bus


# ── Base Manager ─────────────────────────────────────────────────────


class BaseManager:
    """Shared infrastructure: subscriptions, metric cache, node resolution.

    Config values are REQUIRED at construction. No silent fallbacks.
    """

    def __init__(
        self,
        node_resolver: Callable[[str], str | None],
        *,
        auth_validator: Callable[[str], bool] | None = None,
        host_ip: str = "",
        host_name: str = "",
        management_server: str = "",
        callhome_public_key: str = "",
        mesh_key: str = "",
    ) -> None:
        self.subscription_mgr = heartbeat.SubscriptionManager()
        self.metric_cache = heartbeat.MetricCache()
        self._node_resolver = node_resolver
        self._auth_validator = auth_validator
        self._host_ip = host_ip
        self._host_name = host_name
        self._management_server = management_server
        self._callhome_public_key = callhome_public_key
        self._mesh_key = mesh_key
        self._container_checkins: dict[str, dict] = {}
        self._collector_map: dict[str, Any] = {
            "wifi": heartbeat.collect_wifi_metrics,
            "bridge": heartbeat.collect_bridge_metrics,
            "router": heartbeat.collect_router_metrics,
            "mesh": heartbeat.collect_mesh_metrics,
            "batman": heartbeat.collect_batman_metrics,
        }

    @property
    def host_ip(self) -> str:
        return self._host_ip

    @property
    def host_name(self) -> str:
        return self._host_name

    @property
    def management_server(self) -> str:
        return self._management_server

    @property
    def callhome_public_key(self) -> str:
        return self._callhome_public_key

    @property
    def mesh_key(self) -> str:
        return self._mesh_key

    def resolve_node_ip(self, node_id: str) -> str | None:
        return self._node_resolver(node_id)

    def check_mutation_auth(self, request: Any) -> Any | None:
        from starlette.responses import JSONResponse
        if self._auth_validator is None:
            return None
        token = request.headers.get("x-callhome-token", "")
        if not self._auth_validator(token):
            return JSONResponse({"error": "unauthorized"}, status_code=403)
        return None

    def get_container_checkins(self) -> dict[str, dict]:
        return self._container_checkins

    def clear_container_checkins(self) -> None:
        self._container_checkins.clear()

    # ── Background tasks ─────────────────────────────────────────

    async def _heartbeat_poller(self) -> None:
        log = logging.getLogger("vm_builds.heartbeat")
        while True:
            try:
                self.subscription_mgr.cleanup_expired()
                active = self.subscription_mgr.get_active_nodes()
                for node_id, metric_type in active:
                    ip = self.resolve_node_ip(node_id)
                    if not ip:
                        continue
                    collector = self._collector_map.get(metric_type)
                    if not collector:
                        continue
                    try:
                        result = await asyncio.to_thread(collector, ip)
                        result.node_id = node_id
                        self.metric_cache.store(result)
                    except (OSError, ValueError, TypeError, RuntimeError) as exc:
                        log.warning("Collector %s for %s failed: %s", metric_type, node_id, exc)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                log.error("Heartbeat poller error: %s", exc)
            await asyncio.sleep(5)

    def _collect_host_metrics(self, host_ip: str) -> dict:
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
                    metrics["services"].append(f"ct:{parts[0]}:{parts[2]}:{parts[1].lower()}")
        ok_vm, vm_out = heartbeat._ssh_exec(
            host_ip, "qm list 2>/dev/null || true", timeout=10,
        )
        if ok_vm and vm_out:
            for line in vm_out.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3:
                    metrics["services"].append(f"vm:{parts[0]}:{parts[2]}:{parts[1].lower()}")
        return metrics

    def build_relay_payload(
        self,
        host_name: str,
        host_ip: str,
        host_metrics: dict,
        container_checkins: dict[str, dict],
    ) -> dict:
        containers_summary: dict[str, dict] = {}
        for ct_name, ct_data in container_checkins.items():
            payload = ct_data.get("payload", {})
            ch = payload.get("container_health", {}) or {}
            containers_summary[ct_name] = {
                "ready": bool(ch.get("ready", False)),
                "disk_pct": payload.get("disk_usage_pct", 0),
                "mem_pct": payload.get("memory_usage_pct", 0),
                "uptime": payload.get("uptime_seconds", 0),
                "last_seen": ct_data.get("received_at", ""),
                "systemd_services": ch.get("systemd_services", {}),
                "listening_ports": ch.get("listening_ports", []),
                "extensions": ch.get("extensions", {}),
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

    def _post_to_upstream(self, url: str, payload: dict, token: str = "") -> bool:
        import urllib.request
        import urllib.error
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

    async def _relay_heartbeat(self) -> None:
        log = logging.getLogger("vm_builds.relay")
        await asyncio.sleep(5)
        while True:
            try:
                if not self._management_server:
                    await asyncio.sleep(30)
                    continue
                if not self._host_name or not self._host_ip:
                    log.warning("HOST_NAME or HOST_IP not configured, skipping relay")
                    await asyncio.sleep(30)
                    continue
                host_metrics = await asyncio.to_thread(
                    self._collect_host_metrics, self._host_ip,
                )
                payload = self.build_relay_payload(
                    self._host_name, self._host_ip,
                    host_metrics, self._container_checkins,
                )
                url = f"{self._management_server.rstrip('/')}/api/checkin"
                ok = await asyncio.to_thread(
                    self._post_to_upstream, url, payload, self._callhome_public_key,
                )
                if ok:
                    log.debug("Relayed heartbeat for %s", self._host_name)
                else:
                    log.warning("Failed relay for %s to %s", self._host_name, self._management_server)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                log.error("Relay heartbeat error: %s", exc)
            await asyncio.sleep(30)

    def start_poller(self) -> None:
        asyncio.create_task(self._heartbeat_poller())
        if self._management_server:
            asyncio.create_task(self._relay_heartbeat())

    def _store_container_checkin(self, body: dict) -> str:
        """Store a container heartbeat in _container_checkins.

        Returns the hostname/node_id of the container.
        """
        hostname = body.get("hostname", body.get("node_id", ""))
        if hostname:
            self._container_checkins[hostname] = {
                "payload": body,
                "received_at": datetime.now().isoformat(),
            }
        return hostname

    async def handle_container_checkin(self, request: Any) -> Any:
        from starlette.responses import JSONResponse as _JSONResp
        try:
            body = await request.json()
        except (ValueError, TypeError, _json.JSONDecodeError):
            return _JSONResp({"error": "Invalid JSON"}, status_code=400)
        hostname = self._store_container_checkin(body)
        if not hostname:
            return _JSONResp({"error": "Missing hostname/node_id"}, status_code=400)
        return _JSONResp({"status": "ok"})


# ── Node Manager ─────────────────────────────────────────────────────


_MESH_CT_ID = 103
_BRIDGE_CT_ID = 104
_ROUTER_VM_LAN_IP = "10.10.10.1"
_KIOSK_PORT = 9001


class NodeManager(BaseManager):
    """Per-host manager: local container ops, guest management.

    Only knows about containers on THIS physical host. Receives
    broadcast events from its Cluster Manager.
    """

    async def _get_local_batman_containers(self) -> list[tuple[int, str]]:
        """Discover which batman-capable containers (mesh/bridge) exist locally."""
        if not self._host_ip:
            raise ValueError("HOST_IP is required")
        candidates = [(_MESH_CT_ID, "mesh"), (_BRIDGE_CT_ID, "bridge")]
        present: list[tuple[int, str]] = []
        for vmid, label in candidates:
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, self._host_ip,
                f"pct status {vmid}", timeout=5,
            )
            if ok and "running" in out:
                present.append((vmid, label))
        return present

    async def batman_local(self, action: str, token: str) -> dict:
        """Execute batman_trigger.sh on this node's mesh/bridge containers."""
        if not self._host_ip:
            raise ValueError("HOST_IP is required for batman_local")
        host = self._host_name or "unknown"
        containers = await self._get_local_batman_containers()
        results: dict[str, dict] = {}
        for vmid, label in containers:
            cmd = f"pct exec {vmid} -- /usr/sbin/batman_trigger.sh {action} {token}"
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, self._host_ip, cmd, timeout=30,
            )
            results[f"{host}/{label}-{vmid}"] = {"success": ok, "output": out[:300]}
        return results

    async def batman_local_status(self) -> dict:
        """Query batman status from this node's mesh/bridge containers."""
        if not self._host_ip:
            raise ValueError("HOST_IP is required for batman_local_status")
        host = self._host_name or "unknown"
        containers = await self._get_local_batman_containers()
        statuses: dict[str, dict] = {}
        for vmid, label in containers:
            cmd = f"pct exec {vmid} -- /usr/sbin/batman_trigger.sh status"
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, self._host_ip, cmd, timeout=10,
            )
            if ok:
                active = "BATMAN=active" in out
                originators = heartbeat._parse_batman_originators(out)
                iface_section = out.split("---INTERFACES---")[1] if "---INTERFACES---" in out else ""
                interfaces = heartbeat._parse_batman_interfaces(iface_section)
                statuses[f"{host}/{label}-{vmid}"] = {
                    "active": active, "originators": originators,
                    "interfaces": interfaces,
                }
            else:
                statuses[f"{host}/{label}-{vmid}"] = {"active": False, "error": out[:200]}
        return statuses

    def register_api(self, starlette_app: Any) -> None:
        """Register node-level API routes."""
        from starlette.requests import Request as StarletteRequest
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        mgr = self

        # ── Heartbeat subscription endpoints ─────────────────────

        async def _api_heartbeat_subscribe(request: StarletteRequest) -> JSONResponse:
            try:
                body = await request.json()
                node_id = body["node_id"]
                metric_type = body.get("metric_type", "wifi")
                ttl = float(body.get("ttl", 30))
            except (KeyError, TypeError, _json.JSONDecodeError, ValueError) as exc:
                return JSONResponse({"error": f"Invalid payload: {exc}"}, status_code=400)
            if metric_type not in mgr._collector_map:
                return JSONResponse(
                    {"error": f"Unknown metric_type: {metric_type}"}, status_code=400,
                )
            ip = mgr.resolve_node_ip(node_id)
            if not ip:
                return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
            sub = mgr.subscription_mgr.subscribe(node_id, metric_type, ttl)
            return JSONResponse({
                "subscription_id": sub.subscription_id,
                "node_id": sub.node_id,
                "metric_type": sub.metric_type,
                "expires_at": sub.expires_at.isoformat(timespec="seconds"),
            })

        async def _api_heartbeat_unsubscribe(request: StarletteRequest) -> JSONResponse:
            sub_id = request.path_params.get("subscription_id", "")
            removed = mgr.subscription_mgr.unsubscribe(sub_id)
            return JSONResponse({"removed": removed})

        async def _api_heartbeat_metrics(request: StarletteRequest) -> JSONResponse:
            node_id = request.path_params.get("node_id", "")
            metric_type = request.path_params.get("metric_type", "")
            cached = mgr.metric_cache.get(node_id, metric_type)
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
            subs = mgr.subscription_mgr.list_subscriptions()
            return JSONResponse([{
                "subscription_id": s.subscription_id,
                "node_id": s.node_id,
                "metric_type": s.metric_type,
                "expires_at": s.expires_at.isoformat(timespec="seconds"),
            } for s in subs])

        # ── Guest management (local host only) ───────────────────

        async def _api_guests(request: StarletteRequest) -> JSONResponse:
            if not mgr._host_ip:
                return JSONResponse({"error": "HOST_IP not configured"}, status_code=500)
            guests: list[dict] = []
            ok_ct, ct_out = await asyncio.to_thread(
                heartbeat._ssh_exec, mgr._host_ip,
                "pct list 2>/dev/null || true", timeout=10,
            )
            if ok_ct and ct_out:
                for line in ct_out.strip().splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        guests.append({
                            "vmid": parts[0], "type": "lxc",
                            "status": parts[1], "name": parts[2],
                        })
            ok_vm, vm_out = await asyncio.to_thread(
                heartbeat._ssh_exec, mgr._host_ip,
                "qm list 2>/dev/null || true", timeout=10,
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
            vmid = request.path_params.get("vmid", "")
            action = request.path_params.get("action", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            if action not in ("start", "stop", "restart"):
                return JSONResponse({"error": f"Invalid action: {action}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            if not mgr._host_ip:
                return JSONResponse({"error": "HOST_IP not configured"}, status_code=500)
            ok_type, type_out = await asyncio.to_thread(
                heartbeat._ssh_exec, mgr._host_ip,
                f"pct status {vmid} 2>/dev/null && echo LXC || qm status {vmid} 2>/dev/null && echo QEMU",
                timeout=10,
            )
            is_lxc = "LXC" in type_out if ok_type else False
            tool = "pct" if is_lxc else "qm"
            if action == "restart":
                cmd = f"{tool} stop {vmid} 2>/dev/null; sleep 2; {tool} start {vmid}"
            else:
                cmd = f"{tool} {action} {vmid}"
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, mgr._host_ip, cmd, timeout=30,
            )
            return JSONResponse({
                "vmid": vmid, "action": action,
                "success": ok, "output": out[:300],
            })

        # ── Local batman endpoints ───────────────────────────────

        async def _api_batman_local(request: StarletteRequest) -> JSONResponse:
            action = request.path_params.get("action", "")
            if action not in ("enable", "disable"):
                return JSONResponse({"error": f"Invalid action: {action}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            if not mgr._mesh_key:
                return JSONResponse({"error": "MESH_KEY not configured"}, status_code=500)
            import hmac as _hmac
            token = _hmac.new(
                mgr._mesh_key.encode(), f"{action}_batman".encode(), hashlib.sha256,
            ).hexdigest()
            result = await mgr.batman_local(action, token)
            return JSONResponse(result)

        async def _api_batman_local_status(request: StarletteRequest) -> JSONResponse:
            result = await mgr.batman_local_status()
            return JSONResponse(result)

        # ── Event receive endpoint (broadcasts from Cluster Manager) ──

        async def _api_manager_events(request: StarletteRequest) -> JSONResponse:
            try:
                body = await request.json()
            except (ValueError, TypeError, _json.JSONDecodeError):
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            event_type = body.get("type", "")
            action = body.get("action", "")
            token = body.get("token", "")
            if event_type == "batman" and action in ("enable", "disable"):
                result = await mgr.batman_local(action, token)
                return JSONResponse({"type": "batman", "action": action, "result": result})
            return JSONResponse({"error": f"Unknown event: {event_type}"}, status_code=400)

        # ── Container heartbeat ingestion (Manager-only) ─────────
        if mgr._management_server:
            starlette_app.routes.insert(0, Route(
                "/api/checkin", mgr.handle_container_checkin, methods=["POST"],
            ))

        starlette_app.routes.insert(0, Route(
            "/api/manager/events", _api_manager_events, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/batman/local/{action}", _api_batman_local, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/batman/local/status", _api_batman_local_status, methods=["GET"],
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
        starlette_app.routes.insert(0, Route(
            "/api/guests", _api_guests, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/guests/{vmid}/{action}",
            _api_guest_action, methods=["POST"],
        ))


# ── Cluster Manager ─────────────────────────────────────────────────


class ClusterManager(NodeManager):
    """Subnet-scoped fleet manager with event broadcast capability.

    Fleet-level operations (batman across all nodes, bridge/wifi management)
    live here — NOT on NodeManager. Requires MESH_KEY at construction.

    Also acts as a fleet store for child Manager heartbeats, providing
    cluster-scoped /api/nodes and /api/fleet/* endpoints.
    """

    def __init__(
        self,
        node_resolver: Callable[[str], str | None],
        *,
        auth_validator: Callable[[str], bool] | None = None,
        host_ip: str = "",
        host_name: str = "",
        management_server: str = "",
        callhome_public_key: str = "",
        mesh_key: str = "",
        child_managers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            node_resolver,
            auth_validator=auth_validator,
            host_ip=host_ip,
            host_name=host_name,
            management_server=management_server,
            callhome_public_key=callhome_public_key,
            mesh_key=mesh_key,
        )
        self._child_managers: dict[str, str] = child_managers or {}
        self._fleet_nodes: dict[str, dict] = {}

    def register_child_checkin(self, payload: dict) -> str:
        """Store a heartbeat from a child Manager.

        Returns the node_id of the registered node.
        """
        node_id = payload.get("node_id", payload.get("hostname", ""))
        if not node_id:
            raise ValueError("Payload missing node_id/hostname")
        self._fleet_nodes[node_id] = {
            "payload": payload,
            "received_at": datetime.now().isoformat(),
        }
        return node_id

    def get_fleet_nodes(self) -> dict[str, dict]:
        """Return all child Manager heartbeats in this cluster."""
        return self._fleet_nodes

    def build_relay_payload(
        self,
        host_name: str,
        host_ip: str,
        host_metrics: dict,
        container_checkins: dict[str, dict],
    ) -> dict:
        """Build relay payload that includes child Manager fleet data.

        The ClusterManager aggregates child NodeManager heartbeats and
        relays them UP to the SuperManager.  Each child's container data
        (systemd_services, listening_ports, extensions) is forwarded so
        the SuperManager has full per-container visibility.
        """
        payload = super().build_relay_payload(
            host_name, host_ip, host_metrics, container_checkins,
        )
        fleet_summary: dict[str, dict] = {}
        for nid, entry in self._fleet_nodes.items():
            p = entry.get("payload", {})
            ch = p.get("container_health", {}) or {}
            fleet_summary[nid] = {
                "hostname": p.get("hostname", nid),
                "local_ips": p.get("local_ips", []),
                "disk_usage_pct": p.get("disk_usage_pct", 0),
                "memory_usage_pct": p.get("memory_usage_pct", 0),
                "uptime_seconds": p.get("uptime_seconds", 0),
                "last_seen": entry.get("received_at", ""),
                "services": p.get("services", []),
                "container_health": ch,
            }
        payload["cluster_nodes"] = fleet_summary
        return payload

    def _broadcast_event_to_managers_sync(
        self, event_payload: dict,
    ) -> dict[str, dict]:
        """POST an event to each child Manager's /api/manager/events.

        Uses _child_managers (Proxmox host IPs) to reach kiosk Managers,
        NOT _fleet_nodes (which contains container heartbeats).
        Synchronous — called via asyncio.to_thread from async context.
        """
        import urllib.request
        import urllib.error
        log = logging.getLogger("vm_builds.cluster")
        results: dict[str, dict] = {}
        for host_name, host_ip in self._child_managers.items():
            url = f"http://{host_ip}:{_KIOSK_PORT}/api/manager/events"
            body = _json.dumps(event_payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_body = _json.loads(resp.read().decode())
                    results[host_name] = {"success": True, "response": resp_body}
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                log.warning("Event broadcast to %s (%s) failed: %s", host_name, host_ip, exc)
                results[host_name] = {"success": False, "error": str(exc)[:200]}
        return results

    async def batman_fleet(self, action: str) -> dict:
        """Orchestrate batman across ALL nodes in this cluster.

        Uses a two-phase approach:
        1. Execute locally on this node's containers + router VM
        2. Broadcast the batman event to all child Managers (they
           execute locally on THEIR containers)
        """
        import hmac as _hmac

        if not self._mesh_key:
            return {"error": "MESH_KEY not configured"}

        token = _hmac.new(
            self._mesh_key.encode(), f"{action}_batman".encode(), hashlib.sha256,
        ).hexdigest()

        host = self._host_name or "unknown"
        results: dict[str, dict] = {}

        # Phase 1: Local execution (router VM + this node's containers)
        cmd = f"/usr/sbin/batman_trigger.sh {action} {token}"
        ok, out = await asyncio.to_thread(
            heartbeat._ssh_exec, _ROUTER_VM_LAN_IP, cmd, timeout=30,
        )
        results[f"{host}/router-100"] = {"success": ok, "output": out[:300]}

        local_result = await self.batman_local(action, token)
        results.update(local_result)

        # Phase 2: Broadcast to child Managers
        event_payload = {
            "type": "batman",
            "action": action,
            "token": token,
        }
        broadcast_results = await asyncio.to_thread(
            self._broadcast_event_to_managers_sync, event_payload,
        ) if self._child_managers else {}
        for nid, r in broadcast_results.items():
            results[nid] = r

        event_bus.emit({
            "type": "batman_event",
            "action": action,
            "results": {k: v.get("success", False) for k, v in results.items()},
        })

        return results

    async def batman_fleet_status(self) -> dict:
        """Query batman status from all nodes in this cluster.

        Phase 1: local status (router VM + this node's containers).
        Phase 2: query each child Manager via /api/batman/local/status.
        """
        host = self._host_name or "unknown"
        statuses: dict[str, dict] = {}

        # Phase 1: local — router VM on this node
        ok, out = await asyncio.to_thread(
            heartbeat._ssh_exec, _ROUTER_VM_LAN_IP,
            "/usr/sbin/batman_trigger.sh status", timeout=10,
        )
        if ok:
            active = "BATMAN=active" in out
            originators = heartbeat._parse_batman_originators(out)
            iface_section = out.split("---INTERFACES---")[1] if "---INTERFACES---" in out else ""
            interfaces = heartbeat._parse_batman_interfaces(iface_section)
            statuses[f"{host}/router-100"] = {
                "active": active, "originators": originators,
                "interfaces": interfaces,
            }
        else:
            statuses[f"{host}/router-100"] = {"active": False, "error": out[:200]}

        # Local containers
        local_status = await self.batman_local_status()
        statuses.update(local_status)

        # Phase 2: query child Managers
        child_statuses = await asyncio.to_thread(
            self._query_child_batman_status_sync,
        )
        statuses.update(child_statuses)

        return statuses

    def _query_child_batman_status_sync(self) -> dict[str, dict]:
        """GET /api/batman/local/status from each child Manager.

        Uses _child_managers (kiosk container IPs on the LAN) to reach
        child Managers directly. Keys in the response are already
        host-qualified (e.g. "mesh1/mesh-103").
        """
        import urllib.request
        import urllib.error
        log = logging.getLogger("vm_builds.cluster")
        results: dict[str, dict] = {}
        for host_name, host_ip in self._child_managers.items():
            url = f"http://{host_ip}:{_KIOSK_PORT}/api/batman/local/status"
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp_body = _json.loads(resp.read().decode())
                    results.update(resp_body)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                log.warning("Batman status query to %s (%s) failed: %s", host_name, host_ip, exc)
                results[host_name] = {"active": False, "error": str(exc)[:200]}
        return results

    def register_api(  # noqa: C901
        self, starlette_app: Any, *, include_fleet_storage: bool = True,
    ) -> None:
        """Register node-level AND fleet-level API routes.

        Args:
            include_fleet_storage: When True, register /api/checkin,
                /api/nodes, /api/fleet/ready for cluster-scoped fleet
                storage. Set to False when app.py already provides its
                own persistent fleet storage backed by nodes.json.
        """
        super().register_api(starlette_app)

        from starlette.requests import Request as StarletteRequest
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        cluster = self

        if include_fleet_storage:
            async def _api_cluster_checkin(request: StarletteRequest) -> JSONResponse:
                """Accept heartbeats from child Managers or local containers.

                Distinguishes child NodeManager relays from direct container
                heartbeats: a relay includes ``services`` (from ``pct list`` /
                ``qm list`` on the host) or ``cluster_nodes``.  Plain container
                heartbeats lack these and should be routed to the container
                checkin handler (``_container_checkins``) so they appear in
                ``extensions.containers`` during the upstream relay.
                """
                try:
                    body = await request.json()
                except (ValueError, TypeError, _json.JSONDecodeError):
                    return JSONResponse({"error": "Invalid JSON"}, status_code=400)
                node_id = body.get("node_id", body.get("hostname", ""))
                if not node_id:
                    return JSONResponse({"error": "Missing node_id/hostname"}, status_code=400)
                is_manager_relay = bool(
                    body.get("services")
                    or body.get("cluster_nodes")
                    or node_id in (cluster._child_managers or {})
                )
                if is_manager_relay:
                    cluster.register_child_checkin(body)
                else:
                    cluster._store_container_checkin(body)
                return JSONResponse({"status": "ok", "node_id": node_id})

            async def _api_cluster_nodes(request: StarletteRequest) -> JSONResponse:
                """Return all nodes in this cluster."""
                nodes = []
                for node_id, entry in cluster.get_fleet_nodes().items():
                    p = entry.get("payload", {})
                    nodes.append({
                        "node_id": node_id,
                        "hostname": p.get("hostname", node_id),
                        "local_ips": p.get("local_ips", []),
                        "disk_usage_pct": p.get("disk_usage_pct", 0),
                        "memory_usage_pct": p.get("memory_usage_pct", 0),
                        "uptime_seconds": p.get("uptime_seconds", 0),
                        "services": p.get("services", []),
                        "container_health": p.get("container_health"),
                        "last_seen": entry.get("received_at", ""),
                    })
                return JSONResponse({"nodes": nodes})

            async def _api_cluster_fleet_ready(request: StarletteRequest) -> JSONResponse:
                """Check readiness of services in this cluster."""
                services_param = request.query_params.get("services", "")
                if not services_param:
                    return JSONResponse({"error": "Missing services parameter"}, status_code=400)
                requested = [s.strip() for s in services_param.split(",") if s.strip()]
                results: dict[str, dict] = {}
                for svc in requested:
                    found = False
                    for _nid, entry in cluster.get_fleet_nodes().items():
                        p = entry.get("payload", {})
                        ch = p.get("container_health", {})
                        containers = ch.get("extensions", {}).get("containers", {}) if ch else {}
                        if svc in containers and containers[svc].get("ready"):
                            results[svc] = {"status": "ready", "node": _nid}
                            found = True
                            break
                    if not found:
                        results[svc] = {"status": "unknown"}
                ready_count = sum(1 for v in results.values() if v["status"] == "ready")
                return JSONResponse({
                    "all_ready": ready_count == len(requested),
                    "ready_count": ready_count,
                    "total": len(requested),
                    "services": results,
                })

            starlette_app.routes.insert(0, Route(
                "/api/checkin", _api_cluster_checkin, methods=["POST"],
            ))
            starlette_app.routes.insert(0, Route(
                "/api/nodes", _api_cluster_nodes, methods=["GET"],
            ))
            starlette_app.routes.insert(0, Route(
                "/api/fleet/ready", _api_cluster_fleet_ready, methods=["GET"],
            ))

        # ── Fleet batman endpoints ───────────────────────────────

        async def _api_batman_enable(request: StarletteRequest) -> JSONResponse:
            auth_err = cluster.check_mutation_auth(request)
            if auth_err:
                return auth_err
            results = await cluster.batman_fleet("enable")
            if "error" in results:
                return JSONResponse(results, status_code=500)
            total = len(results)
            ok_count = sum(1 for r in results.values() if r.get("success"))
            return JSONResponse({
                "action": "enable", "total": total,
                "succeeded": ok_count, "results": results,
            })

        async def _api_batman_disable(request: StarletteRequest) -> JSONResponse:
            auth_err = cluster.check_mutation_auth(request)
            if auth_err:
                return auth_err
            results = await cluster.batman_fleet("disable")
            if "error" in results:
                return JSONResponse(results, status_code=500)
            total = len(results)
            ok_count = sum(1 for r in results.values() if r.get("success"))
            return JSONResponse({
                "action": "disable", "total": total,
                "succeeded": ok_count, "results": results,
            })

        async def _api_batman_status(request: StarletteRequest) -> JSONResponse:
            return JSONResponse(await cluster.batman_fleet_status())

        # ── Fleet WiFi/bridge endpoints ──────────────────────────

        async def _api_bridge_restart_wifi(request: StarletteRequest) -> JSONResponse:
            auth_err = cluster.check_mutation_auth(request)
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
                ip = cluster.resolve_node_ip(node["node_id"])
                if not ip:
                    results[node["node_id"]] = {"success": False, "error": "IP not resolved"}
                    continue
                cmd = f"pct exec {_BRIDGE_CT_ID} -- /usr/sbin/wifi_setup.sh restart"
                ok, output = heartbeat._ssh_exec(ip, cmd, timeout=15)
                results[node["node_id"]] = {"success": ok, "output": output[:200]}
            return JSONResponse(results)

        async def _api_wifi_mode(request: StarletteRequest) -> JSONResponse:
            auth_err = cluster.check_mutation_auth(request)
            if auth_err:
                return auth_err
            node_id = request.path_params.get("node", "")
            mode = request.path_params.get("mode", "")
            if mode not in ("ap", "sta"):
                return JSONResponse(
                    {"error": f"Invalid mode: {mode} (expected ap or sta)"}, status_code=400,
                )
            ip = cluster.resolve_node_ip(node_id)
            if not ip:
                return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
            cmd = f"pct exec {_BRIDGE_CT_ID} -- /usr/sbin/wifi_setup.sh switch-mode {mode}"
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, ip, cmd, timeout=30,
            )
            return JSONResponse({
                "node_id": node_id, "mode": mode,
                "success": ok, "output": out[:300],
            })

        async def _api_wifi_status(request: StarletteRequest) -> JSONResponse:
            node_id = request.path_params.get("node", "")
            ip = cluster.resolve_node_ip(node_id)
            if not ip:
                return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
            cmd = f"pct exec {_BRIDGE_CT_ID} -- /usr/sbin/wifi_setup.sh status"
            ok, out = await asyncio.to_thread(
                heartbeat._ssh_exec, ip, cmd, timeout=10,
            )
            if not ok:
                return JSONResponse({"node_id": node_id, "error": out[:200]}, status_code=502)
            status: dict[str, str] = {}
            for line in out.strip().splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    status[k.strip().lower()] = v.strip()
            return JSONResponse({"node_id": node_id, **status})

        async def _api_wifi_status_all(request: StarletteRequest) -> JSONResponse:
            """Aggregate WiFi status across all nodes with mesh/bridge containers."""
            from scripts.webui.data import get_bridge_nodes, get_mesh_nodes
            targets: dict[str, int] = {}
            for bn in get_bridge_nodes():
                targets[bn["node_id"]] = _BRIDGE_CT_ID
            ap_node, sta_nodes = get_mesh_nodes()
            for n in [ap_node, *sta_nodes]:
                if n not in targets:
                    targets[n] = _MESH_CT_ID
            results: dict[str, dict] = {}
            for node_id, ct_id in targets.items():
                ip = cluster.resolve_node_ip(node_id)
                if not ip:
                    results[node_id] = {"error": f"Unknown node: {node_id}"}
                    continue
                cmd = f"pct exec {ct_id} -- /usr/sbin/wifi_setup.sh status"
                ok, out = await asyncio.to_thread(
                    heartbeat._ssh_exec, ip, cmd, timeout=10,
                )
                if not ok:
                    results[node_id] = {"error": out[:200]}
                    continue
                status: dict[str, str] = {}
                for line in out.strip().splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        status[k.strip().lower()] = v.strip()
                results[node_id] = status
            return JSONResponse(results)

        # ── Cluster event endpoint ───────────────────────────────

        async def _api_cluster_events(request: StarletteRequest) -> JSONResponse:
            auth_err = cluster.check_mutation_auth(request)
            if auth_err:
                return auth_err
            try:
                body = await request.json()
            except (ValueError, TypeError, _json.JSONDecodeError):
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            event_type = body.get("type", "")
            action = body.get("action", "")
            if event_type == "batman" and action in ("enable", "disable"):
                results = await cluster.batman_fleet(action)
                if "error" in results:
                    return JSONResponse(results, status_code=500)
                total = len(results)
                ok_count = sum(1 for r in results.values() if r.get("success"))
                return JSONResponse({
                    "type": "batman", "action": action,
                    "total": total, "succeeded": ok_count, "results": results,
                })
            return JSONResponse({"error": f"Unknown event: {event_type}"}, status_code=400)

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
            "/api/wifi/mode/{node}/{mode}", _api_wifi_mode, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/wifi/status", _api_wifi_status_all, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/wifi/status/{node}", _api_wifi_status, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/cluster/events", _api_cluster_events, methods=["POST"],
        ))


# ── Module-level singleton for backward compatibility ────────────────
# app.py and kiosk_server.py call init() / register_api() / start_poller()
# at module level. These thin wrappers delegate to the current instance.

_instance: BaseManager | None = None


def init(
    node_resolver: Callable[[str], str | None],
    auth_validator: Callable[[str], bool] | None = None,
    config: dict[str, str] | None = None,
    manager_class: type | None = None,
) -> BaseManager:
    """Create the module-level manager singleton.

    The config dict is unpacked into explicit keyword arguments. Callers
    (app.py, kiosk_server.py) must provide all values their tier requires.
    """
    global _instance
    cls = manager_class or ClusterManager
    cfg = config or {}

    child_mgrs: dict[str, str] = {}
    if issubclass(cls, ClusterManager):
        raw = cfg.get("CHILD_MANAGER_IPS", {})
        if isinstance(raw, str):
            import json as _j
            child_mgrs = _j.loads(raw) if raw else {}
        else:
            child_mgrs = dict(raw)

    kwargs: dict = dict(
        auth_validator=auth_validator,
        host_ip=cfg.get("HOST_IP", ""),
        host_name=cfg.get("HOST_NAME", ""),
        management_server=cfg.get("MANAGEMENT_SERVER", ""),
        callhome_public_key=cfg.get("CALLHOME_PUBLIC_KEY", ""),
        mesh_key=cfg.get("MESH_KEY", ""),
    )
    if issubclass(cls, ClusterManager):
        kwargs["child_managers"] = child_mgrs

    _instance = cls(node_resolver, **kwargs)
    return _instance


def reset() -> None:
    global _instance
    _instance = None


def get_instance() -> BaseManager:
    if _instance is None:
        raise RuntimeError("manager.init() has not been called")
    return _instance


def get_subscription_manager() -> heartbeat.SubscriptionManager:
    return get_instance().subscription_mgr


def get_metric_cache() -> heartbeat.MetricCache:
    return get_instance().metric_cache


def resolve_node_ip(node_id: str) -> str | None:
    return get_instance().resolve_node_ip(node_id)


def get_container_checkins() -> dict[str, dict]:
    return get_instance().get_container_checkins()


def clear_container_checkins() -> None:
    get_instance().clear_container_checkins()


def start_poller() -> None:
    get_instance().start_poller()


def register_api(starlette_app: Any, **kwargs: Any) -> None:
    get_instance().register_api(starlette_app, **kwargs)


def build_relay_payload(
    host_name: str,
    host_ip: str,
    host_metrics: dict,
    container_checkins: dict[str, dict],
) -> dict:
    return get_instance().build_relay_payload(
        host_name, host_ip, host_metrics, container_checkins,
    )

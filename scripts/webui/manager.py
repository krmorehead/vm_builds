"""Manager hierarchy for the 4-tier fleet architecture.

Inheritance:
    BaseManager — subscriptions, metric cache, node resolution (no I/O)
        NodeManager — single-host ops (PVE API + HTTP), host metrics
            ClusterManager — subnet-scoped fleet, child aggregation
                SuperManager — HTTP-only global view

Each tier's relay uses the same pattern:
    emit() → build_payload() → POST to upstream /api/checkin

NM: build_payload() = {my_info, containers}
CM: build_payload() = {my_info, containers, cluster_nodes: children}
SM: receives payloads, never emits (top of chain)

kiosk_server.py instantiates NodeManager or ClusterManager.
app.py instantiates SuperManager (is_supermanager=True).
"""

from __future__ import annotations

import asyncio
import errno as _errno
import hashlib
import hmac as _hmac
import json as _json
import logging
import random as _random
import re as _re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from abc import ABC, abstractmethod

from scripts.webui import heartbeat
from scripts.webui.data import Ports, event_bus
from scripts.webui.display_transfer import (
    DisplayTransferService, build_handler,
)
from scripts.webui.host_state import ContainerInfo, HostState, HostStateStore
from scripts.webui.pve_api import PveApiClient, PveApiError


# ── Metric Route Handlers ────────────────────────────────────────────
#
# The SM is HTTP-only — it proxies metric requests to NMs via VPN.
# Each handler resolves two things:
#   1. Which NM host to send the HTTP request to (VPN-routed)
#   2. What container the NM should collect from locally (HTTP on its host)
#
# Three reusable handlers cover all routing patterns:
#   LocalContainerRoute — NM is the host, collect from a named container
#   HostedServiceRoute  — service name → remap to the host that runs it
#   DirectRoute         — node IS the target (default fallback)


class MetricRouteHandler(ABC):
    """Resolves a page-level node_id to NM routing + collection target."""

    @abstractmethod
    def resolve(self, node_id: str) -> tuple[str, str]:
        """Return (nm_host_id, container_target).

        nm_host_id:       Host whose NM receives the HTTP request.
                          Resolved to VPN IP by the node resolver.
        container_target: subscribe_node_id sent to the NM. The NM
                          resolves this locally for HTTP collection.
        """


class LocalContainerRoute(MetricRouteHandler):
    """Node IS the NM host; collect from a named container on that host.

    Used when the metric source is a container running on the same
    host as the NM.  Example: bridge-1's NM collects from its local
    ``openwrt-bridge`` container.
    """

    def __init__(self, container_hostname: str) -> None:
        self._container_hostname = container_hostname

    def resolve(self, node_id: str) -> tuple[str, str]:
        return node_id, self._container_hostname


class HostedServiceRoute(MetricRouteHandler):
    """Service identified by name; route to the host that runs it.

    Used when the page's node_id is a service name (e.g. ``openwrt``)
    rather than a Proxmox host name.  Maps to the hosting NM so the
    SM can reach it over VPN.
    """

    def __init__(self, host_id: str) -> None:
        self._host_id = host_id

    def resolve(self, node_id: str) -> tuple[str, str]:
        return self._host_id, node_id


class DirectRoute(MetricRouteHandler):
    """Node IS the NM host AND the collection target.  Default fallback."""

    def resolve(self, node_id: str) -> tuple[str, str]:
        return node_id, node_id


_DIRECT_ROUTE = DirectRoute()


def _resolve_container_ip(mgr: "BaseManager", vmid: int) -> str | None:
    """Look up a container's IP from heartbeat checkins, PVE runtime, or config."""
    for _name, checkin in mgr._container_checkins.items():
        hb_vmid = checkin.get("container_health", {}).get("vmid")
        if hb_vmid and str(hb_vmid) == str(vmid):
            ip = checkin.get("ip")
            if ip:
                return ip
    pve = getattr(mgr, "_pve", None)
    if not pve:
        return None
    try:
        ifaces = pve.ct_interfaces(vmid)
        for ifc in (ifaces or []):
            if ifc.get("name") == "lo":
                continue
            for addr in ifc.get("ip-addresses", []):
                if addr.get("ip-address-type") == "inet":
                    ip = addr.get("ip-address", "")
                    if ip and ip != "127.0.0.1":
                        return ip
    except (PveApiError, TypeError, KeyError):
        pass
    try:
        cfg = pve.ct_config(vmid)
        for key in sorted(cfg):
            if not key.startswith("net"):
                continue
            net_str = cfg[key]
            for part in net_str.split(","):
                if part.startswith("ip="):
                    ip = part[3:].split("/")[0]
                    if ip and ip != "127.0.0.1" and ip.lower() != "dhcp":
                        return ip
    except PveApiError:
        pass
    return None


async def _callhome_exec(
    container_ip: str,
    cmd: str,
    token: str,
    timeout: int = 30,
    vmid: int | None = None,
) -> tuple[bool, str]:
    """Execute a whitelisted command on a container via its callhome HTTP endpoint.

    Debian containers use callhome.py on /cmd.
    OpenWrt containers use uhttpd CGI on /cgi-bin/cmd.
    """
    path = heartbeat.cmd_path_for_vmid(vmid)
    url = f"http://{container_ip}:{heartbeat.CALLHOME_CMD_PORT}{path}"
    payload = _json.dumps({"command": cmd, "token": token}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = await asyncio.to_thread(
            urllib.request.urlopen, req, timeout=timeout,
        )
        raw = resp.read().decode()
        sanitized = _re.sub(r"[\x00-\x1f]", "", raw)
        body = _json.loads(sanitized)
        return body.get("success", False), body.get("output", "")
    except (urllib.error.URLError, OSError, _json.JSONDecodeError) as exc:
        return False, str(exc)[:300]


# ── Base Manager ─────────────────────────────────────────────────────


class BaseManager:
    """Shared infrastructure: subscriptions, metric cache, node resolution.

    This class holds ONLY cross-tier plumbing. Tier-specific behavior
    (HTTP, relay, host metrics, fleet aggregation) belongs in subclasses.
    """

    def __init__(
        self,
        node_resolver: Callable[[str], str | None],
        *,
        auth_validator: Callable[[str], bool] | None = None,
        display_resolver: Callable[[str], tuple[str, int] | None] | None = None,
        host_ip: str = "",
        host_name: str = "",
        management_server: str = "",
        callhome_public_key: str = "",
        mesh_key: str = "",
        state_dir: str = "",
        pve_api_token: str = "",
        pve_node: str = "",
    ) -> None:
        self.subscription_mgr = heartbeat.SubscriptionManager()
        self.metric_cache = heartbeat.MetricCache()
        self.host_state_store: HostStateStore | None = None
        if state_dir:
            self.host_state_store = HostStateStore(Path(state_dir))
        self._node_resolver = node_resolver
        self._display_resolver = display_resolver
        self._auth_validator = auth_validator
        self._host_ip = host_ip
        self._host_name = host_name
        self._management_server = management_server
        self._callhome_public_key = callhome_public_key
        self._mesh_key = mesh_key
        self._pve_api_token = pve_api_token
        self._pve_node = pve_node
        self._container_checkins: dict[str, dict] = {}
        self.display_transfer = DisplayTransferService()
        self._collector_map: dict[str, Any] = {}
        self._metric_routes: dict[tuple[str, str], MetricRouteHandler] = {}
        self._cached_host_metrics: dict[str, Any] = {}

    def register_metric_route(
        self, node_id: str, metric_type: str, handler: MetricRouteHandler,
    ) -> None:
        """Register a routing handler for a (node_id, metric_type) pair."""
        self._metric_routes[(node_id, metric_type)] = handler

    def resolve_collection_target(
        self, node_id: str, metric_type: str,
    ) -> tuple[str, str]:
        """Resolve where to route and what to collect.

        Returns (nm_host_id, container_target) using the registered
        handler, falling back to DirectRoute if none is registered.
        """
        handler = self._metric_routes.get((node_id, metric_type), _DIRECT_ROUTE)
        return handler.resolve(node_id)

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

    # ── Display streaming and hierarchy ─────────────────────────

    def _resolve_display_target(self, node_id: str) -> tuple[str, int] | None:
        """Resolve the browser-reachable (ip, port_offset) for a node's display.

        Returns (ip, port_offset) where port_offset is added to the app's
        base display port. LAN hosts use a relay on the primary host at
        offset ports (e.g., 6080+100=6180 for kiosk).

        Base implementation returns None (leaf NodeManager).
        ClusterManager overrides to read _child_managers and _display_resolver.
        """
        return None

    def get_child_display_url(self, node_id: str) -> str | None:
        """Build the display URL for a node's kiosk KasmVNC.

        The browser connects directly to the resolved IP and port.
        Port offsets handle LAN hosts relayed through the primary host.
        """
        target = self._resolve_display_target(node_id)
        if not target:
            return None
        ip, offset = target
        return f"http://{ip}:{Ports.KIOSK_DISPLAY + offset}"

    def get_guest_viewstream_url(self, node_id: str, app_id: str) -> str | None:
        """Build the viewstream URL for a display app on a node.

        Resolves the display target and applies any port offset for
        LAN host relays before constructing the URL.

        When no display handler is registered (SM tier), falls back to
        DISPLAY_APP_CONFIGS for the app's base port.
        """
        target = self._resolve_display_target(node_id)
        if not target:
            return None
        ip, offset = target
        handler = self.display_transfer.get_handler(app_id)
        if handler:
            base_url = handler.get_viewstream_url(ip)
            if not base_url or offset == 0:
                return base_url
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(base_url)
            new_port = int(parsed.port or 0) + offset
            return urlunparse(parsed._replace(netloc=f"{parsed.hostname}:{new_port}"))
        from scripts.webui.data import DISPLAY_APP_CONFIGS
        config = DISPLAY_APP_CONFIGS.get(app_id)
        if config and config.display_port:
            return f"http://{ip}:{config.display_port + offset}"
        return None

    @property
    def supports_fleet(self) -> bool:
        """Whether this manager has fleet-level visibility (CM and above)."""
        return False

    def get_fleet_children(self, node_id: str) -> list[str]:
        """Return child node IDs for a given parent.

        Base returns [] (leaf NodeManager). CM and SM override.
        """
        return []

    def get_fleet_nodes(self) -> dict[str, dict]:
        """Return child Manager heartbeats. Base returns empty."""
        return {}

    def register_child_checkin(self, payload: dict) -> str:
        """Store a child Manager heartbeat. Base is a no-op."""
        return payload.get("node_id", payload.get("hostname", ""))

    def get_container_checkins(self) -> dict[str, dict]:
        return self._container_checkins

    def clear_container_checkins(self) -> None:
        self._container_checkins.clear()

    async def fetch_host_state_from_upstream(self) -> None:
        """Fetch state from upstream on startup. Base is a no-op."""

    # ── Background tasks ─────────────────────────────────────────

    async def _heartbeat_poller(self) -> None:
        log = logging.getLogger("vm_builds.heartbeat")
        while True:
            try:
                self.subscription_mgr.cleanup_expired()
                active = self.subscription_mgr.get_active_nodes()

                async def _collect(node_id: str, metric_type: str) -> None:
                    nm_host, container_target = self.resolve_collection_target(
                        node_id, metric_type,
                    )
                    ip = self._resolve_collector_ip(nm_host, container_target)
                    if not ip:
                        return
                    cb = heartbeat.get_circuit_status(ip)
                    if cb["is_open"]:
                        log.debug(
                            "Skipping %s/%s: circuit breaker open for %s (%.0fs)",
                            node_id, metric_type, ip, cb["backoff_remaining_s"],
                        )
                        return
                    collector = self._collector_map.get(metric_type)
                    if not collector:
                        return
                    try:
                        result = await asyncio.to_thread(
                            collector, ip, container_target,
                        )
                        result.node_id = node_id
                        self.metric_cache.store(result)
                    except (OSError, ValueError, TypeError, RuntimeError) as exc:
                        log.warning("Collector %s for %s failed: %s", metric_type, node_id, exc)

                if active:
                    await asyncio.gather(
                        *[_collect(nid, mt) for nid, mt in active],
                    )
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                log.error("Heartbeat poller error: %s", exc)
            await asyncio.sleep(5)

    def _resolve_collector_ip(
        self, nm_host: str, container_target: str,
    ) -> str | None:
        """Resolve the IP to pass to the collector function.

        Default: prefers container_target (local container name → IP),
        falls back to nm_host. Subclasses (SuperManager) override for
        HTTP-only routing.
        """
        return (
            self.resolve_node_ip(container_target)
            or self.resolve_node_ip(nm_host)
        )

    @staticmethod
    def _empty_host_metrics() -> dict[str, Any]:
        return {
            "disk_usage_pct": 0,
            "memory_usage_pct": 0,
            "uptime_seconds": 0,
            "services": [],
        }

    def get_host_metrics(self) -> dict[str, Any]:
        """Return the most recent cached host metrics.

        The actual collection happens in NM's background relay loop.
        Callers never need to worry about circuit breakers.
        """
        return dict(self._cached_host_metrics) if self._cached_host_metrics else self._empty_host_metrics()

    def _summarize_containers(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for ct_name, ct_data in self._container_checkins.items():
            payload = ct_data.get("payload", {})
            ch = payload.get("container_health", {}) or {}
            result[ct_name] = {
                "ready": bool(ch.get("ready", False)),
                "disk_pct": payload.get("disk_usage_pct", 0),
                "mem_pct": payload.get("memory_usage_pct", 0),
                "uptime": payload.get("uptime_seconds", 0),
                "last_seen": ct_data.get("received_at", ""),
                "systemd_services": ch.get("systemd_services", {}),
                "listening_ports": ch.get("listening_ports", []),
                "extensions": ch.get("extensions", {}),
            }
        return result

    def build_payload(self) -> dict:
        """Assemble this node's heartbeat payload from cached state.

        Subclasses extend to add children (CM) or fleet data (SM).
        No I/O — reads only cached metrics and container checkins.
        """
        metrics = self.get_host_metrics()
        return {
            "node_id": self._host_name,
            "hostname": self._host_name,
            "local_ips": [self._host_ip] if self._host_ip else [],
            "uptime_seconds": metrics.get("uptime_seconds", 0),
            "disk_usage_pct": metrics.get("disk_usage_pct", 0),
            "memory_usage_pct": metrics.get("memory_usage_pct", 0),
            "services": metrics.get("services", []),
            "version": "1.0",
            "container_health": {
                "container_id": self._host_name,
                "ready": True,
                "systemd_services": {},
                "listening_ports": [],
                "extensions": {
                    "containers": self._summarize_containers(),
                },
            },
        }

    def build_relay_payload(
        self,
        host_name: str,
        host_ip: str,
        host_metrics: dict,
        container_checkins: dict[str, dict],
    ) -> dict:
        """Build a relay payload from explicit arguments.

        Prefer build_payload() for internal relay loops (reads cached
        state). This method exists for callers that supply their own
        metrics and container data (tests, external integrations).
        """
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

    def emit(self) -> bool:
        """Build payload and POST to upstream management server.

        Returns True on success, False on failure or if relay is
        not configured. Subclasses that need to refresh cached data
        before emitting should override _refresh_metrics().
        """
        if not self._management_server or not self._host_name:
            return False
        url = f"{self._management_server.rstrip('/')}/api/checkin"
        return self._post_to_upstream(
            url, self.build_payload(), self._callhome_public_key,
        )

    def _refresh_metrics(self) -> None:
        """Refresh cached host metrics. Base is a no-op.

        NodeManager overrides to query its own host via PVE API.
        SuperManager never needs host metrics (HTTP-only).
        """

    async def _relay_loop(self) -> None:
        """Background loop: refresh metrics → emit payload upstream."""
        log = logging.getLogger("vm_builds.relay")
        await asyncio.sleep(1)
        while True:
            try:
                await asyncio.to_thread(self._refresh_metrics)
                ok = await asyncio.to_thread(self.emit)
                if ok:
                    log.debug("Relayed heartbeat for %s", self._host_name)
                elif self._management_server:
                    log.warning("Failed relay for %s", self._host_name)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                log.error("Relay error: %s", exc)
            await asyncio.sleep(5)

    def start_poller(self) -> None:
        asyncio.create_task(self._heartbeat_poller())
        if self._management_server:
            asyncio.create_task(self._relay_loop())

    def _update_container_version(self, container_hostname: str, image_version: str) -> None:
        """Update image_version on the ContainerInfo matching this container hostname.

        containers dict is keyed by VMID (int). Hostname lookup is O(N)
        with N ≤ 12 containers per host -- acceptable for heartbeat frequency.
        """
        if not self.host_state_store or not self._host_name:
            return
        state = self.host_state_store.get(self._host_name)
        if state is None:
            return
        for ct in state.containers.values():
            if ct.hostname == container_hostname:
                ct.image_version = image_version
                self.host_state_store.save(state)
                return

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
            ch = body.get("container_health", {})
            image_version = ch.get("image_version", "")
            if image_version:
                self._update_container_version(hostname, image_version)
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


_ROUTER_VM_ID = 100
_MESH_CT_ID = 103
_BRIDGE_CT_ID = 104
_DESKTOP_CT_ID = 400
ROUTER_VM_LAN_IP = "10.10.10.1"
_VALID_DESKTOP_SESSIONS = ("kde", "gnome")


class NodeManager(BaseManager):
    """Per-host manager: local container ops, guest management.

    Only knows about containers on THIS physical host. Receives
    broadcast events from its Cluster Manager. On startup, fetches
    host state from the upstream Manager and caches it locally.
    """

    def __init__(self, node_resolver: Callable[[str], str | None], **kwargs: Any) -> None:
        super().__init__(node_resolver, **kwargs)
        self._local_host_state: HostState | None = None
        token = self._callhome_public_key
        self._collector_map: dict[str, Any] = {
            "wifi": lambda ip, nid="": heartbeat.collect_wifi_metrics(ip, nid, token=token, vmid=_MESH_CT_ID),
            "bridge": lambda ip, nid="": heartbeat.collect_bridge_metrics(ip, nid, token=token, vmid=_BRIDGE_CT_ID),
            "router": lambda ip, nid="": heartbeat.collect_router_metrics(ip, nid, token=token, vmid=_ROUTER_VM_ID),
            "mesh": lambda ip, nid="": heartbeat.collect_mesh_metrics(ip, nid, token=token, vmid=_MESH_CT_ID),
            "batman": lambda ip, nid="": heartbeat.collect_batman_metrics(ip, nid, token=token, vmid=_BRIDGE_CT_ID),
        }
        self._pve: PveApiClient | None = None
        if self._pve_api_token and self._host_ip and self._pve_node:
            self._pve = PveApiClient(
                host=self._host_ip,
                node=self._pve_node,
                token=self._pve_api_token,
            )

    def _refresh_metrics(self) -> None:
        """Collect host metrics via Proxmox REST API and cache them."""
        if not self._pve:
            return
        _log = logging.getLogger("vm_builds.node_metrics")
        metrics = self._empty_host_metrics()
        try:
            ns = self._pve.node_status()
            mem = ns.get("memory", {})
            rootfs = ns.get("rootfs", {})
            if mem.get("total"):
                metrics["memory_usage_pct"] = int(
                    mem["used"] / mem["total"] * 100
                )
            if rootfs.get("total"):
                metrics["disk_usage_pct"] = int(
                    rootfs["used"] / rootfs["total"] * 100
                )
            metrics["uptime_seconds"] = ns.get("uptime", 0)
        except PveApiError as exc:
            _log.warning("PVE node_status failed: %s", exc)
        try:
            for ct in self._pve.ct_list():
                metrics["services"].append(
                    f"ct:{ct['vmid']}:{ct.get('name', '?')}:{ct['status']}"
                )
            for vm in self._pve.vm_list():
                metrics["services"].append(
                    f"vm:{vm['vmid']}:{vm.get('name', '?')}:{vm['status']}"
                )
        except PveApiError as exc:
            _log.warning("PVE guest list failed: %s", exc)
        self._cached_host_metrics = metrics

    async def fetch_host_state_from_upstream(self) -> None:
        """Fetch this host's state from the upstream Manager on startup.

        404 = first time, no state yet (normal for fresh deploys).
        Connection error = log warning, proceed without cached state.
        """
        if not self._management_server or not self._host_name:
            return
        log = logging.getLogger("vm_builds.node_state")
        url = f"{self._management_server.rstrip('/')}/api/host/{self._host_name}/state"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
                self._local_host_state = HostState.from_dict(data)
                log.info("Fetched host state for %s from upstream", self._host_name)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.info("No upstream state for %s (first deploy)", self._host_name)
            else:
                log.warning("Failed to fetch state for %s: HTTP %s", self._host_name, exc.code)
        except (urllib.error.URLError, OSError) as exc:
            log.warning("Cannot reach upstream for state: %s", exc)

    def get_local_host_state(self) -> HostState | None:
        """Return the locally-cached host state (fetched from upstream on startup)."""
        return self._local_host_state

    async def _get_local_batman_targets(self) -> list[dict]:
        """Discover batman-capable guests (LXC containers + router VM) on this host.

        Returns a list of dicts with keys:
          vmid, label, kind ("lxc" or "vm"), reach_ip (for VMs only)
        """
        if not self._pve:
            raise ValueError("PVE API not configured")
        targets: list[dict] = []
        for vmid, label in [(_MESH_CT_ID, "mesh"), (_BRIDGE_CT_ID, "bridge")]:
            st = await asyncio.to_thread(self._pve.ct_status, vmid)
            if st and st.get("status") == "running":
                targets.append({"vmid": vmid, "label": label, "kind": "lxc"})
        st = await asyncio.to_thread(self._pve.vm_status, _ROUTER_VM_ID)
        if st and st.get("status") == "running":
            targets.append({
                "vmid": _ROUTER_VM_ID, "label": "router",
                "kind": "vm", "reach_ip": ROUTER_VM_LAN_IP,
            })
        return targets

    async def _batman_exec(self, target: dict, script_args: str) -> tuple[bool, str]:
        """Invoke batman_trigger.sh on a guest via its HTTP command endpoint."""
        if target["kind"] == "lxc":
            ct_ip = _resolve_container_ip(self, target["vmid"])
            if not ct_ip:
                return False, f"Cannot resolve IP for VMID {target['vmid']}"
            return await _callhome_exec(
                ct_ip, f"/usr/sbin/batman_trigger.sh {script_args}",
                self._callhome_public_key, 30, vmid=target["vmid"],
            )
        return await _callhome_exec(
            target["reach_ip"], f"/usr/sbin/batman_trigger.sh {script_args}",
            self._callhome_public_key, 30, vmid=target["vmid"],
        )

    async def batman_local(self, action: str, token: str) -> dict:
        """Execute batman_trigger.sh on this node's mesh/bridge containers and router VM.

        All targets are executed in parallel via asyncio.gather.
        """
        host = self._host_name or "unknown"
        targets = await self._get_local_batman_targets()

        async def _exec_one(t: dict) -> tuple[str, dict]:
            ok, out = await self._batman_exec(t, f"{action} {token}")
            return f"{host}/{t['label']}-{t['vmid']}", {"success": ok, "output": out[:300]}

        gathered = await asyncio.gather(*[_exec_one(t) for t in targets])
        return dict(gathered)

    async def batman_local_status(self) -> dict:
        """Query batman status from this node's mesh/bridge containers and router VM.

        All targets are queried in parallel via asyncio.gather.
        """
        host = self._host_name or "unknown"
        targets = await self._get_local_batman_targets()

        async def _status_one(t: dict) -> tuple[str, dict]:
            ok, out = await self._batman_exec(t, "status")
            key = f"{host}/{t['label']}-{t['vmid']}"
            if ok:
                active = "BATMAN=active" in out
                originators = heartbeat._parse_batman_originators(out)
                iface_section = out.split("---INTERFACES---")[1] if "---INTERFACES---" in out else ""
                interfaces = heartbeat._parse_batman_interfaces(iface_section)
                return key, {
                    "active": active, "originators": originators,
                    "interfaces": interfaces,
                }
            return key, {"active": False, "error": out[:200]}

        gathered = await asyncio.gather(*[_status_one(t) for t in targets])
        return dict(gathered)

    async def switch_desktop_session(self, session: str) -> dict:
        """Switch the Desktop container's KasmVNC session between KDE and GNOME."""
        if session not in _VALID_DESKTOP_SESSIONS:
            return {"success": False, "error": f"Invalid session: {session!r} (use kde or gnome)"}
        ct_ip = _resolve_container_ip(self, _DESKTOP_CT_ID)
        if not ct_ip:
            return {"success": False, "error": f"Cannot resolve IP for desktop CT {_DESKTOP_CT_ID}"}
        ok, out = await _callhome_exec(
            ct_ip, f"/usr/sbin/switch-desktop-session {session}",
            self._callhome_public_key, 30, vmid=_DESKTOP_CT_ID,
        )
        if ok and f"SESSION={session}" in out:
            return {"success": True, "session": session}
        return {"success": False, "error": out[:300]}

    async def get_desktop_session(self) -> dict:
        """Query the current desktop session (kde or gnome)."""
        ct_ip = _resolve_container_ip(self, _DESKTOP_CT_ID)
        if not ct_ip:
            return {"session": "unknown", "error": f"Cannot resolve IP for desktop CT {_DESKTOP_CT_ID}"}
        ok, out = await _callhome_exec(
            ct_ip, "/usr/sbin/switch-desktop-session status",
            self._callhome_public_key, 10, vmid=_DESKTOP_CT_ID,
        )
        if ok:
            for line in out.splitlines():
                if line.startswith("SESSION="):
                    return {"session": line.split("=", 1)[1]}
        return {"session": "unknown"}

    def _relay_to_cluster_manager(
        self, path: str, *, method: str = "POST", token: str = "",
    ) -> dict:
        """Relay a request to this node's Cluster Manager.

        NodeManagers use this to forward cluster-scoped operations (batman
        enable/disable/status) UP to the CM, which then broadcasts to all
        children.  Returns the CM's JSON response or an error dict.
        """
        if not self._management_server:
            return {"error": "MANAGEMENT_SERVER not configured"}

        url = f"{self._management_server}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("x-callhome-token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            return {"error": f"CM relay failed: {exc}"}

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
                return JSONResponse({
                    "status": "no_data",
                    "node_id": node_id,
                    "metric_type": metric_type,
                    "data": {},
                    "success": False,
                    "error": f"No {metric_type} metrics collected yet for {node_id}. "
                             f"Ensure the Node Manager is running and the metric "
                             f"subscription is active.",
                })
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

        # ── Health and circuit breaker endpoints ───────────────────

        async def _api_health(request: StarletteRequest) -> JSONResponse:
            return JSONResponse({"status": "ok", "host": mgr._host_name or "unknown"})

        async def _api_circuit_breakers(request: StarletteRequest) -> JSONResponse:
            breakers: dict[str, dict] = {}
            with heartbeat._circuit_lock:
                for ip, cb in heartbeat._circuit_breakers.items():
                    breakers[ip] = heartbeat.get_circuit_status(ip)
            return JSONResponse(breakers)

        async def _api_circuit_reset(request: StarletteRequest) -> JSONResponse:
            ip = request.path_params.get("ip", "")
            heartbeat.reset_circuit(ip)
            return JSONResponse({"reset": ip, "status": heartbeat.get_circuit_status(ip)})

        async def _api_callhome_restart(request: StarletteRequest) -> JSONResponse:
            """Restart callhome on all running containers via their HTTP command endpoint.

            Fan-out is parallelized via asyncio.gather.
            """
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            try:
                cts = await asyncio.to_thread(pve.ct_list)
            except PveApiError as exc:
                return JSONResponse({"error": str(exc)[:300]}, status_code=500)

            targets: list[tuple[str, int]] = []
            for ct in cts:
                if ct.get("status") != "running":
                    continue
                ct_ip = _resolve_container_ip(mgr, ct["vmid"])
                if ct_ip:
                    targets.append((ct_ip, ct["vmid"]))

            async def _restart_one(ip: str, vmid: int) -> bool:
                ok, _ = await _callhome_exec(
                    ip, "systemctl restart callhome",
                    mgr._callhome_public_key, 15, vmid=vmid,
                )
                return ok

            results = await asyncio.gather(
                *[_restart_one(ip, vmid) for ip, vmid in targets],
            )
            return JSONResponse({"restarted": sum(1 for ok in results if ok)})

        starlette_app.routes.insert(0, Route(
            "/api/health", _api_health, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/circuit-breakers", _api_circuit_breakers, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/circuit-breakers/{ip:path}/reset", _api_circuit_reset, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/callhome/restart", _api_callhome_restart, methods=["POST"],
        ))

        # ── Guest management (local host only) ───────────────────

        async def _api_guests(request: StarletteRequest) -> JSONResponse:
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            try:
                ct_list, vm_list = await asyncio.gather(
                    asyncio.to_thread(pve.ct_list),
                    asyncio.to_thread(pve.vm_list),
                )
            except PveApiError as exc:
                return JSONResponse({"error": str(exc)[:300]}, status_code=502)
            guests: list[dict] = []
            for ct in ct_list:
                guests.append({
                    "vmid": str(ct["vmid"]), "name": ct.get("name", "?"),
                    "status": ct["status"], "type": "lxc",
                })
            for vm in vm_list:
                guests.append({
                    "vmid": str(vm["vmid"]), "name": vm.get("name", "?"),
                    "status": vm["status"], "type": "qemu",
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
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            vid = int(vmid)
            gtype = await asyncio.to_thread(pve.guest_type, vid)
            if not gtype:
                return JSONResponse({"error": f"VMID {vmid} not found"}, status_code=404)
            try:
                if action in ("stop", "restart"):
                    fn = pve.ct_stop if gtype == "lxc" else pve.vm_stop
                    upid = await asyncio.to_thread(fn, vid)
                    if upid:
                        await asyncio.to_thread(pve.wait_for_task, upid, 30)
                if action in ("start", "restart"):
                    fn = pve.ct_start if gtype == "lxc" else pve.vm_start
                    upid = await asyncio.to_thread(fn, vid)
                    if upid:
                        await asyncio.to_thread(pve.wait_for_task, upid, 30)
                return JSONResponse({"vmid": vmid, "action": action, "success": True})
            except PveApiError as exc:
                return JSONResponse({
                    "vmid": vmid, "action": action, "success": False,
                    "output": str(exc)[:300],
                }, status_code=500)

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
            token = _hmac.new(
                mgr._mesh_key.encode(), f"{action}_batman".encode(), hashlib.sha256,
            ).hexdigest()
            result = await mgr.batman_local(action, token)
            return JSONResponse(result)

        async def _api_batman_local_status(request: StarletteRequest) -> JSONResponse:
            result = await mgr.batman_local_status()
            return JSONResponse(result)

        # ── Cluster-scoped batman (relay to CM if we're a NodeManager) ──

        async def _api_batman_enable(request: StarletteRequest) -> JSONResponse:
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            token = request.headers.get("x-callhome-token", "")
            result = await asyncio.to_thread(
                mgr._relay_to_cluster_manager,
                "/api/batman/enable", method="POST", token=token,
            )
            if "error" in result and "CM relay" in result.get("error", ""):
                return JSONResponse(result, status_code=502)
            return JSONResponse(result)

        async def _api_batman_disable(request: StarletteRequest) -> JSONResponse:
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            token = request.headers.get("x-callhome-token", "")
            result = await asyncio.to_thread(
                mgr._relay_to_cluster_manager,
                "/api/batman/disable", method="POST", token=token,
            )
            if "error" in result and "CM relay" in result.get("error", ""):
                return JSONResponse(result, status_code=502)
            return JSONResponse(result)

        async def _api_batman_status(request: StarletteRequest) -> JSONResponse:
            result = await asyncio.to_thread(
                mgr._relay_to_cluster_manager,
                "/api/batman/status", method="GET",
            )
            if "error" in result and "CM relay" in result.get("error", ""):
                return JSONResponse(result, status_code=502)
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
            "/api/batman/enable", _api_batman_enable, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/batman/disable", _api_batman_disable, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/batman/status", _api_batman_status, methods=["GET"],
        ))

        # ── Local WiFi endpoints (operate on THIS host's containers) ──

        async def _api_wifi_local_status(request: StarletteRequest) -> JSONResponse:
            """WiFi status for local mesh/bridge containers via callhome HTTP.

            Both containers are queried in parallel via asyncio.gather.
            """
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)

            async def _query_wifi(vmid: int, label: str) -> tuple[str, dict] | None:
                st = await asyncio.to_thread(pve.ct_status, vmid)
                if not st or st.get("status") != "running":
                    return None
                ct_ip = _resolve_container_ip(mgr, vmid)
                if not ct_ip:
                    return label, {"error": f"Cannot resolve IP for VMID {vmid}"}
                ok, out = await _callhome_exec(
                    ct_ip, "/usr/sbin/wifi_setup.sh status",
                    mgr._callhome_public_key, 10, vmid=vmid,
                )
                if not ok:
                    return label, {"error": out[:200]}
                status: dict[str, str] = {}
                for line in out.strip().splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        status[k.strip().lower()] = v.strip()
                return label, status

            gathered = await asyncio.gather(
                _query_wifi(_MESH_CT_ID, "mesh"),
                _query_wifi(_BRIDGE_CT_ID, "bridge"),
            )
            results: dict[str, dict] = {}
            for item in gathered:
                if item is not None:
                    results[item[0]] = item[1]
            return JSONResponse(results)

        async def _api_wifi_local_restart(request: StarletteRequest) -> JSONResponse:
            """Restart WiFi on local mesh/bridge containers via callhome HTTP.

            Both containers are restarted in parallel via asyncio.gather.
            """
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)

            async def _restart_wifi(vmid: int, label: str) -> tuple[str, dict] | None:
                st = await asyncio.to_thread(pve.ct_status, vmid)
                if not st or st.get("status") != "running":
                    return None
                ct_ip = _resolve_container_ip(mgr, vmid)
                if not ct_ip:
                    return None
                ok, out = await _callhome_exec(
                    ct_ip, "/usr/sbin/wifi_setup.sh restart",
                    mgr._callhome_public_key, 15, vmid=vmid,
                )
                return label, {"success": ok, "output": out[:200]}

            gathered = await asyncio.gather(
                _restart_wifi(_MESH_CT_ID, "mesh"),
                _restart_wifi(_BRIDGE_CT_ID, "bridge"),
            )
            results: dict[str, dict] = {}
            for item in gathered:
                if item is not None:
                    results[item[0]] = item[1]
            return JSONResponse(results)

        async def _api_wifi_local_mode(request: StarletteRequest) -> JSONResponse:
            """Switch WiFi mode on local bridge container via callhome HTTP."""
            mode = request.path_params.get("mode", "")
            if mode not in ("ap", "sta"):
                return JSONResponse(
                    {"error": f"Invalid mode: {mode} (expected ap or sta)"}, status_code=400,
                )
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            st = await asyncio.to_thread(pve.ct_status, _BRIDGE_CT_ID)
            if not st or st.get("status") != "running":
                return JSONResponse(
                    {"error": f"Bridge container {_BRIDGE_CT_ID} not running"}, status_code=404,
                )
            ct_ip = _resolve_container_ip(mgr, _BRIDGE_CT_ID)
            if not ct_ip:
                return JSONResponse(
                    {"error": f"Cannot resolve IP for bridge CT {_BRIDGE_CT_ID}"}, status_code=404,
                )
            ok, out = await _callhome_exec(
                ct_ip, f"/usr/sbin/wifi_setup.sh switch-mode {mode}",
                mgr._callhome_public_key, 30, vmid=_BRIDGE_CT_ID,
            )
            return JSONResponse({"mode": mode, "success": ok, "output": out[:300]})

        starlette_app.routes.insert(0, Route(
            "/api/wifi/local/status", _api_wifi_local_status, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/wifi/local/restart", _api_wifi_local_restart, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/wifi/local/mode/{mode}", _api_wifi_local_mode, methods=["POST"],
        ))

        # ── Desktop session switching ─────────────────────────────

        async def _api_desktop_session_switch(request: StarletteRequest) -> JSONResponse:
            session = request.path_params.get("session", "")
            result = await mgr.switch_desktop_session(session)
            code = 200 if result.get("success") else 400
            return JSONResponse(result, status_code=code)

        async def _api_desktop_session_status(request: StarletteRequest) -> JSONResponse:
            result = await mgr.get_desktop_session()
            return JSONResponse(result)

        starlette_app.routes.insert(0, Route(
            "/api/desktop/session/{session}",
            _api_desktop_session_switch, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/desktop/session",
            _api_desktop_session_status, methods=["GET"],
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
        async def _api_heartbeat_latest(request: StarletteRequest) -> JSONResponse:
            """Return this node's latest cached metric of the given type.

            Used by the SuperManager to collect metrics via HTTP.
            The SM doesn't know this NM's node_id, so this
            endpoint searches the cache for any entry matching the type.

            Query params:
              subscribe_node_id — auto-subscribe this node_id for the
              requested metric_type so the NM's poller starts collecting
              data for subsequent requests.
            """
            metric_type = request.path_params.get("metric_type", "")
            subscribe_node = request.query_params.get("subscribe_node_id", "")
            if subscribe_node and metric_type in mgr._collector_map:
                ip = mgr.resolve_node_ip(subscribe_node)
                if ip:
                    mgr.subscription_mgr.subscribe(subscribe_node, metric_type, ttl_seconds=60)
            cached = None
            for sub in mgr.subscription_mgr.list_subscriptions():
                if sub.metric_type == metric_type:
                    cached = mgr.metric_cache.get(sub.node_id, metric_type)
                    if cached:
                        break
            if not cached and mgr._host_name:
                cached = mgr.metric_cache.get(mgr._host_name, metric_type)
            if cached is None:
                return JSONResponse({
                    "status": "no_data",
                    "metric_type": metric_type,
                    "data": {},
                    "success": False,
                    "error": f"No {metric_type} metrics collected yet. "
                             f"Ensure a subscription is active and the "
                             f"collector has run at least once.",
                })
            return JSONResponse({
                "node_id": cached.node_id,
                "metric_type": cached.metric_type,
                "data": cached.data,
                "collected_at": cached.collected_at,
                "success": cached.success,
                "error": cached.error,
            })

        starlette_app.routes.insert(0, Route(
            "/api/heartbeat/subscriptions",
            _api_heartbeat_subscriptions, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/heartbeat/latest/{metric_type}",
            _api_heartbeat_latest, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/guests", _api_guests, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/guests/{vmid}/{action}",
            _api_guest_action, methods=["POST"],
        ))

        # ── Self-config endpoint ──

        async def _api_config_self(request: StarletteRequest) -> JSONResponse:
            """Return this kiosk's own config.json keys and 4-tier settings.

            Eliminates the need for pct exec to read config.json during verify.
            """
            from scripts.webui.data import load_kiosk_config
            config = load_kiosk_config()
            return JSONResponse({
                "keys": sorted(config.keys()),
                "MANAGEMENT_SERVER": config.get("MANAGEMENT_SERVER", ""),
                "IS_CLUSTER_MANAGER": config.get("IS_CLUSTER_MANAGER", "false"),
                "HOST_NAME": config.get("HOST_NAME", ""),
                "HOST_IP": config.get("HOST_IP", ""),
            })

        starlette_app.routes.insert(0, Route(
            "/api/config/self", _api_config_self, methods=["GET"],
        ))

        # ── Display transfer endpoints ────────────────────────────

        async def _api_display_enter(request: StarletteRequest) -> JSONResponse:
            app_id = request.path_params.get("app_id", "")
            if not mgr._host_ip:
                return JSONResponse({"error": "HOST_IP not configured"}, status_code=500)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            result = mgr.display_transfer.enter(app_id, mgr._host_ip)
            status_code = 200 if result.success else 502
            handler = mgr.display_transfer.get_handler(app_id)
            return JSONResponse({
                "app_id": app_id, "success": result.success,
                "viewstream_url": result.viewstream_url,
                "handler_type": handler.handler_type if handler else None,
                "error": result.error,
            }, status_code=status_code)

        async def _api_display_exit(request: StarletteRequest) -> JSONResponse:
            app_id = request.path_params.get("app_id", "")
            if not mgr._host_ip:
                return JSONResponse({"error": "HOST_IP not configured"}, status_code=500)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            result = mgr.display_transfer.exit(app_id, mgr._host_ip)
            status_code = 200 if result.success else 502
            return JSONResponse({
                "app_id": app_id, "success": result.success,
                "error": result.error,
            }, status_code=status_code)

        async def _api_display_status(request: StarletteRequest) -> JSONResponse:
            app_id = request.path_params.get("app_id", "")
            if not mgr._host_ip:
                return JSONResponse({"error": "HOST_IP not configured"}, status_code=500)
            handler = mgr.display_transfer.get_handler(app_id)
            if not handler:
                return JSONResponse({"error": f"No handler for {app_id}"}, status_code=404)
            active = mgr.display_transfer.is_active(app_id, mgr._host_ip)
            url = handler.get_viewstream_url(mgr._host_ip) if active else None
            return JSONResponse({
                "app_id": app_id, "active": active,
                "handler_type": handler.handler_type,
                "viewstream_url": url,
            })

        async def _api_display_list(request: StarletteRequest) -> JSONResponse:
            handlers = mgr.display_transfer.list_handlers()
            active: list[str] = []
            if mgr._host_ip:
                active = mgr.display_transfer.list_active(mgr._host_ip)
            return JSONResponse({"handlers": handlers, "active": active})

        starlette_app.routes.insert(0, Route(
            "/api/display/{app_id}/enter", _api_display_enter, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/display/{app_id}/exit", _api_display_exit, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/display/{app_id}/status", _api_display_status, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/display/list", _api_display_list, methods=["GET"],
        ))

        # ── Host state endpoints (Manager as source of truth) ─────

        def _no_store() -> JSONResponse:
            return JSONResponse(
                {"error": "state store not configured"}, status_code=501,
            )

        def _bad_json() -> JSONResponse:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        def _require_store() -> HostStateStore | None:
            """Return the store or None (caller returns _no_store())."""
            return mgr.host_state_store

        async def _parse_body(request: StarletteRequest) -> dict | None:
            """Parse JSON body; returns None on malformed input."""
            try:
                return await request.json()
            except (ValueError, TypeError, _json.JSONDecodeError):
                return None

        async def _api_host_state_get(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            store = _require_store()
            if not store:
                return _no_store()
            state = store.get(host_id)
            if state is None:
                return JSONResponse({"error": "unknown host"}, status_code=404)
            return JSONResponse(state.to_dict())

        async def _api_host_hardware_post(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            store = _require_store()
            if not store:
                return _no_store()
            body = await _parse_body(request)
            if body is None:
                return _bad_json()
            store.get_or_create(host_id, body.get("ip", ""))
            result = store.update_hardware(host_id, body)
            if result is None:
                return JSONResponse({"error": "unknown host"}, status_code=404)
            return JSONResponse(result.to_dict(), status_code=200)

        async def _api_host_bridges_post(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            store = _require_store()
            if not store:
                return _no_store()
            body = await _parse_body(request)
            if body is None:
                return _bad_json()
            store.get_or_create(host_id, body.get("ip", ""))
            result = store.update_bridges(host_id, body)
            if result is None:
                return JSONResponse({"error": "unknown host"}, status_code=404)
            return JSONResponse(result.to_dict(), status_code=200)

        async def _api_host_container_post(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            vmid_str = request.path_params["vmid"]
            store = _require_store()
            if not store:
                return _no_store()
            try:
                vmid = int(vmid_str)
            except ValueError:
                return JSONResponse({"error": f"Invalid VMID: {vmid_str}"}, status_code=400)
            body = await _parse_body(request)
            if body is None:
                return _bad_json()
            result = store.register_container(host_id, vmid, body)
            if result is None:
                return JSONResponse({"error": "unknown host"}, status_code=404)
            return JSONResponse(result.to_dict(), status_code=201)

        async def _api_host_container_delete(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            vmid_str = request.path_params["vmid"]
            store = _require_store()
            if not store:
                return _no_store()
            try:
                vmid = int(vmid_str)
            except ValueError:
                return JSONResponse({"error": f"Invalid VMID: {vmid_str}"}, status_code=400)
            result = store.deregister_container(host_id, vmid)
            if result is None:
                return JSONResponse({"error": "unknown host"}, status_code=404)
            return JSONResponse({"status": "ok"}, status_code=200)

        async def _api_host_phy_patch(request: StarletteRequest) -> JSONResponse:
            host_id = request.path_params["id"]
            phy_name = request.path_params["name"]
            store = _require_store()
            if not store:
                return _no_store()
            body = await _parse_body(request)
            if body is None:
                return _bad_json()
            namespace = body.get("namespace")
            if not namespace:
                return JSONResponse({"error": "namespace required"}, status_code=400)
            result = store.update_phy_namespace(host_id, phy_name, namespace)
            if result is None:
                return JSONResponse({"error": "unknown host or PHY"}, status_code=404)
            return JSONResponse(result.to_dict(), status_code=200)

        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/state", _api_host_state_get, methods=["GET"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/hardware", _api_host_hardware_post, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/bridges", _api_host_bridges_post, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/containers/{vmid}",
            _api_host_container_post, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/containers/{vmid}",
            _api_host_container_delete, methods=["DELETE"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/host/{id}/hardware/phy/{name}",
            _api_host_phy_patch, methods=["PATCH"],
        ))

        # ── Container provisioning endpoints ──────────────────────
        # NM executes pct create/destroy locally. The controller calls
        # these via HTTP over VPN.

        async def _api_provision_create(request: StarletteRequest) -> JSONResponse:
            """Full container lifecycle: version check, create/rebuild, start, wait."""
            try:
                return await _do_provision_create(request)
            except (PveApiError, OSError, ValueError, TypeError, KeyError,
                    RuntimeError, TimeoutError) as exc:
                import traceback
                tb = traceback.format_exc()
                logging.getLogger("vm_builds.provision").error(
                    "Provision endpoint crashed: %s\n%s", exc, tb,
                )
                return JSONResponse({
                    "success": False, "status": "internal_error",
                    "output": f"{type(exc).__name__}: {exc}",
                    "traceback": tb[:2000],
                }, status_code=500)

        async def _do_provision_create(request: StarletteRequest) -> JSONResponse:
            """Inner implementation of provision create — uses PVE API."""
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            body = await request.json()
            vmid = body.get("vmid")
            template = body.get("template_path", "")
            hostname = body.get("hostname", "")
            memory = body.get("memory", 512)
            swap = body.get("swap", 256)
            cores = body.get("cores", 1)
            disk = body.get("disk", "4")
            storage = body.get("storage", "local-lvm")
            ip = body.get("ip", "")
            gateway = body.get("gateway", "")
            bridge = body.get("bridge", "vmbr0")
            nameserver = body.get("nameserver", "")
            features = body.get("features", "")
            unprivileged = body.get("unprivileged", True)
            onboot = body.get("onboot", True)
            startup_order = body.get("startup_order", 0)
            ostype = body.get("ostype", "")
            mount_entries = body.get("mount_entries", [])
            lxc_conf_entries = body.get("lxc_conf_entries", [])
            image_version = body.get("image_version", "")
            force_rebuild = body.get("force_rebuild", False)
            if not vmid or not template:
                return JSONResponse(
                    {"error": "vmid and template_path are required"}, status_code=400,
                )
            action_log: list[str] = []
            plog = logging.getLogger("vm_builds.provision")
            t0 = time.monotonic()
            handler_deadline = t0 + 600

            def _elapsed() -> str:
                return f"{time.monotonic() - t0:.1f}s"

            def _remaining() -> float:
                return max(handler_deadline - time.monotonic(), 0)

            plog.info("[CT %s] Provision start", vmid)
            ct_status = await asyncio.to_thread(pve.ct_status, int(vmid))
            ct_exists = ct_status is not None
            ct_running = ct_exists and ct_status.get("status") == "running"
            need_create = not ct_exists
            plog.info("[CT %s] Status check: exists=%s running=%s (%s)", vmid, ct_exists, ct_running, _elapsed())

            if ct_exists and (force_rebuild or image_version):
                deployed_ver = ""
                cfg = await asyncio.to_thread(pve.ct_config, int(vmid))
                if cfg:
                    desc = cfg.get("description", "")
                    for part in desc.replace("%0A", "\n").splitlines():
                        if part.startswith("image_version="):
                            deployed_ver = part.split("=", 1)[1].strip()
                            break
                if force_rebuild or (image_version and deployed_ver != image_version):
                    action_log.append(f"Version mismatch: deployed={deployed_ver}, target={image_version}")
                    plog.info("[CT %s] Destroying stale container (deployed=%s, target=%s) (%s)", vmid, deployed_ver, image_version, _elapsed())
                    await asyncio.to_thread(pve.ct_stop_and_destroy, int(vmid), 60)
                    plog.info("[CT %s] Destroy complete (%s)", vmid, _elapsed())
                    need_create = True
                else:
                    action_log.append(f"Version match: {deployed_ver}")

            if not need_create:
                if ct_exists and not ct_running:
                    upid = await asyncio.to_thread(pve.ct_start, int(vmid))
                    if upid:
                        await asyncio.to_thread(pve.wait_for_task, upid, 60)
                    action_log.append("Started existing container")
                    return JSONResponse({
                        "success": True, "vmid": vmid, "status": "started",
                        "log": action_log,
                        "elapsed_seconds": round(time.monotonic() - t0, 1),
                    })
                return JSONResponse({
                    "success": True, "vmid": vmid, "status": "already_running",
                    "log": action_log,
                    "elapsed_seconds": round(time.monotonic() - t0, 1),
                })

            tmpl_ref = f"local:vztmpl/{template}" if "/" not in template else template
            net_spec = f"name=eth0,bridge={bridge},firewall=0"
            if ip:
                net_spec += f",ip={ip}"
            if gateway:
                net_spec += f",gw={gateway}"

            create_kwargs: dict[str, str | int | bool] = {
                "ostemplate": tmpl_ref,
                "memory": memory,
                "swap": swap,
                "cores": cores,
                "rootfs": f"{storage}:{disk}",
                "net0": net_spec,
                "unprivileged": 1 if unprivileged else 0,
                "onboot": 1 if onboot else 0,
                "start": 0,
            }
            if hostname:
                create_kwargs["hostname"] = hostname
            if nameserver:
                create_kwargs["nameserver"] = nameserver
            if features:
                create_kwargs["features"] = features
            if startup_order:
                create_kwargs["startup"] = f"order={startup_order}"
            if ostype:
                create_kwargs["ostype"] = ostype
            if image_version:
                create_kwargs["description"] = f"image_version={image_version}"

            deferred_host_config: dict = {}

            plog.info("[CT %s] Creating container (%s)", vmid, _elapsed())
            try:
                upid = await asyncio.to_thread(
                    pve.ct_create, int(vmid), **create_kwargs,
                )
                plog.info("[CT %s] ct_create returned UPID, waiting for task (%s)", vmid, _elapsed())
                await asyncio.to_thread(pve.wait_for_task, upid, int(_remaining()))
                plog.info("[CT %s] Create task completed (%s)", vmid, _elapsed())
            except PveApiError as exc:
                if exc.status == 403 and features:
                    action_log.append(
                        f"PVE 403 with features={features} "
                        "(API tokens cannot set feature flags on privileged CTs), "
                        "creating without — Ansible will apply via pct set on host"
                    )
                    deferred_host_config["features"] = features
                    plog.info("[CT %s] 403 on features, retrying without (%s)", vmid, _elapsed())
                    kw_no_feat = {k: v for k, v in create_kwargs.items() if k != "features"}
                    upid = await asyncio.to_thread(
                        pve.ct_create, int(vmid), **kw_no_feat,
                    )
                    plog.info("[CT %s] Retry ct_create returned UPID, waiting (%s)", vmid, _elapsed())
                    await asyncio.to_thread(pve.wait_for_task, upid, int(_remaining()))
                    plog.info("[CT %s] Retry create task completed (%s)", vmid, _elapsed())
                else:
                    plog.error("[CT %s] Create failed: %s (%s)", vmid, exc, _elapsed())
                    return JSONResponse({
                        "success": False, "vmid": vmid, "status": "create_failed",
                        "log": action_log,
                        "output": f"PVE API {exc.status}: {exc}",
                    }, status_code=500)
            action_log.append("Container created")

            deferred_mounts: list[str] = []
            for idx, mount in enumerate(mount_entries):
                try:
                    await asyncio.to_thread(
                        pve.ct_set, int(vmid), **{f"mp{idx}": mount},
                    )
                    action_log.append(f"Mount {idx}: {mount}")
                except PveApiError as exc:
                    if exc.status == 403:
                        deferred_mounts.append(mount)
                        action_log.append(f"Mount {idx} deferred (403): {mount}")
                    else:
                        action_log.append(f"Mount {idx} failed: {exc}")
            if deferred_mounts:
                deferred_host_config["mount_entries"] = deferred_mounts

            deferred_lxc_conf: list[str] = []
            if lxc_conf_entries:
                plog.info("[CT %s] Applying %d LXC conf entries via PVE API (%s)", vmid, len(lxc_conf_entries), _elapsed())
                for entry in lxc_conf_entries:
                    deferred_lxc_conf.append(entry)
                    action_log.append(f"LXC conf deferred for host: {entry}")
            if deferred_lxc_conf:
                deferred_host_config["lxc_conf_entries"] = deferred_lxc_conf

            if deferred_host_config:
                plog.info("[CT %s] Deferred host config: %s — not starting (%s)", vmid, list(deferred_host_config.keys()), _elapsed())
                action_log.append("Container NOT started — deferred config requires host-side pct set")
                svc_type = request.path_params.get("service_type", "")
                if image_version and svc_type and mgr.host_state_store and mgr._host_name:
                    state = mgr.host_state_store.get(mgr._host_name)
                    if state is not None:
                        state.containers[int(vmid)] = ContainerInfo(
                            vmid=int(vmid),
                            service_type=svc_type,
                            hostname=hostname or svc_type,
                            state="created",
                            image_version=image_version,
                        )
                        mgr.host_state_store.save(state)
                elapsed_s = round(time.monotonic() - t0, 1)
                return JSONResponse({
                    "success": True, "vmid": vmid,
                    "status": "created_needs_host_config",
                    "log": action_log,
                    "deferred_host_config": deferred_host_config,
                    "elapsed_seconds": elapsed_s,
                })

            plog.info("[CT %s] Starting container (%s)", vmid, _elapsed())
            try:
                upid = await asyncio.to_thread(pve.ct_start, int(vmid))
                if upid:
                    await asyncio.to_thread(pve.wait_for_task, upid, min(60, int(_remaining())))
            except PveApiError as exc:
                plog.error("[CT %s] Start failed: %s (%s)", vmid, exc, _elapsed())
                return JSONResponse({
                    "success": False, "vmid": vmid, "status": "start_failed",
                    "log": action_log,
                    "output": f"PVE API start failed: {exc}",
                }, status_code=500)
            action_log.append("Container started")
            plog.info("[CT %s] Container started (%s)", vmid, _elapsed())

            for _attempt in range(5):
                st = await asyncio.to_thread(pve.ct_status, int(vmid))
                if st and st.get("status") == "running":
                    break
                await asyncio.sleep(2)
            else:
                action_log.append("WARNING: init wait timed out after 10s")

            if ostype != "unmanaged":
                for _attempt in range(15):
                    has_ip = await asyncio.to_thread(pve.ct_has_ip, int(vmid))
                    if has_ip:
                        action_log.append("Networking ready")
                        break
                    await asyncio.sleep(3)
                else:
                    action_log.append("WARNING: networking wait timed out after 45s")

            svc_type = request.path_params.get("service_type", "")
            if image_version and svc_type and mgr.host_state_store and mgr._host_name:
                state = mgr.host_state_store.get(mgr._host_name)
                if state is not None:
                    state.containers[int(vmid)] = ContainerInfo(
                        vmid=int(vmid),
                        service_type=svc_type,
                        hostname=hostname or svc_type,
                        state="running",
                        image_version=image_version,
                    )
                    mgr.host_state_store.save(state)
                    action_log.append(f"Registered version {image_version} for {svc_type}")

            elapsed_s = round(time.monotonic() - t0, 1)
            plog.info("[CT %s] Provision complete (%s)", vmid, _elapsed())
            resp: dict = {
                "success": True, "vmid": vmid, "status": "created",
                "log": action_log,
                "elapsed_seconds": elapsed_s,
            }
            return JSONResponse(resp)

        async def _api_provision_destroy(request: StarletteRequest) -> JSONResponse:
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            try:
                await asyncio.to_thread(pve.ct_stop_and_destroy, int(vmid), 60)
            except PveApiError as exc:
                if "does not exist" in str(exc).lower() or "not found" in str(exc).lower():
                    return JSONResponse({"success": True, "vmid": vmid, "output": "does not exist"})
                return JSONResponse({
                    "success": False, "vmid": vmid,
                    "output": str(exc)[:300],
                }, status_code=500)
            return JSONResponse({"success": True, "vmid": vmid, "output": "destroyed"})

        async def _api_provision_status(request: StarletteRequest) -> JSONResponse:
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            ct_st = await asyncio.to_thread(pve.ct_status, int(vmid))
            if ct_st:
                return JSONResponse({"vmid": vmid, "status": ct_st.get("status", "unknown"), "type": "lxc"})
            vm_st = await asyncio.to_thread(pve.vm_status, int(vmid))
            if vm_st:
                return JSONResponse({"vmid": vmid, "status": vm_st.get("status", "unknown"), "type": "qemu"})
            return JSONResponse({"vmid": vmid, "status": "absent"})

        async def _api_provision_exec(request: StarletteRequest) -> JSONResponse:
            """Execute a command inside a container via its callhome command endpoint."""
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            body = await request.json()
            cmd = body.get("cmd", "")
            timeout = min(body.get("timeout", 30), 300)
            if not cmd:
                return JSONResponse({"error": "cmd is required"}, status_code=400)
            ct_ip = _resolve_container_ip(mgr, int(vmid))
            if not ct_ip:
                return JSONResponse({"error": f"Cannot resolve IP for VMID {vmid}"}, status_code=404)
            ok, out = await _callhome_exec(ct_ip, cmd, mgr._callhome_public_key, timeout, vmid=int(vmid))
            return JSONResponse({"success": ok, "vmid": vmid, "output": out[:2000]})

        async def _api_provision_pct_set(request: StarletteRequest) -> JSONResponse:
            """Modify container config via PVE API."""
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            body = await request.json()
            config = body.get("config", {})
            if not config:
                return JSONResponse({"error": "config dict is required"}, status_code=400)
            try:
                await asyncio.to_thread(pve.ct_set, int(vmid), **config)
                return JSONResponse({"success": True, "vmid": vmid})
            except PveApiError as exc:
                return JSONResponse({"success": False, "vmid": vmid, "output": str(exc)[:500]}, status_code=500)

        async def _api_provision_stop_start(request: StarletteRequest) -> JSONResponse:
            """Stop or start a container via PVE API."""
            vmid = request.path_params.get("vmid", "")
            action = request.path_params.get("action", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            if action not in ("stop", "start", "restart"):
                return JSONResponse({"error": f"Invalid action: {action}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)
            try:
                if action in ("stop", "restart"):
                    upid = await asyncio.to_thread(pve.ct_stop, int(vmid))
                    if upid:
                        await asyncio.to_thread(pve.wait_for_task, upid, 30)
                if action in ("start", "restart"):
                    upid = await asyncio.to_thread(pve.ct_start, int(vmid))
                    if upid:
                        await asyncio.to_thread(pve.wait_for_task, upid, 30)
                return JSONResponse({"success": True, "vmid": vmid, "action": action})
            except PveApiError as exc:
                return JSONResponse({
                    "success": False, "vmid": vmid, "action": action,
                    "output": str(exc)[:300],
                }, status_code=500)

        starlette_app.routes.insert(0, Route(
            "/api/provision/{vmid}/{action}",
            _api_provision_stop_start, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/provision/{vmid}",
            _api_provision_destroy, methods=["DELETE"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/provision/{service_type}",
            _api_provision_create, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/provision/{vmid}/set",
            _api_provision_pct_set, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/provision/{vmid}/exec",
            _api_provision_exec, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/provision/{vmid}/status",
            _api_provision_status, methods=["GET"],
        ))

        # ── Config push endpoints ─────────────────────────────────
        # Push files, edit configs, and manage services inside containers.

        async def _api_config_file(request: StarletteRequest) -> JSONResponse:
            """Write a file inside a container via its callhome command endpoint.

            Uses base64 to safely transport arbitrary file content through
            the shell without heredoc/quoting issues.
            """
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            body = await request.json()
            path = body.get("path", "")
            content = body.get("content", "")
            mode = body.get("mode", "0644")
            if not path or content is None:
                return JSONResponse(
                    {"error": "path and content are required"}, status_code=400,
                )
            ct_ip = _resolve_container_ip(mgr, int(vmid))
            if not ct_ip:
                return JSONResponse({"error": f"Cannot resolve IP for VMID {vmid}"}, status_code=404)
            import base64 as _b64
            b64 = _b64.b64encode(content.encode()).decode()
            write_cmd = f"echo '{b64}' | base64 -d > {path} && chmod {mode} {path}"
            ok, out = await _callhome_exec(ct_ip, write_cmd, mgr._callhome_public_key, 30, vmid=int(vmid))
            return JSONResponse({
                "success": ok, "vmid": vmid, "path": path, "output": out[:300],
            })

        async def _api_config_sed(request: StarletteRequest) -> JSONResponse:
            """Run sed on a file inside a container via its callhome command endpoint."""
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            body = await request.json()
            path = body.get("path", "")
            pattern = body.get("pattern", "")
            replacement = body.get("replacement", "")
            if not path or not pattern:
                return JSONResponse(
                    {"error": "path and pattern are required"}, status_code=400,
                )
            ct_ip = _resolve_container_ip(mgr, int(vmid))
            if not ct_ip:
                return JSONResponse({"error": f"Cannot resolve IP for VMID {vmid}"}, status_code=404)
            escaped_pattern = pattern.replace("'", "'\\''")
            escaped_replacement = replacement.replace("'", "'\\''")
            cmd = f"sed -i 's|{escaped_pattern}|{escaped_replacement}|g' {path}"
            ok, out = await _callhome_exec(ct_ip, cmd, mgr._callhome_public_key, 15, vmid=int(vmid))
            return JSONResponse({
                "success": ok, "vmid": vmid, "path": path, "output": out[:300],
            })

        async def _api_config_service(request: StarletteRequest) -> JSONResponse:
            """Manage a systemd service inside a container via callhome HTTP."""
            vmid = request.path_params.get("vmid", "")
            service = request.path_params.get("service", "")
            action = request.path_params.get("action", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            if action not in ("start", "stop", "restart", "status"):
                return JSONResponse({"error": f"Invalid action: {action}"}, status_code=400)
            if action != "status":
                auth_err = mgr.check_mutation_auth(request)
                if auth_err:
                    return auth_err
            ct_ip = _resolve_container_ip(mgr, int(vmid))
            if not ct_ip:
                return JSONResponse({"error": f"Cannot resolve IP for VMID {vmid}"}, status_code=404)
            ok, out = await _callhome_exec(
                ct_ip, f"systemctl {action} {service}",
                mgr._callhome_public_key, 30, vmid=int(vmid),
            )
            return JSONResponse({
                "success": ok, "vmid": vmid,
                "service": service, "action": action,
                "output": out[:500],
            })

        async def _api_container_exec(request: StarletteRequest) -> JSONResponse:
            """Run a command inside a container via its callhome HTTP endpoint."""
            vmid = request.path_params.get("vmid", "")
            if not vmid.isdigit():
                return JSONResponse({"error": f"Invalid VMID: {vmid}"}, status_code=400)
            body = await request.json()
            cmd = body.get("cmd", "")
            timeout_s = body.get("timeout", 30)
            if not cmd:
                return JSONResponse({"error": "cmd is required"}, status_code=400)
            ct_ip = _resolve_container_ip(mgr, int(vmid))
            if not ct_ip:
                return JSONResponse(
                    {"error": f"Cannot resolve IP for VMID {vmid}"}, status_code=404,
                )
            ok, out = await _callhome_exec(
                ct_ip, cmd, mgr._callhome_public_key, timeout_s, vmid=int(vmid),
            )
            return JSONResponse({
                "success": ok, "vmid": vmid,
                "rc": 0 if ok else 1,
                "stdout": out[:4096], "stderr": "",
            })

        starlette_app.routes.insert(0, Route(
            "/api/container/{vmid}/exec",
            _api_container_exec, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/config/{vmid}/file",
            _api_config_file, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/config/{vmid}/sed",
            _api_config_sed, methods=["POST"],
        ))
        starlette_app.routes.insert(0, Route(
            "/api/config/{vmid}/service/{service}/{action}",
            _api_config_service, methods=["POST", "GET"],
        ))

        # ── Image version endpoint ────────────────────────────────
        # Uses add_api_route() instead of routes.insert() because
        # NiceGUI's page system can interfere with raw starlette Route
        # registration ordering during middleware stack compilation.

        async def _api_image_versions() -> JSONResponse:
            """Return deployed image versions from the Node Manager's state."""
            state = None
            if mgr.host_state_store and mgr._host_name:
                state = mgr.host_state_store.get(mgr._host_name)
            if state is None:
                state = mgr.get_local_host_state()
            if state is None:
                return JSONResponse({"versions": {}})
            return JSONResponse({"versions": state.image_versions()})

        starlette_app.add_api_route(
            "/api/images/versions", _api_image_versions, methods=["GET"],
            include_in_schema=False,
        )

        # ── Image build endpoints ──────────────────────────────────────
        # Distributed build: controller POSTs a recipe, NM builds locally.

        _build_locks: dict[str, asyncio.Lock] = {}


        async def _api_build_service(request: StarletteRequest) -> JSONResponse:
            """Build an LXC image from a YAML recipe on this host.

            POST /api/build/{service}
            Body: the full recipe YAML parsed as JSON.
            """
            import base64 as _b64
            import yaml as _yaml

            service = request.path_params.get("service", "")
            if not service:
                return JSONResponse({"error": "service name required"}, status_code=400)
            auth_err = mgr.check_mutation_auth(request)
            if auth_err:
                return auth_err
            pve = mgr._pve
            if not pve:
                return JSONResponse({"error": "PVE API not configured"}, status_code=500)

            if service not in _build_locks:
                _build_locks[service] = asyncio.Lock()
            if _build_locks[service].locked():
                return JSONResponse(
                    {"error": f"Build already in progress for {service}"},
                    status_code=409,
                )

            try:
                body_raw = await request.body()
                recipe = _yaml.safe_load(body_raw.decode())
            except Exception as exc:
                return JSONResponse(
                    {"error": f"Invalid recipe YAML: {exc}"}, status_code=400,
                )

            async with _build_locks[service]:
                t0 = time.monotonic()
                build_vmid = int(recipe.get("build_vmid", 998))
                base_tpl = recipe.get("base_template", "")
                hostname = recipe.get("hostname", f"{service}-build")
                memory = int(recipe.get("memory", 512))
                cores = int(recipe.get("cores", 1))
                disk = str(recipe.get("disk", "2"))
                unpriv = recipe.get("unprivileged", True)
                features = recipe.get("features", "")
                ostype = recipe.get("ostype", "")

                _log = logging.getLogger("vm_builds.build")
                _log.info("[%s] Starting build on %s (VMID %d)", service, mgr._host_name, build_vmid)

                try:
                    # 1. Destroy stale build container
                    await asyncio.to_thread(pve.ct_stop_and_destroy, build_vmid, 60)

                    # 2. Create build container
                    create_kwargs: dict[str, str | int] = {
                        "ostemplate": f"local:vztmpl/{base_tpl}",
                        "hostname": hostname,
                        "memory": memory,
                        "cores": cores,
                        "rootfs": f"local-lvm:{disk}",
                        "net0": "name=eth0,bridge=vmbr0,ip=dhcp",
                        "nameserver": "8.8.8.8",
                    }
                    if unpriv:
                        create_kwargs["unprivileged"] = 1
                    if features:
                        create_kwargs["features"] = features
                    if ostype:
                        create_kwargs["ostype"] = ostype
                    _log.info("[%s] Creating build CT %d", service, build_vmid)
                    await asyncio.to_thread(
                        pve.ct_create_and_start, build_vmid, 300, True, **create_kwargs,
                    )

                    # 3. Wait for callhome agent to register
                    _log.info("[%s] Waiting for callhome agent...", service)
                    ct_ip = None
                    for _attempt in range(45):
                        await asyncio.sleep(2)
                        ct_ip = _resolve_container_ip(mgr, build_vmid)
                        if ct_ip:
                            ok, _ = await _callhome_exec(
                                ct_ip, "echo ready",
                                mgr._callhome_public_key, 10, vmid=build_vmid,
                            )
                            if ok:
                                break
                            ct_ip = None
                    if not ct_ip:
                        raise RuntimeError(f"Build CT {build_vmid} never registered with callhome")
                    _log.info("[%s] Callhome agent ready at %s", service, ct_ip)

                    # 4. Execute setup_commands
                    for cmd in recipe.get("setup_commands", []):
                        _log.info("[%s] setup: %s", service, cmd[:80])
                        ok, out = await _callhome_exec(
                            ct_ip, cmd, mgr._callhome_public_key, 120, vmid=build_vmid,
                        )
                        if not ok:
                            raise RuntimeError(f"setup_command failed: {cmd}\n{out[:500]}")

                    # 5. Push config_files via base64 (before install —
                    #    installers often need pre-seeded config)
                    for cf in recipe.get("config_files", []):
                        fpath = cf.get("path", "")
                        content = cf.get("content", "")
                        mode = cf.get("mode", "0644")
                        mkdir = cf.get("mkdir", False)
                        owner = cf.get("owner", "")
                        if not fpath:
                            continue
                        _log.info("[%s] config_file: %s", service, fpath)
                        if mkdir:
                            parent = "/".join(fpath.split("/")[:-1])
                            await _callhome_exec(
                                ct_ip, f"mkdir -p {parent}",
                                mgr._callhome_public_key, 10, vmid=build_vmid,
                            )
                        b64 = _b64.b64encode(content.encode()).decode()
                        write_cmd = f"sh -c 'echo {b64} | base64 -d > {fpath} && chmod {mode} {fpath}'"
                        ok, out = await _callhome_exec(
                            ct_ip, write_cmd, mgr._callhome_public_key, 30, vmid=build_vmid,
                        )
                        if not ok:
                            raise RuntimeError(f"config_file write failed: {fpath}\n{out[:300]}")
                        if owner:
                            await _callhome_exec(
                                ct_ip, f"chown {owner} {fpath}",
                                mgr._callhome_public_key, 10, vmid=build_vmid,
                            )

                    # 6. Execute install_commands
                    for cmd in recipe.get("install_commands", []):
                        _log.info("[%s] install: %s", service, cmd[:80])
                        ok, out = await _callhome_exec(
                            ct_ip, cmd, mgr._callhome_public_key, 600, vmid=build_vmid,
                        )
                        if not ok:
                            raise RuntimeError(f"install_command failed: {cmd}\n{out[:500]}")

                    # 7. Push baked_files from controller filesystem
                    for bf in recipe.get("baked_files", []):
                        src = bf.get("src", "")
                        dest = bf.get("dest", "")
                        mode = bf.get("mode", "0644")
                        if not src or not dest:
                            continue
                        src_path = Path(src)
                        if not src_path.is_absolute():
                            project_root = Path(__file__).resolve().parent.parent.parent
                            src_path = project_root / src
                        if not src_path.exists():
                            raise RuntimeError(f"baked_file source not found: {src_path}")
                        content = src_path.read_text()
                        b64 = _b64.b64encode(content.encode()).decode()
                        parent = "/".join(dest.split("/")[:-1])
                        await _callhome_exec(
                            ct_ip, f"mkdir -p {parent}",
                            mgr._callhome_public_key, 10, vmid=build_vmid,
                        )
                        write_cmd = f"sh -c 'echo {b64} | base64 -d > {dest} && chmod {mode} {dest}'"
                        ok, out = await _callhome_exec(
                            ct_ip, write_cmd, mgr._callhome_public_key, 30, vmid=build_vmid,
                        )
                        if not ok:
                            raise RuntimeError(f"baked_file write failed: {dest}\n{out[:300]}")

                    # 8. Write service-specific callhome env vars
                    # The callhome agent + systemd service are already in the
                    # base template. We only add service-specific probes/config.
                    callhome_cfg = recipe.get("callhome", {}) or {}
                    http_probes = callhome_cfg.get("http_probes", "")
                    config_files_env = callhome_cfg.get("config_files", "")
                    if http_probes or config_files_env:
                        _log.info("[%s] Writing callhome service config", service)
                        sed_cmds = []
                        if http_probes:
                            sed_cmds.append(
                                f"grep -q CALLHOME_HTTP_PROBES /etc/default/callhome"
                                f" && sed -i 's|^CALLHOME_HTTP_PROBES=.*|CALLHOME_HTTP_PROBES={http_probes}|'"
                                f" /etc/default/callhome"
                                f" || echo 'CALLHOME_HTTP_PROBES={http_probes}' >> /etc/default/callhome",
                            )
                        if config_files_env:
                            sed_cmds.append(
                                f"grep -q CALLHOME_CONFIG_FILES /etc/default/callhome"
                                f" && sed -i 's|^CALLHOME_CONFIG_FILES=.*|CALLHOME_CONFIG_FILES={config_files_env}|'"
                                f" /etc/default/callhome"
                                f" || echo 'CALLHOME_CONFIG_FILES={config_files_env}' >> /etc/default/callhome",
                            )
                        for scmd in sed_cmds:
                            await _callhome_exec(
                                ct_ip, f"sh -c '{scmd}'",
                                mgr._callhome_public_key, 10, vmid=build_vmid,
                            )

                    # 9. Execute post_commands
                    for cmd in recipe.get("post_commands", []):
                        _log.info("[%s] post: %s", service, cmd[:80])
                        ok, out = await _callhome_exec(
                            ct_ip, cmd, mgr._callhome_public_key, 120, vmid=build_vmid,
                        )
                        if not ok:
                            _log.warning("[%s] post_command returned non-zero: %s", service, cmd[:80])

                    # 10. Write image version
                    version = f"{time.strftime('%Y%m%d')}.{_random.randint(100, 999)}"
                    await _callhome_exec(
                        ct_ip, f"sh -c 'echo {version} > /etc/image_version'",
                        mgr._callhome_public_key, 10, vmid=build_vmid,
                    )

                    # 11. Stop container
                    _log.info("[%s] Stopping build CT", service)
                    stop_upid = await asyncio.to_thread(pve.ct_stop, build_vmid)
                    if stop_upid:
                        await asyncio.to_thread(pve.wait_for_task, stop_upid, 60)
                    await asyncio.sleep(2)

                    # 12. vzdump
                    _log.info("[%s] Running vzdump", service)
                    await asyncio.to_thread(pve.vzdump, build_vmid)

                    # 13. Find archive path
                    archive_path = await asyncio.to_thread(
                        pve.vzdump_find_archive, build_vmid,
                    )
                    _log.info("[%s] vzdump archive: %s", service, archive_path)

                    # 14. Destroy build container
                    await asyncio.to_thread(pve.ct_stop_and_destroy, build_vmid, 60)

                    elapsed = time.monotonic() - t0
                    result = {
                        "success": True,
                        "service": service,
                        "template_path": archive_path,
                        "version": version,
                        "elapsed_seconds": round(elapsed, 1),
                    }
                    _log.info("[%s] Build complete in %.0fs: %s", service, elapsed, archive_path)
                    return JSONResponse(result)

                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    _log.error("[%s] Build failed after %.0fs: %s", service, elapsed, exc)
                    try:
                        await asyncio.to_thread(pve.ct_stop_and_destroy, build_vmid, 30)
                    except Exception:
                        pass
                    return JSONResponse(
                        {"error": str(exc)[:500], "service": service, "elapsed_seconds": round(elapsed, 1)},
                        status_code=500,
                    )

        starlette_app.routes.insert(0, Route(
            "/api/build/{service}", _api_build_service, methods=["POST"],
        ))


# ── Cluster Manager ─────────────────────────────────────────────────


class ClusterManager(NodeManager):
    """Subnet-scoped fleet manager with event broadcast capability.

    Fleet-level operations (batman across all nodes, bridge/wifi management)
    live here — NOT on NodeManager. Requires MESH_KEY at construction.

    Also acts as a fleet store for child Manager heartbeats, providing
    cluster-scoped /api/nodes and /api/fleet/* endpoints.
    """

    @staticmethod
    def _parse_child_managers(raw: str | dict | None) -> dict[str, str]:
        if not raw:
            return {}
        if isinstance(raw, str):
            return _json.loads(raw) if raw else {}
        return dict(raw)

    def __init__(
        self,
        node_resolver: Callable[[str], str | None],
        *,
        child_managers: str | dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(node_resolver, **kwargs)
        self._child_managers = self._parse_child_managers(child_managers)
        self._fleet_nodes: dict[str, dict] = {}

    def _resolve_display_target(self, node_id: str) -> tuple[str, int] | None:
        """Resolve browser-reachable (ip, port_offset) for a node's display.

        Display URLs are loaded in the USER's browser via iframe, so they
        must resolve to IPs the browser can reach (WAN IPs with port
        forwarding). This is distinct from _node_resolver which resolves
        management IPs (VPN) for server-side operations.

        CM checks: own host first, then _child_managers, then _display_resolver.
        The display_resolver returns (ip, port_offset) where offset > 0
        indicates the host's displays are relayed through another host.
        """
        if node_id == self._host_name and self._host_ip:
            return (self._host_ip, 0)
        ip = self._child_managers.get(node_id)
        if ip:
            return (ip, 0)
        if self._display_resolver is None:
            return None
        return self._display_resolver(node_id)

    @property
    def supports_fleet(self) -> bool:
        return True

    def get_fleet_children(self, node_id: str) -> list[str]:
        """Return child node IDs when node_id matches this CM.

        Also returns fleet node IDs for the SM (which sees all nodes
        via heartbeat checkins in _fleet_nodes).
        """
        if node_id == self._host_name:
            children = list(self._child_managers.keys())
            for nid in self._fleet_nodes:
                if nid not in children and nid != self._host_name:
                    children.append(nid)
            return children
        for nid, entry in self._fleet_nodes.items():
            p = entry.get("payload", {})
            cluster_nodes = p.get("cluster_nodes", {})
            if node_id == nid and cluster_nodes:
                return list(cluster_nodes.keys())
        return []

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

    def _summarize_fleet(self) -> dict[str, dict]:
        """Build cluster_nodes summary from child Manager heartbeats."""
        children: dict[str, dict] = {}
        for nid, entry in self._fleet_nodes.items():
            p = entry.get("payload", {})
            ch = p.get("container_health", {}) or {}
            children[nid] = {
                "hostname": p.get("hostname", nid),
                "local_ips": p.get("local_ips", []),
                "disk_usage_pct": p.get("disk_usage_pct", 0),
                "memory_usage_pct": p.get("memory_usage_pct", 0),
                "uptime_seconds": p.get("uptime_seconds", 0),
                "last_seen": entry.get("received_at", ""),
                "services": p.get("services", []),
                "container_health": ch,
            }
        return children

    def build_payload(self) -> dict:
        """Extend base payload with aggregated child Manager data."""
        payload = super().build_payload()
        payload["cluster_nodes"] = self._summarize_fleet()
        return payload

    def build_relay_payload(
        self,
        host_name: str,
        host_ip: str,
        host_metrics: dict,
        container_checkins: dict[str, dict],
    ) -> dict:
        """Extend base relay payload with child Manager fleet data."""
        payload = super().build_relay_payload(
            host_name, host_ip, host_metrics, container_checkins,
        )
        payload["cluster_nodes"] = self._summarize_fleet()
        return payload

    @staticmethod
    def _http_with_enetunreach_retry(
        url: str, *, host_label: str, method: str = "GET",
        body: bytes | None = None, timeout: int = 30,
    ) -> tuple[Any | None, Exception | None]:
        """Issue an HTTP request, retrying once on ENETUNREACH.

        The LAN container's ARP entry for the default gateway goes STALE
        between Phase 1 Ansible calls and Phase 2 broadcasts, causing the
        kernel to return ENETUNREACH before the ARP probe completes.
        A single 0.5 s retry is sufficient for the ARP refresh.

        Returns (response_body, None) on success or (None, exception) on
        failure.
        """
        log = logging.getLogger("vm_builds.cluster")
        headers = {"Content-Type": "application/json"} if body else {}
        last_exc: Exception | None = None
        for attempt in range(2):
            req = urllib.request.Request(
                url, data=body, headers=headers, method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode()
                    if not raw:
                        return {}, None
                    return _json.loads(raw), None
            except (_json.JSONDecodeError, ValueError) as exc:
                return None, exc
            except OSError as exc:
                last_exc = exc
                if getattr(exc, "errno", None) == _errno.ENETUNREACH and attempt == 0:
                    log.info("ENETUNREACH to %s, retrying after ARP refresh", host_label)
                    time.sleep(0.5)
                    continue
                break
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_exc = exc
                break
        return None, last_exc

    def _broadcast_event_to_managers_sync(
        self, event_payload: dict,
    ) -> dict[str, dict]:
        """POST an event to each child Manager's /api/manager/events.

        Uses _child_managers (Proxmox host IPs) to reach kiosk Managers,
        NOT _fleet_nodes (which contains container heartbeats).
        Synchronous — called via asyncio.to_thread from async context.
        Fan-out is parallelized via ThreadPoolExecutor.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        log = logging.getLogger("vm_builds.cluster")
        results: dict[str, dict] = {}

        def _post_one(host_name: str, host_ip: str) -> tuple[str, dict]:
            url = f"http://{host_ip}:{Ports.MANAGER}/api/manager/events"
            body = _json.dumps(event_payload).encode("utf-8")
            resp_body, exc = self._http_with_enetunreach_retry(
                url, host_label=host_name, method="POST", body=body, timeout=30,
            )
            if exc is not None:
                log.warning("Event broadcast to %s (%s) failed: %s", host_name, host_ip, exc)
                return host_name, {"success": False, "error": str(exc)[:200]}
            return host_name, {"success": True, "response": resp_body}

        with ThreadPoolExecutor(max_workers=len(self._child_managers) or 1) as pool:
            futures = [
                pool.submit(_post_one, name, ip)
                for name, ip in self._child_managers.items()
            ]
            for future in as_completed(futures):
                name, result = future.result()
                results[name] = result
        return results

    async def batman_fleet(self, action: str) -> dict:
        """Orchestrate batman across ALL nodes in this cluster.

        Phase 1: Broadcast to child Managers (must complete before local
                 router batman, which can transiently disrupt WAN routing).
        Phase 2: Execute locally on this node's containers + router VM.
                 batman_local() discovers both LXC containers (HTTP cmd)
                 and the router VM (HTTP cmd) automatically.
        """
        if not self._mesh_key:
            return {"error": "MESH_KEY not configured"}

        token = _hmac.new(
            self._mesh_key.encode(), f"{action}_batman".encode(), hashlib.sha256,
        ).hexdigest()

        results: dict[str, dict] = {}

        # Phase 1: Broadcast to child Managers first (before local
        # router batman disrupts WAN routing to child nodes)
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

        # Phase 2: Local execution — batman_local handles router VM + containers
        local_result = await self.batman_local(action, token)
        results.update(local_result)

        event_bus.emit({
            "type": "batman_event",
            "action": action,
            "results": {k: v.get("success", False) for k, v in results.items()},
        })

        return results

    async def batman_fleet_status(self) -> dict:
        """Query batman status from all nodes in this cluster.

        Phase 1: local status — batman_local_status handles router VM + containers.
        Phase 2: query each child Manager via /api/batman/local/status.
        """
        statuses: dict[str, dict] = {}

        # Phase 1: local — batman_local_status discovers router VM + containers
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

        Uses _child_managers (Proxmox host IPs) to reach child Managers.
        Keys in the response are host-qualified (e.g. "mesh1/mesh-103").
        Fan-out is parallelized via ThreadPoolExecutor.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        log = logging.getLogger("vm_builds.cluster")
        results: dict[str, dict] = {}

        def _query_one(host_name: str, host_ip: str) -> tuple[str, dict | None, Exception | None]:
            url = f"http://{host_ip}:{Ports.MANAGER}/api/batman/local/status"
            resp_body, exc = self._http_with_enetunreach_retry(
                url, host_label=host_name, timeout=10,
            )
            return host_name, resp_body, exc

        with ThreadPoolExecutor(max_workers=len(self._child_managers) or 1) as pool:
            futures = [
                pool.submit(_query_one, name, ip)
                for name, ip in self._child_managers.items()
            ]
            for future in as_completed(futures):
                host_name, resp_body, exc = future.result()
                if exc is not None:
                    host_ip = self._child_managers[host_name]
                    log.warning("Batman status query to %s (%s) failed: %s", host_name, host_ip, exc)
                    results[host_name] = {"active": False, "error": str(exc)[:200]}
                else:
                    results.update(resp_body)
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
                body = {}
            target = body.get("target", "all")
            nodes = get_bridge_nodes()
            if target == "sta":
                nodes = [n for n in nodes if n["default_role"] == "sta"]
            token = request.headers.get("x-callhome-token", "")

            resolved: list[tuple[str, str]] = []
            results: dict[str, dict] = {}
            for node in nodes:
                ip = cluster.resolve_node_ip(node["node_id"])
                if not ip:
                    results[node["node_id"]] = {"success": False, "error": "IP not resolved"}
                else:
                    resolved.append((node["node_id"], ip))

            async def _restart(nid: str, ip: str) -> tuple[str, dict]:
                result = await asyncio.to_thread(
                    _http_post_json, ip, "/api/wifi/local/restart", token=token,
                )
                return nid, result

            gathered = await asyncio.gather(
                *[_restart(nid, ip) for nid, ip in resolved],
            )
            for nid, data in gathered:
                results[nid] = data
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
            token = request.headers.get("x-callhome-token", "")
            result = await asyncio.to_thread(
                _http_post_json, ip, f"/api/wifi/local/mode/{mode}", token=token,
            )
            result["node_id"] = node_id
            return JSONResponse(result)

        async def _api_wifi_status(request: StarletteRequest) -> JSONResponse:
            node_id = request.path_params.get("node", "")
            ip = cluster.resolve_node_ip(node_id)
            if not ip:
                return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
            result = await asyncio.to_thread(
                _http_get_json, ip, "/api/wifi/local/status",
            )
            if "error" in result:
                return JSONResponse({"node_id": node_id, "error": result["error"]}, status_code=502)
            return JSONResponse({"node_id": node_id, **result})

        async def _api_wifi_status_all(request: StarletteRequest) -> JSONResponse:
            """Aggregate WiFi status across all nodes via their NM APIs.

            Queries each NM's /api/wifi/local/status endpoint over HTTP.
            Fan-out is parallelized via asyncio.gather.
            """
            from scripts.webui.data import get_bridge_nodes, get_mesh_nodes
            target_nodes: set[str] = set()
            for bn in get_bridge_nodes():
                target_nodes.add(bn["node_id"])
            _ap_node, sta_nodes = get_mesh_nodes()
            for n in sta_nodes:
                target_nodes.add(n)

            resolved: list[tuple[str, str]] = []
            results: dict[str, dict] = {}
            for node_id in target_nodes:
                ip = cluster.resolve_node_ip(node_id)
                if not ip:
                    results[node_id] = {"error": f"Unknown node: {node_id}"}
                else:
                    resolved.append((node_id, ip))

            async def _fetch(nid: str, ip: str) -> tuple[str, dict]:
                result = await asyncio.to_thread(
                    _http_get_json, ip, "/api/wifi/local/status",
                )
                if "error" in result:
                    return nid, {"error": result["error"]}
                return nid, result

            gathered = await asyncio.gather(
                *[_fetch(nid, ip) for nid, ip in resolved],
            )
            for nid, data in gathered:
                results[nid] = data
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


# ── HTTP-based collectors for SuperManager tier ──────────────────────
# The SM collects metrics by querying NM API endpoints over VPN.
# These use HTTP exclusively — no SSH anywhere in the fleet communication.


def _http_get_json(ip: str, path: str, *, timeout: int = 10) -> dict:
    """GET a JSON endpoint on a NodeManager, return parsed body or error."""
    url = f"http://{ip}:{Ports.MANAGER}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"error": str(exc)}


def _http_post_json(
    ip: str, path: str, body: dict | None = None, *,
    timeout: int = 30, token: str = "",
) -> dict:
    """POST to a NodeManager endpoint, return parsed body or error."""
    url = f"http://{ip}:{Ports.MANAGER}{path}"
    payload = _json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("x-callhome-token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"error": str(exc)}


def _http_collect(
    ip: str, node_id: str, metric_type: str,
) -> heartbeat.HeartbeatCache:
    """Shared HTTP collector — GETs cached data from a NM and ensures a
    remote subscription exists so the NM's poller starts collecting.

    The NM returns a serialized HeartbeatCache: ``{data, success, error,
    collected_at, ...}``.  We unwrap it so the SM's cache stores only the
    inner metric payload, matching what local HTTP collectors produce.
    """
    qs = f"?subscribe_node_id={node_id}" if node_id else ""
    raw = _http_get_json(ip, f"/api/heartbeat/latest/{metric_type}{qs}")
    if raw.get("data") is None:
        return heartbeat.HeartbeatCache(
            node_id="", metric_type=metric_type, data={},
            collected_at=str(time.monotonic()),
            success=False, error=raw.get("error", "Not reachable"),
        )
    return heartbeat.HeartbeatCache(
        node_id="", metric_type=metric_type,
        data=raw["data"],
        collected_at=raw.get("collected_at", str(time.monotonic())),
        success=raw.get("success", True),
        error=raw.get("error", ""),
    )


def _make_http_collector(
    metric_type: str,
) -> Callable[[str, str], heartbeat.HeartbeatCache]:
    """Factory: create an HTTP collector for a given metric type."""
    def collector(ip: str, node_id: str = "") -> heartbeat.HeartbeatCache:
        return _http_collect(ip, node_id, metric_type)
    collector.__name__ = f"http_collect_{metric_type}"
    return collector


SM_COLLECTOR_MAP: dict[str, Any] = {
    mt: _make_http_collector(mt) for mt in ("wifi", "bridge", "router", "mesh", "batman")
}


# ── SuperManager ─────────────────────────────────────────────────────


class SuperManager(ClusterManager):
    """Global fleet view: HTTP-only, VPN transport.

    The SM aggregates heartbeats from all Cluster Managers. All
    operations proxy through downstream managers via HTTP.
    """

    def __init__(self, node_resolver: Callable[[str], str | None], **kwargs: Any) -> None:
        super().__init__(node_resolver, **kwargs)
        self._collector_map = SM_COLLECTOR_MAP.copy()

    def _resolve_collector_ip(
        self, nm_host: str, container_target: str,
    ) -> str | None:
        """SM routes to the NM's management/VPN IP for HTTP collection."""
        return self.resolve_node_ip(nm_host)

    def _refresh_metrics(self) -> None:
        """SM has no local host — nothing to collect."""

    def register_api(self, starlette_app: Any, **kwargs: Any) -> None:
        """Register SM routes — HTTP-only."""
        _register_sm_routes(self, starlette_app)


def _register_sm_routes(mgr: "SuperManager", starlette_app: Any) -> None:
    """Register SuperManager-specific API routes.

    HTTP-only tier. Registers:
    - Heartbeat subscription endpoints
    - Host state store endpoints
    - HTTP proxy endpoints that forward to CM/NM over VPN
    """
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    # ── Heartbeat subscription endpoints ─────────────

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
            return JSONResponse({
                "status": "no_data",
                "node_id": node_id,
                "metric_type": metric_type,
                "data": {},
                "success": False,
                "error": f"No {metric_type} metrics collected yet for {node_id}. "
                         f"Ensure the Node Manager is running and the metric "
                         f"subscription is active.",
            })
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

    # ── HTTP proxy endpoints (forward to CM/NM over VPN) ──────

    def _resolve_cm_ip() -> str | None:
        """Resolve the Cluster Manager IP (always the 'home' node)."""
        return mgr.resolve_node_ip("home")

    async def _proxy_batman_enable(request: StarletteRequest) -> JSONResponse:
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, cm_ip, "/api/batman/enable", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_batman_disable(request: StarletteRequest) -> JSONResponse:
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, cm_ip, "/api/batman/disable", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_batman_status(request: StarletteRequest) -> JSONResponse:
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        result = await asyncio.to_thread(_http_get_json, cm_ip, "/api/batman/status")
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_bridge_restart_wifi(request: StarletteRequest) -> JSONResponse:
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        body = {}
        try:
            body = await request.json()
        except (ValueError, TypeError):
            pass
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, cm_ip, "/api/bridge/restart-wifi", body, token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_wifi_status(request: StarletteRequest) -> JSONResponse:
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        result = await asyncio.to_thread(_http_get_json, cm_ip, "/api/wifi/status")
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_wifi_status_node(request: StarletteRequest) -> JSONResponse:
        node = request.path_params.get("node", "")
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        result = await asyncio.to_thread(
            _http_get_json, cm_ip, f"/api/wifi/status/{node}",
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_wifi_mode(request: StarletteRequest) -> JSONResponse:
        node = request.path_params.get("node", "")
        mode = request.path_params.get("mode", "")
        cm_ip = _resolve_cm_ip()
        if not cm_ip:
            return JSONResponse({"error": "CM IP not resolved"}, status_code=502)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, cm_ip, f"/api/wifi/mode/{node}/{mode}", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_guests(request: StarletteRequest) -> JSONResponse:
        """Proxy guest list to a specific NM. Requires node_id query param."""
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        result = await asyncio.to_thread(_http_get_json, nm_ip, "/api/guests")
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_guest_action(request: StarletteRequest) -> JSONResponse:
        node_id = request.query_params.get("node_id", "")
        vmid = request.path_params.get("vmid", "")
        action = request.path_params.get("action", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, nm_ip, f"/api/guests/{vmid}/{action}", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_display_enter(request: StarletteRequest) -> JSONResponse:
        app_id = request.path_params.get("app_id", "")
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, nm_ip, f"/api/display/{app_id}/enter", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_display_exit(request: StarletteRequest) -> JSONResponse:
        app_id = request.path_params.get("app_id", "")
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, nm_ip, f"/api/display/{app_id}/exit", token=token,
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_display_status(request: StarletteRequest) -> JSONResponse:
        app_id = request.path_params.get("app_id", "")
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        result = await asyncio.to_thread(
            _http_get_json, nm_ip, f"/api/display/{app_id}/status",
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_display_list(request: StarletteRequest) -> JSONResponse:
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        result = await asyncio.to_thread(_http_get_json, nm_ip, "/api/display/list")
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_desktop_session_switch(request: StarletteRequest) -> JSONResponse:
        session = request.path_params.get("session", "")
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        token = request.headers.get("x-callhome-token", "")
        result = await asyncio.to_thread(
            _http_post_json, nm_ip, f"/api/desktop/session/{session}", token=token,
        )
        code = 200 if result.get("success") else 400
        return JSONResponse(result, status_code=code)

    async def _proxy_desktop_session_status(request: StarletteRequest) -> JSONResponse:
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        result = await asyncio.to_thread(
            _http_get_json, nm_ip, "/api/desktop/session",
        )
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    async def _proxy_nm_config(request: StarletteRequest) -> JSONResponse:
        """Proxy NM config query. Requires node_id query param."""
        node_id = request.query_params.get("node_id", "")
        if not node_id:
            return JSONResponse({"error": "node_id query param required"}, status_code=400)
        nm_ip = mgr.resolve_node_ip(node_id)
        if not nm_ip:
            return JSONResponse({"error": f"Unknown node: {node_id}"}, status_code=404)
        result = await asyncio.to_thread(_http_get_json, nm_ip, "/api/config/self")
        code = 502 if "error" in result else 200
        return JSONResponse(result, status_code=code)

    starlette_app.routes.insert(0, Route(
        "/api/config/self", _proxy_nm_config, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/enable", _proxy_batman_enable, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/disable", _proxy_batman_disable, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/batman/status", _proxy_batman_status, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/bridge/restart-wifi", _proxy_bridge_restart_wifi, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/wifi/status", _proxy_wifi_status, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/wifi/status/{node}", _proxy_wifi_status_node, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/wifi/mode/{node}/{mode}", _proxy_wifi_mode, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/guests", _proxy_guests, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/guests/{vmid}/{action}", _proxy_guest_action, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/display/{app_id}/enter", _proxy_display_enter, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/display/{app_id}/exit", _proxy_display_exit, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/display/{app_id}/status", _proxy_display_status, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/display/list", _proxy_display_list, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/desktop/session/{session}",
        _proxy_desktop_session_switch, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/desktop/session", _proxy_desktop_session_status, methods=["GET"],
    ))

    # ── Host state endpoints ──────────────
    # These are needed on the SM tier for nodes.json-backed state.

    def _no_store() -> JSONResponse:
        return JSONResponse(
            {"error": "state store not configured"}, status_code=501,
        )

    def _bad_json() -> JSONResponse:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    def _require_store() -> HostStateStore | None:
        return mgr.host_state_store

    async def _parse_body(request: StarletteRequest) -> dict | None:
        try:
            return await request.json()
        except (ValueError, TypeError, _json.JSONDecodeError):
            return None

    async def _api_host_state_get(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        store = _require_store()
        if not store:
            return _no_store()
        state = store.get(host_id)
        if state is None:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse(state.to_dict())

    async def _api_host_hardware_post(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        store = _require_store()
        if not store:
            return _no_store()
        body = await _parse_body(request)
        if body is None:
            return _bad_json()
        store.get_or_create(host_id, body.get("ip", ""))
        result = store.update_hardware(host_id, body)
        if result is None:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse(result.to_dict(), status_code=200)

    async def _api_host_bridges_post(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        store = _require_store()
        if not store:
            return _no_store()
        body = await _parse_body(request)
        if body is None:
            return _bad_json()
        store.get_or_create(host_id, body.get("ip", ""))
        result = store.update_bridges(host_id, body)
        if result is None:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse(result.to_dict(), status_code=200)

    async def _api_host_container_post(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        vmid_str = request.path_params["vmid"]
        store = _require_store()
        if not store:
            return _no_store()
        try:
            vmid = int(vmid_str)
        except ValueError:
            return JSONResponse({"error": f"Invalid VMID: {vmid_str}"}, status_code=400)
        body = await _parse_body(request)
        if body is None:
            return _bad_json()
        result = store.register_container(host_id, vmid, body)
        if result is None:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse(result.to_dict(), status_code=201)

    async def _api_host_container_delete(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        vmid_str = request.path_params["vmid"]
        store = _require_store()
        if not store:
            return _no_store()
        try:
            vmid = int(vmid_str)
        except ValueError:
            return JSONResponse({"error": f"Invalid VMID: {vmid_str}"}, status_code=400)
        result = store.deregister_container(host_id, vmid)
        if result is None:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse({"status": "ok"}, status_code=200)

    async def _api_host_phy_patch(request: StarletteRequest) -> JSONResponse:
        host_id = request.path_params["id"]
        phy_name = request.path_params["name"]
        store = _require_store()
        if not store:
            return _no_store()
        body = await _parse_body(request)
        if body is None:
            return _bad_json()
        namespace = body.get("namespace")
        if not namespace:
            return JSONResponse({"error": "namespace required"}, status_code=400)
        result = store.update_phy_namespace(host_id, phy_name, namespace)
        if result is None:
            return JSONResponse({"error": "unknown host or PHY"}, status_code=404)
        return JSONResponse(result.to_dict(), status_code=200)

    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/state", _api_host_state_get, methods=["GET"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/hardware", _api_host_hardware_post, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/bridges", _api_host_bridges_post, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/containers/{vmid}",
        _api_host_container_post, methods=["POST"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/containers/{vmid}",
        _api_host_container_delete, methods=["DELETE"],
    ))
    starlette_app.routes.insert(0, Route(
        "/api/host/{id}/hardware/phy/{name}",
        _api_host_phy_patch, methods=["PATCH"],
    ))

    # ── Image version endpoint (aggregates from NMs via HTTP) ─
    async def _api_image_versions() -> JSONResponse:
        state = None
        if mgr.host_state_store and mgr._host_name:
            state = mgr.host_state_store.get(mgr._host_name)
        if state is None:
            state = mgr.get_local_host_state()
        if state is None:
            return JSONResponse({"versions": {}})
        return JSONResponse({"versions": state.image_versions()})

    starlette_app.add_api_route(
        "/api/images/versions", _api_image_versions, methods=["GET"],
        include_in_schema=False,
    )


# ── Metric route registration ─────────────────────────────────────────


def _register_default_metric_routes(mgr: BaseManager) -> None:
    """Register routing handlers from fleet topology.

    Three reusable handler types cover all patterns:
      LocalContainerRoute  — NM host has a named container to collect from
      HostedServiceRoute   — service name remaps to the host running it
      DirectRoute          — identity (automatic fallback, no registration)
    """
    from scripts.webui.data import get_bridge_nodes, get_mesh_nodes, get_router_node

    bridge_route = LocalContainerRoute("openwrt-bridge")
    for node in get_bridge_nodes():
        nid = node["node_id"]
        mgr.register_metric_route(nid, "bridge", bridge_route)
        mgr.register_metric_route(nid, "wifi", bridge_route)

    mesh_ap, mesh_stas = get_mesh_nodes()

    router_node = get_router_node()
    router_route = HostedServiceRoute(mesh_ap)
    mgr.register_metric_route(router_node, "router", router_route)
    mgr.register_metric_route(router_node, "wifi", router_route)

    mgr.register_metric_route(mesh_ap, "mesh", LocalContainerRoute("openwrt"))
    mesh_sta_route = LocalContainerRoute("openwrt-mesh")
    for sta in mesh_stas:
        mgr.register_metric_route(sta, "mesh", mesh_sta_route)


# ── Module-level singleton ────────────────────────────────────────────
# app.py and kiosk_server.py call init() / register_api() / start_poller()
# at module level. These thin wrappers delegate to the current instance.

_instance: BaseManager | None = None


def init(
    node_resolver: Callable[[str], str | None],
    auth_validator: Callable[[str], bool] | None = None,
    config: dict[str, str] | None = None,
    manager_class: type | None = None,
    *,
    is_supermanager: bool = False,
    display_resolver: Callable[[str], tuple[str, int] | None] | None = None,
) -> BaseManager:
    """Create the module-level manager singleton.

    Callers pass the tier class directly (NodeManager, ClusterManager)
    or set ``is_supermanager=True`` which auto-selects SuperManager.
    """
    global _instance
    cls = SuperManager if is_supermanager else (manager_class or ClusterManager)
    cfg = config or {}

    kwargs: dict = dict(
        auth_validator=auth_validator,
        display_resolver=display_resolver,
        host_ip=cfg.get("HOST_IP", ""),
        host_name=cfg.get("HOST_NAME", ""),
        management_server=cfg.get("MANAGEMENT_SERVER", ""),
        callhome_public_key=cfg.get("CALLHOME_PUBLIC_KEY", ""),
        mesh_key=cfg.get("MESH_KEY", ""),
        state_dir=cfg.get("STATE_DIR", ""),
        pve_api_token=cfg.get("PVE_API_TOKEN", ""),
        pve_node=cfg.get("PVE_NODE", ""),
    )
    if issubclass(cls, ClusterManager):
        kwargs["child_managers"] = cfg.get("CHILD_MANAGER_IPS")

    _instance = cls(node_resolver, **kwargs)

    pve_client = getattr(_instance, "_pve", None)
    if pve_client is not None or isinstance(_instance, NodeManager):
        from scripts.webui.data import DISPLAY_APP_CONFIGS
        for app_config in DISPLAY_APP_CONFIGS.values():
            handler = build_handler(app_config, pve=pve_client)
            _instance.display_transfer.register(handler)

    _register_default_metric_routes(_instance)
    return _instance


def reset() -> None:
    global _instance
    _instance = None


def get_instance() -> BaseManager:
    if _instance is None:
        raise RuntimeError("manager.init() has not been called")
    return _instance


def try_get_instance() -> BaseManager | None:
    """Return the manager singleton or None if not yet initialised."""
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

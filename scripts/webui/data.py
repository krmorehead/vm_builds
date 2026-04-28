"""Pure data layer for the web UI — no framework imports.

Provides functions for environment management, host discovery, service
tags, deploy command construction, image status, and deploy history.
All functions are synchronous and testable without a running UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import build  # noqa: E402


# ── UI string constants (canonical source: constants.py) ─────────────
# Re-exported here for backward compatibility — all page modules and tests
# can import from either ``data`` or ``constants`` interchangeably.

from scripts.webui.constants import (  # noqa: F401
    CLUSTER_NAV_SECTIONS,
    KIOSK_NAV_ITEMS,
    NAV_SECTIONS,
    ApiRoutes,
    DisplayAppConfig,
    Labels,
    ManagerDefaults,
    NavItem,
    NetworkAddresses,
    PageTitles,
    Ports,
    Routes,
    VMIDs,
)


# ── Server port for internal API calls ────────────────────────────────

_SERVER_PORT: int = int(os.environ.get("WEBUI_PORT", "52500"))


def set_server_port(port: int) -> None:
    """Set the port used for internal API calls. Called once during startup."""
    global _SERVER_PORT
    _SERVER_PORT = port


def get_api_base_url() -> str:
    """Return the base URL for internal API calls (e.g. http://127.0.0.1:52500)."""
    return f"http://127.0.0.1:{_SERVER_PORT}"


# ── Event bus for SSE streaming ───────────────────────────────────────

_log = logging.getLogger("vm_builds.events")


class EventBus:
    """Async fan-out event bus for SSE subscribers.

    Callers subscribe via ``subscribe()`` which returns an ``asyncio.Queue``.
    Events emitted via ``emit()`` are pushed to every active subscriber queue.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def emit(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
                _log.warning("Dropping slow SSE subscriber (queue full)")
        for q in dead:
            self._subscribers.remove(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = EventBus()


# ── Deployment timeline tracking ─────────────────────────────────────


@dataclass
class ServiceTimestamp:
    """Timing data for a single service's readiness during deployment."""

    service_id: str
    first_checkin: float | None = None
    ready_at: float | None = None


@dataclass
class DeployTimeline:
    """Tracks per-service provisioning and readiness timing for a deployment."""

    start_time: float = 0.0
    end_time: float = 0.0
    services: dict[str, ServiceTimestamp] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0


_active_timeline: DeployTimeline | None = None


def start_timeline() -> DeployTimeline:
    """Begin tracking a new deployment timeline."""
    global _active_timeline
    _active_timeline = DeployTimeline(start_time=time.monotonic())
    return _active_timeline


def stop_timeline() -> DeployTimeline | None:
    """Stop tracking and return the completed timeline."""
    global _active_timeline
    if _active_timeline:
        _active_timeline.end_time = time.monotonic()
    tl = _active_timeline
    _active_timeline = None
    return tl


def get_active_timeline() -> DeployTimeline | None:
    """Return the currently active timeline, if any."""
    return _active_timeline


def record_service_event(service_id: str, event_type: str) -> None:
    """Record a service check-in or readiness event on the active timeline."""
    if not _active_timeline:
        return
    now = time.monotonic()
    if service_id not in _active_timeline.services:
        _active_timeline.services[service_id] = ServiceTimestamp(service_id=service_id)
    svc = _active_timeline.services[service_id]
    if event_type in ("node_checkin", "container_ready") and svc.first_checkin is None:
        svc.first_checkin = now
    if event_type == "container_ready" and svc.ready_at is None:
        svc.ready_at = now


def save_timeline(state_dir: Path, timeline: DeployTimeline) -> None:
    """Persist a completed timeline to the state directory."""
    state_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out: dict[str, Any] = {
        "start_time": timeline.start_time,
        "end_time": timeline.end_time,
        "duration": timeline.duration,
        "services": {},
    }
    for sid, svc in timeline.services.items():
        entry: dict[str, Any] = {"service_id": sid}
        if svc.first_checkin is not None:
            entry["checkin_offset"] = round(svc.first_checkin - timeline.start_time, 2)
        if svc.ready_at is not None:
            entry["ready_offset"] = round(svc.ready_at - timeline.start_time, 2)
        out["services"][sid] = entry
    tl_file = state_dir / f"timeline_{ts}.json"
    tl_file.write_text(json.dumps(out, indent=2) + "\n")


def load_timelines(state_dir: Path, max_count: int = 10) -> list[dict[str, Any]]:
    """Load recent timeline files from the state directory."""
    if not state_dir.exists():
        return []
    files = sorted(state_dir.glob("timeline_*.json"), reverse=True)[:max_count]
    results: list[dict[str, Any]] = []
    for f in files:
        try:
            results.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return results


# ── Environment management ───────────────────────────────────────────


@dataclass
class EnvVar:
    """Metadata for a single environment variable."""

    name: str
    description: str
    required: bool
    example: str
    sensitive: bool = False


@dataclass
class EnvResult:
    """Result of loading and validating an environment file."""

    values: dict[str, str]
    missing: list[str]
    warnings: list[str]


ENV_TEMPLATE: list[EnvVar] = [
    EnvVar("PRIMARY_HOST", "IP of the primary Proxmox node", True, "192.168.86.201", False),
    EnvVar("HOME_API_TOKEN", "Proxmox API token for home node", True, "", True),
    EnvVar("MESH_KEY", "WPA3-SAE passphrase for mesh networking", True, "", True),
    EnvVar("AI_HOST", "IP of the AI compute node", False, "192.168.86.220", False),
    EnvVar("MESH_2_HOST", "IP of the second mesh node", False, "192.168.86.211", False),
    EnvVar("MESH1_API_TOKEN", "Proxmox API token for mesh1 node", False, "", True),
    EnvVar("MESH2_API_TOKEN", "Proxmox API token for mesh2 node", False, "", True),
    EnvVar("AI_API_TOKEN", "Proxmox API token for AI node", False, "", True),
    EnvVar("WAN_MAC", "Clone this MAC onto OpenWrt WAN interface", False, "AA:BB:CC:DD:EE:FF", False),
    EnvVar("PIHOLE_WEB_PASSWORD", "Pi-hole admin web password", False, "", True),
    EnvVar("GAMING_VM_ADMIN_USER", "Gaming VM admin username", False, "admin", False),
    EnvVar("GAMING_VM_ADMIN_PASSWORD", "Gaming VM admin password", False, "", True),
    EnvVar("SUNSHINE_USER", "Sunshine streaming server username", False, "admin", False),
    EnvVar("SUNSHINE_PASSWORD", "Sunshine streaming server password", False, "", True),
    EnvVar("MOONLIGHT_SERVER_IP", "IP of the Sunshine server for Moonlight", False, "", False),
    EnvVar("MOONLIGHT_PAIR_PIN", "Moonlight pairing PIN", False, "", True),
    EnvVar("DESKTOP_USER", "Desktop LXC username", False, "desktop", False),
    EnvVar("HA_ADMIN_PASSWORD", "Home Assistant admin password", False, "", True),
    EnvVar("WEBUI_PORT", "Web UI / API port (firewall must allow)", False, "52500", False),
    EnvVar("CALLHOME_SERVER", "Management server URL for fleet call-home (auto-detected)", False, "", False),
    EnvVar("CALLHOME_PRIVATE_KEY", "Server-side secret for validating call-home tokens", False, "", True),
    EnvVar("CALLHOME_PUBLIC_KEY", "Token distributed to nodes for call-home auth", False, "", True),
    EnvVar("BRIDGE_1_HOST", "IP of the first WiFi bridge node", False, "192.168.86.230", False),
    EnvVar("BRIDGE_2_HOST", "IP of the second WiFi bridge node", False, "192.168.86.231", False),
    EnvVar("BRIDGE_1_API_TOKEN", "Proxmox API token for bridge-1 node", False, "", True),
    EnvVar("BRIDGE_2_API_TOKEN", "Proxmox API token for bridge-2 node", False, "", True),
]


def load_environment(path: Path) -> EnvResult:
    """Load and validate an environment file using build.py functions."""
    values = build.load_env(path)
    missing = build.validate_env(values)
    warnings = build.warn_multi_host(values)
    return EnvResult(values=values, missing=missing, warnings=warnings)


def get_env_template() -> list[EnvVar]:
    """Return the list of all env vars with metadata."""
    return list(ENV_TEMPLATE)


def save_environment(path: Path, env: dict[str, str]) -> None:
    """Write env dict to file, creating a .bak backup first."""
    if path.exists():
        backup = path.parent / (path.name + ".bak")
        shutil.copy2(path, backup)
    lines = [f"{k}={v}" for k, v in env.items()]
    path.write_text("\n".join(lines) + "\n")


# ── Host discovery ───────────────────────────────────────────────────


@dataclass
class HostInfo:
    """Static info about a known host.

    After base setup, ALL communication uses the VPN IP.  The
    ``provisioning_ip`` is the WAN/LAN address used only during
    initial Ansible provisioning — never for runtime operations.
    """

    name: str
    ip: str  # VPN IP — the ONLY runtime address
    env_var: str
    wol_capable: bool
    provisioning_ip: str = ""  # WAN/LAN IP for Ansible base setup only


@dataclass
class HostStatus:
    """Result of probing a single host."""

    host: HostInfo
    reachable: bool
    latency_ms: float | None = None
    error: str = ""


@dataclass
class ApiProbeResult:
    """Result of a PVE API connectivity probe."""

    success: bool
    output: str = ""
    error: str = ""


_HOST_VARS_DIR = PROJECT_ROOT / "inventory" / "host_vars"

_HOST_MAP = {
    "PRIMARY_HOST": "home",
    "AI_HOST": "ai",
    "MESH_2_HOST": "mesh2",
    "BRIDGE_1_HOST": "bridge-1",
    "BRIDGE_2_HOST": "bridge-2",
}

_HOST_VPN_MAP: dict[str, str] = {
    "home": "HOME_VPN_IP",
    "ai": "AI_VPN_IP",
    "mesh1": "MESH_1_VPN_IP",
    "mesh2": "MESH_2_VPN_IP",
    "bridge-1": "BRIDGE_1_VPN_IP",
    "bridge-2": "BRIDGE_2_VPN_IP",
}

# ── Host Registry (persistent identity store) ────────────────────────


class HostBucket:
    """IP-based host classification.

    Last octet of the IP determines the bucket for auto-discovered hosts.
    ``RANGES`` is an ordered list of ``(bucket_name, octet_ranges)`` —
    add new buckets by appending to the list.
    """

    TEST = "test"
    LAB = "lab"
    PRODUCTION = "production"

    RANGES: list[tuple[str, list[range]]] = [
        (TEST, [range(200, 256)]),
        (LAB, [range(0, 100), range(100, 200)]),
    ]
    DEFAULT = PRODUCTION

    @staticmethod
    def classify_ip(ip: str) -> str:
        """Determine bucket from the last octet of an IP address."""
        try:
            last_octet = int(ip.rsplit(".", 1)[-1])
        except (ValueError, IndexError):
            return HostBucket.DEFAULT
        for bucket_name, ranges in HostBucket.RANGES:
            if any(last_octet in r for r in ranges):
                return bucket_name
        return HostBucket.DEFAULT


@dataclass
class HostRecord:
    """Persistent registry entry for a known host.

    Stored in ``.state/registry.json``. Identity fields (``name``,
    ``mac``) are used for upsert matching. ``bucket`` and ``source``
    are immutable after creation — subsequent registrations update
    mutable fields (``ip``, ``mac``, ``vpn_ip``, ``last_seen``) only.
    """

    name: str
    ip: str
    mac: str = ""
    bucket: str = ""
    source: str = "manual"
    wol_capable: bool = True
    vpn_ip: str = ""
    first_seen: str = ""
    last_seen: str = ""


def extract_primary_mac(extensions: dict[str, dict]) -> str:
    """Pick the first real MAC from heartbeat network extensions.

    Skips loopback (``00:00:00:00:00:00``), locally-administered
    (``fe:...``), and empty addresses. Returns ``""`` if none found.
    """
    net = extensions.get("network", {})
    interfaces = net.get("interfaces", [])
    for iface in interfaces:
        mac = iface.get("mac", "").lower().strip()
        if not mac or mac == "00:00:00:00:00:00":
            continue
        if mac.startswith("fe:"):
            continue
        name = iface.get("name", "")
        if name == "lo":
            continue
        return mac
    return ""


class HostRegistry:
    """Persistent host identity store backed by ``.state/registry.json``.

    Single responsibility: **who are my hosts** — name, IP, MAC, bucket,
    and how they were discovered (source). Telemetry data lives separately
    in ``nodes.json``.

    ``register()`` is the ONLY write path. Every caller — env seeding,
    heartbeat auto-register, manual form, ``TEST_UNITS`` — MUST go
    through ``register()``. NEVER write to ``registry.json`` directly
    or duplicate upsert logic elsewhere.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._file = state_dir / "registry.json"
        self._records: list[HostRecord] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._records = self._load_from_disk()
            self._loaded = True

    def _load_from_disk(self) -> list[HostRecord]:
        if not self._file.exists():
            return []
        try:
            raw = json.loads(self._file.read_text())
            return [
                HostRecord(
                    name=r.get("name", ""),
                    ip=r.get("ip", ""),
                    mac=r.get("mac", ""),
                    bucket=r.get("bucket", ""),
                    source=r.get("source", "manual"),
                    wol_capable=r.get("wol_capable", True),
                    vpn_ip=r.get("vpn_ip", ""),
                    first_seen=r.get("first_seen", ""),
                    last_seen=r.get("last_seen", ""),
                )
                for r in raw
            ]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        raw = [
            {
                "name": r.name,
                "ip": r.ip,
                "mac": r.mac,
                "bucket": r.bucket,
                "source": r.source,
                "wol_capable": r.wol_capable,
                "vpn_ip": r.vpn_ip,
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
            }
            for r in self._records
        ]
        self._file.write_text(json.dumps(raw, indent=2) + "\n")

    def all(self) -> list[HostRecord]:
        """Return all registered hosts."""
        self._ensure_loaded()
        return list(self._records)

    def find_by_mac(self, mac: str) -> HostRecord | None:
        """Find a host by MAC address."""
        self._ensure_loaded()
        mac_lower = mac.lower().strip()
        if not mac_lower:
            return None
        return next(
            (r for r in self._records if r.mac.lower() == mac_lower), None
        )

    def find_by_name(self, name: str) -> HostRecord | None:
        """Find a host by name."""
        self._ensure_loaded()
        return next((r for r in self._records if r.name == name), None)

    def register(
        self,
        name: str,
        ip: str,
        *,
        mac: str = "",
        bucket: str = "",
        source: str = "manual",
        wol_capable: bool = True,
        vpn_ip: str = "",
    ) -> HostRecord:
        """Upsert a host into the registry. THE ONLY write path.

        Resolution order for identity matching:
        1. MAC match (if provided and non-empty) — hardware identity
        2. Name match — logical identity
        3. No match — create new record

        On match: update mutable fields (ip, mac, vpn_ip, last_seen).
        Immutable-on-create fields (bucket, source, first_seen) are
        never overwritten by subsequent registrations.

        Bucket is auto-classified from IP if not explicitly provided.

        Every caller — env seeding, heartbeat auto-register, manual
        form, TEST_UNITS — MUST go through this method. NEVER write
        to registry.json directly or duplicate upsert logic elsewhere.
        """
        self._ensure_loaded()
        now = datetime.now().isoformat(timespec="seconds")
        resolved_bucket = bucket or HostBucket.classify_ip(ip)

        existing = self.find_by_mac(mac) if mac else None
        if not existing:
            existing = self.find_by_name(name)

        if existing:
            if ip:
                existing.ip = ip
            if mac:
                existing.mac = mac
            if vpn_ip:
                existing.vpn_ip = vpn_ip
            existing.last_seen = now
            self._save()
            return existing

        record = HostRecord(
            name=name,
            ip=ip,
            mac=mac,
            bucket=resolved_bucket,
            source=source,
            wol_capable=wol_capable,
            vpn_ip=vpn_ip,
            first_seen=now,
            last_seen=now,
        )
        self._records.append(record)
        self._save()
        return record

    def seed_from_env(self, env: dict[str, str]) -> None:
        """Populate registry from env vars (``_HOST_MAP`` + ``TEST_UNITS``).

        Idempotent — existing records are updated, not duplicated.
        Delegates every write to ``register()``.
        """
        self._ensure_loaded()

        for env_var, name in _HOST_MAP.items():
            ip = env.get(env_var, "")
            if env_var == "PRIMARY_HOST" or ip:
                vpn_env = _HOST_VPN_MAP.get(name, "")
                self.register(
                    name,
                    ip or env.get("PRIMARY_HOST", ""),
                    source="env",
                    wol_capable=_read_wol_capable(name),
                    vpn_ip=env.get(vpn_env, "") if vpn_env else "",
                )

        mesh1_vpn_env = _HOST_VPN_MAP.get("mesh1", "")
        self.register(
            "mesh1",
            env.get("MESH_1_HOST", "10.10.10.210"),
            source="env",
            wol_capable=_read_wol_capable("mesh1"),
            vpn_ip=env.get(mesh1_vpn_env, "") if mesh1_vpn_env else "",
        )

        test_units = env.get("TEST_UNITS", "")
        if test_units:
            for ip_str in test_units.split(","):
                ip_str = ip_str.strip()
                if not ip_str:
                    continue
                try:
                    last_octet = ip_str.rsplit(".", 1)[-1]
                except (ValueError, IndexError):
                    last_octet = ip_str
                test_name = f"test-{last_octet}"
                if not self.find_by_name(test_name):
                    existing_by_ip = next(
                        (r for r in self._records if r.ip == ip_str), None
                    )
                    if existing_by_ip:
                        continue
                    self.register(
                        test_name,
                        ip_str,
                        bucket=HostBucket.TEST,
                        source="test_units",
                    )


def _read_wol_capable(name: str) -> bool:
    """Read wol_capable from host_vars YAML (simple grep, no YAML dep)."""
    host_file = _HOST_VARS_DIR / f"{name}.yml"
    if not host_file.exists():
        return True
    for line in host_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("wol_capable:"):
            return "true" in line.lower()
    return True


def get_known_hosts(env: dict[str, str]) -> list[HostInfo]:
    """Discover hosts from env vars and inventory.

    After base setup, every host is reached via its VPN IP.  The
    provisioning IP (WAN/LAN) is retained for display context only.
    """
    hosts: list[HostInfo] = []
    all_hosts = dict(_HOST_MAP)
    all_hosts["MESH_1_HOST"] = "mesh1"

    for env_var, name in all_hosts.items():
        provisioning_ip = env.get(env_var, "")
        if env_var == "PRIMARY_HOST" and not provisioning_ip:
            continue
        if env_var != "PRIMARY_HOST" and not provisioning_ip:
            provisioning_ip = env.get(env_var, "")
            if not provisioning_ip and env_var != "MESH_1_HOST":
                continue

        vpn_env = _HOST_VPN_MAP.get(name, "")
        vpn_ip = env.get(vpn_env, "") if vpn_env else ""
        if not vpn_ip:
            continue

        hosts.append(HostInfo(
            name=name,
            ip=vpn_ip,
            env_var=env_var,
            wol_capable=_read_wol_capable(name),
            provisioning_ip=provisioning_ip,
        ))
    return hosts


def probe_all_hosts(hosts: list[HostInfo]) -> list[HostStatus]:
    """Probe all hosts for TCP connectivity via VPN in parallel.

    Every host is probed on its VPN IP (``host.ip``).  If VPN is
    unreachable, the host is DOWN — no fallback to provisioning IPs.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _probe_one(host: HostInfo) -> HostStatus:
        if not host.ip:
            return HostStatus(host=host, reachable=False, error="No VPN IP configured")
        start = time.monotonic()
        reachable = build.probe_host(host.ip, timeout=3.0)
        elapsed = (time.monotonic() - start) * 1000
        return HostStatus(
            host=host,
            reachable=reachable,
            latency_ms=round(elapsed, 1) if reachable else None,
            error="" if reachable else f"VPN unreachable at {host.ip}",
        )

    with ThreadPoolExecutor(max_workers=min(len(hosts), 6)) as pool:
        return list(pool.map(_probe_one, hosts))


def test_api_connection(ip: str) -> ApiProbeResult:
    """Test host connectivity via PVE API (HTTPS probe, no SSH).

    Any HTTP response (including 401) means the host is reachable.
    Only connection failures count as unreachable.
    """
    import urllib.request
    import urllib.error
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://{ip}:8006/api2/json/version"
    start = time.monotonic()
    try:
        resp = urllib.request.urlopen(url, timeout=5, context=ctx)
        resp.read()
        elapsed = (time.monotonic() - start) * 1000
        return ApiProbeResult(success=True, output=f"PVE API OK ({elapsed:.0f}ms)")
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - start) * 1000
        return ApiProbeResult(success=True, output=f"PVE API reachable, HTTP {exc.code} ({elapsed:.0f}ms)")
    except urllib.error.URLError as exc:
        return ApiProbeResult(success=False, error=f"PVE API unreachable: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return ApiProbeResult(success=False, error=f"PVE API timeout: {exc}")


@dataclass
class KickstartResult:
    """Result of a callhome kickstart attempt on a remote host."""

    success: bool
    restarted: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""


def kickstart_callhome(host: Host) -> KickstartResult:
    """Restart callhome on all running LXC containers for a host.

    Uses HTTP via the NodeManager API over VPN exclusively.
    No fallback to provisioning IPs — if the NM is unreachable
    via VPN, the system is broken and must be fixed.
    """
    ip = host.reachable_ip
    if not ip:
        return KickstartResult(
            success=False, message="No reachable IP for this host"
        )

    log = logging.getLogger("vm_builds.kickstart")
    import urllib.request

    health_url = f"http://{ip}:{Ports.MANAGER}/api/health"
    try:
        req = urllib.request.Request(health_url, method="GET")
        urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.warning("NodeManager HTTP unreachable at %s: %s", ip, exc)
        return KickstartResult(
            success=False,
            message=f"NodeManager unreachable at {ip}:{Ports.MANAGER} — "
                    f"VPN or kiosk may be down: {exc}",
        )

    restart_url = f"http://{ip}:{Ports.MANAGER}/api/callhome/restart"
    try:
        req2 = urllib.request.Request(restart_url, method="POST")
        resp2 = urllib.request.urlopen(req2, timeout=15)
        if resp2.status == 200:
            return KickstartResult(
                success=True, restarted=1,
                message="Restarted callhome via NodeManager HTTP API",
            )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.warning("callhome restart via HTTP failed for %s: %s", ip, exc)
        return KickstartResult(
            success=False,
            message=f"callhome restart failed via HTTP: {exc}",
        )

    return KickstartResult(
        success=False,
        message="Unexpected: HTTP response was not 200",
    )


# ── Auto-kickstart for active viewers ────────────────────────────────

_KICKSTART_COOLDOWN: dict[str, float] = {}
_KICKSTART_COOLDOWN_SECONDS = 600  # 10 minutes between kickstarts per host
_KICKSTART_GLOBAL_LOCK = threading.Lock()
_KICKSTART_IN_PROGRESS = False
_MAX_KICKSTARTS_PER_CYCLE = 2


def auto_kickstart_stale_fleet(fleet: "Fleet") -> list[str]:
    """Kickstart heartbeats on stale hosts when a user is actively viewing.

    Called from page refresh cycles. For each host that is NOT online but
    IS reachable (has a VPN IP), restarts callhome on its containers.

    Rate-limited per host (10 min cooldown), globally serialized (only one
    kickstart cycle at a time), and capped to 2 hosts per cycle to prevent
    connection storms that overwhelm fragile links like USB ethernet.

    Returns list of host names where kickstart was triggered.
    """
    global _KICKSTART_IN_PROGRESS
    with _KICKSTART_GLOBAL_LOCK:
        if _KICKSTART_IN_PROGRESS:
            return []
        _KICKSTART_IN_PROGRESS = True

    try:
        return _do_auto_kickstart(fleet)
    finally:
        with _KICKSTART_GLOBAL_LOCK:
            _KICKSTART_IN_PROGRESS = False


def _do_auto_kickstart(fleet: "Fleet") -> list[str]:
    from scripts.webui import heartbeat

    now = time.monotonic()
    triggered: list[str] = []
    log = logging.getLogger("vm_builds.auto_kickstart")

    stale_hosts = [
        h for h in fleet.hosts
        if not h.online and h.reachable_ip
    ]

    for host in stale_hosts:
        if len(triggered) >= _MAX_KICKSTARTS_PER_CYCLE:
            log.debug("Hit per-cycle cap (%d), deferring remaining hosts", _MAX_KICKSTARTS_PER_CYCLE)
            break

        cb_status = heartbeat.get_circuit_status(host.reachable_ip)
        if cb_status["is_open"]:
            log.debug(
                "Skipping %s: circuit breaker open (%.0fs remaining)",
                host.name, cb_status["backoff_remaining_s"],
            )
            continue

        with _KICKSTART_GLOBAL_LOCK:
            last = _KICKSTART_COOLDOWN.get(host.name, 0)
            if now - last < _KICKSTART_COOLDOWN_SECONDS:
                continue
            _KICKSTART_COOLDOWN[host.name] = now

        log.info("Auto-kickstarting stale host: %s via %s", host.name, host.reachable_ip)
        result = kickstart_callhome(host)
        if result.success and result.restarted > 0:
            triggered.append(host.name)
            log.info("Kickstarted %s: %s", host.name, result.message)
        elif not result.success:
            log.warning("Kickstart failed for %s: %s", host.name, result.message)

        time.sleep(0.5)

    return triggered


# ── Service tags ─────────────────────────────────────────────────────


@dataclass
class ServiceTag:
    """A deployable service identified by its Ansible tag."""

    tag: str
    description: str
    category: str
    hosts: list[str]
    is_opt_in: bool = False


SERVICE_TAGS: list[ServiceTag] = [
    ServiceTag("backup", "Back up host config and VMs", "Network", ["home", "mesh1", "ai", "mesh2", "bridge-1", "bridge-2"]),
    ServiceTag("infra", "Bridges, PCI passthrough, iGPU", "Network", ["home", "mesh1", "ai", "mesh2", "bridge-1", "bridge-2"]),
    ServiceTag("openwrt", "OpenWrt router VM", "Network", ["home"]),
    ServiceTag("lan-satellite", "Bootstrap LAN hosts", "Network", ["home", "mesh1"]),
    ServiceTag("cleanup", "Remove temp bootstrap networking", "Network", ["home", "mesh1", "ai", "mesh2", "bridge-1", "bridge-2"]),
    ServiceTag("pihole", "Pi-hole DNS", "DNS & VPN", ["home"]),
    ServiceTag("wireguard", "WireGuard VPN", "DNS & VPN", ["home", "mesh1", "ai", "mesh2"]),
    ServiceTag("monitoring", "rsyslog + Netdata", "Monitoring", ["home"]),
    ServiceTag("homeassistant", "Home Assistant", "Services", ["home"]),
    ServiceTag("media", "Jellyfin + Kodi", "Media", ["home"]),
    ServiceTag("moonlight", "Moonlight streaming client", "Media", ["mesh1"]),
    ServiceTag("desktop", "Debian XFCE desktop LXC", "Desktop", ["home", "mesh1", "ai", "mesh2", "bridge-1", "bridge-2"]),
    ServiceTag("kiosk", "Custom UX kiosk", "Desktop", ["home", "mesh1", "ai", "mesh2", "bridge-1", "bridge-2"]),
    ServiceTag("mesh-wifi", "Mesh WiFi LXC", "WiFi", ["mesh1", "mesh2"]),
    ServiceTag("bridge", "Dedicated WiFi Bridge", "Network", ["bridge-1", "bridge-2"]),
    ServiceTag("gaming", "Gaming LXC (opt-in)", "Gaming", ["ai"], is_opt_in=True),
]


@dataclass
class DeployProfile:
    """A preset combination of service tags."""

    name: str
    tags: list[str]
    description: str


def _tags_for_host(host: str) -> list[str]:
    """Return all non-opt-in service tags that target a given host."""
    return [
        t.tag for t in SERVICE_TAGS
        if host in t.hosts and not t.is_opt_in and t.tag != "cleanup"
    ]


DEPLOY_PROFILES: list[DeployProfile] = [
    DeployProfile(
        "Full Deploy",
        [t.tag for t in SERVICE_TAGS if not t.is_opt_in and t.tag != "cleanup"],
        "Deploy all services (except opt-in gaming and cleanup)",
    ),
    DeployProfile(
        "Home Unit",
        _tags_for_host("home"),
        "All services on home: router, DNS, VPN, monitoring, media, desktop, kiosk",
    ),
    DeployProfile(
        "Mesh Unit",
        _tags_for_host("mesh1"),
        "All services on mesh1: VPN, mesh WiFi, Moonlight, kiosk",
    ),
    DeployProfile(
        "Gamer Unit",
        [*_tags_for_host("ai"), "gaming"],
        "All services on ai: VPN, kiosk, gaming LXC (Sunshine)",
    ),
    DeployProfile(
        "Bridge Units",
        _tags_for_host("bridge-1"),
        "WiFi bridge on bridge-1 and bridge-2",
    ),
    DeployProfile(
        "Network Only",
        ["backup", "infra", "openwrt", "lan-satellite"],
        "Core network infrastructure only",
    ),
    DeployProfile(
        "Core Services",
        ["backup", "infra", "openwrt", "pihole", "wireguard", "monitoring", "lan-satellite"],
        "Network + DNS + VPN + monitoring",
    ),
    DeployProfile(
        "Media Stack",
        ["backup", "infra", "media", "moonlight"],
        "Jellyfin, Kodi, and Moonlight streaming",
    ),
    DeployProfile(
        "Custom",
        [],
        "Manual service selection",
    ),
]


def get_service_tags() -> list[ServiceTag]:
    """Return all known service tags."""
    return list(SERVICE_TAGS)


def get_deploy_profiles() -> list[DeployProfile]:
    """Return all predefined deployment profiles."""
    return list(DEPLOY_PROFILES)


def get_hosts_for_tags(tags: list[str]) -> list[str]:
    """Compute the unique set of hosts targeted by the given tags."""
    tag_map = {t.tag: t for t in SERVICE_TAGS}
    hosts: set[str] = set()
    for tag in tags:
        if tag in tag_map:
            hosts.update(tag_map[tag].hosts)
    return sorted(hosts)


# ── Deploy execution ─────────────────────────────────────────────────


@dataclass
class DeployRecord:
    """A single deployment history entry."""

    timestamp: str
    tags: list[str]
    env_file: str
    exit_code: int
    duration_seconds: float
    host_limit: str | None = None


# ── Ansible exit code semantics ───────────────────────────────────────

ANSIBLE_EXIT_CODES: dict[int, str] = {
    0: "success",
    1: "error",
    2: "host unreachable or task failed",
    3: "invalid command/args",
    4: "host unreachable",
    5: "bad module result",
    99: "interrupted by user",
    250: "unexpected error",
}


def exit_code_label(exit_code: int) -> str:
    """Human-readable label for an Ansible exit code."""
    if exit_code == 0:
        return "success"
    desc = ANSIBLE_EXIT_CODES.get(exit_code, "unknown error")
    return f"failed — {desc}"


def exit_code_color(exit_code: int) -> str:
    """Semantic badge color for an Ansible exit code."""
    return "green" if exit_code == 0 else "red"


# ── Host and Fleet domain model ──────────────────────────────────────


@dataclass
class GuestInfo:
    """A VM or container running on a Proxmox host."""

    vmid: str
    name: str
    vm_type: str  # "vm" or "ct"
    running: bool = True


@dataclass
class HostTelemetry:
    """Live telemetry snapshot from a host's callhome heartbeat."""

    node_id: str
    last_ip: str
    local_ips: list[str]
    first_seen: str
    last_seen: str
    uptime_seconds: float
    services: list[str]
    disk_usage_pct: float
    memory_usage_pct: float
    version: str
    status: str = "offline"
    container_health: ContainerHealth | None = None


def _parse_guests(services: list[str]) -> list[GuestInfo]:
    """Parse service strings ('vm:100:openwrt') into GuestInfo objects."""
    guests: list[GuestInfo] = []
    for entry in services:
        parts = entry.split(":")
        if len(parts) >= 2:
            guests.append(GuestInfo(
                vmid=parts[1],
                name=parts[2] if len(parts) > 2 else parts[1],
                vm_type=parts[0],
                running=True,
            ))
    return guests


class Host:
    """A Proxmox host with identity, deploy history, telemetry, and derived health.

    Business logic lives here — the UI layer only reads properties.
    """

    def __init__(
        self,
        name: str,
        ip: str,
        *,
        wol_capable: bool = True,
        vpn_ip: str = "",
        provisioning_ip: str = "",
        bucket: str = "",
        mac: str = "",
    ) -> None:
        self.name = name
        self.ip = ip
        self.wol_capable = wol_capable
        self.vpn_ip = vpn_ip
        self.provisioning_ip = provisioning_ip
        self.bucket = bucket
        self.mac = mac
        self.deploys: list[DeployRecord] = []
        self.telemetry: HostTelemetry | None = None
        self.reachable: bool | None = None
        self._guests: list[GuestInfo] | None = None

    def attach_telemetry(self, telemetry: HostTelemetry) -> None:
        """Wire live heartbeat data into this host."""
        self.telemetry = telemetry
        self._guests = _parse_guests(telemetry.services)

    # ── Deploy health ────────────────────────────────────────────

    @property
    def last_deploy(self) -> DeployRecord | None:
        return self.deploys[-1] if self.deploys else None

    @property
    def healthy(self) -> bool:
        """True when the most recent deploy succeeded or no deploys recorded."""
        if not self.deploys:
            return True
        return self.deploys[-1].exit_code == 0

    @property
    def errors(self) -> list[str]:
        issues: list[str] = []
        if self.deploys and self.deploys[-1].exit_code != 0:
            last = self.deploys[-1]
            issues.append(
                f"Last deploy failed: {exit_code_label(last.exit_code)} "
                f"({last.timestamp})"
            )
        if self.telemetry and self.telemetry.status == "offline":
            issues.append("Host offline — no heartbeat received recently")
        elif not self.telemetry and self.reachable is False:
            issues.append("Host unreachable — PVE API and NM not responding")
        return issues

    @property
    def warnings(self) -> list[str]:
        """Non-critical warnings (degraded but not broken)."""
        warns: list[str] = []
        if not self.telemetry and self.reachable:
            warns.append("Reachable but no heartbeat — callhome agent may not be running")
        return warns

    # ── Telemetry properties ─────────────────────────────────────

    @property
    def online(self) -> bool:
        if not self.telemetry:
            return False
        return self.telemetry.status == "online"

    @property
    def reachable_ip(self) -> str:
        """VPN IP — the only runtime address for this host."""
        return self.vpn_ip

    @property
    def registered(self) -> bool:
        """True if this host has ever sent a heartbeat (has telemetry data).

        For nationally distributed units, this distinguishes 'deployed and
        reporting' from 'configured but never heartbeated'. A registered
        host that goes dark retains its last-known state (services, disk,
        memory) for historical display.
        """
        return self.telemetry is not None

    @property
    def status(self) -> str:
        if self.telemetry:
            return self.telemetry.status
        if self.reachable:
            return "reachable"
        if self.reachable is False:
            return "unreachable"
        return "unknown"

    @property
    def uptime(self) -> str:
        if not self.telemetry:
            return "--"
        return format_uptime(self.telemetry.uptime_seconds)

    @property
    def uptime_seconds(self) -> float:
        if not self.telemetry:
            return 0.0
        return self.telemetry.uptime_seconds

    @property
    def disk_pct(self) -> float:
        if not self.telemetry:
            return 0.0
        return self.telemetry.disk_usage_pct

    @property
    def memory_pct(self) -> float:
        if not self.telemetry:
            return 0.0
        return self.telemetry.memory_usage_pct

    @property
    def version(self) -> str:
        if not self.telemetry:
            return ""
        return self.telemetry.version

    @property
    def last_seen(self) -> str:
        if not self.telemetry:
            return ""
        return self.telemetry.last_seen

    @property
    def last_seen_relative(self) -> str:
        if not self.telemetry:
            return "never"
        return format_last_seen_relative(self.telemetry.last_seen)

    @property
    def local_ips(self) -> list[str]:
        if not self.telemetry:
            return []
        return self.telemetry.local_ips

    @property
    def extensions(self) -> dict[str, dict]:
        if not self.telemetry or not self.telemetry.container_health:
            return {}
        return self.telemetry.container_health.extensions

    # ── Guest properties ─────────────────────────────────────────

    @property
    def guests(self) -> list[GuestInfo]:
        return self._guests if self._guests is not None else []

    @property
    def guest_count(self) -> int:
        return len(self.guests)

    @property
    def running_guests(self) -> int:
        return sum(1 for g in self.guests if g.running)

    @property
    def vms(self) -> list[GuestInfo]:
        return [g for g in self.guests if g.vm_type == "vm"]

    @property
    def containers(self) -> list[GuestInfo]:
        return [g for g in self.guests if g.vm_type == "ct"]

    def __repr__(self) -> str:
        status = "healthy" if self.healthy else "unhealthy"
        return f"Host({self.name!r}, {self.ip!r}, {status})"


class Fleet:
    """Collection of hosts with aggregate health and telemetry."""

    def __init__(self, hosts: list[Host]) -> None:
        self.hosts = hosts

    # ── Deploy health ────────────────────────────────────────────

    @property
    def healthy(self) -> bool:
        return all(h.healthy for h in self.hosts)

    @property
    def errors(self) -> list[str]:
        return [f"{h.name}: {e}" for h in self.hosts for e in h.errors]

    @property
    def warnings(self) -> list[str]:
        return [f"{h.name}: {w}" for h in self.hosts for w in h.warnings]

    @property
    def unhealthy_hosts(self) -> list[Host]:
        return [h for h in self.hosts if not h.healthy]

    @property
    def registered_count(self) -> int:
        """Hosts that have ever sent a heartbeat."""
        return sum(1 for h in self.hosts if h.registered)

    @property
    def last_deploy(self) -> DeployRecord | None:
        """Most recent deploy across all hosts, sorted by timestamp."""
        all_deploys = [d for h in self.hosts for d in h.deploys]
        if not all_deploys:
            return None
        return max(all_deploys, key=lambda d: d.timestamp)

    @property
    def host_count(self) -> int:
        return len(self.hosts)

    # ── Telemetry aggregates ─────────────────────────────────────

    @property
    def online_count(self) -> int:
        return sum(1 for h in self.hosts if h.online)

    @property
    def offline_count(self) -> int:
        return self.host_count - self.online_count

    @property
    def reachable_count(self) -> int:
        """Hosts reachable via PVE API or NM health check."""
        return sum(1 for h in self.hosts if h.online or h.reachable)

    @property
    def has_telemetry(self) -> bool:
        return any(h.telemetry is not None for h in self.hosts)

    @property
    def total_guests(self) -> int:
        return sum(h.guest_count for h in self.hosts)

    @property
    def running_guests(self) -> int:
        return sum(h.running_guests for h in self.hosts)

    @property
    def total_services(self) -> int:
        return self.total_guests

    @property
    def avg_disk_pct(self) -> float:
        vals = [h.disk_pct for h in self.hosts if h.disk_pct > 0]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    @property
    def avg_memory_pct(self) -> float:
        vals = [h.memory_pct for h in self.hosts if h.memory_pct > 0]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    @property
    def worst_disk(self) -> Host | None:
        with_telem = [h for h in self.hosts if h.telemetry]
        return max(with_telem, key=lambda h: h.disk_pct) if with_telem else None

    @property
    def worst_memory(self) -> Host | None:
        with_telem = [h for h in self.hosts if h.telemetry]
        return max(with_telem, key=lambda h: h.memory_pct) if with_telem else None

    @property
    def health_score(self) -> int:
        """0-100 fleet health: availability (40%), disk (30%), memory (30%)."""
        if not self.has_telemetry:
            return 100
        total = sum(1 for h in self.hosts if h.telemetry)
        if total == 0:
            return 100
        online = sum(1 for h in self.hosts if h.online)
        reporting = [h for h in self.hosts if h.telemetry and h.status != "offline"]
        return compute_health_score(
            online, total,
            [h.disk_pct for h in reporting],
            [h.memory_pct for h in reporting],
        )

    def hosts_by_bucket(self, bucket: str) -> list[Host]:
        """Return hosts belonging to a specific bucket."""
        return [h for h in self.hosts if h.bucket == bucket]

    def get_host(self, name: str) -> Host | None:
        """Look up a host by name."""
        return next((h for h in self.hosts if h.name == name), None)

    def __repr__(self) -> str:
        status = "healthy" if self.healthy else f"{len(self.unhealthy_hosts)} unhealthy"
        return f"Fleet({self.host_count} hosts, {status})"


def _deploy_targets_host(record: DeployRecord, host_name: str) -> bool:
    """Check if a deploy record targeted a specific host."""
    if not record.host_limit:
        return True
    return host_name in record.host_limit


def build_fleet(
    env: dict[str, str],
    state_dir: Path,
    *,
    probe: bool = True,
) -> Fleet:
    """Build a Fleet from the HostRegistry, deploy history, and live telemetry.

    Wires together four data sources into a single domain object:
    1. Host identity from registry.json (via HostRegistry)
    2. Deploy history from deploy_history.json
    3. Live telemetry from nodes.json (callhome heartbeats)
    4. TCP probes for hosts without heartbeat data (when probe=True)

    Set probe=False for timer-driven refreshes to avoid blocking the
    event loop on network timeouts.  Probing is only needed for the
    initial page load or explicit user-triggered refreshes.
    """
    registry = HostRegistry(state_dir)
    registry.seed_from_env(env)

    history = load_deploy_history(state_dir)
    nodes = load_node_registry(state_dir)

    node_map: dict[str, RegisteredNode] = {}
    for n in nodes:
        node_map[n.hostname] = n
        node_map[n.node_id] = n

    hosts: list[Host] = []
    for rec in registry.all():
        host = Host(
            name=rec.name,
            ip=rec.vpn_ip,
            wol_capable=rec.wol_capable,
            vpn_ip=rec.vpn_ip,
            provisioning_ip=rec.ip,
            bucket=rec.bucket,
            mac=rec.mac,
        )
        for record in history:
            if _deploy_targets_host(record, host.name):
                host.deploys.append(record)

        node = node_map.get(rec.name)
        if node:
            host.attach_telemetry(HostTelemetry(
                node_id=node.node_id,
                last_ip=node.last_ip,
                local_ips=node.local_ips,
                first_seen=node.first_seen,
                last_seen=node.last_seen,
                uptime_seconds=node.uptime_seconds,
                services=node.services,
                disk_usage_pct=node.disk_usage_pct,
                memory_usage_pct=node.memory_usage_pct,
                version=node.version,
                status=node.status,
                container_health=node.container_health,
            ))
        hosts.append(host)

    if probe:
        _probe_reachable_hosts(hosts)

    return Fleet(hosts)


def _probe_reachable_hosts(hosts: list[Host]) -> None:
    """Probe hosts for reachability via the NodeManager HTTP API over VPN.

    HTTP health check on the NM API (port 9001) via VPN IP is the
    ONLY probe.  No fallback to WAN/LAN IPs — if VPN is down the
    host is unreachable and must be fixed, not worked around.
    """
    from concurrent.futures import ThreadPoolExecutor

    probeable = [h for h in hosts if not h.telemetry and h.reachable_ip]
    if not probeable:
        return

    def _probe(host: Host) -> None:
        from scripts.webui import heartbeat as hb

        ip = host.reachable_ip
        cb = hb.get_circuit_status(ip)
        if cb["is_open"]:
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://{ip}:{Ports.MANAGER}/api/health", method="GET",
            )
            resp = urllib.request.urlopen(req, timeout=3)
            if resp.status == 200:
                host.reachable = True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass

    with ThreadPoolExecutor(max_workers=min(len(probeable), 4)) as pool:
        list(pool.map(_probe, probeable))


def build_deploy_command(
    env_path: Path,
    tags: list[str],
    limit: str | None = None,
    check: bool = False,
    diff: bool = False,
    verbose: int = 0,
) -> list[str]:
    """Construct the build.py command for a deployment."""
    cmd = [sys.executable, str(PROJECT_ROOT / "build.py")]
    cmd.extend(["--env", str(env_path)])
    if tags:
        cmd.extend(["--tags", ",".join(tags)])
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    if diff:
        cmd.append("--diff")
    if verbose > 0:
        cmd.append("-" + "v" * verbose)
    return cmd


def load_deploy_history(state_dir: Path) -> list[DeployRecord]:
    """Load deploy history from JSON file. Returns empty list if missing."""
    history_file = state_dir / "deploy_history.json"
    if not history_file.exists():
        return []
    try:
        raw = json.loads(history_file.read_text())
        return [
            DeployRecord(
                timestamp=r["timestamp"],
                tags=r["tags"],
                env_file=r["env_file"],
                exit_code=r["exit_code"],
                duration_seconds=r["duration_seconds"],
                host_limit=r.get("host_limit"),
            )
            for r in raw
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def save_deploy_record(state_dir: Path, record: DeployRecord) -> None:
    """Append a deploy record to history, trimming to 50 entries."""
    state_dir.mkdir(parents=True, exist_ok=True)
    history = load_deploy_history(state_dir)
    history.append(record)
    if len(history) > 50:
        history = history[-50:]
    history_file = state_dir / "deploy_history.json"
    raw = [
        {
            "timestamp": r.timestamp,
            "tags": r.tags,
            "env_file": r.env_file,
            "exit_code": r.exit_code,
            "duration_seconds": r.duration_seconds,
            "host_limit": r.host_limit,
        }
        for r in history
    ]
    history_file.write_text(json.dumps(raw, indent=2) + "\n")


# ── Image management ─────────────────────────────────────────────────


@dataclass
class ImageInfo:
    """Status of a single build image."""

    name: str
    filename: str
    exists: bool
    size_mb: float | None = None
    modified_date: str | None = None
    build_target: str = ""
    requires_host: bool = True


EXPECTED_IMAGES: list[tuple[str, str, str, bool]] = [
    ("OpenWrt Mesh LXC", "openwrt-mesh-*.tar.gz", "mesh", False),
    ("OpenWrt Router VM", "openwrt-*.img.gz", "router", False),
    ("Pi-hole DNS", "pihole-*.tar.zst", "pihole", True),
    ("rsyslog Collector", "rsyslog-*.tar.zst", "rsyslog", True),
    ("Jellyfin Media Server", "jellyfin-*.tar.zst", "jellyfin", True),
    ("Netdata Monitoring", "netdata-*.tar.zst", "netdata", True),
    ("WireGuard VPN", "wireguard-*.tar.zst", "wireguard", True),
    ("Home Assistant", "homeassistant-*.tar.zst", "homeassistant", True),
    ("Kodi Media Player", "kodi-*.tar.zst", "kodi", True),
    ("Kiosk Dashboard", "kiosk-*.tar.zst", "kiosk", True),
    ("Moonlight Streaming", "moonlight-*.tar.zst", "moonlight", True),
    ("Gaming LXC", "gaming-*.tar.zst", "gaming", True),
    ("Sunshine VM", "sunshine-*.qcow2", "sunshine", True),
    ("Desktop LXC", "desktop-*.tar.zst", "desktop", True),
]


def get_image_status(images_dir: Path) -> list[ImageInfo]:
    """Check which expected images exist in the images directory."""
    results: list[ImageInfo] = []
    for display_name, pattern, target, requires_host in EXPECTED_IMAGES:
        matches = sorted(images_dir.glob(pattern)) if images_dir.exists() else []
        if matches:
            img = matches[-1]
            stat = img.stat()
            results.append(ImageInfo(
                name=display_name,
                filename=img.name,
                exists=True,
                size_mb=round(stat.st_size / (1024 * 1024), 1),
                modified_date=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                build_target=target,
                requires_host=requires_host,
            ))
        else:
            results.append(ImageInfo(
                name=display_name,
                filename=pattern,
                exists=False,
                build_target=target,
                requires_host=requires_host,
            ))
    return results


def build_image_command(
    target: str,
    host: str | None = None,
    parallel: bool = False,
) -> list[str]:
    """Construct a build-images.sh command."""
    cmd = [str(PROJECT_ROOT / "scripts" / "build-images.sh")]
    if parallel:
        cmd.append("--parallel")
    else:
        cmd.extend(["--only", target])
        if host and target not in ("mesh", "router"):
            cmd.extend(["--host", host])
    return cmd


# ── Kiosk Hub service definitions ────────────────────────────────────


@dataclass
class HubService:
    """A service card displayed on the kiosk Home Hub."""

    key: str
    icon: str
    title: str
    description: str
    tag: str
    section: str
    url_key: str


HUB_SERVICES: list[HubService] = [
    HubService("bridge", "\U0001f4e1", "WiFi Bridge", "Dedicated WDS bridge link status, signal and throughput.", "Bridge", "Infrastructure", "BRIDGE_PAGE"),
    HubService("mesh_detail", "\U0001f4f6", "Mesh WiFi", "Mesh network topology, peer status and signal quality.", "Mesh", "Infrastructure", "MESH_PAGE"),
    HubService("router_detail", "\U0001f5a7", "Router Detail", "Router interfaces, DHCP, firewall and system metrics.", "Router", "Infrastructure", "ROUTER_PAGE"),
    HubService("desktop", "\U0001f5a5", "Desktop", "Full desktop \u2014 view remotely via KasmVNC. Switch between Windows (KDE) and Mac (GNOME) sessions.", "Desktop LXC", "Desktop & Media", "DESKTOP_URL"),
    HubService("jellyfin", "\U0001f3ac", "Jellyfin", "Stream movies, shows and music from your media library.", "Media Server", "Desktop & Media", "JELLYFIN_URL"),
    HubService("kodi", "\U0001f3a6", "Kodi", "Media center with full-screen playback and remote control.", "Media Player", "Desktop & Media", "KODI_URL"),
    HubService("homeassistant", "\U0001f3e0", "Home Assistant", "Smart home dashboard \u2014 automations, sensors and controls.", "Automation", "Desktop & Media", "HOMEASSISTANT_URL"),
    HubService("moonlight", "\U0001f3ae", "Moonlight", "Game streaming client \u2014 stream from Sunshine server.", "Streaming", "Desktop & Media", "MOONLIGHT_URL"),
    HubService("gaming", "\U0001f579", "Gaming", "Sunshine streaming server \u2014 manage streams and apps.", "Game Server", "Desktop & Media", "GAMING_URL"),
    HubService("openwrt", "\U0001f310", "Router", "OpenWrt network management \u2014 firewall, DHCP, WiFi and VLANs.", "Network", "Settings & Network", "OPENWRT_URL"),
    HubService("pihole", "\U0001f6e1", "Pi-hole", "DNS ad-blocking \u2014 query logs, blocklists and statistics.", "DNS", "Settings & Network", "PIHOLE_URL"),
    HubService("wireguard", "\U0001f510", "WireGuard", "VPN tunnel status and peer configuration.", "VPN", "Settings & Network", "WIREGUARD_URL"),
    HubService("netdata", "\U0001f4c8", "Netdata", "Real-time system metrics \u2014 CPU, memory, disk and network.", "Metrics", "Monitoring", "NETDATA_URL"),
    HubService("rsyslog", "\U0001f4dc", "Logs", "Centralized syslog collector \u2014 system and service logs.", "Logging", "Monitoring", "RSYSLOG_URL"),
    HubService("containers", "\U0001f4e6", "Containers & VMs", "Manage LXC containers and QEMU VMs \u2014 start, stop and restart.", "Management", "System", "CONTAINERS_PAGE"),
]

INTERNAL_PAGES: dict[str, str] = {
    "BRIDGE_PAGE": "/bridge",
    "MESH_PAGE": "/mesh",
    "ROUTER_PAGE": "/router",
    "CONTAINERS_PAGE": "/containers",
    "WIREGUARD_URL": "/wireguard",
    "RSYSLOG_URL": "/logs",
}

# ── Display app configuration (single source of truth) ────────────────

DISPLAY_APP_CONFIGS: dict[str, DisplayAppConfig] = {
    "kiosk": DisplayAppConfig(
        app_id="kiosk", handler_type="container_display",
        ct_id=str(VMIDs.KIOSK_CT), display_port=Ports.KIOSK_DISPLAY,
        conflicts=[],
        label="Kiosk", icon="\U0001f3e0",
        description="Home Hub kiosk display (KasmVNC, no DRI conflict).",
    ),
    "desktop": DisplayAppConfig(
        app_id="desktop", handler_type="container_display",
        ct_id=str(VMIDs.DESKTOP_CT), display_port=Ports.DESKTOP_DISPLAY,
        conflicts=["kodi", "moonlight"],
        label="Desktop", icon="\U0001f5a5",
        description="Full desktop \u2014 view remotely via KasmVNC. Switch between Windows (KDE) and Mac (GNOME) sessions.",
        target_hosts=["home"],
    ),
    "kodi": DisplayAppConfig(
        app_id="kodi", handler_type="container_display",
        ct_id=str(VMIDs.KODI_CT), display_port=Ports.KODI_DISPLAY,
        conflicts=["desktop", "moonlight"],
        label="Kodi", icon="\U0001f3a6",
        description="Media center with KasmVNC display.",
        target_hosts=["home"],
    ),
    "moonlight": DisplayAppConfig(
        app_id="moonlight", handler_type="container_display",
        ct_id=str(VMIDs.MOONLIGHT_CT), display_port=Ports.MOONLIGHT_DISPLAY,
        conflicts=["desktop", "kodi"],
        label="Moonlight", icon="\U0001f3ae",
        description="Game streaming client with KasmVNC console.",
        target_hosts=["mesh1"],
    ),
}

_URL_KEY_TO_APP_ID: dict[str, str] = {
    "MOONLIGHT_URL": "moonlight",
    "KODI_URL": "kodi",
    "DESKTOP_URL": "desktop",
}

DISPLAY_APPS: dict[str, dict] = {
    url_key: {
        "vmid": cfg.ct_id,
        "label": cfg.label,
        "icon": cfg.icon,
        "app_id": cfg.app_id,
        "description": cfg.description,
    }
    for url_key, app_id in _URL_KEY_TO_APP_ID.items()
    if (cfg := DISPLAY_APP_CONFIGS.get(app_id))
}


def console_url(node_id: str, app_id: str, back: str = "") -> str:
    """Build a /console/{node_id}/{app_id} URL with optional back param."""
    url = Routes.CONSOLE.replace("{node_id}", node_id).replace("{app_id}", app_id)
    if back:
        from urllib.parse import quote
        url = f"{url}?back={quote(back, safe='')}"
    return url


def get_hub_services() -> list[HubService]:
    """Return the list of kiosk Home Hub service definitions."""
    return list(HUB_SERVICES)


# ── Infrastructure node definitions ──────────────────────────────────


def get_bridge_nodes() -> list[dict]:
    """Return bridge node definitions for the bridge detail page."""
    return [
        {"node_id": "bridge-1", "label": "Bridge 1", "default_role": "ap"},
        {"node_id": "bridge-2", "label": "Bridge 2", "default_role": "sta"},
    ]


def get_mesh_nodes() -> tuple[str, list[str]]:
    """Return (ap_node_id, sta_node_ids) for the mesh detail page."""
    return "home", ["mesh1", "mesh2"]



def get_router_node() -> str:
    """Return the router node ID (OpenWrt VM, not the Proxmox host)."""
    return "openwrt"


KIOSK_CONFIG_PATH = Path("/opt/kiosk/config.json")

SM_SERVICE_URLS: dict[str, tuple[int, int, str]] = {
    "OPENWRT_URL": (1, 80, "http"),
    "PIHOLE_URL": (10, 80, "http"),
    "HOMEASSISTANT_URL": (14, 8123, "http"),
    "JELLYFIN_URL": (15, 8096, "http"),
    "NETDATA_URL": (40, 19999, "http"),
    "GAMING_URL": (18, 47990, "https"),
}


def generate_sm_hub_urls(env: dict[str, str]) -> dict[str, str]:
    """Compute external service URLs for the SuperManager hub from fleet topology.

    Uses LAN_GATEWAY (from env.generated) to derive service IPs.
    The SM viewer iframes load these URLs in the user's browser, which
    must be on the fleet LAN or VPN to reach them.
    """
    gateway = env.get("LAN_GATEWAY", "")
    if not gateway:
        return {}
    prefix = gateway.rsplit(".", 1)[0]
    urls: dict[str, str] = {}
    for key, (offset, port, scheme) in SM_SERVICE_URLS.items():
        ip = f"{prefix}.{offset}"
        suffix = "/admin" if key == "PIHOLE_URL" else ""
        if port in (80, 443):
            urls[key] = f"{scheme}://{ip}{suffix}"
        else:
            urls[key] = f"{scheme}://{ip}:{port}{suffix}"
    return urls


def load_kiosk_config(path: Path | None = None) -> dict[str, str]:
    """Load service URLs from a kiosk JSON config file.

    Falls back to NiceGUI app storage when running inside the full Web UI.
    Used by both hub.py and kiosk_server.py to eliminate duplication.
    """
    cfg_path = path or KIOSK_CONFIG_PATH
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    try:
        from nicegui import app as nicegui_app
        return nicegui_app.storage.general.get("hub_urls", {})
    except (ImportError, RuntimeError):
        return {}


# ── Call-home authentication (canonical source: callhome_crypto.py) ──

from scripts.webui.callhome_crypto import (  # noqa: F401, E402
    CALLHOME_HMAC_MSG,
    derive_public_key,
    generate_callhome_keys,
    validate_callhome_token,
)


# ── Fleet telemetry (canonical source: fleet_telemetry.py) ────────────
# Re-exported here for backward compatibility — all modules can import
# from either ``data`` or ``fleet_telemetry`` interchangeably.

from scripts.webui.fleet_telemetry import (  # noqa: F401, E402
    CONTAINER_READY_SECONDS,
    DISK_CRITICAL_PCT,
    DISK_WARNING_PCT,
    MAX_METRIC_ENTRIES,
    MEMORY_CRITICAL_PCT,
    MEMORY_WARNING_PCT,
    NODE_ONLINE_SECONDS,
    NODE_STALE_SECONDS,
    ContainerHealth,
    FleetHealth,
    MetricSnapshot,
    NodeAlert,
    NodeCheckin,
    ParsedService,
    RegisteredNode,
    ServiceMatch,
    ServiceMatrixEntry,
    _compute_node_status,
    _resource_score,
    _trim_metric_file,
    check_container_ready,
    check_fleet_readiness,
    check_fleet_staleness,
    compute_alerts,
    compute_fleet_health,
    compute_health_score,
    compute_service_matrix,
    format_last_seen_relative,
    format_node_status,
    format_uptime,
    load_metric_history,
    load_node_registry,
    parse_service_entry,
    save_node_registry,
    usage_level,
)
from scripts.webui.fleet_telemetry import (
    register_checkin as _ft_register_checkin,
)


def register_checkin(state_dir: Path, checkin: NodeCheckin, remote_ip: str) -> RegisteredNode:
    """Process a call-home heartbeat with module-level event bus + timeline."""
    return _ft_register_checkin(
        state_dir, checkin, remote_ip,
        event_bus=event_bus,
        record_event=record_service_event,
    )

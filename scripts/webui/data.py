"""Pure data layer for the web UI — no framework imports.

Provides functions for environment management, host discovery, service
tags, deploy command construction, image status, and deploy history.
All functions are synchronous and testable without a running UI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import build  # noqa: E402


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
    EnvVar("DESKTOP_USER", "Desktop VM username", False, "desktop", False),
    EnvVar("DESKTOP_PASSWORD", "Desktop VM password", False, "", True),
    EnvVar("DESKTOP_SSH_PUBLIC_KEY", "SSH public key for desktop VM", False, "", False),
    EnvVar("DESKTOP_AUTOLOGIN", "Enable autologin on desktop VM", False, "false", False),
    EnvVar("DESKTOP_DEFAULT_SESSION", "Default desktop session (plasma/gnome)", False, "plasma", False),
    EnvVar("HA_ADMIN_PASSWORD", "Home Assistant admin password", False, "", True),
    EnvVar("CALLHOME_SERVER", "Management server URL for fleet call-home", False, "http://localhost:8080", False),
    EnvVar("CALLHOME_PRIVATE_KEY", "Server-side secret for validating call-home tokens", False, "", True),
    EnvVar("CALLHOME_PUBLIC_KEY", "Token distributed to nodes for call-home auth", False, "", True),
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
    """Static info about a known host."""

    name: str
    ip: str
    env_var: str
    wol_capable: bool
    is_lan: bool = False


@dataclass
class HostStatus:
    """Result of probing a single host."""

    host: HostInfo
    reachable: bool
    latency_ms: float | None = None
    error: str = ""


@dataclass
class SshResult:
    """Result of an SSH connection test."""

    success: bool
    output: str = ""
    error: str = ""


_HOST_VARS_DIR = PROJECT_ROOT / "inventory" / "host_vars"

_HOST_MAP = {
    "PRIMARY_HOST": "home",
    "AI_HOST": "ai",
    "MESH_2_HOST": "mesh2",
}


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
    """Discover hosts from env vars and inventory."""
    hosts: list[HostInfo] = []
    for env_var, name in _HOST_MAP.items():
        ip = env.get(env_var, "")
        if env_var == "PRIMARY_HOST" or ip:
            hosts.append(HostInfo(
                name=name,
                ip=ip or env.get("PRIMARY_HOST", ""),
                env_var=env_var,
                wol_capable=_read_wol_capable(name),
                is_lan=False,
            ))

    hosts.append(HostInfo(
        name="mesh1",
        ip="10.10.10.210",
        env_var="MESH_1_HOST",
        wol_capable=_read_wol_capable("mesh1"),
        is_lan=True,
    ))
    return hosts


def probe_all_hosts(hosts: list[HostInfo]) -> list[HostStatus]:
    """Probe each host for TCP connectivity."""
    results: list[HostStatus] = []
    for host in hosts:
        if not host.ip:
            results.append(HostStatus(host=host, reachable=False, error="No IP configured"))
            continue
        start = time.monotonic()
        reachable = build.probe_host(host.ip)
        elapsed = (time.monotonic() - start) * 1000
        results.append(HostStatus(
            host=host,
            reachable=reachable,
            latency_ms=round(elapsed, 1) if reachable else None,
            error="" if reachable else f"Connection to {host.ip}:22 timed out",
        ))
    return results


def test_ssh_connection(ip: str) -> SshResult:
    """Test SSH connectivity to a host."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no", f"root@{ip}", "echo ok"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return SshResult(success=True, output=result.stdout.strip())
        return SshResult(success=False, error=result.stderr.strip())
    except subprocess.TimeoutExpired:
        return SshResult(success=False, error="SSH connection timed out")
    except FileNotFoundError:
        return SshResult(success=False, error="ssh binary not found")


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
    ServiceTag("backup", "Back up host config and VMs", "Network", ["home", "mesh1", "ai", "mesh2"]),
    ServiceTag("infra", "Bridges, PCI passthrough, iGPU", "Network", ["home", "mesh1", "ai", "mesh2"]),
    ServiceTag("openwrt", "OpenWrt router VM", "Network", ["home"]),
    ServiceTag("lan-satellite", "Bootstrap LAN hosts", "Network", ["home", "mesh1"]),
    ServiceTag("cleanup", "Remove temp bootstrap networking", "Network", ["home", "mesh1", "ai", "mesh2"]),
    ServiceTag("pihole", "Pi-hole DNS", "DNS & VPN", ["home"]),
    ServiceTag("wireguard", "WireGuard VPN", "DNS & VPN", ["home", "mesh1", "ai", "mesh2"]),
    ServiceTag("monitoring", "rsyslog + Netdata", "Monitoring", ["home"]),
    ServiceTag("homeassistant", "Home Assistant", "Services", ["home"]),
    ServiceTag("media", "Jellyfin + Kodi", "Media", ["home"]),
    ServiceTag("moonlight", "Moonlight streaming client", "Media", ["mesh1"]),
    ServiceTag("desktop", "Debian desktop VM", "Desktop", ["home"]),
    ServiceTag("kiosk", "Custom UX kiosk", "Desktop", ["home"]),
    ServiceTag("mesh-wifi", "Mesh WiFi LXC", "WiFi", ["mesh1", "mesh2"]),
    ServiceTag("gaming", "Gaming LXC (opt-in)", "Gaming", ["ai"], is_opt_in=True),
]


@dataclass
class DeployProfile:
    """A preset combination of service tags."""

    name: str
    tags: list[str]
    description: str


DEPLOY_PROFILES: list[DeployProfile] = [
    DeployProfile(
        "Full Deploy",
        [t.tag for t in SERVICE_TAGS if not t.is_opt_in and t.tag != "cleanup"],
        "Deploy all services (except opt-in gaming and cleanup)",
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
    ("Desktop VM", "desktop-*.qcow2", "desktop", True),
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
    HubService("desktop", "\U0001f5a5", "Desktop", "Full Debian KDE desktop \u2014 launch via Proxmox or remote control.", "Desktop VM", "Desktop & Media", "DESKTOP_URL"),
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
]


def get_hub_services() -> list[HubService]:
    """Return the list of kiosk Home Hub service definitions."""
    return list(HUB_SERVICES)


KIOSK_CONFIG_PATH = Path("/opt/kiosk/config.json")


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


# ── Call-home authentication ──────────────────────────────────────────

CALLHOME_HMAC_MSG = b"vm_builds_callhome"


def generate_callhome_keys() -> tuple[str, str]:
    """Generate a (private_key, public_key) pair for call-home auth.

    The private key stays on the management server. The public key
    (derived via HMAC-SHA256) is distributed to fleet nodes.
    """
    private_key = secrets.token_hex(32)
    public_key = derive_public_key(private_key)
    return private_key, public_key


def derive_public_key(private_key: str) -> str:
    """Derive the public key from a private key."""
    return hmac.new(
        private_key.encode(), CALLHOME_HMAC_MSG, hashlib.sha256,
    ).hexdigest()


def validate_callhome_token(token: str, private_key: str) -> bool:
    """Check whether a presented token matches the server's private key."""
    if not token or not private_key:
        return False
    expected = derive_public_key(private_key)
    return hmac.compare_digest(token, expected)


# ── Node call-home registry ──────────────────────────────────────────

NODE_ONLINE_SECONDS = 300
NODE_STALE_SECONDS = 3600


@dataclass
class NodeCheckin:
    """Payload sent by a node during a call-home heartbeat."""

    node_id: str
    hostname: str
    local_ips: list[str]
    uptime_seconds: float
    services: list[str]
    disk_usage_pct: float
    memory_usage_pct: float
    version: str


@dataclass
class RegisteredNode:
    """Persisted state for a single fleet node."""

    node_id: str
    hostname: str
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


def format_uptime(seconds: float) -> str:
    """Human-readable uptime string from seconds."""
    if seconds <= 0:
        return "--"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


def format_node_status(status: str) -> str:
    """Status string with Unicode indicator dot."""
    if status == "online":
        return "\u25cf Online"
    if status == "stale":
        return "\u25cb Stale"
    return "\u25cf Offline"


def fleet_summary(nodes: list[RegisteredNode]) -> tuple[str, str]:
    """Compute a one-line fleet summary and its status level.

    Returns (text, status) where status is "success", "warning", "error", or "info".
    """
    if not nodes:
        return "No nodes registered", "info"
    online = sum(1 for n in nodes if n.status == "online")
    total = len(nodes)
    if online == total:
        return f"All {total} nodes online", "success"
    stale = sum(1 for n in nodes if n.status == "stale")
    offline = sum(1 for n in nodes if n.status == "offline")
    parts = []
    if online:
        parts.append(f"{online} online")
    if stale:
        parts.append(f"{stale} stale")
    if offline:
        parts.append(f"{offline} offline")
    level = "warning" if online > 0 else "error"
    return " · ".join(parts), level


def _compute_node_status(last_seen: str) -> str:
    """Determine online/stale/offline from last_seen timestamp."""
    try:
        last_dt = datetime.fromisoformat(last_seen)
        age = (datetime.now() - last_dt).total_seconds()
    except (ValueError, TypeError):
        return "offline"
    if age <= NODE_ONLINE_SECONDS:
        return "online"
    if age <= NODE_STALE_SECONDS:
        return "stale"
    return "offline"


def load_node_registry(state_dir: Path) -> list[RegisteredNode]:
    """Load node registry from JSON and recompute statuses."""
    registry_file = state_dir / "nodes.json"
    if not registry_file.exists():
        return []
    try:
        raw = json.loads(registry_file.read_text())
        nodes = []
        for r in raw:
            node = RegisteredNode(
                node_id=r["node_id"],
                hostname=r["hostname"],
                last_ip=r["last_ip"],
                local_ips=r.get("local_ips", []),
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                uptime_seconds=r.get("uptime_seconds", 0),
                services=r.get("services", []),
                disk_usage_pct=r.get("disk_usage_pct", 0),
                memory_usage_pct=r.get("memory_usage_pct", 0),
                version=r.get("version", ""),
            )
            node.status = _compute_node_status(node.last_seen)
            nodes.append(node)
        return nodes
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _save_node_registry(state_dir: Path, nodes: list[RegisteredNode]) -> None:
    """Write node registry to JSON and a plain-text IP map."""
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_file = state_dir / "nodes.json"
    raw = [
        {
            "node_id": n.node_id,
            "hostname": n.hostname,
            "last_ip": n.last_ip,
            "local_ips": n.local_ips,
            "first_seen": n.first_seen,
            "last_seen": n.last_seen,
            "uptime_seconds": n.uptime_seconds,
            "services": n.services,
            "disk_usage_pct": n.disk_usage_pct,
            "memory_usage_pct": n.memory_usage_pct,
            "version": n.version,
        }
        for n in nodes
    ]
    registry_file.write_text(json.dumps(raw, indent=2) + "\n")
    _write_fleet_ips(state_dir, nodes)


def _write_fleet_ips(state_dir: Path, nodes: list[RegisteredNode]) -> None:
    """Write a simple hostname→IP text file for easy consumption."""
    ip_file = state_dir / "fleet_ips.txt"
    lines = [f"{n.hostname}\t{n.last_ip}" for n in nodes if n.last_ip]
    if lines:
        ip_file.write_text("\n".join(sorted(lines)) + "\n")
    else:
        ip_file.write_text("")


def register_checkin(state_dir: Path, checkin: NodeCheckin, remote_ip: str) -> RegisteredNode:
    """Process a call-home heartbeat: upsert the node in the registry."""
    nodes = load_node_registry(state_dir)
    now = datetime.now().isoformat(timespec="seconds")

    existing = next((n for n in nodes if n.node_id == checkin.node_id), None)
    if existing:
        existing.hostname = checkin.hostname
        existing.last_ip = remote_ip
        existing.local_ips = checkin.local_ips
        existing.last_seen = now
        existing.uptime_seconds = checkin.uptime_seconds
        existing.services = checkin.services
        existing.disk_usage_pct = checkin.disk_usage_pct
        existing.memory_usage_pct = checkin.memory_usage_pct
        existing.version = checkin.version
        existing.status = "online"
        _save_node_registry(state_dir, nodes)
        _append_metric_snapshot(state_dir, checkin)
        return existing

    new_node = RegisteredNode(
        node_id=checkin.node_id,
        hostname=checkin.hostname,
        last_ip=remote_ip,
        local_ips=checkin.local_ips,
        first_seen=now,
        last_seen=now,
        uptime_seconds=checkin.uptime_seconds,
        services=checkin.services,
        disk_usage_pct=checkin.disk_usage_pct,
        memory_usage_pct=checkin.memory_usage_pct,
        version=checkin.version,
        status="online",
    )
    nodes.append(new_node)
    _save_node_registry(state_dir, nodes)
    _append_metric_snapshot(state_dir, checkin)
    return new_node


# ── Metric history ───────────────────────────────────────────────────

MAX_METRIC_ENTRIES = 1440


@dataclass
class MetricSnapshot:
    """Single point-in-time metric sample for a node."""

    timestamp: str
    disk_usage_pct: float
    memory_usage_pct: float
    uptime_seconds: float
    service_count: int


def _append_metric_snapshot(state_dir: Path, checkin: NodeCheckin) -> None:
    """Append a metric snapshot to the node's history file (JSONL)."""
    metrics_dir = state_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    safe_id = checkin.node_id.replace("/", "_").replace("..", "_")
    metric_file = metrics_dir / f"{safe_id}.jsonl"
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "disk": checkin.disk_usage_pct,
        "mem": checkin.memory_usage_pct,
        "up": checkin.uptime_seconds,
        "svcs": len(checkin.services),
    }
    with open(metric_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    _trim_metric_file(metric_file)


def _trim_metric_file(path: Path) -> None:
    """Keep only the last MAX_METRIC_ENTRIES lines."""
    try:
        lines = path.read_text().splitlines()
        if len(lines) > MAX_METRIC_ENTRIES:
            path.write_text("\n".join(lines[-MAX_METRIC_ENTRIES:]) + "\n")
    except OSError:
        pass


def load_metric_history(
    state_dir: Path, node_id: str, max_entries: int = 60,
) -> list[MetricSnapshot]:
    """Load recent metric snapshots for a node."""
    safe_id = node_id.replace("/", "_").replace("..", "_")
    metric_file = state_dir / "metrics" / f"{safe_id}.jsonl"
    if not metric_file.exists():
        return []
    snapshots: list[MetricSnapshot] = []
    try:
        lines = metric_file.read_text().splitlines()
        for line in lines[-max_entries:]:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                snapshots.append(MetricSnapshot(
                    timestamp=r.get("ts", ""),
                    disk_usage_pct=r.get("disk", 0),
                    memory_usage_pct=r.get("mem", 0),
                    uptime_seconds=r.get("up", 0),
                    service_count=r.get("svcs", 0),
                ))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return snapshots


# ── Fleet health analytics ───────────────────────────────────────────

DISK_WARNING_PCT = 70.0
DISK_CRITICAL_PCT = 85.0
MEMORY_WARNING_PCT = 70.0
MEMORY_CRITICAL_PCT = 85.0


@dataclass
class FleetHealth:
    """Aggregate health metrics across all fleet nodes."""

    total_nodes: int
    online_nodes: int
    stale_nodes: int
    offline_nodes: int
    total_services: int
    avg_disk_pct: float
    avg_memory_pct: float
    health_score: int
    worst_disk_node: str
    worst_disk_pct: float
    worst_memory_node: str
    worst_memory_pct: float


def compute_fleet_health(nodes: list[RegisteredNode]) -> FleetHealth:
    """Compute aggregate fleet health from registered nodes."""
    if not nodes:
        return FleetHealth(
            total_nodes=0, online_nodes=0, stale_nodes=0, offline_nodes=0,
            total_services=0, avg_disk_pct=0, avg_memory_pct=0,
            health_score=100, worst_disk_node="", worst_disk_pct=0,
            worst_memory_node="", worst_memory_pct=0,
        )

    online = sum(1 for n in nodes if n.status == "online")
    stale = sum(1 for n in nodes if n.status == "stale")
    offline = sum(1 for n in nodes if n.status == "offline")
    total_svcs = sum(len(n.services) for n in nodes)

    disk_vals = [n.disk_usage_pct for n in nodes if n.disk_usage_pct > 0]
    mem_vals = [n.memory_usage_pct for n in nodes if n.memory_usage_pct > 0]
    avg_disk = round(sum(disk_vals) / len(disk_vals), 1) if disk_vals else 0
    avg_mem = round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else 0

    worst_disk_node = max(nodes, key=lambda n: n.disk_usage_pct)
    worst_mem_node = max(nodes, key=lambda n: n.memory_usage_pct)

    score = _compute_health_score(nodes, online, len(nodes))

    return FleetHealth(
        total_nodes=len(nodes), online_nodes=online,
        stale_nodes=stale, offline_nodes=offline,
        total_services=total_svcs,
        avg_disk_pct=avg_disk, avg_memory_pct=avg_mem,
        health_score=score,
        worst_disk_node=worst_disk_node.hostname,
        worst_disk_pct=worst_disk_node.disk_usage_pct,
        worst_memory_node=worst_mem_node.hostname,
        worst_memory_pct=worst_mem_node.memory_usage_pct,
    )


def _compute_health_score(
    nodes: list[RegisteredNode], online: int, total: int,
) -> int:
    """0-100 score: availability (40%), disk (30%), memory (30%)."""
    if total == 0:
        return 100
    avail_score = (online / total) * 40

    disk_score = 0.0
    mem_score = 0.0
    reporting = [n for n in nodes if n.status != "offline"]
    if reporting:
        for n in reporting:
            disk_score += _resource_score(n.disk_usage_pct)
            mem_score += _resource_score(n.memory_usage_pct)
        disk_score = (disk_score / len(reporting)) * 30
        mem_score = (mem_score / len(reporting)) * 30
    else:
        disk_score = 0.0
        mem_score = 0.0

    return max(0, min(100, round(avail_score + disk_score + mem_score)))


def _resource_score(usage_pct: float) -> float:
    """1.0 for low usage, tapering to 0.0 at 100%."""
    if usage_pct <= 50:
        return 1.0
    if usage_pct >= 95:
        return 0.0
    return round(1.0 - ((usage_pct - 50) / 45), 3)


# ── Node alerts ──────────────────────────────────────────────────────


@dataclass
class NodeAlert:
    """A health alert for a specific node."""

    hostname: str
    severity: str
    message: str
    metric: str


def compute_alerts(nodes: list[RegisteredNode]) -> list[NodeAlert]:
    """Generate alerts for nodes with concerning metrics."""
    alerts: list[NodeAlert] = []
    versions: set[str] = set()

    for n in nodes:
        if n.status == "offline":
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="critical",
                message=f"Node offline — last seen {n.last_seen or 'never'}",
                metric="status",
            ))
        elif n.status == "stale":
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="warning",
                message=f"Stale — last check-in {n.last_seen}",
                metric="status",
            ))

        if n.disk_usage_pct >= DISK_CRITICAL_PCT:
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="critical",
                message=f"Disk usage {n.disk_usage_pct}% (critical >={DISK_CRITICAL_PCT}%)",
                metric="disk",
            ))
        elif n.disk_usage_pct >= DISK_WARNING_PCT:
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="warning",
                message=f"Disk usage {n.disk_usage_pct}% (warning >={DISK_WARNING_PCT}%)",
                metric="disk",
            ))

        if n.memory_usage_pct >= MEMORY_CRITICAL_PCT:
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="critical",
                message=f"Memory usage {n.memory_usage_pct}% (critical >={MEMORY_CRITICAL_PCT}%)",
                metric="memory",
            ))
        elif n.memory_usage_pct >= MEMORY_WARNING_PCT:
            alerts.append(NodeAlert(
                hostname=n.hostname, severity="warning",
                message=f"Memory usage {n.memory_usage_pct}% (warning >={MEMORY_WARNING_PCT}%)",
                metric="memory",
            ))

        if n.version:
            versions.add(n.version)

    if len(versions) > 1:
        for n in nodes:
            if n.version:
                alerts.append(NodeAlert(
                    hostname=n.hostname, severity="warning",
                    message=f"Version {n.version} — fleet has mixed versions: {', '.join(sorted(versions))}",
                    metric="version",
                ))

    alerts.sort(key=lambda a: (0 if a.severity == "critical" else 1, a.hostname))
    return alerts


# ── Service matrix ───────────────────────────────────────────────────


@dataclass
class ParsedService:
    """A parsed running service entry from call-home data."""

    vm_type: str
    vmid: str
    name: str


def parse_service_entry(entry: str) -> ParsedService | None:
    """Parse 'vm:100:openwrt' or 'ct:101:wireguard' format."""
    parts = entry.split(":")
    if len(parts) < 2:
        return None
    return ParsedService(
        vm_type=parts[0],
        vmid=parts[1],
        name=parts[2] if len(parts) > 2 else parts[1],
    )


@dataclass
class ServiceMatrixEntry:
    """One cell in the service matrix: service × node."""

    service_name: str
    vmid: str
    vm_type: str
    running: bool


def compute_service_matrix(
    nodes: list[RegisteredNode],
) -> tuple[list[str], dict[str, dict[str, ServiceMatrixEntry | None]]]:
    """Build a service-by-node matrix from running services.

    Returns (service_names_sorted, {service_name: {hostname: entry_or_None}}).
    """
    all_services: dict[str, dict[str, ServiceMatrixEntry]] = {}
    hostnames = [n.hostname for n in nodes]

    for node in nodes:
        for svc_str in node.services:
            parsed = parse_service_entry(svc_str)
            if not parsed:
                continue
            if parsed.name not in all_services:
                all_services[parsed.name] = {}
            all_services[parsed.name][node.hostname] = ServiceMatrixEntry(
                service_name=parsed.name,
                vmid=parsed.vmid,
                vm_type=parsed.vm_type,
                running=True,
            )

    svc_names = sorted(all_services.keys())
    matrix: dict[str, dict[str, ServiceMatrixEntry | None]] = {}
    for svc_name in svc_names:
        matrix[svc_name] = {}
        for hostname in hostnames:
            matrix[svc_name][hostname] = all_services[svc_name].get(hostname)

    return svc_names, matrix


def format_last_seen_relative(last_seen: str) -> str:
    """Format a last_seen ISO timestamp as a human-readable relative string."""
    if not last_seen:
        return "never"
    try:
        dt = datetime.fromisoformat(last_seen)
        age = (datetime.now() - dt).total_seconds()
    except (ValueError, TypeError):
        return "unknown"
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def usage_level(pct: float) -> str:
    """Classify a usage percentage as ok/warning/critical."""
    if pct >= 85:
        return "critical"
    if pct >= 70:
        return "warning"
    return "ok"

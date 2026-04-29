"""Fleet telemetry: node registry, heartbeats, readiness, and health analytics.

Manages the node registry (``nodes.json``), heartbeat check-in processing,
fleet readiness/staleness checks, health scoring, alerts, metrics history,
service matrix, and formatting helpers.

All functions are synchronous and testable without a running UI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


_log = logging.getLogger("vm_builds.fleet")


# ── Constants ─────────────────────────────────────────────────────────

NODE_ONLINE_SECONDS = 300
NODE_STALE_SECONDS = 3600
CONTAINER_READY_SECONDS = 120
MAX_METRIC_ENTRIES = 1440

DISK_WARNING_PCT = 70.0
DISK_CRITICAL_PCT = 85.0
MEMORY_WARNING_PCT = 70.0
MEMORY_CRITICAL_PCT = 85.0


# ── Core dataclasses ──────────────────────────────────────────────────


@dataclass
class ContainerHealth:
    """Health snapshot pushed by a container's callhome agent.

    Core fields (systemd_services, listening_ports, ready) are always present.
    Service-specific data lives in extensions — a flat dict of collector name
    to arbitrary dict payload. Each collector (network, wireguard, docker, etc.)
    is independently composable; the API passes extensions through without
    needing to understand their schema.
    """

    container_id: str
    systemd_services: dict[str, str]
    listening_ports: list[int]
    ready: bool
    extensions: dict[str, dict] = field(default_factory=dict)


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
    container_health: ContainerHealth | None = None


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
    container_health: ContainerHealth | None = None


# ── Formatting helpers ────────────────────────────────────────────────


def format_uptime(seconds: float) -> str:
    """Human-readable uptime string from seconds.

    Canonical uptime formatter — used by Fleet dashboard, cluster
    dashboard, and node detail pages. Sub-hour precision for short
    uptimes, day+hour for long uptimes.
    """
    if seconds <= 0:
        return "--"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_node_status(status: str) -> str:
    """Status string with Unicode indicator dot."""
    labels = {
        "online": "\u25cf Online",
        "stale": "\u25cb Stale",
        "reachable": "\u25cb Reachable",
        "unreachable": "\u25cf Unreachable",
        "unknown": "\u25cb Unknown",
        "offline": "\u25cb Offline",
    }
    return labels.get(status, "\u25cb Offline")


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


# ── Node status computation ──────────────────────────────────────────


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


# ── Node registry persistence ────────────────────────────────────────


def load_node_registry(state_dir: Path) -> list[RegisteredNode]:
    """Load node registry from JSON and recompute statuses."""
    registry_file = state_dir / "nodes.json"
    if not registry_file.exists():
        return []
    try:
        raw = json.loads(registry_file.read_text())
        nodes = []
        for r in raw:
            ch_raw = r.get("container_health")
            ch = None
            if ch_raw and isinstance(ch_raw, dict):
                ch = ContainerHealth(
                    container_id=ch_raw.get("container_id", ""),
                    systemd_services=ch_raw.get("systemd_services", {}),
                    listening_ports=ch_raw.get("listening_ports", []),
                    ready=ch_raw.get("ready", False),
                    extensions=ch_raw.get("extensions", {}),
                )
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
                container_health=ch,
            )
            node.status = _compute_node_status(node.last_seen)
            nodes.append(node)
        return nodes
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def save_node_registry(state_dir: Path, nodes: list[RegisteredNode]) -> None:
    """Write node registry to JSON and a plain-text IP map."""
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_file = state_dir / "nodes.json"
    raw = []
    for n in nodes:
        entry: dict = {
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
        if n.container_health:
            entry["container_health"] = {
                "container_id": n.container_health.container_id,
                "systemd_services": n.container_health.systemd_services,
                "listening_ports": n.container_health.listening_ports,
                "ready": n.container_health.ready,
                "extensions": n.container_health.extensions,
            }
        raw.append(entry)
    registry_file.write_text(json.dumps(raw, indent=2) + "\n")
    _write_fleet_ips(state_dir, nodes)


def _write_fleet_ips(state_dir: Path, nodes: list[RegisteredNode]) -> None:
    """Write a simple hostname->IP text file for easy consumption."""
    ip_file = state_dir / "fleet_ips.txt"
    lines = [f"{n.hostname}\t{n.last_ip}" for n in nodes if n.last_ip]
    if lines:
        ip_file.write_text("\n".join(sorted(lines)) + "\n")
    else:
        ip_file.write_text("")


# ── Check-in processing ──────────────────────────────────────────────


def register_checkin(
    state_dir: Path,
    checkin: NodeCheckin,
    remote_ip: str,
    event_bus: Any = None,
    record_event: Any = None,
) -> RegisteredNode:
    """Process a call-home heartbeat: upsert the node in ``nodes.json``.

    Heartbeats update the live telemetry store (``nodes.json``) only.
    The ``HostRegistry`` (``registry.json``) is NOT modified here — it
    contains physical host identities seeded from env vars or manual
    registration. Containers heartbeat their health data into
    ``nodes.json``; the fleet dashboard groups that data under the
    parent host, never as independent fleet members.

    Emits SSE events on state transitions:
      - ``container_ready``  — first check-in or readiness flip to True
      - ``container_stale``  — readiness flips to False (was previously ready)
      - ``node_checkin``     — every successful check-in
    """
    nodes = load_node_registry(state_dir)
    now = datetime.now().isoformat(timespec="seconds")

    container_id = ""
    if checkin.container_health:
        container_id = checkin.container_health.container_id or checkin.hostname

    existing = next((n for n in nodes if n.node_id == checkin.node_id), None)
    if existing:
        was_ready = bool(
            existing.container_health
            and existing.container_health.ready
        )
        existing.hostname = checkin.hostname
        existing.last_ip = remote_ip
        existing.local_ips = checkin.local_ips
        existing.last_seen = now
        existing.uptime_seconds = checkin.uptime_seconds
        existing.services = checkin.services
        existing.disk_usage_pct = checkin.disk_usage_pct
        existing.memory_usage_pct = checkin.memory_usage_pct
        existing.version = checkin.version
        existing.container_health = checkin.container_health
        existing.status = "online"
        save_node_registry(state_dir, nodes)
        _append_metric_snapshot(state_dir, checkin)

        is_ready = bool(checkin.container_health and checkin.container_health.ready)
        if event_bus:
            if is_ready and not was_ready:
                event_bus.emit({
                    "type": "container_ready",
                    "container_id": container_id,
                    "node_id": checkin.node_id,
                    "timestamp": now,
                })
            elif was_ready and not is_ready:
                event_bus.emit({
                    "type": "container_stale",
                    "container_id": container_id,
                    "node_id": checkin.node_id,
                    "timestamp": now,
                })
            event_bus.emit({
                "type": "node_checkin",
                "node_id": checkin.node_id,
                "hostname": checkin.hostname,
                "container_id": container_id,
                "ready": is_ready,
                "timestamp": now,
            })
        if record_event:
            if is_ready and not was_ready:
                record_event(container_id, "container_ready")
            record_event(container_id, "node_checkin")
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
        container_health=checkin.container_health,
        status="online",
    )
    nodes.append(new_node)
    save_node_registry(state_dir, nodes)
    _append_metric_snapshot(state_dir, checkin)

    is_ready = bool(checkin.container_health and checkin.container_health.ready)
    etype = "container_ready" if is_ready else "node_checkin"
    if event_bus:
        event_bus.emit({
            "type": etype,
            "container_id": container_id,
            "node_id": checkin.node_id,
            "hostname": checkin.hostname,
            "ready": is_ready,
            "first_seen": True,
            "timestamp": now,
        })
    if record_event:
        record_event(container_id, etype)
    return new_node


# ── Fleet readiness ───────────────────────────────────────────────────


def _is_recently_seen(last_seen: str, max_age: int = CONTAINER_READY_SECONDS) -> bool:
    """True if last_seen is within max_age seconds of now."""
    if not last_seen:
        return False
    try:
        seen_dt = datetime.fromisoformat(last_seen)
        return (datetime.now() - seen_dt).total_seconds() < max_age
    except (ValueError, TypeError):
        return False


@dataclass
class ServiceMatch:
    """Result of resolving a service name against the node registry.

    Unified match result used by container-ready, fleet-readiness, and
    fleet-staleness checks — eliminates duplicated 3-level matching logic.
    """

    found: bool = False
    ready: bool = False
    recent: bool = False
    status: str = "unknown"
    last_seen: str = ""
    node_id: str = ""
    hostname: str = ""
    systemd_services: dict = field(default_factory=dict)
    listening_ports: list = field(default_factory=list)
    extensions: dict = field(default_factory=dict)
    nested: bool = False


def _node_last_seen_key(n: RegisteredNode) -> str:
    """Sort key: most recent last_seen first (ISO format sorts lexically)."""
    return n.last_seen or ""


def _build_service_match(
    level: int, n: RegisteredNode, service_id: str, max_age: int,
) -> ServiceMatch:
    """Build a ServiceMatch from a resolved candidate at the given level."""
    if level == 1:
        assert n.container_health is not None
        recent = _is_recently_seen(n.last_seen, max_age)
        return ServiceMatch(
            found=True,
            ready=recent and n.container_health.ready,
            recent=recent,
            status=n.status,
            last_seen=n.last_seen,
            node_id=n.node_id,
            hostname=n.hostname,
            systemd_services=n.container_health.systemd_services,
            listening_ports=n.container_health.listening_ports,
            extensions=n.container_health.extensions,
        )
    if level == 2:
        recent = _is_recently_seen(n.last_seen, max_age)
        is_ready = recent and n.container_health.ready if n.container_health else recent
        return ServiceMatch(
            found=True,
            ready=is_ready,
            recent=recent,
            status=n.status,
            last_seen=n.last_seen,
            node_id=n.node_id,
            hostname=n.hostname,
        )
    assert n.container_health is not None
    ct = n.container_health.extensions.get("containers", {})[service_id]
    recent = _is_recently_seen(n.last_seen, max_age)
    return ServiceMatch(
        found=True,
        ready=recent and ct.get("ready", False),
        recent=recent,
        status="running" if ct.get("ready") else "degraded",
        last_seen=ct.get("last_seen", n.last_seen),
        node_id=n.node_id,
        hostname=n.hostname,
        systemd_services=ct.get("systemd_services", {}),
        listening_ports=ct.get("listening_ports", []),
        extensions=ct.get("extensions", {}),
        nested=True,
    )


def _resolve_service(
    nodes: list[RegisteredNode], service_id: str,
    max_age: int = CONTAINER_READY_SECONDS,
) -> ServiceMatch:
    """Match a service name against nodes using the 3-level search.

    When multiple nodes share the same container_id (e.g. "wireguard" on
    4 hosts), the most recently seen match wins.  This prevents stale
    entries from previous runs from shadowing fresh heartbeats.

    1. container_health.container_id exact match (freshest wins)
    2. hostname / node_id match (freshest wins)
    3. Nested containers in extensions.containers (4-tier relay)

    Fresh-data preference: when the best priority level only has stale
    matches but a lower-priority level has fresh data, the fresh match
    wins. This handles the bootstrap → relay transition where direct
    container heartbeats (level 2) become stale after callhome is
    rewritten to the local NodeManager, while relayed heartbeats
    (level 3) carry fresh data.
    """
    candidates: list[tuple[int, RegisteredNode]] = []
    for n in nodes:
        if n.container_health and n.container_health.container_id == service_id:
            candidates.append((1, n))
        elif service_id in (n.hostname, n.node_id):
            candidates.append((2, n))
        elif n.container_health:
            nested = n.container_health.extensions.get("containers", {})
            if service_id in nested:
                candidates.append((3, n))

    if not candidates:
        return ServiceMatch()

    for level in sorted(set(c[0] for c in candidates)):
        level_matches = [c[1] for c in candidates if c[0] == level]
        level_matches.sort(key=_node_last_seen_key, reverse=True)
        freshest = level_matches[0]
        if _is_recently_seen(freshest.last_seen, max_age):
            return _build_service_match(level, freshest, service_id, max_age)

    best_level = min(c[0] for c in candidates)
    level_matches = [c[1] for c in candidates if c[0] == best_level]
    level_matches.sort(key=_node_last_seen_key, reverse=True)
    return _build_service_match(best_level, level_matches[0], service_id, max_age)


def check_container_ready(state_dir: Path, container_id: str) -> dict:
    """Check if a container has heartbeated recently."""
    nodes = load_node_registry(state_dir)
    m = _resolve_service(nodes, container_id)
    result: dict[str, Any] = {
        "container_id": container_id,
        "ready": m.ready,
        "status": m.status,
        "last_seen": m.last_seen,
        "systemd_services": m.systemd_services,
        "listening_ports": m.listening_ports,
        "extensions": m.extensions,
    }
    if m.nested:
        result["host"] = m.hostname
    return result


def _resolve_all_instances(
    nodes: list[RegisteredNode], service_id: str,
    max_age: int = CONTAINER_READY_SECONDS,
) -> list[ServiceMatch]:
    """Find ALL instances of a service across all nodes.

    Unlike _resolve_service (which returns the single freshest match),
    this returns every node that has the service — via direct heartbeat
    OR via relayed container data in extensions.containers.
    """
    results: list[ServiceMatch] = []
    seen_node_ids: set[str] = set()
    for n in nodes:
        if n.container_health and n.container_health.container_id == service_id:
            if n.node_id not in seen_node_ids:
                results.append(_build_service_match(1, n, service_id, max_age))
                seen_node_ids.add(n.node_id)
        elif service_id in (n.hostname, n.node_id):
            if n.node_id not in seen_node_ids:
                results.append(_build_service_match(2, n, service_id, max_age))
                seen_node_ids.add(n.node_id)
        elif n.container_health:
            nested = n.container_health.extensions.get("containers", {})
            if service_id in nested and n.node_id not in seen_node_ids:
                results.append(_build_service_match(3, n, service_id, max_age))
                seen_node_ids.add(n.node_id)
    return results


def _load_registered_host_count(state_dir: Path) -> int:
    """Load the number of registered physical hosts from registry.json.

    The host registry is the source of truth for how many NMs exist.
    Every registered host MUST have a kiosk (kiosk_nodes = all hosts).
    """
    registry_file = state_dir / "registry.json"
    if not registry_file.exists():
        return 0
    try:
        records = json.loads(registry_file.read_text())
        if isinstance(records, list):
            return len(records)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def check_fleet_readiness(
    state_dir: Path, expected_services: list[str],
) -> dict:
    """Check readiness of ALL instances of each service across fleet.

    Driven by the host registry: total = registered NMs that SHOULD
    have the service heartbeating. A fleet with 6 registered hosts
    returns total=6 for kiosk (since kiosk_nodes = all hosts).

    all_ready = every registered host has the service AND it's ready.
    If 6 hosts are registered but only 1 has kiosk heartbeats, that's
    1/6 — NOT ready.
    """
    nodes = load_node_registry(state_dir)
    registered_count = _load_registered_host_count(state_dir)
    services: dict[str, dict] = {}
    total_instances = 0
    total_ready = 0

    any_missing = False
    for svc in expected_services:
        instances = _resolve_all_instances(nodes, svc)
        ready_instances = [m for m in instances if m.ready]
        total_instances += len(instances)
        total_ready += len(ready_instances)
        if not instances:
            any_missing = True
            services[svc] = {
                "ready": False,
                "status": "unknown",
                "last_seen": "",
                "node_id": "",
            }
        else:
            for m in instances:
                key = f"{svc}" if len(instances) == 1 else f"{svc}.{m.node_id}"
                services[key] = {
                    "ready": m.ready,
                    "status": m.status,
                    "last_seen": m.last_seen,
                    "node_id": m.node_id,
                }

    all_ready = (
        registered_count > 0
        and not any_missing
        and total_instances >= registered_count
        and total_ready == total_instances
    )
    return {
        "all_ready": all_ready,
        "total": total_instances,
        "ready_count": total_ready,
        "registered_hosts": registered_count,
        "services": services,
    }


def check_fleet_staleness(
    state_dir: Path,
    expected_services: list[str],
    max_age_seconds: int = CONTAINER_READY_SECONDS,
) -> dict:
    """Detect services whose heartbeat was once active but has gone stale."""
    nodes = load_node_registry(state_dir)
    healthy: list[str] = []
    stale: list[dict] = []
    never_seen: list[str] = []

    for svc in expected_services:
        m = _resolve_service(nodes, svc, max_age_seconds)
        if not m.found:
            never_seen.append(svc)
        elif m.recent:
            healthy.append(svc)
        else:
            stale.append({
                "service": svc,
                "last_seen": m.last_seen,
                "node_id": m.node_id,
                "status": m.status,
            })

    return {
        "has_stale": len(stale) > 0,
        "healthy": healthy,
        "stale": stale,
        "never_seen": never_seen,
    }


# ── Metric history ───────────────────────────────────────────────────


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

    reporting = [n for n in nodes if n.status != "offline"]
    score = compute_health_score(
        online, len(nodes),
        [n.disk_usage_pct for n in reporting],
        [n.memory_usage_pct for n in reporting],
    )

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


def compute_health_score(
    online: int,
    total: int,
    disk_usages: list[float],
    memory_usages: list[float],
) -> int:
    """0-100 fleet health: availability (40%), disk (30%), memory (30%).

    Unified scorer used by both ``Fleet.health_score`` and
    ``compute_fleet_health``. Takes pre-filtered metric lists so callers
    handle their own "reporting vs offline" filtering.
    """
    if total == 0:
        return 100
    avail_score = (online / total) * 40

    disk_score = 0.0
    mem_score = 0.0
    if disk_usages:
        disk_score = sum(_resource_score(d) for d in disk_usages)
        disk_score = (disk_score / len(disk_usages)) * 30
    if memory_usages:
        mem_score = sum(_resource_score(m) for m in memory_usages)
        mem_score = (mem_score / len(memory_usages)) * 30

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
    """One cell in the service matrix: service x node."""

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

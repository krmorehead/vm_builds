"""On-demand heartbeat subscription system for real-time node metrics.

Clients (kiosk, web UI) subscribe to metrics for specific nodes. While
subscriptions are active, the server collects results via HTTP (callhome
command endpoint on port 9002) and caches them. Subscriptions expire after
a short TTL (default 30s) unless refreshed by the client.

HTTP calls are protected by a per-host circuit breaker that backs off
exponentially after consecutive failures.

No framework imports — pure data + HTTP.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta


# ── Data models ──────────────────────────────────────────────────────


@dataclass
class HeartbeatSubscription:
    """A single subscription to a node's metrics."""

    subscription_id: str
    node_id: str
    metric_type: str
    expires_at: datetime
    interval_seconds: float = 5.0


@dataclass
class HeartbeatCache:
    """Cached metric data from a single collection cycle."""

    node_id: str
    metric_type: str
    data: dict
    collected_at: str
    success: bool = True
    error: str = ""


# ── Subscription manager ────────────────────────────────────────────


class SubscriptionManager:
    """Thread-safe subscription lifecycle manager.

    Subscriptions have a TTL. Clients refresh them periodically while
    viewing a detail page. When the client navigates away, the subscription
    expires and the poller stops collecting for that node.
    """

    def __init__(self) -> None:
        self._subs: dict[str, HeartbeatSubscription] = {}
        self._lock = threading.Lock()

    def subscribe(
        self, node_id: str, metric_type: str, ttl_seconds: float = 30.0,
    ) -> HeartbeatSubscription:
        """Create or refresh a subscription. Returns the subscription."""
        with self._lock:
            existing = self._find(node_id, metric_type)
            if existing:
                existing.expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
                return existing
            sub = HeartbeatSubscription(
                subscription_id=uuid.uuid4().hex[:12],
                node_id=node_id,
                metric_type=metric_type,
                expires_at=datetime.now() + timedelta(seconds=ttl_seconds),
            )
            self._subs[sub.subscription_id] = sub
            return sub

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by ID. Returns True if it existed."""
        with self._lock:
            return self._subs.pop(subscription_id, None) is not None

    def get_active_nodes(self) -> list[tuple[str, str]]:
        """Return (node_id, metric_type) pairs with active subscriptions."""
        now = datetime.now()
        with self._lock:
            return list({
                (s.node_id, s.metric_type)
                for s in self._subs.values()
                if s.expires_at > now
            })

    def cleanup_expired(self) -> int:
        """Remove expired subscriptions. Returns count removed."""
        now = datetime.now()
        with self._lock:
            expired = [k for k, v in self._subs.items() if v.expires_at <= now]
            for k in expired:
                del self._subs[k]
            return len(expired)

    def is_subscribed(self, node_id: str, metric_type: str) -> bool:
        """Check if an active subscription exists."""
        now = datetime.now()
        with self._lock:
            return any(
                s.node_id == node_id
                and s.metric_type == metric_type
                and s.expires_at > now
                for s in self._subs.values()
            )

    def list_subscriptions(self) -> list[HeartbeatSubscription]:
        """Return all subscriptions (including expired, pre-cleanup)."""
        with self._lock:
            return list(self._subs.values())

    def _find(self, node_id: str, metric_type: str) -> HeartbeatSubscription | None:
        now = datetime.now()
        for s in self._subs.values():
            if s.node_id == node_id and s.metric_type == metric_type and s.expires_at > now:
                return s
        return None


# ── Metric cache ─────────────────────────────────────────────────────


class MetricCache:
    """Thread-safe in-memory cache of collected metrics."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], HeartbeatCache] = {}
        self._lock = threading.Lock()

    def store(self, entry: HeartbeatCache) -> None:
        with self._lock:
            self._cache[(entry.node_id, entry.metric_type)] = entry

    def get(self, node_id: str, metric_type: str) -> HeartbeatCache | None:
        with self._lock:
            return self._cache.get((node_id, metric_type))

    def clear(self, node_id: str | None = None) -> None:
        with self._lock:
            if node_id is None:
                self._cache.clear()
            else:
                keys = [k for k in self._cache if k[0] == node_id]
                for k in keys:
                    del self._cache[k]

    def all_entries(self) -> list[HeartbeatCache]:
        with self._lock:
            return list(self._cache.values())


# ── Per-host circuit breaker ─────────────────────────────────────────


@dataclass
class _CircuitState:
    """Per-host circuit breaker state for HTTP connections."""

    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    backoff_until: float = 0.0
    total_failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    _BASE_BACKOFF = 15.0
    _MAX_BACKOFF = 300.0

    def record_failure(self) -> None:
        with self.lock:
            self.consecutive_failures += 1
            self.total_failures += 1
            self.last_failure_time = time.monotonic()
            backoff = min(
                self._BASE_BACKOFF * (2 ** (self.consecutive_failures - 1)),
                self._MAX_BACKOFF,
            )
            self.backoff_until = self.last_failure_time + backoff

    def record_success(self) -> None:
        with self.lock:
            self.consecutive_failures = 0
            self.backoff_until = 0.0

    def is_open(self) -> bool:
        with self.lock:
            if self.consecutive_failures < 2:
                return False
            return time.monotonic() < self.backoff_until

    def backoff_remaining(self) -> float:
        with self.lock:
            return max(0.0, self.backoff_until - time.monotonic())


_circuit_breakers: dict[str, _CircuitState] = {}
_circuit_lock = threading.Lock()


def _get_circuit(ip: str) -> _CircuitState:
    with _circuit_lock:
        if ip not in _circuit_breakers:
            _circuit_breakers[ip] = _CircuitState()
        return _circuit_breakers[ip]


def get_circuit_status(ip: str) -> dict:
    """Return circuit breaker state for a host (for diagnostics)."""
    cb = _get_circuit(ip)
    with cb.lock:
        return {
            "consecutive_failures": cb.consecutive_failures,
            "total_failures": cb.total_failures,
            "backoff_remaining_s": max(0.0, cb.backoff_until - time.monotonic()),
            "is_open": cb.consecutive_failures >= 2 and time.monotonic() < cb.backoff_until,
        }


def reset_circuit(ip: str) -> None:
    """Manually reset a circuit breaker (e.g. after physical recovery)."""
    cb = _get_circuit(ip)
    cb.record_success()


# ── HTTP command execution ────────────────────────────────────────────

CALLHOME_CMD_PORT = 9002

OPENWRT_VMIDS: frozenset[int] = frozenset({100, 103, 104})


def cmd_path_for_vmid(vmid: int | None) -> str:
    """Return the HTTP path for the command endpoint based on container type."""
    if vmid is not None and vmid in OPENWRT_VMIDS:
        return "/cgi-bin/cmd"
    return "/cmd"


def _http_exec(
    container_ip: str, command: str, token: str = "",
    timeout: int = 10, vmid: int | None = None,
) -> tuple[bool, str]:
    """Execute a command on a container via its callhome HTTP endpoint.

    Synchronous counterpart to _callhome_exec in manager.py, designed for
    use in metric collectors that run in asyncio.to_thread().

    Debian containers use /cmd, OpenWrt containers use /cgi-bin/cmd.
    """
    import json as _json
    import urllib.request
    import urllib.error

    path = cmd_path_for_vmid(vmid)
    url = f"http://{container_ip}:{CALLHOME_CMD_PORT}{path}"
    payload = _json.dumps({"command": command, "token": token}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = _json.loads(resp.read().decode())
        if body.get("success"):
            return True, body.get("output", "")
        return False, body.get("error", body.get("output", "command failed"))
    except urllib.error.URLError as exc:
        return False, f"HTTP error: {exc}"
    except (TimeoutError, OSError) as exc:
        return False, f"Connection error: {exc}"
    except (_json.JSONDecodeError, ValueError, KeyError) as exc:
        return False, f"Response parse error: {exc}"


# ── Metric collectors ────────────────────────────────────────────────


def collect_wifi_metrics(ip: str, node_id: str = "", *, token: str = "", vmid: int | None = None) -> HeartbeatCache:
    """Collect WiFi interface and link metrics from an OpenWrt node via HTTP.

    Uses wifi_setup.sh (baked into mesh/bridge images) as the primary
    data source via the container's callhome HTTP command endpoint.
    Falls back to raw iw/uci commands for nodes without wifi_setup.sh.
    """
    now = datetime.now().isoformat(timespec="seconds")
    data: dict = {
        "interfaces": [],
        "stations": [],
        "radio": {},
    }
    _exec = lambda cmd, t=10: _http_exec(ip, cmd, token, t, vmid)

    ok, metrics_out = _exec("/usr/sbin/wifi_setup.sh metrics 2>/dev/null")
    if ok and "PHY=" in metrics_out:
        data["script_status"] = _parse_key_value(metrics_out)
        data["raw_metrics"] = metrics_out[:2000]
        data["interfaces"] = _parse_iw_dev(metrics_out)
        station_section = ""
        if "---STATION_DUMP---" in metrics_out:
            station_section = metrics_out.split("---STATION_DUMP---", 1)[1]
            if "---BRIDGE---" in station_section:
                station_section = station_section.split("---BRIDGE---", 1)[0]
        data["stations"] = _parse_station_dump(station_section)
        return HeartbeatCache(
            node_id="", metric_type="wifi", data=data,
            collected_at=now, success=True,
        )

    ok, iw_dev = _exec("iw dev")
    if ok:
        data["interfaces"] = _parse_iw_dev(iw_dev)
    else:
        return HeartbeatCache(
            node_id="", metric_type="wifi", data=data,
            collected_at=now, success=False, error=iw_dev,
        )

    ok, station_dump = _exec(
        "iw dev 2>/dev/null | awk '/Interface/{print $2}' | "
        "while read iface; do echo \"=== $iface ===\"; "
        "iw dev $iface station dump 2>/dev/null; done",
    )
    if ok:
        data["stations"] = _parse_station_dump(station_dump)

    ok, uci_out = _exec("uci show wireless 2>/dev/null | head -30")
    if ok:
        data["radio"] = _parse_uci_wireless(uci_out)

    return HeartbeatCache(
        node_id="", metric_type="wifi", data=data,
        collected_at=now, success=True,
    )


def collect_bridge_metrics(ip: str, node_id: str = "", *, token: str = "", vmid: int | None = None) -> HeartbeatCache:
    """Collect WiFi bridge metrics (superset of wifi metrics) via HTTP.

    When wifi_setup.sh is available, all data (interfaces, stations,
    bridge, STP) comes from the script's metrics output via the container's
    HTTP command endpoint.
    """
    wifi = collect_wifi_metrics(ip, node_id, token=token, vmid=vmid)
    now = datetime.now().isoformat(timespec="seconds")
    _exec = lambda cmd, t=10: _http_exec(ip, cmd, token, t, vmid)

    bridge_data = dict(wifi.data)
    bridge_data["bridge"] = {}

    raw_metrics = bridge_data.get("raw_metrics", "")
    if "---BRIDGE---" in raw_metrics:
        bridge_section = raw_metrics.split("---BRIDGE---", 1)[1]
        bridge_data["bridge"]["interfaces"] = _parse_brctl(bridge_section)
        bridge_data["bridge"]["stp"] = bridge_section[:500]
    else:
        ok, brctl_out = _exec("brctl show 2>/dev/null || echo 'no-brctl'")
        if ok:
            bridge_data["bridge"]["interfaces"] = _parse_brctl(brctl_out)
        ok, stp_out = _exec("brctl showstp br-lan 2>/dev/null || echo 'no-stp'")
        if ok:
            bridge_data["bridge"]["stp"] = stp_out[:500]

    role = infer_wifi_role(
        bridge_data.get("script_status", {}),
        bridge_data.get("interfaces", []),
    )
    bridge_data["bridge"]["role"] = role
    bridge_data["bridge"]["paired"] = len(bridge_data.get("stations", [])) > 0

    return HeartbeatCache(
        node_id="", metric_type="bridge", data=bridge_data,
        collected_at=now, success=wifi.success, error=wifi.error,
    )


def collect_router_metrics(ip: str, node_id: str = "", *, token: str = "", vmid: int | None = None) -> HeartbeatCache:
    """Collect router-level metrics from an OpenWrt node via HTTP."""
    now = datetime.now().isoformat(timespec="seconds")
    data: dict = {}
    _exec = lambda cmd, t=10: _http_exec(ip, cmd, token, t, vmid)

    ok, wan_out = _exec(
        "uci get network.wan.proto 2>/dev/null; "
        "ifstatus wan 2>/dev/null | jsonfilter "
        "-e '@[\"ipv4-address\"][0].address' -e '@[\"up\"]' -e '@.uptime'",
    )
    if ok:
        lines = wan_out.strip().splitlines()
        data["wan"] = {
            "proto": lines[0] if len(lines) > 0 else "",
            "ip": lines[1] if len(lines) > 1 else "",
            "up": lines[2] if len(lines) > 2 else "",
            "uptime": lines[3] if len(lines) > 3 else "",
        }

    ok, lan_out = _exec(
        "uci get network.lan.ipaddr 2>/dev/null; "
        "uci get network.lan.netmask 2>/dev/null; "
        "uci get dhcp.lan.start 2>/dev/null; "
        "uci get dhcp.lan.limit 2>/dev/null",
    )
    if ok:
        lines = lan_out.strip().splitlines()
        data["lan"] = {
            "ip": lines[0] if len(lines) > 0 else "",
            "netmask": lines[1] if len(lines) > 1 else "",
            "dhcp_start": lines[2] if len(lines) > 2 else "",
            "dhcp_limit": lines[3] if len(lines) > 3 else "",
        }

    ok, lease_out = _exec("cat /tmp/dhcp.leases 2>/dev/null | wc -l")
    if ok:
        data["dhcp_lease_count"] = int(lease_out.strip() or "0")

    ok, lease_detail = _exec("cat /tmp/dhcp.leases 2>/dev/null | head -20")
    if ok:
        data["dhcp_leases"] = _parse_dhcp_leases(lease_detail)

    ok, fw_out = _exec(
        "fw4 -q zone 2>/dev/null || "
        "uci show firewall 2>/dev/null | grep '\\.name=' | head -10",
    )
    if ok:
        data["firewall_zones"] = fw_out[:500]

    ok, sys_out = _exec(
        "uptime; free 2>/dev/null || cat /proc/meminfo | head -3; "
        "df / 2>/dev/null | tail -1",
    )
    if ok:
        data["system"] = _parse_system_info(sys_out)

    ok, client_out = _exec("cat /proc/net/arp 2>/dev/null | tail -n +2 | wc -l")
    if ok:
        data["arp_client_count"] = int(client_out.strip() or "0")

    return HeartbeatCache(
        node_id="", metric_type="router", data=data,
        collected_at=now, success=True,
    )


def collect_mesh_metrics(ip: str, node_id: str = "", *, token: str = "", vmid: int | None = None) -> HeartbeatCache:
    """Collect mesh network metrics (WiFi + peer info) via HTTP.

    Role detection uses wifi_setup.sh when available (baked into the
    mesh/bridge image). Falls back to iw interface type parsing.
    """
    wifi = collect_wifi_metrics(ip, node_id, token=token, vmid=vmid)
    now = datetime.now().isoformat(timespec="seconds")
    mesh_data = dict(wifi.data)

    mesh_data["peers"] = []
    for station in mesh_data.get("stations", []):
        mesh_data["peers"].append({
            "mac": station.get("mac", ""),
            "signal": station.get("signal", ""),
            "rx_bitrate": station.get("rx_bitrate", ""),
            "tx_bitrate": station.get("tx_bitrate", ""),
            "connected_time": station.get("connected_time", ""),
        })

    role = infer_wifi_role(
        mesh_data.get("script_status", {}),
        mesh_data.get("interfaces", []),
    )
    mesh_data["role"] = role

    return HeartbeatCache(
        node_id="", metric_type="mesh", data=mesh_data,
        collected_at=now, success=wifi.success, error=wifi.error,
    )


# ── Shared helpers ───────────────────────────────────────────────────


def infer_wifi_role(
    script_status: dict, interfaces: list[dict],
) -> str:
    """Determine WiFi role from script status or interface modes.

    Used by both ``collect_bridge_metrics`` and ``collect_mesh_metrics``
    to avoid duplicating the same role-inference logic.
    """
    if "mode" in script_status:
        return script_status["mode"]
    for iface in interfaces:
        mode = iface.get("type", "")
        if mode == "AP":
            return "ap"
        if mode in ("managed", "station"):
            return "sta"
    return "unknown"


def parse_guest_list(stdout: str) -> list[dict]:
    """Parse ``pct list`` or ``qm list`` output into structured dicts.

    ``pct list`` columns: VMID  Status  Lock  Name
    ``qm list``  columns: VMID  NAME  STATUS  MEM  BOOTDISK  PID

    Detects format from the header row and maps fields accordingly.
    Returns ``[{"vmid": ..., "status": ..., "name": ...}, ...]``.
    """
    lines = stdout.strip().splitlines()
    if not lines:
        return []
    header = lines[0].upper().split()
    qm_format = len(header) >= 2 and header[1] == "NAME"
    guests: list[dict] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            if qm_format:
                guests.append({
                    "vmid": parts[0],
                    "name": parts[1],
                    "status": parts[2],
                })
            else:
                guests.append({
                    "vmid": parts[0],
                    "status": parts[1],
                    "name": parts[2],
                })
    return guests


# ── Parser helpers ───────────────────────────────────────────────────


def _parse_key_value(output: str) -> dict[str, str]:
    """Parse KEY=value output from container-side scripts."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line and not line.startswith("---"):
            k, _, v = line.partition("=")
            key = k.strip().lower()
            if key:
                result[key] = v.strip()
    return result


def _parse_iw_dev(output: str) -> list[dict]:
    """Parse `iw dev` output into a list of interface dicts."""
    interfaces: list[dict] = []
    current: dict = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Interface"):
            if current:
                interfaces.append(current)
            current = {"name": line.split()[-1]}
        elif line.startswith("addr"):
            current["addr"] = line.split()[-1]
        elif line.startswith("ssid"):
            current["ssid"] = line.split(None, 1)[-1]
        elif line.startswith("type"):
            current["type"] = line.split()[-1]
        elif line.startswith("channel"):
            current["channel"] = line.split(None, 1)[-1]
        elif line.startswith("txpower"):
            current["txpower"] = line.split()[-2]
    if current:
        interfaces.append(current)
    return interfaces


def _parse_station_dump(output: str) -> list[dict]:
    """Parse combined `iw dev <iface> station dump` output."""
    stations: list[dict] = []
    current: dict = {}
    current_iface = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("==="):
            current_iface = line.strip("= ")
            continue
        if line.startswith("Station"):
            if current:
                stations.append(current)
            parts = line.split()
            current = {
                "mac": parts[1] if len(parts) > 1 else "",
                "interface": current_iface,
            }
        elif "signal:" in line:
            m = re.search(r"signal:\s*(-?\d+)", line)
            if m:
                current["signal"] = m.group(1)
        elif "signal avg:" in line:
            m = re.search(r"signal avg:\s*(-?\d+)", line)
            if m:
                current["signal_avg"] = m.group(1)
        elif "tx bitrate:" in line:
            current["tx_bitrate"] = line.split(":", 1)[-1].strip()
        elif "rx bitrate:" in line:
            current["rx_bitrate"] = line.split(":", 1)[-1].strip()
        elif "rx bytes:" in line:
            m = re.search(r"rx bytes:\s*(\d+)", line)
            if m:
                current["rx_bytes"] = int(m.group(1))
        elif "tx bytes:" in line:
            m = re.search(r"tx bytes:\s*(\d+)", line)
            if m:
                current["tx_bytes"] = int(m.group(1))
        elif "rx packets:" in line:
            m = re.search(r"rx packets:\s*(\d+)", line)
            if m:
                current["rx_packets"] = int(m.group(1))
        elif "tx packets:" in line:
            m = re.search(r"tx packets:\s*(\d+)", line)
            if m:
                current["tx_packets"] = int(m.group(1))
        elif "tx retries:" in line:
            m = re.search(r"tx retries:\s*(\d+)", line)
            if m:
                current["tx_retries"] = int(m.group(1))
        elif "tx failed:" in line:
            m = re.search(r"tx failed:\s*(\d+)", line)
            if m:
                current["tx_failed"] = int(m.group(1))
        elif "connected time:" in line:
            m = re.search(r"connected time:\s*(\d+)", line)
            if m:
                current["connected_time"] = int(m.group(1))
    if current and current.get("mac"):
        stations.append(current)
    return stations


def _parse_uci_wireless(output: str) -> dict:
    """Parse `uci show wireless` into key-value pairs."""
    result: dict = {}
    for line in output.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            short_key = key.split(".")[-1] if "." in key else key
            result[short_key] = val.strip("'\"")
    return result


def _parse_brctl(output: str) -> list[dict]:
    """Parse `brctl show` output."""
    bridges: list[dict] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        if len(parts) >= 3:
            bridges.append({
                "name": parts[0],
                "id": parts[1] if len(parts) > 1 else "",
                "stp": parts[2] if len(parts) > 2 else "",
                "interfaces": parts[3:],
            })
        elif bridges and len(parts) == 1:
            bridges[-1].setdefault("interfaces", []).append(parts[0])
    return bridges


def _parse_dhcp_leases(output: str) -> list[dict]:
    """Parse /tmp/dhcp.leases format: timestamp MAC IP hostname clientid."""
    leases: list[dict] = []
    for line in output.strip().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            leases.append({
                "expires": parts[0],
                "mac": parts[1],
                "ip": parts[2],
                "hostname": parts[3],
            })
    return leases


def _parse_system_info(output: str) -> dict:
    """Parse combined uptime/free/df output."""
    info: dict = {}
    lines = output.strip().splitlines()
    for line in lines:
        if "load average" in line:
            m = re.search(r"up\s+(.+?),\s+\d+ user", line)
            if m:
                info["uptime_str"] = m.group(1).strip()
            m = re.search(r"load average:\s*(.+)", line)
            if m:
                info["load"] = m.group(1).strip()
        elif "MemTotal" in line:
            m = re.search(r"MemTotal:\s+(\d+)", line)
            if m:
                info["mem_total_kb"] = int(m.group(1))
        elif "MemAvailable" in line or "MemFree" in line:
            m = re.search(r"Mem\w+:\s+(\d+)", line)
            if m:
                info["mem_avail_kb"] = int(m.group(1))
        elif "%" in line and "/" in line:
            parts = line.split()
            if len(parts) >= 5:
                info["disk_usage"] = parts[4].rstrip("%")
    return info


# ── Signal quality classification ────────────────────────────────────


def signal_quality(dbm: int) -> str:
    """Classify signal strength in dBm to a quality label."""
    if dbm >= -50:
        return "excellent"
    if dbm >= -60:
        return "good"
    if dbm >= -70:
        return "fair"
    if dbm >= -80:
        return "weak"
    return "poor"


def signal_percentage(dbm: int) -> int:
    """Convert dBm to a 0-100 percentage for display."""
    clamped = max(-90, min(-30, dbm))
    return round(((clamped + 90) / 60) * 100)


# ── Batman-adv metrics collector ─────────────────────────────────────


def _parse_batman_originators(output: str) -> list[dict]:
    """Parse `batctl o` originator table.

    Example line:
      * aa:bb:cc:dd:ee:ff    0.904s   (254) cc:dd:ee:ff:00:11 [  wlan0]
    """
    originators = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("No batman"):
            continue
        m = re.match(
            r"\*?\s*([0-9a-fA-F:]+)\s+"
            r"([0-9.]+)s\s+"
            r"\(\s*(\d+)\)\s+"
            r"([0-9a-fA-F:]+)\s+"
            r"\[\s*(\S+)\]",
            line,
        )
        if m:
            originators.append({
                "mac": m.group(1),
                "last_seen": m.group(2),
                "tq": int(m.group(3)),
                "next_hop": m.group(4),
                "interface": m.group(5),
            })
    return originators


def _parse_batman_interfaces(output: str) -> list[dict]:
    """Parse `batctl if` interface list.

    Example line:
      wlan0: active
    """
    interfaces = []
    for line in output.strip().splitlines():
        line = line.strip()
        if ":" in line:
            parts = line.split(":", 1)
            interfaces.append({
                "name": parts[0].strip(),
                "status": parts[1].strip(),
            })
    return interfaces


def collect_batman_metrics(ip: str, node_id: str = "", *, token: str = "", vmid: int | None = None) -> HeartbeatCache:
    """Collect batman-adv status from a node via HTTP command endpoint.

    batman_trigger.sh runs inside the container. The command endpoint
    executes it and returns the output.
    """
    now = datetime.now().isoformat(timespec="seconds")
    ok, raw = _http_exec(ip, "/usr/sbin/batman_trigger.sh status", token, 10, vmid)
    if not ok:
        return HeartbeatCache(
            node_id="", metric_type="batman",
            data={}, collected_at=now,
            success=False, error=raw[:200],
        )

    active = "BATMAN=active" in raw
    originators = _parse_batman_originators(raw)
    interfaces = _parse_batman_interfaces(
        raw.split("---INTERFACES---")[1] if "---INTERFACES---" in raw else ""
    )

    return HeartbeatCache(
        node_id="", metric_type="batman",
        data={
            "active": active,
            "originators": originators,
            "interfaces": interfaces,
        },
        collected_at=now,
    )

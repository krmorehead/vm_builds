#!/usr/bin/env python3
"""Call-home heartbeat agent for vm_builds managed nodes.

Two modes:
  Host mode    — contacts the server when the node's IP changes (cron).
  Container mode (--container) — heartbeats every --interval seconds
      regardless of IP changes. Reports systemd services, listening
      ports, and extension metrics (WiFi, WireGuard, Docker, etc.).

Baked into LXC images via build-images.sh inject_callhome_agent().
Configured at deploy time by writing CALLHOME_SERVER to
/etc/default/callhome. Zero external dependencies — stdlib only.

Usage:
    python3 callhome.py --once              # check once and exit
    python3 callhome.py --container         # container heartbeat loop
    python3 callhome.py --interval 60       # host poll loop

Environment (or /etc/default/callhome):
    CALLHOME_SERVER      Management server URL
    CALLHOME_PUBLIC_KEY  Auth token (the "public key" given to nodes)
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import typing
import urllib.error
import urllib.request

CONF_FILE = "/etc/default/callhome"
DEFAULT_STATE_FILE = (
    "/var/lib/callhome/last_ip" if os.getuid() == 0
    else os.path.expanduser("~/.callhome_last_ip")
)


def _load_conf() -> None:
    """Source /etc/default/callhome into environment if it exists."""
    if not os.path.exists(CONF_FILE):
        return
    try:
        with open(CONF_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    val = val.strip().strip("'\"")
                    os.environ.setdefault(key.strip(), val)
    except OSError:
        pass


# ── IP tracking ──────────────────────────────────────────────────────


def get_primary_ip() -> str:
    """Get the primary non-loopback IPv4 address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for ip in result.stdout.strip().split():
                if ":" not in ip:
                    return ip
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def read_last_ip(state_file: str = "") -> str:
    """Read the IP that was last successfully sent home."""
    sf = state_file or DEFAULT_STATE_FILE
    try:
        with open(sf) as f:
            return f.read().strip()
    except OSError:
        return ""


def save_last_ip(ip: str, state_file: str = "") -> None:
    """Persist the IP after a successful check-in."""
    sf = state_file or DEFAULT_STATE_FILE
    state_dir = os.path.dirname(sf)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(sf, "w") as f:
        f.write(ip)


def ip_changed(state_file: str = "") -> tuple[bool, str]:
    """Return (changed, current_ip). Changed is True on first run too."""
    current = get_primary_ip()
    if not current:
        return False, ""
    sf = state_file or DEFAULT_STATE_FILE
    last = read_last_ip(sf)
    return current != last, current


# ── System metrics ───────────────────────────────────────────────────


def get_local_ips() -> list[str]:
    ips: list[str] = []
    try:
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ips = [ip for ip in result.stdout.strip().split() if ":" not in ip]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ips


def get_uptime() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def get_disk_usage() -> float:
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        if total == 0:
            return 0.0
        return round((1 - free / total) * 100, 1)
    except OSError:
        return 0.0


def get_memory_usage() -> float:
    try:
        with open("/proc/meminfo") as f:
            info: dict[str, int] = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            if total == 0:
                return 0.0
            return round((1 - avail / total) * 100, 1)
    except (OSError, ValueError, KeyError):
        return 0.0


def get_running_services() -> list[str]:
    services: list[str] = []
    for tool, kind in [("qm", "vm"), ("pct", "ct")]:
        try:
            result = subprocess.run(
                [tool, "list"], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "running":
                        name = parts[2] if len(parts) > 2 else parts[0]
                        services.append(f"{kind}:{parts[0]}:{name}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return services


# ── Container-mode health ─────────────────────────────────────────


def get_systemd_services() -> dict[str, str]:
    """Return a map of service_name -> active/inactive/failed for key units."""
    result_map: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all",
             "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    unit = parts[0].removesuffix(".service")
                    state = parts[3]
                    result_map[unit] = state
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return result_map


def _parse_proc_net_ports(path: str, listen_state: str = "0A") -> list[int]:
    """Parse listening ports from /proc/net/tcp or /proc/net/udp.

    TCP uses state 0A (LISTEN). UDP uses state 07 (CLOSE) which means
    the socket is bound and ready to receive — UDP has no LISTEN state.
    """
    ports: list[int] = []
    try:
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 4 or parts[0] == "sl":
                    continue
                if parts[3] == listen_state:
                    hex_port = parts[1].split(":")[1]
                    ports.append(int(hex_port, 16))
    except OSError:
        pass
    return ports


def get_listening_ports() -> list[int]:
    """Read listening TCP and UDP ports from /proc/net/{tcp,udp}."""
    tcp = _parse_proc_net_ports("/proc/net/tcp", "0A")
    udp = _parse_proc_net_ports("/proc/net/udp", "07")
    return sorted(set(tcp + udp))


def collect_network() -> dict | None:
    """Network interfaces and default gateway (stdlib-only).

    Returns None when no interfaces are found, matching the collector
    contract (None = skip this extension).
    """
    interfaces = []
    try:
        for iface in sorted(os.listdir("/sys/class/net/")):
            if iface == "lo":
                continue
            entry: dict = {"name": iface, "addresses": [], "operstate": "unknown"}
            try:
                with open(f"/sys/class/net/{iface}/operstate") as f:
                    entry["operstate"] = f.read().strip()
            except OSError:
                pass
            try:
                with open(f"/sys/class/net/{iface}/address") as f:
                    entry["mac"] = f.read().strip()
            except OSError:
                pass
            interfaces.append(entry)
    except OSError:
        pass

    # Parse IPs from ip command if available
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    iface_name = parts[1]
                    addr_cidr = parts[3]
                    for iface in interfaces:
                        if iface["name"] == iface_name:
                            iface["addresses"].append(addr_cidr)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    gateway = ""
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    gw_hex = parts[2]
                    gw_bytes = bytes.fromhex(gw_hex)
                    gateway = f"{gw_bytes[3]}.{gw_bytes[2]}.{gw_bytes[1]}.{gw_bytes[0]}"
                    break
    except OSError:
        pass

    if not interfaces:
        return None
    return {"interfaces": interfaces, "default_gateway": gateway}


def collect_wireguard() -> dict | None:
    """WireGuard interface status and peer count (None if wg not present)."""
    try:
        result = subprocess.run(
            ["wg", "show", "all", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    interfaces: dict[str, dict] = {}
    for line in result.stdout.strip().splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        iface = cols[0]
        if iface not in interfaces:
            interfaces[iface] = {"peer_count": 0, "up": True}
        else:
            interfaces[iface]["peer_count"] += 1

    if not interfaces:
        return None
    return {"interfaces": interfaces}


def collect_docker() -> dict | None:
    """Docker daemon status and running container count (None if no docker)."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ContainersRunning}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"active": False, "running": 0}
        running = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        return {"active": True, "running": running}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def collect_config_files() -> dict | None:
    """Report keys and hash of allow-listed config files.

    Set CALLHOME_CONFIG_FILES=/path/one.json:/path/two.toml to enable.
    Each file's content hash is reported. Top-level keys are extracted
    for JSON files; non-JSON files report the hash only.
    """
    paths_str = os.environ.get("CALLHOME_CONFIG_FILES", "")
    if not paths_str:
        return None
    result: dict[str, dict] = {}
    for path in paths_str.split(":"):
        path = path.strip()
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                raw = f.read()
            content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
            entry: dict = {"hash": content_hash}
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    entry["keys"] = sorted(data.keys())
            except (json.JSONDecodeError, TypeError):
                pass
            result[path] = entry
        except OSError:
            pass
    return result if result else None


def collect_http_probes() -> dict | None:
    """Probe local HTTP endpoints and report status codes.

    Set CALLHOME_HTTP_PROBES=http://127.0.0.1:8096,http://127.0.0.1:19999
    Each URL is probed with a 5-second timeout. Result maps URL to status
    code (0 for connection refused/timeout).
    """
    probes_str = os.environ.get("CALLHOME_HTTP_PROBES", "")
    if not probes_str:
        return None
    result: dict[str, int] = {}
    for url in probes_str.split(","):
        url = url.strip()
        if not url:
            continue
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                result[url] = resp.status
        except urllib.error.HTTPError as e:
            result[url] = e.code
        except (urllib.error.URLError, OSError, TimeoutError):
            result[url] = 0
    return result if result else None


class CollectorRegistry:
    """Auto-discovers and runs health extension collectors.

    Each collector returns ``dict | None``. Collectors that return
    ``None`` (prerequisites missing) are silently omitted.  The
    ``network`` collector uses a special non-empty check on
    ``interfaces`` instead of the ``None`` gate.
    """

    def __init__(self) -> None:
        self._collectors: list[tuple[str, typing.Callable[[], dict | None]]] = []

    def register(
        self, name: str, fn: typing.Callable[[], dict | None],
    ) -> None:
        self._collectors.append((name, fn))

    def collect_all(self) -> dict[str, dict]:
        ext: dict[str, dict] = {}
        for name, fn in self._collectors:
            try:
                result = fn()
            except Exception as exc:
                print(
                    f"[callhome] collector '{name}' failed: {exc}",
                    file=sys.stderr, flush=True,
                )
                continue
            if result is None:
                continue
            ext[name] = result
        return ext


# Module-level registry populated at import time.
_registry = CollectorRegistry()
_registry.register("network", collect_network)
_registry.register("wireguard", collect_wireguard)
_registry.register("docker", collect_docker)
_registry.register("config_files", collect_config_files)
_registry.register("http_probes", collect_http_probes)


def collect_extensions() -> dict[str, dict]:
    """Run all registered collectors and return extensions dict."""
    return _registry.collect_all()


_IMAGE_VERSION_PATH = "/etc/image_version"


def get_image_version(path: str | None = None) -> str:
    """Read the image version baked in at build time."""
    target = path or _IMAGE_VERSION_PATH
    try:
        with open(target) as f:
            return f.read().strip()
    except OSError:
        return ""


def build_container_payload(container_id: str = "") -> dict:
    """Build a heartbeat payload for container mode."""
    cid = container_id or socket.gethostname()
    systemd_svcs = get_systemd_services()
    ports = get_listening_ports()
    extensions = collect_extensions()
    return {
        "node_id": socket.getfqdn(),
        "hostname": socket.gethostname(),
        "local_ips": get_local_ips(),
        "uptime_seconds": get_uptime(),
        "services": [],
        "disk_usage_pct": get_disk_usage(),
        "memory_usage_pct": get_memory_usage(),
        "version": get_version(),
        "container_health": {
            "container_id": cid,
            "systemd_services": systemd_svcs,
            "listening_ports": ports,
            "ready": True,
            "extensions": extensions,
            "image_version": get_image_version(),
        },
    }


def get_version() -> str:
    """Read project_version from inventory/group_vars/all.yml if available."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        all_yml = os.path.join(project_root, "inventory", "group_vars", "all.yml")
        with open(all_yml) as f:
            for line in f:
                if line.strip().startswith("project_version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except (OSError, IndexError, ValueError):
        pass
    return ""


def build_payload() -> dict:
    hostname = os.environ.get("CALLHOME_HOSTNAME") or socket.gethostname()
    return {
        "node_id": socket.getfqdn(),
        "hostname": hostname,
        "local_ips": get_local_ips(),
        "uptime_seconds": get_uptime(),
        "services": get_running_services(),
        "disk_usage_pct": get_disk_usage(),
        "memory_usage_pct": get_memory_usage(),
        "version": get_version(),
    }


# ── Network ──────────────────────────────────────────────────────────


def send_checkin(server_url: str, payload: dict, token: str = "") -> bool:
    """POST heartbeat to the management server. Returns True on success."""
    url = f"{server_url.rstrip('/')}/api/checkin"
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Callhome-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ── Main loop ────────────────────────────────────────────────────────


def run_once(
    server_url: str, token: str, force: bool = False,
    state_file: str = "", container_id: str = "",
) -> bool:
    """Check IP, send if changed. Returns True if check-in was sent and succeeded."""
    sf = state_file or DEFAULT_STATE_FILE
    if not force and not container_id:
        changed, current_ip = ip_changed(sf)
        if not changed:
            return False

    payload = (
        build_container_payload(container_id)
        if container_id else build_payload()
    )
    if send_checkin(server_url, payload, token=token):
        current_ip = get_primary_ip()
        if current_ip:
            save_last_ip(current_ip, sf)
        print(f"[callhome] checked in to {server_url} (ip={current_ip})", flush=True)
        return True

    print(f"[callhome] failed to reach {server_url}", flush=True)
    return False


def _compute_state_hash(container_id: str) -> str:
    """Hash the current service + port state for change detection."""
    svcs = get_systemd_services() if container_id else {}
    ports = get_listening_ports() if container_id else []
    raw = json.dumps({"s": svcs, "p": ports}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def run_loop(
    server_url: str, token: str, interval: int,
    force: bool = False, state_file: str = "",
    container_id: str = "", interval_startup: int = 0,
) -> None:
    """Periodic heartbeat loop with state-change detection.

    In container mode, always sends (no IP-change gate).
    With interval_startup, uses a faster rate for the first 60s.
    Between heartbeats, polls service state every 5s. If a state change
    is detected (service crashed, new port opened), sends immediately
    instead of waiting for the next interval.
    """
    start = time.monotonic()
    startup_window = 60.0
    first = True
    last_state_hash = ""
    state_poll_interval = 5

    while True:
        try:
            elapsed = time.monotonic() - start
            is_startup = interval_startup > 0 and elapsed < startup_window
            current_interval = interval_startup if is_startup else interval

            run_once(
                server_url, token,
                force=(force and first) or bool(container_id),
                state_file=state_file, container_id=container_id,
            )
            first = False

            if container_id:
                last_state_hash = _compute_state_hash(container_id)

            slept = 0.0
            while slept < current_interval:
                chunk = min(state_poll_interval, current_interval - slept)
                time.sleep(chunk)
                slept += chunk

                if container_id and slept < current_interval:
                    try:
                        current_hash = _compute_state_hash(container_id)
                    except Exception:
                        continue
                    if current_hash != last_state_hash:
                        print(
                            "[callhome] state change detected, sending immediately",
                            flush=True,
                        )
                        break
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[callhome] loop error: {exc}", file=sys.stderr, flush=True)
            time.sleep(interval)


# ── Command HTTP endpoint ─────────────────────────────────────────────
#
# A lightweight HTTP server that accepts POST /cmd from the NodeManager.
# Only whitelisted commands are allowed.  HMAC-authenticated using the
# same CALLHOME_PUBLIC_KEY that the heartbeat uses.

COMMAND_PORT = 9002

ALLOWED_COMMANDS: list[str] = [
    # Container-side service scripts
    "/usr/sbin/batman_trigger.sh",
    "/usr/sbin/wifi_setup.sh",
    "/usr/sbin/wireguard-firewall-setup",
    "/usr/sbin/switch-desktop-session",
    "/usr/sbin/rsyslogd",
    "/usr/local/bin/pihole",
    "/usr/bin/pihole-FTL",
    "/usr/bin/sunshine",
    # Runtime operations
    "docker",
    "echo",
    "hostname",
    "iptables",
    "passwd",
    "systemctl",
    "cat",
    "curl",
    "ip",
    "rm",
    "sed",
    "sh -c",
    "bash -c",
    # Package managers (image builds via NM API)
    "apt-get",
    "apt-cache",
    "dpkg",
    "dnf",
    "rpm",
    "pip",
    "pip3",
    # File and archive tools
    "wget",
    "tar",
    "gzip",
    "zstd",
    "cp",
    "mv",
    "ln",
    "mkdir",
    "chmod",
    "chown",
    # User/group management
    "useradd",
    "groupadd",
    "usermod",
    "loginctl",
    # Build tools
    "cmake",
    "make",
    "git",
    "python3",
    "getent",
    "tee",
    "test",
    "id",
]


def _is_command_allowed(cmd: str) -> bool:
    """Check if a command matches the whitelist."""
    stripped = cmd.strip()
    for prefix in ALLOWED_COMMANDS:
        if stripped == prefix or stripped.startswith(prefix + " "):
            return True
    return False


class CommandHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the command endpoint (POST /cmd)."""

    auth_token: str = ""

    def log_message(self, format: str, *args: typing.Any) -> None:
        print(f"[callhome-cmd] {format % args}", flush=True)

    def do_POST(self) -> None:
        if self.path != "/cmd":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 65536:
            self.send_error(413, "Payload too large")
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, ValueError):
            self.send_error(400, "Invalid JSON")
            return

        token = body.get("token", "")
        if self.auth_token and token != self.auth_token:
            self._json_response(403, {"success": False, "error": "Unauthorized"})
            return

        cmd = body.get("command", "").strip()
        if not cmd:
            self._json_response(400, {"success": False, "error": "command required"})
            return

        if not _is_command_allowed(cmd):
            self._json_response(
                403, {"success": False, "error": f"Command not whitelisted: {cmd.split()[0]}"},
            )
            return

        timeout = min(int(body.get("timeout", 30)), 900)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout,
            )
            self._json_response(200, {
                "success": result.returncode == 0,
                "output": (result.stdout + result.stderr)[:4000],
                "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            self._json_response(504, {
                "success": False, "error": f"Command timed out after {timeout}s",
            })
        except OSError as exc:
            self._json_response(500, {
                "success": False, "error": str(exc)[:300],
            })

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response(200, {"status": "ok", "port": COMMAND_PORT})
            return
        self.send_error(404, "Not Found")

    def _json_response(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_command_server(port: int, token: str) -> threading.Thread:
    """Start the command HTTP server in a background thread."""
    CommandHandler.auth_token = token
    server = http.server.HTTPServer(("0.0.0.0", port), CommandHandler)
    server.timeout = 1
    thread = threading.Thread(
        target=server.serve_forever, daemon=True, name="callhome-cmd",
    )
    thread.start()
    print(f"[callhome] command server listening on :{port}", flush=True)
    return thread


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="vm_builds call-home client")
    parser.add_argument(
        "--server",
        default=os.environ.get("CALLHOME_SERVER", ""),
        help="Management server URL (or set CALLHOME_SERVER)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Auth token (INSECURE: visible in /proc — prefer CALLHOME_PUBLIC_KEY env var)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("CALLHOME_INTERVAL", "60")),
        help="Heartbeat interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send even if IP has not changed",
    )
    parser.add_argument(
        "--container",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="Container mode: report systemd/port health instead of qm/pct. "
             "Optional ID arg sets container_id (default: hostname).",
    )
    parser.add_argument(
        "--interval-startup",
        type=int,
        default=0,
        metavar="SECS",
        help="Faster heartbeat interval for the first 60s after start (default: off)",
    )
    parser.add_argument(
        "--command-port",
        type=int,
        default=int(os.environ.get("CALLHOME_COMMAND_PORT", "0")),
        help="Port for HTTP command endpoint (0 = disabled, default: 9002 in container mode)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    _load_conf()
    args = parse_args(argv)

    if args.token is not None:
        token = args.token
        print(
            "[callhome] WARNING: --token on command line is visible in "
            "/proc/<pid>/cmdline. Prefer CALLHOME_PUBLIC_KEY env var or "
            "/etc/default/callhome.",
            file=sys.stderr,
        )
    else:
        token = os.environ.get("CALLHOME_PUBLIC_KEY", "")

    if not token:
        print("[callhome] WARNING: no auth token set", file=sys.stderr)

    is_container = args.container is not None
    container_id = (args.container or socket.gethostname()) if is_container else ""
    mode_label = f"container={container_id}" if is_container else "host"

    cmd_port = args.command_port
    if cmd_port == 0 and is_container:
        cmd_port = COMMAND_PORT
    if cmd_port > 0:
        try:
            start_command_server(cmd_port, token)
        except OSError as exc:
            print(f"[callhome] WARNING: command server failed to start on :{cmd_port}: {exc}",
                  file=sys.stderr, flush=True)

    if not args.server:
        # No server configured — run command server only (build container mode).
        # The heartbeat loop requires a server URL, but the /cmd endpoint
        # is available for receiving commands from the NodeManager.
        print(f"[callhome] mode={mode_label} cmd-server-only (no CALLHOME_SERVER)", flush=True)
        if cmd_port > 0:
            import signal
            signal.pause()
        else:
            print("[callhome] ERROR: no server and no command port — nothing to do", file=sys.stderr)
            sys.exit(1)
        return

    if args.once:
        run_once(args.server, token, force=args.force, container_id=container_id)
    else:
        print(f"[callhome] mode={mode_label} polling every {args.interval}s", flush=True)
        run_loop(
            args.server, token, args.interval,
            force=args.force, container_id=container_id,
            interval_startup=args.interval_startup,
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Call-home client for vm_builds managed nodes.

Only contacts the server when the node's IP address changes.
Saves the last-sent IP to /var/lib/callhome/last_ip so the check
survives reboots. Designed to run from cron every minute.

Zero external dependencies — stdlib only.

Usage:
    python3 callhome.py --once          # check once and exit
    python3 callhome.py --interval 60   # poll loop (legacy)
    python3 callhome.py --force         # ignore saved IP, always send

Environment (or /etc/default/callhome):
    CALLHOME_SERVER      Management server URL
    CALLHOME_PUBLIC_KEY  Auth token (the "public key" given to nodes)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
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
                    os.environ.setdefault(key.strip(), val.strip())
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
    return {
        "node_id": socket.getfqdn(),
        "hostname": socket.gethostname(),
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
    state_file: str = "",
) -> bool:
    """Check IP, send if changed. Returns True if check-in was sent and succeeded."""
    sf = state_file or DEFAULT_STATE_FILE
    if not force:
        changed, current_ip = ip_changed(sf)
        if not changed:
            return False

    payload = build_payload()
    if send_checkin(server_url, payload, token=token):
        current_ip = get_primary_ip()
        if current_ip:
            save_last_ip(current_ip, sf)
        print(f"[callhome] checked in to {server_url} (ip={current_ip})", flush=True)
        return True

    print(f"[callhome] failed to reach {server_url}", flush=True)
    return False


def run_loop(
    server_url: str, token: str, interval: int,
    force: bool = False, state_file: str = "",
) -> None:
    """Poll loop: check IP every interval, send only on change."""
    first = True
    while True:
        run_once(server_url, token, force=(force and first), state_file=state_file)
        first = False
        time.sleep(interval)


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    _load_conf()
    args = parse_args(argv)
    if not args.server:
        print("[callhome] ERROR: --server or CALLHOME_SERVER required", file=sys.stderr)
        sys.exit(1)

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

    if args.once:
        run_once(args.server, token, force=args.force)
    else:
        print(f"[callhome] polling every {args.interval}s", flush=True)
        run_loop(args.server, token, args.interval, force=args.force)


if __name__ == "__main__":
    main()

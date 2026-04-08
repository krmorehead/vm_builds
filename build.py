#!/usr/bin/env python3
"""
Execute an Ansible build against a Proxmox host.

Loads environment variables from a .env file, validates that all required
variables are present, and runs the selected playbook. All core logic is
in testable functions; see tests/test_build.py.

.env file format (one VAR=VALUE per line, no quotes needed):
─────────────────────────────────────────────────────────────
    # Required
    HOME_API_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    PRIMARY_HOST=192.168.1.100
    MESH_KEY=your-secure-mesh-passphrase

    # Optional
    WAN_MAC=AA:BB:CC:DD:EE:FF

    # HOME_API_TOKEN
    #   The Proxmox API token secret. Create one in the PVE web UI at
    #   Datacenter > Permissions > API Tokens. Use user root@pam,
    #   token ID "ansible", and UNCHECK Privilege Separation.
    #
    # PRIMARY_HOST
    #   IP address of the target Proxmox node. Must be reachable via
    #   SSH (key-based auth) from this machine.
    #
    # MESH_KEY
    #   WPA3-SAE passphrase for 802.11s mesh networking. Must match
    #   across all mesh nodes. Ignored if no WiFi hardware is detected.
    #
    # WAN_MAC (optional)
    #   Clone this MAC address onto the OpenWrt WAN interface (net0).
    #   Use the old router's MAC to avoid ISP DHCP lease / DNS cert
    #   issues when swapping routers. Omit to use auto-generated MAC.

Available tags (site.yml plays):
    backup          Back up Proxmox host config and VMs
    infra           Shared infrastructure (bridges, PCI passthrough, iGPU)
    openwrt         OpenWrt router VM provisioning and configuration
    pihole          Pi-hole DNS container
    monitoring      rsyslog + Netdata monitoring containers
    homeassistant   Home Assistant service container
    media           Jellyfin + Kodi media containers
    moonlight       Moonlight streaming client container
    wireguard       WireGuard VPN container
    mesh-wifi       OpenWrt mesh WiFi LXC containers
    desktop         Debian desktop VM
    kiosk           Custom UX kiosk LXC container
    gaming          Gaming LXC container (opt-in, tagged with never)
    lan-satellite   LAN host bootstrap
    cleanup         Remove temporary bootstrap networking

    Tags are independent. If a play depends on another (e.g., openwrt
    depends on infra), include both: --tags infra,openwrt

Usage:
    python build.py                                # run everything
    python build.py --tags openwrt                 # only OpenWrt plays
    python build.py --tags infra,openwrt           # infra + OpenWrt
    python build.py --skip-tags backup             # skip backup
    python build.py --skip-tags backup,cleanup     # skip backup and cleanup
    python build.py --playbook cleanup             # run a different playbook
    python build.py --playbook cleanup --tags clean  # playbook + tag
    python build.py --env test.env                 # use test environment
    python build.py --limit home                   # target a specific host
    python build.py --no-api                       # skip management API server
    python build.py --check                        # dry run (no changes)
    python build.py --check --diff                 # dry run with diffs
    python build.py -vvv                           # verbose output
    python build.py -- -e foo=bar                  # pass-through args
"""

import argparse
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
DEFAULT_PLAYBOOK = "site.yml"
API_PORT = int(os.environ.get("WEBUI_PORT", "52500"))

REQUIRED_ENV = [
    "HOME_API_TOKEN",
    "PRIMARY_HOST",
    "MESH_KEY",
]


def load_env(env_path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file, skipping comments and blank lines.

    Surrounding single or double quotes on values are stripped so that
    both ``FOO=bar`` and ``FOO="bar"`` produce the same result.
    """
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if key and sep:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env[key.strip()] = value
    return env


def validate_env(env: dict[str, str]) -> list[str]:
    """Return list of missing or empty required variables."""
    return [var for var in REQUIRED_ENV if not env.get(var)]


OPTIONAL_HOST_VARS = ["AI_HOST", "MESH_2_HOST", "BRIDGE_1_HOST", "BRIDGE_2_HOST"]

TOKEN_SUFFIX = "_API_TOKEN"

KNOWN_HOSTS = ["HOME", "MESH1", "MESH2", "AI", "BRIDGE_1", "BRIDGE_2"]


def warn_multi_host(env: dict[str, str]) -> list[str]:
    """Return warnings for optional multi-host variables that look misconfigured.

    Not hard failures — single-host runs are valid without these.
    """
    warnings = []
    for var in OPTIONAL_HOST_VARS:
        val = env.get(var, "")
        if val and not val.replace(".", "").isdigit():
            warnings.append(f"{var}={val!r} does not look like an IP address")

    for host in KNOWN_HOSTS:
        token_var = f"{host}{TOKEN_SUFFIX}"
        if token_var in env and not env[token_var]:
            warnings.append(f"{token_var} is set but empty")
    return warnings


STATE_DIR = PROJECT_ROOT / ".state"


def probe_host(ip: str, port: int = 22, timeout: float = 5.0) -> bool:
    """Check if a host is reachable via TCP connect to the given port."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def resolve_proxmox_host(env: dict[str, str]) -> str:
    """Return a reachable IP for the Proxmox host.

    Tries the configured PRIMARY_HOST first.  If unreachable, falls back to
    cached IPs from a previous run stored in .state/addresses.json.
    Returns an empty string if no IP is reachable.
    """
    primary = env["PRIMARY_HOST"]
    print(f"Probing {primary} ...", end=" ", flush=True)
    if probe_host(primary):
        print("reachable")
        return primary
    print("unreachable")

    state_file = STATE_DIR / "addresses.json"
    if state_file.exists():
        try:
            addresses = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            addresses = {}
        for ip in addresses.get("ips", []):
            if ip == primary:
                continue
            print(f"Probing {ip} (cached) ...", end=" ", flush=True)
            if probe_host(ip):
                print("reachable")
                return ip
            print("unreachable")

    return ""


def resolve_playbook(name: str) -> Path:
    """Resolve a playbook name to a full path.

    Checks in order:
      1. Exact path (absolute or relative to cwd)
      2. Under playbooks/ directory
      3. Under playbooks/ with .yml extension appended
    """
    direct = Path(name)
    if direct.exists():
        return direct.resolve()

    in_playbooks = PROJECT_ROOT / "playbooks" / name
    if in_playbooks.exists():
        return in_playbooks

    if not name.endswith((".yml", ".yaml")):
        with_ext = PROJECT_ROOT / "playbooks" / f"{name}.yml"
        if with_ext.exists():
            return with_ext

    return direct


def find_ansible_playbook() -> str | None:
    """Locate the ansible-playbook binary, preferring the project venv."""
    venv_bin = VENV_DIR / "bin" / "ansible-playbook"
    if venv_bin.exists():
        return str(venv_bin)

    system_bin = shutil.which("ansible-playbook")
    if system_bin:
        return system_bin

    return None


def get_controller_ip() -> str:
    """Detect the controller's primary IPv4 address (routable to the fleet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def start_api_server(env_path: Path, port: int = API_PORT) -> subprocess.Popen | None:
    """Start the central management API as a background subprocess.

    Returns the Popen handle, or None if the server failed to start.
    """
    python = str(VENV_DIR / "bin" / "python3")
    if not Path(python).exists():
        python = sys.executable

    app_module = str(PROJECT_ROOT / "scripts" / "webui" / "app.py")
    cmd = [
        python, app_module,
        "--headless",
        "--port", str(port),
        "--env", str(env_path),
    ]

    log_file = PROJECT_ROOT / ".state" / "api_server.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_file, "w")

    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )

    for _ in range(20):
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"WARNING: API server exited early (rc={proc.returncode})", file=sys.stderr)
            log_fh.close()
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc
        except (OSError, TimeoutError):
            continue

    print("WARNING: API server did not become ready in 10s", file=sys.stderr)
    proc.terminate()
    log_fh.close()
    return None


def stop_api_server(proc: subprocess.Popen | None) -> None:
    """Gracefully shut down the API server subprocess."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def build_command(
    ansible_bin: str,
    playbook: str,
    *,
    tags: str | None = None,
    skip_tags: str | None = None,
    limit: str | None = None,
    check: bool = False,
    diff: bool = False,
    verbose: int = 0,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Construct the ansible-playbook command as a list of strings."""
    cmd = [ansible_bin, playbook]

    if tags:
        cmd.extend(["--tags", tags])
    if skip_tags:
        cmd.extend(["--skip-tags", skip_tags])
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    if diff:
        cmd.append("--diff")
    if verbose > 0:
        cmd.append("-" + "v" * verbose)
    if extra_args:
        cmd.extend(extra_args)

    return cmd


class FleetProgressMonitor:
    """Background thread that polls fleet health and prints live status."""

    _INTERVAL = 10
    _ANSI_CLEAR_LINE = "\033[2K"
    _ANSI_UP = "\033[A"
    _STATUS_ICONS = {"online": "+", "stale": "?", "offline": "-", "unknown": " "}

    def __init__(self, api_url: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._known_containers: set[str] = set()
        self._last_line_count = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _fetch_json(self, path: str) -> dict | list | None:
        try:
            with urlopen(f"{self._api_url}{path}", timeout=3) as resp:
                return json.loads(resp.read())
        except (URLError, OSError, json.JSONDecodeError):
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._INTERVAL)

    def _poll_once(self) -> None:
        health = self._fetch_json("/api/fleet/health")
        nodes = self._fetch_json("/api/nodes")
        if health is None or nodes is None:
            return

        lines: list[str] = []
        lines.append(
            f"  Fleet: {health.get('online_nodes', 0)} online, "
            f"{health.get('stale_nodes', 0)} stale, "
            f"{health.get('offline_nodes', 0)} offline  "
            f"(score: {health.get('health_score', 0):.0f}%)"
        )

        for node in sorted(nodes, key=lambda n: n.get("hostname", "")):
            ch = node.get("container_health")
            if not ch:
                continue
            cid = ch.get("container_id", node.get("hostname", "?"))
            ready = ch.get("ready", False)
            status = node.get("status", "unknown")
            icon = self._STATUS_ICONS.get(status, " ")

            is_new = cid not in self._known_containers
            self._known_containers.add(cid)

            marker = " NEW" if is_new else ""
            ready_str = "ready" if ready else "starting"
            lines.append(f"  [{icon}] {cid:<20s} {ready_str:<10s} {status}{marker}")

        if self._last_line_count > 0:
            sys.stderr.write(self._ANSI_UP * self._last_line_count)

        for line in lines:
            sys.stderr.write(f"{self._ANSI_CLEAR_LINE}{line}\n")

        if self._last_line_count > len(lines):
            for _ in range(self._last_line_count - len(lines)):
                sys.stderr.write(f"{self._ANSI_CLEAR_LINE}\n")
            sys.stderr.write(self._ANSI_UP * (self._last_line_count - len(lines)))

        self._last_line_count = len(lines)
        sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, validate environment, and run the playbook.

    Returns the ansible-playbook exit code.
    """
    parser = argparse.ArgumentParser(
        description="Execute an Ansible build against a Proxmox host.",
        epilog=(
            "Any arguments after -- are passed directly to ansible-playbook.\n\n"
            "Available tags: backup, infra, openwrt, pihole, monitoring, homeassistant, "
            "media, moonlight, wireguard, mesh-wifi, desktop, kiosk, gaming, lan-satellite, cleanup"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--playbook",
        default=DEFAULT_PLAYBOOK,
        help=(
            "Playbook to run (default: site.yml). Accepts a name, filename, "
            "or path. Names are resolved from playbooks/."
        ),
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to the environment file (default: .env)",
    )
    parser.add_argument(
        "--tags",
        help="Comma-separated tags to run (e.g., infra,openwrt)",
    )
    parser.add_argument(
        "--skip-tags",
        help="Comma-separated tags to skip (e.g., backup,cleanup)",
    )
    parser.add_argument(
        "--limit",
        help="Limit execution to specific hosts (ansible --limit)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry run — show what would change without applying",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show file diffs for template changes",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Skip starting the central management API server",
    )

    args, extra = parser.parse_known_args(argv)

    # Resolve environment file
    env_path = PROJECT_ROOT / args.env
    if not env_path.exists():
        print(f"ERROR: {env_path} not found.", file=sys.stderr)
        print("  Copy test.env to .env and fill in your values:", file=sys.stderr)
        print("  cp test.env .env", file=sys.stderr)
        return 1

    env = load_env(env_path)
    missing = validate_env(env)
    if missing:
        print(f"ERROR: Missing required variables in {args.env}:", file=sys.stderr)
        for var in missing:
            print(f"  - {var}", file=sys.stderr)
        return 1

    for warning in warn_multi_host(env):
        print(f"WARNING: {warning}", file=sys.stderr)

    # Pre-flight: find a reachable IP for the Proxmox host
    host = resolve_proxmox_host(env)
    if not host:
        print(
            "ERROR: Proxmox host unreachable at all known IPs.",
            file=sys.stderr,
        )
        print(
            f"  Configured: {env['PRIMARY_HOST']}",
            file=sys.stderr,
        )
        state_file = STATE_DIR / "addresses.json"
        if state_file.exists():
            print(f"  Cached:     {state_file}", file=sys.stderr)
        else:
            print("  No cached addresses found (.state/addresses.json)", file=sys.stderr)
        print("  Update PRIMARY_HOST or check network connectivity.", file=sys.stderr)
        return 1
    if host != env["PRIMARY_HOST"]:
        print(f"  Using cached IP {host} (original {env['PRIMARY_HOST']} unreachable)")
    env["PRIMARY_HOST"] = host

    # Resolve playbook
    playbook = resolve_playbook(args.playbook)
    if not playbook.exists():
        print(f"ERROR: Playbook not found: {args.playbook}", file=sys.stderr)
        available = sorted(p.stem for p in (PROJECT_ROOT / "playbooks").glob("*.yml"))
        if available:
            print(f"  Available: {', '.join(available)}", file=sys.stderr)
        return 1

    ansible_bin = find_ansible_playbook()
    if ansible_bin is None:
        print("ERROR: ansible-playbook not found.", file=sys.stderr)
        print("  Run ./setup.sh to create the virtual environment.", file=sys.stderr)
        return 1

    cmd = build_command(
        ansible_bin,
        str(playbook),
        tags=args.tags,
        skip_tags=args.skip_tags,
        limit=args.limit,
        check=args.check,
        diff=args.diff,
        verbose=args.verbose,
        extra_args=extra,
    )

    rel_playbook = playbook.relative_to(PROJECT_ROOT) if playbook.is_relative_to(PROJECT_ROOT) else playbook
    print(f"Target:   {host}")
    print(f"Env file: {args.env}")
    print(f"Playbook: {rel_playbook}")
    if args.tags:
        print(f"Tags:     {args.tags}")
    if args.skip_tags:
        print(f"Skip:     {args.skip_tags}")
    if args.limit:
        print(f"Limit:    {args.limit}")
    if args.check:
        print("Mode:     dry run (--check)")
    print()

    os.chdir(PROJECT_ROOT)

    api_proc: subprocess.Popen | None = None
    if not args.no_api:
        controller_ip = get_controller_ip()
        callhome_url = f"http://{controller_ip}:{API_PORT}"
        print(f"API:      {callhome_url}")

        api_proc = start_api_server(env_path, port=API_PORT)
        if api_proc:
            state_dir = PROJECT_ROOT / ".state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "callhome_url").write_text(callhome_url)
            atexit.register(stop_api_server, api_proc)
        else:
            print("WARNING: Continuing without API server", file=sys.stderr)

    fleet_monitor: FleetProgressMonitor | None = None
    if api_proc and not args.no_api:
        fleet_monitor = FleetProgressMonitor(callhome_url)
        fleet_monitor.start()
        print("Fleet monitor:  active (live status on stderr)\n")
        try:
            req = Request(
                f"{callhome_url}/api/timeline/start", method="POST",
            )
            urlopen(req, timeout=5)
        except Exception:
            pass
    else:
        print()

    result = subprocess.run(cmd, env={**os.environ, **env})

    if fleet_monitor:
        fleet_monitor.stop()
        try:
            req = Request(
                f"{callhome_url}/api/timeline/stop", method="POST",
            )
            resp = urlopen(req, timeout=5)
            tl_data = json.loads(resp.read())
            svc_count = tl_data.get("services", 0)
            duration = tl_data.get("duration", 0)
            if svc_count > 0:
                print(f"\nTimeline: {svc_count} services tracked over {duration:.0f}s")
        except Exception:
            pass
        print()

    stop_api_server(api_proc)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

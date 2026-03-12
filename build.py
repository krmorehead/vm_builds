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
    backup      Back up Proxmox host config and VMs before changes
    infra       Shared infrastructure (bridges, PCI passthrough)
    openwrt     OpenWrt VM provisioning and configuration
    wireguard   WireGuard VPN LXC container provisioning and configuration
    cleanup     Remove temporary bootstrap networking

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
    python build.py --check                        # dry run (no changes)
    python build.py --check --diff                 # dry run with diffs
    python build.py -vvv                           # verbose output
    python build.py -- -e foo=bar                  # pass-through args
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
DEFAULT_PLAYBOOK = "site.yml"

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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, validate environment, and run the playbook.

    Returns the ansible-playbook exit code.
    """
    parser = argparse.ArgumentParser(
        description="Execute an Ansible build against a Proxmox host.",
        epilog=(
            "Any arguments after -- are passed directly to ansible-playbook.\n\n"
            "Available tags: backup, infra, openwrt, wireguard, cleanup"
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
    result = subprocess.run(cmd, env={**os.environ, **env})
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

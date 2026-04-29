#!/usr/bin/env python3
"""Start the headless API server and write state files for molecule.

Reuses start_api_server() from build.py.  The SM listens on 0.0.0.0
and is reachable via VPN (wg0) at CONTROLLER_VPN_IP after base
converge establishes WireGuard tunnels.

Writes:
    .state/test_api.pid   — API server process
    .state/callhome_url   — VPN URL containers use to reach the API

Prints the URL to stdout on success.
"""
import os
import subprocess
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from build import start_api_server, API_PORT  # noqa: E402


def _cleanup_stale(state: Path, primary_host: str) -> None:
    """Kill lingering API server from prior runs."""
    pf = state / "test_api.pid"
    if pf.exists():
        try:
            os.kill(int(pf.read_text().strip()), 15)
        except (ProcessLookupError, ValueError):
            pass
        pf.unlink(missing_ok=True)

    (state / "tunnel.pid").unlink(missing_ok=True)

    fuser = subprocess.run(
        ["fuser", f"{API_PORT}/tcp"],
        capture_output=True, timeout=5, text=True,
    )
    if fuser.returncode == 0:
        for pid_str in fuser.stdout.split():
            pid_str = pid_str.strip()
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_text()
            except OSError:
                continue
            if "python" in cmdline and "app.py" in cmdline:
                os.kill(pid, 15)

    # Clean up legacy supermanager-relay from prior runs (one-time migration)
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"root@{primary_host}",
         "systemctl stop supermanager-relay 2>/dev/null; "
         "systemctl disable supermanager-relay 2>/dev/null; "
         "rm -f /etc/systemd/system/supermanager-relay.service; "
         "systemctl daemon-reload 2>/dev/null; true"],
        capture_output=True, timeout=10,
    )


def main() -> None:
    env_file = project_root / os.environ.get("ENV_FILE", "test.env")
    primary_host = os.environ.get("PRIMARY_HOST", "")
    if not primary_host:
        print("ERROR: PRIMARY_HOST not set", file=sys.stderr)
        sys.exit(1)

    controller_vpn_ip = os.environ.get("CONTROLLER_VPN_IP", "")
    if not controller_vpn_ip:
        print("ERROR: CONTROLLER_VPN_IP not set", file=sys.stderr)
        sys.exit(1)

    state = project_root / ".state"
    state.mkdir(exist_ok=True)

    _cleanup_stale(state, primary_host)

    proc = start_api_server(env_file, port=API_PORT)
    if not proc:
        print("ERROR: API server failed to start", file=sys.stderr)
        sys.exit(1)
    (state / "test_api.pid").write_text(str(proc.pid))

    url = f"http://{controller_vpn_ip}:{API_PORT}"
    (state / "callhome_url").write_text(url)
    print(url)


if __name__ == "__main__":
    main()

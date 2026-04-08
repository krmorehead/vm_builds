#!/usr/bin/env python3
"""Start the headless API server and write state files for molecule.

Reuses start_api_server() from build.py.  Sets up an SSH reverse
tunnel + socat relay so LXC containers behind OpenWrt can reach
the API via the Proxmox host's IP.

Writes:
    .state/test_api.pid   — API server process
    .state/tunnel.pid     — SSH reverse tunnel
    .state/socat.pid      — socat relay on Proxmox host
    .state/callhome_url   — URL containers use to reach the API

Prints the URL to stdout on success.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from build import start_api_server, API_PORT  # noqa: E402

RELAY_PORT = API_PORT + 1  # tunnel relay on port+1, always one above the API


def _cleanup_stale(state: Path, primary_host: str) -> None:
    """Kill lingering API server, SSH tunnel, and remote socat from prior runs."""
    for pidfile in ("test_api.pid", "tunnel.pid"):
        pf = state / pidfile
        if pf.exists():
            try:
                os.kill(int(pf.read_text().strip()), 15)
            except (ProcessLookupError, ValueError):
                pass
            pf.unlink(missing_ok=True)

    socat_pf = state / "socat.pid"
    if socat_pf.exists():
        old_pid = socat_pf.read_text().strip()
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null",
             f"root@{primary_host}", f"kill {old_pid} 2>/dev/null; true"],
            capture_output=True, timeout=10,
        )
        socat_pf.unlink(missing_ok=True)

    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"root@{primary_host}",
         f"fuser -k {RELAY_PORT}/tcp 2>/dev/null; "
         f"fuser -k {API_PORT}/tcp 2>/dev/null; true"],
        capture_output=True, timeout=10,
    )


def _start_tunnel(state: Path, primary_host: str) -> subprocess.Popen:
    """Open an SSH reverse tunnel from controller to the Proxmox host."""
    tunnel_log = open(state / "tunnel.log", "w")  # noqa: SIM115
    tunnel = subprocess.Popen(
        [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4",
            "-N",
            "-R", f"127.0.0.1:{RELAY_PORT}:127.0.0.1:{API_PORT}",
            f"root@{primary_host}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=tunnel_log,
    )
    time.sleep(2)
    if tunnel.poll() is not None:
        raise RuntimeError(f"SSH tunnel failed (exit {tunnel.returncode})")
    (state / "tunnel.pid").write_text(str(tunnel.pid))
    return tunnel


def _start_socat(state: Path, primary_host: str) -> str:
    """Start socat relay on the Proxmox host (0.0.0.0:API_PORT -> tunnel)."""
    socat_cmd = (
        f"nohup socat TCP-LISTEN:{API_PORT},fork,reuseaddr "
        f"TCP:127.0.0.1:{RELAY_PORT} "
        f"</dev/null >/dev/null 2>&1 & echo $!"
    )
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"root@{primary_host}", socat_cmd],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"socat relay failed: {result.stderr}")
    pid = result.stdout.strip()
    (state / "socat.pid").write_text(pid)
    return pid


def main() -> None:
    env_file = project_root / os.environ.get("ENV_FILE", "test.env")
    primary_host = os.environ.get("PRIMARY_HOST", "")
    if not primary_host:
        print("ERROR: PRIMARY_HOST not set", file=sys.stderr)
        sys.exit(1)

    state = project_root / ".state"
    state.mkdir(exist_ok=True)

    _cleanup_stale(state, primary_host)

    proc = start_api_server(env_file, port=API_PORT)
    if not proc:
        print("ERROR: API server failed to start", file=sys.stderr)
        sys.exit(1)
    (state / "test_api.pid").write_text(str(proc.pid))

    try:
        _start_tunnel(state, primary_host)
        _start_socat(state, primary_host)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        proc.terminate()
        sys.exit(1)

    url = f"http://{primary_host}:{API_PORT}"
    (state / "callhome_url").write_text(url)
    print(url)


if __name__ == "__main__":
    main()

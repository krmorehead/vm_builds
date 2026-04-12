#!/usr/bin/env python3
"""Start the headless API server and write state files for molecule.

Reuses start_api_server() from build.py.  Sets up an SSH reverse
tunnel + socat relay so LXC containers behind OpenWrt can reach
the API via the Proxmox host's IP.

Writes:
    .state/test_api.pid   — API server process
    .state/tunnel.pid     — SSH reverse tunnel
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

    # Kill orphaned *Python* processes holding the API port (covers PID-file loss).
    # Scoped to python3 to avoid killing unrelated listeners on the same port.
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

    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"root@{primary_host}",
         f"systemctl stop supermanager-relay 2>/dev/null; "
         f"systemctl disable supermanager-relay 2>/dev/null; "
         f"rm -f /etc/systemd/system/supermanager-relay.service; "
         f"systemctl daemon-reload 2>/dev/null; "
         f"fuser -k {RELAY_PORT}/tcp 2>/dev/null; "
         f"fuser -k {API_PORT}/tcp 2>/dev/null; true"],
        capture_output=True, timeout=10,
    )


def _start_tunnel(state: Path, primary_host: str) -> subprocess.Popen:
    """Open an SSH reverse tunnel from controller to the Proxmox host."""
    tunnel_log = (state / "tunnel.log").open("w")
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=tunnel_log,
        start_new_session=True,
    )
    tunnel_log.close()  # fd is dup'd by Popen; safe to close in parent
    time.sleep(2)
    if tunnel.poll() is not None:
        raise RuntimeError(f"SSH tunnel failed (exit {tunnel.returncode})")
    (state / "tunnel.pid").write_text(str(tunnel.pid))
    return tunnel


def _start_socat(state: Path, primary_host: str) -> str:
    """Deploy socat relay as a systemd unit on the Proxmox host.

    Using a systemd unit instead of nohup because background processes
    started via SSH can die when ansible's ControlMaster session closes.
    The systemd unit survives independently and auto-restarts on failure.
    """
    unit_content = (
        "[Unit]\n"
        f"Description=SuperManager API relay (0.0.0.0:{API_PORT} -> tunnel:{RELAY_PORT})\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/bin/socat TCP-LISTEN:{API_PORT},fork,reuseaddr "
        f"TCP:127.0.0.1:{RELAY_PORT}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    deploy_cmd = (
        f"cat > /etc/systemd/system/supermanager-relay.service << 'UNIT'\n"
        f"{unit_content}UNIT\n"
        f"systemctl daemon-reload && "
        f"systemctl enable --now supermanager-relay && "
        f"systemctl restart supermanager-relay && "
        f"systemctl show -p MainPID supermanager-relay --value"
    )
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         f"root@{primary_host}", deploy_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"socat relay failed: {result.stderr}")
    pid = result.stdout.strip().split("\n")[-1]
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

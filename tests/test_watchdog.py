"""Prove the heartbeat watchdog KILLS when a service goes stale.

This is a functional test of the REAL heartbeat_watchdog.sh script.
It starts a mock SM API, launches the actual watchdog targeting an
isolated victim process, and verifies:

  1. Stale heartbeat → victim KILLED
  2. SM API dies → victim KILLED
  3. Healthy heartbeats → victim stays ALIVE
  4. Target finishes naturally → watchdog exits cleanly

If ANY of these tests fail, the deployment pipeline's safety net is broken.
These tests run on every `pytest tests/` invocation. They cannot be skipped.
"""

import http.server
import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

WATCHDOG_SCRIPT = Path(__file__).parent.parent / "scripts" / "heartbeat_watchdog.sh"


# ── Mock SM API ──────────────────────────────────────────────────────


class MockSMAPI(http.server.HTTPServer):
    """Tiny HTTP server that simulates the SuperManager fleet API."""

    def __init__(self, port: int):
        self.fleet_response: dict = {
            "has_stale": False,
            "healthy": ["kiosk"],
            "stale": [],
            "never_seen": [],
        }
        self.health_responding = True
        super().__init__(("127.0.0.1", port), _Handler)


class _Handler(http.server.BaseHTTPRequestHandler):
    server: MockSMAPI  # type: ignore[assignment]

    def do_GET(self):  # noqa: N802
        if "/api/fleet/health" in self.path:
            if not self.server.health_responding:
                self.send_error(503)
                return
            self._json_ok({"status": "ok"})
            return
        if "/api/fleet/stale" in self.path:
            self._json_ok(self.server.fleet_response)
            return
        self.send_error(404)

    def _json_ok(self, data: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):  # noqa: A002
        pass


# ── Helpers ──────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_api(port: int) -> MockSMAPI:
    server = MockSMAPI(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _start_victim() -> subprocess.Popen:
    """Start a `sleep` in its OWN process group (isolated from pytest)."""
    return subprocess.Popen(["sleep", "300"], start_new_session=True)


def _launch_watchdog(
    api_url: str, pgid_file: str, services: str,
    max_age: int, state_dir: str,
) -> subprocess.Popen:
    """Launch the REAL watchdog script with an overridden state dir."""
    env = os.environ.copy()
    env["WATCHDOG_STATE_DIR"] = state_dir
    return subprocess.Popen(
        [
            "bash", str(WATCHDOG_SCRIPT),
            api_url, pgid_file, services, str(max_age),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # isolate watchdog from pytest's group
        env=env,
    )


def _alive(p: subprocess.Popen) -> bool:
    return p.poll() is None


def _wait_dead(p: subprocess.Popen, timeout: float = 15.0) -> bool:
    """True if process dies within timeout."""
    try:
        p.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _cleanup(*procs: subprocess.Popen) -> None:
    for p in procs:
        if _alive(p):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.no_infra
@pytest.mark.timeout(30)
class TestWatchdogKillsOnStaleHeartbeat:
    """PROVE: stale heartbeat -> victim process KILLED."""

    def test_stale_service_kills_target(self, tmp_path):
        port = _free_port()
        api = _start_api(port)
        victim = _start_victim()
        pgid = os.getpgid(victim.pid)

        pgid_file = str(tmp_path / "pgid")
        Path(pgid_file).write_text(str(pgid))

        watchdog = _launch_watchdog(
            f"http://127.0.0.1:{port}", pgid_file, "kiosk",
            max_age=5, state_dir=str(tmp_path),
        )
        try:
            # Healthy phase — victim must survive
            time.sleep(3)
            assert _alive(victim), "Victim died during healthy phase"

            # KILL THE HEARTBEAT
            api.fleet_response = {
                "has_stale": True,
                "healthy": [],
                "stale": [{
                    "service": "kiosk",
                    "last_seen": "2026-01-01T00:00:00",
                    "node_id": "home",
                    "status": "stale",
                }],
                "never_seen": [],
            }

            assert _wait_dead(victim, timeout=10), (
                f"WATCHDOG FAILED TO KILL on stale heartbeat. "
                f"Victim PID {victim.pid} still alive."
            )
            _wait_dead(watchdog, timeout=5)
            assert watchdog.returncode == 1
            out = (watchdog.stdout.read().decode() if watchdog.stdout else "")
            assert "STALE SERVICE DETECTED" in out

        finally:
            api.shutdown()
            _cleanup(victim, watchdog)


@pytest.mark.no_infra
@pytest.mark.timeout(45)
class TestWatchdogKillsOnSMDeath:
    """PROVE: SM API unreachable for 5+ seconds -> victim KILLED."""

    def test_sm_death_kills_target(self, tmp_path):
        port = _free_port()
        api = _start_api(port)
        victim = _start_victim()
        pgid = os.getpgid(victim.pid)

        pgid_file = str(tmp_path / "pgid")
        Path(pgid_file).write_text(str(pgid))

        watchdog = _launch_watchdog(
            f"http://127.0.0.1:{port}", pgid_file, "kiosk",
            max_age=5, state_dir=str(tmp_path),
        )
        try:
            time.sleep(3)
            assert _alive(victim), "Victim died during healthy phase"

            # KILL THE SM API
            api.shutdown()

            # SM_FAIL_THRESHOLD=5 failures × (1s sleep + 3s curl timeout)
            assert _wait_dead(victim, timeout=30), (
                f"WATCHDOG FAILED TO KILL on SM API death. "
                f"Victim PID {victim.pid} still alive."
            )
            _wait_dead(watchdog, timeout=5)
            assert watchdog.returncode == 1
            out = (watchdog.stdout.read().decode() if watchdog.stdout else "")
            assert "SUPERMANAGER API DEAD" in out

        finally:
            _cleanup(victim, watchdog)


@pytest.mark.no_infra
@pytest.mark.timeout(20)
class TestWatchdogSpareHealthy:
    """PROVE: healthy heartbeats -> victim stays ALIVE."""

    def test_healthy_no_kill(self, tmp_path):
        port = _free_port()
        api = _start_api(port)
        victim = _start_victim()
        pgid = os.getpgid(victim.pid)

        pgid_file = str(tmp_path / "pgid")
        Path(pgid_file).write_text(str(pgid))

        watchdog = _launch_watchdog(
            f"http://127.0.0.1:{port}", pgid_file, "kiosk",
            max_age=5, state_dir=str(tmp_path),
        )
        try:
            time.sleep(8)
            assert _alive(victim), (
                "WATCHDOG KILLED A HEALTHY TARGET — false positive!"
            )
            assert _alive(watchdog), "Watchdog died unexpectedly"

        finally:
            api.shutdown()
            _cleanup(victim, watchdog)


@pytest.mark.no_infra
@pytest.mark.timeout(30)
class TestWatchdogKillsOnZeroHeartbeats:
    """PROVE: SM alive but ZERO heartbeats -> victim KILLED.

    SM with zero heartbeats is DEAD. Not "alive but missing heartbeats."
    DEAD. No heartbeats = no system. The watchdog MUST kill within
    WARMUP_DEADLINE seconds.
    """

    def test_zero_heartbeats_kills_target(self, tmp_path):
        port = _free_port()
        api = _start_api(port)
        # Set fleet response to ZERO services — SM is "alive" but nobody home
        api.fleet_response = {
            "has_stale": False,
            "healthy": [],
            "stale": [],
            "never_seen": [],
        }
        victim = _start_victim()
        pgid = os.getpgid(victim.pid)

        pgid_file = str(tmp_path / "pgid")
        Path(pgid_file).write_text(str(pgid))

        env = os.environ.copy()
        env["WATCHDOG_STATE_DIR"] = str(tmp_path)
        watchdog = subprocess.Popen(
            [
                "bash", str(WATCHDOG_SCRIPT),
                f"http://127.0.0.1:{port}", pgid_file, "kiosk",
                "5",   # MAX_AGE
                "5",   # WARMUP_DEADLINE — 5 seconds, not 300
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        try:
            assert _wait_dead(victim, timeout=20), (
                f"WATCHDOG FAILED TO KILL on ZERO HEARTBEATS. "
                f"Victim PID {victim.pid} still alive. "
                f"SM with zero heartbeats is DEAD, not alive."
            )
            _wait_dead(watchdog, timeout=5)
            assert watchdog.returncode == 1
            out = (watchdog.stdout.read().decode() if watchdog.stdout else "")
            assert "WARMUP DEADLINE EXCEEDED" in out
            assert "heartbeat chain is BROKEN" in out

        finally:
            api.shutdown()
            _cleanup(victim, watchdog)


@pytest.mark.no_infra
@pytest.mark.timeout(25)
class TestWatchdogCleanExit:
    """PROVE: target finishes naturally -> watchdog exits 0."""

    def test_target_done_watchdog_exits(self, tmp_path):
        port = _free_port()
        api = _start_api(port)
        victim = subprocess.Popen(["sleep", "3"], start_new_session=True)
        pgid = os.getpgid(victim.pid)

        pgid_file = str(tmp_path / "pgid")
        Path(pgid_file).write_text(str(pgid))

        watchdog = _launch_watchdog(
            f"http://127.0.0.1:{port}", pgid_file, "kiosk",
            max_age=5, state_dir=str(tmp_path),
        )
        try:
            victim.wait(timeout=10)
            assert _wait_dead(watchdog, timeout=10), "Watchdog didn't exit after target finished"
            assert watchdog.returncode == 0

        finally:
            api.shutdown()
            _cleanup(victim, watchdog)

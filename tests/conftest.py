"""Shared fixtures for NiceGUI web UI tests."""

import json as _json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import build  # noqa: E402


# ── Session-scoped test.env loading ──────────────────────────────────
# Loads test.env ONCE at session start, injects all values into
# os.environ so every test has access to CALLHOME_PUBLIC_KEY,
# PRIMARY_HOST, etc. without per-file boilerplate.

_TEST_ENV_FILE = PROJECT_ROOT / "test.env"


def _load_and_inject_test_env() -> dict[str, str]:
    """Load test.env and inject values into os.environ."""
    if not _TEST_ENV_FILE.exists():
        return {}
    env = build.load_env(_TEST_ENV_FILE)
    for key, value in env.items():
        if key not in os.environ:
            os.environ[key] = value
    return env


_test_env_cache: dict[str, str] = _load_and_inject_test_env()


@pytest.fixture(scope="session")
def test_env() -> dict[str, str]:
    """Parsed test.env as a dict — available to any test via fixture."""
    assert _test_env_cache, "test.env not found or empty"
    return _test_env_cache


# ── Infrastructure prerequisite gate ─────────────────────────────────
# VPN + Kiosk/NM are the TWO base requirements. Every integration test
# depends on them. Validate ONCE at session start, fail fast if broken.

_HOST_KEYS = [
    "PRIMARY_HOST", "AI_HOST", "MESH_2_HOST",
    "BRIDGE_1_HOST", "BRIDGE_2_HOST",
]

_NM_PORT = 9001


def _nm_health_check(ip: str, timeout: int = 3) -> dict:
    """Query a NodeManager's /api/health endpoint. Returns parsed JSON or error dict."""
    url = f"http://{ip}:{_NM_PORT}/api/health"
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return _json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": str(exc)}


def _nm_guests(ip: str, timeout: int = 5) -> list[dict]:
    """Query a NodeManager's /api/guests endpoint. Returns guest list."""
    url = f"http://{ip}:{_NM_PORT}/api/guests"
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = _json.loads(resp.read().decode())
        return data.get("guests", [])
    except (urllib.error.URLError, OSError, ValueError):
        return []


def _check_single_host(key: str) -> tuple[str, dict]:
    """Check VPN + Kiosk/NM prerequisites for a single host. Thread-safe."""
    ip = _test_env_cache.get(key, "")
    if not ip:
        return key, {}
    entry: dict = {"ip": ip, "nm_ok": False, "kiosk_running": False, "wireguard_running": False, "guests": []}

    health = _nm_health_check(ip)
    entry["nm_ok"] = health.get("status") == "ok"
    entry["nm_host"] = health.get("host", "unknown")

    if entry["nm_ok"]:
        guests = _nm_guests(ip)
        entry["guests"] = guests
        entry["kiosk_running"] = any(
            g.get("name") == "kiosk" and g.get("status") == "running"
            for g in guests
        )
        entry["wireguard_running"] = any(
            g.get("name") == "wireguard" and g.get("status") == "running"
            for g in guests
        )

    return key, entry


def _check_vpn_and_kiosk() -> dict[str, dict]:
    """Validate VPN + Kiosk/NM prerequisites on all hosts in parallel.

    Uses ThreadPoolExecutor to probe all hosts concurrently. Total time
    equals the slowest single host (~3-5s) instead of sum of all hosts
    (~15-25s sequential).
    """
    host_keys = [k for k in _HOST_KEYS if _test_env_cache.get(k, "")]
    if not host_keys:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=len(host_keys)) as pool:
        futures = {pool.submit(_check_single_host, k): k for k in host_keys}
        for future in as_completed(futures):
            key, entry = future.result()
            if entry:
                results[key] = entry
    return results


_infra_state: dict[str, dict] | None = None


def _get_infra_state() -> dict[str, dict]:
    """Lazy-load and cache infrastructure state for the session."""
    global _infra_state
    if _infra_state is None:
        _infra_state = _check_vpn_and_kiosk()
    return _infra_state


_infra_failures: list[str] = []
_infra_checked = False


def _validate_infra() -> list[str]:
    """Check infrastructure once, cache failures list."""
    global _infra_checked, _infra_failures
    if _infra_checked:
        return _infra_failures
    _infra_checked = True
    state = _get_infra_state()
    if not state:
        _infra_failures = ["No hosts found in test.env"]
        return _infra_failures
    for key, info in state.items():
        if not info["nm_ok"]:
            _infra_failures.append(f"{key} ({info['ip']}): NodeManager NOT reachable on port {_NM_PORT}")
        if not info.get("kiosk_running"):
            _infra_failures.append(f"{key} ({info['ip']}): kiosk container NOT running")
        if not info.get("wireguard_running"):
            _infra_failures.append(f"{key} ({info['ip']}): wireguard container NOT running")
    return _infra_failures


@pytest.fixture(autouse=True)
def _infra_prereq_gate(request):
    """Per-test gate: VPN + Kiosk/NM must be running on ALL hosts.

    Autouse — runs for every test. Tests marked @pytest.mark.no_infra
    are exempted (pure unit tests that don't touch real infrastructure).
    """
    if request.node.get_closest_marker("no_infra"):
        return
    failures = _validate_infra()
    if failures:
        pytest.fail(
            "INFRASTRUCTURE PREREQUISITES NOT MET — VPN + Kiosk must be running on ALL hosts.\n"
            + "\n".join(f"  - {f}" for f in failures)
            + "\n\nRun 'molecule converge' to establish the baseline first."
        )


@pytest.fixture(scope="session")
def infra_state() -> dict[str, dict]:
    """Cached infrastructure state — container lists per host from NM API."""
    return _get_infra_state()


def host_has_container(host_key: str, container_name: str) -> bool:
    """Check if a specific container is running on a host via cached infra state."""
    state = _get_infra_state()
    info = state.get(host_key, {})
    return any(
        g.get("name") == container_name and g.get("status") == "running"
        for g in info.get("guests", [])
    )


# Direct NiceGUI storage to a temp directory so it never writes .nicegui/
# inside the project tree. The directory persists for the full pytest session
# so async backup tasks that fire during teardown still have a valid path.
_STORAGE_DIR = tempfile.mkdtemp(prefix="nicegui_test_storage_")
os.environ["NICEGUI_STORAGE_PATH"] = _STORAGE_DIR


# Workaround for NiceGUI 3.9.0 testing bugs:
#
# 1. Storage.clear() crashes between consecutive user_simulation() contexts
#    because it accesses a slot whose parent was deleted during cleanup.
#    Patched to clear in-memory dicts + unlink JSON files without removing
#    the temp directory (async_backup still needs it).
#
# 2. background_tasks.teardown() enters an infinite loop when tasks from a
#    previous event loop can't be gathered (ValueError: future belongs to a
#    different loop). The `while running_tasks` loop retries forever.
#    Patched to filter out stale tasks before calling the real teardown.
#
# Both are upstream issues — remove when upgrading past the fix.

from nicegui.persistence.file_persistent_dict import FilePersistentDict  # noqa: E402
from nicegui.storage import Storage  # noqa: E402
from nicegui import background_tasks  # noqa: E402

_orig_storage_clear = Storage.clear


def _safe_storage_clear(self: Storage) -> None:
    """Clear storage data but keep the temp directory alive.

    NiceGUI's async_backup coroutine writes storage-general.json after
    clear() returns. If we delete the directory here, the async write
    hits FileNotFoundError. The temp dir is cleaned in pytest_sessionfinish.
    """
    self._general.clear()
    self._users.clear()
    self._tabs.clear()
    for filepath in self.path.glob("storage-*.json"):
        try:
            filepath.unlink()
        except FileNotFoundError:
            pass


Storage.clear = _safe_storage_clear

_orig_teardown = background_tasks.teardown


async def _safe_teardown() -> None:
    """Clear stale tasks from other event loops before running teardown."""
    import asyncio
    loop = asyncio.get_running_loop()
    stale = set()
    for task in background_tasks.running_tasks:
        try:
            if task.get_loop() is not loop:
                stale.add(task)
        except RuntimeError:
            stale.add(task)
    background_tasks.running_tasks -= stale

    stale_lazy = [
        name for name, task in background_tasks.lazy_tasks_running.items()
        if task.get_loop() is not loop
    ]
    for name in stale_lazy:
        background_tasks.lazy_tasks_running.pop(name, None)

    await _orig_teardown()


background_tasks.teardown = _safe_teardown


def pytest_sessionfinish(session, exitstatus):
    """Clean up the NiceGUI temp storage directory after all tests complete."""
    import shutil
    if os.path.isdir(_STORAGE_DIR):
        shutil.rmtree(_STORAGE_DIR, ignore_errors=True)

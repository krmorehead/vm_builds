"""Shared fixtures for NiceGUI web UI tests."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Direct NiceGUI storage to a temp directory so it never writes .nicegui/
# inside the project tree. The directory persists for the full pytest session
# so async backup tasks that fire during teardown still have a valid path.
_STORAGE_DIR = tempfile.mkdtemp(prefix="nicegui_test_storage_")
os.environ["NICEGUI_STORAGE_PATH"] = _STORAGE_DIR


# Workaround for NiceGUI 3.9.0 testing bugs:
#
# 0. FilePersistentDict.backup() schedules an async task that opens the
#    storage file.  Between user_simulation() contexts the directory can
#    vanish.  Monkey-patch backup() to ensure the parent dir exists inside
#    the async coroutine and swallow errors during teardown.
#
# 1. Storage.clear() crashes between consecutive user_simulation() contexts
#    because it accesses a slot whose parent was deleted during cleanup.
#
# 2. background_tasks.teardown() enters an infinite loop when tasks from a
#    previous event loop can't be gathered (ValueError: future belongs to a
#    different loop). The `while running_tasks` loop retries forever.
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

"""Shared async subprocess runner for deploy and image build pages.

Output is ALWAYS persisted to a log file in .state/ so it survives
browser disconnects. The ui.log element is best-effort — if the
browser session dies mid-deploy, the process keeps running and the
log file captures everything. On reconnect the deploy page loads the
persisted log.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable

from nicegui import ui

from scripts.webui.data import PROJECT_ROOT

_log = logging.getLogger(__name__)

DEPLOY_LOG = PROJECT_ROOT / ".state" / "deploy_output.log"


async def stream_process(
    cmd: list[str],
    log: ui.log,
    *,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    proc_holder: dict | None = None,
    log_file: Path | None = None,
) -> int:
    """Run a command, stream stdout into a ui.log, return exit code.

    Args:
        cmd: Command and arguments to execute.
        log: NiceGUI log element to push output lines into.
        env_extra: Additional environment variables merged with os.environ.
        cwd: Working directory (defaults to PROJECT_ROOT).
        on_line: Optional callback for each output line (e.g. to parse status).
        proc_holder: If provided, the running process is stored at key "process"
                     so callers can send signals (e.g. SIGTERM for cancel).
        log_file: If provided, all output is also written to this file.

    Returns:
        Process exit code, or 1 on exception.
    """
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)

    file_handle = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handle = open(log_file, "w")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(cwd or PROJECT_ROOT),
        )
        if proc_holder is not None:
            proc_holder["process"] = proc

        if proc.stdout:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if file_handle:
                    file_handle.write(text + "\n")
                    file_handle.flush()
                try:
                    log.push(text)
                except RuntimeError:
                    pass
                if on_line:
                    try:
                        on_line(text)
                    except RuntimeError:
                        pass
        return await proc.wait()
    except Exception as exc:
        try:
            log.push(f"Error: {exc}")
        except RuntimeError:
            pass
        _log.error("stream_process error: %s", exc)
        return 1
    finally:
        if proc_holder is not None:
            proc_holder["process"] = None
        if file_handle:
            file_handle.close()

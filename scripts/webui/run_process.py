"""Shared async subprocess runner for deploy and image build pages."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable

from nicegui import ui

from scripts.webui.data import PROJECT_ROOT


async def stream_process(
    cmd: list[str],
    log: ui.log,
    *,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    proc_holder: dict | None = None,
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

    Returns:
        Process exit code, or 1 on exception.
    """
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)

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
                log.push(text)
                if on_line:
                    on_line(text)
        return await proc.wait()
    except Exception as exc:
        log.push(f"Error: {exc}")
        return 1
    finally:
        if proc_holder is not None:
            proc_holder["process"] = None

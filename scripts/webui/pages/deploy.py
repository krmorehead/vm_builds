"""Deploy execution page with live output streaming."""

from __future__ import annotations

import asyncio
import signal
import time

from nicegui import app as nicegui_app, ui

from scripts.webui import data, theme
from scripts.webui.run_process import stream_process


def register() -> None:
    @ui.page("/deploy")
    def deploy_page() -> None:
        from scripts.webui.app import get_env_path, get_state_dir

        env_path = get_env_path()
        state_dir = get_state_dir()
        state: dict = {"running": False, "process": None, "start_time": 0.0}

        with theme.page_shell("deploy"):
            theme.page_header("Deploy")

            summary = ui.label("").classes("text-sm")

            with ui.row().classes("gap-4 items-center"):
                limit_input = ui.input(label="Host limit", placeholder="host pattern").classes("w-48")
                dry_run = ui.switch("Dry Run")
                verbose_input = ui.input(label="Verbose (0-3)", value="0").classes("w-24")

            log = ui.log(max_lines=2000).classes("w-full h-96 font-mono text-xs")
            status_label = ui.label("").classes("text-sm font-semibold")

            def _sync_buttons() -> None:
                if state["running"]:
                    start_btn.disable()
                    cancel_btn.enable()
                else:
                    start_btn.enable()
                    cancel_btn.disable()

            def _finish_deploy(exit_code: int, tags: list[str]) -> None:
                elapsed = time.monotonic() - state["start_time"]
                state["running"] = False
                _sync_buttons()

                if exit_code == 0:
                    theme.status_text(status_label, f"Deploy succeeded in {elapsed:.0f}s", "success")
                elif exit_code == -15:
                    theme.status_text(status_label, "Deploy cancelled", "warning")
                else:
                    theme.status_text(status_label, f"Deploy failed (exit {exit_code}) after {elapsed:.0f}s", "error")

                record = data.DeployRecord(
                    timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    tags=tags,
                    env_file=str(env_path),
                    exit_code=exit_code,
                    duration_seconds=round(elapsed, 1),
                    host_limit=limit_input.value.strip() or None,
                )
                data.save_deploy_record(state_dir, record)

            async def _start_deploy() -> None:
                if state["running"]:
                    ui.notify("A deployment is already running.", type="warning")
                    return

                tags = nicegui_app.storage.general.get("selected_tags", [])
                if not tags:
                    ui.notify("No tags selected.", type="warning")
                    return

                limit = limit_input.value.strip() or None
                check = dry_run.value
                verbose_str = verbose_input.value.strip()
                verbose = min(int(verbose_str), 3) if verbose_str.isdigit() else 0

                cmd = data.build_deploy_command(
                    env_path=env_path,
                    tags=tags,
                    limit=limit,
                    check=check,
                    diff=check,
                    verbose=verbose,
                )

                log.clear()
                theme.status_text(status_label, "Deploying...", "info")
                state["running"] = True
                state["start_time"] = time.monotonic()
                _sync_buttons()

                env_extra: dict[str, str] = {"ANSIBLE_FORCE_COLOR": "true"}
                if env_path.exists():
                    loaded = data.load_environment(env_path)
                    env_extra.update(loaded.values)

                def _on_line(text: str) -> None:
                    if text.startswith("PLAY ["):
                        play_name = text.split("[", 1)[1].rstrip("]") if "[" in text else text
                        status_label.text = f"Running: {play_name}"

                rc = await stream_process(
                    cmd, log,
                    env_extra=env_extra,
                    on_line=_on_line,
                    proc_holder=state,
                )
                _finish_deploy(rc, tags)

            async def _cancel_deploy() -> None:
                proc = state.get("process")
                if proc and state["running"]:
                    proc.send_signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                    ui.notify("Deploy cancelled.", type="warning")

            with ui.row().classes("gap-3"):
                start_btn = ui.button(
                    "Start Deploy",
                    icon="play_arrow",
                    on_click=_start_deploy,
                ).classes("action-btn")
                cancel_btn = ui.button(
                    "Cancel",
                    icon="stop",
                    on_click=_cancel_deploy,
                ).classes("outline-btn")
                cancel_btn.disable()

            def _update_summary() -> None:
                tags = nicegui_app.storage.general.get("selected_tags", [])
                if tags:
                    hosts = data.get_hosts_for_tags(tags)
                    summary.text = (
                        f"Tags: {', '.join(tags)}  ·  "
                        f"Env: {env_path.name}  ·  "
                        f"Hosts: {', '.join(hosts)}"
                    )
                    summary.style(f"color: {theme.TEXT_PRIMARY}")
                else:
                    summary.text = "No tags selected. Go to Services to select."
                    summary.style(f"color: {theme.TEXT_SECONDARY}")
                _sync_buttons()

            _update_summary()

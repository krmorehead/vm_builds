"""Image management page — show build status and trigger builds."""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import Labels, PageTitles, Routes
from scripts.webui.run_process import stream_process

LOCAL_TARGETS = [
    ("mesh", "Mesh LXC", "Build the OpenWrt mesh container image (no host needed)"),
    ("router", "Router VM", "Build the OpenWrt router VM image (no host needed)"),
]


def register() -> None:
    @ui.page(Routes.IMAGES)
    def images_page() -> None:
        from scripts.webui.app import get_images_dir, load_active_env

        images_dir = get_images_dir()
        env = load_active_env()
        state: dict = {"building": False}

        with theme.page_shell("images"):
            theme.page_header(PageTitles.IMAGES)

            status_summary = ui.label("").classes("text-sm")

            theme.section_label("Quick Build — No Host Required")
            with ui.row().classes("gap-3"):
                for target, label, tooltip in LOCAL_TARGETS:
                    ui.button(
                        label,
                        icon="build",
                        on_click=lambda t=target: _quick_build(t),
                    ).classes("action-btn").tooltip(tooltip)

            theme.section_label("All Images")
            table = ui.table(
                columns=[
                    {"name": "image", "label": "Image", "field": "image", "align": "left", "sortable": True},
                    {"name": "status", "label": "Status", "field": "status", "align": "center"},
                    {"name": "size", "label": "Size", "field": "size", "align": "right"},
                    {"name": "modified", "label": "Modified", "field": "modified", "align": "center"},
                    {"name": "build_type", "label": "Build Type", "field": "build_type", "align": "center"},
                ],
                rows=[],
                row_key="target",
                selection="single",
            ).classes("w-full")

            log = ui.log(max_lines=1000).classes("w-full h-64 font-mono text-xs")
            log.visible = False

            def _refresh_table() -> None:
                imgs = data.get_image_status(images_dir)
                built = sum(1 for i in imgs if i.exists)
                total = len(imgs)
                if built == total:
                    theme.status_text(status_summary, f"All {total} images built", "success")
                else:
                    theme.status_text(status_summary, f"{built}/{total} images built — {total - built} missing", "warning")

                rows: list[dict] = []
                for img in imgs:
                    rows.append({
                        "target": img.build_target,
                        "image": img.name,
                        "status": "Built" if img.exists else "Missing",
                        "size": f"{img.size_mb} MB" if img.size_mb else "",
                        "modified": img.modified_date or "",
                        "build_type": "local" if not img.requires_host else "remote",
                    })
                table.rows = rows

            async def _run_build(cmd: list[str]) -> None:
                state["building"] = True
                log.clear()
                log.visible = True
                build_btn.disable()
                build_all_btn.disable()

                rc = await stream_process(cmd, log)
                if rc == 0:
                    ui.notify("Build completed successfully.", type="positive")
                else:
                    ui.notify(f"Build failed (rc={rc}).", type="negative")

                state["building"] = False
                build_btn.enable()
                build_all_btn.enable()
                _refresh_table()

            async def _quick_build(target: str) -> None:
                if state["building"]:
                    ui.notify("A build is already running.", type="warning")
                    return
                cmd = data.build_image_command(target)
                await _run_build(cmd)

            async def _build_selected() -> None:
                if state["building"]:
                    ui.notify("A build is already running.", type="warning")
                    return
                sel = table.selected
                if not sel:
                    ui.notify("Select an image to build.", type="warning")
                    return
                target = sel[0]["target"]
                host = env.get("PRIMARY_HOST")
                cmd = data.build_image_command(target, host=host)
                await _run_build(cmd)

            async def _build_all() -> None:
                if state["building"]:
                    ui.notify("A build is already running.", type="warning")
                    return
                cmd = data.build_image_command("all", parallel=True)
                await _run_build(cmd)

            with ui.row().classes("gap-3"):
                ui.button(Labels.REFRESH, icon="refresh", on_click=_refresh_table).classes("subtle-btn")
                build_btn = ui.button(
                    Labels.BUILD_SELECTED,
                    icon="build",
                    on_click=_build_selected,
                ).classes("outline-btn")
                build_all_btn = ui.button(
                    Labels.BUILD_ALL,
                    icon="rocket_launch",
                    on_click=_build_all,
                ).classes("action-btn")

            _refresh_table()

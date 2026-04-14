"""Display-app launcher — starts containers/VMs via the display transfer service.

The kiosk stays alive during display app launches (headless rendering).
The display transfer service handles conflict resolution between mutually
exclusive display apps. Remote users view the app via VNC console from
the SuperManager or ClusterManager.
"""

from __future__ import annotations

from urllib.parse import unquote

from nicegui import ui

from scripts.webui import theme
from scripts.webui.api_client import api
from scripts.webui.data import DISPLAY_APPS, Labels, Routes, console_url
from scripts.webui.manager import try_get_instance


async def _launch_guest(vmid: str) -> dict:
    """Fire-and-forget start of a container/VM via the local manager API."""
    result = await api.post_json(f"/api/guests/{vmid}/start", timeout=10)
    if result is not None:
        return result
    return {"success": False, "error": "Manager API unreachable"}


def register() -> None:
    @ui.page(Routes.LAUNCH)
    async def launch_page(
        vmid: str = "",
        title: str = "",
        url_key: str = "",
    ) -> None:
        title = unquote(title) if title else Labels.LAUNCH_PREFIX
        theme.apply_theme()
        theme.kiosk_nav_bar()

        app_info = DISPLAY_APPS.get(url_key, {})
        icon = app_info.get("icon", "\U0001f680")
        description = app_info.get("description", "")
        app_id = app_info.get("app_id", "")

        mgr = try_get_instance()
        node_id = mgr._host_name if mgr else ""

        with ui.column().classes(
            "w-full max-w-[600px] mx-auto items-center justify-center gap-6 kiosk-body-offset"
        ).style("min-height: calc(100vh - 80px);"):

            ui.label(icon).classes("text-6xl")
            ui.label(title).classes("text-2xl font-bold").style(
                f"color: {theme.TEXT_PRIMARY}"
            )

            if description:
                ui.label(description).classes("text-sm text-center max-w-sm").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )

            if not vmid:
                ui.label(Labels.NO_VMID_CONFIGURED).style(
                    f"color: {theme.COLOR_ERROR}"
                )
                ui.button(
                    Labels.BACK_TO_HUB, icon="home",
                    on_click=lambda: ui.navigate.to(Routes.HUB),
                ).classes("action-btn mt-4")
                return

            status_label = ui.label("").classes("text-sm").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            launch_btn = ui.button(
                f"{Labels.LAUNCH_PREFIX} {title}", icon="play_arrow",
            ).classes("action-btn text-lg px-8 py-3")

            console_link = console_url(node_id, app_id, back=Routes.HUB) if (node_id and app_id) else ""

            async def do_launch() -> None:
                launch_btn.disable()
                status_label.text = f"Starting VMID {vmid}..."
                status_label.style(f"color: {theme.COLOR_WARNING}")
                result = await _launch_guest(vmid)
                if result.get("success", True):
                    status_label.text = f"{title} is running."
                    status_label.style(f"color: {theme.ACCENT}")
                    if console_link:
                        ui.navigate.to(console_link)
                else:
                    status_label.text = result.get("error", "Launch failed")
                    status_label.style(f"color: {theme.COLOR_ERROR}")
                    launch_btn.enable()

            launch_btn.on_click(do_launch)

            if console_link:
                ui.button(
                    f"View {title} Console", icon="cast",
                    on_click=lambda: ui.navigate.to(console_link),
                ).classes("action-btn-outline mt-2")

            ui.separator().classes("w-full max-w-sm")

            ui.button(
                Labels.BACK_TO_HUB, icon="home",
                on_click=lambda: ui.navigate.to(Routes.HUB),
            ).classes("action-btn-outline")

"""Display-app launcher — starts containers/VMs that take over the display.

When a display-exclusive app (Moonlight, Kodi, Desktop VM) is launched,
the Proxmox hookscript stops the kiosk container so the app owns the
GPU/display. When the app stops, the hookscript restarts the kiosk,
returning the user to the Home Hub automatically.

Because the kiosk container is stopped mid-launch, the API call is
fire-and-forget: we show a "switching display..." message and accept
that the response may never arrive.
"""

from __future__ import annotations

from urllib.parse import unquote

from nicegui import ui

from scripts.webui import theme
from scripts.webui.api_client import api
from scripts.webui.data import DISPLAY_APPS, Labels, Routes


async def _launch_guest(vmid: str) -> dict:
    """Fire-and-forget start of a container/VM via the local manager API."""
    result = await api.post_json(f"/api/guests/{vmid}/start", timeout=5)
    if result is not None:
        return result
    return {"success": False, "error": "Manager API unreachable"}


def register() -> None:
    @ui.page("/launch")
    async def launch_page(
        vmid: str = "",
        title: str = "App",
        url_key: str = "",
    ) -> None:
        title = unquote(title)
        theme.apply_theme()
        theme.kiosk_nav_bar()

        app_info = DISPLAY_APPS.get(url_key, {})
        icon = app_info.get("icon", "\U0001f680")
        description = app_info.get("description", "")

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
                ui.label("No VMID configured for this app").style(
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
                f"Launch {title}", icon="play_arrow",
            ).classes("action-btn text-lg px-8 py-3")

            async def do_launch() -> None:
                launch_btn.disable()
                status_label.text = f"Starting VMID {vmid}..."
                status_label.style(f"color: {theme.COLOR_WARNING}")
                result = await _launch_guest(vmid)
                if result.get("success", True):
                    status_label.text = (
                        "Display switching... the kiosk will return when "
                        f"{title} exits."
                    )
                    status_label.style(f"color: {theme.ACCENT}")
                else:
                    status_label.text = result.get("error", "Launch failed")
                    status_label.style(f"color: {theme.COLOR_ERROR}")
                    launch_btn.enable()

            launch_btn.on_click(do_launch)

            ui.separator().classes("w-full max-w-sm")

            with ui.expansion("How does this work?").classes("w-full max-w-sm"):
                ui.label(
                    "Display-exclusive apps (Moonlight, Kodi, Desktop) take "
                    "full control of the GPU and display. The kiosk container "
                    "is automatically stopped while the app runs. When you "
                    "exit the app, the kiosk restarts and this hub reappears."
                ).classes("text-xs").style(f"color: {theme.TEXT_SECONDARY}")

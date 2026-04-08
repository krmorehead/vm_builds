"""External app viewer — wraps external service URLs in an iframe with navigation.

In kiosk mode (Cage + Chromium --kiosk), there's no address bar or back button.
This page provides a persistent top bar with "Back to Hub" so the user can
always return from any external service (Jellyfin, Pi-hole, Home Assistant, etc.).
"""

from __future__ import annotations

import html
from urllib.parse import unquote

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import Labels, Routes


def register() -> None:
    @ui.page("/view")
    async def viewer_page(url: str = "", title: str = "App") -> None:
        title = unquote(title)

        theme.apply_theme()

        ui.add_head_html("""
        <style>
            body { margin: 0; overflow: hidden; }
            .viewer-bar {
                position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
                height: 40px; display: flex; align-items: center;
                padding: 0 12px; gap: 12px;
                transition: opacity 0.3s ease;
            }
            .viewer-bar:hover { opacity: 1 !important; }
            .viewer-frame {
                position: fixed; top: 40px; left: 0; right: 0; bottom: 0;
                border: none; width: 100%; height: calc(100vh - 40px);
            }
        </style>
        """)

        bar_bg = f"background: {theme.BG_CARD}; border-bottom: 1px solid {theme.BORDER};"
        with ui.element("div").classes("viewer-bar").style(bar_bg + " opacity: 0.85;"):
            ui.button(
                icon="home", on_click=lambda: ui.navigate.to(Routes.HUB),
            ).props("flat dense round").style(f"color: {theme.ACCENT}")
            ui.label(title).classes("text-sm font-medium").style(
                f"color: {theme.TEXT_PRIMARY}"
            )
            ui.space()
            if url:
                safe_js_url = html.escape(url, quote=True)
                ui.button(
                    icon="open_in_new",
                    on_click=lambda u=safe_js_url: ui.run_javascript(f'window.location.href="{u}"'),
                ).props("flat dense round").tooltip("Open directly (leaves kiosk)").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )

        if url:
            safe_url = html.escape(url, quote=True)
            ui.element("iframe").props(f'src="{safe_url}"').classes("viewer-frame")
        else:
            with ui.column().classes("w-full items-center justify-center").style(
                "height: calc(100vh - 40px);"
            ):
                ui.icon("link_off", size="xl").style(f"color: {theme.TEXT_DISABLED}")
                ui.label("No URL configured for this service").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                ui.button(
                    Labels.BACK_TO_HUB, icon="home",
                    on_click=lambda: ui.navigate.to(Routes.HUB),
                ).classes("action-btn mt-4")

"""Unified display console — KasmVNC iframe for any display app.

Routes:
    /console/{node_id}/{app_id}  — renders a KasmVNC iframe for the
                                    display app's stream endpoint.

The browser connects DIRECTLY to the app's KasmVNC endpoint — no nesting.
The SM only needs the node's VPN IP to establish the KasmVNC connection.
Once connected, all interaction (session switching, app navigation) happens
through the node's own kiosk UI inside the stream.

The viewer bar is a minimal, toggleable overlay providing just:
- Back navigation to the SM
- App/node label for orientation
- Toggle to hide the bar for full-screen immersion
"""

from __future__ import annotations

from urllib.parse import unquote

from nicegui import ui

from scripts.webui import manager, theme
from scripts.webui.data import (
    DISPLAY_APP_CONFIGS, Labels, Routes,
)
from scripts.webui.pages.display_shared import (
    iframe_passthrough_css,
    render_display_iframe, render_viewer_error,
    toggle_viewer_bar_js, viewer_base_css,
)


def _viewer_bar(
    label: str,
    node_id: str,
    icon: str,
    back: str,
) -> None:
    """Minimal, toggleable top bar — just back + label + toggle.

    The SM only provides navigation chrome.  All app-level controls
    (session switching, launching) happen inside the node's kiosk UI
    via the KasmVNC stream.
    """
    with ui.element("div").classes("viewer-bar"):
        ui.button(
            icon="arrow_back", on_click=lambda: ui.navigate.to(back),
        ).props("flat dense round").style(f"color: {theme.ACCENT}")
        if icon:
            ui.label(icon).classes("text-lg")
        ui.label(f"{label} on {node_id}").style(
            f"color: {theme.TEXT_PRIMARY}; font-weight: 600;"
        )
        ui.element("div").style("flex: 1;")
        ui.button(
            icon="visibility_off",
            on_click=lambda: toggle_viewer_bar_js(),
        ).props("flat dense round").tooltip("Toggle bar").style(
            f"color: {theme.TEXT_SECONDARY}"
        )


def register() -> None:
    """Register the /console/{node_id}/{app_id} route."""

    @ui.page(Routes.CONSOLE)
    def console_page(
        node_id: str,
        app_id: str,
        back: str = Routes.NODES,
    ) -> None:
        node_id = unquote(node_id)
        app_id = unquote(app_id)
        back = unquote(back)

        mgr = manager.try_get_instance()
        config = DISPLAY_APP_CONFIGS.get(app_id)
        label = config.label if config else app_id
        icon = config.icon if config else ""

        theme.apply_theme()

        if not mgr:
            _render_error(label, node_id, back, Labels.MANAGER_NOT_INITIALIZED)
            return

        if config and config.target_hosts and node_id not in config.target_hosts:
            _render_error(label, node_id, back, f"{label} is not available on {node_id}")
            return

        viewstream_url = mgr.get_guest_viewstream_url(node_id, app_id)
        if not viewstream_url:
            _render_error(label, node_id, back, f"{Labels.HOST_UNREACHABLE}: {node_id}")
            return

        _render_display_console(node_id, app_id, label, icon, viewstream_url, back)


def _render_error(
    label: str, node_id: str, back: str, message: str,
) -> None:
    """Render an error when the console cannot be reached."""
    render_viewer_error(f"{label} on {node_id}", message, back)


def _render_display_console(
    node_id: str,
    app_id: str,
    label: str,
    icon: str,
    display_url: str,
    back: str,
) -> None:
    """Render a KasmVNC iframe for a display app.

    The SM provides only the connection and a minimal toggleable bar.
    All app-level interaction happens inside the node's own kiosk UI
    through the KasmVNC stream.
    """
    ui.add_head_html(viewer_base_css())
    ui.add_head_html(iframe_passthrough_css())

    _viewer_bar(label, node_id, icon, back)

    render_display_iframe(display_url)

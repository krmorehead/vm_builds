"""Remote Kiosk viewer — VNC streaming via noVNC.

Renders a direct WebSocket VNC connection to a target kiosk's
wayvnc/websockify stack, with hierarchical back-navigation,
a child node picker for drill-down, and app switcher buttons
for direct transitions to any registered display app.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote

from nicegui import ui

from scripts.webui import manager, theme
from scripts.webui.data import Labels, Routes
from scripts.webui.pages.vnc_shared import (
    mount_static, render_app_console_links, render_viewer_error,
    render_vnc_canvas, viewer_base_css,
)


def register() -> None:
    """Register the /remote/{node_id} route and static assets."""
    mount_static()

    @ui.page(Routes.REMOTE_KIOSK)
    def remote_kiosk_page(node_id: str, back: str = Routes.NODES) -> None:
        node_id = unquote(node_id)
        back = unquote(back)

        theme.apply_theme()

        mgr = manager.try_get_instance()
        if not mgr:
            _render_not_reachable(node_id, back)
            return

        vnc_url = mgr.get_child_vnc_url(node_id)
        children = mgr.get_fleet_children(node_id)

        if vnc_url is None:
            _render_not_reachable(node_id, back)
            return

        _render_vnc_viewer(node_id, vnc_url, children, back)


def _render_not_reachable(node_id: str, back: str) -> None:
    """Render an error state when the kiosk VNC is not reachable."""
    render_viewer_error(node_id, Labels.KIOSK_NOT_REACHABLE, back, icon="cast_connected")


def _render_vnc_viewer(
    node_id: str,
    vnc_url: str,
    children: list[str],
    back: str,
) -> None:
    """Render the noVNC canvas with top bar navigation chrome."""
    ui.add_head_html(viewer_base_css())

    with ui.element("div").classes("viewer-bar"):
        ui.button(
            icon="arrow_back", on_click=lambda: ui.navigate.to(back),
        ).props("flat dense round").style(f"color: {theme.ACCENT}")
        ui.label(node_id).style(
            f"color: {theme.TEXT_PRIMARY}; font-weight: 600;"
        )

        if children:
            def _navigate_child(e: Any) -> None:
                child_id = e.value
                if child_id:
                    remote_url = Routes.REMOTE_KIOSK.replace("{node_id}", node_id)
                    encoded_back = quote(f"{remote_url}?back={quote(back)}")
                    child_url = Routes.REMOTE_KIOSK.replace("{node_id}", child_id)
                    ui.navigate.to(f"{child_url}?back={encoded_back}")

            ui.select(
                options=children,
                label=Labels.DRILL_INTO,
                on_change=_navigate_child,
            ).props("dense outlined").style(
                f"min-width: 140px; color: {theme.TEXT_PRIMARY};"
            )

        kiosk_back = Routes.REMOTE_KIOSK.replace("{node_id}", node_id) + f"?back={quote(back)}"
        render_app_console_links(node_id, back=kiosk_back)

        ui.element("div").props('id="vnc-status-dot"')

    render_vnc_canvas(vnc_url)

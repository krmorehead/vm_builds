"""Remote Kiosk viewer — display streaming via KasmVNC iframe.

Renders a KasmVNC iframe connecting to a target kiosk's display.
The SM only needs the node's VPN IP to establish the connection.
Once connected, all interaction happens through the node's own
kiosk UI inside the KasmVNC stream.

The viewer bar is a minimal, toggleable overlay for back-navigation
and an optional child-node picker for drill-down.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote

from nicegui import ui

from scripts.webui import manager, theme
from scripts.webui.data import Labels, Routes
from scripts.webui.pages.display_shared import (
    iframe_passthrough_css,
    render_display_iframe, render_viewer_error,
    toggle_viewer_bar_js, viewer_base_css,
)


def register() -> None:
    """Register the /remote/{node_id} route."""

    @ui.page(Routes.REMOTE_KIOSK)
    def remote_kiosk_page(node_id: str, back: str = Routes.NODES) -> None:
        node_id = unquote(node_id)
        back = unquote(back)

        theme.apply_theme()

        mgr = manager.try_get_instance()
        if not mgr:
            _render_not_reachable(node_id, back)
            return

        display_url = mgr.get_child_display_url(node_id)
        children = mgr.get_fleet_children(node_id)

        if display_url is None:
            _render_not_reachable(node_id, back)
            return

        _render_display_viewer(node_id, display_url, children, back)


def _render_not_reachable(node_id: str, back: str) -> None:
    """Render an error state when the kiosk display is not reachable."""
    render_viewer_error(node_id, Labels.KIOSK_NOT_REACHABLE, back, icon="cast_connected")


def _render_display_viewer(
    node_id: str,
    display_url: str,
    children: list[str],
    back: str,
) -> None:
    """Render a KasmVNC iframe with a minimal, toggleable top bar.

    The SM provides only the VPN connection and back-navigation.
    All interaction (app launching, session switching, fleet ops)
    happens through the node's own kiosk UI inside the stream.
    """
    ui.add_head_html(viewer_base_css())
    ui.add_head_html(iframe_passthrough_css())

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
            ).props('dense outlined data-testid="drill-down-select"').style(
                f"min-width: 140px; color: {theme.TEXT_PRIMARY};"
            )

        ui.element("div").style("flex: 1;")
        ui.button(
            icon="visibility_off",
            on_click=lambda: toggle_viewer_bar_js(),
        ).props("flat dense round").tooltip("Toggle bar").style(
            f"color: {theme.TEXT_SECONDARY}"
        )

    render_display_iframe(display_url)

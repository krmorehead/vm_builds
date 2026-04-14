"""Unified display console — VNC or web view for any display app.

Routes:
    /console/{node_id}/{app_id}  — renders noVNC canvas or iframe based
                                    on the handler's DisplayType.

The browser connects DIRECTLY to the app's stream endpoint — no nesting.
The SM/CM provides routing and URL discovery but never proxies VNC traffic.

The viewer bar includes an app switcher that shows all other registered
display apps, enabling direct app-to-app transitions without navigating
back to the node detail page. Conflict resolution is handled automatically
by the DisplayTransferService.
"""

from __future__ import annotations

from html import escape as html_escape
from urllib.parse import quote, unquote

from nicegui import ui

from scripts.webui import manager, theme
from scripts.webui.data import (
    DISPLAY_APP_CONFIGS, Labels, Routes, console_url,
)
from scripts.webui.display_transfer import DisplayType
from scripts.webui.pages.vnc_shared import (
    VIEWER_BAR_HEIGHT, mount_static, pointer_passthrough_css,
    render_app_console_links, render_viewer_error, render_vnc_canvas,
    viewer_base_css,
)


def _viewer_bar(
    label: str,
    node_id: str,
    icon: str,
    back: str,
    *,
    current_app_id: str = "",
    show_kiosk_button: bool = False,
    show_status_dot: bool = False,
) -> None:
    """Render the top navigation bar shared across all console views.

    When current_app_id is set, renders app switcher buttons for all other
    registered display apps, enabling direct transitions.
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

        if show_kiosk_button:
            kiosk_back = quote(console_url(node_id, current_app_id, back=back))
            kiosk_target = Routes.REMOTE_KIOSK.replace("{node_id}", node_id) + f"?back={kiosk_back}"
            ui.button(
                Labels.OPEN_KIOSK, icon="home",
                on_click=lambda t=kiosk_target: ui.navigate.to(t),
            ).props("flat dense").style(f"color: {theme.ACCENT}")

        if current_app_id:
            render_app_console_links(node_id, back, skip=current_app_id)

        if show_status_dot:
            ui.element("div").props('id="vnc-status-dot"')


def register() -> None:
    """Register the /console/{node_id}/{app_id} route."""
    mount_static()

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

        handler = mgr.display_transfer.get_handler(app_id)
        if not handler:
            _render_error(label, node_id, back, f"{Labels.NO_HANDLER} for {app_id!r}")
            return

        viewstream_url = mgr.get_guest_viewstream_url(node_id, app_id)
        if not viewstream_url:
            _render_error(label, node_id, back, f"{Labels.HOST_UNREACHABLE}: {node_id}")
            return

        if handler.display_type is DisplayType.VNC:
            _render_vnc_console(node_id, app_id, label, icon, viewstream_url, back)
        else:
            _render_web_console(node_id, app_id, label, icon, viewstream_url, back)


def _render_error(
    label: str, node_id: str, back: str, message: str,
) -> None:
    """Render an error when the console cannot be reached."""
    render_viewer_error(f"{label} on {node_id}", message, back)


def _render_vnc_console(
    node_id: str,
    app_id: str,
    label: str,
    icon: str,
    vnc_url: str,
    back: str,
) -> None:
    """Render a noVNC canvas for a VNC-backed display app."""
    ui.add_head_html(viewer_base_css())

    _viewer_bar(
        label, node_id, icon, back,
        current_app_id=app_id,
        show_kiosk_button=True,
        show_status_dot=True,
    )

    render_vnc_canvas(vnc_url)


def _render_web_console(
    node_id: str,
    app_id: str,
    label: str,
    icon: str,
    web_url: str,
    back: str,
) -> None:
    """Render an iframe for a web-based display app."""
    ui.add_head_html(viewer_base_css())
    ui.add_head_html(pointer_passthrough_css())
    ui.add_head_html(f"""
    <style>
        .web-frame-wrap {{
                        position: fixed; top: {VIEWER_BAR_HEIGHT}; left: 0; right: 0; bottom: 0;
                        z-index: 9990; pointer-events: auto;
                    }}
        .web-frame-wrap iframe {{
            width: 100%; height: 100%; border: none;
        }}
    </style>
    """)

    _viewer_bar(label, node_id, icon, back, current_app_id=app_id)

    ui.add_body_html(
        f'<div class="web-frame-wrap">'
        f'<iframe src="{html_escape(web_url, quote=True)}"></iframe>'
        f'</div>'
    )
    ui.add_body_html("""
    <script>
    (function() {
        function reposition() {
            var wrap = document.querySelector('.web-frame-wrap');
            if (wrap && wrap.nextElementSibling) document.body.appendChild(wrap);
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', reposition);
        } else { reposition(); }
        setTimeout(reposition, 500);
    })();
    </script>
    """)

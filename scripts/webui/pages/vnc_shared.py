"""Shared VNC viewer utilities for noVNC pages (remote_kiosk, console).

Provides CSS for the VNC canvas, status dot, and viewer bar, plus the
JavaScript initialization snippet that connects noVNC RFB to the canvas.

Also provides ``render_app_console_links()`` — a shared component that
renders compact icon-links for all registered display apps, enabling
direct app-to-app transitions from any viewer page.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from scripts.webui import theme

_NOVNC_DIR = Path(__file__).resolve().parent.parent / "static" / "noVNC"
VIEWER_BAR_HEIGHT = "40px"
_VNC_INIT_MAX_RETRIES = 50


def mount_static() -> None:
    """Serve noVNC JS assets if present on disk."""
    if _NOVNC_DIR.is_dir():
        app.add_static_files("/static/noVNC", str(_NOVNC_DIR))


def viewer_base_css() -> str:
    """CSS for the fixed top bar shared across all viewer/console pages."""
    return f"""
    <style>
        body {{ margin: 0; overflow: hidden; }}
        .viewer-bar {{
            position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
            height: {VIEWER_BAR_HEIGHT}; display: flex; align-items: center;
            padding: 0 12px; gap: 8px;
            background: {theme.BG_CARD}; border-bottom: 1px solid {theme.BORDER};
        }}
    </style>
    """


def pointer_passthrough_css() -> str:
    """CSS to keep NiceGUI/Quasar DOM from blocking the VNC canvas.

    NiceGUI's template renders body_html BEFORE <div id="app">, so the
    Quasar app div sits ON TOP of our VNC canvas in DOM order. We kill
    pointer-events on #app and all descendants, then re-enable them on
    the viewer-bar with HIGHER specificity (prefix #app to beat #app *).
    Quasar popups (.q-menu, .q-dialog) are teleported outside #app to
    <body>, so they're naturally unaffected by the #app rule.
    """
    return """
    <style>
        #app, #app * {
            pointer-events: none !important;
        }
        #app .viewer-bar,
        #app .viewer-bar * {
            pointer-events: auto !important;
        }
        .q-menu, .q-menu *,
        .q-dialog, .q-dialog *,
        .q-select__dialog, .q-select__dialog * {
            pointer-events: auto !important;
        }
    </style>
    """


def vnc_canvas_css() -> str:
    """CSS for the VNC canvas wrapper and the connection status dot."""
    return f"""
    <style>
        .vnc-canvas-wrap {{
            position: fixed; top: {VIEWER_BAR_HEIGHT}; left: 0; right: 0; bottom: 0;
            background: #000; z-index: 9990;
            pointer-events: auto;
        }}
        .vnc-canvas-wrap.overlay-open {{
            pointer-events: none;
        }}
        #vnc-status-dot {{
            width: 10px; height: 10px; border-radius: 50%;
            background: {theme.TEXT_DISABLED};
            margin-left: auto; margin-right: 8px;
        }}
    </style>
    """


def vnc_init_script(vnc_url: str) -> str:
    """JavaScript that initializes noVNC RFB and wires the status dot."""
    return f"""
    <script type="module">
        import RFB from '/static/noVNC/core/rfb.js';

        function setDot(color) {{
            const d = document.getElementById('vnc-status-dot');
            if (d) d.style.background = color;
        }}

        let _vncRetries = 0;
        function initVNC() {{
            const container = document.getElementById('vnc-container');
            if (!container) {{
                if (++_vncRetries < {_VNC_INIT_MAX_RETRIES}) {{
                    setTimeout(initVNC, 100);
                }} else {{
                    console.error('noVNC: vnc-container not found after', _vncRetries, 'retries');
                    setDot('{theme.COLOR_ERROR}');
                }}
                return;
            }}
            const url = '{vnc_url}';
            try {{
                const rfb = new RFB(container, url);
                rfb.scaleViewport = true;
                rfb.resizeSession = true;
                rfb.showDotCursor = true;
                rfb.addEventListener('connect', () => {{
                    setDot('{theme.COLOR_SUCCESS}');
                    rfb.focus();
                }});
                rfb.addEventListener('disconnect', (e) => {{
                    setDot(e.detail.clean ? '{theme.COLOR_WARNING}' : '{theme.COLOR_ERROR}');
                }});
            }} catch(err) {{
                setDot('{theme.COLOR_ERROR}');
                console.error('noVNC connection failed:', err);
            }}
        }}
        initVNC();
    </script>
    """


def _overlay_guard_script() -> str:
    """JS that handles VNC container positioning and overlay management.

    1. Moves .vnc-canvas-wrap to the END of <body> so it paints after
       #app in the default stacking order. NiceGUI's add_body_html()
       places content BEFORE #app; we need it AFTER.
    2. Watches for Quasar overlays (.q-menu, .q-dialog) and temporarily
       disables VNC pointer capture so dropdown clicks reach the popup.
    """
    return """
    <script>
    (function() {
        function reposition() {
            var wrap = document.querySelector('.vnc-canvas-wrap');
            if (wrap && wrap.nextElementSibling) {
                document.body.appendChild(wrap);
            }
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', reposition);
        } else {
            reposition();
        }
        setTimeout(reposition, 500);

        const OVERLAY_SEL = '.q-menu, .q-dialog';
        function findWrap() {
            return document.querySelector('.vnc-canvas-wrap');
        }
        function sync() {
            var wrap = findWrap();
            if (!wrap) return;
            var open = document.querySelectorAll(OVERLAY_SEL).length > 0;
            wrap.classList.toggle('overlay-open', open);
        }
        new MutationObserver(sync).observe(document.body, {
            childList: true, subtree: true,
        });
    })();
    </script>
    """


def render_vnc_canvas(vnc_url: str) -> None:
    """Emit the CSS, canvas container, and init script for a VNC viewer.

    The VNC container div is injected directly into <body> via
    ui.add_body_html() so it lives OUTSIDE NiceGUI's .nicegui-content
    wrapper tree. This is critical — NiceGUI's Vue/Quasar framework
    captures mouse events on elements inside its DOM tree, preventing
    the noVNC canvas from receiving clicks. By placing the container
    as a direct child of <body>, noVNC gets unfiltered mouse/keyboard
    input and the remote desktop is fully interactive.
    """
    ui.add_head_html(pointer_passthrough_css())
    ui.add_head_html(vnc_canvas_css())
    ui.add_body_html(f"""
    <div class="vnc-canvas-wrap">
        <div id="vnc-container" style="width: 100%; height: 100%;"></div>
    </div>
    """)
    ui.add_body_html(vnc_init_script(vnc_url))
    ui.add_body_html(_overlay_guard_script())


def render_viewer_error(
    label: str,
    message: str,
    back: str,
    *,
    icon: str = "error_outline",
) -> None:
    """Render a centered error state with back button — shared by all viewer pages."""
    from scripts.webui.data import Labels

    ui.add_head_html(viewer_base_css())

    with ui.element("div").classes("viewer-bar"):
        ui.button(
            icon="arrow_back", on_click=lambda: ui.navigate.to(back),
        ).props("flat dense round").style(f"color: {theme.ACCENT}")
        ui.label(label).style(f"color: {theme.TEXT_PRIMARY}; font-weight: 600;")

    with ui.column().classes(
        "w-full items-center justify-center"
    ).style(f"margin-top: {VIEWER_BAR_HEIGHT};"):
        ui.icon(icon).style(
            f"font-size: 64px; color: {theme.TEXT_DISABLED};"
        )
        ui.label(message).style(
            f"color: {theme.TEXT_DISABLED}; font-size: 18px; margin-top: 16px;"
        )
        ui.button(
            Labels.GO_BACK, icon="arrow_back",
            on_click=lambda: ui.navigate.to(back),
        ).classes("action-btn mt-4")


def render_app_console_links(
    node_id: str,
    back: str,
    *,
    skip: str = "kiosk",
) -> None:
    """Render compact icon-links for registered display apps.

    Used by viewer bars, fleet lists, and node detail pages to provide
    direct app-to-app transitions. Iterates DISPLAY_APP_CONFIGS (the
    single source of truth) and renders a tooltip + icon link for each
    app except the one specified by ``skip``.
    """
    from scripts.webui.data import (
        DISPLAY_APP_CONFIGS, Labels, console_url, display_icon,
    )

    for aid, cfg in DISPLAY_APP_CONFIGS.items():
        if aid == skip:
            continue
        icon_name = display_icon("vnc" if cfg.handler_type != "web_view" else "web")
        target = console_url(node_id, aid, back=back)
        with ui.link(target=target).style("text-decoration: none;").on(
            "click.stop", lambda: None,
        ):
            ui.tooltip(f"{cfg.label} {Labels.CONSOLE_SUFFIX}")
            ui.icon(icon_name).style(
                f"color: {theme.ACCENT_DIM}; cursor: pointer; font-size: 18px;"
            )

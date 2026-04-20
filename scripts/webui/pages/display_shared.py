"""Shared display viewer utilities for KasmVNC pages (remote_kiosk, console).

Provides CSS for the viewer bar and iframe passthrough, plus shared
components like ``render_app_console_links()`` that renders compact
icon-links for all registered display apps.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import theme

VIEWER_BAR_HEIGHT = "40px"


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


def iframe_passthrough_css() -> str:
    """CSS to keep NiceGUI/Quasar DOM from blocking the display iframe.

    Two problems solved:

    1. **Visual occlusion** — Quasar dark-mode applies opaque backgrounds
       to #app, .q-layout, .q-page-container, etc. These paint over the
       iframe even though the iframe has z-index: 9990.  ``visibility:
       hidden`` on #app removes ALL rendering (background, borders, text)
       while ``visibility: visible`` on .viewer-bar re-shows just the bar.

    2. **Pointer blocking** — NiceGUI's template renders body_html BEFORE
       <div id="app">, so the Quasar app div sits ON TOP of our iframe in
       DOM order. ``pointer-events: none`` lets clicks pass through.
       ``pointer-events: auto`` on the viewer-bar and on Quasar popups
       (teleported outside #app to <body>) re-enables interaction.
    """
    return """
    <style>
        #app {
            visibility: hidden !important;
        }
        #app .viewer-bar,
        #app .viewer-bar * {
            visibility: visible !important;
        }

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
            visibility: visible !important;
        }
    </style>
    """


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


def render_display_iframe(display_url: str) -> None:
    """Render a full-viewport KasmVNC iframe below the viewer bar.

    Shared by both remote_kiosk and console pages. The iframe is placed
    outside NiceGUI's #app div via add_body_html so it receives mouse
    events directly (see iframe_passthrough_css).

    KasmVNC URL parameters:
    - resize=remote: server adjusts display to match client viewport
    - autoconnect=true: skip the connection dialog
    """
    from html import escape as html_escape
    from urllib.parse import urlencode, urlparse

    parsed = urlparse(display_url)
    sep = "&" if parsed.query else "?"
    embed_url = f"{display_url}{sep}{urlencode({'resize': 'remote', 'autoconnect': 'true'})}"

    ui.add_head_html(f"""
    <style>
        .display-frame-wrap {{
            position: fixed; top: {VIEWER_BAR_HEIGHT}; left: 0; right: 0; bottom: 0;
            z-index: 9990; pointer-events: auto;
        }}
        .display-frame-wrap iframe {{
            width: 100%; height: 100%; border: none;
        }}
    </style>
    """)
    ui.add_body_html(
        f'<div class="display-frame-wrap">'
        f'<iframe src="{html_escape(embed_url, quote=True)}"'
        f' allow="clipboard-read; clipboard-write"></iframe>'
        f'</div>'
    )
    ui.add_body_html("""
    <script>
    (function() {
        function reposition() {
            var wrap = document.querySelector('.display-frame-wrap');
            if (wrap && wrap.nextElementSibling) document.body.appendChild(wrap);
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', reposition);
        } else { reposition(); }
        setTimeout(reposition, 500);
    })();
    </script>
    """)


def toggle_viewer_bar_js() -> None:
    """Toggle the viewer bar visibility for full-screen immersion.

    Shared by console.py and remote_kiosk.py — single source of truth
    for the toggle behavior.
    """
    ui.run_javascript("""
        const bar = document.querySelector('.viewer-bar');
        if (!bar) return;
        const hidden = bar.style.display === 'none';
        bar.style.display = hidden ? '' : 'none';
        const wrap = document.querySelector('.display-frame-wrap');
        if (wrap) wrap.style.top = hidden ? '40px' : '0';
    """)


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
    from scripts.webui.data import DISPLAY_APP_CONFIGS, Labels, console_url

    for aid, cfg in DISPLAY_APP_CONFIGS.items():
        if aid == skip:
            continue
        available = not cfg.target_hosts or node_id in cfg.target_hosts
        icon_name = "web" if cfg.handler_type == "web_view" else "tv"
        if available:
            target = console_url(node_id, aid, back=back)
            with ui.link(target=target).style("text-decoration: none;").on(
                "click.stop", lambda: None,
            ):
                ui.tooltip(f"{cfg.label} {Labels.CONSOLE_SUFFIX}")
                ui.icon(icon_name).style(
                    f"color: {theme.ACCENT_DIM}; cursor: pointer; font-size: 18px;"
                )
        else:
            with ui.element("span"):
                ui.tooltip(f"{cfg.label} — not available on {node_id}")
                ui.icon(icon_name).style(
                    f"color: {theme.TEXT_DISABLED}; font-size: 18px; opacity: 0.4;"
                )

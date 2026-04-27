"""Shared dark theme and reusable UI components for the web UI.

All colors and spacing are defined as Python constants. CSS is generated
from these constants so changes propagate everywhere automatically.
To restyle the app, edit the constants below — the CSS follows.

Design language: Steam library / high-class service dashboard.
Dark blue base with teal accents, reverse-vignette gradient
(lighter edges, darker center), floating cards, glowing buttons.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from nicegui import ui

# ── Color palette ─────────────────────────────────────────────────────
# Dark blue + teal. Edit these to re-skin the entire application.

BG_PAGE_CENTER = "#050c1a"
BG_PAGE_EDGE = "#102845"
BG_SIDEBAR_OUTER = "#0e1f3a"
BG_SIDEBAR_INNER = "#071020"
BG_CARD = "rgba(10, 22, 44, 0.65)"
BG_CARD_DISABLED = "rgba(8, 16, 32, 0.45)"
BG_TABLE = "rgba(8, 18, 36, 0.6)"

TEXT_PRIMARY = "#e0e7ef"
TEXT_SECONDARY = "#7e92a8"
TEXT_DISABLED = "#3e5068"

ACCENT = "#14b8a6"
ACCENT_LIGHT = "#2dd4bf"
ACCENT_DIM = "rgba(20, 184, 166, 0.08)"

BORDER = "rgba(20, 184, 166, 0.06)"
BORDER_HOVER = "rgba(20, 184, 166, 0.25)"
BORDER_DISABLED = "rgba(30, 50, 75, 0.2)"

SHADOW_CARD = "0 8px 32px rgba(0, 0, 0, 0.4), 0 0 1px rgba(20, 184, 166, 0.04)"
SHADOW_HOVER = "0 12px 40px rgba(0, 0, 0, 0.45), 0 0 20px rgba(20, 184, 166, 0.12)"
SHADOW_BUTTON = "0 2px 12px rgba(20, 184, 166, 0.3)"
SHADOW_BUTTON_HOVER = "0 4px 24px rgba(20, 184, 166, 0.5)"

COLOR_SUCCESS = "#34d399"
COLOR_ERROR = "#f87171"
COLOR_WARNING = "#fbbf24"
COLOR_INFO = "#60a5fa"

SIDEBAR_WIDTH = "210px"

# ── Generated CSS ─────────────────────────────────────────────────────

GLOBAL_STYLES = f"""
<style>
body {{
    background: radial-gradient(
        ellipse at 55% 50%,
        {BG_PAGE_CENTER} 0%,
        #081428 40%,
        #0c1e38 70%,
        {BG_PAGE_EDGE} 100%
    ) !important;
    background-attachment: fixed !important;
}}
.nicegui-content {{
    padding: 0 !important;
}}
.q-table__container {{
    background: {BG_TABLE} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 10px !important;
    backdrop-filter: blur(8px);
}}
.q-table thead th {{
    color: {TEXT_SECONDARY} !important;
    font-weight: 500 !important;
    font-size: 0.75rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    border-bottom: 1px solid {ACCENT_DIM} !important;
}}
.q-table tbody td {{
    border-bottom: 1px solid rgba(20, 184, 166, 0.03) !important;
}}
.q-card {{
    background: {BG_CARD} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 12px !important;
    box-shadow: {SHADOW_CARD} !important;
    backdrop-filter: blur(8px);
}}
.q-log {{
    background: rgba(4, 10, 22, 0.8) !important;
    border: 1px solid {BORDER} !important;
    border-radius: 10px !important;
}}
.q-field__control {{
    background: rgba(8, 16, 32, 0.6) !important;
    border-radius: 8px !important;
}}
.action-btn {{
    background: linear-gradient(135deg, #0d9488 0%, {ACCENT} 100%) !important;
    color: white !important;
    border: none !important;
    box-shadow: {SHADOW_BUTTON} !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    text-transform: none !important;
    transition: all 0.25s ease !important;
}}
.action-btn:hover {{
    box-shadow: {SHADOW_BUTTON_HOVER} !important;
    transform: translateY(-1px) !important;
}}
.outline-btn {{
    border: 1px solid rgba(20, 184, 166, 0.3) !important;
    color: {ACCENT} !important;
    background: transparent !important;
    border-radius: 8px !important;
    text-transform: none !important;
    transition: all 0.25s ease !important;
}}
.outline-btn:hover {{
    background: {ACCENT_DIM} !important;
    border-color: rgba(20, 184, 166, 0.5) !important;
}}
.subtle-btn {{
    color: {TEXT_SECONDARY} !important;
    background: transparent !important;
    border-radius: 8px !important;
    text-transform: none !important;
}}
.subtle-btn:hover {{
    background: rgba(14, 31, 58, 0.6) !important;
    color: {TEXT_PRIMARY} !important;
}}
</style>
"""

HOVER_CARD_STYLES = f"""
<style>
.hub-card {{
    transition: all 0.3s ease;
}}
.hub-card:hover {{
    background: rgba(14, 30, 58, 0.8) !important;
    border-color: {BORDER_HOVER} !important;
    box-shadow: {SHADOW_HOVER};
    transform: translateY(-2px);
}}
</style>
"""

# ── Status colors lookup ──────────────────────────────────────────────

_STATUS_COLORS = {
    "success": COLOR_SUCCESS,
    "error": COLOR_ERROR,
    "warning": COLOR_WARNING,
    "info": COLOR_INFO,
}


# ── Reusable UI components ───────────────────────────────────────────


def apply_theme() -> None:
    """Apply the shared dark theme to the current page."""
    ui.dark_mode().enable()
    ui.add_head_html(GLOBAL_STYLES)


def page_header(title: str, subtitle: str = "") -> None:
    """Render a styled page header."""
    with ui.element("header").classes("w-full text-center py-5"):
        ui.label(title).classes("text-2xl font-light tracking-wide").style(
            f"color: {TEXT_PRIMARY}"
        )
        if subtitle:
            ui.label(subtitle).classes("text-sm mt-1").style(
                f"color: {TEXT_SECONDARY}"
            )


def _render_sidebar(items: list, title: str, active: str) -> None:
    """Shared sidebar renderer for both SuperManager and Cluster Manager."""
    sidebar_bg = (
        f"background: linear-gradient(90deg, {BG_SIDEBAR_OUTER} 0%, "
        f"{BG_SIDEBAR_INNER} 100%); "
        f"border-right: 1px solid {ACCENT_DIM};"
    )
    with ui.left_drawer(value=True).props("breakpoint=800 bordered").style(
        f"{sidebar_bg} width: {SIDEBAR_WIDTH}"
    ):
        ui.label(title).classes("text-lg font-medium text-center py-4").style(
            f"color: {TEXT_PRIMARY}"
        )
        ui.separator().style(f"background: {ACCENT_DIM}")
        for label, path, icon in items:
            is_active = active == label.lower().replace(" ", "")
            color = ACCENT if is_active else TEXT_SECONDARY
            bg = ACCENT_DIM if is_active else "transparent"
            border_left = f"3px solid {ACCENT}" if is_active else "3px solid transparent"
            ui.button(
                label,
                icon=icon,
                on_click=lambda p=path: ui.navigate.to(p),
            ).props("flat align=left").classes("w-full justify-start").style(
                f"color: {color}; font-size: 0.85rem; "
                f"background: {bg}; border-left: {border_left}; "
                "border-radius: 0; transition: all 0.2s ease;"
            )


def nav_sidebar(active: str = "") -> None:
    """Render the SuperManager left navigation sidebar."""
    from scripts.webui.data import Labels, NAV_SECTIONS

    _render_sidebar(NAV_SECTIONS, Labels.APP_TITLE, active)


def cluster_nav_sidebar(active: str = "") -> None:
    """Render the Cluster Manager sidebar (only pages registered on the kiosk)."""
    from scripts.webui.data import CLUSTER_NAV_SECTIONS

    _render_sidebar(CLUSTER_NAV_SECTIONS, "Cluster Manager", active)


def section_label(text: str) -> None:
    """Render a section divider label."""
    ui.label(text).classes("text-xs uppercase tracking-widest mt-4 mb-2").style(
        f"color: {ACCENT}; letter-spacing: 0.08em; opacity: 0.7;"
    )


def card_title(text: str) -> ui.label:
    """Render a card heading. Returns the label for optional chaining."""
    return ui.label(text).classes("text-base font-semibold").style(
        f"color: {TEXT_PRIMARY}"
    )


def card_subtitle(text: str) -> ui.label:
    """Render muted card subtitle text."""
    return ui.label(text).classes("text-xs mb-2").style(
        f"color: {TEXT_SECONDARY}"
    )


def muted_text(text: str) -> ui.label:
    """Render small muted helper text."""
    return ui.label(text).classes("text-xs mt-2").style(
        f"color: {TEXT_DISABLED}"
    )


def status_text(label: ui.label, text: str, status: str) -> None:
    """Set text and color on a label based on status type."""
    label.text = text
    label.style(f"color: {_STATUS_COLORS.get(status, TEXT_SECONDARY)}")


@contextmanager
def page_shell(active: str) -> Generator[ui.column, None, None]:
    """Apply theme, render sidebar, and yield a centered content column.

    Reduces boilerplate across all page modules. Usage::

        with theme.page_shell("hosts") as content:
            theme.page_header("Hosts")
            ...
    """
    apply_theme()
    nav_sidebar(active=active)
    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4") as col:
        yield col


@contextmanager
def cluster_page_shell(active: str) -> Generator[ui.column, None, None]:
    """Like page_shell() but with the Cluster Manager sidebar.

    Only shows pages that exist on the kiosk_server (fleet, hub,
    bridge, mesh, router, containers). Prevents 404 links.
    """
    apply_theme()
    cluster_nav_sidebar(active=active)
    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4") as col:
        yield col


# ── Fleet monitoring components ──────────────────────────────────────

_USAGE_COLORS = {
    "ok": COLOR_SUCCESS,
    "warning": COLOR_WARNING,
    "critical": COLOR_ERROR,
}

_SEVERITY_COLORS = {
    "critical": COLOR_ERROR,
    "warning": COLOR_WARNING,
}

_SEVERITY_ICONS = {
    "critical": "error",
    "warning": "warning",
}


def usage_color(level: str) -> str:
    """Return hex color for a usage level (ok/warning/critical)."""
    return _USAGE_COLORS.get(level, TEXT_SECONDARY)


def severity_color(severity: str) -> str:
    """Return hex color for an alert severity."""
    return _SEVERITY_COLORS.get(severity, TEXT_SECONDARY)


def severity_icon(severity: str) -> str:
    """Return material icon name for an alert severity."""
    return _SEVERITY_ICONS.get(severity, "info")


def health_score_color(score: int) -> str:
    """Color for a 0-100 health score."""
    if score >= 80:
        return COLOR_SUCCESS
    if score >= 50:
        return COLOR_WARNING
    return COLOR_ERROR


def metric_bar(label: str, value: float, level: str) -> None:
    """Render a labeled linear progress bar with color-coded level."""
    color = usage_color(level)
    with ui.row().classes("items-center gap-2 w-full"):
        ui.label(label).classes("text-xs w-12").style(f"color: {TEXT_SECONDARY}")
        ui.linear_progress(
            value=value / 100,
            show_value=False,
        ).classes("flex-1").props(f'color="{color}"')
        ui.label(f"{value:.0f}%").classes("text-xs w-10 text-right font-mono").style(
            f"color: {color}"
        )


def status_color(status: str) -> str:
    """Return the semantic color hex for a host status string."""
    if status == "online":
        return COLOR_SUCCESS
    if status in ("stale", "reachable"):
        return COLOR_WARNING
    if status in ("offline", "unreachable"):
        return COLOR_ERROR
    return TEXT_DISABLED


def status_dot(status: str) -> ui.icon:
    """Render a small colored status dot icon.

    Color semantics:
    - online → green (healthy, heartbeat active)
    - reachable → orange (PVE API or NM reachable, but no heartbeat)
    - stale → orange (heartbeat was active, now stale)
    - unreachable → red (PVE API and NM not responding)
    - offline → red (heartbeat reports offline)
    - unknown / anything else → grey (not probed yet)
    """
    if status == "online":
        return ui.icon("circle", size="xs").style(f"color: {COLOR_SUCCESS}")
    if status in ("stale", "reachable"):
        return ui.icon("radio_button_unchecked", size="xs").style(f"color: {COLOR_WARNING}")
    if status in ("offline", "unreachable"):
        return ui.icon("circle", size="xs").style(f"color: {COLOR_ERROR}")
    return ui.icon("circle", size="xs").style(f"color: {TEXT_DISABLED}")


def stat_value(value: str, label: str) -> None:
    """Render a large stat value with a muted label underneath."""
    with ui.column().classes("items-center gap-0"):
        ui.label(value).classes("text-2xl font-bold font-mono").style(
            f"color: {TEXT_PRIMARY}"
        )
        ui.label(label).classes("text-xs uppercase tracking-wider").style(
            f"color: {TEXT_SECONDARY}"
        )


# ── Infrastructure components ────────────────────────────────────────

_SIGNAL_COLORS = {
    "excellent": COLOR_SUCCESS,
    "good": "#4ade80",
    "fair": COLOR_WARNING,
    "weak": "#fb923c",
    "poor": COLOR_ERROR,
}


def signal_color(quality: str) -> str:
    """Return a hex color for a signal quality label."""
    return _SIGNAL_COLORS.get(quality, TEXT_SECONDARY)


def signal_gauge(dbm: int | None, size: str = "md") -> None:
    """Render a signal strength gauge with dBm value and quality label."""
    from scripts.webui.heartbeat import signal_percentage, signal_quality

    if dbm is None:
        with ui.column().classes("items-center gap-0"):
            ui.icon("signal_wifi_off", size=size).style(f"color: {TEXT_DISABLED}")
            ui.label("--").classes("text-xs font-mono").style(f"color: {TEXT_DISABLED}")
        return

    quality = signal_quality(dbm)
    pct = signal_percentage(dbm)
    color = signal_color(quality)

    with ui.column().classes("items-center gap-0"):
        ui.circular_progress(
            value=pct / 100,
            show_value=False,
            size=size,
        ).props(f'color="{color}" thickness=0.25')
        ui.label(f"{dbm} dBm").classes("text-xs font-mono mt-1").style(
            f"color: {color}"
        )
        ui.label(quality.capitalize()).classes("text-xs").style(
            f"color: {TEXT_SECONDARY}"
        )


def link_status_banner(
    connected: bool, role: str = "", uptime_seconds: int = 0,
    signal_dbm: int | None = None,
) -> None:
    """Render a full-width status banner for bridge/mesh connections."""
    from scripts.webui.data import format_uptime

    if connected:
        icon_name = "link"
        status_label = "Connected"
        border_color = COLOR_SUCCESS
        text_color = COLOR_SUCCESS
    else:
        icon_name = "link_off"
        status_label = "Disconnected"
        border_color = COLOR_ERROR
        text_color = COLOR_ERROR

    with ui.card().classes("w-full").style(
        f"border-left: 4px solid {border_color} !important"
    ):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-4"):
            with ui.row().classes("items-center gap-3"):
                ui.icon(icon_name, size="md").style(f"color: {text_color}")
                with ui.column().classes("gap-0"):
                    ui.label(status_label).classes("text-lg font-semibold").style(
                        f"color: {text_color}"
                    )
                    if role:
                        ui.label(f"Role: {role.upper()}").classes(
                            "text-xs font-mono"
                        ).style(f"color: {TEXT_SECONDARY}")

            with ui.row().classes("items-center gap-6"):
                if uptime_seconds > 0:
                    with ui.column().classes("items-center gap-0"):
                        ui.label(format_uptime(uptime_seconds)).classes(
                            "text-sm font-mono"
                        ).style(f"color: {TEXT_PRIMARY}")
                        ui.label("Uptime").classes("text-xs").style(
                            f"color: {TEXT_SECONDARY}"
                        )
                if signal_dbm is not None:
                    signal_gauge(signal_dbm)


def traffic_sparkline(
    tx_data: list[float], rx_data: list[float], height: str = "h-24",
) -> None:
    """Render a dual-line traffic sparkline using echart."""
    if len(tx_data) < 2 and len(rx_data) < 2:
        ui.label("Collecting data...").classes("text-xs").style(
            f"color: {TEXT_DISABLED}"
        )
        return

    max_len = max(len(tx_data), len(rx_data))
    ui.echart({
        "grid": {"top": 10, "bottom": 10, "left": 40, "right": 10},
        "xAxis": {
            "type": "category", "show": False,
            "data": list(range(max_len)),
        },
        "yAxis": {"type": "value", "show": True, "splitLine": {
            "lineStyle": {"color": "rgba(20, 184, 166, 0.05)"},
        }},
        "series": [
            {
                "type": "line", "data": tx_data,
                "smooth": True, "symbol": "none",
                "lineStyle": {"width": 2, "color": ACCENT},
                "areaStyle": {"color": "rgba(20, 184, 166, 0.1)"},
                "name": "TX",
            },
            {
                "type": "line", "data": rx_data,
                "smooth": True, "symbol": "none",
                "lineStyle": {"width": 2, "color": COLOR_INFO},
                "areaStyle": {"color": "rgba(96, 165, 250, 0.1)"},
                "name": "RX",
            },
        ],
        "legend": {
            "data": ["TX", "RX"], "bottom": 0,
            "textStyle": {"color": TEXT_SECONDARY, "fontSize": 10},
        },
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(10, 22, 44, 0.9)",
            "borderColor": ACCENT_DIM,
            "textStyle": {"color": TEXT_PRIMARY, "fontSize": 11},
        },
    }).classes(f"w-full {height}")


def metric_row(label: str, value: str, unit: str = "") -> None:
    """Render a single metric display row: label ... value unit."""
    display = f"{value} {unit}".strip() if unit else value
    with ui.row().classes("w-full items-center justify-between py-1"):
        ui.label(label).classes("text-sm").style(f"color: {TEXT_SECONDARY}")
        ui.label(display).classes("text-sm font-mono").style(
            f"color: {TEXT_PRIMARY}"
        )


def help_tooltip(text: str) -> None:
    """Render a small info icon with a hover tooltip for laymen explanations."""
    with ui.icon("help_outline", size="xs").style(
        f"color: {TEXT_SECONDARY}; cursor: help; opacity: 0.7;"
    ):
        ui.tooltip(text).style(
            f"background: rgba(10, 22, 44, 0.95); color: {TEXT_PRIMARY}; "
            f"border: 1px solid {ACCENT_DIM}; border-radius: 8px; "
            "max-width: 320px; font-size: 0.8rem; padding: 8px 12px; "
            "line-height: 1.4;"
        )


def kiosk_nav_bar() -> None:
    """Render a fixed top bar with Home button for kiosk-mode pages.

    Replaces the full sidebar navigation (which contains links to
    Dashboard, Deploy, etc. that don't exist on the kiosk server).
    """
    bar_style = (
        f"position: fixed; top: 0; left: 0; right: 0; z-index: 9999; "
        f"height: 44px; display: flex; align-items: center; "
        f"padding: 0 16px; gap: 12px; "
        f"background: {BG_CARD}; border-bottom: 1px solid {BORDER};"
    )
    from scripts.webui import manager as _mgr
    from scripts.webui.data import KIOSK_NAV_ITEMS, Labels, Routes

    mgr = _mgr.try_get_instance()

    with ui.element("div").style(bar_style):
        ui.button(
            icon="home", on_click=lambda: ui.navigate.to(Routes.HUB),
        ).props("flat dense round").style(f"color: {ACCENT}")
        ui.label(Labels.HOME_HUB).classes("text-sm").style(f"color: {TEXT_SECONDARY}")
        ui.space()
        for nav_label, nav_path, nav_icon in KIOSK_NAV_ITEMS:
            if nav_path == Routes.FLEET and (not mgr or not mgr.supports_fleet):
                continue
            ui.button(
                nav_label, icon=nav_icon,
                on_click=lambda p=nav_path: ui.navigate.to(p),
            ).props("flat dense").classes("text-xs").style(f"color: {TEXT_SECONDARY}")
    ui.add_head_html("""<style>.kiosk-body-offset { padding-top: 52px !important; }</style>""")


@contextmanager
def kiosk_page_shell(page_name: str = "") -> Generator[ui.column, None, None]:
    """Apply theme, render kiosk nav bar, and yield a centered content column.

    Like page_shell() but uses a slim home bar instead of the full sidebar.
    """
    apply_theme()
    kiosk_nav_bar()
    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4 kiosk-body-offset") as col:
        yield col


def connection_indicator(status: str) -> None:
    """Render an animated status indicator dot."""
    if status == "connected":
        color = COLOR_SUCCESS
        animation = "animation: pulse-green 2s infinite;"
    elif status == "pairing":
        color = COLOR_WARNING
        animation = "animation: pulse-yellow 1s infinite;"
    else:
        color = COLOR_ERROR
        animation = ""

    pulse_css = """
    <style>
    @keyframes pulse-green {
        0%, 100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.6); }
        50% { box-shadow: 0 0 0 6px rgba(52, 211, 153, 0); }
    }
    @keyframes pulse-yellow {
        0%, 100% { box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.6); }
        50% { box-shadow: 0 0 0 6px rgba(251, 191, 36, 0); }
    }
    </style>
    """
    ui.add_head_html(pulse_css)
    ui.element("div").style(
        f"width: 10px; height: 10px; border-radius: 50%; "
        f"background: {color}; {animation}"
    )

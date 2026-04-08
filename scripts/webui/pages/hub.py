"""Kiosk Home Hub page — service launcher dashboard for TV display.

Renders a responsive grid of service cards. Each card links to a service
URL when available, or shows as disabled when the URL is empty.
External services route through /view?url=... so the kiosk always has
a "Back to Hub" button (Chromium --kiosk has no navigation controls).
"""

from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import (
    DISPLAY_APPS, INTERNAL_PAGES, HubService, PageTitles, Routes,
    get_hub_services, load_kiosk_config,
)


def render_hub(urls: dict[str, str] | None = None) -> None:
    """Render the Home Hub dashboard. Reusable by both the full app and kiosk server."""
    if urls is None:
        urls = load_kiosk_config()

    services = get_hub_services()
    current_section = ""

    with ui.column().classes("w-full max-w-[1200px] mx-auto px-6 py-6 gap-5"):
        theme.page_header(PageTitles.HUB, "Entertainment, settings & monitoring")

        for svc in services:
            if svc.section != current_section:
                current_section = svc.section
                theme.section_label(current_section)

            url = urls.get(svc.url_key, "")
            _render_card(svc, url)

        ui.label("Home Hub  ·  Powered by Proxmox VE").classes(
            "text-center text-xs py-4"
        ).style(f"color: {theme.TEXT_DISABLED}")


def _render_card(svc: HubService, url: str) -> None:
    """Render a single service card.

    Infrastructure cards (url_key in INTERNAL_PAGES) navigate internally
    and are always enabled. External service cards link out when a URL
    is configured, otherwise show as disabled.
    """
    internal_path = INTERNAL_PAGES.get(svc.url_key)

    base_style = (
        "border-radius: 10px; padding: 1rem 1.25rem; "
        "display: flex; align-items: center; gap: 1rem; "
        "text-decoration: none; min-height: 72px; "
    )
    enabled_style = (
        f"background: {theme.BG_CARD}; border: 1px solid {theme.BORDER}; "
        f"{base_style}"
        "cursor: pointer; transition: all 0.25s ease;"
    )

    display_app = DISPLAY_APPS.get(svc.url_key)

    if internal_path:
        with ui.element("div").style(enabled_style).classes(
            "hub-card w-full"
        ).on("click", lambda p=internal_path: ui.navigate.to(p)):
            _card_content(svc, available=True)
    elif display_app:
        launch_url = (
            f"/launch?vmid={display_app['vmid']}"
            f"&title={quote(svc.title, safe='')}"
            f"&url_key={quote(svc.url_key, safe='')}"
        )
        with ui.element("div").style(enabled_style).classes(
            "hub-card w-full"
        ).on("click", lambda v=launch_url: ui.navigate.to(v)):
            _card_content(svc, available=True, badge_label="Launch")
    elif url:
        viewer_url = f"/view?url={quote(url, safe='')}&title={quote(svc.title, safe='')}"
        with ui.element("div").style(enabled_style).classes(
            "hub-card w-full"
        ).on("click", lambda v=viewer_url: ui.navigate.to(v)):
            _card_content(svc, available=True)
    else:
        disabled_style = (
            f"background: {theme.BG_CARD_DISABLED}; "
            f"border: 1px solid {theme.BORDER_DISABLED}; "
            f"{base_style}"
            "opacity: 0.4; pointer-events: none;"
        )
        with ui.element("div").style(disabled_style).classes("w-full"):
            _card_content(svc, available=False)


def _card_content(
    svc: HubService, available: bool, badge_label: str = "",
) -> None:
    """Render the inner content of a service card."""
    ui.label(svc.icon).classes("text-2xl flex-shrink-0").style("width: 40px; text-align: center;")

    with ui.column().classes("gap-0 flex-1 min-w-0"):
        ui.label(svc.title).classes("text-base font-medium leading-tight").style(
            f"color: {theme.TEXT_PRIMARY}"
        )
        ui.label(svc.description).classes("text-xs leading-snug").style(
            f"color: {theme.TEXT_SECONDARY if available else theme.TEXT_DISABLED}"
        )

    if not available:
        ui.badge("Not available").classes("flex-shrink-0 text-xs").props("outline color=grey")
    elif badge_label:
        ui.badge(badge_label).classes("flex-shrink-0 text-xs").props("outline color=green")
    else:
        ui.badge(svc.tag).classes("flex-shrink-0 text-xs").props("outline color=blue")


def register() -> None:
    @ui.page("/hub")
    def hub_page() -> None:
        theme.apply_theme()
        theme.nav_sidebar(active="homehub")
        ui.add_head_html(theme.HOVER_CARD_STYLES)
        render_hub()

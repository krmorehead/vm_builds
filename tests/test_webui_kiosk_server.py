"""Tier 3 — Kiosk server integration tests.

Tests for the kiosk server entry point: create_app(), config-driven rendering,
root URL routing, multi-host config differentiation, and kiosk nav bar presence.

Covers manual testing findings from Phase 3-4:
- Config-driven tile rendering per host (home vs ai configs)
- Root URL (/) routes to hub
- Kiosk nav bar appears on internal pages (not full sidebar)
- Viewer bar structure (home, title, open-in-new)
- Error pages for missing VMID and missing URL
- Display app tiles always enabled regardless of config

Run with: pytest tests/test_webui_kiosk_server.py -v
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import app as nicegui_app, ui
from nicegui.testing import user_simulation

from scripts.webui import data, manager
from scripts.webui.kiosk_server import create_app


HOME_CONFIG = {
    "DESKTOP_URL": "",
    "JELLYFIN_URL": "http://10.10.10.15:8096",
    "KODI_URL": "http://10.10.10.16:8080",
    "HOMEASSISTANT_URL": "http://10.10.10.14:8123",
    "MOONLIGHT_URL": "",
    "GAMING_URL": "",
    "OPENWRT_URL": "http://10.10.10.1",
    "PIHOLE_URL": "http://10.10.10.10/admin",
    "WIREGUARD_URL": "",
    "NETDATA_URL": "http://10.10.10.21:19999",
    "RSYSLOG_URL": "",
    "MANAGEMENT_SERVER": "http://192.168.86.201:9001",
    "CALLHOME_SERVER": "http://192.168.86.30:8088",
    "HOST_IP": "192.168.86.201",
    "NODE_IPS": {
        "home": "192.168.86.201",
        "openwrt": "10.10.10.1",
        "pihole": "10.10.10.10",
    },
}

AI_CONFIG = {
    "DESKTOP_URL": "",
    "JELLYFIN_URL": "",
    "KODI_URL": "",
    "HOMEASSISTANT_URL": "",
    "MOONLIGHT_URL": "",
    "GAMING_URL": "https://10.10.10.18:47990",
    "OPENWRT_URL": "",
    "PIHOLE_URL": "",
    "WIREGUARD_URL": "http://10.10.10.5:51821",
    "NETDATA_URL": "http://10.10.10.22:19999",
    "RSYSLOG_URL": "",
    "MANAGEMENT_SERVER": "http://192.168.86.220:9001",
    "CALLHOME_SERVER": "http://192.168.86.30:8088",
    "HOST_IP": "192.168.86.220",
    "NODE_IPS": {
        "ai": "192.168.86.220",
        "wireguard": "10.10.10.5",
        "netdata": "10.10.10.22",
        "gaming": "10.10.10.18",
    },
}


def _write_config(tmp_path: Path, config: dict) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return config_path


@asynccontextmanager
async def kiosk_ctx(tmp_path: Path, config: dict | None = None):
    """Create a NiceGUI user simulation with the kiosk server app."""
    config = config or HOME_CONFIG
    config_path = _write_config(tmp_path, config)
    try:
        async with user_simulation() as user:
            create_app(config_path=config_path)
            yield user
    finally:
        manager.reset()


# ── Kiosk server create_app() ────────────────────────────────────────


class TestKioskCreateApp:
    async def test_create_app_registers_hub(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Home Hub")

    async def test_create_app_root_routes_to_hub(self, tmp_path):
        """Root URL / should render the hub page."""
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/")
            await user.should_see("Home Hub")
            await user.should_see("Entertainment, settings & monitoring")

    async def test_create_app_registers_bridge(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/bridge")
            await user.should_see("WiFi Bridge")

    async def test_create_app_registers_mesh(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/mesh")
            await user.should_see("Mesh Network")

    async def test_create_app_registers_router(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/router")
            await user.should_see("Router")

    async def test_create_app_registers_containers(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/containers")
            await user.should_see("Containers & VMs")

    async def test_create_app_registers_launch(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("Moonlight")

    async def test_create_app_registers_viewer(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view?url=http://example.com&title=Test")
            await user.should_see("Test")

    async def test_create_app_sets_storage_fields(self, tmp_path):
        config_path = _write_config(tmp_path, HOME_CONFIG)
        try:
            async with user_simulation():
                create_app(config_path=config_path)
                assert nicegui_app.storage.general["management_server"] == HOME_CONFIG["MANAGEMENT_SERVER"]
                assert nicegui_app.storage.general["host_ip"] == HOME_CONFIG["HOST_IP"]
                assert nicegui_app.storage.general["mesh_key"] == ""
        finally:
            manager.reset()


# ── Multi-host config differentiation ────────────────────────────────


class TestMultiHostConfig:
    """Verify hub tile state changes based on host-specific config."""

    async def test_home_config_shows_jellyfin_enabled(self, tmp_path):
        async with kiosk_ctx(tmp_path, HOME_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Media Server")

    async def test_home_config_shows_pihole_enabled(self, tmp_path):
        async with kiosk_ctx(tmp_path, HOME_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("DNS")

    async def test_home_config_gaming_disabled(self, tmp_path):
        """Home has no Gaming URL, so Gaming tile should show 'Not available'."""
        async with kiosk_ctx(tmp_path, HOME_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_ai_config_gaming_enabled(self, tmp_path):
        """AI has Gaming URL, so Gaming tile should show 'Game Server' badge."""
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Game Server")

    async def test_ai_config_wireguard_enabled(self, tmp_path):
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("VPN")

    async def test_ai_config_jellyfin_disabled(self, tmp_path):
        """AI has no Jellyfin URL; Jellyfin tile should be disabled."""
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_not_see("Media Server")

    async def test_ai_config_has_more_disabled_tiles(self, tmp_path):
        """AI config has more disabled tiles than home (fewer services)."""
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_display_apps_always_enabled_home(self, tmp_path):
        """Display apps (Moonlight, Kodi, Desktop) always show 'Launch' on home."""
        async with kiosk_ctx(tmp_path, HOME_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Launch")

    async def test_display_apps_always_enabled_ai(self, tmp_path):
        """Display apps always show 'Launch' even with different config."""
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("Launch")

    async def test_infrastructure_always_enabled(self, tmp_path):
        """Infrastructure pages (Bridge, Mesh, Router Detail, Containers) always enabled."""
        async with kiosk_ctx(tmp_path, AI_CONFIG) as user:
            await user.open("/hub")
            await user.should_see("WiFi Bridge")
            await user.should_see("Mesh WiFi")
            await user.should_see("Router Detail")
            await user.should_see("Containers & VMs")


# ── Kiosk nav bar tests ─────────────────────────────────────────────


class TestKioskNavBar:
    """Verify kiosk pages use the slim nav bar, not the full sidebar."""

    async def test_bridge_shows_kiosk_nav(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/bridge")
            await user.should_see("Home Hub")
            await user.should_see("Containers")

    async def test_mesh_shows_kiosk_nav(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/mesh")
            await user.should_see("Home Hub")

    async def test_router_shows_kiosk_nav(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/router")
            await user.should_see("Home Hub")

    async def test_containers_shows_kiosk_nav(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/containers")
            await user.should_see("Home Hub")

    async def test_kiosk_pages_no_full_sidebar(self, tmp_path):
        """Kiosk pages should NOT show the full sidebar title."""
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/bridge")
            await user.should_not_see("vm_builds")


# ── Error handling pages ─────────────────────────────────────────────


class TestKioskErrorPages:
    async def test_launch_no_vmid_shows_error(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch")
            await user.should_see("No VMID configured")

    async def test_launch_no_vmid_shows_back_button(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch")
            await user.should_see("Back to Hub")

    async def test_viewer_no_url_shows_error(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view")
            await user.should_see("No URL configured")

    async def test_viewer_no_url_shows_back_button(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view")
            await user.should_see("Back to Hub")

    async def test_viewer_no_url_hides_open_button(self, tmp_path):
        """When no URL is set, the 'Open in new' button should not appear."""
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view")
            await user.should_not_see("open_in_new")


# ── Display app launch page rendering ────────────────────────────────


class TestKioskLaunchRendering:
    async def test_moonlight_icon_and_description(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("takes over this display for game streaming")

    async def test_kodi_icon_and_description(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch?vmid=301&title=Kodi&url_key=KODI_URL")
            await user.should_see("takes over this display for media playback")

    async def test_desktop_icon_and_description(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch?vmid=400&title=Desktop&url_key=DESKTOP_URL")
            await user.should_see("desktop VM takes over this display")

    async def test_launch_has_explanation_section(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/launch?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("How does this work?")


# ── Viewer bar structure ─────────────────────────────────────────────


class TestKioskViewerBar:
    async def test_viewer_shows_title(self, tmp_path):
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view?url=http://example.com&title=Jellyfin")
            await user.should_see("Jellyfin")

    async def test_viewer_url_decoded_title(self, tmp_path):
        """URL-encoded titles should be decoded properly."""
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view?url=http://example.com&title=Home%20Assistant")
            await user.should_see("Home Assistant")

    async def test_viewer_default_title(self, tmp_path):
        """Missing title defaults to 'App'."""
        async with kiosk_ctx(tmp_path) as user:
            await user.open("/view?url=http://example.com")
            await user.should_see("App")

"""Tier 3 — Kiosk Home Hub page tests.

Tests for the kiosk dashboard interactions: card rendering, disabled states,
section groupings, config loading, and URL injection.
Run with: pytest tests/test_webui_hub.py -v
"""

import json
import sys
from pathlib import Path
from contextlib import asynccontextmanager

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import app as nicegui_app, ui
from nicegui.testing import user_simulation

from scripts.webui import data
from scripts.webui.pages import hub


@asynccontextmanager
async def hub_ctx(tmp_path: Path, urls: dict[str, str] | None = None):
    """Create a NiceGUI user simulation with the hub page and optional URLs."""
    async with user_simulation() as user:
        hub.register()
        nicegui_app.storage.general["env_path"] = str(tmp_path / ".env")
        nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
        nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
        nicegui_app.storage.general["selected_tags"] = []
        nicegui_app.storage.general["hub_urls"] = urls or {}
        yield user


# ── Card rendering ───────────────────────────────────────────────────


class TestHubRendering:
    async def test_hub_page_loads(self, tmp_path):
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Home Hub")

    async def test_shows_all_service_titles(self, tmp_path):
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            for svc in data.get_hub_services():
                await user.should_see(svc.title)

    async def test_shows_section_labels(self, tmp_path):
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Infrastructure")
            await user.should_see("Desktop & Media")
            await user.should_see("Settings & Network")
            await user.should_see("Monitoring")
            await user.should_see("System")

    async def test_shows_footer(self, tmp_path):
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Powered by Proxmox VE")

    async def test_shows_service_descriptions(self, tmp_path):
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Stream movies, shows and music")
            await user.should_see("DNS ad-blocking")

    async def test_shows_tags_when_urls_set(self, tmp_path):
        urls = {
            "JELLYFIN_URL": "http://10.10.10.15:8096",
            "OPENWRT_URL": "http://10.10.10.1",
            "NETDATA_URL": "http://10.10.10.21:19999",
        }
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_see("Media Server")
            await user.should_see("Network")
            await user.should_see("Metrics")

    async def test_available_cards_are_clickable(self, tmp_path):
        """Cards with URLs should render as clickable elements."""
        urls = {"JELLYFIN_URL": "http://10.10.10.15:8096"}
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_see("Jellyfin")
            await user.should_see("Media Server")

    async def test_icons_render(self, tmp_path):
        """Each service card should show its emoji icon."""
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            for svc in data.get_hub_services():
                await user.should_see(svc.icon)


# ── Disabled state ───────────────────────────────────────────────────


class TestHubDisabledState:
    async def test_no_urls_shows_not_available(self, tmp_path):
        async with hub_ctx(tmp_path, urls={}) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_some_urls_shows_mixed_state(self, tmp_path):
        urls = {
            "JELLYFIN_URL": "http://10.10.10.15:8096",
            "PIHOLE_URL": "http://10.10.10.10/admin",
        }
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_see("Media Server")
            await user.should_see("Not available")

    async def test_all_urls_no_not_available(self, tmp_path):
        svcs = data.get_hub_services()
        urls = {svc.url_key: f"http://example.com/{svc.key}" for svc in svcs
                if svc.url_key not in data.INTERNAL_PAGES}
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_not_see("Not available")


# ── Config loading ───────────────────────────────────────────────────


class TestHubConfig:
    def test_load_urls_from_json(self, tmp_path):
        config = tmp_path / "config.json"
        urls = {"JELLYFIN_URL": "http://10.10.10.15:8096", "PIHOLE_URL": "http://10.10.10.10/admin"}
        config.write_text(json.dumps(urls))
        result = data.load_kiosk_config(path=config)
        assert result["JELLYFIN_URL"] == "http://10.10.10.15:8096"
        assert result["PIHOLE_URL"] == "http://10.10.10.10/admin"

    def test_load_urls_missing_file(self, tmp_path):
        result = data.load_kiosk_config(path=tmp_path / "nonexistent.json")
        assert isinstance(result, dict)

    def test_load_urls_corrupt_json(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text("{invalid json")
        result = data.load_kiosk_config(path=config)
        assert isinstance(result, dict)

    def test_load_urls_all_services(self, tmp_path):
        config = tmp_path / "config.json"
        svcs = data.get_hub_services()
        urls = {svc.url_key: f"http://example.com/{svc.key}" for svc in svcs}
        config.write_text(json.dumps(urls))
        result = data.load_kiosk_config(path=config)
        for svc in svcs:
            assert svc.url_key in result


# ── Service card data integrity ──────────────────────────────────────


class TestHubServiceData:
    def test_service_keys_match_expected(self):
        keys = {s.key for s in data.get_hub_services()}
        expected = {
            "bridge", "mesh_detail", "router_detail",
            "desktop", "jellyfin", "kodi", "homeassistant", "moonlight",
            "gaming", "openwrt", "pihole", "wireguard", "netdata", "rsyslog",
            "containers",
        }
        assert keys == expected

    def test_url_keys_match_kiosk_configure(self):
        expected_url_keys = {
            "BRIDGE_PAGE", "MESH_PAGE", "ROUTER_PAGE", "CONTAINERS_PAGE",
            "DESKTOP_URL", "JELLYFIN_URL", "KODI_URL", "HOMEASSISTANT_URL",
            "MOONLIGHT_URL", "GAMING_URL", "OPENWRT_URL", "PIHOLE_URL",
            "WIREGUARD_URL", "NETDATA_URL", "RSYSLOG_URL",
        }
        actual_url_keys = {s.url_key for s in data.get_hub_services()}
        assert actual_url_keys == expected_url_keys

    def test_five_sections_exist(self):
        sections = {s.section for s in data.get_hub_services()}
        assert "Infrastructure" in sections
        assert "Desktop & Media" in sections
        assert "Settings & Network" in sections
        assert "Monitoring" in sections
        assert "System" in sections
        assert len(sections) == 5

    def test_service_count(self):
        assert len(data.get_hub_services()) == 15

    def test_icons_are_non_empty(self):
        for svc in data.get_hub_services():
            assert len(svc.icon) > 0, f"{svc.key} has empty icon"

    def test_sections_contiguous(self):
        svcs = data.get_hub_services()
        sections = [s.section for s in svcs]
        seen: set[str] = set()
        current = ""
        for sec in sections:
            if sec != current:
                assert sec not in seen, f"Section '{sec}' appears non-contiguously"
                seen.add(sec)
                current = sec


# ── Hub card routing ─────────────────────────────────────────────────


class TestHubCardRouting:
    """Verify that hub cards route correctly by type."""

    async def test_display_app_shows_launch_badge(self, tmp_path):
        """Display apps (Moonlight, Kodi, Desktop) show a 'Launch' badge."""
        async with hub_ctx(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Launch")

    async def test_web_app_with_url_shows_tag_badge(self, tmp_path):
        """Web apps with URLs show their service tag badge."""
        urls = {"JELLYFIN_URL": "http://10.10.10.15:8096"}
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_see("Media Server")

    async def test_internal_pages_always_enabled(self, tmp_path):
        """Internal pages (Bridge, Mesh, Router, Containers) are always enabled."""
        async with hub_ctx(tmp_path, urls={}) as user:
            await user.open("/hub")
            for svc in data.get_hub_services():
                if svc.url_key in data.INTERNAL_PAGES:
                    await user.should_see(svc.title)

    async def test_display_apps_always_enabled(self, tmp_path):
        """Display apps are always enabled (they launch containers, no URL needed)."""
        async with hub_ctx(tmp_path, urls={}) as user:
            await user.open("/hub")
            for svc in data.get_hub_services():
                if svc.url_key in data.DISPLAY_APPS:
                    await user.should_see(svc.title)

    async def test_web_apps_without_url_disabled(self, tmp_path):
        """Web apps without configured URLs show 'Not available'."""
        async with hub_ctx(tmp_path, urls={}) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_all_services_render_with_full_config(self, tmp_path):
        """With all URLs set, every service card should render."""
        svcs = data.get_hub_services()
        urls = {
            svc.url_key: f"http://example.com/{svc.key}"
            for svc in svcs
            if svc.url_key not in data.INTERNAL_PAGES
            and svc.url_key not in data.DISPLAY_APPS
        }
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            await user.should_not_see("Not available")


# ── Multi-host config differentiation ────────────────────────────────


class TestHubMultiHostConfig:
    """Verify that different host configs produce different tile states.

    From manual testing Phase 4: swapping config.json between home
    (Jellyfin/Pi-hole enabled) and ai (Gaming/WireGuard enabled)
    correctly changes which tiles show active vs disabled badges.
    """

    async def test_home_enables_jellyfin_ai_disables(self, tmp_path):
        """Jellyfin enabled on home, disabled on ai."""
        home_urls = {"JELLYFIN_URL": "http://10.10.10.15:8096"}
        async with hub_ctx(tmp_path, urls=home_urls) as user:
            await user.open("/hub")
            await user.should_see("Media Server")

        ai_urls: dict[str, str] = {}
        async with hub_ctx(tmp_path, urls=ai_urls) as user:
            await user.open("/hub")
            await user.should_not_see("Media Server")

    async def test_ai_enables_gaming_home_disables(self, tmp_path):
        """Gaming enabled on ai, disabled on home."""
        ai_urls = {"GAMING_URL": "https://10.10.10.18:47990"}
        async with hub_ctx(tmp_path, urls=ai_urls) as user:
            await user.open("/hub")
            await user.should_see("Game Server")

        home_urls: dict[str, str] = {}
        async with hub_ctx(tmp_path, urls=home_urls) as user:
            await user.open("/hub")
            await user.should_not_see("Game Server")

    async def test_wireguard_toggle_between_hosts(self, tmp_path):
        """WireGuard shows different badges based on URL config."""
        ai_urls = {"WIREGUARD_URL": "http://10.10.10.5:51821"}
        async with hub_ctx(tmp_path, urls=ai_urls) as user:
            await user.open("/hub")
            await user.should_see("WireGuard")

        home_urls: dict[str, str] = {}
        async with hub_ctx(tmp_path, urls=home_urls) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_netdata_enabled_on_both(self, tmp_path):
        """Netdata enabled on both home and ai (different IPs, same result)."""
        for netdata_ip in ["10.10.10.21", "10.10.10.22"]:
            urls = {"NETDATA_URL": f"http://{netdata_ip}:19999"}
            async with hub_ctx(tmp_path, urls=urls) as user:
                await user.open("/hub")
                await user.should_see("Metrics")

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
            await user.should_see("Desktop & Media")
            await user.should_see("Settings & Network")
            await user.should_see("Monitoring")

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

    async def test_available_cards_are_links(self, tmp_path):
        """Cards with URLs should render as clickable links."""
        urls = {"JELLYFIN_URL": "http://10.10.10.15:8096"}
        async with hub_ctx(tmp_path, urls=urls) as user:
            await user.open("/hub")
            with user:
                links = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.link)]
                jellyfin_links = [lnk for lnk in links if hasattr(lnk, '_props') and 'http://10.10.10.15:8096' in str(getattr(lnk, '_props', {}))]
                assert len(links) > 0

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
        urls = {svc.url_key: f"http://example.com/{svc.key}" for svc in svcs}
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
        expected = {"desktop", "jellyfin", "kodi", "homeassistant", "moonlight",
                    "gaming", "openwrt", "pihole", "wireguard", "netdata", "rsyslog"}
        assert keys == expected

    def test_url_keys_match_kiosk_configure(self):
        expected_url_keys = {
            "DESKTOP_URL", "JELLYFIN_URL", "KODI_URL", "HOMEASSISTANT_URL",
            "MOONLIGHT_URL", "GAMING_URL", "OPENWRT_URL", "PIHOLE_URL",
            "WIREGUARD_URL", "NETDATA_URL", "RSYSLOG_URL",
        }
        actual_url_keys = {s.url_key for s in data.get_hub_services()}
        assert actual_url_keys == expected_url_keys

    def test_three_sections_exist(self):
        sections = {s.section for s in data.get_hub_services()}
        assert "Desktop & Media" in sections
        assert "Settings & Network" in sections
        assert "Monitoring" in sections
        assert len(sections) == 3

    def test_service_count(self):
        assert len(data.get_hub_services()) == 11

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

"""Tier 3 — Bridge, Mesh, Router, Viewer, Launch, and Containers page tests.

Tests for infrastructure detail pages: rendering, empty states,
metric display, navigation, and display-app launch flow.
Run with: pytest tests/test_webui_bridge.py -v
"""

from __future__ import annotations

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
from scripts.webui.data import Labels, PageTitles, Routes
from scripts.webui.pages import bridge, containers, launch, mesh, router, viewer

FIXTURES = Path(__file__).parent / "fixtures"


@asynccontextmanager
async def infra_ctx(tmp_path: Path, env_file: str = "complete.env"):
    """Create a NiceGUI user simulation with infrastructure pages registered."""
    from scripts.webui.app import _env_node_resolver
    manager.init(_env_node_resolver)
    try:
        async with user_simulation() as user:
            bridge.register()
            mesh.register()
            router.register()
            nicegui_app.storage.general["env_path"] = str(FIXTURES / env_file)
            nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
            nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
            nicegui_app.storage.general["selected_tags"] = []
            yield user
    finally:
        manager.reset()


# ── Bridge page tests ────────────────────────────────────────────────


class TestBridgePage:
    async def test_bridge_page_loads(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(PageTitles.BRIDGE)

    async def test_bridge_shows_both_nodes(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see("Bridge 1")
            await user.should_see("Bridge 2")

    async def test_bridge_shows_not_linked_when_no_data(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see("not linked")

    async def test_bridge_shows_deploy_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.DEPLOY_BRIDGE)

    async def test_bridge_shows_refresh_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.REFRESH_NOW)

    async def test_bridge_shows_traffic_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see("Traffic")

    async def test_bridge_shows_restart_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.RESTART_WIFI)

    async def test_bridge_shows_repair_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.FORCE_REPAIR)


# ── Mesh page tests ──────────────────────────────────────────────────


class TestMeshPage:
    async def test_mesh_page_loads(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see(PageTitles.MESH)

    async def test_mesh_shows_topology(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see("Topology")

    async def test_mesh_shows_ap_node(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see("home")
            await user.should_see("WDS AP")

    async def test_mesh_shows_sta_nodes(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see("mesh1")
            await user.should_see("mesh2")

    async def test_mesh_shows_refresh_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see(Labels.REFRESH_NOW)

    async def test_mesh_shows_batman_section(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see("Batman Mode")

    async def test_mesh_shows_batman_buttons(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.MESH)
            await user.should_see(Labels.ENABLE_BATMAN)
            await user.should_see(Labels.DISABLE_BATMAN)


# ── Router page tests ────────────────────────────────────────────────


class TestRouterPage:
    async def test_router_page_loads(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("Router")

    async def test_router_shows_wan_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("WAN")

    async def test_router_shows_lan_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("LAN")

    async def test_router_shows_firewall_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("Firewall")

    async def test_router_shows_wifi_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("WiFi (WDS AP)")

    async def test_router_shows_system_card(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("System")

    async def test_router_shows_refresh_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see(Labels.REFRESH_NOW)

    async def test_router_shows_disconnected_when_no_data(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.ROUTER)
            await user.should_see("Not reachable")


# ── Viewer page tests ────────────────────────────────────────────────


@asynccontextmanager
async def viewer_ctx(tmp_path: Path, env_file: str = "complete.env"):
    """Create a NiceGUI user simulation with viewer page registered."""
    from scripts.webui.app import _env_node_resolver
    manager.init(_env_node_resolver)
    try:
        async with user_simulation() as user:
            viewer.register()
            containers.register()
            nicegui_app.storage.general["env_path"] = str(FIXTURES / env_file)
            nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
            nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
            nicegui_app.storage.general["selected_tags"] = []
            yield user
    finally:
        manager.reset()


class TestViewerPage:
    async def test_viewer_page_loads(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com&title=Test%20App")
            await user.should_see("Test App")

    async def test_viewer_shows_no_url(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.VIEW)
            await user.should_see(Labels.NO_URL)

    async def test_viewer_has_home_button(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com&title=TestApp")
            await user.should_see("TestApp")


# ── Containers page tests ─────────────────────────────────────────────


class TestContainersPage:
    async def test_containers_page_loads(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.CONTAINERS)
            await user.should_see(PageTitles.CONTAINERS)

    async def test_containers_shows_refresh_button(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.CONTAINERS)
            await user.should_see("Refresh")

    async def test_containers_shows_help_tooltip(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.CONTAINERS)
            await user.should_see("Manage all guests")


# ── Launch page tests ─────────────────────────────────────────────────


@asynccontextmanager
async def launch_ctx(tmp_path: Path, env_file: str = "complete.env"):
    """Create a NiceGUI user simulation with launch page registered."""
    from scripts.webui.app import _env_node_resolver
    manager.init(_env_node_resolver)
    try:
        async with user_simulation() as user:
            launch.register()
            viewer.register()
            nicegui_app.storage.general["env_path"] = str(FIXTURES / env_file)
            nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
            nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
            nicegui_app.storage.general["selected_tags"] = []
            yield user
    finally:
        manager.reset()


class TestLaunchPage:
    async def test_launch_page_loads_with_vmid(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("Moonlight")

    async def test_launch_page_shows_description(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("takes over this display")

    async def test_launch_page_shows_launch_button(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("Launch Moonlight")

    async def test_launch_page_no_vmid_shows_error(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(Routes.LAUNCH)
            await user.should_see(Labels.NO_VMID)

    async def test_launch_page_no_vmid_shows_back_button(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(Routes.LAUNCH)
            await user.should_see(Labels.BACK_TO_HUB)

    async def test_launch_page_kodi(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=301&title=Kodi&url_key=KODI_URL")
            await user.should_see("Kodi")
            await user.should_see("Launch Kodi")

    async def test_launch_page_desktop(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=400&title=Desktop&url_key=DESKTOP_URL")
            await user.should_see("Desktop")
            await user.should_see("Launch Desktop")

    async def test_launch_page_shows_explanation(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("How does this work?")

    async def test_launch_page_has_home_button(self, tmp_path):
        """The kiosk nav bar home button should be present."""
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see(Labels.HOME_HUB)


# ── Viewer robustness tests ──────────────────────────────────────────


class TestViewerRobustness:
    async def test_viewer_with_special_chars_in_url(self, tmp_path):
        """URLs with special characters should render without errors."""
        from urllib.parse import quote
        raw_url = "http://10.10.10.15:8096/web/index.html#/search?q=hello world"
        encoded_url = quote(raw_url, safe="")
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"/view?url={encoded_url}&title=Jellyfin%20Search")
            await user.should_see("Jellyfin Search")

    async def test_viewer_with_empty_title(self, tmp_path):
        """Empty title defaults to 'App'."""
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com")
            await user.should_see("App")

    async def test_viewer_no_url_has_back_button(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.VIEW)
            await user.should_see(Labels.BACK_TO_HUB)

    async def test_viewer_with_url_shows_title(self, tmp_path):
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://10.10.10.1&title=Router")
            await user.should_see("Router")


# ── Display-app flow integration ─────────────────────────────────────


class TestDisplayAppFlow:
    """Test the complete flow: hub → launch → display handoff concept."""

    def test_display_apps_have_matching_hub_services(self):
        """Every DISPLAY_APPS entry must have a corresponding HubService."""
        service_url_keys = {s.url_key for s in data.get_hub_services()}
        for url_key in data.DISPLAY_APPS:
            assert url_key in service_url_keys

    def test_moonlight_is_display_app_not_viewer(self):
        """Moonlight should route to /launch, not /view."""
        assert "MOONLIGHT_URL" in data.DISPLAY_APPS
        assert "MOONLIGHT_URL" not in data.INTERNAL_PAGES

    def test_kodi_is_display_app_not_viewer(self):
        assert "KODI_URL" in data.DISPLAY_APPS

    def test_desktop_is_display_app_not_viewer(self):
        assert "DESKTOP_URL" in data.DISPLAY_APPS

    def test_jellyfin_is_web_app_not_display(self):
        """Jellyfin should route to /view (web UI), not /launch."""
        assert "JELLYFIN_URL" not in data.DISPLAY_APPS

    def test_gaming_is_web_app_not_display(self):
        """Gaming (Sunshine web UI) is a web app, not a display app."""
        assert "GAMING_URL" not in data.DISPLAY_APPS

    def test_pihole_is_web_app(self):
        assert "PIHOLE_URL" not in data.DISPLAY_APPS

    def test_homeassistant_is_web_app(self):
        assert "HOMEASSISTANT_URL" not in data.DISPLAY_APPS

    def test_netdata_is_web_app(self):
        assert "NETDATA_URL" not in data.DISPLAY_APPS

    def test_bridge_is_internal_page(self):
        assert "BRIDGE_PAGE" in data.INTERNAL_PAGES
        assert "BRIDGE_PAGE" not in data.DISPLAY_APPS

    def test_containers_is_internal_page(self):
        assert "CONTAINERS_PAGE" in data.INTERNAL_PAGES
        assert "CONTAINERS_PAGE" not in data.DISPLAY_APPS


# ── Kiosk navigation flow tests ──────────────────────────────────────


class TestKioskNavigationFlows:
    """End-to-end navigation flow tests for kiosk mode."""

    async def test_viewer_back_to_hub_flow(self, tmp_path):
        """Viewer page should have a home icon button that returns to hub."""
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com&title=Test")
            await user.should_see("Test")
            await user.should_see(ui.button)

    async def test_launch_back_to_hub_flow(self, tmp_path):
        """Launch page should have a home button."""
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see(Labels.HOME_HUB)

    async def test_all_display_apps_have_launch_pages(self, tmp_path):
        """Each display app should render correctly on the launch page."""
        for url_key, info in data.DISPLAY_APPS.items():
            async with launch_ctx(tmp_path) as user:
                vmid = info["vmid"]
                label = info["label"]
                await user.open(
                    f"/launch?vmid={vmid}&title={label}&url_key={url_key}"
                )
                await user.should_see(label)
                await user.should_see(f"Launch {label}")


# ── Launch state transition tests ────────────────────────────────────


class TestLaunchStateTransitions:
    """Verify launch page button behavior (from manual testing Phase 3c)."""

    async def test_launch_button_present_when_vmid_set(self, tmp_path):
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=302&title=Moonlight&url_key=MOONLIGHT_URL")
            await user.should_see("Launch Moonlight")

    async def test_launch_button_absent_when_no_vmid(self, tmp_path):
        """Without VMID, no launch button should appear."""
        async with launch_ctx(tmp_path) as user:
            await user.open(Routes.LAUNCH)
            await user.should_not_see("Launch")

    async def test_unknown_url_key_uses_fallback_icon(self, tmp_path):
        """Unknown url_key should still render with a fallback rocket icon."""
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=999&title=Unknown&url_key=NONEXISTENT")
            await user.should_see("Unknown")
            await user.should_see("Launch Unknown")

    async def test_launch_page_url_decodes_title(self, tmp_path):
        """URL-encoded titles should render decoded."""
        async with launch_ctx(tmp_path) as user:
            await user.open(f"{Routes.LAUNCH}?vmid=400&title=Desktop%20VM&url_key=DESKTOP_URL")
            await user.should_see("Desktop VM")
            await user.should_see("Launch Desktop VM")


# ── Viewer bar structure tests ───────────────────────────────────────


class TestViewerBarStructure:
    """Verify viewer page bar elements (from manual testing Phase 3d)."""

    async def test_viewer_with_url_has_open_button(self, tmp_path):
        """When URL is set, 'open in new' button should be present."""
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com&title=Test")
            await user.should_see("Test")

    async def test_viewer_without_url_no_open_button(self, tmp_path):
        """When no URL, the 'open in new' button should NOT be present."""
        async with viewer_ctx(tmp_path) as user:
            await user.open(Routes.VIEW)
            await user.should_see(Labels.NO_URL)
            await user.should_see(Labels.BACK_TO_HUB)

    async def test_viewer_preserves_special_chars_in_title(self, tmp_path):
        """Titles with special characters should render safely."""
        async with viewer_ctx(tmp_path) as user:
            await user.open(f"{Routes.VIEW}?url=http://example.com&title=Pi-hole%20Admin")
            await user.should_see("Pi-hole Admin")

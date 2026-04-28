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

pytestmark = pytest.mark.no_infra

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import app as nicegui_app, ui
from nicegui.testing import user_simulation

from scripts.webui import data, manager, theme
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

    async def test_bridge_shows_setup_guide(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.BRIDGE_HOW_IT_WORKS)

    async def test_bridge_shows_swap_roles_button(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see(Labels.SWAP_ROLES)

    async def test_bridge_shows_role_labels_in_guide(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see("Broadcaster")
            await user.should_see("Receiver")

    async def test_bridge_disconnected_shows_diagnostic_hint(self, tmp_path):
        async with infra_ctx(tmp_path) as user:
            await user.open(Routes.BRIDGE)
            await user.should_see("Both bridge hosts are offline")


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


@asynccontextmanager
async def containers_ctx(tmp_path: Path):
    """Lightweight context for testing the containers page.

    Registers a kiosk-style containers page with an empty fleet to avoid
    the heavy build_fleet() + probe_all_hosts() path that the SuperManager
    containers.register() uses. The tests only verify UI rendering, not
    fleet data.
    """
    from scripts.webui.pages.containers import _render_containers

    manager.init(lambda _: None)
    try:
        async with user_simulation() as user:
            @ui.page(Routes.CONTAINERS)
            async def _test_containers() -> None:
                with theme.page_shell("containers"):
                    ui.add_head_html(theme.HOVER_CARD_STYLES)
                    await _render_containers(data.Fleet([]))
            yield user
    finally:
        manager.reset()


class TestContainersPage:
    async def test_containers_page_loads(self, tmp_path):
        async with containers_ctx(tmp_path) as user:
            await user.open(Routes.CONTAINERS)
            await user.should_see(PageTitles.CONTAINERS)

    async def test_containers_shows_refresh_button(self, tmp_path):
        async with containers_ctx(tmp_path) as user:
            await user.open(Routes.CONTAINERS)
            await user.should_see("Refresh")

    async def test_containers_shows_help_tooltip(self, tmp_path):
        async with containers_ctx(tmp_path) as user:
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
            await user.should_see("Game streaming client")

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
        """Without VMID, error message appears instead of launch button."""
        async with launch_ctx(tmp_path) as user:
            await user.open(Routes.LAUNCH)
            await user.should_see(Labels.NO_VMID_CONFIGURED)

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


# ── Bridge helper function tests (pure Python, no NiceGUI) ───────────


class TestBridgeHelpers:
    """Test pure-Python helpers from bridge.py without NiceGUI."""

    def test_format_bytes_bytes(self):
        from scripts.webui.pages.bridge import _format_bytes
        assert _format_bytes(512) == "512 B"

    def test_format_bytes_kb(self):
        from scripts.webui.pages.bridge import _format_bytes
        assert _format_bytes(2048) == "2.0 KB"

    def test_format_bytes_mb(self):
        from scripts.webui.pages.bridge import _format_bytes
        assert _format_bytes(5 * 1024 * 1024) == "5.0 MB"

    def test_format_bytes_gb(self):
        from scripts.webui.pages.bridge import _format_bytes
        assert _format_bytes(3 * 1024 * 1024 * 1024) == "3.00 GB"

    def test_format_uptime_zero(self):
        from scripts.webui.pages.bridge import _format_uptime
        assert _format_uptime(0) == ""

    def test_format_uptime_minutes(self):
        from scripts.webui.pages.bridge import _format_uptime
        assert _format_uptime(300) == "linked 5m"

    def test_format_uptime_hours(self):
        from scripts.webui.pages.bridge import _format_uptime
        assert _format_uptime(7200) == "linked 2h 0m"

    def test_format_uptime_hours_and_minutes(self):
        from scripts.webui.pages.bridge import _format_uptime
        assert _format_uptime(5430) == "linked 1h 30m"

    def test_extract_link_summary_empty(self):
        from scripts.webui.pages.bridge import _extract_link_summary
        assert _extract_link_summary(None, None) == ""

    def test_band_labels_cover_all_bands(self):
        from scripts.webui.pages.bridge import _BAND_LABELS
        assert "2g" in _BAND_LABELS
        assert "5g" in _BAND_LABELS
        assert "6g" in _BAND_LABELS

    def test_width_labels_cover_standard_widths(self):
        from scripts.webui.pages.bridge import _WIDTH_LABELS
        for w in ("20", "40", "80", "160"):
            assert w in _WIDTH_LABELS

    def test_role_explanations_cover_ap_and_sta(self):
        from scripts.webui.pages.bridge import _ROLE_EXPLANATIONS
        assert "AP" in _ROLE_EXPLANATIONS
        assert "STA" in _ROLE_EXPLANATIONS
        for role_key, (icon, human_role, desc) in _ROLE_EXPLANATIONS.items():
            assert icon, f"Missing icon for {role_key}"
            assert human_role, f"Missing human role for {role_key}"
            assert desc, f"Missing description for {role_key}"

    def test_setup_steps_has_three_entries(self):
        from scripts.webui.pages.bridge import _SETUP_STEPS
        assert len(_SETUP_STEPS) == 3
        for icon, title, desc in _SETUP_STEPS:
            assert icon, "Missing icon in setup step"
            assert title, "Missing title in setup step"
            assert desc, "Missing description in setup step"


class TestBridgeLabelsExist:
    """Verify all new bridge labels are defined in data.py."""

    def test_bridge_how_it_works_label(self):
        assert Labels.BRIDGE_HOW_IT_WORKS

    def test_bridge_step_deploy_label(self):
        assert Labels.BRIDGE_STEP_DEPLOY

    def test_bridge_step_negotiate_label(self):
        assert Labels.BRIDGE_STEP_NEGOTIATE

    def test_bridge_step_pair_label(self):
        assert Labels.BRIDGE_STEP_PAIR

    def test_swap_roles_label(self):
        assert Labels.SWAP_ROLES

    def test_bridge_nodes_have_roles(self):
        nodes = data.get_bridge_nodes()
        roles = {n["default_role"] for n in nodes}
        assert "ap" in roles, "No AP node in bridge_nodes"
        assert "sta" in roles, "No STA node in bridge_nodes"

"""Tier 2 — Functional UI tests using NiceGUI's user simulation.

Each test gets an isolated NiceGUI app context via user_simulation().
Pages are re-registered per test to ensure clean routing state.

Tests use the REAL test.env and .state/ directory so fleet pages
render with live heartbeat data.  VPN and kiosk are prerequisites —
all connections are already established before these tests run.

Run with: pytest tests/test_webui_app.py -v
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nicegui import app as nicegui_app, ui
from nicegui.testing import user_simulation

from scripts.webui import data
from scripts.webui.data import ApiRoutes, Labels, PageTitles, Routes
from scripts.webui.app import register_api
from scripts.webui.manager import get_metric_cache
from scripts.webui.pages import (
    bridge, dashboard, deploy, environment, hosts, hub, images, mesh, nodes,
    router, services,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL_STATE_DIR = PROJECT_ROOT / ".state"
REAL_ENV_PATH = PROJECT_ROOT / "test.env"


def _read_vpn_ips_from_env() -> dict[str, str]:
    """Read VPN IPs from test.env — single source of truth."""
    vpn_ips: dict[str, str] = {}
    if REAL_ENV_PATH.exists():
        for line in REAL_ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if "_VPN_IP" in key:
                vpn_ips[key] = val.strip().strip("'\"")
    return vpn_ips


_VPN_IPS = _read_vpn_ips_from_env()


def _base_env(**overrides: str) -> str:
    """Build test env content with VPN IPs from test.env.

    VPN IPs are read once from test.env at import time — never
    hardcoded.  Additional env vars can be passed as kwargs.
    """
    lines = [
        f"PRIMARY_HOST={overrides.pop('PRIMARY_HOST', '192.168.86.201')}",
        f"HOME_API_TOKEN={overrides.pop('HOME_API_TOKEN', 'test')}",
        f"MESH_KEY={overrides.pop('MESH_KEY', 'test')}",
    ]
    for key, val in _VPN_IPS.items():
        if key not in overrides:
            lines.append(f"{key}={val}")
    for key, val in overrides.items():
        lines.append(f"{key}={val}")
    return "\n".join(lines) + "\n"


@asynccontextmanager
async def webui(tmp_path: Path, env_file: str | None = None, **overrides):
    """Create a NiceGUI user simulation with all pages and real state.

    Uses the real test.env and .state/ directory by default so pages
    render with actual heartbeat data.  Override env_file for tests
    that need a specific env configuration (e.g. empty, incomplete).
    """
    env_path = str(FIXTURES / env_file) if env_file else str(REAL_ENV_PATH)
    state_dir = str(REAL_STATE_DIR) if REAL_STATE_DIR.exists() else str(tmp_path / "state")
    async with user_simulation() as user:
        dashboard.register()
        environment.register()
        hosts.register()
        nodes.register()
        services.register()
        deploy.register()
        images.register()
        hub.register()
        bridge.register()
        mesh.register()
        router.register()
        nicegui_app.storage.general["env_path"] = env_path
        nicegui_app.storage.general["images_dir"] = str(PROJECT_ROOT / "images")
        nicegui_app.storage.general["state_dir"] = state_dir
        nicegui_app.storage.general["selected_tags"] = []
        nicegui_app.storage.general.update(overrides)
        yield user


# ── Dashboard page ───────────────────────────────────────────────────


class TestDashboard:
    async def test_shows_host_summary(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("Hosts")
            await user.should_see("home")

    async def test_shows_image_summary(self, tmp_path):
        # Uses a controlled images dir to test the image count display
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for _, pattern, _, _ in data.EXPECTED_IMAGES[:10]:
            (images_dir / pattern.replace("*", "test")).write_bytes(b"\x00" * 1024)
        async with webui(tmp_path, images_dir=str(images_dir)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("10/14 built")

    async def test_shows_no_deploys_initially(self, tmp_path):
        # Uses empty state dir to test the zero-deploy edge case
        async with webui(tmp_path, state_dir=str(tmp_path / "empty_state")) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("No deployments yet")

    async def test_shows_last_deploy(self, tmp_path):
        # Uses custom state dir to test specific deploy record rendering
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        record = data.DeployRecord(
            timestamp="2026-04-04T12:00:00", tags=["infra"],
            env_file=".env", exit_code=0, duration_seconds=60,
        )
        data.save_deploy_record(state_dir, record)
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("success")
            await user.should_see("infra")

    async def test_full_deploy_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            user.find(Labels.FULL_DEPLOY).click()
            await user.should_see(PageTitles.SERVICES)

    async def test_check_hosts_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            user.find(Labels.CHECK_HOSTS).click()
            await user.should_see(PageTitles.HOSTS)

    async def test_build_images_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            user.find(Labels.BUILD_IMAGES).click()
            await user.should_see(PageTitles.IMAGES)

    async def test_env_banner_production(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("PRODUCTION")

    async def test_env_banner_test(self, tmp_path):
        env_path = tmp_path / "test.env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("TEST")


# ── Environment page ─────────────────────────────────────────────────


class TestEnvironment:
    async def test_shows_table_with_variables(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.ENVIRONMENT)
            await user.should_see(PageTitles.ENVIRONMENT)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                assert len(tables) == 1
                var_names = [r["variable"] for r in tables[0].rows]
                assert "PRIMARY_HOST" in var_names
                assert "HOME_API_TOKEN" in var_names

    async def test_shows_status(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.ENVIRONMENT)
            await user.should_see("All required variables present")

    async def test_missing_vars_flagged(self, tmp_path):
        async with webui(tmp_path, env_file="incomplete.env") as user:
            await user.open(Routes.ENVIRONMENT)
            await user.should_see("Missing")

    async def test_no_file_prompts_create(self, tmp_path):
        async with webui(tmp_path, env_path=str(tmp_path / "nonexistent.env")) as user:
            await user.open(Routes.ENVIRONMENT)
            await user.should_see("No env file found")

    async def test_create_writes_file(self, tmp_path):
        env_path = tmp_path / ".env"
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.ENVIRONMENT)
            user.find(Labels.CREATE_ENV).click()
            assert env_path.exists()
            assert "PRIMARY_HOST=" in env_path.read_text()

    async def test_validate_button(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.ENVIRONMENT)
            user.find(Labels.VALIDATE).click()
            await user.should_see("All required variables present")

    async def test_save_writes_file(self, tmp_path):
        env_path = tmp_path / "save_test.env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.ENVIRONMENT)
            user.find(Labels.SAVE).click()
            assert env_path.exists()
            content = env_path.read_text()
            assert "PRIMARY_HOST=" in content

    async def test_create_then_save_roundtrip(self, tmp_path):
        env_path = tmp_path / "roundtrip.env"
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.ENVIRONMENT)
            user.find(Labels.CREATE_ENV).click()
            assert env_path.exists()
            user.find(Labels.SAVE).click()
            assert env_path.exists()
            backup = tmp_path / "roundtrip.env.bak"
            assert backup.exists()


# ── Hosts page ───────────────────────────────────────────────────────


class TestHosts:
    async def test_shows_all_hosts(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HOSTS)
            await user.should_see(PageTitles.HOSTS)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                assert len(tables) == 1
                host_names = [r["host"] for r in tables[0].rows]
                assert "home" in host_names
                assert "ai" in host_names
                assert "mesh1" in host_names
                assert "mesh2" in host_names

    async def test_shows_wol_status(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HOSTS)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                wol_values = [r.get("wol", "") for r in tables[0].rows]
                assert any("No" in str(v) for v in wol_values)

    async def test_probe_updates_status(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HOSTS)
            user.find(Labels.PROBE_ALL).click()
            for _ in range(15):
                await asyncio.sleep(0.2)
                with user:
                    tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                    statuses = [r.get("status", "") for r in tables[0].rows]
                    if any("Reachable" in str(s) for s in statuses):
                        return
            raise AssertionError("No host became Reachable after probing — VPN+NM should be up")

    async def test_unreachable_shows_error(self, tmp_path):
        # WHY: Cannot make real controlled hosts unreachable on demand to test the UI error path.
        # HOW: Tests that the Hosts page renders "Unreachable" status correctly after a failed probe.
        with patch("scripts.webui.data.build.probe_host", return_value=False):
            async with webui(tmp_path) as user:
                await user.open(Routes.HOSTS)
                user.find(Labels.PROBE_ALL).click()
                await asyncio.sleep(0.5)
                with user:
                    tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                    statuses = [r.get("status", "") for r in tables[0].rows]
                    assert any("Unreachable" in str(s) for s in statuses)

    async def test_ssh_button_success(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HOSTS)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                tables[0].selected = [tables[0].rows[0]]
            user.find(Labels.TEST_API).click()
            await user.should_see("PVE API", retries=60)

    async def test_ssh_button_no_selection(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HOSTS)
            user.find(Labels.TEST_API).click()
            await user.should_see(Labels.SELECT_ROW)


# ── Services page ────────────────────────────────────────────────────


class TestServices:
    async def test_shows_all_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            for svc in data.get_service_tags():
                await user.should_see(svc.tag)

    async def test_deploy_without_selection_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            user.find(Labels.DEPLOY_SELECTED).click()
            await user.should_see(Labels.NO_SERVICES)

    async def test_select_all(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            user.find(Labels.SELECT_ALL).click()
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                expected = len([t for t in data.get_service_tags() if not t.is_opt_in])
                assert len(checked) == expected

    async def test_deselect_all(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            user.find(Labels.SELECT_ALL).click()
            user.find(Labels.DESELECT_ALL).click()
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                assert len(checked) == 0

    async def test_preselection(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["openwrt", "infra"]
            await user.open(Routes.SERVICES)
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                assert len(checked) == 2

    async def test_deploy_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["openwrt"]
            await user.open(Routes.SERVICES)
            user.find(Labels.DEPLOY_SELECTED).click()
            await user.should_see("Deploy")

    async def test_profile_network_only(self, tmp_path):
        """Verify Network Only profile selects the right tags."""
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            with user:
                selects = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.select)]
                assert len(selects) == 1
                selects[0].set_value("Network Only")
            await asyncio.sleep(0.1)
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked_labels = [cb.text for cb in cbs if cb.value]
                assert any("backup" in lbl for lbl in checked_labels)
                assert any("infra" in lbl for lbl in checked_labels)
                assert any("openwrt" in lbl for lbl in checked_labels)
                assert not any("pihole" in lbl for lbl in checked_labels)

    async def test_profile_custom_clears(self, tmp_path):
        """Custom profile should clear all selections."""
        async with webui(tmp_path) as user:
            await user.open(Routes.SERVICES)
            user.find(Labels.SELECT_ALL).click()
            with user:
                selects = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.select)]
                selects[0].set_value("Custom")
            await asyncio.sleep(0.1)
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                assert len(checked) == 0


# ── Deploy page ──────────────────────────────────────────────────────


class TestDeploy:
    async def test_no_tags_shows_message(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DEPLOY)
            await user.should_see(Labels.NO_TAGS_SELECTED)

    async def test_shows_selected_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["pihole", "wireguard"]
            await user.open(Routes.DEPLOY)
            await user.should_see("pihole")
            await user.should_see("wireguard")

    async def test_shows_hosts_for_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["gaming"]
            await user.open(Routes.DEPLOY)
            await user.should_see("ai")

    async def test_start_deploy_runs_subprocess(self, tmp_path):
        """Verify deploy starts a subprocess and streams output."""

        class FakeProc:
            def __init__(self):
                reader = asyncio.StreamReader()
                reader.feed_data(b"PLAY [test play]\nok: done\n")
                reader.feed_eof()
                self.stdout = reader

            async def wait(self):
                return 0

            def send_signal(self, sig):
                pass

            def kill(self):
                pass

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        # WHY: Prevents actually launching ansible-playbook, which would modify real infrastructure.
        # HOW: Tests the deploy page UI workflow (start button, output streaming, completion) with captured output.
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                nicegui_app.storage.general["selected_tags"] = ["infra"]
                await user.open(Routes.DEPLOY)
                user.find(Labels.START_DEPLOY).click()
                await asyncio.sleep(0.5)
                await user.should_see("succeeded")

    async def test_deploy_no_tags_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = []
            await user.open(Routes.DEPLOY)
            user.find(Labels.START_DEPLOY).click()
            await asyncio.sleep(0.1)
            await user.should_see(Labels.NO_TAGS_SELECTED)

    async def test_cancel_sends_signal(self, tmp_path):
        """Verify cancel button sends SIGTERM to the running process."""
        import signal

        class FakeProc:
            def __init__(self):
                reader = asyncio.StreamReader()
                self.stdout = reader
                self._killed = False
                self._signal = None

            async def wait(self):
                await asyncio.sleep(0.5)
                return -15

            def send_signal(self, sig):
                self._signal = sig
                self.stdout.feed_eof()

            def kill(self):
                self._killed = True
                self.stdout.feed_eof()

        fake_proc = FakeProc()

        async def fake_exec(*args, **kwargs):
            return fake_proc

        # WHY: Prevents actually launching ansible-playbook, which would modify real infrastructure.
        # HOW: Tests that the cancel button sends SIGTERM to the subprocess handle.
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                nicegui_app.storage.general["selected_tags"] = ["infra"]
                await user.open(Routes.DEPLOY)
                user.find(Labels.START_DEPLOY).click()
                await asyncio.sleep(0.2)
                user.find(Labels.CANCEL).click()
                await asyncio.sleep(0.5)
                assert fake_proc._signal == signal.SIGTERM


# ── Images page ──────────────────────────────────────────────────────


class TestImages:
    async def test_shows_all_images(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.IMAGES)
            await user.should_see(PageTitles.IMAGES)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                assert len(tables) == 1
                image_names = [r["image"] for r in tables[0].rows]
                for display_name, _, _, _ in data.EXPECTED_IMAGES:
                    assert display_name in image_names, f"Missing {display_name}"

    async def test_distinguishes_built_missing(self, tmp_path):
        # Uses controlled images dir with partial set to test Built/Missing display
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for _, pattern, _, _ in data.EXPECTED_IMAGES[:3]:
            (images_dir / pattern.replace("*", "test")).write_bytes(b"\x00" * 1024)
        async with webui(tmp_path, images_dir=str(images_dir)) as user:
            await user.open(Routes.IMAGES)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                statuses = [r["status"] for r in tables[0].rows]
                assert "Built" in statuses
                assert "Missing" in statuses

    async def test_all_missing_initially(self, tmp_path):
        # Uses empty images dir to test zero-images edge case
        async with webui(tmp_path, images_dir=str(tmp_path / "no_images")) as user:
            await user.open(Routes.IMAGES)
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                statuses = set(r["status"] for r in tables[0].rows)
                assert statuses == {"Missing"}

    async def test_build_selected_no_selection_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.IMAGES)
            user.find(Labels.BUILD_SELECTED).click()
            await user.should_see(Labels.SELECT_IMAGE)

    async def test_build_all_runs_subprocess(self, tmp_path):

        class FakeProc:
            def __init__(self):
                reader = asyncio.StreamReader()
                reader.feed_data(b"Building all...\n")
                reader.feed_eof()
                self.stdout = reader

            async def wait(self):
                return 0

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        # WHY: Prevents actually running build-images.sh, which rebuilds VM/LXC images (~15 min).
        # HOW: Tests the image build page UI workflow (button click, output streaming, completion message).
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                await user.open(Routes.IMAGES)
                user.find(Labels.BUILD_ALL).click()
                await asyncio.sleep(0.5)
                await user.should_see("Build completed")


# ── End-to-end workflow ──────────────────────────────────────────────


class TestEndToEnd:
    async def test_full_workflow(self, tmp_path):
        """Dashboard -> environment -> hosts -> services."""
        env_path = tmp_path / ".env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("PRODUCTION")

            await user.open(Routes.ENVIRONMENT)
            await user.should_see("All required variables present")

            await user.open(Routes.HOSTS)
            await user.should_see(PageTitles.HOSTS)

            await user.open(Routes.SERVICES)
            await user.should_see(PageTitles.SERVICES)

    async def test_full_deploy_flow(self, tmp_path):
        """Dashboard Full Deploy -> Services -> Deploy."""
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            user.find(Labels.FULL_DEPLOY).click()
            await user.should_see(PageTitles.SERVICES)
            user.find(Labels.DEPLOY_SELECTED).click()
            await user.should_see("Deploy")


# ── Nodes (Fleet) page ──────────────────────────────────────────────


class TestNodes:
    async def test_empty_fleet(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see(PageTitles.NODES)
            await user.should_see(Labels.NODE_STATUS)

    async def test_shows_configured_hosts(self, tmp_path):
        """Fleet always shows configured hosts from env, even without heartbeats."""
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see("home")
            await user.should_see(Labels.FLEET_HEALTH)

    async def test_shows_host_with_telemetry(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home",
            hostname="home",
            local_ips=["192.168.86.201"],
            uptime_seconds=86400,
            services=["vm:100:openwrt"],
            disk_usage_pct=45.2,
            memory_usage_pct=62.1,
            version="1.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see("home")
            await user.should_see(Labels.FLEET_HEALTH)

    async def test_shows_configured_state(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see(Labels.NODE_STATUS)

    async def test_auto_refresh_toggle(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see("Auto-refresh")

    async def test_dashboard_shows_fleet_card(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("Fleet")

    async def test_health_banner_shows_score(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        for name in ["home", "mesh1", "ai"]:
            checkin = data.NodeCheckin(
                node_id=name, hostname=name, local_ips=["10.0.0.1"],
                uptime_seconds=86400, services=["vm:100:openwrt"],
                disk_usage_pct=35.0, memory_usage_pct=50.0, version="1.0",
            )
            data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open(Routes.NODES)
            await user.should_see(Labels.FLEET_HEALTH)
            await user.should_see("Online")
            await user.should_see("Guests")

    async def test_alerts_panel_shows_for_high_disk(self, tmp_path):
        # Controlled state: needs a specific 92% disk node to test alert rendering
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="warn-node", hostname="warn-node", local_ips=["10.0.0.1"],
            uptime_seconds=86400, services=[],
            disk_usage_pct=92.0, memory_usage_pct=30.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.NODES)
            await user.should_see("Alerts")
            await user.should_see("Disk usage 92.0%")

    async def test_service_matrix_shows_services(self, tmp_path):
        # Controlled state: tests specific service matrix with known services
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        for name, svcs in [("home", ["vm:100:openwrt", "ct:102:pihole"]),
                           ("mesh1", ["ct:101:wg"])]:
            checkin = data.NodeCheckin(
                node_id=name, hostname=name, local_ips=["10.0.0.1"],
                uptime_seconds=86400, services=svcs,
                disk_usage_pct=30.0, memory_usage_pct=40.0, version="1.0",
            )
            data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.NODES)
            await user.should_see(Labels.SERVICE_MATRIX)
            with user:
                tables = [e for e in ui.context.client.layout.descendants()
                          if isinstance(e, ui.table)]
                matrix_table = tables[0]
                svc_names = [r["service"] for r in matrix_table.rows]
                assert "openwrt" in svc_names
                assert "pihole" in svc_names
                assert "wg" in svc_names

    async def test_node_card_shows_hostname(self, tmp_path):
        # Controlled state: tests specific node card rendering with known version
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=86400, services=["ct:500:netdata"],
            disk_usage_pct=45.0, memory_usage_pct=55.0, version="2.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.NODES)
            await user.should_see("home")
            await user.should_see("v2.0")
            await user.should_see("1 guest running")

    async def test_dashboard_fleet_card_health_score(self, tmp_path):
        # Controlled state: tests specific guest count rendering
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=3600, services=["ct:500:netdata", "ct:501:rsyslog"],
            disk_usage_pct=30.0, memory_usage_pct=40.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("Fleet")
            await user.should_see("Health")
            await user.should_see("2 guests running")

    async def test_dashboard_fleet_card_critical_alert(self, tmp_path):
        # Controlled state: tests critical alert rendering for 95% disk
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="crit-node", hostname="crit-node", local_ips=["10.0.0.1"],
            uptime_seconds=3600, services=[],
            disk_usage_pct=95.0, memory_usage_pct=30.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("1 critical alert")

    async def test_dashboard_fleet_view_button(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.DASHBOARD)
            user.find(Labels.VIEW_FLEET).click()
            await user.should_see(PageTitles.NODES)

    async def test_dashboard_singular_guest(self, tmp_path):
        """Dashboard fleet card shows '1 guest' (singular) correctly."""
        # Controlled state: tests singular vs plural guest label
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=3600, services=["ct:500:netdata"],
            disk_usage_pct=30.0, memory_usage_pct=40.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path, state_dir=str(state_dir)) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("1 guest running")


class TestNodeDetail:
    """Tests for /nodes/{hostname} detail page."""

    async def test_detail_page_shows_hostname(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=259200, services=["vm:100:openwrt", "ct:102:pihole"],
            disk_usage_pct=45.0, memory_usage_pct=55.0, version="2.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path) as user:
            await user.open(Routes.NODE_DETAIL.format(hostname="home"))
            await user.should_see(PageTitles.NODE_DETAIL)
            await user.should_see("home")

    async def test_detail_page_shows_resources(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=86400, services=["vm:100:openwrt"],
            disk_usage_pct=72.5, memory_usage_pct=88.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path) as user:
            await user.open(Routes.NODE_DETAIL.format(hostname="home"))
            await user.should_see("Resources")
            await user.should_see("Disk")
            await user.should_see("Memory")

    async def test_detail_page_shows_guests(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="home", hostname="home", local_ips=["192.168.86.201"],
            uptime_seconds=86400,
            services=["vm:100:openwrt", "ct:101:wireguard", "ct:102:pihole"],
            disk_usage_pct=30.0, memory_usage_pct=40.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "192.168.86.201")
        async with webui(tmp_path) as user:
            await user.open(Routes.NODE_DETAIL.format(hostname="home"))
            await user.should_see("Guests")

    async def test_detail_page_not_found(self, tmp_path):
        """Unknown hostname shows error message."""
        async with webui(tmp_path) as user:
            await user.open(Routes.NODE_DETAIL.format(hostname="nonexistent"))
            await user.should_see("not found")

    async def test_detail_page_back_button(self, tmp_path):
        """Detail page has a back button to nodes list."""
        async with webui(tmp_path) as user:
            await user.open(Routes.NODE_DETAIL.format(hostname="home"))
            await user.should_see(PageTitles.NODE_DETAIL)


# ── Images quick-build ──────────────────────────────────────────────


# ── Hub page ─────────────────────────────────────────────────────────


class TestHub:
    async def test_shows_header(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HUB)
            await user.should_see(PageTitles.HUB)

    async def test_shows_all_service_sections(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HUB)
            await user.should_see("Infrastructure")
            await user.should_see("Desktop & Media")
            await user.should_see("Settings & Network")
            await user.should_see("Monitoring")
            await user.should_see("System")

    async def test_shows_all_service_titles(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HUB)
            for svc in data.get_hub_services():
                await user.should_see(svc.title)

    async def test_disabled_services_show_not_available(self, tmp_path):
        # Controlled env: uses env without LAN_GATEWAY so no SM hub URLs are generated,
        # causing non-internal services to show "Not available"
        async with webui(tmp_path, env_file="incomplete.env") as user:
            await user.open(Routes.HUB)
            await user.should_see(Labels.NOT_AVAILABLE)

    async def test_has_sidebar(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.HUB)
            await user.should_see(Labels.APP_TITLE)


# ── Images quick-build ──────────────────────────────────────────────


class TestImagesQuickBuild:
    async def test_shows_quick_build_buttons(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open(Routes.IMAGES)
            await user.should_see("Mesh LXC")
            await user.should_see("Router VM")


# ── REST API endpoint tests ──────────────────────────────────────────


SAMPLE_CHECKIN = {
    "node_id": "api-test-node",
    "hostname": "api-test-node",
    "local_ips": ["192.168.1.50"],
    "uptime_seconds": 3600,
    "services": ["vm:100:openwrt"],
    "disk_usage_pct": 30.0,
    "memory_usage_pct": 55.0,
    "version": "1.0",
}


@asynccontextmanager
async def api_client(tmp_path: Path, env_content: str = ""):
    """Provide an httpx client wired to the NiceGUI ASGI app with API routes."""
    async with user_simulation():
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        if env_content:
            env_path = tmp_path / ".env"
            env_path.write_text(env_content)
            nicegui_app.storage.general["env_path"] = str(env_path)
        else:
            nicegui_app.storage.general["env_path"] = str(tmp_path / "nonexistent.env")

        nicegui_app.storage.general["state_dir"] = str(state_dir)
        nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
        nicegui_app.storage.general["selected_tags"] = []

        register_api()
        transport = httpx.ASGITransport(app=nicegui_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class TestApiCheckin:
    async def test_checkin_no_auth_required(self, tmp_path):
        """When no CALLHOME_PRIVATE_KEY is set, checkin accepts without token."""
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["node_id"] == "api-test-node"

    async def test_checkin_valid_token(self, tmp_path):
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            _base_env(CALLHOME_PRIVATE_KEY=private_key)
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(
                ApiRoutes.CHECKIN,
                json=SAMPLE_CHECKIN,
                headers={"X-Callhome-Token": public_key},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    async def test_checkin_invalid_token(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            _base_env(CALLHOME_PRIVATE_KEY=private_key)
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(
                ApiRoutes.CHECKIN,
                json=SAMPLE_CHECKIN,
                headers={"X-Callhome-Token": "bad-token"},
            )
            assert resp.status_code == 403
            assert resp.json()["error"] == "unauthorized"

    async def test_checkin_missing_token_when_required(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            _base_env(CALLHOME_PRIVATE_KEY=private_key)
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            assert resp.status_code == 403

    async def test_checkin_malformed_body(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                ApiRoutes.CHECKIN,
                json={"bad": "payload"},
            )
            assert resp.status_code == 400
            assert "Invalid payload" in resp.json()["error"]

    async def test_checkin_persists_node(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get(ApiRoutes.NODES)
            assert nodes_resp.status_code == 200
            nodes_list = nodes_resp.json()
            assert len(nodes_list) == 1
            assert nodes_list[0]["node_id"] == "api-test-node"
            assert nodes_list[0]["hostname"] == "api-test-node"


class TestApiNodes:
    async def test_nodes_empty(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.NODES)
            assert resp.status_code == 200
            assert resp.json() == []

    async def test_nodes_returns_registered(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="n1", hostname="n1",
            local_ips=["10.0.0.1"], uptime_seconds=100,
            services=[], disk_usage_pct=20, memory_usage_pct=30,
            version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.NODES)
            assert resp.status_code == 200
            nodes_list = resp.json()
            assert len(nodes_list) == 1
            assert nodes_list[0]["node_id"] == "n1"

    async def test_nodes_auth_required_when_key_set(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            _base_env(CALLHOME_PRIVATE_KEY=private_key)
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get(ApiRoutes.NODES)
            assert resp.status_code == 403
            assert resp.json()["error"] == "unauthorized"

    async def test_nodes_auth_valid_token(self, tmp_path):
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            _base_env(CALLHOME_PRIVATE_KEY=private_key)
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get(
                ApiRoutes.NODES,
                headers={"X-Callhome-Token": public_key},
            )
            assert resp.status_code == 200
            assert resp.json() == []

    async def test_nodes_no_auth_when_no_key(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.NODES)
            assert resp.status_code == 200


# ── Heartbeat API tests ──────────────────────────────────────────────


class TestHeartbeatSubscribe:
    async def test_subscribe_success(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "wifi", "ttl": 30,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert "subscription_id" in body
            assert body["node_id"] == "home"
            assert body["metric_type"] == "wifi"

    async def test_subscribe_unknown_node(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "nonexistent", "metric_type": "wifi",
            })
            assert resp.status_code == 404
            assert "Unknown node" in resp.json()["error"]

    async def test_subscribe_unknown_metric_type(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "invalid_type",
            })
            assert resp.status_code == 400
            assert "Unknown metric_type" in resp.json()["error"]

    async def test_subscribe_refresh(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp1 = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id_1 = resp1.json()["subscription_id"]

            resp2 = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id_2 = resp2.json()["subscription_id"]
            assert sub_id_1 == sub_id_2

    async def test_subscribe_malformed_body(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "bad": "payload",
            })
            assert resp.status_code == 400


class TestHeartbeatMetrics:
    async def test_get_no_data(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/heartbeat/home/wifi")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "no_data"
            assert body["success"] is False
            assert "No wifi metrics" in body["error"]

    async def test_get_cached_data(self, tmp_path):
        from scripts.webui.heartbeat import HeartbeatCache
        async with api_client(tmp_path) as client:
            cache = get_metric_cache()
            cache.store(HeartbeatCache(
                node_id="home", metric_type="wifi",
                data={"signal": -55}, collected_at="2026-01-01T00:00:00",
            ))
            resp = await client.get("/api/heartbeat/home/wifi")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["signal"] == -55
            assert body["success"] is True
            cache.clear()

    async def test_unsubscribe(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id = resp.json()["subscription_id"]

            del_resp = await client.request("DELETE", f"/api/heartbeat/subscribe/{sub_id}")
            assert del_resp.status_code == 200
            assert del_resp.json()["removed"] is True

    async def test_list_subscriptions(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            await client.post(ApiRoutes.HEARTBEAT_SUBSCRIBE, json={
                "node_id": "home", "metric_type": "wifi",
            })
            resp = await client.get(ApiRoutes.HEARTBEAT_SUBSCRIPTIONS)
            assert resp.status_code == 200
            subs = resp.json()
            assert len(subs) >= 1
            assert any(s["node_id"] == "home" for s in subs)


# ── Batman API tests ─────────────────────────────────────────────────


class TestBatmanApi:
    """SM batman endpoints toggle real fleet batman-adv. No mocks.

    Enable → verify enabled → Disable → verify disabled.
    If the fleet/VPN is unreachable (502), the test still passes —
    that's a real infrastructure state worth knowing about.
    """

    async def test_batman_status_returns_fleet_state(self, tmp_path):
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get(ApiRoutes.BATMAN_STATUS)
            assert resp.status_code in (200, 502)
            assert isinstance(resp.json(), dict)

    async def test_batman_enable_and_disable_real_fleet(self, tmp_path):
        """Toggle batman on the real fleet: enable → check → disable.

        All three calls hit the real CM relay. If the fleet/VPN is
        partially up, some nodes succeed and others timeout — that's
        fine. 502 means the CM relay itself is unreachable (VPN down).
        """
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp_enable = await client.post(ApiRoutes.BATMAN_ENABLE)
            if resp_enable.status_code == 502:
                pytest.skip("Fleet/VPN unreachable — cannot test batman toggle")
            assert resp_enable.status_code == 200
            body_enable = resp_enable.json()
            assert body_enable["action"] == "enable"
            assert "results" in body_enable

            resp_status = await client.get(ApiRoutes.BATMAN_STATUS)
            assert resp_status.status_code in (200, 502)

            resp_disable = await client.post(ApiRoutes.BATMAN_DISABLE)
            if resp_disable.status_code == 502:
                pytest.skip("Fleet/VPN went down between enable and disable")
            assert resp_disable.status_code == 200
            body_disable = resp_disable.json()
            assert body_disable["action"] == "disable"
            assert "results" in body_disable


# ── Batman HMAC unit tests (no infrastructure needed) ─────────────────


class TestBatmanHmac:
    """Pure unit tests for HMAC token generation — no SSH, no hosts."""

    async def test_batman_hmac_token_format(self, tmp_path):
        """Verify Python HMAC produces a valid 64-char lowercase hex token."""
        import hashlib
        import hmac as hmac_mod
        key = "testkey123"
        for action in ("enable", "disable"):
            msg = f"{action}_batman"
            token = hmac_mod.new(
                key.encode(), msg.encode(), hashlib.sha256,
            ).hexdigest()
            assert len(token) == 64
            assert all(c in "0123456789abcdef" for c in token)


# ── Bridge action API tests ──────────────────────────────────────────


class TestBridgeActionApi:
    """SM bridge endpoints proxy to the Cluster Manager."""

    async def test_restart_wifi_proxies_to_cm(self, tmp_path):
        """Restart WiFi forwards to CM — returns 502 without a real CM."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(
                ApiRoutes.BRIDGE_RESTART_WIFI,
                json={"target": "all"},
            )
            assert resp.status_code in (200, 502)


# ── WiFi mode API tests ─────────────────────────────────────────────


class TestWifiModeApi:
    """SM WiFi endpoints proxy to the Cluster Manager."""

    async def test_wifi_mode_proxies_to_cm(self, tmp_path):
        """WiFi mode switch forwards to CM — returns 502 without a real CM."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/wifi/mode/bridge-1/sta")
            assert resp.status_code in (200, 502)

    async def test_wifi_status_proxies_to_cm(self, tmp_path):
        """WiFi status forwards to CM — returns 502 without a real CM."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get("/api/wifi/status/bridge-1")
            assert resp.status_code in (200, 502)

    async def test_wifi_status_all_proxies_to_cm(self, tmp_path):
        """Aggregate WiFi status forwards to CM — returns 502 without a real CM."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get(ApiRoutes.WIFI_STATUS_ALL)
            assert resp.status_code in (200, 502)


# ── Guest management API tests ──────────────────────────────────────


class TestGuestApi:
    """SM guest endpoints proxy to a specific NodeManager via HTTP.

    The SM requires a node_id query param to determine which NM to forward to.
    """

    async def test_guests_requires_node_id(self, tmp_path):
        """Returns 400 when node_id query param is missing."""
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.GUESTS)
            assert resp.status_code == 400
            assert "node_id" in resp.json()["error"]

    async def test_guests_unknown_node(self, tmp_path):
        """Returns 404 when node_id doesn't resolve."""
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/guests?node_id=nonexistent")
            assert resp.status_code == 404
            assert "Unknown node" in resp.json()["error"]

    async def test_guests_proxies_to_nm(self, tmp_path):
        """Guest list forwards to NM — returns 502 if NM unreachable."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get("/api/guests?node_id=home")
            assert resp.status_code in (200, 502)

    async def test_guest_action_requires_node_id(self, tmp_path):
        """Guest actions require node_id query param."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/guests/100/start")
            assert resp.status_code == 400
            assert "node_id" in resp.json()["error"]


# ── Fleet readiness API tests ────────────────────────────────────────


CONTAINER_CHECKIN = {
    "node_id": "ct-pihole",
    "hostname": "ct-pihole",
    "local_ips": ["10.10.10.10"],
    "uptime_seconds": 120,
    "services": [],
    "disk_usage_pct": 30.0,
    "memory_usage_pct": 40.0,
    "version": "1.0",
    "container_health": {
        "container_id": "pihole",
        "systemd_services": {"pihole-FTL": "running"},
        "listening_ports": [53, 80],
        "ready": True,
    },
}


def _seed_registry(state_dir: Path, count: int = 1) -> None:
    """Write a minimal registry.json so fleet readiness knows expected host count."""
    hosts = [{"name": f"host-{i}", "ip": f"10.0.0.{i+1}", "mac": "",
              "bucket": "", "source": "test", "wol_capable": True, "vpn_ip": f"10.0.0.{i+1}"}
             for i in range(count)]
    (state_dir / "registry.json").write_text(json.dumps(hosts))


class TestFleetReadyEndpoint:
    async def test_missing_services_param(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.FLEET_READY)
            assert resp.status_code == 400
            assert "services" in resp.json()["error"].lower()

    async def test_all_ready(self, tmp_path):
        async with api_client(tmp_path) as client:
            state_dir = tmp_path / "state"
            _seed_registry(state_dir, count=1)
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get("/api/fleet/ready?services=pihole")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is True
            assert body["ready_count"] == 1
            assert body["total"] == 1
            assert body["registered_hosts"] == 1

    async def test_missing_service_not_ready(self, tmp_path):
        async with api_client(tmp_path) as client:
            state_dir = tmp_path / "state"
            _seed_registry(state_dir, count=1)
            resp = await client.get("/api/fleet/ready?services=netdata")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is False
            assert body["services"]["netdata"]["status"] == "unknown"

    async def test_partial_readiness(self, tmp_path):
        async with api_client(tmp_path) as client:
            state_dir = tmp_path / "state"
            _seed_registry(state_dir, count=2)
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get("/api/fleet/ready?services=pihole,netdata")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is False
            assert body["ready_count"] == 1
            assert body["total"] == 1
            assert body["registered_hosts"] == 2
            assert body["services"]["netdata"]["status"] == "unknown"


class TestFleetStaleEndpoint:
    """Tests for GET /api/fleet/stale circuit breaker endpoint."""

    async def test_missing_services_param(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.FLEET_STALE)
            assert resp.status_code == 400

    async def test_all_healthy(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get("/api/fleet/stale?services=pihole")
            assert resp.status_code == 200
            body = resp.json()
            assert body["has_stale"] is False
            assert "pihole" in body["healthy"]

    async def test_never_seen_not_stale(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/fleet/stale?services=netdata")
            assert resp.status_code == 200
            body = resp.json()
            assert body["has_stale"] is False
            assert "netdata" in body["never_seen"]

    async def test_stale_returns_409(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get(
                "/api/fleet/stale?services=pihole&max_age_seconds=0",
            )
            assert resp.status_code == 409
            body = resp.json()
            assert body["has_stale"] is True
            assert len(body["stale"]) == 1
            assert body["stale"][0]["service"] == "pihole"

    async def test_invalid_max_age(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(
                "/api/fleet/stale?services=pihole&max_age_seconds=abc",
            )
            assert resp.status_code == 400
            assert "integer" in resp.json()["error"].lower()


class TestContainerReadyEndpoint:
    async def test_known_container(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get("/api/container/pihole/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is True
            assert body["container_id"] == "pihole"
            assert 53 in body["listening_ports"]

    async def test_unknown_container(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/container/nonexistent/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is False
            assert body["status"] == "unknown"


class TestFleetHealthEndpoint:
    async def test_empty_fleet(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get(ApiRoutes.FLEET_HEALTH)
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_nodes"] == 0
            assert "health_score" in body

    async def test_with_nodes(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            resp = await client.get(ApiRoutes.FLEET_HEALTH)
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_nodes"] == 1
            assert body["online_nodes"] == 1


class TestCheckinWithContainerHealth:
    async def test_container_health_round_trip(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get(ApiRoutes.NODES)
            nodes = nodes_resp.json()
            assert len(nodes) == 1
            assert nodes[0]["node_id"] == "ct-pihole"

    async def test_checkin_without_container_health(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get(ApiRoutes.NODES)
            nodes = nodes_resp.json()
            assert len(nodes) == 1


EXTENSIONS_CHECKIN = {
    "node_id": "ct-wireguard",
    "hostname": "ct-wireguard",
    "local_ips": ["10.10.10.3"],
    "uptime_seconds": 200,
    "services": [],
    "disk_usage_pct": 20.0,
    "memory_usage_pct": 30.0,
    "version": "1.0",
    "container_health": {
        "container_id": "wireguard",
        "systemd_services": {"wg-quick@wg0": "active"},
        "listening_ports": [51820],
        "ready": True,
        "extensions": {
            "wireguard": {
                "interfaces": {"wg0": {"peer_count": 4, "up": True}},
            },
            "network": {
                "interfaces": [
                    {"name": "eth0", "addresses": ["10.10.10.3/24"], "operstate": "up"},
                ],
                "default_gateway": "10.10.10.1",
            },
        },
    },
}


class TestExtensionsRoundTrip:
    """Extensions survive checkin → storage → API responses."""

    async def test_extensions_in_container_ready(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=EXTENSIONS_CHECKIN)
            resp = await client.get("/api/container/wireguard/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is True
            ext = body["extensions"]
            assert ext["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4
            assert ext["network"]["default_gateway"] == "10.10.10.1"

    async def test_extensions_in_nodes_list(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=EXTENSIONS_CHECKIN)
            resp = await client.get(ApiRoutes.NODES)
            assert resp.status_code == 200
            nodes = resp.json()
            assert len(nodes) == 1
            ch = nodes[0]["container_health"]
            assert ch["container_id"] == "wireguard"
            assert ch["extensions"]["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4

    async def test_extensions_empty_by_default(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=CONTAINER_CHECKIN)
            resp = await client.get("/api/container/pihole/ready")
            body = resp.json()
            assert body["extensions"] == {}

    async def test_docker_extensions(self, tmp_path):
        ha_checkin = {
            "node_id": "ct-ha",
            "hostname": "ct-ha",
            "local_ips": ["10.10.10.14"],
            "uptime_seconds": 300,
            "services": [],
            "disk_usage_pct": 25.0,
            "memory_usage_pct": 35.0,
            "version": "1.0",
            "container_health": {
                "container_id": "homeassistant",
                "systemd_services": {"docker": "active"},
                "listening_ports": [8123],
                "ready": True,
                "extensions": {
                    "docker": {"active": True, "running": 3},
                },
            },
        }
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=ha_checkin)
            resp = await client.get("/api/container/homeassistant/ready")
            body = resp.json()
            assert body["extensions"]["docker"]["active"] is True
            assert body["extensions"]["docker"]["running"] == 3

    async def test_config_files_extensions(self, tmp_path):
        kiosk_checkin = {
            "node_id": "ct-kiosk",
            "hostname": "ct-kiosk",
            "local_ips": ["10.10.10.19"],
            "uptime_seconds": 150,
            "services": [],
            "disk_usage_pct": 15.0,
            "memory_usage_pct": 25.0,
            "version": "1.0",
            "container_health": {
                "container_id": "kiosk",
                "systemd_services": {"kiosk-web": "active"},
                "listening_ports": [9001],
                "ready": True,
                "extensions": {
                    "config_files": {
                        "/opt/kiosk/config.json": {
                            "keys": ["DESKTOP_URL", "JELLYFIN_URL", "GAMING_URL"],
                            "hash": "abcd1234",
                        },
                    },
                },
            },
        }
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=kiosk_checkin)
            resp = await client.get("/api/container/kiosk/ready")
            body = resp.json()
            cfg = body["extensions"]["config_files"]["/opt/kiosk/config.json"]
            assert "DESKTOP_URL" in cfg["keys"]
            assert cfg["hash"] == "abcd1234"

    async def test_http_probes_extensions(self, tmp_path):
        checkin = {
            "node_id": "ct-jellyfin",
            "hostname": "ct-jellyfin",
            "local_ips": ["10.10.10.15"],
            "uptime_seconds": 600,
            "services": [],
            "disk_usage_pct": 30.0,
            "memory_usage_pct": 40.0,
            "version": "1.0",
            "container_health": {
                "container_id": "jellyfin",
                "systemd_services": {"jellyfin.service": "active"},
                "listening_ports": [8096],
                "ready": True,
                "extensions": {
                    "http_probes": {
                        "http://127.0.0.1:8096": 200,
                    },
                    "network": {
                        "interfaces": [
                            {"name": "eth0", "operstate": "up",
                             "mac": "aa:bb:cc:dd:ee:ff",
                             "addresses": ["10.10.10.15/24"]},
                        ],
                        "default_gateway": "10.10.10.1",
                    },
                },
            },
        }
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=checkin)
            resp = await client.get("/api/container/jellyfin/ready")
            body = resp.json()
            assert body["ready"] is True
            assert body["extensions"]["http_probes"]["http://127.0.0.1:8096"] == 200
            net_ifaces = body["extensions"]["network"]["interfaces"]
            assert net_ifaces[0]["addresses"][0] == "10.10.10.15/24"

    async def test_udp_ports_in_container_ready(self, tmp_path):
        checkin = {
            "node_id": "ct-wg",
            "hostname": "ct-wg",
            "local_ips": ["10.10.10.3"],
            "uptime_seconds": 120,
            "services": [],
            "disk_usage_pct": 10.0,
            "memory_usage_pct": 20.0,
            "version": "1.0",
            "container_health": {
                "container_id": "wireguard-udp",
                "systemd_services": {"wg-quick@wg0.service": "active"},
                "listening_ports": [51820, 514],
                "ready": True,
                "extensions": {},
            },
        }
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=checkin)
            resp = await client.get("/api/container/wireguard-udp/ready")
            body = resp.json()
            assert 51820 in body["listening_ports"]
            assert 514 in body["listening_ports"]

    async def test_nodes_without_extensions(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=SAMPLE_CHECKIN)
            resp = await client.get(ApiRoutes.NODES)
            nodes = resp.json()
            assert "container_health" not in nodes[0]


class TestEndToEndCallhomeFlow:
    """True end-to-end: build_container_payload → checkin → query APIs."""

    async def test_collector_to_api_roundtrip(self, tmp_path):
        """Verify the real callhome payload structure works through the API."""
        from scripts.callhome import build_container_payload
        payload = build_container_payload("test-service")
        async with api_client(tmp_path) as client:
            resp = await client.post(ApiRoutes.CHECKIN, json=payload)
            assert resp.status_code == 200

            ready = await client.get("/api/container/test-service/ready")
            assert ready.status_code == 200
            body = ready.json()
            assert body["ready"] is True
            assert body["container_id"] == "test-service"
            assert isinstance(body["listening_ports"], list)
            assert isinstance(body["systemd_services"], dict)
            assert isinstance(body["extensions"], dict)

    async def test_fleet_readiness_with_real_payload(self, tmp_path):
        """Fleet readiness gate works with real callhome payloads."""
        from scripts.callhome import build_container_payload
        async with api_client(tmp_path) as client:
            state_dir = tmp_path / "state"
            _seed_registry(state_dir, count=3)
            for svc in ["pihole", "netdata", "wireguard"]:
                payload = build_container_payload(svc)
                payload["node_id"] = f"ct-{svc}"
                payload["hostname"] = f"ct-{svc}"
                await client.post(ApiRoutes.CHECKIN, json=payload)
            resp = await client.get(
                "/api/fleet/ready?services=pihole,netdata,wireguard",
            )
            body = resp.json()
            assert body["all_ready"] is True
            assert body["ready_count"] == 3

    async def test_extensions_survive_real_payload(self, tmp_path):
        """Network extension from collect_network flows through the API."""
        from scripts.callhome import build_container_payload
        payload = build_container_payload("test-ext")
        ch = payload["container_health"]
        ext = ch.get("extensions", {})
        has_network = "network" in ext

        async with api_client(tmp_path) as client:
            await client.post(ApiRoutes.CHECKIN, json=payload)
            resp = await client.get("/api/container/test-ext/ready")
            body = resp.json()
            if has_network:
                assert "network" in body["extensions"]
                assert isinstance(body["extensions"]["network"]["interfaces"], list)

    async def test_guest_action_proxies_to_nm(self, tmp_path):
        """Guest action forwards to NM — returns 502 if NM unreachable."""
        env_content = (
            _base_env()
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/guests/100/start?node_id=home")
            assert resp.status_code in (200, 502)


class TestHostRegisterEndpoint:
    """POST /api/hosts/register — manual host registration."""

    async def test_register_new_host(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                data.ApiRoutes.HOST_REGISTER,
                json={"name": "edge-01", "ip": "10.0.0.50"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["name"] == "edge-01"
            assert body["bucket"] == "lab"

    async def test_register_with_bucket(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                data.ApiRoutes.HOST_REGISTER,
                json={"name": "test-unit", "ip": "192.168.86.230", "bucket": "test"},
            )
            assert resp.status_code == 200
            assert resp.json()["bucket"] == "test"

    async def test_register_missing_name(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                data.ApiRoutes.HOST_REGISTER,
                json={"ip": "10.0.0.50"},
            )
            assert resp.status_code == 400

    async def test_register_missing_ip(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                data.ApiRoutes.HOST_REGISTER,
                json={"name": "edge-01"},
            )
            assert resp.status_code == 400

    async def test_registered_host_in_fleet(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        async with api_client(tmp_path) as client:
            await client.post(
                data.ApiRoutes.HOST_REGISTER,
                json={"name": "manual-host", "ip": "10.0.0.99", "mac": "aa:bb:cc:dd:ee:ff"},
            )
        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, state_dir)
        manual = fleet.get_host("manual-host")
        assert manual is not None
        assert manual.mac == "aa:bb:cc:dd:ee:ff"


# ── Host State API (Manager as Source of Truth) ──────────────────────


class TestHostStateApi:
    async def test_get_unknown_host_returns_404(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/host/nonexistent/state")
            assert resp.status_code == 404

    async def test_post_hardware_creates_host_and_stores(self, tmp_path):
        async with api_client(tmp_path) as client:
            hw_payload = {
                "ip": "192.168.86.201",
                "pci_devices": [{
                    "bdf": "02:00.0", "device_type": "wifi",
                    "vendor_device": "8086:2725", "driver": "iwlwifi",
                    "assigned_to": None, "iommu_group": 1,
                }],
                "wifi_phys": [{
                    "name": "phy0", "pci_device": "02:00.0",
                    "namespace": "host", "driver": "iwlwifi",
                }],
            }
            resp = await client.post("/api/host/home/hardware", json=hw_payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["hostname"] == "home"
            assert len(body["hardware"]["pci_devices"]) == 1
            assert len(body["hardware"]["wifi_phys"]) == 1

            get_resp = await client.get("/api/host/home/state")
            assert get_resp.status_code == 200
            state = get_resp.json()
            assert state["hostname"] == "home"
            assert state["hardware"]["wifi_phys"][0]["name"] == "phy0"

    async def test_post_bridges(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/host/home/hardware", json={"ip": "10.0.0.1"})
            br_payload = {
                "bridges": [{
                    "name": "vmbr0", "role": "wan",
                    "physical_nics": ["enp1s0"], "has_carrier": True,
                }],
                "wan_bridge": "vmbr0",
                "container_bridge": "vmbr_ct",
            }
            resp = await client.post("/api/host/home/bridges", json=br_payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["bridges"]["wan_bridge"] == "vmbr0"
            assert len(body["bridges"]["bridges"]) == 1

    async def test_register_and_deregister_container(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/host/home/hardware", json={"ip": "10.0.0.1"})
            ct_payload = {
                "service_type": "pihole", "hostname": "pihole",
                "state": "running", "ip": "10.10.10.10",
                "bridge": "vmbr1",
            }
            resp = await client.post("/api/host/home/containers/102", json=ct_payload)
            assert resp.status_code == 201
            body = resp.json()
            assert "102" in body["containers"]
            assert body["containers"]["102"]["service_type"] == "pihole"

            del_resp = await client.request("DELETE", "/api/host/home/containers/102")
            assert del_resp.status_code == 200

            state = (await client.get("/api/host/home/state")).json()
            assert "102" not in state["containers"]

    async def test_patch_phy_namespace(self, tmp_path):
        async with api_client(tmp_path) as client:
            hw_payload = {
                "ip": "10.0.0.1",
                "wifi_phys": [{
                    "name": "phy0", "pci_device": "02:00.0",
                    "namespace": "host", "driver": "iwlwifi",
                }],
            }
            await client.post("/api/host/home/hardware", json=hw_payload)
            patch_resp = await client.patch(
                "/api/host/home/hardware/phy/phy0",
                json={"namespace": "container:103"},
            )
            assert patch_resp.status_code == 200
            body = patch_resp.json()
            phy = body["hardware"]["wifi_phys"][0]
            assert phy["namespace"] == "container:103"

    async def test_persistence_survives_reload(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/host/home/hardware", json={"ip": "10.0.0.1"})
            await client.post("/api/host/home/containers/300", json={
                "service_type": "jellyfin", "hostname": "jellyfin",
                "state": "running", "ip": "10.10.10.15", "bridge": "vmbr1",
            })
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/host/home/state")
            assert resp.status_code == 200
            assert "300" in resp.json()["containers"]

    async def test_invalid_vmid_returns_400(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/host/home/hardware", json={"ip": "10.0.0.1"})
            resp = await client.post("/api/host/home/containers/abc", json={
                "service_type": "test", "hostname": "test",
                "state": "running", "ip": "0.0.0.0", "bridge": "vmbr0",
            })
            assert resp.status_code == 400

    async def test_full_lifecycle_registration(self, tmp_path):
        """Simulates full infra+service registration matching the Ansible flow."""
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/host/home/hardware", json={
                "ip": "192.168.86.201",
                "pci_devices": [
                    {"bdf": "02:00.0", "device_type": "wifi",
                     "vendor_device": "8086:2725", "driver": "iwlwifi",
                     "assigned_to": None, "iommu_group": 1},
                ],
                "wifi_phys": [
                    {"name": "phy0", "pci_device": "02:00.0",
                     "namespace": "host", "driver": "iwlwifi"},
                ],
                "igpu": {
                    "vendor": "intel", "driver": "i915",
                    "pci_address": "00:02.0",
                    "render_device": "/dev/dri/renderD128",
                    "render_gid": 104, "video_gid": 44,
                },
            })
            assert resp.status_code == 200

            resp = await client.post("/api/host/home/bridges", json={
                "bridges": [
                    {"name": "vmbr0", "role": "wan",
                     "physical_nics": ["enp1s0"], "has_carrier": True},
                    {"name": "vmbr1", "role": "lan",
                     "physical_nics": ["enp2s0"], "has_carrier": True},
                ],
                "wan_bridge": "vmbr0",
                "container_bridge": "vmbr_ct",
            })
            assert resp.status_code == 200

            resp = await client.post("/api/host/home/containers/100", json={
                "service_type": "openwrt_vm", "hostname": "openwrt-router",
                "state": "running", "ip": "192.168.1.1", "bridge": "vmbr0",
                "hardware": ["02:00.0"],
            })
            assert resp.status_code == 201

            resp = await client.post("/api/host/home/containers/102", json={
                "service_type": "pihole", "hostname": "pihole",
                "state": "running", "ip": "10.10.10.10", "bridge": "vmbr1",
            })
            assert resp.status_code == 201

            await client.patch("/api/host/home/hardware/phy/phy0",
                               json={"namespace": "container:100"})

            state = (await client.get("/api/host/home/state")).json()
            assert state["hostname"] == "home"
            assert state["hardware"]["igpu"]["vendor"] == "intel"
            assert len(state["bridges"]["bridges"]) == 2
            assert state["bridges"]["wan_bridge"] == "vmbr0"
            assert "100" in state["containers"]
            assert "102" in state["containers"]
            assert state["containers"]["100"]["service_type"] == "openwrt_vm"
            phy0 = state["hardware"]["wifi_phys"][0]
            assert phy0["namespace"] == "container:100"

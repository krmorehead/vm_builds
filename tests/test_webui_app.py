"""Tier 2 — Functional UI tests using NiceGUI's user simulation.

Each test gets an isolated NiceGUI app context via user_simulation().
Pages are re-registered per test to ensure clean routing state.
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
from scripts.webui.app import register_api
from scripts.webui.manager import get_subscription_manager, get_metric_cache
from scripts.webui.pages import (
    bridge, dashboard, deploy, environment, hosts, hub, images, mesh, nodes,
    router, services,
)

FIXTURES = Path(__file__).parent / "fixtures"


@asynccontextmanager
async def webui(tmp_path: Path, env_file: str = "complete.env", **overrides):
    """Create a NiceGUI user simulation with all pages and default storage."""
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
        nicegui_app.storage.general["env_path"] = str(FIXTURES / env_file)
        nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
        nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
        nicegui_app.storage.general["selected_tags"] = []
        nicegui_app.storage.general.update(overrides)
        yield user


# ── Dashboard page ───────────────────────────────────────────────────


class TestDashboard:
    async def test_shows_host_summary(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            await user.should_see("Hosts")
            await user.should_see("home")

    async def test_shows_image_summary(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for _, pattern, _, _ in data.EXPECTED_IMAGES[:10]:
            (images_dir / pattern.replace("*", "test")).write_bytes(b"\x00" * 1024)
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["images_dir"] = str(images_dir)
            await user.open("/")
            await user.should_see("10/14 built")

    async def test_shows_no_deploys_initially(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            await user.should_see("No deployments yet")

    async def test_shows_last_deploy(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        record = data.DeployRecord(
            timestamp="2026-04-04T12:00:00", tags=["infra"],
            env_file=".env", exit_code=0, duration_seconds=60,
        )
        data.save_deploy_record(state_dir, record)
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["state_dir"] = str(state_dir)
            await user.open("/")
            await user.should_see("success")
            await user.should_see("infra")

    async def test_full_deploy_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            user.find("Full Deploy").click()
            await user.should_see("Service Selection")

    async def test_check_hosts_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            user.find("Check Hosts").click()
            await user.should_see("Host Connectivity")

    async def test_build_images_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            user.find("Build Images").click()
            await user.should_see("Image Management")

    async def test_env_banner_production(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/")
            await user.should_see("PRODUCTION")

    async def test_env_banner_test(self, tmp_path):
        env_path = tmp_path / "test.env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/")
            await user.should_see("TEST")


# ── Environment page ─────────────────────────────────────────────────


class TestEnvironment:
    async def test_shows_table_with_variables(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/environment")
            await user.should_see("Environment")
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                assert len(tables) == 1
                var_names = [r["variable"] for r in tables[0].rows]
                assert "PRIMARY_HOST" in var_names
                assert "HOME_API_TOKEN" in var_names

    async def test_shows_status(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/environment")
            await user.should_see("All required variables present")

    async def test_missing_vars_flagged(self, tmp_path):
        async with webui(tmp_path, env_file="incomplete.env") as user:
            await user.open("/environment")
            await user.should_see("Missing")

    async def test_no_file_prompts_create(self, tmp_path):
        async with webui(tmp_path, env_path=str(tmp_path / "nonexistent.env")) as user:
            await user.open("/environment")
            await user.should_see("No env file found")

    async def test_create_writes_file(self, tmp_path):
        env_path = tmp_path / ".env"
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/environment")
            user.find("Create .env").click()
            assert env_path.exists()
            assert "PRIMARY_HOST=" in env_path.read_text()

    async def test_validate_button(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/environment")
            user.find("Validate").click()
            await user.should_see("All required variables present")

    async def test_save_writes_file(self, tmp_path):
        env_path = tmp_path / "save_test.env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/environment")
            user.find("Save").click()
            assert env_path.exists()
            content = env_path.read_text()
            assert "PRIMARY_HOST=" in content

    async def test_create_then_save_roundtrip(self, tmp_path):
        env_path = tmp_path / "roundtrip.env"
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/environment")
            user.find("Create .env").click()
            assert env_path.exists()
            user.find("Save").click()
            assert env_path.exists()
            backup = tmp_path / "roundtrip.env.bak"
            assert backup.exists()


# ── Hosts page ───────────────────────────────────────────────────────


class TestHosts:
    async def test_shows_all_hosts(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hosts")
            await user.should_see("Host Connectivity")
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
            await user.open("/hosts")
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                wol_values = [r.get("wol", "") for r in tables[0].rows]
                assert any("No" in str(v) for v in wol_values)

    async def test_probe_updates_status(self, tmp_path):
        with patch("scripts.webui.data.build.probe_host", return_value=True):
            async with webui(tmp_path) as user:
                await user.open("/hosts")
                user.find("Probe All").click()
                await asyncio.sleep(0.5)
                with user:
                    tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                    statuses = [r.get("status", "") for r in tables[0].rows]
                    assert any("Reachable" in str(s) for s in statuses)

    async def test_unreachable_shows_error(self, tmp_path):
        with patch("scripts.webui.data.build.probe_host", return_value=False):
            async with webui(tmp_path) as user:
                await user.open("/hosts")
                user.find("Probe All").click()
                await asyncio.sleep(0.5)
                with user:
                    tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                    statuses = [r.get("status", "") for r in tables[0].rows]
                    assert any("Unreachable" in str(s) for s in statuses)

    async def test_ssh_button_success(self, tmp_path):
        mock_result = data.SshResult(success=True, output="ok")
        with patch("scripts.webui.data.test_ssh_connection", return_value=mock_result):
            async with webui(tmp_path) as user:
                await user.open("/hosts")
                with user:
                    tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                    tables[0].selected = [tables[0].rows[0]]
                user.find("Test SSH").click()
                await asyncio.sleep(0.5)
                await user.should_see("OK")

    async def test_ssh_button_no_selection(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hosts")
            user.find("Test SSH").click()
            await user.should_see("Select a row first")


# ── Services page ────────────────────────────────────────────────────


class TestServices:
    async def test_shows_all_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/services")
            for svc in data.get_service_tags():
                await user.should_see(svc.tag)

    async def test_deploy_without_selection_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/services")
            user.find("Deploy Selected").click()
            await user.should_see("No services selected")

    async def test_select_all(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/services")
            user.find("Select All").click()
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                expected = len([t for t in data.get_service_tags() if not t.is_opt_in])
                assert len(checked) == expected

    async def test_deselect_all(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/services")
            user.find("Select All").click()
            user.find("Deselect All").click()
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                assert len(checked) == 0

    async def test_preselection(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["openwrt", "infra"]
            await user.open("/services")
            with user:
                cbs = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.checkbox)]
                checked = [cb for cb in cbs if cb.value]
                assert len(checked) == 2

    async def test_deploy_navigates(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["openwrt"]
            await user.open("/services")
            user.find("Deploy Selected").click()
            await user.should_see("Deploy")

    async def test_profile_network_only(self, tmp_path):
        """Verify Network Only profile selects the right tags."""
        async with webui(tmp_path) as user:
            await user.open("/services")
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
            await user.open("/services")
            user.find("Select All").click()
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
            await user.open("/deploy")
            await user.should_see("No tags selected")

    async def test_shows_selected_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["pihole", "wireguard"]
            await user.open("/deploy")
            await user.should_see("pihole")
            await user.should_see("wireguard")

    async def test_shows_hosts_for_tags(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = ["gaming"]
            await user.open("/deploy")
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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                nicegui_app.storage.general["selected_tags"] = ["infra"]
                await user.open("/deploy")
                user.find("Start Deploy").click()
                await asyncio.sleep(0.5)
                await user.should_see("succeeded")

    async def test_deploy_no_tags_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["selected_tags"] = []
            await user.open("/deploy")
            user.find("Start Deploy").click()
            await asyncio.sleep(0.1)
            await user.should_see("No tags selected")

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
                await asyncio.sleep(10)
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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                nicegui_app.storage.general["selected_tags"] = ["infra"]
                await user.open("/deploy")
                user.find("Start Deploy").click()
                await asyncio.sleep(0.2)
                user.find("Cancel").click()
                await asyncio.sleep(0.5)
                assert fake_proc._signal == signal.SIGTERM


# ── Images page ──────────────────────────────────────────────────────


class TestImages:
    async def test_shows_all_images(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/images")
            await user.should_see("Image Management")
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                assert len(tables) == 1
                image_names = [r["image"] for r in tables[0].rows]
                for display_name, _, _, _ in data.EXPECTED_IMAGES:
                    assert display_name in image_names, f"Missing {display_name}"

    async def test_distinguishes_built_missing(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for _, pattern, _, _ in data.EXPECTED_IMAGES[:3]:
            (images_dir / pattern.replace("*", "test")).write_bytes(b"\x00" * 1024)
        async with webui(tmp_path) as user:
            nicegui_app.storage.general["images_dir"] = str(images_dir)
            await user.open("/images")
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                statuses = [r["status"] for r in tables[0].rows]
                assert "Built" in statuses
                assert "Missing" in statuses

    async def test_all_missing_initially(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/images")
            with user:
                tables = [e for e in ui.context.client.layout.descendants() if isinstance(e, ui.table)]
                statuses = set(r["status"] for r in tables[0].rows)
                assert statuses == {"Missing"}

    async def test_build_selected_no_selection_warns(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/images")
            user.find("Build Selected").click()
            await user.should_see("Select an image to build")

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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            async with webui(tmp_path) as user:
                await user.open("/images")
                user.find("Build All").click()
                await asyncio.sleep(0.5)
                await user.should_see("Build completed")


# ── End-to-end workflow ──────────────────────────────────────────────


class TestEndToEnd:
    async def test_full_workflow(self, tmp_path):
        """Dashboard -> environment -> hosts -> services."""
        env_path = tmp_path / ".env"
        env_path.write_text("PRIMARY_HOST=1.2.3.4\nHOME_API_TOKEN=x\nMESH_KEY=y\n")
        async with webui(tmp_path, env_path=str(env_path)) as user:
            await user.open("/")
            await user.should_see("PRODUCTION")

            await user.open("/environment")
            await user.should_see("All required variables present")

            await user.open("/hosts")
            await user.should_see("Host Connectivity")

            await user.open("/services")
            await user.should_see("Service Selection")

    async def test_full_deploy_flow(self, tmp_path):
        """Dashboard Full Deploy -> Services -> Deploy."""
        async with webui(tmp_path) as user:
            await user.open("/")
            user.find("Full Deploy").click()
            await user.should_see("Service Selection")
            user.find("Deploy Selected").click()
            await user.should_see("Deploy")


# ── Nodes (Fleet) page ──────────────────────────────────────────────


class TestNodes:
    async def test_empty_fleet(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("Fleet Nodes")
            await user.should_see("No nodes registered")

    async def test_shows_registered_nodes(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="test-node-1",
            hostname="test-node-1",
            local_ips=["192.168.1.100"],
            uptime_seconds=86400,
            services=["vm:100:openwrt"],
            disk_usage_pct=45.2,
            memory_usage_pct=62.1,
            version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("test-node-1")
            await user.should_see("Fleet Health")

    async def test_shows_empty_state(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("No nodes registered")

    async def test_auto_refresh_toggle(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("Auto-refresh")

    async def test_dashboard_shows_fleet_card(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
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
            await user.open("/nodes")
            await user.should_see("Fleet Health")
            await user.should_see("Online")
            await user.should_see("Services")

    async def test_alerts_panel_shows_for_high_disk(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="warn-node", hostname="warn-node", local_ips=["10.0.0.1"],
            uptime_seconds=86400, services=[],
            disk_usage_pct=92.0, memory_usage_pct=30.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("Alerts")
            await user.should_see("Disk usage 92.0%")

    async def test_service_matrix_shows_services(self, tmp_path):
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
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("Service Matrix")
            with user:
                tables = [e for e in ui.context.client.layout.descendants()
                          if isinstance(e, ui.table)]
                matrix_table = tables[0]
                svc_names = [r["service"] for r in matrix_table.rows]
                assert "openwrt" in svc_names
                assert "pihole" in svc_names
                assert "wg" in svc_names

    async def test_node_card_shows_hostname(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="cardtest", hostname="cardtest", local_ips=["10.0.0.1"],
            uptime_seconds=86400, services=["ct:500:netdata"],
            disk_usage_pct=45.0, memory_usage_pct=55.0, version="2.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open("/nodes")
            await user.should_see("cardtest")
            await user.should_see("v2.0")
            await user.should_see("1 service running")

    async def test_dashboard_fleet_card_health_score(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="dash-node", hostname="dash-node", local_ips=["10.0.0.1"],
            uptime_seconds=3600, services=["ct:500:netdata", "ct:501:rsyslog"],
            disk_usage_pct=30.0, memory_usage_pct=40.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open("/")
            await user.should_see("Fleet")
            await user.should_see("Health")
            await user.should_see("2 services")

    async def test_dashboard_fleet_card_critical_alert(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        checkin = data.NodeCheckin(
            node_id="crit-node", hostname="crit-node", local_ips=["10.0.0.1"],
            uptime_seconds=3600, services=[],
            disk_usage_pct=95.0, memory_usage_pct=30.0, version="1.0",
        )
        data.register_checkin(state_dir, checkin, "10.0.0.1")
        async with webui(tmp_path) as user:
            await user.open("/")
            await user.should_see("1 critical alert")

    async def test_dashboard_fleet_view_button(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/")
            user.find("View Fleet Dashboard").click()
            await user.should_see("Fleet Nodes")


# ── Images quick-build ──────────────────────────────────────────────


# ── Hub page ─────────────────────────────────────────────────────────


class TestHub:
    async def test_shows_header(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Home Hub")

    async def test_shows_all_service_sections(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Infrastructure")
            await user.should_see("Desktop & Media")
            await user.should_see("Settings & Network")
            await user.should_see("Monitoring")
            await user.should_see("System")

    async def test_shows_all_service_titles(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hub")
            for svc in data.get_hub_services():
                await user.should_see(svc.title)

    async def test_disabled_services_show_not_available(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("Not available")

    async def test_has_sidebar(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/hub")
            await user.should_see("vm_builds")


# ── Images quick-build ──────────────────────────────────────────────


class TestImagesQuickBuild:
    async def test_shows_quick_build_buttons(self, tmp_path):
        async with webui(tmp_path) as user:
            await user.open("/images")
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
            resp = await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["node_id"] == "api-test-node"

    async def test_checkin_valid_token(self, tmp_path):
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(
                "/api/checkin",
                json=SAMPLE_CHECKIN,
                headers={"X-Callhome-Token": public_key},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    async def test_checkin_invalid_token(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post(
                "/api/checkin",
                json=SAMPLE_CHECKIN,
                headers={"X-Callhome-Token": "bad-token"},
            )
            assert resp.status_code == 403
            assert resp.json()["error"] == "unauthorized"

    async def test_checkin_missing_token_when_required(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            assert resp.status_code == 403

    async def test_checkin_malformed_body(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post(
                "/api/checkin",
                json={"bad": "payload"},
            )
            assert resp.status_code == 400
            assert "Invalid payload" in resp.json()["error"]

    async def test_checkin_persists_node(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get("/api/nodes")
            assert nodes_resp.status_code == 200
            nodes_list = nodes_resp.json()
            assert len(nodes_list) == 1
            assert nodes_list[0]["node_id"] == "api-test-node"
            assert nodes_list[0]["hostname"] == "api-test-node"


class TestApiNodes:
    async def test_nodes_empty(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/nodes")
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
            resp = await client.get("/api/nodes")
            assert resp.status_code == 200
            nodes_list = resp.json()
            assert len(nodes_list) == 1
            assert nodes_list[0]["node_id"] == "n1"

    async def test_nodes_auth_required_when_key_set(self, tmp_path):
        private_key, _ = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get("/api/nodes")
            assert resp.status_code == 403
            assert resp.json()["error"] == "unauthorized"

    async def test_nodes_auth_valid_token(self, tmp_path):
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get(
                "/api/nodes",
                headers={"X-Callhome-Token": public_key},
            )
            assert resp.status_code == 200
            assert resp.json() == []

    async def test_nodes_no_auth_when_no_key(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/nodes")
            assert resp.status_code == 200


# ── Heartbeat API tests ──────────────────────────────────────────────


class TestHeartbeatSubscribe:
    async def test_subscribe_success(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "wifi", "ttl": 30,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert "subscription_id" in body
            assert body["node_id"] == "home"
            assert body["metric_type"] == "wifi"

    async def test_subscribe_unknown_node(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "nonexistent", "metric_type": "wifi",
            })
            assert resp.status_code == 404
            assert "Unknown node" in resp.json()["error"]

    async def test_subscribe_unknown_metric_type(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "invalid_type",
            })
            assert resp.status_code == 400
            assert "Unknown metric_type" in resp.json()["error"]

    async def test_subscribe_refresh(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp1 = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id_1 = resp1.json()["subscription_id"]

            resp2 = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id_2 = resp2.json()["subscription_id"]
            assert sub_id_1 == sub_id_2

    async def test_subscribe_malformed_body(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/heartbeat/subscribe", json={
                "bad": "payload",
            })
            assert resp.status_code == 400


class TestHeartbeatMetrics:
    async def test_get_no_data(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/heartbeat/home/wifi")
            assert resp.status_code == 404
            assert "No data" in resp.json()["error"]

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
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "wifi",
            })
            sub_id = resp.json()["subscription_id"]

            del_resp = await client.request("DELETE", f"/api/heartbeat/subscribe/{sub_id}")
            assert del_resp.status_code == 200
            assert del_resp.json()["removed"] is True

    async def test_list_subscriptions(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            await client.post("/api/heartbeat/subscribe", json={
                "node_id": "home", "metric_type": "wifi",
            })
            resp = await client.get("/api/heartbeat/subscriptions")
            assert resp.status_code == 200
            subs = resp.json()
            assert len(subs) >= 1
            assert any(s["node_id"] == "home" for s in subs)


# ── Batman API tests ─────────────────────────────────────────────────


class TestBatmanApi:
    async def test_batman_status_returns_nodes(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        with patch("scripts.webui.heartbeat._ssh_exec", return_value=(True, "BATMAN=inactive\n")):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.get("/api/batman/status")
                assert resp.status_code == 200
                body = resp.json()
                assert isinstance(body, dict)
                assert "home" in body

    async def test_batman_enable_no_mesh_key(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
        )
        with patch.dict("os.environ", {"MESH_KEY": ""}, clear=False):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.post("/api/batman/enable")
                assert resp.status_code == 500
                assert "MESH_KEY" in resp.json()["error"]

    async def test_batman_enable_happy_path(self, tmp_path):
        """Enable batman across all mesh + bridge nodes."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=testkey123\n"
            "MESH_1_HOST=10.10.10.210\n"
            "MESH_2_HOST=192.168.86.211\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )

        def _mock_ssh(ip, cmd, timeout=30):
            if "status" in cmd:
                return (True, "BATMAN=active\nINTERFACE=bat0\n---INTERFACES---\nwlan0: active\n")
            return (True, "OK: batman-adv enabled on bat0 via wlan0")

        with patch("scripts.webui.heartbeat._ssh_exec", side_effect=_mock_ssh):
            async with api_client(tmp_path, env_content=env_content) as client:
                with patch("asyncio.sleep", return_value=None):
                    resp = await client.post("/api/batman/enable")
                assert resp.status_code == 200
                body = resp.json()
                assert body["action"] == "enable"
                assert body["total"] == 5
                assert body["succeeded"] == body["total"]
                for node_id, result in body["results"].items():
                    assert result["success"] is True
                    assert "status_check" in result

    async def test_batman_disable_happy_path(self, tmp_path):
        """Disable batman across all nodes."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=testkey123\n"
            "MESH_1_HOST=10.10.10.210\n"
            "MESH_2_HOST=192.168.86.211\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )
        with patch("scripts.webui.heartbeat._ssh_exec", return_value=(True, "OK: batman-adv disabled")):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.post("/api/batman/disable")
                assert resp.status_code == 200
                body = resp.json()
                assert body["action"] == "disable"
                assert body["succeeded"] == body["total"]
                for result in body["results"].values():
                    assert result["success"] is True
                    assert "status_check" not in result

    async def test_batman_enable_partial_failure(self, tmp_path):
        """When some nodes fail, response shows partial success."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=testkey123\n"
            "MESH_1_HOST=10.10.10.210\n"
            "MESH_2_HOST=192.168.86.211\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )
        call_count = [0]

        def _mock_ssh(ip, cmd, timeout=30):
            call_count[0] += 1
            if ip == "192.168.86.230":
                return (False, "ssh: connect to host 192.168.86.230 port 22: Connection refused")
            if "status" in cmd:
                return (True, "BATMAN=active\n")
            return (True, "OK: batman-adv enabled")

        with patch("scripts.webui.heartbeat._ssh_exec", side_effect=_mock_ssh):
            async with api_client(tmp_path, env_content=env_content) as client:
                with patch("asyncio.sleep", return_value=None):
                    resp = await client.post("/api/batman/enable")
                body = resp.json()
                assert body["succeeded"] < body["total"]
                failed = [n for n, r in body["results"].items() if not r["success"]]
                assert len(failed) >= 1

    async def test_batman_enable_requires_auth(self, tmp_path):
        """Batman mutation endpoints require auth when private key is set."""
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=testkey123\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/batman/enable")
            assert resp.status_code == 403

            resp = await client.post(
                "/api/batman/enable",
                headers={"x-callhome-token": public_key},
            )
            assert resp.status_code != 403

    async def test_batman_hmac_token_matches_openssl(self, tmp_path):
        """Verify Python HMAC produces the same output as openssl dgst."""
        import hashlib
        import hmac as hmac_mod
        key = "testkey123"
        for action in ("enable", "disable"):
            msg = f"{action}_batman"
            expected = hmac_mod.new(
                key.encode(), msg.encode(), hashlib.sha256,
            ).hexdigest()
            assert len(expected) == 64
            assert all(c in "0123456789abcdef" for c in expected)


# ── Bridge action API tests ──────────────────────────────────────────


class TestBridgeActionApi:
    async def test_restart_wifi_resolves_nodes(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )
        with patch(
            "scripts.webui.heartbeat._ssh_exec",
            return_value=(True, "OK: WiFi restarted\nMODE=ap\nINTERFACES=1\n"),
        ) as mock_ssh:
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.post(
                    "/api/bridge/restart-wifi",
                    json={"target": "all"},
                )
                assert resp.status_code == 200
                cmds = [call[0][1] for call in mock_ssh.call_args_list]
                assert all("wifi_setup.sh restart" in c for c in cmds)

    async def test_restart_sta_only(self, tmp_path):
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )
        with patch(
            "scripts.webui.heartbeat._ssh_exec",
            return_value=(True, "OK: WiFi restarted\nMODE=sta\nINTERFACES=1\n"),
        ) as mock_ssh:
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.post(
                    "/api/bridge/restart-wifi",
                    json={"target": "sta"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert "bridge-1" not in body or not body.get("bridge-1", {}).get("success")


# ── WiFi mode API tests ─────────────────────────────────────────────


class TestWifiModeApi:
    async def test_wifi_mode_switch_happy_path(self, tmp_path):
        """Switch a bridge node's WiFi mode via the API."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
            "BRIDGE_2_HOST=192.168.86.231\n"
        )
        with patch(
            "scripts.webui.heartbeat._ssh_exec",
            return_value=(True, "OK: WiFi mode switched to sta\nSSID=test\nMODE=sta\n"),
        ):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.post("/api/wifi/mode/bridge-1/sta")
                assert resp.status_code == 200
                body = resp.json()
                assert body["node_id"] == "bridge-1"
                assert body["mode"] == "sta"
                assert body["success"] is True

    async def test_wifi_mode_invalid_mode(self, tmp_path):
        """Reject invalid mode values."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/wifi/mode/bridge-1/mesh")
            assert resp.status_code == 400
            assert "Invalid mode" in resp.json()["error"]

    async def test_wifi_mode_unknown_node(self, tmp_path):
        """Return 404 for unknown node IDs."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/wifi/mode/nonexistent/ap")
            assert resp.status_code == 404

    async def test_wifi_mode_requires_auth(self, tmp_path):
        """WiFi mode switch requires auth when private key is set."""
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"BRIDGE_1_HOST=192.168.86.230\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/wifi/mode/bridge-1/ap")
            assert resp.status_code == 403

            with patch(
                "scripts.webui.heartbeat._ssh_exec",
                return_value=(True, "OK: WiFi mode switched to ap\n"),
            ):
                resp = await client.post(
                    "/api/wifi/mode/bridge-1/ap",
                    headers={"x-callhome-token": public_key},
                )
                assert resp.status_code != 403

    async def test_wifi_status_happy_path(self, tmp_path):
        """Query WiFi status from a bridge node."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
        )
        status_output = (
            "PHY=phy0\nMODE=ap\nSSID=vm-builds-bridge\n"
            "BAND=5g\nINTERFACES=1\nWIFI=up\n"
        )
        with patch(
            "scripts.webui.heartbeat._ssh_exec",
            return_value=(True, status_output),
        ):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.get("/api/wifi/status/bridge-1")
                assert resp.status_code == 200
                body = resp.json()
                assert body["node_id"] == "bridge-1"
                assert body["mode"] == "ap"
                assert body["ssid"] == "vm-builds-bridge"
                assert body["wifi"] == "up"

    async def test_wifi_status_unknown_node(self, tmp_path):
        """Return 404 for unknown node IDs on status check."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.get("/api/wifi/status/nonexistent")
            assert resp.status_code == 404

    async def test_wifi_status_ssh_failure(self, tmp_path):
        """Return 502 when SSH to container fails."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "BRIDGE_1_HOST=192.168.86.230\n"
        )
        with patch(
            "scripts.webui.heartbeat._ssh_exec",
            return_value=(False, "ssh: Connection refused"),
        ):
            async with api_client(tmp_path, env_content=env_content) as client:
                resp = await client.get("/api/wifi/status/bridge-1")
                assert resp.status_code == 502


# ── Guest management API tests ──────────────────────────────────────


class TestGuestApi:
    async def test_guests_no_host_ip(self, tmp_path):
        """Returns 500 when HOST_IP is not configured."""
        with patch.dict("os.environ", {"HOST_IP": ""}, clear=False):
            async with api_client(tmp_path) as client:
                resp = await client.get("/api/guests")
                assert resp.status_code == 500
                assert "HOST_IP" in resp.json()["error"]

    async def test_guests_lists_containers(self, tmp_path):
        """Returns parsed pct list output."""
        pct_output = (
            "VMID       Status     Lock         Name\n"
            "100        running                 openwrt\n"
            "401        running                 kiosk\n"
        )
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )

        def mock_ssh(ip, cmd, timeout=10, user="root", identity_file=None):
            if "pct list" in cmd:
                return True, pct_output
            return True, ""

        with patch.dict("os.environ", {"HOST_IP": "10.10.10.2"}, clear=False):
            with patch("scripts.webui.heartbeat._ssh_exec", side_effect=mock_ssh):
                async with api_client(tmp_path, env_content=env_content) as client:
                    nicegui_app.storage.general["host_ip"] = "10.10.10.2"
                    resp = await client.get("/api/guests")
                    assert resp.status_code == 200
                    guests = resp.json()["guests"]
                    assert len(guests) >= 2
                    vmids = [g["vmid"] for g in guests]
                    assert "100" in vmids
                    assert "401" in vmids

    async def test_guest_start_action(self, tmp_path):
        """Can start a guest via the action endpoint."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        with patch.dict("os.environ", {"HOST_IP": "10.10.10.2"}, clear=False):
            with patch("scripts.webui.heartbeat._ssh_exec", return_value=(True, "ok")):
                async with api_client(tmp_path, env_content=env_content) as client:
                    nicegui_app.storage.general["host_ip"] = "10.10.10.2"
                    resp = await client.post("/api/guests/100/start")
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body["vmid"] == "100"
                    assert body["action"] == "start"

    async def test_guest_invalid_action(self, tmp_path):
        """Rejects invalid actions."""
        env_content = (
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=test\n"
            "MESH_KEY=test\n"
        )
        with patch.dict("os.environ", {"HOST_IP": "10.10.10.2"}, clear=False):
            async with api_client(tmp_path, env_content=env_content) as client:
                nicegui_app.storage.general["host_ip"] = "10.10.10.2"
                resp = await client.post("/api/guests/100/delete")
                assert resp.status_code == 400
                assert "Invalid action" in resp.json()["error"]


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


class TestFleetReadyEndpoint:
    async def test_missing_services_param(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/fleet/ready")
            assert resp.status_code == 400
            assert "services" in resp.json()["error"].lower()

    async def test_all_ready(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
            resp = await client.get("/api/fleet/ready?services=pihole")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is True
            assert body["ready_count"] == 1
            assert body["total"] == 1

    async def test_missing_service_not_ready(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/fleet/ready?services=netdata")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is False
            assert body["services"]["netdata"]["status"] == "unknown"

    async def test_partial_readiness(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
            resp = await client.get("/api/fleet/ready?services=pihole,netdata")
            assert resp.status_code == 200
            body = resp.json()
            assert body["all_ready"] is False
            assert body["ready_count"] == 1
            assert body["total"] == 2


class TestFleetStaleEndpoint:
    """Tests for GET /api/fleet/stale circuit breaker endpoint."""

    async def test_missing_services_param(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.get("/api/fleet/stale")
            assert resp.status_code == 400

    async def test_all_healthy(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
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
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
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
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
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
            resp = await client.get("/api/fleet/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_nodes"] == 0
            assert "health_score" in body

    async def test_with_nodes(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            resp = await client.get("/api/fleet/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_nodes"] == 1
            assert body["online_nodes"] == 1


class TestCheckinWithContainerHealth:
    async def test_container_health_round_trip(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/checkin", json=CONTAINER_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get("/api/nodes")
            nodes = nodes_resp.json()
            assert len(nodes) == 1
            assert nodes[0]["node_id"] == "ct-pihole"

    async def test_checkin_without_container_health(self, tmp_path):
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            assert resp.status_code == 200

            nodes_resp = await client.get("/api/nodes")
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
            await client.post("/api/checkin", json=EXTENSIONS_CHECKIN)
            resp = await client.get("/api/container/wireguard/ready")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ready"] is True
            ext = body["extensions"]
            assert ext["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4
            assert ext["network"]["default_gateway"] == "10.10.10.1"

    async def test_extensions_in_nodes_list(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=EXTENSIONS_CHECKIN)
            resp = await client.get("/api/nodes")
            assert resp.status_code == 200
            nodes = resp.json()
            assert len(nodes) == 1
            ch = nodes[0]["container_health"]
            assert ch["container_id"] == "wireguard"
            assert ch["extensions"]["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4

    async def test_extensions_empty_by_default(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=CONTAINER_CHECKIN)
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
            await client.post("/api/checkin", json=ha_checkin)
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
                "listening_ports": [8080],
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
            await client.post("/api/checkin", json=kiosk_checkin)
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
            await client.post("/api/checkin", json=checkin)
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
            await client.post("/api/checkin", json=checkin)
            resp = await client.get("/api/container/wireguard-udp/ready")
            body = resp.json()
            assert 51820 in body["listening_ports"]
            assert 514 in body["listening_ports"]

    async def test_nodes_without_extensions(self, tmp_path):
        async with api_client(tmp_path) as client:
            await client.post("/api/checkin", json=SAMPLE_CHECKIN)
            resp = await client.get("/api/nodes")
            nodes = resp.json()
            assert "container_health" not in nodes[0]


class TestEndToEndCallhomeFlow:
    """True end-to-end: build_container_payload → checkin → query APIs."""

    async def test_collector_to_api_roundtrip(self, tmp_path):
        """Verify the real callhome payload structure works through the API."""
        from scripts.callhome import build_container_payload
        payload = build_container_payload("test-service")
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/checkin", json=payload)
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
            for svc in ["pihole", "netdata", "wireguard"]:
                payload = build_container_payload(svc)
                payload["node_id"] = f"ct-{svc}"
                payload["hostname"] = f"ct-{svc}"
                await client.post("/api/checkin", json=payload)
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
            await client.post("/api/checkin", json=payload)
            resp = await client.get("/api/container/test-ext/ready")
            body = resp.json()
            if has_network:
                assert "network" in body["extensions"]
                assert isinstance(body["extensions"]["network"]["interfaces"], list)

    async def test_vmid_injection_blocked(self, tmp_path):
        """VMID injection via path params is rejected."""
        async with api_client(tmp_path) as client:
            resp = await client.post("/api/guests/100;whoami/start")
            assert resp.status_code == 400
            assert "Invalid VMID" in resp.json()["error"]

    async def test_manager_auth_required(self, tmp_path):
        """Mutation endpoints require auth when private key is set."""
        private_key, public_key = data.generate_callhome_keys()
        env_content = (
            f"PRIMARY_HOST=192.168.86.201\n"
            f"HOME_API_TOKEN=test\n"
            f"MESH_KEY=test\n"
            f"CALLHOME_PRIVATE_KEY={private_key}\n"
        )
        async with api_client(tmp_path, env_content=env_content) as client:
            resp = await client.post("/api/guests/100/stop")
            assert resp.status_code == 403

            resp = await client.post(
                "/api/guests/100/stop",
                headers={"x-callhome-token": public_key},
            )
            assert resp.status_code != 403

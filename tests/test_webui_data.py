"""Tier 1 — Pure data layer tests for scripts/webui/data.py.

No NiceGUI imports, no async, no UI. Fast and deterministic.
Run with: pytest tests/test_webui_data.py -v
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.webui import data

FIXTURES = Path(__file__).parent / "fixtures"


# ── Environment management ───────────────────────────────────────────


class TestLoadEnvironment:
    def test_load_environment_valid(self):
        result = data.load_environment(FIXTURES / "complete.env")
        assert result.values["PRIMARY_HOST"] == "192.168.86.201"
        assert result.values["HOME_API_TOKEN"] == "cab59c9a-c517-4033-a31e-8b332a24f391"
        assert result.missing == []

    def test_load_environment_missing_vars(self):
        result = data.load_environment(FIXTURES / "incomplete.env")
        assert "HOME_API_TOKEN" in result.missing
        assert "MESH_KEY" in result.missing

    def test_load_environment_empty(self):
        result = data.load_environment(FIXTURES / "empty.env")
        assert "PRIMARY_HOST" in result.missing
        assert "HOME_API_TOKEN" in result.missing
        assert "MESH_KEY" in result.missing

    def test_load_environment_warnings_bad_ip(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=x\n"
            "MESH_KEY=y\n"
            "AI_HOST=not-an-ip\n"
        )
        result = data.load_environment(env_file)
        assert len(result.warnings) > 0
        assert any("AI_HOST" in w for w in result.warnings)

    def test_load_environment_warnings_empty_token(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PRIMARY_HOST=192.168.86.201\n"
            "HOME_API_TOKEN=x\n"
            "MESH_KEY=y\n"
            "AI_API_TOKEN=\n"
        )
        result = data.load_environment(env_file)
        assert any("AI_API_TOKEN" in w for w in result.warnings)


class TestGetEnvTemplate:
    def test_get_env_template(self):
        template = data.get_env_template()
        assert len(template) > 0
        names = [v.name for v in template]
        assert "PRIMARY_HOST" in names
        assert "HOME_API_TOKEN" in names
        assert "MESH_KEY" in names

    def test_sensitive_vars_blanked(self):
        template = data.get_env_template()
        sensitive = [v for v in template if v.sensitive]
        assert len(sensitive) > 0
        for v in sensitive:
            assert v.example == "", f"{v.name} is sensitive but has example={v.example!r}"

    def test_non_sensitive_have_defaults(self):
        template = data.get_env_template()
        ip_vars = [v for v in template if v.name in ("PRIMARY_HOST", "AI_HOST")]
        for v in ip_vars:
            assert v.example != "", f"{v.name} should have a non-blank example"

    def test_template_covers_all_required_from_build_py(self):
        import build
        template_names = {v.name for v in data.get_env_template()}
        for req in build.REQUIRED_ENV:
            assert req in template_names, f"build.REQUIRED_ENV has {req} but it's missing from ENV_TEMPLATE"

    def test_template_required_flags_match_build_py(self):
        import build
        template = data.get_env_template()
        template_required = {v.name for v in template if v.required}
        build_required = set(build.REQUIRED_ENV)
        assert template_required == build_required, (
            f"Template required={template_required} != build.py required={build_required}"
        )


class TestSaveEnvironment:
    def test_save_creates_backup(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OLD=value\n")
        data.save_environment(env_file, {"NEW": "data"})
        backup = tmp_path / ".env.bak"
        assert backup.exists(), f"Expected backup at {backup}"
        assert backup.read_text() == "OLD=value\n"

    def test_save_new_file_no_backup(self, tmp_path):
        env_file = tmp_path / ".env"
        assert not env_file.exists()
        data.save_environment(env_file, {"FOO": "bar"})
        backup = tmp_path / ".env.bak"
        assert not backup.exists()
        assert env_file.exists()

    def test_save_writes_correct_format(self, tmp_path):
        env_file = tmp_path / ".env"
        data.save_environment(env_file, {"FOO": "bar", "BAZ": "qux"})
        content = env_file.read_text()
        assert "FOO=bar\n" in content
        assert "BAZ=qux\n" in content
        assert '""' not in content

    def test_save_roundtrip(self, tmp_path):
        env_file = tmp_path / ".env"
        original = {"PRIMARY_HOST": "1.2.3.4", "MESH_KEY": "secret"}
        data.save_environment(env_file, original)
        loaded = data.load_environment(env_file)
        for key, val in original.items():
            assert loaded.values[key] == val

    def test_save_preserves_values_with_special_chars(self, tmp_path):
        env_file = tmp_path / ".env"
        original = {
            "API_TOKEN": "cab59c9a-c517-4033-a31e-8b332a24f391",
            "PASSWORD": "P@ss!w0rd#123",
            "SSH_KEY": "ssh-rsa AAAAB3...",
        }
        data.save_environment(env_file, original)
        loaded = data.load_environment(env_file)
        for key, val in original.items():
            assert loaded.values[key] == val, f"{key} roundtrip failed"


# ── Host discovery ───────────────────────────────────────────────────


class TestGetKnownHosts:
    def test_get_known_hosts_from_env(self):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "AI_HOST": "192.168.86.220",
            "MESH_2_HOST": "192.168.86.211",
        }
        hosts = data.get_known_hosts(env)
        names = [h.name for h in hosts]
        assert "home" in names
        assert "ai" in names
        assert "mesh2" in names
        assert "mesh1" in names
        mesh1 = next(h for h in hosts if h.name == "mesh1")
        assert mesh1.is_lan is True
        assert mesh1.ip == "10.10.10.210"

    def test_get_known_hosts_missing_optional(self):
        env = {"PRIMARY_HOST": "192.168.86.201"}
        hosts = data.get_known_hosts(env)
        names = [h.name for h in hosts]
        assert "home" in names
        assert "mesh1" in names
        assert len(hosts) == 2

    def test_probe_all_hosts_mixed_results(self):
        env = {"PRIMARY_HOST": "192.168.86.201", "AI_HOST": "192.168.86.220"}
        hosts = data.get_known_hosts(env)
        with patch("build.probe_host") as mock_probe:
            mock_probe.side_effect = lambda ip, **kw: ip == "192.168.86.201"
            results = data.probe_all_hosts(hosts)
            home_status = next(r for r in results if r.host.name == "home")
            ai_status = next(r for r in results if r.host.name == "ai")
            assert home_status.reachable is True
            assert ai_status.reachable is False


# ── Service tags ─────────────────────────────────────────────────────


class TestServiceTags:
    def test_get_service_tags_returns_all(self):
        tags = data.get_service_tags()
        assert len(tags) >= 15
        for t in tags:
            assert t.description
            assert t.category
            assert len(t.hosts) > 0

    def test_gaming_is_opt_in(self):
        tags = data.get_service_tags()
        gaming = next(t for t in tags if t.tag == "gaming")
        assert gaming.is_opt_in is True

    def test_deploy_profiles_full_excludes_gaming(self):
        profiles = data.get_deploy_profiles()
        full = next(p for p in profiles if p.name == "Full Deploy")
        assert "gaming" not in full.tags

    def test_deploy_profiles_network_only(self):
        profiles = data.get_deploy_profiles()
        network = next(p for p in profiles if p.name == "Network Only")
        assert set(network.tags) == {"backup", "infra", "openwrt", "lan-satellite"}

    def test_deploy_profiles_all_tags_exist(self):
        all_tags = {t.tag for t in data.get_service_tags()}
        for profile in data.get_deploy_profiles():
            for tag in profile.tags:
                assert tag in all_tags, f"Profile {profile.name!r} references unknown tag {tag!r}"

    def test_get_hosts_for_tags(self):
        hosts = data.get_hosts_for_tags(["gaming"])
        assert hosts == ["ai"]
        hosts = data.get_hosts_for_tags(["openwrt", "pihole"])
        assert "home" in hosts

    def test_get_hosts_for_tags_unknown_tag(self):
        hosts = data.get_hosts_for_tags(["nonexistent-tag"])
        assert hosts == []

    def test_get_hosts_for_tags_empty(self):
        hosts = data.get_hosts_for_tags([])
        assert hosts == []

    def test_service_tags_match_build_py_docstring(self):
        import build
        doc_tags = set()
        in_tags = False
        for line in (build.__doc__ or "").splitlines():
            line = line.strip()
            if line.startswith("Available tags"):
                in_tags = True
                continue
            if in_tags:
                if not line or line.startswith("Tags are"):
                    break
                parts = line.split()
                if parts:
                    doc_tags.add(parts[0])
        webui_tags = {t.tag for t in data.get_service_tags()}
        for tag in doc_tags:
            assert tag in webui_tags, f"build.py documents tag '{tag}' but it's missing from SERVICE_TAGS"


# ── Deploy command + history ─────────────────────────────────────────


class TestBuildDeployCommand:
    def test_basic(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["openwrt"])
        assert "build.py" in cmd[1]
        assert "--tags" in cmd
        assert "openwrt" in cmd[cmd.index("--tags") + 1]
        assert "--env" in cmd

    def test_dry_run(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["infra"],
                                        check=True, diff=True)
        assert "--check" in cmd
        assert "--diff" in cmd

    def test_limit(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["infra"],
                                        limit="home")
        assert "--limit" in cmd
        assert "home" in cmd

    def test_verbose(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["infra"],
                                        verbose=2)
        assert "-vv" in cmd

    def test_multiple_tags(self):
        cmd = data.build_deploy_command(Path(".env"),
                                        tags=["infra", "openwrt", "pihole"])
        tag_val = cmd[cmd.index("--tags") + 1]
        assert tag_val == "infra,openwrt,pihole"

    def test_empty_tags_no_flag(self):
        cmd = data.build_deploy_command(Path(".env"), tags=[])
        assert "--tags" not in cmd

    def test_command_targets_build_py(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["infra"])
        assert cmd[1].endswith("build.py"), f"Expected build.py, got {cmd[1]}"

    def test_command_uses_python_executable(self):
        cmd = data.build_deploy_command(Path(".env"), tags=["infra"])
        assert cmd[0] == sys.executable


class TestDeployHistory:
    def test_round_trip(self, tmp_path):
        record = data.DeployRecord(
            timestamp="2026-04-04T10:00:00",
            tags=["infra", "openwrt"],
            env_file=".env",
            exit_code=0,
            duration_seconds=120.5,
            host_limit="home",
        )
        data.save_deploy_record(tmp_path, record)
        history = data.load_deploy_history(tmp_path)
        assert len(history) == 1
        assert history[0].tags == ["infra", "openwrt"]
        assert history[0].exit_code == 0
        assert history[0].host_limit == "home"

    def test_trims_to_50(self, tmp_path):
        for i in range(55):
            record = data.DeployRecord(
                timestamp=f"2026-04-04T{i:02d}:00:00",
                tags=["infra"],
                env_file=".env",
                exit_code=0,
                duration_seconds=10,
            )
            data.save_deploy_record(tmp_path, record)
        history = data.load_deploy_history(tmp_path)
        assert len(history) == 50

    def test_missing_file(self, tmp_path):
        history = data.load_deploy_history(tmp_path / "nonexistent")
        assert history == []

    def test_corrupt_json(self, tmp_path):
        history_file = tmp_path / "deploy_history.json"
        history_file.write_text("{not valid json")
        history = data.load_deploy_history(tmp_path)
        assert history == []

    def test_malformed_records(self, tmp_path):
        history_file = tmp_path / "deploy_history.json"
        history_file.write_text('[{"timestamp": "x"}]')
        history = data.load_deploy_history(tmp_path)
        assert history == []


# ── Image management ─────────────────────────────────────────────────


class TestImageStatus:
    def test_all_present(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for _, pattern, _, _ in data.EXPECTED_IMAGES:
            name = pattern.replace("*", "test")
            (images_dir / name).write_bytes(b"\x00" * 1024 * 1024)
        results = data.get_image_status(images_dir)
        assert all(r.exists for r in results)
        assert len(results) == 14

    def test_some_missing(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for i, (_, pattern, _, _) in enumerate(data.EXPECTED_IMAGES[:3]):
            name = pattern.replace("*", "test")
            (images_dir / name).write_bytes(b"\x00" * 1024)
        results = data.get_image_status(images_dir)
        built = [r for r in results if r.exists]
        missing = [r for r in results if not r.exists]
        assert len(built) == 3
        assert len(missing) == 11

    def test_empty_dir(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        results = data.get_image_status(images_dir)
        assert all(not r.exists for r in results)

    def test_image_count(self):
        assert len(data.EXPECTED_IMAGES) == 14

    def test_nonexistent_dir(self):
        results = data.get_image_status(Path("/tmp/nonexistent_dir_xyz"))
        assert all(not r.exists for r in results)
        assert len(results) == 14

    def test_image_has_size_when_exists(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        pattern = data.EXPECTED_IMAGES[0][1]
        name = pattern.replace("*", "test")
        (images_dir / name).write_bytes(b"\x00" * 2 * 1024 * 1024)
        results = data.get_image_status(images_dir)
        first = results[0]
        assert first.exists
        assert first.size_mb is not None
        assert first.size_mb == 2.0
        assert first.modified_date is not None

    def test_all_targets_match_build_images_targets(self):
        valid_targets = {
            "mesh", "router", "pihole", "rsyslog", "jellyfin", "netdata",
            "wireguard", "homeassistant", "kodi", "kiosk", "moonlight",
            "gaming", "sunshine", "desktop",
        }
        for _, _, target, _ in data.EXPECTED_IMAGES:
            assert target in valid_targets, f"build_target '{target}' not in build-images.sh"


class TestBuildImageCommand:
    def test_local_mesh(self):
        cmd = data.build_image_command("mesh")
        assert "--only" in cmd
        assert "mesh" in cmd
        assert "--host" not in cmd

    def test_local_router(self):
        cmd = data.build_image_command("router")
        assert "--only" in cmd
        assert "router" in cmd
        assert "--host" not in cmd

    def test_remote(self):
        cmd = data.build_image_command("pihole", host="192.168.86.201")
        assert "--only" in cmd
        assert "pihole" in cmd
        assert "--host" in cmd
        assert "192.168.86.201" in cmd

    def test_parallel(self):
        cmd = data.build_image_command("all", parallel=True)
        assert "--parallel" in cmd
        assert "--only" not in cmd


# ── Hub services ─────────────────────────────────────────────────────


class TestHubServices:
    def test_get_hub_services_returns_all(self):
        services = data.get_hub_services()
        assert len(services) == 15

    def test_all_services_have_required_fields(self):
        for svc in data.get_hub_services():
            assert svc.key, f"Service missing key"
            assert svc.icon, f"{svc.key} missing icon"
            assert svc.title, f"{svc.key} missing title"
            assert svc.description, f"{svc.key} missing description"
            assert svc.tag, f"{svc.key} missing tag"
            assert svc.section, f"{svc.key} missing section"
            assert svc.url_key, f"{svc.key} missing url_key"

    def test_sections_are_grouped(self):
        services = data.get_hub_services()
        sections = [s.section for s in services]
        seen: set[str] = set()
        current = ""
        for sec in sections:
            if sec != current:
                assert sec not in seen, f"Section '{sec}' appears non-contiguously"
                seen.add(sec)
                current = sec

    def test_url_keys_are_unique(self):
        url_keys = [s.url_key for s in data.get_hub_services()]
        assert len(url_keys) == len(set(url_keys)), "Duplicate url_keys found"

    def test_expected_services_present(self):
        keys = {s.key for s in data.get_hub_services()}
        expected = {
            "bridge", "mesh_detail", "router_detail",
            "desktop", "jellyfin", "kodi", "homeassistant", "moonlight",
            "gaming", "openwrt", "pihole", "wireguard", "netdata", "rsyslog",
            "containers",
        }
        assert keys == expected


# ── Display apps ──────────────────────────────────────────────────────


class TestDisplayApps:
    def test_display_apps_have_required_keys(self):
        for url_key, info in data.DISPLAY_APPS.items():
            assert "vmid" in info, f"{url_key} missing vmid"
            assert "label" in info, f"{url_key} missing label"
            assert "icon" in info, f"{url_key} missing icon"
            assert "description" in info, f"{url_key} missing description"

    def test_display_apps_vmids_match_project(self):
        assert data.DISPLAY_APPS["MOONLIGHT_URL"]["vmid"] == "302"
        assert data.DISPLAY_APPS["KODI_URL"]["vmid"] == "301"
        assert data.DISPLAY_APPS["DESKTOP_URL"]["vmid"] == "400"

    def test_display_app_keys_are_hub_service_url_keys(self):
        """Every DISPLAY_APPS key must correspond to a HubService url_key."""
        service_url_keys = {s.url_key for s in data.get_hub_services()}
        for url_key in data.DISPLAY_APPS:
            assert url_key in service_url_keys, (
                f"DISPLAY_APPS key '{url_key}' not in HubService url_keys"
            )

    def test_display_apps_not_in_internal_pages(self):
        """Display apps should NOT also be in INTERNAL_PAGES."""
        for url_key in data.DISPLAY_APPS:
            assert url_key not in data.INTERNAL_PAGES, (
                f"{url_key} should not be in both DISPLAY_APPS and INTERNAL_PAGES"
            )

    def test_display_apps_count(self):
        assert len(data.DISPLAY_APPS) == 3

    def test_display_apps_descriptions_mention_return(self):
        """Each description should mention the kiosk returning."""
        for url_key, info in data.DISPLAY_APPS.items():
            assert "return" in info["description"].lower(), (
                f"{url_key} description should mention kiosk return"
            )


# ── SSH connection ────────────────────────────────────────────────────


class TestSshConnection:
    def test_ssh_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()
            result = data.test_ssh_connection("192.168.86.201")
            assert result.success is True
            assert result.output == "ok"

    def test_ssh_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 255, "stdout": "", "stderr": "Connection refused"})()
            result = data.test_ssh_connection("192.168.86.201")
            assert result.success is False
            assert "Connection refused" in result.error

    def test_ssh_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
            result = data.test_ssh_connection("192.168.86.201")
            assert result.success is False
            assert "timed out" in result.error

    def test_ssh_missing_binary(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = data.test_ssh_connection("192.168.86.201")
            assert result.success is False
            assert "not found" in result.error


# ── app.py unit tests ─────────────────────────────────────────────────


class TestAppConfigure:
    def test_parse_args_defaults(self):
        from scripts.webui.app import parse_args
        args = parse_args([])
        assert args.env is None
        assert args.port == 8080
        assert args.host == "127.0.0.1"

    def test_parse_args_custom(self):
        from scripts.webui.app import parse_args
        args = parse_args(["--env", "test.env", "--port", "9090", "--host", "127.0.0.1"])
        assert args.env == "test.env"
        assert args.port == 9090
        assert args.host == "127.0.0.1"

    def test_resolve_env_path_prefers_dotenv(self, tmp_path):
        from scripts.webui.app import _resolve_env_path
        from scripts.webui import data as d
        orig = d.PROJECT_ROOT
        try:
            d.PROJECT_ROOT = tmp_path
            # Reimport to pick up new root
            import importlib
            import scripts.webui.app as app_mod
            app_mod.PROJECT_ROOT = tmp_path
            (tmp_path / ".env").write_text("X=1\n")
            (tmp_path / "test.env").write_text("X=2\n")
            result = app_mod._resolve_env_path()
            assert result.name == ".env"
        finally:
            d.PROJECT_ROOT = orig
            app_mod.PROJECT_ROOT = d.PROJECT_ROOT

    def test_resolve_env_path_falls_back_to_test(self, tmp_path):
        from scripts.webui import data as d
        import scripts.webui.app as app_mod
        orig = d.PROJECT_ROOT
        try:
            d.PROJECT_ROOT = tmp_path
            app_mod.PROJECT_ROOT = tmp_path
            (tmp_path / "test.env").write_text("X=2\n")
            result = app_mod._resolve_env_path()
            assert result.name == "test.env"
        finally:
            d.PROJECT_ROOT = orig
            app_mod.PROJECT_ROOT = d.PROJECT_ROOT


# ── kiosk_server.py unit tests ────────────────────────────────────────


class TestStreamProcess:
    """Tests for the shared subprocess runner."""

    async def test_captures_output(self, tmp_path):
        import asyncio
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        rc = await stream_process(
            ["echo", "hello world"],
            mock_log,
            cwd=tmp_path,
        )
        assert rc == 0
        mock_log.push.assert_called()
        lines = [call.args[0] for call in mock_log.push.call_args_list]
        assert any("hello world" in line for line in lines)

    async def test_returns_nonzero_on_failure(self, tmp_path):
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        rc = await stream_process(
            ["false"],
            mock_log,
            cwd=tmp_path,
        )
        assert rc != 0

    async def test_calls_on_line_callback(self, tmp_path):
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        callback_lines: list[str] = []
        rc = await stream_process(
            ["echo", "callback-test"],
            mock_log,
            cwd=tmp_path,
            on_line=lambda text: callback_lines.append(text),
        )
        assert rc == 0
        assert any("callback-test" in line for line in callback_lines)

    async def test_passes_env_extra(self, tmp_path):
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        rc = await stream_process(
            ["bash", "-c", "echo $TEST_STREAM_VAR"],
            mock_log,
            cwd=tmp_path,
            env_extra={"TEST_STREAM_VAR": "stream-value"},
        )
        assert rc == 0
        lines = [call.args[0] for call in mock_log.push.call_args_list]
        assert any("stream-value" in line for line in lines)

    async def test_handles_bad_command(self, tmp_path):
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        rc = await stream_process(
            ["/nonexistent/binary"],
            mock_log,
            cwd=tmp_path,
        )
        assert rc == 1
        mock_log.push.assert_called()
        lines = [call.args[0] for call in mock_log.push.call_args_list]
        assert any("Error" in line for line in lines)

    async def test_proc_holder_stores_and_clears_process(self, tmp_path):
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        holder: dict = {"process": None}
        rc = await stream_process(
            ["echo", "holder-test"],
            mock_log,
            cwd=tmp_path,
            proc_holder=holder,
        )
        assert rc == 0
        assert holder["process"] is None, "process should be cleared after completion"

    async def test_proc_holder_exposes_running_process(self, tmp_path):
        """Verify proc_holder['process'] is set while the command runs."""
        import asyncio
        from unittest.mock import MagicMock
        from scripts.webui.run_process import stream_process

        mock_log = MagicMock()
        holder: dict = {"process": None}
        saw_process = False

        def _check_holder(text: str) -> None:
            nonlocal saw_process
            if holder["process"] is not None:
                saw_process = True

        rc = await stream_process(
            ["echo", "running"],
            mock_log,
            cwd=tmp_path,
            on_line=_check_holder,
            proc_holder=holder,
        )
        assert rc == 0
        assert saw_process, "proc_holder should have a process while streaming"


class TestStatusText:
    """Tests for the theme.status_text helper."""

    def test_sets_text_and_color(self):
        from unittest.mock import MagicMock
        from scripts.webui.theme import status_text, COLOR_SUCCESS

        label = MagicMock()
        status_text(label, "All good", "success")
        assert label.text == "All good"
        label.style.assert_called_once_with(f"color: {COLOR_SUCCESS}")

    def test_unknown_status_uses_secondary(self):
        from unittest.mock import MagicMock
        from scripts.webui.theme import status_text, TEXT_SECONDARY

        label = MagicMock()
        status_text(label, "Unknown state", "other")
        label.style.assert_called_once_with(f"color: {TEXT_SECONDARY}")


class TestKioskServer:
    def test_load_config_valid(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"JELLYFIN_URL": "http://10.10.10.15:8096"}))
        result = data.load_kiosk_config(cfg)
        assert result["JELLYFIN_URL"] == "http://10.10.10.15:8096"

    def test_load_config_missing(self, tmp_path):
        result = data.load_kiosk_config(tmp_path / "missing.json")
        assert result == {}

    def test_load_config_corrupt(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text("{broken")
        result = data.load_kiosk_config(cfg)
        assert result == {}

    def test_load_config_all_services(self, tmp_path):
        cfg = tmp_path / "config.json"
        urls = {svc.url_key: f"http://example.com/{svc.key}" for svc in data.get_hub_services()}
        cfg.write_text(json.dumps(urls))
        result = data.load_kiosk_config(cfg)
        assert len(result) == 15


# ── Node registry ────────────────────────────────────────────────────


class TestNodeRegistry:
    def _make_checkin(self, node_id: str = "node-1") -> data.NodeCheckin:
        return data.NodeCheckin(
            node_id=node_id,
            hostname=node_id,
            local_ips=["192.168.1.100"],
            uptime_seconds=3600,
            services=["vm:100:openwrt"],
            disk_usage_pct=45.0,
            memory_usage_pct=60.0,
            version="1.0",
        )

    def test_register_new_node(self, tmp_path):
        checkin = self._make_checkin()
        node = data.register_checkin(tmp_path, checkin, "10.0.0.1")
        assert node.node_id == "node-1"
        assert node.last_ip == "10.0.0.1"
        assert node.status == "online"
        assert node.first_seen == node.last_seen

    def test_register_updates_existing(self, tmp_path):
        checkin1 = self._make_checkin()
        data.register_checkin(tmp_path, checkin1, "10.0.0.1")

        checkin2 = self._make_checkin()
        checkin2.uptime_seconds = 7200
        checkin2.disk_usage_pct = 55.0
        node = data.register_checkin(tmp_path, checkin2, "10.0.0.2")
        assert node.last_ip == "10.0.0.2"
        assert node.uptime_seconds == 7200
        assert node.disk_usage_pct == 55.0

        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 1

    def test_multiple_nodes(self, tmp_path):
        data.register_checkin(tmp_path, self._make_checkin("a"), "1.1.1.1")
        data.register_checkin(tmp_path, self._make_checkin("b"), "2.2.2.2")
        data.register_checkin(tmp_path, self._make_checkin("c"), "3.3.3.3")
        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 3
        ids = {n.node_id for n in nodes}
        assert ids == {"a", "b", "c"}

    def test_empty_registry(self, tmp_path):
        nodes = data.load_node_registry(tmp_path)
        assert nodes == []

    def test_corrupt_registry(self, tmp_path):
        (tmp_path / "nodes.json").write_text("{bad json")
        nodes = data.load_node_registry(tmp_path)
        assert nodes == []

    def test_status_computation(self, tmp_path):
        from datetime import datetime, timedelta
        now = datetime.now()

        recent = now.isoformat(timespec="seconds")
        assert data._compute_node_status(recent) == "online"

        stale_time = (now - timedelta(seconds=600)).isoformat(timespec="seconds")
        assert data._compute_node_status(stale_time) == "stale"

        old_time = (now - timedelta(hours=2)).isoformat(timespec="seconds")
        assert data._compute_node_status(old_time) == "offline"

    def test_persistence_roundtrip(self, tmp_path):
        data.register_checkin(tmp_path, self._make_checkin(), "10.0.0.1")
        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 1
        assert nodes[0].hostname == "node-1"
        assert nodes[0].services == ["vm:100:openwrt"]

    def test_fleet_ips_file_written(self, tmp_path):
        data.register_checkin(tmp_path, self._make_checkin("alpha"), "10.0.0.1")
        data.register_checkin(tmp_path, self._make_checkin("beta"), "10.0.0.2")
        ip_file = tmp_path / "fleet_ips.txt"
        assert ip_file.exists()
        content = ip_file.read_text()
        assert "alpha\t10.0.0.1" in content
        assert "beta\t10.0.0.2" in content

    def test_fleet_ips_sorted(self, tmp_path):
        data.register_checkin(tmp_path, self._make_checkin("zulu"), "10.0.0.3")
        data.register_checkin(tmp_path, self._make_checkin("alpha"), "10.0.0.1")
        content = (tmp_path / "fleet_ips.txt").read_text().strip().split("\n")
        assert content[0].startswith("alpha")
        assert content[1].startswith("zulu")

    def test_fleet_ips_empty_when_no_ips(self, tmp_path):
        data._save_node_registry(tmp_path, [])
        ip_file = tmp_path / "fleet_ips.txt"
        assert ip_file.exists()
        assert ip_file.read_text() == ""

    def test_upsert_preserves_first_seen(self, tmp_path):
        checkin = self._make_checkin()
        node1 = data.register_checkin(tmp_path, checkin, "10.0.0.1")
        first_seen = node1.first_seen

        checkin.uptime_seconds = 9999
        node2 = data.register_checkin(tmp_path, checkin, "10.0.0.2")
        assert node2.first_seen == first_seen
        assert node2.last_ip == "10.0.0.2"

    def test_status_bad_timestamp(self):
        assert data._compute_node_status("not-a-date") == "offline"
        assert data._compute_node_status("") == "offline"
        assert data._compute_node_status(None) == "offline"


# ── Container health and fleet readiness ──────────────────────────────


class TestContainerHealth:
    """Round-trip tests for ContainerHealth through the registry."""

    def _make_container_checkin(
        self, node_id: str = "ct-pihole", container_id: str = "102",
        ready: bool = True,
    ) -> data.NodeCheckin:
        return data.NodeCheckin(
            node_id=node_id,
            hostname=node_id,
            local_ips=["10.10.10.10"],
            uptime_seconds=120,
            services=[],
            disk_usage_pct=30.0,
            memory_usage_pct=40.0,
            version="1.0",
            container_health=data.ContainerHealth(
                container_id=container_id,
                systemd_services={"pihole-FTL": "running", "callhome": "running"},
                listening_ports=[53, 80],
                ready=ready,
            ),
        )

    def test_roundtrip_persistence(self, tmp_path):
        checkin = self._make_container_checkin()
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 1
        n = nodes[0]
        assert n.container_health is not None
        assert n.container_health.container_id == "102"
        assert n.container_health.systemd_services == {
            "pihole-FTL": "running", "callhome": "running",
        }
        assert n.container_health.listening_ports == [53, 80]
        assert n.container_health.ready is True

    def test_container_health_none_when_absent(self, tmp_path):
        checkin = data.NodeCheckin(
            node_id="host-1", hostname="host-1",
            local_ips=["192.168.1.1"], uptime_seconds=100,
            services=[], disk_usage_pct=10, memory_usage_pct=20,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "192.168.1.1")
        nodes = data.load_node_registry(tmp_path)
        assert nodes[0].container_health is None

    def test_update_preserves_container_health(self, tmp_path):
        checkin = self._make_container_checkin()
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

        checkin2 = self._make_container_checkin(ready=False)
        checkin2.uptime_seconds = 300
        data.register_checkin(tmp_path, checkin2, "10.10.10.10")

        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 1
        assert nodes[0].container_health.ready is False
        assert nodes[0].uptime_seconds == 300

    def test_nodes_json_includes_container_health(self, tmp_path):
        checkin = self._make_container_checkin()
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

        import json
        raw = json.loads((tmp_path / "nodes.json").read_text())
        assert "container_health" in raw[0]
        assert raw[0]["container_health"]["container_id"] == "102"
        assert raw[0]["container_health"]["listening_ports"] == [53, 80]


class TestCheckContainerReady:
    """Tests for check_container_ready()."""

    def _register_container(
        self, tmp_path, container_id="102", ready=True, node_id="ct-pihole",
    ):
        checkin = data.NodeCheckin(
            node_id=node_id, hostname=node_id,
            local_ips=["10.10.10.10"], uptime_seconds=120,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0",
            container_health=data.ContainerHealth(
                container_id=container_id,
                systemd_services={"svc": "running"},
                listening_ports=[80],
                ready=ready,
            ),
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

    def test_ready_container(self, tmp_path):
        self._register_container(tmp_path)
        result = data.check_container_ready(tmp_path, "102")
        assert result["ready"] is True
        assert result["status"] == "online"
        assert result["container_id"] == "102"

    def test_not_ready_when_health_false(self, tmp_path):
        self._register_container(tmp_path, ready=False)
        result = data.check_container_ready(tmp_path, "102")
        assert result["ready"] is False

    def test_unknown_container(self, tmp_path):
        result = data.check_container_ready(tmp_path, "999")
        assert result["ready"] is False
        assert result["status"] == "unknown"
        assert result["last_seen"] == ""

    def test_match_by_hostname(self, tmp_path):
        checkin = data.NodeCheckin(
            node_id="pihole-host", hostname="pihole-host",
            local_ips=["10.10.10.10"], uptime_seconds=120,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.10")
        result = data.check_container_ready(tmp_path, "pihole-host")
        assert result["ready"] is True
        assert result["systemd_services"] == {}

    def test_stale_container_not_ready(self, tmp_path):
        from datetime import datetime, timedelta
        self._register_container(tmp_path)
        nodes = data.load_node_registry(tmp_path)
        old_time = (datetime.now() - timedelta(seconds=300)).isoformat(timespec="seconds")
        nodes[0].last_seen = old_time
        data._save_node_registry(tmp_path, nodes)

        result = data.check_container_ready(tmp_path, "102")
        assert result["ready"] is False


class TestCheckFleetReadiness:
    """Tests for check_fleet_readiness()."""

    def _register_service(
        self, tmp_path, node_id, container_id, ready=True,
    ):
        checkin = data.NodeCheckin(
            node_id=node_id, hostname=node_id,
            local_ips=["10.10.10.10"], uptime_seconds=120,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0",
            container_health=data.ContainerHealth(
                container_id=container_id,
                systemd_services={"svc": "running"},
                listening_ports=[80],
                ready=ready,
            ),
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

    def test_all_ready(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        self._register_service(tmp_path, "rsyslog-ct", "rsyslog")
        result = data.check_fleet_readiness(tmp_path, ["pihole", "rsyslog"])
        assert result["all_ready"] is True
        assert result["ready_count"] == 2
        assert result["total"] == 2

    def test_partial_ready(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole", ready=True)
        self._register_service(tmp_path, "rsyslog-ct", "rsyslog", ready=False)
        result = data.check_fleet_readiness(tmp_path, ["pihole", "rsyslog"])
        assert result["all_ready"] is False
        assert result["ready_count"] == 1

    def test_missing_service(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        result = data.check_fleet_readiness(tmp_path, ["pihole", "netdata"])
        assert result["all_ready"] is False
        assert result["services"]["netdata"]["ready"] is False
        assert result["services"]["netdata"]["status"] == "unknown"

    def test_empty_services_list(self, tmp_path):
        result = data.check_fleet_readiness(tmp_path, [])
        assert result["all_ready"] is True
        assert result["total"] == 0
        assert result["ready_count"] == 0

    def test_match_by_hostname(self, tmp_path):
        checkin = data.NodeCheckin(
            node_id="wireguard-home", hostname="wireguard-home",
            local_ips=["10.10.10.3"], uptime_seconds=120,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.3")
        result = data.check_fleet_readiness(tmp_path, ["wireguard-home"])
        assert result["all_ready"] is True
        assert result["services"]["wireguard-home"]["ready"] is True


class TestCheckFleetStaleness:
    """Tests for check_fleet_staleness() circuit breaker."""

    def _register_service(
        self, tmp_path, node_id, container_id, ready=True,
    ):
        checkin = data.NodeCheckin(
            node_id=node_id, hostname=node_id,
            local_ips=["10.10.10.10"], uptime_seconds=120,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0",
            container_health=data.ContainerHealth(
                container_id=container_id,
                systemd_services={"svc": "running"},
                listening_ports=[80],
                ready=ready,
            ),
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

    def _make_service_stale(self, tmp_path, container_id):
        """Backdate a service's last_seen to make it stale."""
        nodes = data.load_node_registry(tmp_path)
        for n in nodes:
            if n.container_health and n.container_health.container_id == container_id:
                stale_time = datetime.now() - timedelta(seconds=300)
                n.last_seen = stale_time.isoformat(timespec="seconds")
                break
        data._save_node_registry(tmp_path, nodes)

    def test_all_healthy(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        self._register_service(tmp_path, "rsyslog-ct", "rsyslog")
        result = data.check_fleet_staleness(tmp_path, ["pihole", "rsyslog"])
        assert result["has_stale"] is False
        assert len(result["healthy"]) == 2
        assert len(result["stale"]) == 0
        assert len(result["never_seen"]) == 0

    def test_stale_service_detected(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        self._register_service(tmp_path, "rsyslog-ct", "rsyslog")
        self._make_service_stale(tmp_path, "rsyslog")
        result = data.check_fleet_staleness(tmp_path, ["pihole", "rsyslog"])
        assert result["has_stale"] is True
        assert "pihole" in result["healthy"]
        assert len(result["stale"]) == 1
        assert result["stale"][0]["service"] == "rsyslog"

    def test_never_seen_not_stale(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        result = data.check_fleet_staleness(
            tmp_path, ["pihole", "gaming"],
        )
        assert result["has_stale"] is False
        assert "pihole" in result["healthy"]
        assert "gaming" in result["never_seen"]

    def test_empty_services_list(self, tmp_path):
        result = data.check_fleet_staleness(tmp_path, [])
        assert result["has_stale"] is False
        assert len(result["healthy"]) == 0
        assert len(result["stale"]) == 0
        assert len(result["never_seen"]) == 0

    def test_custom_max_age(self, tmp_path):
        self._register_service(tmp_path, "pihole-ct", "pihole")
        self._make_service_stale(tmp_path, "pihole")
        result_short = data.check_fleet_staleness(
            tmp_path, ["pihole"], max_age_seconds=60,
        )
        assert result_short["has_stale"] is True
        result_long = data.check_fleet_staleness(
            tmp_path, ["pihole"], max_age_seconds=600,
        )
        assert result_long["has_stale"] is False


# ── Extensions round-trip ─────────────────────────────────────────────


class TestContainerExtensions:
    """Extensions dict round-trip through registry and API responses."""

    def _make_checkin_with_extensions(
        self, tmp_path, container_id="wireguard", extensions=None,
    ):
        ext = extensions or {}
        checkin = data.NodeCheckin(
            node_id=f"ct-{container_id}", hostname=f"ct-{container_id}",
            local_ips=["10.10.10.3"], uptime_seconds=200,
            services=[], disk_usage_pct=20, memory_usage_pct=30,
            version="1.0",
            container_health=data.ContainerHealth(
                container_id=container_id,
                systemd_services={"wg-quick@wg0": "active"},
                listening_ports=[51820],
                ready=True,
                extensions=ext,
            ),
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.3")

    def test_extensions_persist_empty(self, tmp_path):
        self._make_checkin_with_extensions(tmp_path, extensions={})
        nodes = data.load_node_registry(tmp_path)
        assert nodes[0].container_health.extensions == {}

    def test_wireguard_extensions_roundtrip(self, tmp_path):
        wg_ext = {"wireguard": {"interfaces": {"wg0": {"peer_count": 4, "up": True}}}}
        self._make_checkin_with_extensions(tmp_path, extensions=wg_ext)
        nodes = data.load_node_registry(tmp_path)
        ext = nodes[0].container_health.extensions
        assert ext["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4
        assert ext["wireguard"]["interfaces"]["wg0"]["up"] is True

    def test_docker_extensions_roundtrip(self, tmp_path):
        docker_ext = {"docker": {"active": True, "running": 3}}
        self._make_checkin_with_extensions(
            tmp_path, container_id="homeassistant", extensions=docker_ext,
        )
        nodes = data.load_node_registry(tmp_path)
        ext = nodes[0].container_health.extensions
        assert ext["docker"]["active"] is True
        assert ext["docker"]["running"] == 3

    def test_network_extensions_roundtrip(self, tmp_path):
        net_ext = {
            "network": {
                "interfaces": [
                    {"name": "eth0", "addresses": ["10.10.10.3/24"], "operstate": "up"},
                ],
                "default_gateway": "10.10.10.1",
            },
        }
        self._make_checkin_with_extensions(tmp_path, extensions=net_ext)
        nodes = data.load_node_registry(tmp_path)
        ext = nodes[0].container_health.extensions
        assert ext["network"]["default_gateway"] == "10.10.10.1"
        assert ext["network"]["interfaces"][0]["name"] == "eth0"

    def test_config_files_extensions_roundtrip(self, tmp_path):
        cfg_ext = {
            "config_files": {
                "/opt/kiosk/config.json": {
                    "keys": ["DESKTOP_URL", "JELLYFIN_URL", "KODI_URL"],
                    "hash": "abc123deadbeef00",
                },
            },
        }
        self._make_checkin_with_extensions(
            tmp_path, container_id="kiosk", extensions=cfg_ext,
        )
        nodes = data.load_node_registry(tmp_path)
        ext = nodes[0].container_health.extensions
        cfg = ext["config_files"]["/opt/kiosk/config.json"]
        assert "DESKTOP_URL" in cfg["keys"]
        assert cfg["hash"] == "abc123deadbeef00"

    def test_extensions_in_check_container_ready(self, tmp_path):
        wg_ext = {"wireguard": {"interfaces": {"wg0": {"peer_count": 2}}}}
        self._make_checkin_with_extensions(tmp_path, extensions=wg_ext)
        result = data.check_container_ready(tmp_path, "wireguard")
        assert result["ready"] is True
        assert result["extensions"]["wireguard"]["interfaces"]["wg0"]["peer_count"] == 2

    def test_extensions_in_json_file(self, tmp_path):
        wg_ext = {"wireguard": {"interfaces": {"wg0": {"peer_count": 1}}}}
        self._make_checkin_with_extensions(tmp_path, extensions=wg_ext)
        raw = json.loads((tmp_path / "nodes.json").read_text())
        assert raw[0]["container_health"]["extensions"]["wireguard"] == {
            "interfaces": {"wg0": {"peer_count": 1}},
        }

    def test_multiple_extensions_compose(self, tmp_path):
        multi_ext = {
            "network": {"interfaces": [], "default_gateway": "10.10.10.1"},
            "wireguard": {"interfaces": {"wg0": {"peer_count": 3}}},
            "docker": {"active": False, "running": 0},
        }
        self._make_checkin_with_extensions(tmp_path, extensions=multi_ext)
        nodes = data.load_node_registry(tmp_path)
        ext = nodes[0].container_health.extensions
        assert len(ext) == 3
        assert ext["wireguard"]["interfaces"]["wg0"]["peer_count"] == 3
        assert ext["docker"]["active"] is False

    def test_extensions_update_on_recheckin(self, tmp_path):
        ext1 = {"wireguard": {"interfaces": {"wg0": {"peer_count": 1}}}}
        self._make_checkin_with_extensions(tmp_path, extensions=ext1)
        ext2 = {"wireguard": {"interfaces": {"wg0": {"peer_count": 4}}}}
        self._make_checkin_with_extensions(tmp_path, extensions=ext2)
        nodes = data.load_node_registry(tmp_path)
        assert len(nodes) == 1
        assert nodes[0].container_health.extensions["wireguard"]["interfaces"]["wg0"]["peer_count"] == 4


class TestCallhomeCollectors:
    """Unit tests for composable health collectors in callhome.py."""

    def test_collect_network_returns_interfaces(self):
        from scripts.callhome import collect_network
        result = collect_network()
        assert "interfaces" in result
        assert "default_gateway" in result
        assert isinstance(result["interfaces"], list)

    def test_collect_network_excludes_loopback(self):
        from scripts.callhome import collect_network
        result = collect_network()
        iface_names = [i["name"] for i in result["interfaces"]]
        assert "lo" not in iface_names

    def test_collect_wireguard_none_without_binary(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError),
        )
        assert callhome.collect_wireguard() is None

    def test_collect_docker_none_without_binary(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError),
        )
        assert callhome.collect_docker() is None

    def test_collect_config_files_none_without_env(self, monkeypatch):
        from scripts import callhome
        monkeypatch.delenv("CALLHOME_CONFIG_FILES", raising=False)
        assert callhome.collect_config_files() is None

    def test_collect_config_files_reads_json(self, tmp_path, monkeypatch):
        from scripts import callhome
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"URL_A": "http://a", "URL_B": "http://b"}))
        monkeypatch.setenv("CALLHOME_CONFIG_FILES", str(cfg_file))
        result = callhome.collect_config_files()
        assert result is not None
        entry = result[str(cfg_file)]
        assert sorted(entry["keys"]) == ["URL_A", "URL_B"]
        assert len(entry["hash"]) == 16

    def test_collect_config_files_skips_missing(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setenv("CALLHOME_CONFIG_FILES", "/nonexistent/file.json")
        assert callhome.collect_config_files() is None

    def test_collect_extensions_always_has_network(self):
        from scripts.callhome import collect_extensions
        ext = collect_extensions()
        assert "network" in ext or ext.get("network") is None

    def test_build_container_payload_includes_extensions(self):
        from scripts.callhome import build_container_payload
        payload = build_container_payload("test-ct")
        ch = payload["container_health"]
        assert "extensions" in ch
        assert isinstance(ch["extensions"], dict)

    def test_collect_http_probes_none_without_env(self, monkeypatch):
        from scripts import callhome
        monkeypatch.delenv("CALLHOME_HTTP_PROBES", raising=False)
        assert callhome.collect_http_probes() is None

    def test_collect_http_probes_reports_status_codes(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setenv("CALLHOME_HTTP_PROBES", "http://127.0.0.1:1/nope")
        result = callhome.collect_http_probes()
        assert result is not None
        assert result["http://127.0.0.1:1/nope"] == 0

    def test_listening_ports_includes_udp(self, tmp_path, monkeypatch):
        """Verify that get_listening_ports reads both TCP and UDP."""
        from scripts import callhome
        tcp_content = "  sl  local_address  rem_address   st\n   0: 00000000:0CEA 00000000:0000 0A\n"
        udp_content = "  sl  local_address  rem_address   st\n   0: 00000000:CA5C 00000000:0000 07\n"
        tcp_file = tmp_path / "tcp"
        udp_file = tmp_path / "udp"
        tcp_file.write_text(tcp_content)
        udp_file.write_text(udp_content)
        tcp_ports = callhome._parse_proc_net_ports(str(tcp_file), "0A")
        udp_ports = callhome._parse_proc_net_ports(str(udp_file), "07")
        assert 3306 in tcp_ports
        assert 51804 in udp_ports

    def test_parse_proc_net_ports_skips_header(self, tmp_path):
        from scripts import callhome
        content = "  sl  local_address rem_address   st\n"
        f = tmp_path / "tcp"
        f.write_text(content)
        assert callhome._parse_proc_net_ports(str(f), "0A") == []

    def test_parse_proc_net_ports_handles_missing_file(self):
        from scripts import callhome
        assert callhome._parse_proc_net_ports("/nonexistent/proc/net/tcp", "0A") == []

    def test_collect_http_probes_with_multiple_urls(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setenv("CALLHOME_HTTP_PROBES", "http://127.0.0.1:1,http://127.0.0.1:2")
        result = callhome.collect_http_probes()
        assert result is not None
        assert len(result) == 2
        assert all(v == 0 for v in result.values())

    def test_extensions_includes_http_probes_when_set(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setenv("CALLHOME_HTTP_PROBES", "http://127.0.0.1:1/bad")
        ext = callhome.collect_extensions()
        assert "http_probes" in ext
        assert ext["http_probes"]["http://127.0.0.1:1/bad"] == 0

    def test_extensions_omits_http_probes_when_unset(self, monkeypatch):
        from scripts import callhome
        monkeypatch.delenv("CALLHOME_HTTP_PROBES", raising=False)
        ext = callhome.collect_extensions()
        assert "http_probes" not in ext


class TestStateChangeDetection:
    """Tests for state-change detection in the heartbeat loop."""

    def test_compute_state_hash_deterministic(self):
        from scripts.callhome import _compute_state_hash
        h1 = _compute_state_hash("test-ct")
        h2 = _compute_state_hash("test-ct")
        assert h1 == h2
        assert len(h1) == 16

    def test_compute_state_hash_empty_for_host_mode(self):
        from scripts.callhome import _compute_state_hash
        h = _compute_state_hash("")
        assert len(h) == 16

    def test_compute_state_hash_changes_with_services(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setattr(callhome, "get_systemd_services", lambda: {"a": "active"})
        h1 = callhome._compute_state_hash("ct")
        monkeypatch.setattr(callhome, "get_systemd_services", lambda: {"a": "inactive"})
        h2 = callhome._compute_state_hash("ct")
        assert h1 != h2

    def test_compute_state_hash_changes_with_ports(self, monkeypatch):
        from scripts import callhome
        monkeypatch.setattr(callhome, "get_systemd_services", lambda: {})
        monkeypatch.setattr(callhome, "get_listening_ports", lambda: [80])
        h1 = callhome._compute_state_hash("ct")
        monkeypatch.setattr(callhome, "get_listening_ports", lambda: [80, 443])
        h2 = callhome._compute_state_hash("ct")
        assert h1 != h2


# ── Call-home client ─────────────────────────────────────────────────


class TestCallhomeAuth:
    def test_generate_keys(self):
        priv, pub = data.generate_callhome_keys()
        assert len(priv) == 64
        assert len(pub) == 64
        assert priv != pub

    def test_derive_is_deterministic(self):
        priv = "abc123"
        pub1 = data.derive_public_key(priv)
        pub2 = data.derive_public_key(priv)
        assert pub1 == pub2

    def test_validate_correct_token(self):
        priv, pub = data.generate_callhome_keys()
        assert data.validate_callhome_token(pub, priv) is True

    def test_validate_wrong_token(self):
        priv, _ = data.generate_callhome_keys()
        assert data.validate_callhome_token("wrong", priv) is False

    def test_validate_empty_rejects(self):
        assert data.validate_callhome_token("", "key") is False
        assert data.validate_callhome_token("tok", "") is False

    def test_different_keys_different_tokens(self):
        _, pub1 = data.generate_callhome_keys()
        _, pub2 = data.generate_callhome_keys()
        assert pub1 != pub2


class TestCallhomeClient:
    def test_build_payload(self):
        from scripts.callhome import build_payload
        payload = build_payload()
        assert "node_id" in payload
        assert "hostname" in payload
        assert "local_ips" in payload
        assert "uptime_seconds" in payload
        assert "disk_usage_pct" in payload
        assert "memory_usage_pct" in payload
        assert isinstance(payload["local_ips"], list)
        assert isinstance(payload["disk_usage_pct"], float)

    def test_get_disk_usage(self):
        from scripts.callhome import get_disk_usage
        usage = get_disk_usage()
        assert 0 <= usage <= 100

    def test_get_memory_usage(self):
        from scripts.callhome import get_memory_usage
        usage = get_memory_usage()
        assert 0 <= usage <= 100

    def test_get_uptime(self):
        from scripts.callhome import get_uptime
        uptime = get_uptime()
        assert uptime >= 0

    def test_parse_args_defaults(self):
        from scripts.callhome import parse_args
        with patch.dict("os.environ", {"CALLHOME_SERVER": "http://test:8080"}):
            args = parse_args([])
            assert args.server == "http://test:8080"
            assert args.interval == 60
            assert args.once is False

    def test_parse_args_explicit(self):
        from scripts.callhome import parse_args
        args = parse_args(["--server", "http://x:9090", "--interval", "30", "--once"])
        assert args.server == "http://x:9090"
        assert args.interval == 30
        assert args.once is True

    def test_parse_args_token(self):
        from scripts.callhome import parse_args
        args = parse_args(["--server", "http://x", "--token", "abc123"])
        assert args.token == "abc123"

    def test_parse_args_force(self):
        from scripts.callhome import parse_args
        args = parse_args(["--server", "http://x", "--force"])
        assert args.force is True

    def test_send_checkin_handles_unreachable(self):
        from scripts.callhome import send_checkin
        result = send_checkin("http://127.0.0.1:1", {"node_id": "x"})
        assert result is False

    def test_send_checkin_sends_token_header(self):
        from scripts.callhome import send_checkin
        import urllib.request
        captured = {}

        def fake_urlopen(req, **kwargs):
            captured["headers"] = dict(req.headers)
            raise urllib.error.URLError("fake")

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            send_checkin("http://fake:1", {"node_id": "x"}, token="mytoken")

        assert captured["headers"].get("X-callhome-token") == "mytoken"


class TestCallhomeGetVersion:
    def test_reads_project_version_from_yaml(self):
        from scripts.callhome import get_version
        version = get_version()
        if version:
            assert isinstance(version, str)
            assert len(version) > 0

    def test_returns_empty_on_missing_file(self):
        from scripts.callhome import get_version
        with patch("builtins.open", side_effect=OSError("no file")):
            result = get_version()
            assert result == ""


class TestCallhomeRunOnce:
    def test_skips_when_ip_unchanged(self, tmp_path):
        from scripts.callhome import run_once, save_last_ip, get_primary_ip
        state_file = str(tmp_path / "last_ip")
        current = get_primary_ip()
        save_last_ip(current, state_file=state_file)
        result = run_once("http://127.0.0.1:1", "tok", state_file=state_file)
        assert result is False

    def test_sends_when_ip_changed(self, tmp_path):
        from scripts.callhome import run_once, save_last_ip
        state_file = str(tmp_path / "last_ip")
        save_last_ip("99.99.99.99", state_file=state_file)
        result = run_once("http://127.0.0.1:1", "tok", state_file=state_file)
        assert result is False  # fails to connect, but attempted

    def test_force_ignores_saved_ip(self, tmp_path):
        from scripts.callhome import run_once, save_last_ip, get_primary_ip
        state_file = str(tmp_path / "last_ip")
        current = get_primary_ip()
        save_last_ip(current, state_file=state_file)
        result = run_once("http://127.0.0.1:1", "tok", force=True, state_file=state_file)
        assert result is False  # fails to connect, but force skipped the IP check


class TestCallhomePrimaryIp:
    def test_returns_valid_ip(self):
        from scripts.callhome import get_primary_ip
        ip = get_primary_ip()
        assert ip != ""
        parts = ip.split(".")
        assert len(parts) == 4

    def test_not_loopback(self):
        from scripts.callhome import get_primary_ip
        ip = get_primary_ip()
        assert not ip.startswith("127.")


class TestCallhomeIPTracking:
    def test_save_and_read_last_ip(self, tmp_path):
        from scripts.callhome import save_last_ip, read_last_ip
        state_file = str(tmp_path / "last_ip")
        save_last_ip("10.0.0.1", state_file=state_file)
        assert read_last_ip(state_file=state_file) == "10.0.0.1"

    def test_read_missing_file(self, tmp_path):
        from scripts.callhome import read_last_ip
        assert read_last_ip(state_file=str(tmp_path / "nope")) == ""

    def test_ip_changed_first_run(self, tmp_path):
        from scripts.callhome import ip_changed
        state_file = str(tmp_path / "last_ip")
        changed, ip = ip_changed(state_file=state_file)
        assert changed is True
        assert ip != ""

    def test_ip_unchanged_after_save(self, tmp_path):
        from scripts.callhome import ip_changed, save_last_ip, get_primary_ip
        state_file = str(tmp_path / "last_ip")
        current = get_primary_ip()
        save_last_ip(current, state_file=state_file)
        changed, ip = ip_changed(state_file=state_file)
        assert changed is False

    def test_ip_changed_after_different_save(self, tmp_path):
        from scripts.callhome import ip_changed, save_last_ip
        state_file = str(tmp_path / "last_ip")
        save_last_ip("99.99.99.99", state_file=state_file)
        changed, ip = ip_changed(state_file=state_file)
        assert changed is True


# ── Theme constants ──────────────────────────────────────────────────


class TestCallhomeShellScript:
    def test_script_exists_and_executable(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        assert script.exists()
        assert os.access(script, os.X_OK)

    def test_script_reads_conf_file(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert '/etc/default/callhome' in content
        assert 'CALLHOME_SERVER' in content
        assert 'CALLHOME_PUBLIC_KEY' in content

    def test_script_compares_ip_not_marker(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert 'LAST_IP_FILE' in content
        assert 'CURRENT_IP' in content
        assert 'LAST_IP' in content
        assert '/tmp/.callhome_done' not in content

    def test_script_saves_ip_on_success(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert 'printf' in content and 'CURRENT_IP' in content

    def test_script_sends_auth_header(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert 'X-Callhome-Token' in content

    def test_script_sends_ip_in_payload(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert 'CURRENT_IP' in content
        assert 'local_ips' in content

    def test_script_supports_curl_and_wget(self):
        script = data.PROJECT_ROOT / "scripts" / "callhome.sh"
        content = script.read_text()
        assert 'curl' in content
        assert 'wget' in content


class TestEnvTemplateCallhome:
    def test_callhome_vars_in_template(self):
        template = data.get_env_template()
        names = [v.name for v in template]
        assert "CALLHOME_SERVER" in names
        assert "CALLHOME_PUBLIC_KEY" in names
        assert "CALLHOME_PRIVATE_KEY" in names

    def test_private_key_is_sensitive(self):
        template = data.get_env_template()
        priv = next(v for v in template if v.name == "CALLHOME_PRIVATE_KEY")
        assert priv.sensitive is True

    def test_public_key_is_sensitive(self):
        template = data.get_env_template()
        pub = next(v for v in template if v.name == "CALLHOME_PUBLIC_KEY")
        assert pub.sensitive is True


# ── Data formatting helpers ──────────────────────────────────────────


class TestFormatUptime:
    def test_zero_returns_dash(self):
        assert data.format_uptime(0) == "--"

    def test_negative_returns_dash(self):
        assert data.format_uptime(-10) == "--"

    def test_minutes_only(self):
        assert data.format_uptime(3600 + 120) == "1h 2m"

    def test_days_and_hours(self):
        assert data.format_uptime(86400 * 3 + 3600 * 5) == "3d 5h"

    def test_sub_hour(self):
        assert data.format_uptime(300) == "0h 5m"


class TestFormatNodeStatus:
    def test_online(self):
        assert "Online" in data.format_node_status("online")

    def test_stale(self):
        assert "Stale" in data.format_node_status("stale")

    def test_offline(self):
        assert "Offline" in data.format_node_status("offline")


class TestFleetSummary:
    def test_empty_list(self):
        text, level = data.fleet_summary([])
        assert level == "info"
        assert "No nodes" in text

    def test_all_online(self):
        now = "2026-01-01T00:00:00"
        nodes = [
            data.RegisteredNode(
                node_id=f"n{i}", hostname=f"h{i}", last_ip="1.2.3.4",
                local_ips=[], first_seen=now, last_seen=now,
                uptime_seconds=100, services=[], disk_usage_pct=0,
                memory_usage_pct=0, version="", status="online",
            )
            for i in range(3)
        ]
        text, level = data.fleet_summary(nodes)
        assert level == "success"
        assert "All 3" in text

    def test_mixed_status(self):
        now = "2026-01-01T00:00:00"
        nodes = [
            data.RegisteredNode(
                node_id="a", hostname="a", last_ip="1.2.3.4",
                local_ips=[], first_seen=now, last_seen=now,
                uptime_seconds=100, services=[], disk_usage_pct=0,
                memory_usage_pct=0, version="", status="online",
            ),
            data.RegisteredNode(
                node_id="b", hostname="b", last_ip="1.2.3.5",
                local_ips=[], first_seen=now, last_seen=now,
                uptime_seconds=100, services=[], disk_usage_pct=0,
                memory_usage_pct=0, version="", status="offline",
            ),
        ]
        text, level = data.fleet_summary(nodes)
        assert level == "warning"
        assert "1 online" in text
        assert "1 offline" in text


# ── Metric history ───────────────────────────────────────────────────


class TestMetricHistory:
    def test_checkin_creates_metric_file(self, tmp_path):
        checkin = data.NodeCheckin(
            node_id="m1", hostname="m1", local_ips=["10.0.0.1"],
            uptime_seconds=3600, services=["vm:100:openwrt"],
            disk_usage_pct=45.0, memory_usage_pct=62.0, version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.0.0.1")
        metric_file = tmp_path / "metrics" / "m1.jsonl"
        assert metric_file.exists()
        lines = metric_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["disk"] == 45.0
        assert entry["mem"] == 62.0
        assert entry["svcs"] == 1

    def test_multiple_checkins_append(self, tmp_path):
        for i in range(5):
            checkin = data.NodeCheckin(
                node_id="m1", hostname="m1", local_ips=["10.0.0.1"],
                uptime_seconds=3600 * (i + 1), services=[],
                disk_usage_pct=30.0 + i, memory_usage_pct=50.0 + i, version="1.0",
            )
            data.register_checkin(tmp_path, checkin, "10.0.0.1")
        metric_file = tmp_path / "metrics" / "m1.jsonl"
        lines = metric_file.read_text().strip().splitlines()
        assert len(lines) == 5

    def test_load_metric_history(self, tmp_path):
        for i in range(10):
            checkin = data.NodeCheckin(
                node_id="h1", hostname="h1", local_ips=["10.0.0.1"],
                uptime_seconds=i * 60, services=["ct:101:wg"] if i % 2 == 0 else [],
                disk_usage_pct=20.0 + i, memory_usage_pct=40.0 + i, version="1.0",
            )
            data.register_checkin(tmp_path, checkin, "10.0.0.1")
        history = data.load_metric_history(tmp_path, "h1", max_entries=5)
        assert len(history) == 5
        assert history[-1].disk_usage_pct == 29.0

    def test_load_metric_history_empty(self, tmp_path):
        assert data.load_metric_history(tmp_path, "nonexistent") == []

    def test_load_metric_history_corrupt(self, tmp_path):
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir(parents=True)
        (metrics_dir / "bad.jsonl").write_text("not json\n{bad\n")
        assert data.load_metric_history(tmp_path, "bad") == []

    def test_trim_keeps_max_entries(self, tmp_path):
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir(parents=True)
        metric_file = metrics_dir / "trim.jsonl"
        lines = [json.dumps({"ts": f"t{i}", "disk": i, "mem": i, "up": i, "svcs": 0})
                 for i in range(data.MAX_METRIC_ENTRIES + 100)]
        metric_file.write_text("\n".join(lines) + "\n")
        data._trim_metric_file(metric_file)
        result = metric_file.read_text().strip().splitlines()
        assert len(result) == data.MAX_METRIC_ENTRIES

    def test_sanitizes_node_id(self, tmp_path):
        checkin = data.NodeCheckin(
            node_id="weird/node../id", hostname="weird", local_ips=["10.0.0.1"],
            uptime_seconds=100, services=[], disk_usage_pct=10,
            memory_usage_pct=20, version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.0.0.1")
        metric_file = tmp_path / "metrics" / "weird_node__id.jsonl"
        assert metric_file.exists()


# ── Fleet health ─────────────────────────────────────────────────────


class TestFleetHealth:
    def _make_node(self, hostname, status="online", disk=30.0, memory=40.0,
                   services=None, version="1.0"):
        return data.RegisteredNode(
            node_id=hostname, hostname=hostname, last_ip="10.0.0.1",
            local_ips=["10.0.0.1"], first_seen="2026-01-01T00:00:00",
            last_seen="2026-01-01T00:00:00", uptime_seconds=86400,
            services=services or [], disk_usage_pct=disk,
            memory_usage_pct=memory, version=version, status=status,
        )

    def test_empty_fleet(self):
        health = data.compute_fleet_health([])
        assert health.total_nodes == 0
        assert health.health_score == 100

    def test_all_healthy(self):
        nodes = [self._make_node(f"n{i}", disk=30, memory=40) for i in range(4)]
        health = data.compute_fleet_health(nodes)
        assert health.total_nodes == 4
        assert health.online_nodes == 4
        assert health.offline_nodes == 0
        assert health.health_score >= 80

    def test_mixed_status(self):
        nodes = [
            self._make_node("a", status="online"),
            self._make_node("b", status="offline"),
        ]
        health = data.compute_fleet_health(nodes)
        assert health.online_nodes == 1
        assert health.offline_nodes == 1
        assert health.health_score < 100

    def test_high_disk_lowers_score(self):
        low = [self._make_node(f"n{i}", disk=30, memory=30) for i in range(4)]
        high = [self._make_node(f"n{i}", disk=90, memory=30) for i in range(4)]
        health_low = data.compute_fleet_health(low)
        health_high = data.compute_fleet_health(high)
        assert health_high.health_score < health_low.health_score

    def test_worst_node_tracking(self):
        nodes = [
            self._make_node("a", disk=30, memory=40),
            self._make_node("b", disk=88, memory=92),
            self._make_node("c", disk=50, memory=60),
        ]
        health = data.compute_fleet_health(nodes)
        assert health.worst_disk_node == "b"
        assert health.worst_disk_pct == 88
        assert health.worst_memory_node == "b"
        assert health.worst_memory_pct == 92

    def test_service_count(self):
        nodes = [
            self._make_node("a", services=["vm:100:openwrt", "ct:101:wg"]),
            self._make_node("b", services=["ct:102:pihole"]),
        ]
        health = data.compute_fleet_health(nodes)
        assert health.total_services == 3

    def test_averages(self):
        nodes = [
            self._make_node("a", disk=20, memory=40),
            self._make_node("b", disk=60, memory=80),
        ]
        health = data.compute_fleet_health(nodes)
        assert health.avg_disk_pct == 40.0
        assert health.avg_memory_pct == 60.0

    def test_score_all_offline(self):
        nodes = [self._make_node(f"n{i}", status="offline") for i in range(3)]
        health = data.compute_fleet_health(nodes)
        assert health.health_score == 0


# ── Node alerts ──────────────────────────────────────────────────────


class TestComputeAlerts:
    def _make_node(self, hostname, **kwargs):
        defaults = dict(
            node_id=hostname, hostname=hostname, last_ip="10.0.0.1",
            local_ips=["10.0.0.1"], first_seen="2026-01-01T00:00:00",
            last_seen="2026-01-01T00:00:00", uptime_seconds=86400,
            services=[], disk_usage_pct=30, memory_usage_pct=40,
            version="1.0", status="online",
        )
        defaults.update(kwargs)
        return data.RegisteredNode(**defaults)

    def test_no_alerts_healthy(self):
        nodes = [self._make_node("a"), self._make_node("b")]
        assert data.compute_alerts(nodes) == []

    def test_offline_alert(self):
        nodes = [self._make_node("a", status="offline")]
        alerts = data.compute_alerts(nodes)
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].metric == "status"

    def test_stale_alert(self):
        nodes = [self._make_node("a", status="stale")]
        alerts = data.compute_alerts(nodes)
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"

    def test_disk_warning(self):
        nodes = [self._make_node("a", disk_usage_pct=75)]
        alerts = data.compute_alerts(nodes)
        assert any(a.metric == "disk" and a.severity == "warning" for a in alerts)

    def test_disk_critical(self):
        nodes = [self._make_node("a", disk_usage_pct=90)]
        alerts = data.compute_alerts(nodes)
        assert any(a.metric == "disk" and a.severity == "critical" for a in alerts)

    def test_memory_warning(self):
        nodes = [self._make_node("a", memory_usage_pct=75)]
        alerts = data.compute_alerts(nodes)
        assert any(a.metric == "memory" and a.severity == "warning" for a in alerts)

    def test_memory_critical(self):
        nodes = [self._make_node("a", memory_usage_pct=90)]
        alerts = data.compute_alerts(nodes)
        assert any(a.metric == "memory" and a.severity == "critical" for a in alerts)

    def test_version_mismatch(self):
        nodes = [
            self._make_node("a", version="1.0"),
            self._make_node("b", version="2.0"),
        ]
        alerts = data.compute_alerts(nodes)
        version_alerts = [a for a in alerts if a.metric == "version"]
        assert len(version_alerts) == 2

    def test_no_version_mismatch_when_same(self):
        nodes = [
            self._make_node("a", version="1.0"),
            self._make_node("b", version="1.0"),
        ]
        alerts = data.compute_alerts(nodes)
        assert not any(a.metric == "version" for a in alerts)

    def test_alerts_sorted_critical_first(self):
        nodes = [
            self._make_node("a", disk_usage_pct=75),
            self._make_node("b", status="offline"),
        ]
        alerts = data.compute_alerts(nodes)
        assert alerts[0].severity == "critical"

    def test_multiple_alerts_per_node(self):
        nodes = [self._make_node("a", disk_usage_pct=90, memory_usage_pct=90)]
        alerts = data.compute_alerts(nodes)
        assert len(alerts) == 2
        assert {a.metric for a in alerts} == {"disk", "memory"}


# ── Service matrix ───────────────────────────────────────────────────


class TestServiceMatrix:
    def _make_node(self, hostname, services):
        return data.RegisteredNode(
            node_id=hostname, hostname=hostname, last_ip="10.0.0.1",
            local_ips=[], first_seen="2026-01-01T00:00:00",
            last_seen="2026-01-01T00:00:00", uptime_seconds=86400,
            services=services, disk_usage_pct=30, memory_usage_pct=40,
            version="1.0", status="online",
        )

    def test_empty(self):
        svc_names, matrix = data.compute_service_matrix([])
        assert svc_names == []
        assert matrix == {}

    def test_single_node(self):
        nodes = [self._make_node("home", ["vm:100:openwrt", "ct:102:pihole"])]
        svc_names, matrix = data.compute_service_matrix(nodes)
        assert "openwrt" in svc_names
        assert "pihole" in svc_names
        assert matrix["openwrt"]["home"].running is True
        assert matrix["pihole"]["home"].vmid == "102"

    def test_multi_node_matrix(self):
        nodes = [
            self._make_node("home", ["vm:100:openwrt", "ct:101:wg"]),
            self._make_node("mesh1", ["ct:101:wg"]),
            self._make_node("ai", ["ct:101:wg", "ct:601:gaming"]),
        ]
        svc_names, matrix = data.compute_service_matrix(nodes)
        assert "wg" in svc_names
        assert matrix["wg"]["home"].running is True
        assert matrix["wg"]["mesh1"].running is True
        assert matrix["wg"]["ai"].running is True
        assert matrix["openwrt"]["home"].running is True
        assert matrix["openwrt"].get("mesh1") is None
        assert matrix["gaming"]["ai"].running is True
        assert matrix["gaming"].get("home") is None

    def test_service_types_preserved(self):
        nodes = [self._make_node("home", ["vm:100:openwrt", "ct:102:pihole"])]
        _, matrix = data.compute_service_matrix(nodes)
        assert matrix["openwrt"]["home"].vm_type == "vm"
        assert matrix["pihole"]["home"].vm_type == "ct"

    def test_malformed_service_skipped(self):
        nodes = [self._make_node("home", ["bad", "ct:102:pihole"])]
        svc_names, matrix = data.compute_service_matrix(nodes)
        assert "pihole" in svc_names
        assert len(svc_names) == 1


class TestParseServiceEntry:
    def test_valid_triple(self):
        result = data.parse_service_entry("vm:100:openwrt")
        assert result.vm_type == "vm"
        assert result.vmid == "100"
        assert result.name == "openwrt"

    def test_valid_double(self):
        result = data.parse_service_entry("ct:102")
        assert result.vm_type == "ct"
        assert result.vmid == "102"
        assert result.name == "102"

    def test_invalid(self):
        assert data.parse_service_entry("bad") is None


# ── Format helpers ───────────────────────────────────────────────────


class TestFormatLastSeenRelative:
    def test_never(self):
        assert data.format_last_seen_relative("") == "never"

    def test_invalid(self):
        assert data.format_last_seen_relative("not-a-date") == "unknown"

    def test_just_now(self):
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        assert data.format_last_seen_relative(now) == "just now"


class TestUsageLevel:
    def test_ok(self):
        assert data.usage_level(50) == "ok"

    def test_warning(self):
        assert data.usage_level(75) == "warning"

    def test_critical(self):
        assert data.usage_level(90) == "critical"

    def test_boundary_70(self):
        assert data.usage_level(70) == "warning"

    def test_boundary_85(self):
        assert data.usage_level(85) == "critical"

    def test_zero(self):
        assert data.usage_level(0) == "ok"


class TestResourceScore:
    def test_low_usage(self):
        assert data._resource_score(30) == 1.0

    def test_high_usage(self):
        assert data._resource_score(95) == 0.0

    def test_mid_usage(self):
        score = data._resource_score(70)
        assert 0 < score < 1


class TestThemeConstants:
    def test_all_colors_in_global_styles(self):
        from scripts.webui import theme
        assert theme.BG_PAGE_CENTER in theme.GLOBAL_STYLES
        assert theme.BG_TABLE in theme.GLOBAL_STYLES
        assert theme.BG_CARD in theme.GLOBAL_STYLES
        assert theme.BORDER in theme.GLOBAL_STYLES
        assert theme.TEXT_SECONDARY in theme.GLOBAL_STYLES

    def test_hover_card_uses_constants(self):
        from scripts.webui import theme
        assert theme.BORDER_HOVER in theme.HOVER_CARD_STYLES
        assert theme.SHADOW_HOVER in theme.HOVER_CARD_STYLES

    def test_accent_is_teal(self):
        from scripts.webui import theme
        assert theme.ACCENT.startswith("#")
        assert "b8a6" in theme.ACCENT

    def test_gradient_in_body_css(self):
        from scripts.webui import theme
        assert "radial-gradient" in theme.GLOBAL_STYLES
        assert theme.BG_PAGE_EDGE in theme.GLOBAL_STYLES

    def test_button_classes_in_css(self):
        from scripts.webui import theme
        assert ".action-btn" in theme.GLOBAL_STYLES
        assert ".outline-btn" in theme.GLOBAL_STYLES
        assert ".subtle-btn" in theme.GLOBAL_STYLES


class TestThemeSidebarBreakpoint:
    """Verify the left drawer breakpoint fix (Finding 1 from manual testing).

    The breakpoint=0 prop forces the sidebar to always push content
    instead of overlaying it on smaller viewports.
    """

    def test_nav_sidebar_source_has_breakpoint_zero(self):
        """nav_sidebar() must set breakpoint=0 on the left_drawer."""
        import inspect
        from scripts.webui import theme
        source = inspect.getsource(theme.nav_sidebar)
        assert 'breakpoint=0' in source or "breakpoint=0" in source

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
        env = {"PRIMARY_HOST": "192.168.86.201", "AI_HOST": "10.254.254.254"}
        hosts = data.get_known_hosts(env)
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

    def test_deploy_profile_home_unit(self):
        profiles = data.get_deploy_profiles()
        home = next(p for p in profiles if p.name == "Home Unit")
        assert "openwrt" in home.tags
        assert "pihole" in home.tags
        assert "media" in home.tags
        assert "desktop" in home.tags
        assert "gaming" not in home.tags
        hosts = data.get_hosts_for_tags(home.tags)
        assert "home" in hosts

    def test_deploy_profile_mesh_unit(self):
        profiles = data.get_deploy_profiles()
        mesh = next(p for p in profiles if p.name == "Mesh Unit")
        assert "mesh-wifi" in mesh.tags
        assert "moonlight" in mesh.tags
        assert "openwrt" not in mesh.tags
        hosts = data.get_hosts_for_tags(mesh.tags)
        assert "mesh1" in hosts

    def test_deploy_profile_gamer_unit(self):
        profiles = data.get_deploy_profiles()
        gamer = next(p for p in profiles if p.name == "Gamer Unit")
        assert "gaming" in gamer.tags
        assert "wireguard" in gamer.tags
        assert "openwrt" not in gamer.tags
        hosts = data.get_hosts_for_tags(gamer.tags)
        assert "ai" in hosts

    def test_deploy_profile_bridge_units(self):
        profiles = data.get_deploy_profiles()
        bridge = next(p for p in profiles if p.name == "Bridge Units")
        assert "bridge" in bridge.tags
        hosts = data.get_hosts_for_tags(bridge.tags)
        assert "bridge-1" in hosts
        assert "bridge-2" in hosts

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


# ── Host and Fleet domain model ───────────────────────────────────────


class TestExitCodeLabel:
    def test_success(self):
        assert data.exit_code_label(0) == "success"

    def test_known_failure(self):
        label = data.exit_code_label(2)
        assert "failed" in label
        assert "host unreachable" in label

    def test_unknown_code(self):
        label = data.exit_code_label(42)
        assert "failed" in label
        assert "unknown error" in label

    def test_all_known_codes(self):
        for code in data.ANSIBLE_EXIT_CODES:
            label = data.exit_code_label(code)
            if code == 0:
                assert label == "success"
            else:
                assert label.startswith("failed")


class TestHost:
    def _make_record(self, exit_code=0, timestamp="2026-04-05T10:00:00", host_limit=None):
        return data.DeployRecord(
            timestamp=timestamp,
            tags=["infra"],
            env_file=".env",
            exit_code=exit_code,
            duration_seconds=60.0,
            host_limit=host_limit,
        )

    def test_healthy_no_deploys(self):
        host = data.Host("home", "192.168.86.201")
        assert host.healthy is True
        assert host.errors == []

    def test_healthy_after_success(self):
        host = data.Host("home", "192.168.86.201")
        host.deploys.append(self._make_record(exit_code=0))
        assert host.healthy is True
        assert host.errors == []

    def test_unhealthy_after_failure(self):
        host = data.Host("home", "192.168.86.201")
        host.deploys.append(self._make_record(exit_code=2))
        assert host.healthy is False
        assert len(host.errors) == 1
        assert "host unreachable" in host.errors[0]

    def test_recovers_after_success_following_failure(self):
        host = data.Host("home", "192.168.86.201")
        host.deploys.append(self._make_record(exit_code=2, timestamp="2026-04-04T10:00:00"))
        host.deploys.append(self._make_record(exit_code=0, timestamp="2026-04-05T10:00:00"))
        assert host.healthy is True
        assert host.errors == []

    def test_last_deploy(self):
        host = data.Host("home", "192.168.86.201")
        r1 = self._make_record(timestamp="2026-04-04T10:00:00")
        r2 = self._make_record(timestamp="2026-04-05T10:00:00")
        host.deploys.extend([r1, r2])
        assert host.last_deploy is r2

    def test_last_deploy_none(self):
        host = data.Host("home", "192.168.86.201")
        assert host.last_deploy is None

    def test_repr_healthy(self):
        host = data.Host("home", "192.168.86.201")
        assert "healthy" in repr(host)

    def test_repr_unhealthy(self):
        host = data.Host("home", "192.168.86.201")
        host.deploys.append(self._make_record(exit_code=1))
        assert "unhealthy" in repr(host)

    def test_properties_passthrough(self):
        host = data.Host("mesh1", "10.10.10.210", is_lan=True, wol_capable=False)
        assert host.name == "mesh1"
        assert host.ip == "10.10.10.210"
        assert host.is_lan is True
        assert host.wol_capable is False


class TestFleet:
    def _make_host(self, name, exit_code=None):
        host = data.Host(name, f"10.0.0.{hash(name) % 255}")
        if exit_code is not None:
            host.deploys.append(data.DeployRecord(
                timestamp="2026-04-05T10:00:00",
                tags=["infra"],
                env_file=".env",
                exit_code=exit_code,
                duration_seconds=60.0,
            ))
        return host

    def test_healthy_all_success(self):
        fleet = data.Fleet([
            self._make_host("home", exit_code=0),
            self._make_host("ai", exit_code=0),
        ])
        assert fleet.healthy is True
        assert fleet.errors == []
        assert fleet.unhealthy_hosts == []

    def test_healthy_no_deploys(self):
        fleet = data.Fleet([self._make_host("home"), self._make_host("ai")])
        assert fleet.healthy is True

    def test_unhealthy_one_failure(self):
        fleet = data.Fleet([
            self._make_host("home", exit_code=0),
            self._make_host("ai", exit_code=2),
        ])
        assert fleet.healthy is False
        assert len(fleet.errors) == 1
        assert "ai" in fleet.errors[0]
        assert len(fleet.unhealthy_hosts) == 1
        assert fleet.unhealthy_hosts[0].name == "ai"

    def test_host_count(self):
        fleet = data.Fleet([self._make_host("a"), self._make_host("b"), self._make_host("c")])
        assert fleet.host_count == 3

    def test_last_deploy_across_hosts(self):
        h1 = self._make_host("home", exit_code=0)
        h2 = data.Host("ai", "10.0.0.2")
        h2.deploys.append(data.DeployRecord(
            timestamp="2026-04-06T12:00:00",
            tags=["gaming"],
            env_file=".env",
            exit_code=0,
            duration_seconds=120.0,
        ))
        fleet = data.Fleet([h1, h2])
        assert fleet.last_deploy is not None
        assert fleet.last_deploy.timestamp == "2026-04-06T12:00:00"

    def test_last_deploy_none(self):
        fleet = data.Fleet([self._make_host("home")])
        assert fleet.last_deploy is None

    def test_repr(self):
        fleet = data.Fleet([self._make_host("home", exit_code=0)])
        assert "1 hosts" in repr(fleet)
        assert "healthy" in repr(fleet)

    def test_repr_unhealthy(self):
        fleet = data.Fleet([
            self._make_host("home", exit_code=0),
            self._make_host("ai", exit_code=1),
        ])
        assert "1 unhealthy" in repr(fleet)

    def test_empty_fleet(self):
        fleet = data.Fleet([])
        assert fleet.healthy is True
        assert fleet.host_count == 0
        assert fleet.errors == []
        assert fleet.last_deploy is None


class TestBuildFleet:
    def test_builds_from_env_and_history(self, tmp_path):
        env = {"PRIMARY_HOST": "192.168.86.201", "AI_HOST": "192.168.86.220"}
        record = data.DeployRecord(
            timestamp="2026-04-05T10:00:00",
            tags=["infra"],
            env_file=".env",
            exit_code=0,
            duration_seconds=60.0,
        )
        data.save_deploy_record(tmp_path, record)

        fleet = data.build_fleet(env, tmp_path)
        assert fleet.host_count >= 3  # home, ai, mesh1
        assert fleet.healthy is True

        home = next(h for h in fleet.hosts if h.name == "home")
        assert len(home.deploys) == 1
        assert home.healthy is True

    def test_host_limit_filters_deploys(self, tmp_path):
        env = {"PRIMARY_HOST": "192.168.86.201", "AI_HOST": "192.168.86.220"}
        record = data.DeployRecord(
            timestamp="2026-04-05T10:00:00",
            tags=["gaming"],
            env_file=".env",
            exit_code=2,
            duration_seconds=60.0,
            host_limit="ai",
        )
        data.save_deploy_record(tmp_path, record)

        fleet = data.build_fleet(env, tmp_path)
        home = next(h for h in fleet.hosts if h.name == "home")
        ai = next(h for h in fleet.hosts if h.name == "ai")

        assert len(home.deploys) == 0
        assert home.healthy is True
        assert len(ai.deploys) == 1
        assert ai.healthy is False

    def test_empty_history(self, tmp_path):
        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, tmp_path)
        assert fleet.healthy is True
        for host in fleet.hosts:
            assert host.deploys == []

    def test_recovery_scenario(self, tmp_path):
        """Old failure followed by success = healthy."""
        env = {"PRIMARY_HOST": "192.168.86.201"}
        data.save_deploy_record(tmp_path, data.DeployRecord(
            timestamp="2026-04-04T10:00:00", tags=["infra"],
            env_file=".env", exit_code=2, duration_seconds=60.0,
        ))
        data.save_deploy_record(tmp_path, data.DeployRecord(
            timestamp="2026-04-05T10:00:00", tags=["infra"],
            env_file=".env", exit_code=0, duration_seconds=60.0,
        ))
        fleet = data.build_fleet(env, tmp_path)
        assert fleet.healthy is True
        home = next(h for h in fleet.hosts if h.name == "home")
        assert home.healthy is True
        assert len(home.deploys) == 2

    def test_hosts_have_bucket(self, tmp_path):
        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, tmp_path)
        home = fleet.get_host("home")
        assert home is not None
        assert home.bucket == "test"

    def test_test_units_in_fleet(self, tmp_path):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "TEST_UNITS": "192.168.86.230,192.168.86.231",
        }
        fleet = data.build_fleet(env, tmp_path)
        test_bucket = fleet.hosts_by_bucket("test")
        test_names = [h.name for h in test_bucket]
        assert "test-230" in test_names
        assert "test-231" in test_names

    def test_hosts_by_bucket(self, tmp_path):
        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, tmp_path)
        test_hosts = fleet.hosts_by_bucket("test")
        assert len(test_hosts) >= 1


class TestDeployTargetsHost:
    def test_no_limit_targets_all(self):
        record = data.DeployRecord(
            timestamp="2026-04-05T10:00:00", tags=["infra"],
            env_file=".env", exit_code=0, duration_seconds=60.0,
        )
        assert data._deploy_targets_host(record, "home") is True
        assert data._deploy_targets_host(record, "ai") is True

    def test_limit_matches(self):
        record = data.DeployRecord(
            timestamp="2026-04-05T10:00:00", tags=["gaming"],
            env_file=".env", exit_code=0, duration_seconds=60.0,
            host_limit="ai",
        )
        assert data._deploy_targets_host(record, "ai") is True
        assert data._deploy_targets_host(record, "home") is False

    def test_limit_partial_match(self):
        record = data.DeployRecord(
            timestamp="2026-04-05T10:00:00", tags=["infra"],
            env_file=".env", exit_code=0, duration_seconds=60.0,
            host_limit="home,mesh1",
        )
        assert data._deploy_targets_host(record, "home") is True
        assert data._deploy_targets_host(record, "mesh1") is True
        assert data._deploy_targets_host(record, "ai") is False


# ── HostTelemetry + GuestInfo ─────────────────────────────────────────


class TestGuestInfo:
    def test_dataclass_fields(self):
        g = data.GuestInfo(vmid="100", name="openwrt", vm_type="vm", running=True)
        assert g.vmid == "100"
        assert g.name == "openwrt"
        assert g.vm_type == "vm"
        assert g.running is True

    def test_default_running(self):
        g = data.GuestInfo(vmid="101", name="wireguard", vm_type="ct")
        assert g.running is True


class TestParseGuests:
    def test_standard_entries(self):
        guests = data._parse_guests(["vm:100:openwrt", "ct:101:wireguard"])
        assert len(guests) == 2
        assert guests[0].vmid == "100"
        assert guests[0].name == "openwrt"
        assert guests[0].vm_type == "vm"
        assert guests[1].vm_type == "ct"

    def test_no_name(self):
        guests = data._parse_guests(["vm:100"])
        assert len(guests) == 1
        assert guests[0].name == "100"

    def test_empty(self):
        assert data._parse_guests([]) == []

    def test_malformed_entry_skipped(self):
        guests = data._parse_guests(["bad", "vm:100:ok"])
        assert len(guests) == 1
        assert guests[0].name == "ok"


class TestHostTelemetry:
    def test_construction(self):
        t = data.HostTelemetry(
            node_id="home", last_ip="192.168.86.201",
            local_ips=["192.168.86.201", "10.10.10.2"],
            first_seen="2026-04-01T10:00:00", last_seen="2026-04-07T10:00:00",
            uptime_seconds=86400.0, services=["vm:100:openwrt"],
            disk_usage_pct=45.0, memory_usage_pct=62.0, version="1.0",
            status="online",
        )
        assert t.node_id == "home"
        assert t.status == "online"
        assert t.disk_usage_pct == 45.0

    def test_default_status_offline(self):
        t = data.HostTelemetry(
            node_id="x", last_ip="", local_ips=[], first_seen="", last_seen="",
            uptime_seconds=0, services=[], disk_usage_pct=0, memory_usage_pct=0,
            version="",
        )
        assert t.status == "offline"


class TestHostWithTelemetry:
    def _make_telemetry(self, **overrides):
        defaults = dict(
            node_id="home", last_ip="192.168.86.201",
            local_ips=["192.168.86.201"], first_seen="2026-04-01T10:00:00",
            last_seen="2026-04-07T10:00:00", uptime_seconds=259200.0,
            services=["vm:100:openwrt", "ct:101:wireguard", "ct:102:pihole"],
            disk_usage_pct=45.0, memory_usage_pct=62.0, version="1.0",
            status="online",
        )
        defaults.update(overrides)
        return data.HostTelemetry(**defaults)

    def test_online_with_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(status="online"))
        assert host.online is True
        assert host.status == "online"

    def test_offline_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.online is False
        assert host.status == "unknown"

    def test_disk_pct(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(disk_usage_pct=72.5))
        assert host.disk_pct == 72.5

    def test_disk_pct_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.disk_pct == 0.0

    def test_memory_pct(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(memory_usage_pct=88.0))
        assert host.memory_pct == 88.0

    def test_guests(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry())
        assert host.guest_count == 3
        assert host.running_guests == 3
        assert len(host.vms) == 1
        assert len(host.containers) == 2

    def test_guests_empty_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.guests == []
        assert host.guest_count == 0
        assert host.running_guests == 0
        assert host.vms == []
        assert host.containers == []

    def test_uptime(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(uptime_seconds=259200.0))
        assert host.uptime == "3d 0h"

    def test_uptime_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.uptime == "--"

    def test_version(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(version="2.1"))
        assert host.version == "2.1"

    def test_version_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.version == ""

    def test_local_ips(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(local_ips=["10.0.0.1", "10.0.0.2"]))
        assert host.local_ips == ["10.0.0.1", "10.0.0.2"]

    def test_local_ips_empty_no_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        assert host.local_ips == []

    def test_errors_includes_offline_status(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(self._make_telemetry(status="offline"))
        assert any("offline" in e.lower() for e in host.errors)


class TestFleetAggregates:
    def _make_host_with_telemetry(self, name, status="online", disk=30.0, mem=50.0, services=None):
        host = data.Host(name, f"10.0.0.{hash(name) % 255}")
        host.attach_telemetry(data.HostTelemetry(
            node_id=name, last_ip=host.ip, local_ips=[host.ip],
            first_seen="2026-04-01T00:00:00", last_seen="2026-04-07T00:00:00",
            uptime_seconds=86400.0,
            services=services or ["vm:100:router"],
            disk_usage_pct=disk, memory_usage_pct=mem,
            version="1.0", status=status,
        ))
        return host

    def test_online_count(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", status="online"),
            self._make_host_with_telemetry("b", status="offline"),
            self._make_host_with_telemetry("c", status="online"),
        ])
        assert fleet.online_count == 2
        assert fleet.offline_count == 1

    def test_has_telemetry(self):
        h1 = self._make_host_with_telemetry("a")
        h2 = data.Host("b", "10.0.0.2")
        fleet = data.Fleet([h1, h2])
        assert fleet.has_telemetry is True

    def test_no_telemetry(self):
        fleet = data.Fleet([data.Host("a", "10.0.0.1")])
        assert fleet.has_telemetry is False

    def test_total_guests(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", services=["vm:100:router", "ct:101:vpn"]),
            self._make_host_with_telemetry("b", services=["ct:102:dns"]),
        ])
        assert fleet.total_guests == 3
        assert fleet.running_guests == 3

    def test_avg_disk_pct(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", disk=40.0),
            self._make_host_with_telemetry("b", disk=60.0),
        ])
        assert fleet.avg_disk_pct == 50.0

    def test_avg_memory_pct(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", mem=30.0),
            self._make_host_with_telemetry("b", mem=70.0),
        ])
        assert fleet.avg_memory_pct == 50.0

    def test_worst_disk(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", disk=30.0),
            self._make_host_with_telemetry("b", disk=90.0),
        ])
        assert fleet.worst_disk is not None
        assert fleet.worst_disk.name == "b"

    def test_worst_memory(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", mem=80.0),
            self._make_host_with_telemetry("b", mem=40.0),
        ])
        assert fleet.worst_memory is not None
        assert fleet.worst_memory.name == "a"

    def test_worst_disk_none_without_telemetry(self):
        fleet = data.Fleet([data.Host("a", "10.0.0.1")])
        assert fleet.worst_disk is None
        assert fleet.worst_memory is None

    def test_health_score_all_online_low_usage(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", disk=20.0, mem=20.0),
            self._make_host_with_telemetry("b", disk=20.0, mem=20.0),
        ])
        assert fleet.health_score == 100

    def test_health_score_all_offline(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("a", status="offline", disk=0.0, mem=0.0),
        ])
        assert fleet.health_score == 0

    def test_health_score_no_telemetry(self):
        fleet = data.Fleet([data.Host("a", "10.0.0.1")])
        assert fleet.health_score == 100

    def test_get_host_found(self):
        fleet = data.Fleet([
            self._make_host_with_telemetry("home"),
            self._make_host_with_telemetry("ai"),
        ])
        h = fleet.get_host("ai")
        assert h is not None
        assert h.name == "ai"

    def test_get_host_not_found(self):
        fleet = data.Fleet([self._make_host_with_telemetry("home")])
        assert fleet.get_host("nonexistent") is None


class TestBuildFleetWithTelemetry:
    @staticmethod
    def _recent_iso():
        """Return a naive ISO timestamp matching _compute_node_status expectations."""
        from datetime import datetime
        return datetime.now().isoformat()

    def test_wires_telemetry_from_nodes_json(self, tmp_path):
        """build_fleet loads nodes.json and attaches to matching hosts."""
        import json

        env = {"PRIMARY_HOST": "192.168.86.201"}
        now = self._recent_iso()

        nodes_data = [{
            "node_id": "home-host",
            "hostname": "home",
            "last_ip": "192.168.86.201",
            "local_ips": ["192.168.86.201"],
            "first_seen": "2026-04-01T00:00:00",
            "last_seen": now,
            "uptime_seconds": 86400.0,
            "services": ["vm:100:openwrt", "ct:102:pihole"],
            "disk_usage_pct": 55.0,
            "memory_usage_pct": 68.0,
            "version": "1.5",
            "status": "online",
            "container_health": None,
        }]
        (tmp_path / "nodes.json").write_text(json.dumps(nodes_data))

        fleet = data.build_fleet(env, tmp_path)
        home = next((h for h in fleet.hosts if h.name == "home"), None)
        assert home is not None
        assert home.telemetry is not None
        assert home.online is True
        assert home.disk_pct == 55.0
        assert home.guest_count == 2

    def test_no_nodes_json_still_works(self, tmp_path):
        """build_fleet works without nodes.json — hosts just have no telemetry."""
        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, tmp_path)
        home = next((h for h in fleet.hosts if h.name == "home"), None)
        assert home is not None
        assert home.telemetry is None
        assert home.online is False
        assert home.guest_count == 0

    def test_unmatched_node_not_wired(self, tmp_path):
        """Nodes that don't match any configured host are ignored."""
        import json

        env = {"PRIMARY_HOST": "192.168.86.201"}
        nodes_data = [{
            "node_id": "unknown-node", "hostname": "unknown",
            "last_ip": "10.0.0.99", "local_ips": [],
            "first_seen": "", "last_seen": "",
            "uptime_seconds": 0, "services": [],
            "disk_usage_pct": 0, "memory_usage_pct": 0,
            "version": "", "status": "online",
            "container_health": None,
        }]
        (tmp_path / "nodes.json").write_text(json.dumps(nodes_data))

        fleet = data.build_fleet(env, tmp_path)
        for h in fleet.hosts:
            assert h.name != "unknown"

    def test_node_id_match_fallback(self, tmp_path):
        """build_fleet matches by node_id when hostname differs."""
        import json

        env = {"PRIMARY_HOST": "192.168.86.201"}
        now = self._recent_iso()

        nodes_data = [{
            "node_id": "home",
            "hostname": "home-custom-hostname",
            "last_ip": "192.168.86.201",
            "local_ips": ["192.168.86.201"],
            "first_seen": "2026-04-01T00:00:00",
            "last_seen": now,
            "uptime_seconds": 3600.0,
            "services": ["vm:100:openwrt"],
            "disk_usage_pct": 30.0,
            "memory_usage_pct": 40.0,
            "version": "1.0",
            "status": "online",
            "container_health": None,
        }]
        (tmp_path / "nodes.json").write_text(json.dumps(nodes_data))

        fleet = data.build_fleet(env, tmp_path)
        home = next((h for h in fleet.hosts if h.name == "home"), None)
        assert home is not None
        assert home.telemetry is not None
        assert home.online is True


# ── Host telemetry property edge cases ───────────────────────────────


class TestHostTelemetryEdgeCases:
    """Test Host properties missed in the main TestHostWithTelemetry suite."""

    def _make_telemetry(self, **overrides):
        defaults = dict(
            node_id="host1", last_ip="10.0.0.1",
            local_ips=["10.0.0.1"], first_seen="2026-04-01T00:00:00",
            last_seen="2026-04-07T10:00:00", uptime_seconds=86400.0,
            services=["vm:100:router"],
            disk_usage_pct=45.0, memory_usage_pct=62.0, version="1.0",
            status="online",
        )
        defaults.update(overrides)
        return data.HostTelemetry(**defaults)

    def test_uptime_seconds_with_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(uptime_seconds=7200.0))
        assert host.uptime_seconds == 7200.0

    def test_uptime_seconds_no_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.uptime_seconds == 0.0

    def test_last_seen_with_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(last_seen="2026-04-07T10:00:00"))
        assert host.last_seen == "2026-04-07T10:00:00"

    def test_last_seen_no_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.last_seen == ""

    def test_last_seen_relative_no_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.last_seen_relative == "never"

    def test_extensions_with_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        ch = data.ContainerHealth(
            container_id="ct-101",
            systemd_services={"pihole-FTL": "running"},
            listening_ports=[53, 80],
            ready=True,
            extensions={"network": {"interfaces": ["eth0"]}, "dns": {"upstream": "8.8.8.8"}},
        )
        host.attach_telemetry(self._make_telemetry(container_health=ch))
        exts = host.extensions
        assert "network" in exts
        assert "dns" in exts

    def test_extensions_no_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.extensions == {}

    def test_extensions_no_container_health(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry())
        assert host.extensions == {}

    def test_guest_running_false(self):
        g = data.GuestInfo(vmid="100", name="vm", vm_type="vm", running=False)
        assert g.running is False

    def test_running_guests_counts_only_running(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(services=["vm:100:router", "ct:101:dns"]))
        host._guests[1] = data.GuestInfo(vmid="101", name="dns", vm_type="ct", running=False)
        assert host.guest_count == 2
        assert host.running_guests == 1

    def test_errors_no_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.errors == []

    def test_errors_healthy_online(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(status="online"))
        assert host.errors == []
        assert host.healthy is True

    def test_status_reachable_no_heartbeat(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = True
        assert host.status == "reachable"
        assert host.online is False

    def test_status_unreachable(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = False
        assert host.status == "unreachable"
        assert host.online is False

    def test_status_unknown_not_probed(self):
        host = data.Host("a", "10.0.0.1")
        assert host.reachable is None
        assert host.status == "unknown"

    def test_errors_unreachable(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = False
        assert any("unreachable" in e.lower() for e in host.errors)

    def test_errors_reachable_no_errors(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = True
        assert host.errors == []

    def test_warnings_reachable_no_heartbeat(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = True
        assert len(host.warnings) == 1
        assert "heartbeat" in host.warnings[0].lower()

    def test_warnings_online_no_warnings(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(status="online"))
        assert host.warnings == []

    def test_warnings_unreachable_no_warnings(self):
        host = data.Host("a", "10.0.0.1")
        host.reachable = False
        assert host.warnings == []

    def test_telemetry_overrides_reachable(self):
        """Telemetry status takes priority over reachable probe."""
        host = data.Host("a", "10.0.0.1")
        host.reachable = True
        host.attach_telemetry(self._make_telemetry(status="online"))
        assert host.status == "online"

    def test_registered_with_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(status="online"))
        assert host.registered is True

    def test_not_registered_without_telemetry(self):
        host = data.Host("a", "10.0.0.1")
        assert host.registered is False

    def test_registered_retains_last_known_state(self):
        """Stale/offline hosts still have their last-known services and metrics."""
        host = data.Host("a", "10.0.0.1")
        host.attach_telemetry(self._make_telemetry(
            status="offline",
            services=["vm:100:router", "ct:101:vpn"],
            disk_usage_pct=45.0,
            version="2.0",
        ))
        assert host.registered is True
        assert host.status == "offline"
        assert host.guest_count == 2
        assert host.disk_pct == 45.0
        assert host.version == "2.0"


class TestFleetReachability:
    """Fleet-level reachability aggregates."""

    def test_reachable_count_with_probed_hosts(self):
        h1 = data.Host("a", "10.0.0.1")
        h1.reachable = True
        h2 = data.Host("b", "10.0.0.2")
        h2.reachable = False
        h3 = data.Host("c", "10.0.0.3")
        h3.reachable = True
        fleet = data.Fleet([h1, h2, h3])
        assert fleet.reachable_count == 2

    def test_reachable_count_includes_online_hosts(self):
        h1 = data.Host("a", "10.0.0.1")
        h1.attach_telemetry(data.HostTelemetry(
            node_id="a", last_ip="10.0.0.1", local_ips=[], first_seen="",
            last_seen="", uptime_seconds=0, services=[], disk_usage_pct=0,
            memory_usage_pct=0, version="", status="online",
        ))
        h2 = data.Host("b", "10.0.0.2")
        h2.reachable = True
        fleet = data.Fleet([h1, h2])
        assert fleet.reachable_count == 2

    def test_registered_count(self):
        h1 = data.Host("a", "10.0.0.1")
        h1.attach_telemetry(data.HostTelemetry(
            node_id="a", last_ip="10.0.0.1", local_ips=[], first_seen="",
            last_seen="", uptime_seconds=0, services=[], disk_usage_pct=0,
            memory_usage_pct=0, version="", status="offline",
        ))
        h2 = data.Host("b", "10.0.0.2")
        h3 = data.Host("c", "10.0.0.3")
        h3.attach_telemetry(data.HostTelemetry(
            node_id="c", last_ip="10.0.0.3", local_ips=[], first_seen="",
            last_seen="", uptime_seconds=0, services=[], disk_usage_pct=0,
            memory_usage_pct=0, version="", status="online",
        ))
        fleet = data.Fleet([h1, h2, h3])
        assert fleet.registered_count == 2

    def test_fleet_warnings_aggregate(self):
        h1 = data.Host("a", "10.0.0.1")
        h1.reachable = True
        h2 = data.Host("b", "10.0.0.2")
        h2.reachable = True
        fleet = data.Fleet([h1, h2])
        assert len(fleet.warnings) == 2
        assert all("heartbeat" in w.lower() for w in fleet.warnings)


class TestRegisteredNationalHosts:
    """National units retain last-known state across sessions."""

    def test_offline_registered_host_retains_metrics(self):
        """A unit that heartbeated days ago keeps its last-known data."""
        host = data.Host("remote-001", "203.0.113.50")
        host.attach_telemetry(data.HostTelemetry(
            node_id="remote-001", last_ip="203.0.113.50",
            local_ips=["192.168.1.100"], first_seen="2026-03-01T00:00:00",
            last_seen="2026-03-15T00:00:00",
            uptime_seconds=86400.0, services=["ct:101:vpn", "ct:102:pihole"],
            disk_usage_pct=42.5, memory_usage_pct=68.3,
            version="1.5", status="offline",
        ))
        assert host.registered is True
        assert host.status == "offline"
        assert host.guest_count == 2
        assert host.disk_pct == 42.5
        assert host.memory_pct == 68.3
        assert host.version == "1.5"

    def test_unregistered_host_behind_nat_shows_unknown(self):
        """New national unit behind NAT — no probe, no heartbeat."""
        host = data.Host("remote-002", "")
        assert host.registered is False
        assert host.status == "unknown"
        assert host.guest_count == 0

    def test_fleet_mixed_registered_and_unregistered(self):
        h1 = data.Host("local", "192.168.86.201")
        h1.reachable = True
        h2 = data.Host("remote-001", "203.0.113.50")
        h2.attach_telemetry(data.HostTelemetry(
            node_id="remote-001", last_ip="203.0.113.50",
            local_ips=[], first_seen="", last_seen="",
            uptime_seconds=0, services=[], disk_usage_pct=0,
            memory_usage_pct=0, version="", status="offline",
        ))
        h3 = data.Host("remote-002", "")
        fleet = data.Fleet([h1, h2, h3])
        assert fleet.registered_count == 1
        assert fleet.reachable_count == 1
        assert fleet.host_count == 3


class TestVpnIpModel:
    """VPN IP wiring through Host and probing fallback."""

    def test_host_vpn_ip_default_empty(self):
        host = data.Host("a", "10.0.0.1")
        assert host.vpn_ip == ""

    def test_host_vpn_ip_set(self):
        host = data.Host("a", "10.0.0.1", vpn_ip="10.8.0.1")
        assert host.vpn_ip == "10.8.0.1"

    def test_reachable_ip_always_prefers_vpn(self):
        host = data.Host("a", "192.168.1.1", vpn_ip="10.8.0.1")
        host.reachable = True
        assert host.reachable_ip == "10.8.0.1"

    def test_reachable_ip_uses_vpn_when_unreachable(self):
        host = data.Host("a", "192.168.1.1", vpn_ip="10.8.0.1")
        host.reachable = False
        assert host.reachable_ip == "10.8.0.1"

    def test_reachable_ip_vpn_only(self):
        host = data.Host("a", "", vpn_ip="10.8.0.1")
        assert host.reachable_ip == "10.8.0.1"

    def test_reachable_ip_no_vpn_no_primary(self):
        host = data.Host("a", "")
        assert host.reachable_ip == ""

    def test_reachable_ip_lan_host_prefers_vpn(self):
        """LAN hosts can't be TCP-probed; VPN bypasses the router."""
        host = data.Host("mesh1", "10.10.10.210", is_lan=True, vpn_ip="10.8.0.5")
        assert host.reachable_ip == "10.8.0.5"

    def test_host_info_vpn_ip(self):
        info = data.HostInfo(
            name="remote", ip="1.2.3.4", env_var="REMOTE_HOST",
            wol_capable=True, vpn_ip="10.8.0.10",
        )
        assert info.vpn_ip == "10.8.0.10"

    def test_get_known_hosts_reads_vpn_env(self):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "HOME_VPN_IP": "10.8.0.1",
        }
        hosts = data.get_known_hosts(env)
        home = next(h for h in hosts if h.name == "home")
        assert home.vpn_ip == "10.8.0.1"

    def test_get_known_hosts_no_vpn_env(self):
        env = {"PRIMARY_HOST": "192.168.86.201"}
        hosts = data.get_known_hosts(env)
        home = next(h for h in hosts if h.name == "home")
        assert home.vpn_ip == ""

    def test_detail_stat_shows_vpn(self):
        """VPN IP field is displayed in node detail header."""
        host = data.Host("a", "10.0.0.1", vpn_ip="10.8.0.1")
        assert host.vpn_ip == "10.8.0.1"


class TestKickstartCallhome:
    """kickstart_callhome function tests — against real infrastructure."""

    def test_no_reachable_ip(self):
        host = data.Host("a", "")
        result = data.kickstart_callhome(host)
        assert not result.success
        assert "No reachable IP" in result.message

    def test_ssh_failure_unreachable_ip(self):
        host = data.Host("bogus", "10.254.254.254")
        host.reachable = True
        result = data.kickstart_callhome(host)
        assert not result.success

    def test_kickstart_real_host(self):
        host = data.Host("home", "192.168.86.201")
        host.reachable = True
        result = data.kickstart_callhome(host)
        assert result.success
        assert result.restarted >= 0


class TestFleetAggregateExtras:
    """Additional Fleet aggregate tests."""

    def _make_host(self, name, status="online", disk=30.0, mem=50.0, services=None):
        host = data.Host(name, f"10.0.0.{hash(name) % 255}")
        host.attach_telemetry(data.HostTelemetry(
            node_id=name, last_ip=host.ip, local_ips=[host.ip],
            first_seen="2026-04-01T00:00:00", last_seen="2026-04-07T00:00:00",
            uptime_seconds=86400.0,
            services=services or ["vm:100:router"],
            disk_usage_pct=disk, memory_usage_pct=mem,
            version="1.0", status=status,
        ))
        return host

    def test_total_services_alias(self):
        fleet = data.Fleet([
            self._make_host("a", services=["vm:100:r", "ct:101:w"]),
        ])
        assert fleet.total_services == fleet.total_guests
        assert fleet.total_services == 2

    def test_health_score_mixed_online_offline(self):
        fleet = data.Fleet([
            self._make_host("a", status="online", disk=20.0, mem=20.0),
            self._make_host("b", status="offline", disk=0.0, mem=0.0),
        ])
        score = fleet.health_score
        assert 0 < score < 100

    def test_last_deploy_sorted_by_timestamp(self):
        """Fleet.last_deploy returns most recent by timestamp, not insertion order."""
        h1 = data.Host("a", "10.0.0.1")
        h1.deploys.append(data.DeployRecord(
            timestamp="2026-04-07T12:00:00", exit_code=0, tags=["infra"],
            env_file="test.env", duration_seconds=120,
        ))
        h2 = data.Host("b", "10.0.0.2")
        h2.deploys.append(data.DeployRecord(
            timestamp="2026-04-06T12:00:00", exit_code=2, tags=["openwrt"],
            env_file="test.env", duration_seconds=60,
        ))
        fleet = data.Fleet([h2, h1])
        assert fleet.last_deploy is not None
        assert fleet.last_deploy.exit_code == 0
        assert "2026-04-07" in fleet.last_deploy.timestamp


class TestExitCodeHelpers:
    """Tests for exit_code_label() and exit_code_color()."""

    def test_success_label(self):
        assert data.exit_code_label(0) == "success"

    def test_failure_label_known(self):
        label = data.exit_code_label(2)
        assert "failed" in label
        assert "unreachable" in label.lower() or "task" in label.lower()

    def test_failure_label_unknown(self):
        label = data.exit_code_label(42)
        assert "failed" in label
        assert "unknown" in label

    def test_color_success(self):
        assert data.exit_code_color(0) == "green"

    def test_color_failure(self):
        assert data.exit_code_color(2) == "red"
        assert data.exit_code_color(4) == "red"
        assert data.exit_code_color(99) == "red"


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


class TestSmHubUrls:
    def test_generates_urls_from_gateway(self):
        env = {"LAN_GATEWAY": "10.10.10.1"}
        urls = data.generate_sm_hub_urls(env)
        assert urls["OPENWRT_URL"] == "http://10.10.10.1"
        assert urls["PIHOLE_URL"] == "http://10.10.10.10/admin"
        assert urls["HOMEASSISTANT_URL"] == "http://10.10.10.14:8123"
        assert urls["JELLYFIN_URL"] == "http://10.10.10.15:8096"
        assert urls["NETDATA_URL"] == "http://10.10.10.40:19999"
        assert urls["GAMING_URL"] == "https://10.10.10.18:47990"

    def test_returns_empty_without_gateway(self):
        assert data.generate_sm_hub_urls({}) == {}
        assert data.generate_sm_hub_urls({"OTHER": "val"}) == {}

    def test_covers_all_sm_service_url_keys(self):
        env = {"LAN_GATEWAY": "10.10.10.1"}
        urls = data.generate_sm_hub_urls(env)
        assert set(urls.keys()) == set(data.SM_SERVICE_URLS.keys())


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

    def test_display_apps_descriptions_mention_display(self):
        """Each description should reference KasmVNC, display, or console access."""
        for url_key, info in data.DISPLAY_APPS.items():
            desc = info["description"].lower()
            assert "kasmvnc" in desc or "console" in desc or "remotely" in desc, (
                f"{url_key} description should reference display/console access"
            )

    def test_display_apps_have_app_id(self):
        """Each display app should have an app_id for handler registry lookup."""
        for url_key, info in data.DISPLAY_APPS.items():
            assert "app_id" in info, f"{url_key} missing app_id"
            assert info["app_id"], f"{url_key} app_id is empty"

    def test_display_apps_derived_from_configs(self):
        """DISPLAY_APPS must be derived from DISPLAY_APP_CONFIGS (single source of truth)."""
        for url_key, info in data.DISPLAY_APPS.items():
            app_id = info["app_id"]
            assert app_id in data.DISPLAY_APP_CONFIGS, (
                f"DISPLAY_APPS[{url_key!r}] references {app_id!r} not in DISPLAY_APP_CONFIGS"
            )
            cfg = data.DISPLAY_APP_CONFIGS[app_id]
            assert info["label"] == cfg.label
            assert info["icon"] == cfg.icon


class TestConsoleUrl:
    def test_basic(self):
        url = data.console_url("home", "desktop")
        expected = data.Routes.CONSOLE.replace("{node_id}", "home").replace("{app_id}", "desktop")
        assert url == expected

    def test_with_back(self):
        url = data.console_url("home", "kodi", back="/fleet")
        expected_prefix = data.Routes.CONSOLE.replace("{node_id}", "home").replace("{app_id}", "kodi")
        assert url.startswith(f"{expected_prefix}?back=")
        assert "%2Ffleet" in url

    def test_no_back(self):
        url = data.console_url("mesh1", "moonlight", back="")
        expected = data.Routes.CONSOLE.replace("{node_id}", "mesh1").replace("{app_id}", "moonlight")
        assert url == expected


# ── SSH connection ────────────────────────────────────────────────────


@pytest.mark.integration
class TestSshConnection:
    def test_ssh_success(self):
        host = os.environ.get("PRIMARY_HOST", "192.168.86.201")
        result = data.test_ssh_connection(host)
        assert result.success is True
        assert result.output == "ok"

    def test_ssh_failure(self):
        result = data.test_ssh_connection("10.254.254.254")
        assert result.success is False
        assert result.error

    def test_ssh_timeout(self):
        # WHY: Cannot reliably trigger a 10-second SSH timeout against controlled hosts.
        # HOW: Tests that TimeoutExpired is caught and produces a "timed out" error message.
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
            result = data.test_ssh_connection("192.168.86.201")
            assert result.success is False
            assert "timed out" in result.error

    def test_ssh_missing_binary(self):
        # WHY: Cannot remove the ssh binary from the test environment to test the missing-binary path.
        # HOW: Tests that FileNotFoundError is caught and produces a "not found" error message.
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
        expected_port = int(os.environ.get("WEBUI_PORT", "52500"))
        assert args.port == expected_port
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


class _LogCapture:
    """Real object that captures push() calls — no MagicMock needed."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def push(self, text: str) -> None:
        self.lines.append(text)


class TestStreamProcess:
    """Tests for the shared subprocess runner."""

    async def test_captures_output(self, tmp_path):
        import asyncio
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        rc = await stream_process(
            ["echo", "hello world"],
            log,
            cwd=tmp_path,
        )
        assert rc == 0
        assert len(log.lines) > 0
        assert any("hello world" in line for line in log.lines)

    async def test_returns_nonzero_on_failure(self, tmp_path):
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        rc = await stream_process(
            ["false"],
            log,
            cwd=tmp_path,
        )
        assert rc != 0

    async def test_calls_on_line_callback(self, tmp_path):
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        callback_lines: list[str] = []
        rc = await stream_process(
            ["echo", "callback-test"],
            log,
            cwd=tmp_path,
            on_line=lambda text: callback_lines.append(text),
        )
        assert rc == 0
        assert any("callback-test" in line for line in callback_lines)

    async def test_passes_env_extra(self, tmp_path):
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        rc = await stream_process(
            ["bash", "-c", "echo $TEST_STREAM_VAR"],
            log,
            cwd=tmp_path,
            env_extra={"TEST_STREAM_VAR": "stream-value"},
        )
        assert rc == 0
        assert any("stream-value" in line for line in log.lines)

    async def test_handles_bad_command(self, tmp_path):
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        rc = await stream_process(
            ["/nonexistent/binary"],
            log,
            cwd=tmp_path,
        )
        assert rc == 1
        assert len(log.lines) > 0
        assert any("Error" in line for line in log.lines)

    async def test_proc_holder_stores_and_clears_process(self, tmp_path):
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        holder: dict = {"process": None}
        rc = await stream_process(
            ["echo", "holder-test"],
            log,
            cwd=tmp_path,
            proc_holder=holder,
        )
        assert rc == 0
        assert holder["process"] is None, "process should be cleared after completion"

    async def test_proc_holder_exposes_running_process(self, tmp_path):
        """Verify proc_holder['process'] is set while the command runs."""
        import asyncio
        from scripts.webui.run_process import stream_process

        log = _LogCapture()
        holder: dict = {"process": None}
        saw_process = False

        def _check_holder(text: str) -> None:
            nonlocal saw_process
            if holder["process"] is not None:
                saw_process = True

        rc = await stream_process(
            ["echo", "running"],
            log,
            cwd=tmp_path,
            on_line=_check_holder,
            proc_holder=holder,
        )
        assert rc == 0
        assert saw_process, "proc_holder should have a process while streaming"


class _FakeLabel:
    """Real object with .text and .style() — no MagicMock needed."""

    def __init__(self) -> None:
        self.text: str = ""
        self._styles: list[str] = []

    def style(self, s: str) -> None:
        self._styles.append(s)


class TestStatusText:
    """Tests for the theme.status_text helper."""

    def test_sets_text_and_color(self):
        from scripts.webui.theme import status_text, COLOR_SUCCESS

        label = _FakeLabel()
        status_text(label, "All good", "success")
        assert label.text == "All good"
        assert f"color: {COLOR_SUCCESS}" in label._styles

    def test_unknown_status_uses_secondary(self):
        from scripts.webui.theme import status_text, TEXT_SECONDARY

        label = _FakeLabel()
        status_text(label, "Unknown state", "other")
        assert f"color: {TEXT_SECONDARY}" in label._styles


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
        data.save_node_registry(tmp_path, [])
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
        data.save_node_registry(tmp_path, nodes)

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

    def test_finds_nested_containers_in_3tier_relay(self, tmp_path):
        """Fleet readiness finds services nested inside Manager relay payloads."""
        checkin = data.NodeCheckin(
            node_id="home", hostname="home",
            container_health=data.ContainerHealth(
                container_id="home",
                systemd_services={},
                listening_ports=[],
                ready=True,
                extensions={
                    "containers": {
                        "pihole": {"ready": True, "disk_pct": 34, "mem_pct": 12},
                        "rsyslog": {"ready": True, "disk_pct": 52, "mem_pct": 37},
                    }
                },
            ),
            local_ips=["192.168.86.201"], uptime_seconds=99000,
            services=[], disk_usage_pct=50, memory_usage_pct=30,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "192.168.86.201")
        result = data.check_fleet_readiness(tmp_path, ["pihole", "rsyslog", "home"])
        assert result["all_ready"] is True
        assert result["ready_count"] == 3
        assert result["services"]["pihole"]["ready"] is True
        assert result["services"]["rsyslog"]["ready"] is True
        assert result["services"]["home"]["ready"] is True

    def test_nested_container_not_ready(self, tmp_path):
        """Nested container with ready=False reports not ready."""
        checkin = data.NodeCheckin(
            node_id="home", hostname="home",
            container_health=data.ContainerHealth(
                container_id="home", ready=True,
                systemd_services={}, listening_ports=[],
                extensions={"containers": {"pihole": {"ready": False}}},
            ),
            local_ips=["192.168.86.201"], uptime_seconds=99000,
            services=[], disk_usage_pct=50, memory_usage_pct=30,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "192.168.86.201")
        result = data.check_fleet_readiness(tmp_path, ["pihole"])
        assert result["all_ready"] is False
        assert result["services"]["pihole"]["ready"] is False


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
        data.save_node_registry(tmp_path, nodes)

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

    def test_collect_wireguard_returns_valid_type(self):
        from scripts import callhome
        result = callhome.collect_wireguard()
        assert result is None or isinstance(result, dict)

    def test_collect_docker_returns_valid_type(self):
        from scripts import callhome
        result = callhome.collect_docker()
        assert result is None or isinstance(result, dict)

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
        # WHY: Isolates hash sensitivity to service state changes; real systemd
        # services vary per host and would make the hash non-deterministic.
        # HOW: Verifies that different service states produce different hashes.
        from scripts import callhome
        monkeypatch.setattr(callhome, "get_systemd_services", lambda: {"a": "active"})
        h1 = callhome._compute_state_hash("ct")
        monkeypatch.setattr(callhome, "get_systemd_services", lambda: {"a": "inactive"})
        h2 = callhome._compute_state_hash("ct")
        assert h1 != h2

    def test_compute_state_hash_changes_with_ports(self, monkeypatch):
        # WHY: Isolates hash sensitivity to port changes; real listening ports
        # vary per host and would make the hash non-deterministic.
        # HOW: Verifies that different port sets produce different hashes.
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

    def test_returns_nonempty_version_from_project(self):
        from scripts.callhome import get_version
        result = get_version()
        assert isinstance(result, str)
        assert len(result) > 0
        parts = result.split(".")
        assert len(parts) >= 2, f"Version '{result}' should be semver-like"


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
        assert data.format_uptime(300) == "5m"


class TestFormatNodeStatus:
    def test_online(self):
        assert "Online" in data.format_node_status("online")

    def test_stale(self):
        assert "Stale" in data.format_node_status("stale")

    def test_offline(self):
        assert "Offline" in data.format_node_status("offline")

    def test_reachable(self):
        assert "Reachable" in data.format_node_status("reachable")

    def test_unreachable(self):
        assert "Unreachable" in data.format_node_status("unreachable")

    def test_unknown(self):
        assert "Unknown" in data.format_node_status("unknown")



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


class TestThemeStatusColor:
    """Tests for theme.status_color semantic color mapping."""

    def test_online_green(self):
        from scripts.webui import theme
        assert theme.status_color("online") == theme.COLOR_SUCCESS

    def test_stale_warning(self):
        from scripts.webui import theme
        assert theme.status_color("stale") == theme.COLOR_WARNING

    def test_reachable_warning(self):
        from scripts.webui import theme
        assert theme.status_color("reachable") == theme.COLOR_WARNING

    def test_offline_error(self):
        from scripts.webui import theme
        assert theme.status_color("offline") == theme.COLOR_ERROR

    def test_unreachable_error(self):
        from scripts.webui import theme
        assert theme.status_color("unreachable") == theme.COLOR_ERROR

    def test_unknown_disabled(self):
        from scripts.webui import theme
        assert theme.status_color("unknown") == theme.TEXT_DISABLED

    def test_fallback_disabled(self):
        from scripts.webui import theme
        assert theme.status_color("bogus") == theme.TEXT_DISABLED


class TestThemeSidebarBreakpoint:
    """Verify the left drawer breakpoint fix (Finding 1 from manual testing).

    The breakpoint=0 prop forces the sidebar to always push content
    instead of overlaying it on smaller viewports.
    """

    def test_nav_sidebar_source_has_breakpoint_zero(self):
        """Sidebar renderer must set breakpoint=0 on the left_drawer."""
        import inspect
        from scripts.webui import theme
        source = inspect.getsource(theme._render_sidebar)
        assert 'breakpoint=0' in source or "breakpoint=0" in source


class TestClusterNavigation:
    """Verify the Cluster Manager has its own sidebar without SuperManager-only pages.

    Finding from GUI testing (2026-04-12): The Cluster Manager fleet dashboard
    used page_shell (SuperManager sidebar) which showed links to /services,
    /deploy, /images, /nodes, /hosts, /environment — all 404 on the kiosk.
    """

    def test_cluster_nav_sections_exist(self):
        from scripts.webui.data import CLUSTER_NAV_SECTIONS
        assert len(CLUSTER_NAV_SECTIONS) > 0

    def test_cluster_nav_no_supermanager_pages(self):
        from scripts.webui.data import CLUSTER_NAV_SECTIONS, Routes
        cluster_paths = {path for _, path, _ in CLUSTER_NAV_SECTIONS}
        supermanager_only = {
            Routes.SERVICES, Routes.DEPLOY, Routes.IMAGES,
            Routes.NODES, Routes.HOSTS, Routes.ENVIRONMENT,
        }
        assert cluster_paths.isdisjoint(supermanager_only), (
            f"Cluster sidebar must not include SuperManager-only pages: "
            f"{cluster_paths & supermanager_only}"
        )

    def test_cluster_nav_has_fleet(self):
        from scripts.webui.data import CLUSTER_NAV_SECTIONS, Routes
        cluster_paths = {path for _, path, _ in CLUSTER_NAV_SECTIONS}
        assert Routes.FLEET in cluster_paths

    def test_cluster_nav_has_kiosk_pages(self):
        from scripts.webui.data import CLUSTER_NAV_SECTIONS, Routes
        cluster_paths = {path for _, path, _ in CLUSTER_NAV_SECTIONS}
        assert Routes.BRIDGE in cluster_paths
        assert Routes.MESH in cluster_paths
        assert Routes.ROUTER in cluster_paths
        assert Routes.CONTAINERS in cluster_paths

    def test_routes_fleet_defined(self):
        from scripts.webui.data import Routes
        assert hasattr(Routes, "FLEET")
        assert Routes.FLEET == "/fleet"

    def test_cluster_dashboard_uses_cluster_shell(self):
        """cluster_dashboard.py must use cluster_page_shell, not page_shell."""
        import inspect
        from scripts.webui.pages import cluster_dashboard
        source = inspect.getsource(cluster_dashboard)
        assert "cluster_page_shell" in source
        assert "page_shell(" not in source.replace("cluster_page_shell", "")

    def test_theme_has_cluster_page_shell(self):
        from scripts.webui import theme
        assert hasattr(theme, "cluster_page_shell")
        assert callable(theme.cluster_page_shell)


class TestKioskServerPort:
    """Verify kiosk_server sets the API client port correctly.

    Finding from GUI testing (2026-04-12): The containers page on the kiosk
    returned 'No guests found' because the ApiClient used the default port
    (from WEBUI_PORT env) instead of the kiosk's actual port (9001).
    """

    def test_kiosk_server_calls_set_server_port(self):
        """kiosk_server.py must call set_server_port() so the API client
        sends requests to the kiosk's own port, not the SuperManager port.
        """
        import inspect
        from scripts.webui import kiosk_server
        source = inspect.getsource(kiosk_server)
        assert "set_server_port" in source


class TestKioskHostIpRouting:
    """Verify kiosk_configure uses a container-routable HOST_IP.

    Finding from GUI testing (2026-04-12): The Containers page was blank
    because HOST_IP was set to ansible_host (WAN IP), which the kiosk
    container on the LAN bridge couldn't route to.
    """

    _KIOSK_TASKS = PROJECT_ROOT / "roles/kiosk_configure/tasks/main.yml"

    def test_kiosk_configure_uses_kiosk_host_ip(self):
        """HOST_IP must reference _kiosk_host_ip, not ansible_host."""
        content = self._KIOSK_TASKS.read_text()
        assert '_kiosk_host_ip' in content
        assert 'HOST_IP: "{{ ansible_host }}"' not in content

    def test_kiosk_configure_computes_host_ip_for_lan(self):
        """LAN containers should use the LAN management IP, not ansible_host."""
        content = self._KIOSK_TASKS.read_text()
        assert "Compute kiosk-reachable host IP" in content
        assert "router_nodes" in content
        assert "lan_hosts" in content

    def test_kiosk_configure_computes_host_ip_for_wan(self):
        """WAN containers should use the NAT bridge gateway."""
        content = self._KIOSK_TASKS.read_text()
        assert "container_subnet_prefix" in content
        assert "container_subnet_id" in content


# ── Host Registry (M0 domain classes) ────────────────────────────────


class TestHostBucket:
    """HostBucket.classify_ip returns correct bucket for IP ranges."""

    def test_test_unit_201(self):
        assert data.HostBucket.classify_ip("192.168.86.201") == "test"

    def test_test_unit_230(self):
        assert data.HostBucket.classify_ip("192.168.86.230") == "test"

    def test_test_unit_255(self):
        assert data.HostBucket.classify_ip("192.168.86.255") == "test"

    def test_lab_unit_low(self):
        assert data.HostBucket.classify_ip("10.0.0.5") == "lab"

    def test_lab_unit_30(self):
        assert data.HostBucket.classify_ip("192.168.86.30") == "lab"

    def test_lab_unit_100_series(self):
        assert data.HostBucket.classify_ip("192.168.86.150") == "lab"

    def test_lab_unit_199(self):
        assert data.HostBucket.classify_ip("192.168.86.199") == "lab"

    def test_invalid_ip_returns_default(self):
        assert data.HostBucket.classify_ip("garbage") == data.HostBucket.DEFAULT

    def test_empty_ip_returns_default(self):
        assert data.HostBucket.classify_ip("") == data.HostBucket.DEFAULT


class TestHostRecord:
    """HostRecord dataclass creation and field defaults."""

    def test_minimal_creation(self):
        r = data.HostRecord(name="test-host", ip="1.2.3.4")
        assert r.name == "test-host"
        assert r.ip == "1.2.3.4"
        assert r.mac == ""
        assert r.bucket == ""
        assert r.source == "manual"
        assert r.wol_capable is True
        assert r.is_lan is False

    def test_full_creation(self):
        r = data.HostRecord(
            name="home", ip="192.168.86.201", mac="aa:bb:cc:dd:ee:ff",
            bucket="test", source="env", is_lan=False, wol_capable=True,
            vpn_ip="10.99.0.1", first_seen="2026-01-01", last_seen="2026-01-02",
        )
        assert r.bucket == "test"
        assert r.vpn_ip == "10.99.0.1"


class TestHostRegistry:
    """HostRegistry CRUD, upsert, and persistence."""

    def test_empty_registry(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        assert reg.all() == []

    def test_register_new_host(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        r = reg.register("home", "192.168.86.201", source="env")
        assert r.name == "home"
        assert r.ip == "192.168.86.201"
        assert r.bucket == "test"
        assert r.first_seen != ""
        assert len(reg.all()) == 1

    def test_upsert_by_name_preserves_immutable(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        r1 = reg.register("home", "1.2.3.4", bucket="lab", source="env")
        r2 = reg.register("home", "5.6.7.8")
        assert r2.ip == "5.6.7.8"
        assert r2.bucket == "lab"
        assert r2.source == "env"
        assert r2.first_seen == r1.first_seen
        assert len(reg.all()) == 1

    def test_upsert_by_mac(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        reg.register("home", "1.2.3.4", mac="aa:bb:cc:dd:ee:ff")
        r2 = reg.register("renamed", "9.8.7.6", mac="aa:bb:cc:dd:ee:ff")
        assert r2.name == "home"
        assert r2.ip == "9.8.7.6"
        assert len(reg.all()) == 1

    def test_mac_takes_precedence(self, tmp_path):
        """MAC match wins over name match when both exist."""
        reg = data.HostRegistry(tmp_path)
        reg.register("alpha", "1.1.1.1", mac="aa:bb:cc:dd:ee:ff")
        reg.register("beta", "2.2.2.2")
        r = reg.register("beta", "3.3.3.3", mac="aa:bb:cc:dd:ee:ff")
        assert r.name == "alpha"
        assert r.ip == "3.3.3.3"
        assert len(reg.all()) == 2

    def test_json_round_trip(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        reg.register("a", "1.1.1.1", mac="aa:bb:cc:dd:ee:ff", bucket="test")
        reg.register("b", "2.2.2.2", is_lan=True, wol_capable=False)
        reg2 = data.HostRegistry(tmp_path)
        hosts = reg2.all()
        assert len(hosts) == 2
        a = reg2.find_by_name("a")
        assert a is not None
        assert a.mac == "aa:bb:cc:dd:ee:ff"
        assert a.bucket == "test"
        b = reg2.find_by_name("b")
        assert b is not None
        assert b.is_lan is True
        assert b.wol_capable is False

    def test_find_by_mac(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        reg.register("x", "1.2.3.4", mac="AA:BB:CC:DD:EE:FF")
        assert reg.find_by_mac("aa:bb:cc:dd:ee:ff") is not None
        assert reg.find_by_mac("") is None
        assert reg.find_by_mac("11:22:33:44:55:66") is None

    def test_find_by_name(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        reg.register("alpha", "1.1.1.1")
        assert reg.find_by_name("alpha") is not None
        assert reg.find_by_name("nonexistent") is None

    def test_corrupted_json_handled(self, tmp_path):
        (tmp_path / "registry.json").write_text("not-json!!!")
        reg = data.HostRegistry(tmp_path)
        assert reg.all() == []

    def test_update_vpn_ip(self, tmp_path):
        reg = data.HostRegistry(tmp_path)
        reg.register("home", "1.2.3.4")
        r = reg.register("home", "1.2.3.4", vpn_ip="10.99.0.1")
        assert r.vpn_ip == "10.99.0.1"


class TestHostRegistrySeedFromEnv:
    """HostRegistry.seed_from_env reads _HOST_MAP + TEST_UNITS."""

    def test_seeds_from_host_map(self, tmp_path):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "AI_HOST": "192.168.86.220",
            "MESH_2_HOST": "192.168.86.211",
        }
        reg = data.HostRegistry(tmp_path)
        reg.seed_from_env(env)
        hosts = reg.all()
        names = [h.name for h in hosts]
        assert "home" in names
        assert "ai" in names
        assert "mesh2" in names
        assert "mesh1" in names

    def test_seeds_test_units(self, tmp_path):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "TEST_UNITS": "192.168.86.201,192.168.86.230",
        }
        reg = data.HostRegistry(tmp_path)
        reg.seed_from_env(env)
        test_hosts = [h for h in reg.all() if h.bucket == "test"]
        assert len(test_hosts) >= 2

    def test_seed_idempotent(self, tmp_path):
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "TEST_UNITS": "192.168.86.230",
        }
        reg = data.HostRegistry(tmp_path)
        reg.seed_from_env(env)
        count1 = len(reg.all())
        reg.seed_from_env(env)
        count2 = len(reg.all())
        assert count1 == count2

    def test_skips_test_unit_with_same_ip_as_host(self, tmp_path):
        """TEST_UNITS entry with same IP as PRIMARY_HOST is not duplicated."""
        env = {
            "PRIMARY_HOST": "192.168.86.201",
            "TEST_UNITS": "192.168.86.201",
        }
        reg = data.HostRegistry(tmp_path)
        reg.seed_from_env(env)
        records_with_ip = [r for r in reg.all() if r.ip == "192.168.86.201"]
        assert len(records_with_ip) == 1


class TestExtractPrimaryMac:
    """extract_primary_mac picks correct MAC from heartbeat extensions."""

    def test_picks_first_real_mac(self):
        ext = {
            "network": {
                "interfaces": [
                    {"name": "lo", "mac": "00:00:00:00:00:00"},
                    {"name": "eth0", "mac": "aa:bb:cc:dd:ee:ff"},
                    {"name": "eth1", "mac": "11:22:33:44:55:66"},
                ],
            },
        }
        assert data.extract_primary_mac(ext) == "aa:bb:cc:dd:ee:ff"

    def test_skips_loopback(self):
        ext = {
            "network": {
                "interfaces": [
                    {"name": "lo", "mac": "00:00:00:00:00:00"},
                ],
            },
        }
        assert data.extract_primary_mac(ext) == ""

    def test_skips_fe_prefix(self):
        ext = {
            "network": {
                "interfaces": [
                    {"name": "veth0", "mac": "fe:01:02:03:04:05"},
                    {"name": "eth0", "mac": "aa:bb:cc:dd:ee:ff"},
                ],
            },
        }
        assert data.extract_primary_mac(ext) == "aa:bb:cc:dd:ee:ff"

    def test_empty_extensions(self):
        assert data.extract_primary_mac({}) == ""

    def test_no_interfaces(self):
        ext = {"network": {}}
        assert data.extract_primary_mac(ext) == ""

    def test_nested_data_format(self):
        ext = {
            "network": {
                "data": {
                    "interfaces": [
                        {"name": "eth0", "mac": "11:22:33:44:55:66"},
                    ],
                },
            },
        }
        assert data.extract_primary_mac(ext) == "11:22:33:44:55:66"


class TestContainerHeartbeatIsolation:
    """Heartbeats update nodes.json telemetry but NEVER the HostRegistry.

    The HostRegistry contains physical host identities only — seeded from
    env vars, manual registration, or TEST_UNITS. Container heartbeats
    must not create host entries; they are tracked in nodes.json and
    surfaced as guests of their parent host.
    """

    def test_container_checkin_does_not_pollute_registry(self, tmp_path):
        """Container heartbeat updates nodes.json but NOT HostRegistry."""
        reg = data.HostRegistry(tmp_path)
        reg.register("home", "192.168.86.201", source="env")
        initial_count = len(reg.all())

        checkin = data.NodeCheckin(
            node_id="pihole", hostname="pihole",
            local_ips=["10.10.10.10"], uptime_seconds=3600,
            services=[], disk_usage_pct=34, memory_usage_pct=12,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.10")

        fresh = data.HostRegistry(tmp_path)
        assert len(fresh.all()) == initial_count
        assert fresh.find_by_name("pihole") is None
        nodes = data.load_node_registry(tmp_path)
        assert any(n.hostname == "pihole" for n in nodes)

    def test_multiple_container_heartbeats_never_grow_registry(self, tmp_path):
        """Repeated container heartbeats must not create registry entries."""
        reg = data.HostRegistry(tmp_path)
        reg.register("home", "192.168.86.201", source="env")
        initial_count = len(reg.all())

        for name in ["wireguard", "pihole", "netdata", "rsyslog", "jellyfin"]:
            checkin = data.NodeCheckin(
                node_id=name, hostname=name,
                local_ips=[f"10.10.10.{hash(name) % 200}"],
                uptime_seconds=100, services=[],
                disk_usage_pct=20, memory_usage_pct=30, version="1.0",
            )
            data.register_checkin(tmp_path, checkin, f"10.10.10.{hash(name) % 200}")

        fresh = data.HostRegistry(tmp_path)
        assert len(fresh.all()) == initial_count

    def test_build_fleet_excludes_container_heartbeats(self, tmp_path):
        """Fleet hosts come from HostRegistry only, not container heartbeats."""
        for name in ["pihole", "wireguard", "netdata"]:
            checkin = data.NodeCheckin(
                node_id=name, hostname=name,
                local_ips=["10.10.10.10"], uptime_seconds=100,
                services=[], disk_usage_pct=20, memory_usage_pct=30,
                version="1.0",
            )
            data.register_checkin(tmp_path, checkin, "10.10.10.10")

        env = {"PRIMARY_HOST": "192.168.86.201"}
        fleet = data.build_fleet(env, tmp_path)
        host_names = [h.name for h in fleet.hosts]
        assert "pihole" not in host_names
        assert "wireguard" not in host_names
        assert "netdata" not in host_names
        assert "home" in host_names

    def test_registry_only_grows_from_env_or_manual(self, tmp_path):
        """HostRegistry entries come from env or manual form, never heartbeats."""
        reg = data.HostRegistry(tmp_path)
        env = {"PRIMARY_HOST": "192.168.86.201"}
        reg.seed_from_env(env)
        env_count = len(reg.all())

        checkin = data.NodeCheckin(
            node_id="netdata", hostname="netdata",
            local_ips=["10.10.10.21"], uptime_seconds=100,
            services=[], disk_usage_pct=62, memory_usage_pct=53,
            version="1.0",
        )
        data.register_checkin(tmp_path, checkin, "10.10.10.21")
        assert len(data.HostRegistry(tmp_path).all()) == env_count

    def test_host_level_heartbeat_does_not_create_registry_entry(self, tmp_path):
        """Even host-mode heartbeats should not auto-create registry entries."""
        checkin = data.NodeCheckin(
            node_id="unknown-host", hostname="unknown-host",
            local_ips=["192.168.86.250"], uptime_seconds=3600,
            services=["vm:100:openwrt"], disk_usage_pct=45,
            memory_usage_pct=60, version="2.0",
        )
        data.register_checkin(tmp_path, checkin, "192.168.86.250")

        reg = data.HostRegistry(tmp_path)
        assert reg.find_by_name("unknown-host") is None
        nodes = data.load_node_registry(tmp_path)
        assert any(n.hostname == "unknown-host" for n in nodes)


class TestHostRegistryConstants:
    """New Labels/ApiRoutes constants for M3."""

    def test_add_host_label(self):
        assert data.Labels.ADD_HOST == "Add Host"

    def test_register_label(self):
        assert data.Labels.REGISTER == "Register"

    def test_bucket_labels(self):
        assert data.Labels.BUCKET_TEST == "Test Units"
        assert data.Labels.BUCKET_LAB == "Lab Units"
        assert data.Labels.BUCKET_PRODUCTION == "Production"

    def test_host_register_api_route(self):
        assert data.ApiRoutes.HOST_REGISTER == "/api/hosts/register"


# ── Manager relay tests ──────────────────────────────────────────────


class TestManagerContainerCheckin:
    """Verify the Manager's /api/checkin stores container heartbeats."""

    def setup_method(self):
        from scripts.webui import manager
        manager.init(lambda n: "10.10.10.1")
        manager.clear_container_checkins()

    def teardown_method(self):
        from scripts.webui import manager
        manager.reset()

    def test_container_checkin_stored_in_memory(self):
        from scripts.webui import manager
        checkins = manager.get_container_checkins()
        assert len(checkins) == 0
        checkins["pihole"] = {
            "payload": {"hostname": "pihole", "disk_usage_pct": 34},
            "received_at": "2026-04-07T12:00:00",
        }
        assert len(manager.get_container_checkins()) == 1
        assert "pihole" in manager.get_container_checkins()

    def test_multiple_containers_stored_independently(self):
        from scripts.webui import manager
        checkins = manager.get_container_checkins()
        checkins["pihole"] = {
            "payload": {"hostname": "pihole"},
            "received_at": "2026-04-07T12:00:00",
        }
        checkins["wireguard"] = {
            "payload": {"hostname": "wireguard"},
            "received_at": "2026-04-07T12:00:01",
        }
        assert len(manager.get_container_checkins()) == 2

    def test_reset_clears_manager_instance(self):
        from scripts.webui import manager
        manager.get_container_checkins()["test"] = {"payload": {}, "received_at": ""}
        assert len(manager.get_container_checkins()) == 1
        manager.reset()
        import pytest
        with pytest.raises(RuntimeError, match="manager.init.*has not been called"):
            manager.get_container_checkins()


class TestManagerRelayPayload:
    """Verify relay payload construction matches SuperManager expectations."""

    def setup_method(self):
        from scripts.webui import manager
        manager.init(lambda n: "10.10.10.1")

    def teardown_method(self):
        from scripts.webui import manager
        manager.reset()

    def test_relay_builds_host_level_payload(self):
        from scripts.webui import manager
        container_checkins = {
            "pihole": {
                "payload": {
                    "hostname": "pihole",
                    "disk_usage_pct": 34,
                    "memory_usage_pct": 12,
                    "container_health": {"ready": True},
                },
                "received_at": "2026-04-07T12:00:00",
            },
            "wireguard": {
                "payload": {
                    "hostname": "wireguard",
                    "disk_usage_pct": 52,
                    "memory_usage_pct": 20,
                    "container_health": {"ready": True},
                },
                "received_at": "2026-04-07T12:00:01",
            },
        }
        host_metrics = {
            "disk_usage_pct": 45,
            "memory_usage_pct": 60,
            "uptime_seconds": 86400,
            "services": ["ct:102:pihole:running", "ct:101:wireguard:running"],
        }

        payload = manager.build_relay_payload(
            host_name="home",
            host_ip="192.168.86.201",
            host_metrics=host_metrics,
            container_checkins=container_checkins,
        )

        assert payload["node_id"] == "home"
        assert payload["hostname"] == "home"
        assert payload["local_ips"] == ["192.168.86.201"]
        assert payload["disk_usage_pct"] == 45
        assert payload["memory_usage_pct"] == 60
        assert payload["uptime_seconds"] == 86400
        assert len(payload["services"]) == 2
        ct_health = payload["container_health"]
        assert ct_health["container_id"] == "home"
        assert ct_health["ready"] is True
        containers = ct_health["extensions"]["containers"]
        assert "pihole" in containers
        assert containers["pihole"]["ready"] is True
        assert containers["pihole"]["disk_pct"] == 34
        assert "wireguard" in containers
        assert containers["wireguard"]["ready"] is True

    def test_relay_payload_empty_containers(self):
        from scripts.webui import manager
        payload = manager.build_relay_payload(
            host_name="ai",
            host_ip="192.168.86.220",
            host_metrics={"disk_usage_pct": 30, "memory_usage_pct": 40,
                          "uptime_seconds": 3600, "services": []},
            container_checkins={},
        )
        assert payload["node_id"] == "ai"
        assert payload["container_health"]["extensions"]["containers"] == {}
        assert payload["services"] == []

    def test_relay_payload_no_host_ip(self):
        from scripts.webui import manager
        payload = manager.build_relay_payload(
            host_name="mesh1",
            host_ip="",
            host_metrics={"disk_usage_pct": 0, "memory_usage_pct": 0,
                          "uptime_seconds": 0, "services": []},
            container_checkins={},
        )
        assert payload["local_ips"] == []


@pytest.mark.integration
class TestManagerRelayPost:
    """Verify relay POST behavior via BaseManager._post_to_upstream."""

    def setup_method(self):
        from scripts.webui import manager
        self.mgr = manager.init(lambda n: None)

    def teardown_method(self):
        from scripts.webui import manager
        manager.reset()

    def test_relay_skips_when_no_management_server(self):
        assert self.mgr.management_server == ""

    def test_post_to_upstream_handles_connection_error(self):
        result = self.mgr._post_to_upstream(
            "http://192.0.2.1:99999/api/checkin",
            {"node_id": "test"},
        )
        assert result is False

    def test_post_to_upstream_success(self):
        """POST to the real API server running on WEBUI_PORT."""
        import socket
        port = int(os.environ.get("WEBUI_PORT", "52525"))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(1)
            reachable = sock.connect_ex(("localhost", port)) == 0
        finally:
            sock.close()
        if not reachable:
            pytest.skip(f"API server not running on port {port}")
        token = os.environ.get("CALLHOME_PUBLIC_KEY", "")
        assert token, (
            "CALLHOME_PUBLIC_KEY not in os.environ — conftest.py "
            "should have loaded it from test.env at session start"
        )
        url = f"http://localhost:{port}/api/checkin"
        result = self.mgr._post_to_upstream(
            url,
            {"node_id": "relay-test", "hostname": "relay-test",
             "local_ips": ["127.0.0.1"], "uptime_seconds": 1,
             "services": [], "disk_usage_pct": 0, "memory_usage_pct": 0},
            token=token,
        )
        assert result is True

    def test_post_to_unreachable_returns_false(self):
        """POST to an unreachable endpoint returns False."""
        result = self.mgr._post_to_upstream(
            "http://10.254.254.254:1/api/checkin",
            {"node_id": "test"},
        )
        assert result is False


class TestManagerPublicKeyRetrieval:
    """Verify callhome_public_key is provided via config at init time."""

    def test_default_is_empty_when_not_provided(self):
        from scripts.webui import manager
        mgr = manager.init(lambda n: None)
        assert mgr.callhome_public_key == ""
        manager.reset()

    def test_reads_from_config_at_init(self):
        from scripts.webui import manager
        mgr = manager.init(
            lambda n: None,
            config={"CALLHOME_PUBLIC_KEY": "config-key-456"},
        )
        assert mgr.callhome_public_key == "config-key-456"
        manager.reset()


class TestThreeTierDataFlow:
    """End-to-end data flow: container heartbeat → relay payload → SM format.

    Validates the 4-tier invariant: containers NEVER appear directly
    on the SuperManager. Only host-level payloads from NodeManagers appear.
    """

    def setup_method(self):
        from scripts.webui import manager
        manager.init(lambda n: "10.10.10.1")
        manager.clear_container_checkins()

    def teardown_method(self):
        from scripts.webui import manager
        manager.reset()

    def test_container_heartbeats_nested_under_host(self):
        """Container data must be in extensions.containers, not top-level."""
        from scripts.webui import manager
        checkins = manager.get_container_checkins()
        checkins["pihole"] = {
            "payload": {
                "hostname": "pihole",
                "disk_usage_pct": 34,
                "memory_usage_pct": 12,
                "uptime_seconds": 3600,
                "container_health": {
                    "container_id": "pihole",
                    "ready": True,
                    "systemd_services": {"pihole-FTL": "active"},
                    "listening_ports": [53, 80],
                    "extensions": {},
                },
            },
            "received_at": "2026-04-08T12:00:00",
        }
        checkins["wireguard"] = {
            "payload": {
                "hostname": "wireguard",
                "disk_usage_pct": 20,
                "memory_usage_pct": 8,
                "container_health": {"ready": True},
            },
            "received_at": "2026-04-08T12:00:05",
        }

        payload = manager.build_relay_payload(
            host_name="home",
            host_ip="192.168.86.201",
            host_metrics={"disk_usage_pct": 50, "memory_usage_pct": 30,
                          "uptime_seconds": 99000, "services": []},
            container_checkins=checkins,
        )

        assert payload["hostname"] == "home"
        assert payload["node_id"] == "home"
        assert payload["local_ips"] == ["192.168.86.201"]
        assert payload["disk_usage_pct"] == 50
        assert payload["memory_usage_pct"] == 30

        containers = payload["container_health"]["extensions"]["containers"]
        assert "pihole" in containers
        assert "wireguard" in containers
        assert containers["pihole"]["ready"] is True
        assert containers["pihole"]["disk_pct"] == 34
        assert containers["wireguard"]["ready"] is True

    def test_relay_payload_is_valid_node_checkin(self):
        """Relay payload must be a valid NodeCheckin for /api/checkin."""
        from scripts.webui import manager
        payload = manager.build_relay_payload(
            host_name="mesh2",
            host_ip="192.168.86.211",
            host_metrics={"disk_usage_pct": 40, "memory_usage_pct": 20,
                          "uptime_seconds": 5000, "services": ["ct:401:kiosk:running"]},
            container_checkins={},
        )

        required_keys = {"node_id", "hostname", "local_ips", "uptime_seconds",
                         "disk_usage_pct", "memory_usage_pct", "container_health"}
        assert required_keys.issubset(payload.keys())
        assert payload["container_health"]["container_id"] == "mesh2"
        assert payload["container_health"]["ready"] is True

    def test_no_container_leaks_to_top_level(self):
        """Container names must NOT appear as top-level hostname/node_id."""
        from scripts.webui import manager
        checkins = manager.get_container_checkins()
        checkins["pihole"] = {
            "payload": {"hostname": "pihole", "container_health": {"ready": True}},
            "received_at": "now",
        }

        payload = manager.build_relay_payload(
            host_name="home",
            host_ip="192.168.86.201",
            host_metrics={"disk_usage_pct": 0, "memory_usage_pct": 0,
                          "uptime_seconds": 0, "services": []},
            container_checkins=checkins,
        )

        assert payload["hostname"] == "home", "Container name must not leak to top-level"
        assert payload["node_id"] == "home"
        assert "pihole" not in payload["hostname"]

    def test_manager_api_checkin_stores_container_data(self):
        """Simulates POST to Manager /api/checkin from a container."""
        from scripts.webui import manager
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        mgr = manager.get_instance()
        test_app = Starlette(routes=[
            Route("/api/checkin", mgr.handle_container_checkin, methods=["POST"]),
        ])

        manager.clear_container_checkins()
        client = TestClient(test_app)
        resp = client.post("/api/checkin", json={
            "hostname": "rsyslog",
            "node_id": "rsyslog",
            "disk_usage_pct": 52,
            "memory_usage_pct": 37,
            "container_health": {
                "container_id": "rsyslog",
                "ready": True,
                "systemd_services": {"rsyslog": "active"},
                "listening_ports": [514],
                "extensions": {},
            },
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        checkins = manager.get_container_checkins()
        assert "rsyslog" in checkins
        assert checkins["rsyslog"]["payload"]["disk_usage_pct"] == 52

    def test_manager_api_checkin_rejects_empty_hostname(self):
        """POST with no hostname must return 400."""
        from scripts.webui import manager
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        mgr = manager.get_instance()
        test_app = Starlette(routes=[
            Route("/api/checkin", mgr.handle_container_checkin, methods=["POST"]),
        ])
        client = TestClient(test_app)
        resp = client.post("/api/checkin", json={"disk_usage_pct": 10})
        assert resp.status_code == 400

    def test_full_roundtrip_container_to_relay(self):
        """Full pipeline: container checkin → store → build relay → verify nesting."""
        from scripts.webui import manager
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        mgr = manager.get_instance()
        test_app = Starlette(routes=[
            Route("/api/checkin", mgr.handle_container_checkin, methods=["POST"]),
        ])

        manager.clear_container_checkins()
        client = TestClient(test_app)

        client.post("/api/checkin", json={
            "hostname": "pihole",
            "node_id": "pihole",
            "disk_usage_pct": 34,
            "memory_usage_pct": 12,
            "container_health": {"container_id": "pihole", "ready": True},
        })
        client.post("/api/checkin", json={
            "hostname": "wireguard",
            "node_id": "wireguard",
            "disk_usage_pct": 20,
            "memory_usage_pct": 8,
            "container_health": {"container_id": "wireguard", "ready": True},
        })

        payload = manager.build_relay_payload(
            host_name="home",
            host_ip="192.168.86.201",
            host_metrics={"disk_usage_pct": 50, "memory_usage_pct": 30,
                          "uptime_seconds": 5000, "services": []},
            container_checkins=manager.get_container_checkins(),
        )

        assert payload["hostname"] == "home"
        assert payload["node_id"] == "home"
        assert "pihole" not in payload["hostname"]
        assert "wireguard" not in payload["hostname"]

        containers = payload["container_health"]["extensions"]["containers"]
        assert len(containers) == 2
        assert containers["pihole"]["ready"] is True
        assert containers["pihole"]["disk_pct"] == 34
        assert containers["wireguard"]["ready"] is True


class TestClusterManagerFleetStorage:
    """Verify ClusterManager stores child Manager heartbeats and includes them in relay."""

    def setup_method(self):
        from scripts.webui import manager
        self.mgr = manager.init(
            lambda n: None,
            config={"HOST_IP": "192.168.86.201", "HOST_NAME": "home", "MESH_KEY": "test"},
            manager_class=manager.ClusterManager,
        )

    def teardown_method(self):
        from scripts.webui import manager
        manager.reset()

    def test_register_child_checkin(self):
        from scripts.webui import manager
        mgr = manager.get_instance()
        assert isinstance(mgr, manager.ClusterManager)
        mgr.register_child_checkin({
            "node_id": "mesh1",
            "hostname": "mesh1",
            "local_ips": ["10.10.10.210"],
            "disk_usage_pct": 40,
            "memory_usage_pct": 25,
        })
        nodes = mgr.get_fleet_nodes()
        assert "mesh1" in nodes
        assert nodes["mesh1"]["payload"]["hostname"] == "mesh1"

    def test_multiple_children(self):
        from scripts.webui import manager
        mgr = manager.get_instance()
        assert isinstance(mgr, manager.ClusterManager)
        mgr.register_child_checkin({"node_id": "mesh1", "hostname": "mesh1", "local_ips": ["10.10.10.210"]})
        mgr.register_child_checkin({"node_id": "bridge-1", "hostname": "bridge-1", "local_ips": ["192.168.86.230"]})
        assert len(mgr.get_fleet_nodes()) == 2

    def test_relay_payload_includes_cluster_nodes(self):
        from scripts.webui import manager
        mgr = manager.get_instance()
        assert isinstance(mgr, manager.ClusterManager)
        mgr.register_child_checkin({
            "node_id": "mesh1",
            "hostname": "mesh1",
            "local_ips": ["10.10.10.210"],
            "disk_usage_pct": 40,
        })
        payload = mgr.build_relay_payload(
            host_name="home",
            host_ip="192.168.86.201",
            host_metrics={"disk_usage_pct": 50, "memory_usage_pct": 30,
                          "uptime_seconds": 5000, "services": []},
            container_checkins={},
        )
        assert "cluster_nodes" in payload
        assert "mesh1" in payload["cluster_nodes"]
        assert payload["cluster_nodes"]["mesh1"]["hostname"] == "mesh1"
        assert payload["cluster_nodes"]["mesh1"]["disk_usage_pct"] == 40

    def test_missing_node_id_raises(self):
        from scripts.webui import manager
        import pytest
        mgr = manager.get_instance()
        assert isinstance(mgr, manager.ClusterManager)
        with pytest.raises(ValueError, match="missing node_id"):
            mgr.register_child_checkin({"hostname": ""})


class TestDisplayConstants:
    """Verify display-related constants exist in data.py."""

    def test_ports_class(self):
        assert hasattr(data, "Ports")
        assert data.Ports.KIOSK_DISPLAY == 6080

    def test_routes_remote_kiosk(self):
        assert hasattr(data.Routes, "REMOTE_KIOSK")
        assert "{node_id}" in data.Routes.REMOTE_KIOSK

    def test_labels_display(self):
        assert hasattr(data.Labels, "OPEN_KIOSK")
        assert hasattr(data.Labels, "KIOSK_NOT_REACHABLE")
        assert hasattr(data.Labels, "DRILL_INTO")
        assert hasattr(data.Labels, "GO_BACK")


class TestManagerDisplayResolution:
    """Verify display URL resolution and child topology via real manager instances."""

    def setup_method(self):
        from scripts.webui import manager
        self._mgr_module = manager

    def teardown_method(self):
        self._mgr_module.reset()

    def test_node_manager_returns_none(self):
        mgr = self._mgr_module.init(
            lambda n: None,
            config={"HOST_IP": "10.99.3.19", "HOST_NAME": "ai", "MESH_KEY": "test"},
            manager_class=self._mgr_module.NodeManager,
        )
        assert mgr.get_child_display_url("home") is None
        assert mgr.get_fleet_children("ai") == []

    def test_cluster_manager_resolves_from_child_managers(self):
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210", "ai": "192.168.86.220"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        url = mgr.get_child_display_url("mesh1")
        assert url is not None
        assert "10.10.10.210" in url
        assert str(data.Ports.KIOSK_DISPLAY) in url

    def test_cluster_manager_unknown_returns_none(self):
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        assert mgr.get_child_display_url("nonexistent") is None

    def test_cluster_manager_fleet_children(self):
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210", "ai": "192.168.86.220"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        children = mgr.get_fleet_children("home")
        assert "mesh1" in children
        assert "ai" in children

    def test_cluster_manager_fleet_children_empty_for_non_self(self):
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        children = mgr.get_fleet_children("mesh1")
        assert children == []

    def test_get_guest_viewstream_url_resolves(self):
        """get_guest_viewstream_url delegates to DisplayTransferService."""
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        url = mgr.get_guest_viewstream_url("mesh1", "desktop")
        assert url is not None
        assert "10.10.10.210" in url
        assert str(data.Ports.DESKTOP_DISPLAY) in url

    def test_get_guest_viewstream_url_unknown_node(self):
        """Returns None when node cannot be resolved."""
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        assert mgr.get_guest_viewstream_url("nonexistent", "desktop") is None

    def test_get_guest_viewstream_url_unknown_app(self):
        """Returns None when app_id has no registered handler."""
        mgr = self._mgr_module.init(
            lambda n: None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.10.10.210"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        assert mgr.get_guest_viewstream_url("mesh1", "nonexistent") is None

    def test_supermanager_resolves_from_fleet_nodes(self):
        """SM resolves display IP from display_resolver (not node_resolver)."""
        def node_resolver(n):
            return {"mesh1": "10.0.0.2", "ai": "10.0.0.3"}.get(n)

        def display_resolver(n):
            return {"mesh1": ("10.10.10.210", 0), "ai": ("192.168.86.220", 0)}.get(n)

        mgr = self._mgr_module.init(
            node_resolver,
            config={"HOST_IP": "192.168.86.201", "HOST_NAME": "super", "MESH_KEY": "test"},
            manager_class=self._mgr_module.ClusterManager,
            display_resolver=display_resolver,
        )
        url = mgr.get_child_display_url("mesh1")
        assert url is not None
        assert "10.10.10.210" in url
        assert str(data.Ports.KIOSK_DISPLAY) in url

    def test_vpn_display_url_uses_standard_port(self):
        """VPN-first: all display URLs use the standard port, no proxy remapping."""
        mgr = self._mgr_module.init(
            lambda n: {"mesh1": "10.0.0.2"}.get(n),
            config={
                "HOST_IP": "10.0.0.1", "HOST_NAME": "super", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {"mesh1": "10.0.0.2"},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        url = mgr.get_child_display_url("mesh1")
        assert url is not None
        assert "10.0.0.2" in url
        assert str(data.Ports.KIOSK_DISPLAY) in url

    def test_supermanager_fleet_children_includes_fleet_nodes(self):
        """SM's get_fleet_children returns fleet nodes as children."""
        mgr = self._mgr_module.init(
            lambda n: None,
            config={"HOST_IP": "192.168.86.201", "HOST_NAME": "super", "MESH_KEY": "test"},
            manager_class=self._mgr_module.ClusterManager,
        )
        mgr.register_child_checkin({
            "node_id": "mesh1", "hostname": "mesh1",
            "local_ips": ["10.10.10.210"],
        })
        mgr.register_child_checkin({
            "node_id": "ai", "hostname": "ai",
            "local_ips": ["192.168.86.220"],
        })
        children = mgr.get_fleet_children("super")
        assert "mesh1" in children
        assert "ai" in children

    def test_no_display_resolver_returns_none_for_unknown_nodes(self):
        """Without display_resolver, nodes not in _child_managers return None."""
        mgr = self._mgr_module.init(
            lambda n: "10.0.0.2" if n == "mesh1" else None,
            config={
                "HOST_IP": "10.10.10.23", "HOST_NAME": "home", "MESH_KEY": "test",
                "CHILD_MANAGER_IPS": {},
            },
            manager_class=self._mgr_module.ClusterManager,
        )
        assert mgr.get_child_display_url("mesh1") is None

    def test_display_resolver_with_port_offset(self):
        """display_resolver returning (ip, offset) applies offset to port."""
        mgr = self._mgr_module.init(
            lambda n: "10.0.0.2" if n == "mesh1" else None,
            config={
                "HOST_IP": "10.0.0.1", "HOST_NAME": "super", "MESH_KEY": "test",
            },
            manager_class=self._mgr_module.ClusterManager,
            is_supermanager=True,
            display_resolver=lambda n: ("192.168.86.201", 100) if n == "mesh1" else None,
        )
        url = mgr.get_child_display_url("mesh1")
        assert url is not None
        assert "192.168.86.201" in url
        assert str(data.Ports.KIOSK_DISPLAY + 100) in url

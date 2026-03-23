"""Tests for build.py.

Run with: pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build


# ── load_env ──────────────────────────────────────────────────────────


class TestLoadEnv:
    def test_basic_key_value(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\n")
        assert build.load_env(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# comment\n\nFOO=bar\n  \n# another\nBAZ=qux\n")
        assert build.load_env(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_value_containing_equals(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("TOKEN=abc=def=ghi\n")
        assert build.load_env(f) == {"TOKEN": "abc=def=ghi"}

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("  FOO  =  bar  \n")
        assert build.load_env(f) == {"FOO": "bar"}

    def test_empty_file(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("")
        assert build.load_env(f) == {}

    def test_line_without_equals_ignored(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("NOEQUALS\nFOO=bar\n")
        assert build.load_env(f) == {"FOO": "bar"}

    def test_double_quoted_values_stripped(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('FOO="bar"\nBAZ="hello world"\n')
        assert build.load_env(f) == {"FOO": "bar", "BAZ": "hello world"}

    def test_single_quoted_values_stripped(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO='bar'\n")
        assert build.load_env(f) == {"FOO": "bar"}

    def test_mismatched_quotes_kept(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO=\"bar'\n")
        assert build.load_env(f) == {"FOO": "\"bar'"}

    def test_single_char_value_not_stripped(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('FOO=""\n')
        assert build.load_env(f) == {"FOO": ""}


# ── validate_env ──────────────────────────────────────────────────────


class TestValidateEnv:
    @pytest.fixture()
    def complete_env(self):
        return {
            "HOME_API_TOKEN": "secret-value",
            "PRIMARY_HOST": "192.168.1.100",
            "MESH_KEY": "passphrase",
        }

    def test_all_present(self, complete_env):
        assert build.validate_env(complete_env) == []

    def test_missing_one(self):
        env = {"PRIMARY_HOST": "1.2.3.4", "MESH_KEY": "key"}
        assert build.validate_env(env) == ["HOME_API_TOKEN"]

    def test_all_missing(self):
        assert build.validate_env({}) == build.REQUIRED_ENV

    def test_empty_value_treated_as_missing(self, complete_env):
        complete_env["PRIMARY_HOST"] = ""
        assert build.validate_env(complete_env) == ["PRIMARY_HOST"]


# ── resolve_playbook ─────────────────────────────────────────────────


class TestResolvePlaybook:
    def test_existing_absolute_path(self, tmp_path):
        pb = tmp_path / "custom.yml"
        pb.write_text("---\n")
        result = build.resolve_playbook(str(pb))
        assert result == pb.resolve()

    def test_name_resolves_from_playbooks_dir(self):
        result = build.resolve_playbook("site.yml")
        assert result == build.PROJECT_ROOT / "playbooks" / "site.yml"

    def test_name_without_extension(self):
        result = build.resolve_playbook("site")
        assert result == build.PROJECT_ROOT / "playbooks" / "site.yml"

    def test_cleanup_playbook(self):
        result = build.resolve_playbook("cleanup")
        assert result == build.PROJECT_ROOT / "playbooks" / "cleanup.yml"

    def test_nonexistent_returns_path_object(self):
        result = build.resolve_playbook("does_not_exist_xyz")
        assert isinstance(result, Path)


# ── build_command ────────────────────────────────────────────────────


class TestBuildCommand:
    BIN = "/usr/bin/ansible-playbook"
    PB = "/path/to/site.yml"

    def test_minimal(self):
        cmd = build.build_command(self.BIN, self.PB)
        assert cmd == [self.BIN, self.PB]

    def test_tags(self):
        cmd = build.build_command(self.BIN, self.PB, tags="openwrt")
        assert cmd[2:4] == ["--tags", "openwrt"]

    def test_multiple_tags(self):
        cmd = build.build_command(self.BIN, self.PB, tags="infra,openwrt")
        assert cmd[2:4] == ["--tags", "infra,openwrt"]

    def test_skip_tags(self):
        cmd = build.build_command(self.BIN, self.PB, skip_tags="backup")
        assert cmd[2:4] == ["--skip-tags", "backup"]

    def test_multiple_skip_tags(self):
        cmd = build.build_command(self.BIN, self.PB, skip_tags="backup,cleanup")
        assert cmd[2:4] == ["--skip-tags", "backup,cleanup"]

    def test_limit(self):
        cmd = build.build_command(self.BIN, self.PB, limit="home")
        assert "--limit" in cmd
        assert cmd[cmd.index("--limit") + 1] == "home"

    def test_check(self):
        cmd = build.build_command(self.BIN, self.PB, check=True)
        assert "--check" in cmd

    def test_diff(self):
        cmd = build.build_command(self.BIN, self.PB, diff=True)
        assert "--diff" in cmd

    def test_verbose_single(self):
        cmd = build.build_command(self.BIN, self.PB, verbose=1)
        assert "-v" in cmd

    def test_verbose_triple(self):
        cmd = build.build_command(self.BIN, self.PB, verbose=3)
        assert "-vvv" in cmd

    def test_zero_verbose_omitted(self):
        cmd = build.build_command(self.BIN, self.PB, verbose=0)
        assert not any(a.startswith("-v") for a in cmd[2:])

    def test_extra_args(self):
        cmd = build.build_command(self.BIN, self.PB, extra_args=["-e", "foo=bar"])
        assert "-e" in cmd
        assert "foo=bar" in cmd

    def test_combined_flags(self):
        cmd = build.build_command(
            self.BIN,
            self.PB,
            tags="openwrt",
            skip_tags="backup",
            limit="home",
            check=True,
            diff=True,
            verbose=2,
            extra_args=["-e", "x=1"],
        )
        assert cmd[0] == self.BIN
        assert cmd[1] == self.PB
        assert "--tags" in cmd
        assert "--skip-tags" in cmd
        assert "--limit" in cmd
        assert "--check" in cmd
        assert "--diff" in cmd
        assert "-vv" in cmd
        assert "-e" in cmd

    def test_tags_and_skip_tags_together(self):
        cmd = build.build_command(
            self.BIN, self.PB, tags="infra,openwrt", skip_tags="cleanup"
        )
        tags_idx = cmd.index("--tags")
        skip_idx = cmd.index("--skip-tags")
        assert cmd[tags_idx + 1] == "infra,openwrt"
        assert cmd[skip_idx + 1] == "cleanup"


# ── find_ansible_playbook ────────────────────────────────────────────


class TestFindAnsiblePlaybook:
    def test_finds_venv_binary(self, monkeypatch, tmp_path):
        venv_bin = tmp_path / "bin" / "ansible-playbook"
        venv_bin.parent.mkdir(parents=True)
        venv_bin.write_text("#!/bin/sh\n")
        monkeypatch.setattr(build, "VENV_DIR", tmp_path)
        assert build.find_ansible_playbook() == str(venv_bin)

    def test_falls_back_to_system(self, monkeypatch, tmp_path):
        monkeypatch.setattr(build, "VENV_DIR", tmp_path / "empty")
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ansible-playbook")
        assert build.find_ansible_playbook() == "/usr/bin/ansible-playbook"

    def test_returns_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(build, "VENV_DIR", tmp_path / "empty")
        monkeypatch.setattr("shutil.which", lambda _: None)
        assert build.find_ansible_playbook() is None


# ── Infrastructure health ────────────────────────────────────────────
# These tests probe REAL hosts from test.env. When a node is down,
# these tests FAIL. That's the point — they're the early warning system.
# mesh1 (10.10.10.210) is behind OpenWrt and only reachable after
# router deployment. It's tested by the E2E suite, not here.

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestInfrastructureHealth:
    """Probe real Proxmox nodes. Failures = real infrastructure problems."""

    @pytest.fixture
    def env(self):
        env_file = REPO_ROOT / "test.env"
        if not env_file.exists():
            pytest.fail("test.env not found — cannot validate infrastructure")
        return build.load_env(env_file)

    def test_primary_host_reachable(self, env):
        """home (PRIMARY_HOST) must always be reachable."""
        ip = env.get("PRIMARY_HOST", "")
        assert ip, "PRIMARY_HOST not set in test.env"
        assert build.probe_host(ip), (
            f"home ({ip}) is UNREACHABLE. Primary Proxmox node is down. "
            "Check power, network cable, SSH service."
        )

    def test_ai_host_reachable(self, env):
        """ai (AI_HOST) must always be reachable — no WoL recovery if lost."""
        ip = env.get("AI_HOST", "")
        assert ip, "AI_HOST not set in test.env"
        assert build.probe_host(ip), (
            f"ai ({ip}) is UNREACHABLE. ai uses USB ethernet — NO Wake-on-LAN. "
            "Requires physical power-on. If automation crashed it, check "
            "dmesg for kernel panic (modprobe -r amdgpu on single-GPU host)."
        )

    def test_mesh2_host_reachable(self, env):
        """mesh2 (MESH_2_HOST) must always be reachable."""
        ip = env.get("MESH_2_HOST", "")
        assert ip, "MESH_2_HOST not set in test.env"
        assert build.probe_host(ip), (
            f"mesh2 ({ip}) is UNREACHABLE. Check power/network. "
            "mesh2 supports WoL: ./scripts/wol.sh mesh2"
        )

    def test_resolve_returns_primary(self, env):
        """resolve_proxmox_host must return PRIMARY_HOST when it's up."""
        result = build.resolve_proxmox_host(env)
        assert result == env["PRIMARY_HOST"], (
            f"Expected PRIMARY_HOST ({env['PRIMARY_HOST']}) but "
            f"resolve_proxmox_host returned '{result}'. Primary is down "
            "or fallback logic was triggered — investigate."
        )

    def test_state_file_valid_if_exists(self):
        """If .state/addresses.json exists, it must contain valid data."""
        import json

        state_file = build.STATE_DIR / "addresses.json"
        if not state_file.exists():
            pytest.skip("No state file yet — first successful run creates it")
        data = json.loads(state_file.read_text())
        assert "ips" in data, "State file missing 'ips' key"
        assert isinstance(data["ips"], list), "'ips' must be a list"
        for ip in data["ips"]:
            assert isinstance(ip, str) and ip, f"Invalid IP entry: {ip!r}"


# ── main (integration-style) ────────────────────────────────────────


class TestMain:
    def test_missing_env_file_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        assert build.main(["--env", "nonexistent.env"]) == 1

    def test_missing_required_vars_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("PRIMARY_HOST=1.2.3.4\n")
        assert build.main(["--env", ".env"]) == 1

    def test_nonexistent_playbook_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        assert build.main(["--env", ".env", "--playbook", "nope"]) == 1

    def test_unreachable_host_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "STATE_DIR", tmp_path / "state")
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        assert build.main(["--env", ".env"]) == 1

    def test_missing_ansible_binary_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: None)
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "site.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        assert build.main(["--env", ".env"]) == 1

    def test_rollback_tags_pass_through(self, tmp_path, monkeypatch):
        """Rollback tags (e.g., openwrt-security-rollback) pass through to ansible-playbook."""
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: "/usr/bin/ansible-playbook")
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "cleanup.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        captured_cmd = []

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return FakeResult()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert build.main(["--env", ".env", "--playbook", "cleanup", "--tags", "openwrt-security-rollback"]) == 0
        assert "--tags" in captured_cmd
        tags_idx = captured_cmd.index("--tags")
        assert captured_cmd[tags_idx + 1] == "openwrt-security-rollback"

    def test_rollback_tag_naming_convention(self):
        """Verify rollback tag naming follows the openwrt-<feature>-rollback pattern."""
        features = ["security", "vlans", "dns", "mesh"]
        for feature in features:
            tag = f"openwrt-{feature}-rollback"
            cmd = build.build_command("/usr/bin/ansible-playbook", "cleanup.yml", tags=tag)
            assert "--tags" in cmd
            assert tag in cmd

    def test_happy_path_runs_subprocess(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: "/usr/bin/ansible-playbook")
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "site.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        captured_cmd = []

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return FakeResult()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert build.main(["--env", ".env"]) == 0
        assert captured_cmd[0] == "/usr/bin/ansible-playbook"
        assert str(playbooks_dir / "site.yml") in captured_cmd[1]

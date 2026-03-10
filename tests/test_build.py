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


# ── validate_env ──────────────────────────────────────────────────────


class TestValidateEnv:
    @pytest.fixture()
    def complete_env(self):
        return {
            "PROXMOX_API_TOKEN_SECRET": "secret-value",
            "PROXMOX_HOST": "192.168.1.100",
            "MESH_KEY": "passphrase",
        }

    def test_all_present(self, complete_env):
        assert build.validate_env(complete_env) == []

    def test_missing_one(self):
        env = {"PROXMOX_HOST": "1.2.3.4", "MESH_KEY": "key"}
        assert build.validate_env(env) == ["PROXMOX_API_TOKEN_SECRET"]

    def test_all_missing(self):
        assert build.validate_env({}) == build.REQUIRED_ENV

    def test_empty_value_treated_as_missing(self, complete_env):
        complete_env["PROXMOX_HOST"] = ""
        assert build.validate_env(complete_env) == ["PROXMOX_HOST"]


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


# ── main (integration-style) ────────────────────────────────────────


class TestMain:
    def test_missing_env_file_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        assert build.main(["--env", "nonexistent.env"]) == 1

    def test_missing_required_vars_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("PROXMOX_HOST=1.2.3.4\n")
        assert build.main(["--env", ".env"]) == 1

    def test_nonexistent_playbook_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PROXMOX_API_TOKEN_SECRET=x\nPROXMOX_HOST=1.2.3.4\nMESH_KEY=k\n"
        )
        assert build.main(["--env", ".env", "--playbook", "nope"]) == 1

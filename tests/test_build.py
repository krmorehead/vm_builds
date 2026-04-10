"""Tests for build.py.

Run with: pytest tests/ -v
"""

from pathlib import Path

import pytest

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


# ── warn_multi_host ──────────────────────────────────────────────────


class TestWarnMultiHost:
    """Validate optional multi-host env variable warnings."""

    def test_no_warnings_on_valid_env(self):
        env = {
            "AI_HOST": "192.168.86.220",
            "MESH_2_HOST": "192.168.86.211",
            "HOME_API_TOKEN": "abc-123",
            "MESH1_API_TOKEN": "def-456",
        }
        assert build.warn_multi_host(env) == []

    def test_warns_on_malformed_ip(self):
        env = {"AI_HOST": "not-an-ip"}
        warnings = build.warn_multi_host(env)
        assert len(warnings) == 1
        assert "AI_HOST" in warnings[0]

    def test_warns_on_empty_token(self):
        env = {"HOME_API_TOKEN": ""}
        warnings = build.warn_multi_host(env)
        assert len(warnings) == 1
        assert "HOME_API_TOKEN" in warnings[0]
        assert "empty" in warnings[0]

    def test_no_warning_on_absent_optional_vars(self):
        warnings = build.warn_multi_host({})
        assert warnings == []

    def test_no_false_positive_on_absent_token(self):
        env = {"PRIMARY_HOST": "192.168.1.1"}
        warnings = build.warn_multi_host(env)
        assert warnings == []


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
        # WHY: Redirects venv lookup to a controlled directory so we can test binary detection.
        # HOW: Tests the actual find_ansible_playbook logic against real filesystem operations.
        monkeypatch.setattr(build, "VENV_DIR", tmp_path)
        assert build.find_ansible_playbook() == str(venv_bin)

    def test_falls_back_to_system(self, monkeypatch, tmp_path):
        # WHY: Tests the PATH fallback when venv is empty; shutil.which result varies by environment.
        # HOW: Tests the actual branching logic in find_ansible_playbook (venv → PATH).
        monkeypatch.setattr(build, "VENV_DIR", tmp_path / "empty")
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ansible-playbook")
        assert build.find_ansible_playbook() == "/usr/bin/ansible-playbook"

    def test_returns_none_when_missing(self, monkeypatch, tmp_path):
        # WHY: Tests the "binary not found anywhere" path; requires both sources to report missing.
        # HOW: Tests the None return that triggers build.py's "ansible not found" error.
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

    def test_bridge1_host_reachable(self, env):
        """bridge-1 (BRIDGE_1_HOST) must always be reachable."""
        ip = env.get("BRIDGE_1_HOST", "")
        assert ip, "BRIDGE_1_HOST not set in test.env"
        assert build.probe_host(ip), (
            f"bridge-1 ({ip}) is UNREACHABLE. Check power/network. "
            "bridge-1 supports WoL: ./scripts/wol.sh bridge-1"
        )

    def test_bridge2_host_reachable(self, env):
        """bridge-2 (BRIDGE_2_HOST) must always be reachable."""
        ip = env.get("BRIDGE_2_HOST", "")
        assert ip, "BRIDGE_2_HOST not set in test.env"
        assert build.probe_host(ip), (
            f"bridge-2 ({ip}) is UNREACHABLE. Check power/network. "
            "bridge-2 supports WoL: ./scripts/wol.sh bridge-2"
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
        """If .state/addresses.json exists, it must contain valid data.

        The state file is created during successful Ansible runs. When it
        doesn't exist, the test passes — that just means no run has
        completed yet. When it does exist, validate the schema.
        """
        import json

        state_file = build.STATE_DIR / "addresses.json"
        if not state_file.exists():
            return
        data = json.loads(state_file.read_text())
        assert "ips" in data, "State file missing 'ips' key"
        assert isinstance(data["ips"], list), "'ips' must be a list"
        for ip in data["ips"]:
            assert isinstance(ip, str) and ip, f"Invalid IP entry: {ip!r}"


# ── resolve_proxmox_host fallback ────────────────────────────────────


class TestResolveProxmoxHostFallback:
    """Fallback logic uses the state file when PRIMARY_HOST is down.

    WHY probe_host is mocked here: These tests use synthetic IPs (10.0.0.x)
    to test the DECISION LOGIC of resolve_proxmox_host — which IP to try,
    when to fall back, how to handle corrupt state. Cannot selectively make
    real hosts unreachable to exercise these code paths.
    HOW: Tests the actual resolve_proxmox_host function's branching logic
    (primary → state file → empty) with controlled probe results.
    """

    def test_returns_primary_when_reachable(self, monkeypatch):
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        env = {"PRIMARY_HOST": "10.0.0.1"}
        assert build.resolve_proxmox_host(env) == "10.0.0.1"

    def test_falls_back_to_cached_ip(self, tmp_path, monkeypatch):
        import json

        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        state_file = state_dir / "addresses.json"
        state_file.write_text(json.dumps({"ips": ["10.0.0.1", "10.0.0.2"]}))
        monkeypatch.setattr(build, "STATE_DIR", state_dir)

        call_count = {"n": 0}
        def fake_probe(ip, **kw):
            call_count["n"] += 1
            return ip == "10.0.0.2"

        monkeypatch.setattr(build, "probe_host", fake_probe)
        env = {"PRIMARY_HOST": "10.0.0.1"}
        assert build.resolve_proxmox_host(env) == "10.0.0.2"

    def test_returns_empty_when_all_unreachable(self, tmp_path, monkeypatch):
        import json

        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        state_file = state_dir / "addresses.json"
        state_file.write_text(json.dumps({"ips": ["10.0.0.1", "10.0.0.2"]}))
        monkeypatch.setattr(build, "STATE_DIR", state_dir)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: False)
        env = {"PRIMARY_HOST": "10.0.0.1"}
        assert build.resolve_proxmox_host(env) == ""

    def test_skips_primary_in_fallback_list(self, tmp_path, monkeypatch):
        """If primary appears in state file, don't re-probe it."""
        import json

        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        state_file = state_dir / "addresses.json"
        state_file.write_text(json.dumps({"ips": ["10.0.0.1", "10.0.0.3"]}))
        monkeypatch.setattr(build, "STATE_DIR", state_dir)

        probed = []
        def tracking_probe(ip, **kw):
            probed.append(ip)
            return ip == "10.0.0.3"

        monkeypatch.setattr(build, "probe_host", tracking_probe)
        env = {"PRIMARY_HOST": "10.0.0.1"}
        result = build.resolve_proxmox_host(env)
        assert result == "10.0.0.3"
        assert probed.count("10.0.0.1") == 1

    def test_handles_corrupt_state_file(self, tmp_path, monkeypatch):
        """Corrupt state file doesn't crash resolve_proxmox_host."""
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        (state_dir / "addresses.json").write_text("{invalid json")
        monkeypatch.setattr(build, "STATE_DIR", state_dir)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: False)
        env = {"PRIMARY_HOST": "10.0.0.1"}
        assert build.resolve_proxmox_host(env) == ""


# ── main (integration-style) ────────────────────────────────────────


class TestMain:
    """Tests for build.main() CLI flow.

    WHY PROJECT_ROOT/probe_host/subprocess.run are mocked: main() orchestrates
    the full build pipeline — env parsing, host probing, ansible-playbook launch.
    Running it unpatched would execute real ansible-playbook against real infra.
    HOW: Tests the CLI validation logic (missing env, missing vars, missing
    playbook, unreachable host, missing binary, tag passthrough) in isolation.
    """

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
        monkeypatch.setattr(build, "start_api_server", lambda *a, **kw: None)
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


# ── API lifecycle ─────────────────────────────────────────────────────


class TestGetControllerIp:
    def test_returns_ip_string(self):
        ip = build.get_controller_ip()
        assert isinstance(ip, str)
        assert len(ip) > 0

    def test_fallback_on_error(self, monkeypatch):
        # WHY: Cannot reliably break the real socket stack to test the fallback.
        # HOW: Tests that get_controller_ip() returns 127.0.0.1 when socket creation fails.
        def broken_socket(*a, **kw):
            raise OSError("no route")

        monkeypatch.setattr("socket.socket", broken_socket)
        assert build.get_controller_ip() == "127.0.0.1"


class TestStopApiServer:
    def test_noop_for_none(self):
        build.stop_api_server(None)

    def test_noop_for_exited_process(self):
        class FakeProc:
            def poll(self):
                return 0
        build.stop_api_server(FakeProc())

    def test_terminates_running_process(self):
        terminated = []
        waited = []

        class FakeProc:
            def poll(self):
                return None

            def terminate(self):
                terminated.append(True)

            def wait(self, timeout=None):
                waited.append(timeout)

        proc = FakeProc()
        build.stop_api_server(proc)
        assert len(terminated) == 1
        assert len(waited) == 1

    def test_kills_on_timeout(self):
        import subprocess as _sp
        killed = []

        class FakeProc:
            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                if not killed:
                    raise _sp.TimeoutExpired("cmd", timeout)

            def kill(self):
                killed.append(True)

        proc = FakeProc()
        build.stop_api_server(proc)
        assert len(killed) == 1


class TestStartApiServer:
    """Tests for API server lifecycle.

    WHY subprocess.Popen is mocked: start_api_server spawns a real Python
    subprocess running app.py. Launching a real server during pytest would
    bind ports and leave orphaned processes.
    HOW: Tests the return-value logic (early exit → None, running → process object).
    """

    def test_returns_none_on_early_exit(self, tmp_path, monkeypatch):
        env_file = tmp_path / "test.env"
        env_file.write_text("CALLHOME_SERVER=http://test\n")

        class FakeProc:
            def poll(self):
                return 1
            returncode = 1

        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *a, **kw: FakeProc(),
        )
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        (tmp_path / ".state").mkdir()

        result = build.start_api_server(env_file)
        assert result is None

    def test_returns_proc_on_success(self, tmp_path, monkeypatch):
        env_file = tmp_path / "test.env"
        env_file.write_text("CALLHOME_SERVER=http://test\n")

        class FakeProc:
            def poll(self):
                return None

        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *a, **kw: FakeProc(),
        )
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        (tmp_path / ".state").mkdir()

        def fake_connect(addr, timeout=None):
            class FakeConn:
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
            return FakeConn()

        monkeypatch.setattr("socket.create_connection", fake_connect)
        monkeypatch.setattr("time.sleep", lambda x: None)

        result = build.start_api_server(env_file)
        assert result is not None


class TestNoApiFlag:
    """Tests for the --no-api CLI flag.

    WHY: Same as TestMain — prevents launching real ansible-playbook and API server.
    HOW: Tests that --no-api correctly skips start_api_server while default starts it.
    """

    def test_no_api_skips_server(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: "/usr/bin/ansible-playbook")
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "site.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text("HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n")

        start_called = []
        monkeypatch.setattr(
            build, "start_api_server",
            lambda *a, **kw: start_called.append(1) or None,
        )

        class FakeResult:
            returncode = 0
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())

        build.main(["--env", ".env", "--no-api"])
        assert len(start_called) == 0

    def test_default_starts_api(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: "/usr/bin/ansible-playbook")
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "site.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text("HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n")

        start_called = []
        monkeypatch.setattr(
            build, "start_api_server",
            lambda *a, **kw: start_called.append(1) or None,
        )

        class FakeResult:
            returncode = 0
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())

        build.main(["--env", ".env"])
        assert len(start_called) == 1

    def test_callhome_server_state_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
        monkeypatch.setattr(build, "find_ansible_playbook", lambda: "/usr/bin/ansible-playbook")
        playbooks_dir = tmp_path / "playbooks"
        playbooks_dir.mkdir()
        (playbooks_dir / "site.yml").write_text("---\n")
        env_file = tmp_path / ".env"
        env_file.write_text("HOME_API_TOKEN=x\nPRIMARY_HOST=1.2.3.4\nMESH_KEY=k\n")

        class FakeProc:
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                pass

        monkeypatch.setattr(
            build, "start_api_server",
            lambda *a, **kw: FakeProc(),
        )

        class FakeResult:
            returncode = 0

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())
        build.main(["--env", ".env"])

        state_file = tmp_path / ".state" / "callhome_url"
        assert state_file.exists()
        assert state_file.read_text().startswith("http://")

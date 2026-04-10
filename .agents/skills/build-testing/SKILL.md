---
name: build-testing
description: Test coverage for build.py functions. Real infrastructure probes, justified mocks only.
---

# Build Testing

## Rules

1. Every public function in build.py MUST have a test class in tests/test_build.py.
2. Every error path MUST have a test: missing file, unreachable host, invalid env.
3. NEVER mock `probe_host` to test infrastructure health. Probe the REAL hosts.
4. Mocking `subprocess.run` is justified — don't actually run `ansible-playbook` from pytest.
5. Mocking `probe_host` is ONLY justified in `TestMain` error-path tests where you need to
   isolate a DIFFERENT error (e.g., "missing playbook" needs to get past the probe step).
6. NEVER write a test that would pass identically if the infrastructure were offline
   (unless it's testing pure Python logic like string parsing).

## Coverage matrix

| Function | Test Class | What it tests |
|---|---|---|
| `load_env` | `TestLoadEnv` | Real Python parsing: quotes, whitespace, comments, edge cases |
| `validate_env` | `TestValidateEnv` | Real Python validation: required vars, empty values |
| `resolve_playbook` | `TestResolvePlaybook` | Real filesystem: actual playbooks/ dir, path resolution |
| `build_command` | `TestBuildCommand` | Real Python list construction: flags, tags, combined |
| `find_ansible_playbook` | `TestFindAnsiblePlaybook` | Filesystem + shutil.which: venv, system, missing |
| `probe_host` | `TestInfrastructureHealth` | REAL TCP probes against REAL hosts from test.env |
| `resolve_proxmox_host` | `TestInfrastructureHealth` | REAL resolution against REAL PRIMARY_HOST |
| `main` | `TestMain` | Error paths (mock subprocess.run to avoid running ansible) |

## Running tests

```bash
pytest tests/test_build.py -v
```

## When tests fail

`TestInfrastructureHealth` failures mean REAL infrastructure problems:
- `test_primary_host_reachable` FAILED → home is down. Check power/network.
- `test_ai_host_reachable` FAILED → ai is down. NO WoL. Manual power-on required.
- `test_mesh2_host_reachable` FAILED → mesh2 is down. Try `./scripts/wol.sh mesh2`.

These are NOT flaky tests. They are the early warning system. If they fail,
your infrastructure has a real problem.

## Justified mock patterns

Every `patch()` or `monkeypatch` call MUST include a comment with TWO parts:
1. WHY this mock is necessary (what side effect it prevents)
2. HOW the test still genuinely validates the feature despite the mock

If you cannot write both sentences, the mock is unjustified — remove it and
test the real thing.

```python
# JUSTIFIED: prevent pytest from running ansible-playbook (side effect: 5-min
# playbook run). Test validates command construction, not playbook execution.
monkeypatch.setattr("subprocess.run", fake_run)

# JUSTIFIED: test "missing binary" error path (side effect: none, but real
# binary is always present). Test validates the error message, not binary detection.
monkeypatch.setattr(build, "find_ansible_playbook", lambda: None)

# JUSTIFIED in TestMain ONLY: isolate a different error path
# (side effect: TCP probe blocks for 3s per host). Test validates "missing
# playbook" error, not host reachability — that's tested by TestInfrastructureHealth.
monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
```

## NEVER-justified mock patterns

```python
# NEVER: mocking probe_host to test infrastructure health
monkeypatch.setattr(build, "probe_host", lambda *a, **kw: True)
env = {"PRIMARY_HOST": "10.0.0.1"}  # FAKE IP!
assert build.resolve_proxmox_host(env) == "10.0.0.1"  # TRIVIALLY OBVIOUS

# NEVER: mocking socket to test probe_host itself
@patch('socket.create_connection')
def test_probe(mock_conn):  # TESTS NOTHING REAL
```

## Previous bug

`TestResolveProxmoxHost` (5 tests) monkeypatched `probe_host` with fake IPs.
All 5 passed while all 3 WAN hosts (home, ai, mesh2) were offline. The ai
host had been crashed by `modprobe -r amdgpu` and nobody knew because the
"host probing tests" never actually probed any hosts. Replaced with
`TestInfrastructureHealth` that probes real hosts from test.env.

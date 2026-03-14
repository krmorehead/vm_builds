---
name: build-testing
description: Test coverage for build.py functions. Every function needs test class, error paths covered.
---

# Build Testing

## Rules

1. Every public function in build.py MUST have test class in tests/test_build.py.
2. Every error path MUST have test: missing file, unreachable host, invalid env, corrupt state.

## Coverage matrix

| Function | Test Class | Coverage |
|---|---|---|
| `load_env` | `TestLoadEnv` | Basic, comments, equals-in-value, whitespace, empty, quoted |
| `validate_env` | `TestValidateEnv` | All present, missing one, all missing, empty |
| `resolve_playbook` | `TestResolvePlaybook` | Absolute path, name, name without ext, nonexistent |
| `build_command` | `TestBuildCommand` | Each flag, combined flags |
| `find_ansible_playbook` | `TestFindAnsiblePlaybook` | Venv found, system fallback, returns None |
| `resolve_proxmox_host` | `TestResolveProxmoxHost` | Primary reachable, fallback, all unreachable, corrupt |
| `main` | `TestMain` | Error paths + happy path |

## Running tests

```bash
pytest tests/test_build.py -v
pytest tests/test_build.py --cov=build --cov-report=term-missing
```

## Coverage requirements

- Line coverage: 90%+
- Branch coverage: 85%+

## Mock patterns

```python
from unittest.mock import patch, MagicMock

@patch('subprocess.run')
def test_main(mock_run):
    mock_run.return_value.returncode = 0
    main(['site.yml'])

@patch('socket.create_connection')
def test_probe(mock_conn):
    mock_conn.return_value.__enter__ = MagicMock()
    mock_conn.return_value.__exit__ = MagicMock()
    assert probe_host("10.0.0.1", 22)
```

## Common failures

- Branch not covered → add error path test
- Mock not called → check patch path
- Test flaky → mock all dependencies
- Coverage false positive → remove or test commented code

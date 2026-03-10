---
name: build-conventions
description: Python code conventions for build.py and supporting scripts. Use when modifying build.py, run.sh, cleanup.sh, test_build.py, adding CLI arguments, changing env variable handling, or adding new entry points.
---

# Build Entry Point Conventions

## Context

`build.py` is the single orchestration entry point. It handles env validation, host probing with fallback, playbook resolution, and subprocess execution. Shell wrappers (`run.sh`, `cleanup.sh`) exist for convenience but MUST delegate to `build.py`. Bypassing it loses host auto-detection, env validation, and quote-safe parsing — all of which caused real production issues.

## Rules

1. ALL shell scripts (`run.sh`, `cleanup.sh`) MUST delegate to `build.py`. NEVER call `ansible-playbook` directly from a shell script. Previous bug: `run.sh` bypassed `build.py`, so host probing and state file fallback didn't run. After cable swaps the script silently connected to the wrong host or failed.
2. Functions NEVER call `sys.exit()`. Return `None`, an error code, or raise an exception. Let `main()` handle all process exits. Previous bug: `find_ansible_playbook()` called `sys.exit(1)` — impossible to test and breaks function composition.
3. `.env` parsing MUST strip surrounding quotes from values. Users write `FOO="bar"` and `FOO='bar'` interchangeably. Previous bug: quoted values passed literal `"192.168.1.100"` to SSH, which silently failed.
4. Every public function in `build.py` MUST have a corresponding test class in `tests/test_build.py`. Every error path (missing file, unreachable host, missing binary) MUST have a test.
5. Optional env variables (e.g., `WAN_MAC`) are handled in Ansible role defaults via `lookup('env', ...) | default('', true)`. NEVER add optional variables to `REQUIRED_ENV` in `build.py`.
6. When adding a new CLI argument to `build.py`, ALWAYS add a corresponding test in `TestBuildCommand` verifying the flag appears in the command list.
7. `build.py` MUST probe the Proxmox host before running Ansible. If unreachable, it MUST try cached IPs from `.state/addresses.json`. If all fail, exit with a clear error — NEVER pass an unreachable host to `ansible-playbook`.

## Patterns

### Shell scripts delegate to build.py

```bash
# BAD — bypasses host probing, env validation, quote handling
source .venv/bin/activate
set -a; source .env; set +a
ansible-playbook playbooks/site.yml "$@"

# GOOD — all logic lives in build.py
source .venv/bin/activate
set -a; source .env; set +a
python3 build.py "$@"
```

### Functions return errors, never exit

```python
# BAD — untestable, kills the process
def find_ansible_playbook() -> str:
    ...
    print("ERROR: not found", file=sys.stderr)
    sys.exit(1)

# GOOD — caller decides what to do
def find_ansible_playbook() -> str | None:
    ...
    return None
```

### Env parsing handles quotes

```python
# BAD — FOO="bar" stores literal quotes
env[key.strip()] = value.strip()

# GOOD — strips matched surrounding quotes
value = value.strip()
if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
    value = value[1:-1]
env[key.strip()] = value
```

## Test coverage requirements

Every test class maps to a function:

| Function | Test Class | Minimum coverage |
|---|---|---|
| `load_env` | `TestLoadEnv` | Basic, comments, equals-in-value, whitespace, empty, quoted values |
| `validate_env` | `TestValidateEnv` | All present, missing one, all missing, empty value |
| `resolve_playbook` | `TestResolvePlaybook` | Absolute path, name, name without ext, nonexistent |
| `build_command` | `TestBuildCommand` | Each flag individually, combined flags |
| `find_ansible_playbook` | `TestFindAnsiblePlaybook` | Venv found, system fallback, returns None |
| `resolve_proxmox_host` | `TestResolveProxmoxHost` | Primary reachable, fallback, all unreachable, corrupt state |
| `main` | `TestMain` | Each error path + happy path with subprocess mock |

---
name: build-entry-point
description: Build.py orchestration patterns. Shell scripts MUST delegate, host probing with fallback, env validation.
---

# Build Entry Point

## Rules

1. ALL shell scripts MUST delegate to build.py. NEVER call ansible-playbook directly.
2. build.py MUST probe host before running Ansible. Try fallback IPs from .state/addresses.json.
3. NEVER add optional env vars to REQUIRED_ENV. Handle in Ansible role defaults.
4. When adding CLI argument, add test in TestBuildCommand.

## Patterns

### Shell delegation

```bash
source .venv/bin/activate
set -a; source .env; set +a
python3 build.py "$@"
```

### Host probing

```python
def resolve_proxmox_host() -> str:
    for ip in get_candidate_hosts():
        if probe_host(ip):
            return ip
    raise RuntimeError("No reachable Proxmox host found")
```

### Playbook resolution

```python
def resolve_playbook(playbook: str) -> Path:
    if (p := Path(playbook)).exists():
        return p
    if (p := Path(f"playbooks/{playbook}")).exists():
        return p
    if (p := Path(f"playbooks/{playbook}.yml")).exists():
        return p
    raise FileNotFoundError(f"Playbook not found: {playbook}")
```

### Optional env vars

```python
# build.py
REQUIRED_ENV = ['PRIMARY_HOST', 'MESH_KEY']

# Ansible role defaults
wan_mac: "{{ lookup('env', 'WAN_MAC') | default('', true) }}"
```

### Command building

```python
def build_command(playbook: Path, extra_vars: dict, tags: list, skip_tags: list) -> list:
    cmd = ['ansible-playbook', str(playbook)]
    if extra_vars:
        cmd.extend(['--extra-vars', format_extra_vars(extra_vars)])
    if tags:
        cmd.extend(['--tags', ','.join(tags)])
    if skip_tags:
        cmd.extend(['--skip-tags', ','.join(skip_tags)])
    return cmd
```

## Previous bugs

- Shell script bypassed build.py → wrong host after cable swap
- Optional vars marked required → blocked valid runs
- Missing fallback host → silent failures
- Wrong host by default → no probing
- Quoted values: `.env` had `PRIMARY_HOST="192.168.1.100"` with literal quotes. SSH connected to `"192.168.1.100"` (with quotes) and hung. Fix: `build.py` strips surrounding quotes during env parsing.
- `sys.exit()` in library function: `find_ansible_playbook()` called `sys.exit(1)` on error instead of returning None or raising. This made the function untestable and killed the process in unexpected places.
- When adding a new CLI argument, ALWAYS add a corresponding test in `TestBuildCommand`.

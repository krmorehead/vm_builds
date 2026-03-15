---
name: secret-generation
description: Auto-generation and persistence patterns for secrets, keys, and dynamic configuration. Includes env file patterns, blockinfile usage, and variable scoping.
---

# Secret and Dynamic Config Generation

Use when generating secrets/keys during Ansible runs, managing dynamic configuration, or implementing per-run generated values with persistence.

## Rules

1. ALWAYS use `env_generated_path` - NEVER hardcode filename
2. ALWAYS use `delegate_to: localhost` when writing generated files
3. ALWAYS use `create: true` - file may not exist on first run
4. ALWAYS use `mode: "0600"` - file contains private keys
5. NEVER use `copy` or `template` - they overwrite entire file
6. ALWAYS provide sensible defaults when reading generated values
7. NEVER fail if generated file is missing - degrade to defaults
8. NEVER put dynamic/computed values in group_vars/all.yml as constants

## Patterns

Writing generated values:

```yaml
- name: Write WireGuard key to generated env file
  ansible.builtin.blockinfile:
    path: "{{ env_generated_path }}"
    create: true
    mode: "0600"
    marker: "# {mark} WireGuard Keys (auto-generated)"
    block: |
      WG_PRIVATE_KEY={{ wg_private_key.stdout }}
      WG_PUBLIC_KEY={{ wg_public_key.stdout }}
  delegate_to: localhost
```

Reading generated values:

```yaml
- name: Read WireGuard private key from generated env
  ansible.builtin.set_fact:
    _wg_private_key: >-
      {{ lookup('pipe', 'grep "^WG_PRIVATE_KEY=" ' + env_generated_path + ' 2>/dev/null | cut -d= -f2')
         | default('', true) }}
```

Path resolution:

```yaml
# In group_vars/all.yml
env_generated_path: >-
  {{ project_root }}/{{
     'test.env.generated'
     if lookup('env', 'MOLECULE_PROJECT_DIRECTORY') | length > 0
     else '.env.generated' }}
```

Variable scoping table:

```yaml
# Static constants (group_vars/all.yml)
openwrt_vm_id: 100

# Operator secrets (.env/test.env)
HOME_API_TOKEN: "secret"

# Auto-generated secrets (env_generated_path)
WG_PRIVATE_KEY: "generated"

# Dynamic runtime config (env_generated_path)  
LAN_GATEWAY: "10.10.10.1"

# Per-host overrides (host_vars/<host>.yml)
ansible_host: "192.168.86.201"
```

## Anti-patterns

NEVER explain what secrets are in secret generation rules
NEVER write the same section from two different roles
NEVER use bare relative paths - they break with molecule scenarios
NEVER hardcode test.env.generated vs .env.generated decisions
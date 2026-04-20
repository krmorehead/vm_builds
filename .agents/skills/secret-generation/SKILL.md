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

**Recent Variable Management Lessons:**
9. ALWAYS provide default values for hardware-dependent variables (igpu_render_device: "/dev/dri/renderD128")
10. ALWAYS use `| default('value', true)` for environment lookups to prevent Jinja2 template failures
11. ALWAYS test with missing generated env files - test scenarios don't always have complete environment setup
12. ALWAYS validate network configuration computation works with default gateway values

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

## Verify requirement

Every section written to the generated env file SHOULD have a corresponding assertion in `molecule/default/verify.yml`. This ensures the generated values are actually produced during converge and are accessible in subsequent plays.

## Controller VPN key flow

The controller (build/test machine) gets its own WireGuard keypair for the
VPN tunnel to the hub. The key flow spans two phases:

1. **Key generation** (`prepare.yml` or first converge): Controller private
   key generated with `wg genkey`, public key derived with `wg pubkey`.
   Both written to `env_generated_path` under the `Controller WireGuard Keys`
   marker block: `WIREGUARD_PRIVATE_KEY_CONTROLLER` and
   `WIREGUARD_PUBLIC_KEY_CONTROLLER`.

2. **Hub pubkey availability** (`wireguard_configure` play): The hub
   (home WireGuard container) generates its keypair and writes
   `WIREGUARD_HUB_PUBLIC_KEY` to `env_generated_path`. The controller VPN
   play reads this via `lookup('env', 'WIREGUARD_HUB_PUBLIC_KEY')`.

3. **Controller VPN setup** (site.yml, after wireguard_configure): Reads
   both keys from env, templates `/etc/wireguard/wg0.conf`, and brings up
   `wg0`. Skips gracefully if either key is empty (first run before
   wireguard_configure has produced the hub key).

4. **Endpoint resolution**: `WIREGUARD_SERVER_ENDPOINT` is computed from
   the hub's reachable IP + port 51820. Written to `env_generated_path` by
   the WireGuard configure role.

The build machine VPN play uses explicit `sudo` (not `become: true`).
The NOPASSWD sudoers entry for `wg-quick`, `wg`, and `install` is already
installed by `setup.sh`. This is a solved problem — no manual steps needed.

## Anti-patterns

NEVER explain what secrets are in secret generation rules
NEVER write the same section from two different roles
NEVER use bare relative paths - they break with molecule scenarios
NEVER hardcode test.env.generated vs .env.generated decisions
NEVER use `become: true` on localhost — use explicit `sudo`. NOPASSWD sudoers is already installed by setup.sh
# Variable Management Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the inventory/ directory. These rules focus on variable scoping, secret management, and group/host variable patterns.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., .agents/skills/secret-generation/SKILL.md), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Secret Management:**
- @.agents/skills/secret-generation - Auto-generation patterns
- @.agents/skills/proxmox-ssh-safety - SSH credential safety
- @.agents/skills/proxmox-cleanup-safety - Cleanup credential protection

**Variable Patterns:**
- @.agents/skills/project-structure-rules - Variable scoping conventions
- @.agents/skills/openwrt-network-topology - Network variable patterns
- @.agents/skills/secret-generation - Dynamic config generation

## Development Guidelines

For secret and dynamic config generation: .agents/skills/secret-generation/SKILL.md

## Cross-Coverage Rules

### From Other Directories
- **Secret generation**: Reference @.agents/skills/secret-generation for auto-generation patterns
- **SSH safety**: Use @.agents/skills/proxmox-ssh-safety for connection management
- **Network topology**: Apply @.agents/skills/openwrt-network-topology for bridge detection
- **Cleanup safety**: Reference @.agents/skills/proxmox-cleanup-safety for credential protection
- **Variable patterns**: Use @.agents/skills/project-structure-rules for scoping conventions

## Variable Scoping Rules

| Value type | Where to define | Example |
|---|---|---|
| Static constants | `group_vars/all.yml` | VMIDs, image paths, storage pool |
| Operator secrets | `.env` / `test.env` | API tokens, SSH keys, WAN_MAC |
| Auto-generated secrets | `env_generated_path` | WireGuard private/public keys |
| Dynamic runtime config | `env_generated_path` | LAN_GATEWAY, LAN_CIDR |
| Per-host overrides | `host_vars/<host>.yml` | ansible_host, reboot policy |

## Group Variables Structure

### group_vars/all.yml
Shared VM parameters and static constants:
- VMIDs by service type
- Image paths and storage configurations
- `project_root` and `env_generated_path` definitions
- Container IP offsets by service

**NEVER** put dynamic/computed values here as constants. If a value is determined at runtime, it belongs in the generated file.

### group_vars/proxmox.yml
Proxmox connection and authentication:
- API authentication settings
- SSH connection settings
- Required secrets: `.env` file → `lookup('env', 'VAR_NAME')`
- API tokens: `<HOSTNAME>_API_TOKEN` resolved dynamically via `(inventory_hostname | upper | replace('-', '_')) + '_API_TOKEN'`

### group_vars/lan_hosts.yml
LAN host SSH configuration:
- ProxyJump through primary host
- SSH connection parameters for hosts behind OpenWrt router

## Host Variables Structure

Per-host configuration in `host_vars/<hostname>.yml`:
- `ansible_host` - IP address for connection
- Reboot policies and host-specific settings
- Network topology overrides

Current hosts:
- `home.yml` - Primary router node
- `mesh1.yml` - LAN satellite (behind home's OpenWrt)
- `ai.yml` - AI computing node
- `mesh2.yml` - Additional mesh node

## Environment Variables

### Required Environment Variables
- `PRIMARY_HOST` - Entry point IP that `build.py` probes
- `AI_HOST`, `MESH_2_HOST` - Additional directly reachable nodes
- `<HOSTNAME>_API_TOKEN` - Proxmox API tokens for each host

### Optional Environment Variables  
- `WAN_MAC` - Cloned MAC address for router replacement
- Defined in `.env` file → `lookup('env', 'VAR_NAME') | default('', true)` in role `defaults/main.yml`

## Generated Env File Pattern

Auto-generated secrets and dynamic configuration are written to a generated env file on the controller during playbook execution.

### File Naming
- **Test runs (Molecule):** `test.env.generated`
- **Production runs:** `.env.generated`
- Both are gitignored

### Path Resolution
`env_generated_path` in `group_vars/all.yml` resolves automatically:

```yaml
env_generated_path: >-
  {{ project_root }}/{{
     'test.env.generated'
     if lookup('env', 'MOLECULE_PROJECT_DIRECTORY') | length > 0
     else '.env.generated' }}
```

**ALWAYS** use `env_generated_path` — **NEVER** hardcode the filename.

## Writing Generated Values

Use `ansible.builtin.blockinfile` with a descriptive marker per section:

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

### Rules for Writing
- **ALWAYS** use `delegate_to: localhost` — the file lives on the controller
- **ALWAYS** use `create: true` — file may not exist on first run
- **ALWAYS** use `mode: "0600"` — file contains private keys
- **ALWAYS** use a unique `marker` per section (role name + description)
- **NEVER** write the same section from two different roles
- **NEVER** use `copy` or `template` — they overwrite the entire file

## Reading Generated Values

Use `lookup('pipe', 'grep ...')` with a fallback default:

```yaml
- name: Read WireGuard private key from generated env
  ansible.builtin.set_fact:
    _wg_private_key: >-
      {{ lookup('pipe', 'grep "^WG_PRIVATE_KEY=" ' + env_generated_path + ' 2>/dev/null | cut -d= -f2')
         | default('', true) }}
```

### Rules for Reading
- **ALWAYS** provide a sensible default. The generated file may not exist on a clean first run
- **NEVER** fail if the generated file is missing — the role should degrade to defaults, not crash
- **NEVER** put dynamic/computed values in `group_vars/all.yml` as constants

## Device Flavors (Inventory Groups)

Hosts belong to child groups under `proxmox` that determine which services they receive:

- `router_nodes` — OpenWrt router VM (home only)
- `vpn_nodes` — WireGuard VPN (home, mesh1, ai, mesh2 — all 4 nodes)
- `dns_nodes` — Pi-hole
- `wifi_nodes` — Mesh WiFi Controller
- `monitoring_nodes` — Netdata, rsyslog
- `service_nodes` — Home Assistant
- `media_nodes` — Jellyfin, Kodi, Moonlight
- `desktop_nodes` — Desktop VM, UX Kiosk
- `gaming_nodes` — Gaming VM
- `lan_hosts` — Satellite Proxmox nodes behind OpenWrt router

A host can belong to multiple flavor groups.

## VMID Allocation

**100-199**: Network (100 OpenWrt, 101 WireGuard, 102 Pi-hole, 103 Mesh WiFi)
**200-299**: Services (200 Home Assistant)
**300-399**: Media (300 Jellyfin, 301 Kodi, 302 Moonlight)
**400-499**: Desktop (400 Desktop VM, 401 Kiosk)
**500-599**: Observability (500 Netdata, 501 rsyslog)
**600-699**: Gaming (600 Gaming VM)
**999**: reserved for molecule test containers

All VMIDs defined in `group_vars/all.yml`.

## Previous Bugs Learned

**Bug**: `lan_gateway` and `lan_cidr` were hardcoded in `group_vars/all.yml` as `"10.10.10.1"` and `"24"`. The actual LAN subnet is auto-detected by `openwrt_configure` and can vary if the default collides with the WAN prefix. Hardcoded values broke when auto-detection picked a different subnet.

**Fix**: Dynamic values go in `env_generated_path`, not `group_vars/all.yml`.

**Variable Management Patterns:**
- Reference @.agents/skills/project-structure-rules for scoping conventions
- Use @.agents/skills/secret-generation for dynamic variable patterns
- Apply @.agents/skills/openwrt-network-topology for network variable detection

## Cleanup Requirements

Both cleanup playbooks (`molecule/default/cleanup.yml` and `playbooks/cleanup.yml`) **MUST** remove the generated files:

```yaml
- name: Remove generated env files from controller
  ansible.builtin.file:
    path: "{{ item }}"
    state: absent
  loop:
    - "{{ playbook_dir }}/../../.env.generated"
    - "{{ playbook_dir }}/../../test.env.generated"
  delegate_to: localhost
```

## Variable Access Patterns

- **Role defaults**: `roles/<role>/defaults/main.yml` (never cross-reference between roles)
- **Cross-role facts**: `set_fact` with `cacheable: true` or `add_host` variables
- **Secrets**: `lookup('env', 'VAR_NAME')` in appropriate variable files

This directory contains the variable scoping structure following the project's separation of static constants, runtime secrets, and dynamic configuration.
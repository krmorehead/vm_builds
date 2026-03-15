# Ansible Role Development Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the roles/ directory. These rules focus on role structure, task conventions, and safety patterns.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., .agents/skills/proxmox-safety-rules/SKILL.md), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Ansible Development:**
- @.agents/skills/ansible-conventions - Task structure and module usage
- @.agents/skills/ansible-shell-safety - Shell task safety patterns
- @.agents/skills/task-ordering - Dependency resolution patterns

**OpenWrt Patterns:**
- @.agents/skills/openwrt-busybox-constraints - BusyBox limitations
- @.agents/skills/openwrt-network-topology - Bridge management
- @.agents/skills/openwrt-network-restart - Network service patterns
- @.agents/skills/openwrt-mesh-lxc-wifi - WiFi PHY management
- @.agents/skills/openwrt-security-transition - SSH authentication
- @.agents/skills/openwrt-mac-conflict - MAC address handling

**Infrastructure Patterns:**
- @.agents/skills/proxmox-safety-rules - Host safety patterns
- @.agents/skills/proxmox-network-safety - Network interface safety
- @.agents/skills/vm-provisioning-patterns - VM lifecycle management
- @.agents/skills/lxc-container-patterns - Container provisioning
- @.agents/skills/image-management-patterns - Build vs configure decisions

## Development Guidelines

For Proxmox safety patterns: .agents/skills/proxmox-safety-rules/SKILL.md
For task dependency ordering: .agents/skills/task-ordering/SKILL.md
For baseline establishment: .agents/skills/clean-baselines/SKILL.md

## Cross-Coverage Rules

### From Other Directories
- **Network topology**: Always reference @.agents/skills/openwrt-network-topology when working with bridges
- **Container patterns**: Use @.agents/skills/lxc-container-patterns for LXC-specific concerns
- **VM provisioning**: Reference @.agents/skills/vm-provisioning-patterns for VM lifecycle
- **Cleanup safety**: Apply @.agents/skills/proxmox-cleanup-safety for file removal patterns
- **Image management**: Use @.agents/skills/image-management-patterns for bake vs configure decisions

## Task Structure Rules

- Use fully qualified collection names (`ansible.builtin.command`, not `command`)
- Every `command`/`shell` task MUST have `changed_when` or `failed_when` where appropriate
- Read-only commands: `changed_when: false`
- Legitimately-can-fail commands: `failed_when: false`
- Guard creation tasks with `when: not vm_exists | bool`
- Use section-header comments (`# ── Section name ──`) to organize long task files

## Module Usage Rules

- Proxmox API: `community.proxmox.proxmox_kvm` (NOT `community.general.proxmox_kvm`)
- Proxmox API calls: `delegate_to: localhost` (proxmoxer runs on the control node)
- ALWAYS include `node` in `proxmox_kvm` calls
- OpenWrt commands: `ansible.builtin.raw` ONLY (no Python on OpenWrt)
- Proxmox host commands: `ansible.builtin.command` or `ansible.builtin.shell`

## Variable Conventions

- Role variables do NOT need role-name prefixes
- DO prefix with the type name for clarity across multi-VM roles (e.g., `openwrt_vm_id`, `homeassistant_vm_id`)
- Secrets: `lookup('env', 'VAR_NAME')`. NEVER use vault files
- Cross-role data: `set_fact` with `cacheable: true`, or pass via `add_host` variables
- NEVER reference another role's `defaults/main.yml` directly

## Handler Naming

- Capitalize first word: `Reload networking`, `Update grub`
- `notify:` must match the handler name exactly

## Safety Rules (CRITICAL)

### NEVER do these on remote hosts
- `ifdown --all` - kills the management network
- `systemctl stop networking` - same effect
- `ip link delete vmbr0` - destroys management bridge
- Any command that tears down ALL interfaces simultaneously

### Safe alternatives
- To bring UP new interfaces: `ifup --all --force` (additive, safe)
- To reload after config changes: use `ifreload -a` or restart specific interfaces

### Bridge Management
- NEVER hardcode specific bridge names (vmbr0, vmbr1) as WAN
- WAN bridge is detected at runtime via host default route device
- Override with `openwrt_wan_bridge` in `host_vars` if auto-detection picks wrong bridge

## OpenWrt/BusyBox Constraints

- No `grep -P`. Use `sed -n 's/pattern/\\1/p'` instead
- Heredocs in YAML | blocks get indented, breaking `#!/bin/sh`
- Use `printf '#!/bin/sh\\n...\\n' > /tmp/script.sh` instead
- Switch OpenWrt opkg to HTTP: `sed -i 's|https://|http://|g' /etc/opkg/distfeeds.conf`
- `opkg install kmod-*` does NOT auto-load modules - run `modprobe <module>` explicitly

## Two-Phase Network Restart Pattern

When a configure role needs both WAN internet AND a LAN IP change:

1. **Phase 1**: Configure WAN + LAN ports, keep LAN at default IP (192.168.1.1). Commit, restart. Migrate bootstrap IP to LAN bridge.
2. **Phase 2**: Install packages, configure services, set final LAN IP. Commit, restart.

**NEVER** install packages before WAN is configured and restarted.

## Detached Restart Pattern for OpenWrt

```yaml
# GOOD: printf + start-stop-daemon (shebang not indented)
- ansible.builtin.raw: >-
    printf '#!/bin/sh\\nsleep 3\\n/etc/init.d/network restart\\nsleep 5\\n/etc/init.d/dropbear restart\\nrm -f /tmp/_restart_net.sh\\n'
    > /tmp/_restart_net.sh &&
    chmod +x /tmp/_restart_net.sh &&
    start-stop-daemon -S -b -x /tmp/_restart_net.sh
  ignore_unreachable: true
```

**NEVER** use:
- `nohup` (unreliable on BusyBox)
- heredoc in YAML | block (indentation breaks shebang)

## Task Ordering Principles

Always resolve dependencies top-down:
1. Fix system state before package installation
2. Install packages before using package commands
3. Generate keys/credentials before configuring services
4. Configure before starting services
5. Start services before runtime verification

## Cleanup Rules

- When ANY role deploys files, ALWAYS add to cleanup in BOTH `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`
- Cleanup removes ONLY files playbook deployed, NEVER operator-created credentials
- **NEVER** remove SSH keys or API tokens during cleanup - they're operator prerequisites

**Cleanup Safety Patterns:**
- Reference @.agents/skills/proxmox-cleanup-safety for complete cleanup procedures
- Apply @.agents/skills/vm-cleanup-maintenance for VM cleanup patterns
- Always follow @.agents/skills/molecule-cleanup requirements for test cleanup

## Build vs Configure Pattern

- **NEVER** install packages during configure roles - bake them into images instead
- Configure roles contain only host-specific configuration (IPs, bridges, subnets, peer keys)
- When new packages needed, add to service's image build via `build-images.sh`

This directory contains the core Ansible roles that implement the two-role per service pattern.
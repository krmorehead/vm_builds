# Playbook Execution Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the playbooks/ directory. These rules focus on playbook execution patterns, task ordering, and async operations.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., @.cursor/rules/async-job-patterns.mdc), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Playbook Execution:**
- @.agents/skills/async-job-patterns - Async operations and detached scripts
- @.agents/skills/task-ordering - Task dependency ordering patterns

**Network Patterns:**
- @.agents/skills/openwrt-network-topology - Bridge ordering and WAN detection
- @.agents/skills/openwrt-network-restart - Network restart patterns
- @.agents/skills/openwrt-virtual-vlan - VLAN configuration patterns

**SSH and Connectivity:**
- @.agents/skills/lan-ssh-patterns - SSH ProxyJump patterns
- @.agents/skills/lan-node-setup - LAN host bootstrap patterns
- @.agents/skills/proxmox-ssh-safety - SSH connection safety

**Rollback Implementation:**
- @.agents/skills/rollback-per-feature - Per-feature rollback patterns
- @.agents/skills/rollback-group-reconstruction - Dynamic group reconstruction

## Development Guidelines

For async and detached job patterns: @.cursor/rules/async-job-patterns.mdc
For task dependency ordering: @.cursor/rules/task-ordering.mdc

## Cross-Coverage Rules

### From Other Directories
- **SSH patterns**: Reference @.agents/skills/lan-ssh-patterns for ProxyJump connections
- **Node setup**: Use @.agents/skills/lan-node-setup for LAN host integration
- **Rollback patterns**: Apply @.agents/skills/rollback-per-feature for rollback implementation
- **Group reconstruction**: Reference @.agents/skills/rollback-group-reconstruction for dynamic groups
- **Network restart**: Use @.agents/skills/openwrt-network-restart for topology changes

## Core Playbook Files

### site.yml
Main playbook execution flow with phased deployment:
- Phase 1: Primary hosts (proxmox:!lan_hosts)
- Phase 2: LAN satellites (reachable after OpenWrt creates LAN)
- Phase 3: Services (flavor groups span both primary + LAN hosts)

### cleanup.yml
Rollback and cleanup operations with tag-based execution

## Task Ordering — Dependencies First

Every task has prerequisites. When building a playbook or writing a project plan, resolve dependencies top-down before writing any implementation tasks.

### Ordering Rules for site.yml Plays

1. **Shared infrastructure before service provisioning** — `proxmox_bridges`, `proxmox_pci_passthrough`, `proxmox_igpu` run BEFORE any VM/LXC creation
2. **Provisioning before configuration** — `<type>_lxc`/`<type>_vm` before `<type>_configure`
3. **Network services before dependent services** — OpenWrt (VMID 100) before WireGuard (101) before Pi-hole (102). Startup order mirrors this
4. **deploy_stamp as the LAST role in every provision play** — Records that the play completed successfully

### Ordering Rules for Ansible Task Files

1. **System state fixes before package installation** — If the base image or template has broken package state, fix it FIRST
2. **Package installation before using package commands** — Install `wireguard-tools` before calling `wg genkey`
3. **Key/credential generation before configuration** — Generate keys before templating config files that reference them
4. **Configuration before service start** — Write config files before `systemctl enable/start`
5. **Service start before runtime verification** — Start `wg-quick@wg0` before checking `wg show wg0`
6. **Network configuration before dependent services** — Configure bridges, IPs, and routes before starting services that bind to specific addresses
7. **Kernel module loading before dependent features** — Load `wireguard` module on the host before `wg-quick` in the container

## Detached Scripts on Remote Hosts

When a remote operation will sever the SSH connection (network restart, firewall restart, service reconfiguration), use a detached script:

```yaml
- name: Schedule restart via detached script
  ansible.builtin.raw: >-
    printf '#!/bin/sh\nsleep 1\n<commands>\nrm -f /tmp/_script.sh\n'
    > /tmp/_script.sh &&
    chmod +x /tmp/_script.sh &&
    start-stop-daemon -S -b -x /tmp/_script.sh
  ignore_unreachable: true
  changed_when: true
```

### Rules for Detached Scripts

1. **ALWAYS** use `ignore_unreachable: true` on the task that launches the script. The SSH connection may drop before the task result is returned
2. The detached script **MUST** self-clean (`rm -f /tmp/_script.sh`) as its last command. If the file still exists after the expected runtime, the script failed
3. **NEVER** assume the script succeeded just because the launch task returned ok. The launch only confirms `start-stop-daemon` spawned the process — the script's internal commands may still fail
4. **ALWAYS** follow a detached script with a `pause` that exceeds the script's total runtime (sum of all sleeps + command times + buffer)
5. After the pause, verify the expected outcome (e.g., `wait_for` on a port, check an IP, verify a service is running) rather than trusting the script

### Verification Pattern

```yaml
- name: Launch detached restart
  ansible.builtin.raw: ...
  ignore_unreachable: true

- name: Wait for services to stabilize
  ansible.builtin.pause:
    seconds: 30

- name: Verify expected outcome
  ansible.builtin.wait_for:
    host: "{{ target_ip }}"
    port: 22
    timeout: 120
  delegate_to: "{{ proxmox_host }}"
```

The `wait_for` is the real success check. The pause is just a buffer.

## Synchronous Restarts Over SSH

**NEVER** restart a service synchronously if the restart will change firewall rules, network topology, or SSH configuration affecting the current connection.

**Previous bugs:**
- Synchronous `firewall restart` applied WAN zone rules (input REJECT) to the active SSH path, killing the connection
- Synchronous `network restart` changed interface assignments while the SSH connection was routed through the old topology

## Long-Running Ansible Tasks

For tasks that take longer than the default SSH timeout:

1. Set `async: <seconds>` and `poll: <interval>` on the task
2. Or increase `ConnectTimeout` and `ServerAliveInterval` in `ansible_ssh_common_args`
3. **NEVER** use `async` with `poll: 0` (fire-and-forget) unless you have a separate verification step

## Common Ordering Mistakes

### Package Command Before Install
- `wg genkey` fails with ENOENT because `wireguard-tools` isn't installed yet
- Previous bug: key generation tasks ran before package installation in the WireGuard configure role

### Service Start Before Config
- `systemctl start wg-quick@wg0` fails because `/etc/wireguard/wg0.conf` doesn't exist yet

### Runtime Check Before Service Start
- `wg show wg0` returns "No such device" because the service hasn't created the interface yet

**Ordering Prevention:**
- Use @.agents/skills/task-ordering for comprehensive dependency patterns
- Reference @.agents/skills/project-planning-task-ordering for milestone ordering
- Apply @.agents/skills/vm-lifecycle-architecture for deployment sequencing

## How to Apply Task Ordering

When writing a new task file or reviewing a project plan, mentally walk through each task and ask: "What must already exist for this to succeed?"

If the answer isn't "nothing" or "everything above me," **reorder**.

## Playbook Tags

site.yml uses tags for selective execution:
- `[backup]` — Host backup operations
- `[infra]` — Infrastructure setup (bridges, PCI, iGPU)
- `[openwrt]` — OpenWrt VM provisioning and configuration
- `[lan-satellite]` — LAN host bootstrap
- `[pihole]` — Pi-hole DNS service
- `[monitoring]` — Netdata and rsyslog monitoring
- `[wireguard]` — WireGuard VPN service
- `[mesh-wifi]` — OpenWrt mesh WiFi
- `[never]` — Per-feature plays (opt-in only)

cleanup.yml uses tags for rollback operations:
- `[cleanup]` — Full cleanup
- `[rollback-*]` — Specific feature rollback
- `[full-restore]` — Restore from backup
- `[clean]` — Clean state removal

This directory contains the main playbook execution flows following strict dependency ordering and async operation patterns.
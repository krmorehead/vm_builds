---
name: ansible-conventions
description: Ansible coding conventions including task structure, module usage, variable patterns, OpenWrt constraints, and network restart patterns.
---

# Ansible Coding Conventions

Use when writing Ansible tasks, configuring OpenWrt systems, managing Proxmox hosts, or following project coding standards.

## Rules

1. ALWAYS use fully qualified collection names (`ansible.builtin.command`, not `command`)
2. NEVER use `local_action` - always use `delegate_to: localhost`
3. ALWAYS include `changed_when` or `failed_when` on command/shell tasks
4. ALWAYS use `community.proxmox.proxmox_kvm` (NOT `community.general.proxmox_kvm`)
5. ALWAYS use OpenWrt commands with `ansible.builtin.raw` ONLY (no Python)
6. NEVER mix legacy directives with RainerScript in rsyslog configs
7. ALWAYS capitalize first word in handler names and match `notify:` exactly
8. NEVER install packages in configure roles - bake into images instead

## Patterns

Task structure:

```yaml
- name: Configure service
  ansible.builtin.command: systemctl enable service
  changed_when: true
  when: not vm_exists | bool
```

OpenWrt two-phase restart:

```yaml
# Phase 1: Configure WAN + LAN, keep LAN at default IP
- name: Configure network interfaces
  ansible.builtin.raw: uci set network.lan.ipaddr='192.168.1.1'
  ansible.builtin.raw: uci commit network

# Phase 2: Install packages, set final LAN IP  
- name: Install packages
  ansible.builtin.raw: opkg install package-name

- name: Set final LAN IP
  ansible.builtin.raw: uci set network.lan.ipaddr='10.10.10.1'
```

Detached restart pattern:

```yaml
- name: Schedule restart via detached script
  ansible.builtin.raw: >-
    printf '#!/bin/sh\nsleep 3\n/etc/init.d/network restart\nsleep 5\n/etc/init.d/dropbear restart\nrm -f /tmp/_restart_net.sh\n'
    > /tmp/_restart_net.sh &&
    chmod +x /tmp/_restart_net.sh &&
    start-stop-daemon -S -b -x /tmp/_restart_net.sh
  ignore_unreachable: true
```

## Anti-patterns

NEVER explain what Ansible is in coding conventions
NEVER use heredocs in YAML | blocks for OpenWrt scripts (indentation breaks shebang)
NEVER install packages during configure roles
NEVER hardcode bridge names (vmbr0, vmbr1) - use auto-detection
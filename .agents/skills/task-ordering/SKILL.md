---
name: task-ordering
description: Task dependency ordering for Ansible playbooks, ensuring prerequisites are met before dependent operations. Includes system state, package, configuration, and service ordering.
---

# Task Ordering — Dependencies First

Use when writing Ansible task files, playbook ordering, or project plans. Dependencies must be resolved top-down before implementation tasks.

## Rules

1. ALWAYS fix system state before package installation (repair broken apt first)
2. NEVER use package commands before installing the package (install `wireguard-tools` before `wg genkey`)
3. NEVER configure services before generating keys/credentials
4. NEVER start services before writing configuration files
5. NEVER verify runtime state before starting services
6. NEVER start dependent services before network configuration
7. NEVER use kernel modules before loading them on host
8. ALWAYS run shared infrastructure before service provisioning

## Patterns

Correct dependency order:

```yaml
# 1. Fix system state first
- name: Fix broken dpkg packages
  ansible.builtin.command: dpkg --configure -a

# 2. Install packages before using them
- name: Install wireguard tools
  ansible.builtin.apt:
    name: wireguard-tools

# 3. Generate keys before configuring
- name: Generate WireGuard keypair
  ansible.builtin.command: wg genkey
  register: wg_private_key

# 4. Configure before starting services
- name: Write WireGuard config
  ansible.builtin.template:
    src: wg0.conf.j2
    dest: /etc/wireguard/wg0.conf

# 5. Start services after configuration
- name: Enable and start WireGuard
  ansible.builtin.systemd:
    name: wg-quick@wg0
    state: started
    enabled: true

# 6. Verify after services are running
- name: Verify WireGuard interface
  ansible.builtin.command: wg show wg0
```

Site.yml play ordering:

```yaml
# Infrastructure first (bridges, PCI, iGPU)
- hosts: proxmox
  roles:
    - proxmox_bridges
    - proxmox_pci_passthrough
    - proxmox_igpu

# Provisioning before configuration
- hosts: flavor_group
  roles:
    - service_vm  # or service_lxc
    - deploy_stamp

# Configure after provisioning
- hosts: dynamic_group
  roles:
    - service_configure
```

## Anti-patterns

NEVER explain what dependencies are in task ordering rules
NEVER install packages after trying to use them
NEVER start services before writing their configuration
NEVER verify service state before starting the service
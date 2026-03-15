---
name: clean-baselines
description: Ensure nodes start from clean working baselines. Fix system state at provisioning layer, never accept broken apt, and verify baselines before adding features.
---

# Clean Baselines — Never Accept Broken State

Use when diagnosing failed service installations, fixing apt/dpkg issues, or establishing working baselines for LXC containers and VMs.

## Rules

1. NEVER paper over broken state in individual service roles - fix at baseline layer
2. NEVER install packages during configure roles - fix template issues in provisioning role
3. NEVER accept broken apt/dpkg state on any node - fix baseline, don't add workarounds
4. NEVER include kernel packages in LXC containers - they share host kernel
5. ALWAYS use `install_recommends: false` in LXC containers
6. ALWAYS verify baseline before adding features - check apt and basic operations first
7. NEVER assume broken template state is acceptable - fix in proxmox_lxc role
8. NEVER debug service roles when baseline is broken - fix baseline first

## Patterns

Fixing template issues in provisioning role:

```yaml
# In roles/proxmox_lxc/tasks/main.yml
- name: Fix broken kernel packages in template
  ansible.builtin.command: dpkg --purge linux-image-rt-amd64
  when: "'linux-image-rt-amd64' in apt.stdout"

- name: Update apt cache
  ansible.builtin.apt:
    update_cache: true
    cache_valid_time: 86400
    install_recommends: false
```

Baseline verification before features:

```yaml
# Before running service configuration
- name: Verify apt works
  ansible.builtin.command: apt-get update
  changed_when: false

- name: Verify systemd starts
  ansible.builtin.systemd:
    name: systemd-networkd
    state: started
  changed_when: false

# Only proceed if baseline is healthy
- name: Configure service
  include_role:
    name: service_configure
  when: apt_update is successful
```

Common baseline failures:

```yaml
# Diagnose in this order:
1. Broken dpkg database (fix in proxmox_lxc)
2. Missing kernel modules (fix in host provisioning)
3. No network connectivity (fix in proxmox_bridges)
4. DNS resolution failure (fix in openwrt_configure)
```

## Anti-patterns

NEVER explain what baselines are in clean baseline rules
NEVER add workarounds in configure roles for template issues
NEVER proceed with service configuration when apt is broken
NEVER assume LXC containers need kernel packages
---
name: proxmox-safety-rules
description: Safety rules for Proxmox host management, remote operations, and credential protection. Includes network safety, cleanup completeness, and authentication preservation.
---

# Proxmox Host Safety Rules

Use when managing remote Proxmox hosts, handling network changes, performing cleanup, or working with SSH connections to prevent catastrophic failures.

## Rules

1. NEVER use `ifdown --all`, `systemctl stop networking`, or `ip link delete vmbr0` on remote hosts
2. NEVER hardcode bridge names (vmbr0, vmbr1) - WAN bridge is detected at runtime via default route
3. NEVER apply WAN_MAC at VM NIC level - always use MAC conflict detection flow
4. NEVER remove SSH keys or API tokens during cleanup - they are operator prerequisites
5. NEVER escape shell variables without proper escaping - always use `\$` for `$` in SSH commands
6. NEVER use `grep -q` in pipelines with `set -o pipefail` - use `grep -c` instead
7. NEVER use local_action - always use `delegate_to: localhost`
8. ALWAYS escape `$` as `\$` in double-quoted SSH commands

## Patterns

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

WAN MAC conflict detection flow:

```yaml
# Conflict detection checks three layers:
# 1. Exact MAC in /proc/net/arp
# 2. EUI-64 in IPv6 neighbor table  
# 3. Gateway MAC shares OUI with cloned MAC
# If conflict detected, MAC saved to /etc/openwrt_wan_mac_deferred
```

Shell escaping in SSH:

```yaml
# GOOD: escaped variable
ssh host "... awk '{print \$2}' ..."

# BAD: unescaped (expands to empty on local shell)
ssh host "... awk '{print $2}' ..."
```

## GPU Driver and Host Crash Prevention

9. NEVER run `modprobe -r amdgpu` on single-GPU AMD hosts — kernel panic
10. NEVER run `modprobe -r amdgpu/i915` in broad-scope plays (hosts: proxmox*) — PCI rescan is sufficient for E2E cleanup
11. ONLY run GPU driver unload in per-feature cleanup gated on VGA count >= 2
12. Every host has `wol_capable` (true/false) in host_vars — non-WoL hosts cannot be recovered remotely
13. `tests/test_host_safety.py` catches this pattern statically at pytest time
14. Previous bug: E2E cleanup `modprobe -r amdgpu` kernel-panicked `ai` (single AMD GPU, USB ethernet). Required physical power-on

## Anti-patterns

NEVER explain what Proxmox is in safety rules
NEVER use synchronous firewall restart over SSH when WAN rules change
NEVER assume PRIMARY_HOST is the only way to reach hosts
NEVER remove files you didn't deploy in cleanup
NEVER run modprobe -r for GPU drivers in broad-scope cleanup
NEVER shut down or crash hosts with wol_capable: false
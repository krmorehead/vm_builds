---
name: molecule-cleanup
description: Molecule cleanup requirements for repeatable Ansible test runs. Cleanup playbooks, credential safety, explicit VMIDs.
---

# Molecule Cleanup

## NEVER destroy credentials

NEVER remove operator-created access credentials:
- `/root/.ssh/authorized_keys` — permanent SSH keys
- Proxmox API tokens via `pveum user token remove`

Locks out remote nodes thousands of miles away with no console access.

Test: "Did converge/playbook create this?" If no → do not touch it.

## Cleanup patterns

Cleanup MUST use service-specific cleanup with explicit VMIDs from group_vars/all.yml:

```yaml
- name: Ensure VM stopped
  ansible.builtin.command:
    cmd: qm stop {{ openwrt_vm_id }}
  ignore_errors: true

- name: Destroy VM
  ansible.builtin.command:
    cmd: qm destroy {{ openwrt_vm_id }}
  ignore_errors: true
```

NEVER use blanket iteration or wildcard (destroys non-project resources).

NEVER delete templates in molecule cleanup — triggers re-upload of ~820MB. Template deletion only in `playbooks/cleanup.yml` behind `[full-restore, clean]` tags.

NEVER restore host config from backup — redundant with explicit file removal, adds ~15s per host. Backup restore only in `playbooks/cleanup.yml` behind `[full-restore]`.

## Required cleanup steps

1. Destroy project VMs by explicit VMID (check existence with `qm status` first)
2. Destroy project containers by explicit VMID (check with `pct status` first)
3. Unbind all devices from `vfio-pci`
4. Remove modprobe blacklist files: `blacklist-wifi.conf`, `vfio-pci.conf`
5. Rebind WiFi PCI devices via sysfs: `tasks/sysfs_wifi_rebind.yml` (NEVER `modprobe -r`)
6. Rescan PCI bus: `echo 1 > /sys/bus/pci/rescan`
7. Tear down stale bridges (skip vmbr0 management bridge)
8. `ifup --all --force` to restore interfaces

Without steps 3-6, next run cannot detect WiFi hardware.

For iGPU passthrough cleanup (gaming_lxc per-feature scenario), the role uses sysfs-only binding (no modprobe configs). Cleanup:
1. Count VGA controllers BEFORE unloading — gate step 2 on count >= 2
2. `modprobe -r i915/amdgpu` to release stale bindings (ONLY if multi-GPU)
3. Rescan PCI bus
4. `modprobe i915/amdgpu` to re-probe
5. Wait for `/dev/dri/renderD*` to appear

NEVER run `modprobe -r amdgpu` on a single-GPU AMD host. It triggers a kernel panic because amdgpu is the sole framebuffer. Intel single-GPU hosts survive (SSH continues, no display). For E2E cleanup, the PCI bus rescan after vfio-pci unbind is sufficient — skip explicit driver unload/reload entirely.

Previous bug: `modprobe i915` without `-r` first was a no-op (module already loaded). DRI devices never reappeared.

Previous bug: cleanup removed `blacklist-igpu.conf` — a file the role never created. Cleanup MUST only remove what the deploy creates.

Previous bug: E2E cleanup ran `modprobe -r amdgpu` on ALL hosts including `ai` (single AMD GPU). Kernel panicked, host crashed. Since `ai` uses USB ethernet (no WoL), it couldn't be recovered remotely. Required physical power-on.

## Host recoverability

Every host MUST have `wol_capable` in host_vars (true/false). Cleanup code MUST NOT run operations that could crash or shut down hosts with `wol_capable: false`. Non-WoL hosts (USB ethernet, no IPMI) require manual power-on and cannot be recovered from automation-caused crashes.

NEVER include non-WoL hosts in `scripts/wol.sh`. Unit tests enforce this via `tests/test_wol.py`.

## Unified cleanup (single source of truth)

`playbooks/cleanup.yml` is the ONE cleanup playbook for all contexts (molecule, CLI, SuperManager). `molecule/default/cleanup.yml` is a one-line `import_playbook`. There is only ONE cleanup to maintain.

When a role writes a file, add to these cleanup paths:
- `playbooks/cleanup.yml` (unified cleanup — primary hosts)
- `tasks/cleanup_lan_host.yml` (shared LAN host cleanup — included by unified cleanup)

Current managed files:
- Host config: `ansible-bridges.conf`, `ansible-proxmox-lan.conf`, `ansible-temp-lan.conf`
- Module config: `blacklist-wifi.conf`, `vfio-pci.conf`, `wireguard.conf`
- Apt repos: `pve-no-subscription.sources` (renamed to `.disabled` after)
- VM images: `/tmp/openwrt-upload*` (temporary)
- Hookscripts: `/var/lib/vz/snippets/mesh-wifi-phy-*.sh`
- Facts: `vm_builds.fact`
- Local: `.state/addresses.json`, `.env.generated`, `test.env.generated`

## Conditionals

Make `update-initramfs` conditional on PCI passthrough config:
```yaml
- name: Check if vfio-pci config exists
  ansible.builtin.stat:
    path: /etc/modprobe.d/vfio-pci.conf
  register: _vfio_conf

- name: Update initramfs
  ansible.builtin.command: update-initramfs -u
  when: _vfio_conf.stat.exists
```

Saves ~20s per host when PCI passthrough not configured.

## Network state

If cleanup removes SSH access temporarily, verify SSH before re-running. May need physical power cycle.

## Cleanup ordering: LAN hosts BEFORE primary hosts

ALWAYS clean LAN satellite hosts BEFORE cleaning primary hosts. The primary host cleanup destroys OpenWrt and tears down bridges, making LAN hosts unreachable. If LAN cleanup runs after, the reachability check fails, LAN host cleanup is skipped, and stale containers survive into the next converge.

Stale containers from a previous converge are NEVER recreated by `proxmox_lxc` — the role checks `pct status` and skips creation if the container already exists. A stale container with an outdated template persists indefinitely until manually destroyed.

```yaml
# Play 1: LAN hosts (while OpenWrt + bridges still exist)
- name: Selective LAN satellite cleanup
  hosts: router_nodes
  tasks:
    - ansible.builtin.include_tasks: ../tasks/cleanup_lan_host.yml
      loop: "{{ groups['lan_hosts'] | default([]) }}"

# Play 2: Primary hosts (destroys OpenWrt, bridges)
- name: Selective service cleanup on primary hosts
  hosts: proxmox:!lan_hosts

# Play 3: Test control plane (conditioned on MOLECULE_PROJECT_DIRECTORY)
- name: Test control plane teardown
  hosts: localhost
```

Previous bug: LAN host cleanup ran AFTER primary cleanup. OpenWrt (gateway to mesh1) was already destroyed. SSH to mesh1 at 10.10.10.210 failed (no route). The stale rsyslog container on mesh1 survived with an outdated logrotate config. Reconverge reused the stale container. The logrotate verify assertion failed on every test cycle until the cleanup ordering was fixed.

## LAN host systemd unit cleanup (CRITICAL)

The main cleanup play targets `proxmox:!lan_hosts` — LAN hosts are
explicitly excluded because they're only reachable through the router.
LAN host cleanup runs via `tasks/cleanup_lan_host.yml` from `router_nodes`.

`cleanup_lan_host.yml` MUST clean up host-level systemd units deployed
by provisioning roles. Without this, legacy services survive cleanup and
hold ports, causing new services to crash-loop on the next converge.

Previous catastrophe (2026-04-14): KasmVNC migration renamed
`kiosk-vnc-proxy` to `kiosk-display-proxy`. LAN host cleanup didn't
remove the old unit. mesh1's old service held port 6080. New service
crash-looped 957 times. Masked by adding 100s of retries instead of
diagnosing the root cause.

## Service migration: stop legacy before deploying replacement

When RENAMING a systemd service, the provisioning role MUST stop and
remove the old service BEFORE deploying the new one:

```yaml
- name: Stop legacy service before deploying replacement
  ansible.builtin.shell:
    cmd: |
      systemctl stop old-service 2>/dev/null || true
      systemctl disable old-service 2>/dev/null || true
      rm -f /etc/systemd/system/old-service.service
      pkill -f 'socat.*TCP-LISTEN:PORT' 2>/dev/null || true
    executable: /bin/bash
  changed_when: true
  failed_when: false
```

## Common failures

| Issue | Cause | Fix |
|-------|-------|-----|
| Stale LAN IP on subsequent runs | Missing config file in cleanup list | Add to all cleanup playbooks |
| Bridge numbers keep incrementing | Didn't remove bridges or reload modules | Add bridge teardown to cleanup |
| WiFi not detected | Didn't unbind vfio-pci or reload modules | Ensure steps 3-6 present |
| Re-authentication fails after rollback | Cleanup removed authorized_keys | NEVER remove credentials |
| Stale container on LAN host | LAN cleanup runs after OpenWrt destroyed | Move LAN cleanup before primary |
| New service crash-loops after rename | Old service holds port, not cleaned | Stop legacy in provisioning role |
| LAN host keeps stale systemd units | cleanup_lan_host.yml misses them | Add unit cleanup to LAN tasks |

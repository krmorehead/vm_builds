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
5. Reload WiFi kernel modules: `modprobe -r iwlmvm iwlwifi && modprobe iwlwifi`
6. Rescan PCI bus: `echo 1 > /sys/bus/pci/rescan`
7. Tear down stale bridges (skip vmbr0 management bridge)
8. `ifup --all --force` to restore interfaces

Without steps 3-6, next run cannot detect WiFi hardware.

For iGPU passthrough cleanup (gaming_vm per-feature scenario), the role uses sysfs-only binding (no modprobe configs). Cleanup:
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

## File parity

When a role writes a file, add to ALL cleanup paths:
- `molecule/default/cleanup.yml` (test cleanup — primary)
- `molecule/default/cleanup_lan_host.yml` (test cleanup — LAN)
- `tasks/cleanup_lan_host.yml` (production cleanup — LAN)
- `playbooks/cleanup.yml` (production cleanup — primary)

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

## Common failures

| Issue | Cause | Fix |
|-------|-------|-----|
| Stale LAN IP on subsequent runs | Missing config file in cleanup list | Add to all cleanup playbooks |
| Bridge numbers keep incrementing | Didn't remove bridges or reload modules | Add bridge teardown to cleanup |
| WiFi not detected | Didn't unbind vfio-pci or reload modules | Ensure steps 3-6 present |
| Re-authentication fails after rollback | Cleanup removed authorized_keys | NEVER remove credentials

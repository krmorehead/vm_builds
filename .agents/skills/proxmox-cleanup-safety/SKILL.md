---
name: proxmox-cleanup-safety
description: Proxmox cleanup completeness and maintenance safety patterns. Use when planning cleanup operations, file removal, or maintenance tasks on Proxmox hosts.
---

# Proxmox Cleanup & Maintenance Rules

## Cleanup Completeness Requirement

1. When ANY role deploys a file to the Proxmox host, ALWAYS add it to the removal list in BOTH cleanup playbooks (`molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`).

## Current Ansible-Managed Files

2. Current ansible-managed files that must be cleaned:
   - `/etc/network/interfaces.d/ansible-bridges.conf` (bridge config, may be modified to `inet dhcp`)
   - `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (legacy LAN management IP, superseded)
   - `/etc/network/interfaces.d/ansible-temp-lan.conf` (test workaround, cleaned up)
   - `/etc/modprobe.d/blacklist-wifi.conf` (WiFi driver blacklist)
   - `/etc/modprobe.d/vfio-pci.conf` (PCI passthrough config)
   - `/etc/ansible/facts.d/vm_builds.fact` (deploy stamp tracking)
   - `/etc/apt/sources.list.d/pve-no-subscription.sources` (added by `proxmox_igpu`)
   - `/tmp/openwrt-router-*.img*` (left behind if build fails mid-upload)
   - `/var/lib/vz/template/cache/debian-*.tar.zst` (LXC templates)
   - Enterprise repos: restore `pve-enterprise.sources.disabled` → `.sources` and `ceph.sources.disabled` → `.sources`

## Local State Files Cleanup

3. Local state files that must be cleaned (via `delegate_to: localhost`):
   - `.state/addresses.json` (cached host IPs)

4. Previous bug: `ansible-proxmox-lan.conf` was deployed but not cleaned up, leaving stale LAN management IPs across test runs.

## Test Machine Protocol

5. Before running destructive operations (cleanup, VM destroy):
   1. Confirm the target is the **test machine** (check `PROXMOX_HOST` env var)
   2. Verify a backup exists (check for `manifest.json` in backup dir)
   3. Use the `cleanup.sh` wrapper which enforces env file sourcing

## PCI Device Cleanup Requirement

6. Devices bound to `vfio-pci` do NOT auto-revert when the VM is destroyed. Without cleanup, the next run can't detect WiFi hardware.

7. Required PCI cleanup sequence:
   ```bash
   # 1. Unbind all vfio-pci devices
   for dev in /sys/bus/pci/drivers/vfio-pci/0000:*/; do
     addr=$(basename "$dev")
     echo "$addr" > /sys/bus/pci/drivers/vfio-pci/unbind
   done

   # 2. Remove blacklist and vfio config files
   rm -f /etc/modprobe.d/blacklist-wifi.conf /etc/modprobe.d/vfio-pci.conf

   # 3. Rebind WiFi via sysfs (NEVER modprobe -r)
   # Use tasks/sysfs_wifi_rebind.yml or inline:
   for phy in /sys/class/ieee80211/phy*; do
     PCI=$(basename $(readlink -f "$phy/device"))
     DRV=$(basename $(readlink -f "$phy/device/driver"))
     echo "$PCI" > /sys/bus/pci/drivers/$DRV/unbind
     echo "" > /sys/bus/pci/devices/$PCI/driver_override
   done

   # 4. Rescan PCI bus + explicit bind
   echo 1 > /sys/bus/pci/rescan
   sleep 1
   echo "$PCI" > /sys/bus/pci/drivers/$DRV/bind
   ```

8. All steps are required. The explicit bind (step 4) is critical -- `echo 1 > /sys/bus/pci/rescan` alone is insufficient because the kernel won't auto-bind drivers that were explicitly unbound.
9. WiFi module unload (`modprobe -r iwlwifi/iwlmvm`) is BANNED on ALL hardware. On AMD APUs (Raven Ridge), the NBIO handles ALL PCIe, USB, and SATA on a shared die — a WiFi module unload triggers a PCIe reset that kills USB ethernet hours later. Use sysfs unbind + PCI rescan + explicit bind instead (`tasks/sysfs_wifi_rebind.yml`). This pattern is universally safe on Intel and AMD, with no hardware-specific branching.

## GPU Driver Cleanup — Separate from WiFi

10. GPU driver cleanup (i915/amdgpu) is DIFFERENT from WiFi cleanup. GPU `modprobe -r amdgpu` on a single-GPU AMD host causes a **kernel panic** (sole framebuffer removal).

10. NEVER run `modprobe -r amdgpu` or `modprobe -r i915` in broad-scope plays (hosts: proxmox*). For E2E cleanup, PCI bus rescan after vfio-pci unbind is sufficient — skip GPU driver unload.

11. ONLY run GPU driver unload in per-feature cleanup (e.g., sunshine-vm, gaming-rollback) gated on `lspci | grep -c 'VGA compatible controller'` >= 2.

12. `tests/test_host_safety.py` is a static linter that catches `modprobe -r amdgpu/i915` in broad-scope plays without VGA guards. Run `pytest tests/` to catch this at dev time.

13. Previous bug: E2E cleanup ran `modprobe -r amdgpu` on ALL hosts including `ai` (single AMD GPU, USB ethernet). Kernel panicked, host crashed. Required physical power-on 3000 miles away.

## Host Recoverability

14. Every host MUST declare `wol_capable` (true/false) in `inventory/host_vars/`. Cleanup MUST NOT run operations that could crash hosts with `wol_capable: false`.

15. Non-WoL hosts (USB ethernet — e.g., `ai`) cannot be recovered remotely. A kernel panic or shutdown is a production-breaking incident requiring physical access.

16. `tests/test_wol.py` enforces WoL exclusion. `tests/test_host_safety.py` enforces GPU safety. Both run as part of `pytest tests/`.
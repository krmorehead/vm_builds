---
name: proxmox-system-safety
description: Proxmox system safety operations and hardware detection patterns. Use when managing system operations, hardware detection, or safety-critical tasks on Proxmox hosts.
---

# Proxmox System Safety Rules

## LVM Operations on Root Volumes

1. Do NOT create LVM snapshots of the Proxmox root volume (`pve/root`). Merging snapshots on a live root volume is unreliable and can leave the system in a stuck merge state requiring reboot.

2. Use file-based config backups (`tar`) and `vzdump` for VMs instead.

## Reboot Awareness

3. If a playbook changes GRUB, initramfs, or kernel modules, a reboot may be needed.

4. Set `pci_passthrough_allow_reboot: true` in host vars to allow automated reboots.

5. After reboot, wait for SSH to come back with `wait_for_connection`.

## Hardware Detection Requirements

6. **iGPU**: every modern Intel CPU has one. `proxmox_igpu` MUST hard-fail if absent.

7. **WiFi + VT-d/IOMMU**: required for PCI passthrough. `proxmox_pci_passthrough` MUST hard-fail if IOMMU is not active after reboot or groups are invalid.

8. NIC count: OK to handle dynamically (hardware legitimately varies).

## Hardware Failure Requirements

9. NEVER add "graceful skip" for hardware expected on every host. Silent skips mask fixable BIOS settings (VT-d disabled) behind warnings that are easy to miss.

10. Previous bug: `proxmox_pci_passthrough` silently skipped WiFi passthrough when IOMMU groups were invalid on mesh1. Root cause was VT-d disabled in BIOS — a 30-second fix masked for an entire test cycle.

## System Safety Decision Tree

11. Use this decision tree:
    ```
    Is it modifying LVM on root?
    ├── YES → BLOCK. Use tar + vzdump instead.
    └── NO → SAFE. Proceed.
    ```

## Hardware Detection Pattern

12. For expected hardware (iGPU, WiFi with VT-d/IOMMU), always hard-fail when absent rather than graceful skip. This ensures critical issues are caught immediately rather than silently ignored.
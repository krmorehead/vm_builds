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

## PCI Passthrough Prerequisites

13. WiFi PCIe passthrough requires the `q35` machine type. Set `machine: q35` when `wifi_pci_devices` is non-empty.

14. IOMMU group isolation is mandatory. ALWAYS verify before binding to vfio-pci.

15. WiFi NICs must be excluded from bridge creation — they're passed through via PCIe, not bridged.

## Package Name Verification

16. NEVER assume a package name is correct without checking. Package names vary between Debian releases, architectures, and distributions. ALWAYS verify with `apt-cache search <keyword>` or `apt list <name>`.

17. Previous bug: `intel-media-va-driver-non-free` was correct on Debian Bullseye but does not exist on Debian Trixie. The correct package is `intel-media-va-driver`. The task failed with "No package matching" and required manual investigation.

## Dynamic Device Detection

18. NEVER hardcode device paths like `/dev/dri/card0`. The card number depends on driver probe order and can change across reboots or kernel updates.

19. ALWAYS detect devices dynamically by querying sysfs driver bindings: iterate `/dev/dri/card*`, check `readlink -f /sys/class/drm/cardN/device/driver`, and match on the driver name.

20. Previous bug: `/dev/dri/card0` was assumed to be the Intel iGPU, but on a multi-GPU system `card0` was the discrete GPU. Sysfs-based detection finds the correct device regardless of probe order.
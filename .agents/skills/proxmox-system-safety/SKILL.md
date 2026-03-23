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

## iGPU PCI Passthrough (vfio-pci)

21. Prefer runtime sysfs manipulation over persistent modprobe configs for iGPU passthrough. Writing to `/sys/bus/pci/drivers/vfio-pci/new_id` and `/sys/bus/pci/drivers/vfio-pci/bind` is reversible without initramfs updates. Modprobe blacklists require `update-initramfs` and a reboot.

22. Single-GPU passthrough is supported for Intel iGPUs and discrete GPUs via Proxmox hookscripts. The hookscript pattern: `pre-start` unbinds the GPU from the native driver via sysfs and binds to vfio-pci; `post-stop` reverses the operation. The host runs headless (SSH/web only) while the VM has the GPU. NEVER attempt GPU passthrough on AMD APU iGPUs (Raven Ridge, etc.) — the GPU shares the SoC die and ANY unbind path (sysfs or modprobe -r) hangs the entire system. This is a hardware limitation, not fixable with hookscripts.

23. NEVER run `modprobe -r amdgpu` or `modprobe -r i915` as GPU cleanup. ALWAYS use sysfs operations: unbind from vfio-pci, clear `driver_override`, PCI rescan. The rescan triggers the kernel to auto-bind the native driver. This is safe on ANY host regardless of GPU count.

24. GPU passthrough hookscript (`/var/lib/vz/snippets/gpu-passthrough-hook.sh`) manages the full lifecycle: discovers hostpci devices from VM config, stops GPU-consuming LXC containers, suspends conflicting VMs, binds/unbinds via sysfs, persists state in `/run/gpu-passthrough/vm-<VMID>.state` for post-stop recovery. The hookscript is generalized — works with any GPU vendor (Intel, AMD, NVIDIA) and any VM that uses `--hostpci`.

25. Previous bug: cleanup ran `modprobe -r amdgpu` on ALL hosts via E2E cleanup. On `ai` (single AMD GPU, USB ethernet), this caused a kernel panic. Fix: replaced all GPU `modprobe -r` with sysfs unbind + PCI rescan.

26. Previous bug: sysfs unbind of AMD Raven Ridge APU iGPU (1002:15dd) via hookscript pre-start hung the entire system. Unlike discrete GPUs, APU iGPUs share the SoC die with the CPU — even sysfs unbind triggers a GPU reset that freezes the NBIO, killing the entire system including USB ethernet (EHOSTUNREACH). This is the same class of failure as `modprobe -r amdgpu` but at the hardware level.

27. Previous bug: PCI rescan after vfio-pci unbind did NOT auto-bind the native driver when the module was already loaded. DRI devices (`/dev/dri/renderD128`) did not reappear. Fix: explicitly bind to the native driver after rescan (`echo PCI_ADDR > /sys/bus/pci/drivers/i915/bind`). The cleanup and hookscript post-stop both must do explicit rebinding, not rely on auto-binding.

27. Cleanup MUST match deployment scope. If the role uses sysfs-only binding (no modprobe configs), cleanup MUST NOT remove modprobe config files. Cleanup MUST also remove hookscript state files from `/run/gpu-passthrough/` and the hookscript itself from `/var/lib/vz/snippets/`.

## Host Recoverability

28. Every host MUST declare `wol_capable` (true/false) in host_vars. This tracks whether the host can be remotely recovered via Wake-on-LAN after a crash or shutdown.

29. USB ethernet adapters do NOT support WoL. The USB host controller powers down in S5 (standby) and lacks magic packet detection circuitry. Hosts connected exclusively via USB ethernet (e.g., `ai`) MUST have `wol_capable: false`.

30. NEVER run operations that could crash a non-WoL host from automation. This includes `modprobe -r` of the sole GPU driver, `shutdown`, `poweroff`, or any operation that could trigger a kernel panic. Non-WoL hosts require physical intervention to recover.

31. `scripts/wol.sh` MUST NOT include non-WoL hosts. Unit tests in `tests/test_wol.py` enforce this. The E2E verify playbook also asserts `wol_capable` is defined for every host and that non-WoL hosts don't appear in wol.sh.

32. Previous bug: `ai` was listed in `wol.sh` with its PCIe NIC MAC, but `ai` is connected via USB ethernet only. The PCIe NIC is not connected to the network, making WoL impossible.
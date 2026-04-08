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

## Missing DRI Devices Recovery

17. Between test cycles, after VM passthrough, or hookscript operations, the iGPU can lack `/dev/dri` nodes even though the native driver module is loaded. Two causes: (a) device bound to vfio-pci, (b) device unbound from native driver but module still loaded (PCI rescan alone won't re-bind).

18. `proxmox_igpu` MUST check `/dev/dri/renderD128` existence BEFORE device detection. If missing: unbind from whatever driver holds the device, clear driver_override, PCI rescan, then EXPLICITLY bind to the native driver (`echo PCI_ADDR > /sys/bus/pci/drivers/i915/bind`). PCI rescan alone does NOT auto-bind when the module is already loaded.

19. Previous bug: `molecule converge` (no cleanup) after a passthrough test left the iGPU without DRI nodes on `home`. `proxmox_igpu` saw `i915` in `lsmod`, skipped recovery, then hard-failed on "DRI devices missing." Explicit driver bind after PCI rescan is the fix.

## Proxmox firmware package conflicts

20. On Proxmox VE, `pve-firmware` bundles all Intel/AMD firmware including iwlwifi. NEVER install standalone `firmware-iwlwifi` — it conflicts with `pve-firmware` and triggers the Proxmox apt hook to block removal of the `proxmox-ve` meta-package.

21. `proxmox_pci_passthrough` MUST check for `pve-firmware` before attempting to install `firmware-iwlwifi`. If `pve-firmware` is present, skip the install — the firmware is already available.

22. Previous bug: `firmware-iwlwifi` install failed on mesh1, bridge-1, bridge-2, and home with `pve-apt-hook returned error code (1)`. All hosts had `pve-firmware` installed, which already provides iwlwifi firmware.

## Enterprise Repository Management

23. Proxmox enterprise repo disabling MUST happen in `pre_tasks` of infrastructure plays, BEFORE any role that calls `apt update`. The `proxmox_pci_passthrough` role needs `apt` for firmware packages and runs before `proxmox_igpu`.

24. NEVER put repo management inside a role that isn't the FIRST role in the play. If any earlier role needs `apt`, the repos won't be ready.

25. Previous bug: Enterprise repo disabling was inside `proxmox_igpu` (third role in infra play). `proxmox_pci_passthrough` (second role) ran `apt update` for firmware packages and failed with 401 Unauthorized on `mesh2`. Fix: moved to `pre_tasks` of both infra plays in `site.yml`.

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

## Hookscript Attachment Ordering

28. Display-exclusive hookscripts that stop DRI-sharing containers MUST NOT be attached during provisioning. Attaching during `kiosk_lxc` (Phase 2.5) causes the hookscript to fire when `desktop_vm` starts in Phase 3, stopping all DRI containers (Kodi, Moonlight, Kiosk) before their configure plays run.

29. Pattern: deploy the hookscript FILE in the provisioning role, attach it to containers/VMs in a dedicated play AFTER all configure plays finish. This ensures all containers are configured before the hookscript can stop them.

30. Configure roles for DRI-sharing containers should include a defensive "ensure container is running" guard at the top (check `pct status`, start if stopped, wait for readiness). This handles re-runs where hookscripts may already be attached from a previous cycle.

31. Previous bug: `kiosk_lxc` deployed AND attached the display-exclusive hookscript during Phase 2.5. When `desktop_vm` started in Phase 3, the hookscript's `pre-start(400)` stopped Kodi (301), Moonlight (302), and Kiosk (401). `Configure Kodi` then failed with "container '301' not running!" Fix: split hookscript deployment (provisioning) from attachment (post-configure play).

32. Verify assertions for DRI-sharing containers (Kodi, Kiosk, Moonlight) MUST skip `systemctl is-active` checks when the Desktop VM is running. The Desktop VM holds the iGPU via PCI passthrough, making DRI devices unavailable to containers. Graphical services (Kodi) legitimately report `inactive` without GPU access. Check `qm status desktop_vm_id` and gate the service-active assertion.

33. Previous bug: Kodi container was `running` but `systemctl is-active kodi` returned `inactive` during verify. Root cause: Desktop VM held the iGPU, DRI devices absent from container. Fix: added `"'running' not in (_desktop_status.stdout | default(''))"` condition to the Kodi service-active assertion.

34. Not all services run as systemd daemons. Moonlight-embedded is an on-demand streaming client binary (`/usr/local/bin/moonlight`), not a persistent service. Verify assertions for such services should check binary existence and config deployment, NOT `systemctl is-active`.

35. Previous bug: Moonlight verify assertion checked `systemctl is-active moonlight` but there IS no `moonlight.service` — moonlight-embedded is compiled from source as a CLI binary with no systemd unit file. The assertion always failed. Fix: changed to check binary existence and config file presence.

## Host Recoverability

28. Every host MUST declare `wol_capable` (true/false) in host_vars. This tracks whether the host can be remotely recovered via Wake-on-LAN after a crash or shutdown.

29. USB ethernet adapters do NOT support WoL. The USB host controller powers down in S5 (standby) and lacks magic packet detection circuitry. Hosts connected exclusively via USB ethernet (e.g., `ai`) MUST have `wol_capable: false`.

30. NEVER run operations that could crash a non-WoL host from automation. This includes `modprobe -r` of the sole GPU driver, `shutdown`, `poweroff`, or any operation that could trigger a kernel panic. Non-WoL hosts require physical intervention to recover.

31. `scripts/wol.sh` MUST NOT include non-WoL hosts. Unit tests in `tests/test_wol.py` enforce this. The E2E verify playbook also asserts `wol_capable` is defined for every host and that non-WoL hosts don't appear in wol.sh.

32. Previous bug: `ai` was listed in `wol.sh` with its PCIe NIC MAC, but `ai` is connected via USB ethernet only. The PCIe NIC is not connected to the network, making WoL impossible.
# Sunshine Host Build Process (Windows VM -- Legacy)

> **Note:** This is the legacy Windows VM gaming path. The active gaming
> deployment uses a Fedora LXC container with GPU render device sharing.
> See [gaming-lxc-build.md](gaming-lxc-build.md) for the current approach.

## Overview

This document describes the build process for the Sunshine/Gaming Windows 11
VM image used in the vm_builds project. The Gaming VM provides game streaming
via Moonlight using iGPU PCI passthrough (vfio-pci) for hardware encoding.
The Windows VM roles (`gaming_vm`, `gaming_configure`) remain in the
repository as a general-purpose Windows build but are no longer the active
gaming deployment path in `site.yml`.

## Image Build Requirements

### Build Command

```bash
scripts/build-images.sh --host <proxmox-ip> --only sunshine
```

### Prerequisites

- Proxmox VE host with internet access
- Tiny11 ISO: `images/Tiny11-2026-03-15.iso` (~5.4 GB)
- virtio-win ISO: `images/isos/virtio-win.iso` (~754 MB)
- Build VMID 992 must be available (temporary build VM)
- Sufficient disk space for Windows install (~64 GB during build)

### Build Output

**Image:** `images/sunshine-win11-amd64.qcow2`
**Size:** ~8-12 GB (QEMU qcow2 format)
**Build Time:** 15-30 minutes

## Design Decisions

### Why Tiny11

Standard Windows 11 ISOs are 5+ GB and include bloatware. Tiny11 is a
slimmed-down Windows 11 ISO that:
- Removes Microsoft Store apps, OneDrive, Cortana
- Disables telemetry and auto-update services
- Supports unattended installation via `autounattend.xml`
- Results in a smaller disk footprint (~8 GB vs ~20+ GB)

### Why Pre-Built Image (Not Runtime Install)

Following the project's "bake, don't configure at runtime" principle:
- Windows installation takes 15-30 minutes — too slow for each deployment
- Runtime installs depend on network (Windows Update, GitHub downloads)
- The pre-built image arrives ready to run with all software installed
- The configure role only applies host-specific settings (credentials, firewall)

### Software Baked into the Image

**Core:**
- Windows 11 (via Tiny11)
- Virtio drivers (disk, network, balloon, serial)
- QEMU Guest Agent (for IP discovery and shutdown)
- OpenSSH Server (for Ansible connectivity)

**Streaming:**
- Sunshine (latest from GitHub releases)

**Gaming:**
- GZDoom (latest from GitHub releases)
- Freedoom Phase 1 + Phase 2 WADs

**System:**
- Windows Firewall rules for Sunshine ports baked in
- RDP enabled for backup remote access
- Auto-login configured for the admin user

## Build Process

### Phase 1: Answer ISO Creation

The build script creates a secondary ISO containing:
- `autounattend.xml` — unattended Windows installation config
- `post-install.ps1` — PowerShell script for software installation

These files live in `roles/gaming_vm/files/`.

### Phase 2: Remote VM Build

1. Upload Tiny11 ISO and virtio-win ISO to Proxmox `/tmp/`
2. Upload answer ISO to Proxmox `/tmp/`
3. Create temporary VM (VMID 992) with:
   - q35 machine type, OVMF UEFI (EFI disk: raw format on LVM-thin)
   - ide0: Tiny11 ISO, ide2: virtio-win ISO, sata0: answer ISO
   - VirtIO SCSI controller + 64 GB virtio disk
   - VirtIO NIC on WAN bridge
4. Start VM and send Enter keypress for OVMF CD boot
5. Windows installs unattended via `autounattend.xml`
6. Poll for QEMU Guest Agent response (indicates OS installation complete)
7. Poll for `post-install.ps1` completion (marker file check, up to 20 min)
8. Shut down and export disk via `qemu-img convert` to qcow2

### Phase 3: Image Export

1. Export disk to `/var/tmp/sunshine-win11-amd64.qcow2` on Proxmox
2. Download image to local `images/` directory via SCP
3. Destroy temporary build VM (VMID 992)
4. Clean up uploaded ISOs from Proxmox `/tmp/`

## Deployment Pattern

### Provisioning (`gaming_vm` role)

1. Verify pre-built image exists locally
2. Validate IOMMU and iGPU IOMMU group isolation
3. Unbind iGPU from host driver, bind to vfio-pci (runtime sysfs, no modprobe files)
4. Upload image to `/var/tmp/` (NOT `/tmp/` — tmpfs is too small) and
   create VM with hostpci0 passthrough
5. Import disk, set boot order, configure auto-start
6. Start VM and wait for Guest Agent
7. Discover IP and register in `gaming` dynamic group

VMID is per-host: `gaming_vm_id + groups['gaming_nodes'].index(hostname)`.
This avoids VMID collisions when multiple hosts share a Proxmox node.

### Configuration (`gaming_configure` role)

1. Verify iGPU visible in Windows
2. Set Sunshine credentials via web API (auto-generates if not in env)
3. Start Sunshine service
4. Verify/add firewall rules for Sunshine ports
5. Verify GZDoom/Freedoom installation
6. Enable RDP

## Testing

### Per-Feature Scenario

```bash
molecule test -s sunshine-vm
```

Runs on home only (single-node per-feature test). Multi-node coverage
(home + mesh1 with per-host VMID) is validated by the full E2E integration
test. The `ai` host is excluded from gaming_nodes because it has a single
AMD GPU — binding it to vfio-pci causes a kernel panic.

Since iGPU is consumed exclusively by vfio-pci, this scenario is mutually
exclusive with media containers (Jellyfin, Kodi) that share the iGPU.

The Windows image (19 GB) is cached on the Proxmox host after the first
upload. Subsequent runs skip the upload unless the cached file is missing.

### Verify Assertions

- VM running with correct VMID and config (q35, OVMF, hostpci0, onboot, agent)
- QEMU Guest Agent responsive
- VM has an IPv4 address
- In-VM command execution works via Guest Agent
- iGPU bound to vfio-pci on host (both count and specific device)
- deploy_stamp exists

## Cleanup

The molecule cleanup and `gaming-rollback` tag in `playbooks/cleanup.yml`:
1. Stops and destroys the Gaming VM (per-host VMID)
2. Unbinds iGPU from vfio-pci (targeted, not broad)
3. Unloads and reloads GPU drivers (i915/amdgpu)
4. Rescans PCI bus and waits for DRI devices

No modprobe config files are written or removed — the role uses runtime
sysfs manipulation only.

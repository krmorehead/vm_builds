# Gaming Rig

## Overview

A dedicated Proxmox host running a Windows 11 VM with full discrete GPU
passthrough for gaming and Sunshine for Moonlight game streaming. This is a
**separate build profile** from the home entertainment box -- it runs on
different hardware.

## Type

VM (KVM/QEMU) on dedicated gaming hardware

## Resources

- Cores: 4-8 (depends on host CPU)
- RAM: 8-16 GB (depends on host memory)
- Disk: 128+ GB (OS + game storage)
- Network: LAN bridge
- GPU: discrete GPU via exclusive PCIe passthrough (vfio-pci)
- VMID: 600

## Hardware Requirements

```
Minimum Gaming Rig Hardware
├── CPU: 6+ cores (to leave 2 for Proxmox + monitoring after VM allocation)
├── RAM: 16+ GB (8 GB for VM, remainder for host + monitoring)
├── GPU: discrete NVIDIA or AMD GPU (for vfio-pci passthrough)
├── Storage: NVMe SSD (PCIe passthrough or virtio-scsi)
├── Network: 1 GbE minimum (for Moonlight streaming)
└── IOMMU: must support clean group isolation for GPU
```

## Startup

- Auto-start: yes (on the gaming rig host, this is the primary service)
- Boot priority: 1
- Depends on: Proxmox host only

## Build Profiles

- Home Entertainment Box: no
- Minimal Router: no
- Gaming Rig: yes (core)
- The gaming rig host also belongs to `monitoring_nodes` for Netdata + rsyslog

## Prerequisites

- Shared infrastructure: `proxmox_pci_passthrough` role (project 00) -- for discrete GPU binding
- Gaming hardware with IOMMU support
- Windows 11 ISO or image

---

## Architectural Decisions

```
Decisions
├── Gaming OS: Windows 11
│   └── Required for AAA game compatibility; DirectX 12; widest game library
│
├── Streaming server: Sunshine
│   └── Open-source, cross-platform Moonlight host; replaces NVIDIA GeForce Experience
│
├── GPU passthrough: full discrete GPU via vfio-pci
│   └── Guest gets native GPU performance; host uses CPU-only rendering
│
├── Audio: virtual audio (PulseAudio or PipeWire) streamed through Sunshine
│   └── No physical audio out needed on gaming rig; Moonlight client handles playback
│
├── Looking Glass: deferred (not in scope for initial build)
│   └── Adds complexity; Sunshine covers streaming use case; revisit if local display needed
│
└── Windows provisioning: virtio ISO + autounattend.xml
    └── Automated Windows install with virtio drivers; no manual interaction required
```

---

## Milestones

### Milestone 1: Image & Driver Preparation

- [ ] Download Windows 11 ISO (or create custom image) into `images/`
- [ ] Download virtio-win ISO (Red Hat) for storage and network drivers
- [ ] Create `autounattend.xml`:
  - License acceptance, partition layout, driver injection, admin user
  - Product key from `.env` (`WINDOWS_PRODUCT_KEY`)
- [ ] Add `gaming_image_path`, `gaming_virtio_iso_path` to `group_vars/all.yml`

### Milestone 2: VM Provisioning

- [ ] Create `roles/gaming_vm/defaults/main.yml`:
  - `gaming_vm_id: 600`, `gaming_vm_memory: 8192` (or 16384), `gaming_vm_cores: 8`
  - `gaming_vm_disk: 128G`
  - `gaming_vm_onboot: true`, `gaming_vm_startup_order: 1`
- [ ] Create `roles/gaming_vm/tasks/main.yml`:
  - `qm create` with q35 machine type, OVMF UEFI, virtio-scsi
  - Import Windows disk image (or attach ISO + autounattend for install)
  - Attach virtio-win ISO as second CD-ROM
  - Configure `hostpci0` for discrete GPU (PCI address from `proxmox_pci_passthrough`)
  - Attach NIC on LAN bridge (virtio model)
  - CPU: host model, hidden KVM, `topoext` for AMD (or appropriate Intel flags)
  - Start VM
  - Register in `gaming` dynamic group via `add_host`
- [ ] Verify GPU unbinds from host and binds to vfio-pci

### Milestone 3: Windows Configuration

- [ ] Create `roles/gaming_configure/tasks/main.yml` (via WinRM or SSH)
- [ ] Install virtio guest agent
- [ ] Install GPU drivers (NVIDIA or AMD, from package or URL)
- [ ] Install Sunshine:
  - Download latest release
  - Configure: username/password from `.env` (`SUNSHINE_USER`, `SUNSHINE_PASSWORD`)
  - Set streaming quality defaults (1080p60 or 4K60 depending on GPU)
- [ ] Install Steam (optional, via winget or Chocolatey)
- [ ] Disable Windows Update auto-restart (gaming interruption prevention)
- [ ] Enable RDP for backup remote access

### Milestone 4: Backup & Snapshot Strategy

- [ ] Configure vzdump schedule for Windows VM
- [ ] Exclude game data directories from backup (large, re-downloadable)
- [ ] Create snapshot before major driver updates
- [ ] Document restore procedure

### Milestone 5: Streaming Verification

- [ ] Verify Sunshine web UI accessible on LAN
- [ ] Pair Moonlight client (project 10) with Sunshine
- [ ] Test streaming: latency, resolution, controller input
- [ ] Verify Moonlight can wake VM if suspended

### Milestone 6: Monitoring Integration

- [ ] Gaming rig host joins `monitoring_nodes` flavor group
- [ ] Netdata + rsyslog containers provisioned alongside gaming VM
- [ ] Monitor: GPU temperature, fan speed, streaming sessions, VM health

### Milestone 7: Integration

- [ ] Add `gaming_vm` provision play to `site.yml` targeting `gaming_nodes`
- [ ] Add `gaming_configure` play targeting `gaming` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `gaming_nodes` flavor group to inventory

### Milestone 8: Documentation

- [ ] Create `docs/architecture/gaming-build.md`
- [ ] Document GPU passthrough, Sunshine setup, Moonlight pairing
- [ ] Add CHANGELOG entry

# Debian Desktop

## Overview

A full Debian VM with KDE and GNOME desktop sessions for general-purpose
computing. Takes **exclusive** iGPU access via `hostpci` passthrough, which
disrupts all iGPU-dependent LXC containers while running. This is the most
impactful member of the display-exclusive group and should only be started
when needed.

## Type

VM (KVM/QEMU)

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 32 GB (OS + applications)
- Network: LAN bridge
- iGPU: exclusive passthrough via `hostpci` (entire GPU bound to vfio-pci)
- VMID: 400

## Startup

- Auto-start: no (on-demand; user starts for desktop tasks)
- Boot priority: N/A
- Depends on: `proxmox_igpu` role (provides PCI address for exclusive hostpci passthrough)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes** (most disruptive)
- Start Desktop VM → Kiosk, Kodi, Moonlight all stop; iGPU unbound from i915
- Jellyfin falls back to software transcoding while Desktop VM runs
- Stop Desktop VM → iGPU returns to i915; Kiosk restarts

## Prerequisites

- Shared infrastructure: `proxmox_igpu`, display-exclusive hookscript (project 00)
- OpenWrt router operational (network, SSH via ProxyJump)
- Physical display connected to host HDMI/DP

---

## Architectural Decisions

```
Decisions
├── Guest OS: Debian 12
│   └── Same distro as Proxmox host; consistent, well-supported, stable
│
├── Base image: Debian cloud image + cloud-init for bootstrap
│   └── No interactive installer; cloud-init sets user, SSH keys, network at first boot
│
├── Display manager: SDDM
│   └── Handles KDE + GNOME session switching; lightweight; works with DRM/KMS
│
├── iGPU access: exclusive passthrough via hostpci (vfio-pci)
│   └── VM needs full GPU driver stack (i915 in guest); only option for display-out from VM
│
├── Desktop sessions
│   ├── KDE Plasma: Windows-style UX (taskbar, system tray, alt-tab)
│   └── GNOME: Mac-style UX (dock, activities, workspaces)
│
└── Configuration method: SSH via ProxyJump (standard VM pattern)
    └── VMs don't support pct exec; SSH key injected by cloud-init
```

---

## Milestones

### Milestone 1: Image Preparation

- [ ] Download Debian 12 cloud image (qcow2) into `images/`
- [ ] Add `desktop_image_path` to `group_vars/all.yml`
- [ ] Verify cloud-init support in the image

### Milestone 2: VM Provisioning

- [ ] Create `roles/desktop_vm/defaults/main.yml`:
  - `desktop_vm_id: 400`, `desktop_vm_memory: 1024`, `desktop_vm_cores: 2`
  - `desktop_vm_disk: 32G`
  - `desktop_vm_onboot: false` (on-demand)
- [ ] Create `roles/desktop_vm/tasks/main.yml`:
  - `qm create` with UEFI BIOS (OVMF), q35 machine type
  - Import disk image
  - Attach NIC on LAN bridge
  - Configure `hostpci0` for iGPU passthrough (PCI address from `proxmox_igpu` facts)
  - Configure cloud-init: user, SSH keys, network (DHCP or static)
  - Attach display-exclusive hookscript
  - Start VM, wait for SSH
  - Register in `desktop` dynamic group via `add_host`
- [ ] Verify iGPU unbinds from host when VM starts

### Milestone 3: Configuration

- [ ] Create `roles/desktop_configure/tasks/main.yml` (via SSH + ProxyJump)
- [ ] Install KDE Plasma desktop (`task-kde-desktop`)
- [ ] Install GNOME desktop (`task-gnome-desktop`)
- [ ] Install SDDM display manager, configure as default
- [ ] User setup: create user from `.env`, add to `video`, `render`, `audio` groups
- [ ] Install Intel GPU drivers in guest (`xserver-xorg-video-intel`, `mesa-vulkan-drivers`)
- [ ] Install base applications: Firefox, file manager, terminal
- [ ] Configure log forwarding to rsyslog

### Milestone 4: Desktop Environment Polish

- [ ] KDE session: taskbar at bottom, system tray, dark theme, window snapping
- [ ] GNOME session: dock at bottom, dash-to-dock extension, dark theme
- [ ] Verify session switching: log out of KDE → SDDM → log into GNOME
- [ ] Auto-login: controlled by `.env` flag (`DESKTOP_AUTOLOGIN=true|false`)

### Milestone 5: Integration

- [ ] Add `desktop_vm` to `site.yml` targeting `desktop_nodes`
- [ ] Add `desktop_configure` play targeting `desktop` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Order play AFTER media tier (Jellyfin, Kodi, Moonlight already provisioned)

### Milestone 6: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - VM created, cloud-init complete, SSH accessible
  - SDDM installed, KDE + GNOME packages present
  - GPU passthrough configured (`hostpci0` set)
- [ ] Extend `molecule/default/cleanup.yml` for VM 400

### Milestone 7: Documentation

- [ ] Create `docs/architecture/desktop-build.md`
- [ ] Document iGPU exclusive passthrough, cloud-init, session switching
- [ ] Add CHANGELOG entry

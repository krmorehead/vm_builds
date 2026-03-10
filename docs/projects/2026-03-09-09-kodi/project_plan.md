# Kodi

## Overview

An LXC container running Kodi as a local media player and home theater
frontend. Connects to Jellyfin for media library access. Renders directly
to the physical display via DRM/KMS using the shared iGPU.

## Type

LXC container

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 4 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (display output + decode)
- VMID: 301

## Startup

- Auto-start: no (on-demand; user starts for media playback)
- Boot priority: N/A
- Depends on: Jellyfin (media backend), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes**
- Start Kodi → Kiosk stops (hookscript)
- Stop Kodi → Kiosk restarts (hookscript)
- iGPU access: shared (LXC bind mount, not exclusive passthrough)

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive hookscript (project 00)
- Jellyfin (project 08) -- media server backend
- Physical display connected to host HDMI/DP

---

## Architectural Decisions

```
Decisions
├── Display output: kodi-standalone via GBM/DRM
│   └── Direct rendering to display, no X11 or Wayland needed, minimal overhead
│
├── Audio output: ALSA passthrough via /dev/snd/* bind mount
│   └── Direct HDMI audio through iGPU, lowest latency
│
├── Jellyfin plugin: JellyCon
│   └── Well-maintained Kodi add-on; native library browsing in Kodi UI
│
└── Remote control: Kodi web interface + CEC (HDMI-CEC via libcec)
    └── Web remote from phone; CEC for TV remote control
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/kodi_lxc/defaults/main.yml`:
  - `kodi_ct_id: 301`, `kodi_ct_memory: 1024`, `kodi_ct_cores: 2`
  - `kodi_ct_disk: 4G`
  - `kodi_ct_onboot: false` (on-demand, display-exclusive)
- [ ] Create `roles/kodi_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with device mounts: `/dev/dri/*`, `/dev/snd/*`, `/dev/input/*`
  - cgroup allowlist: DRI (226:*), sound (116:*), input (13:*)
  - Attach display-exclusive hookscript
- [ ] Register in `kodi` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/kodi_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install `kodi-standalone`, `kodi-gbm`, Mesa Intel drivers, `libcec`
- [ ] Install JellyCon add-on, template connection settings (Jellyfin IP, credentials)
- [ ] Configure ALSA HDMI audio output
- [ ] Enable Kodi web interface on port 8080
- [ ] Auto-start: systemd service for `kodi-standalone` on container boot
- [ ] Template `advancedsettings.xml` for buffer/cache tuning

### Milestone 3: Integration

- [ ] Add `kodi_lxc` to `site.yml` targeting `media_nodes` (combined with jellyfin + moonlight)
- [ ] Add `kodi_configure` play targeting `kodi` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container created, Kodi packages installed, DRI devices present
  - Kodi web interface on port 8080 (when running)
  - JellyCon add-on installed
- [ ] Extend `molecule/default/cleanup.yml` for container 301

### Milestone 5: Documentation

- [ ] Create `docs/architecture/kodi-build.md`
- [ ] Document GBM/DRM output, display-exclusive transitions, remote control
- [ ] Add CHANGELOG entry

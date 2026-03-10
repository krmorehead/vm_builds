# Jellyfin

## Overview

An LXC container running Jellyfin media server with Intel Quick Sync
hardware transcoding via iGPU device passthrough. Serves media to clients
locally and remotely. Offloads transcoding to GPU, keeping CPU usage minimal.

## Type

LXC container

## Resources

- Cores: 2
- RAM: 2048 MB
- Disk: 8 GB (application + metadata; media on external mount)
- Network: LAN bridge, static IP
- iGPU: `/dev/dri/renderD128` bind mount (shared, not exclusive)
- VMID: 300

## Startup

- Auto-start: yes
- Boot priority: 5 (alongside Home Assistant)
- Depends on: OpenWrt Router, `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **no** (uses renderD128 for transcoding only, not display output)
- Runs alongside any display service (Kiosk, Kodi, Moonlight)
- Falls back to software transcoding when Desktop VM takes exclusive iGPU

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- Shared infrastructure: `proxmox_igpu` role (project 00, milestone 2)
- OpenWrt router operational (network)
- Media storage accessible (NFS/SMB mount or local disk)

---

## Architectural Decisions

```
Decisions
├── Media server: Jellyfin
│   └── FOSS, no license, good VA-API support, active development
│
├── Container privileges: unprivileged with device passthrough
│   └── More secure; /dev/dri/renderD128 via cgroup allowlist + GID mapping
│
├── iGPU access: device bind mount (shared) via proxmox_igpu facts
│   └── NOT full PCI passthrough; iGPU stays on host i915 driver; multiple containers share
│
└── Media storage: NFS mount from home server / NAS
    └── Large libraries don't fit on local disk; NFS is transparent to Jellyfin
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/jellyfin_lxc/defaults/main.yml`:
  - `jellyfin_ct_id: 300`, `jellyfin_ct_memory: 2048`, `jellyfin_ct_cores: 2`
  - `jellyfin_ct_disk: 8G`, `jellyfin_ct_ip` (static)
  - `jellyfin_ct_onboot: true`, `jellyfin_ct_startup_order: 5`
  - `jellyfin_media_path: /mnt/media` (host-side mount)
- [ ] Create `roles/jellyfin_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with device mount (`igpu_render_device`), cgroup allowlist (`c 226:128 rwm`), media bind mount
- [ ] Register in `jellyfin` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/jellyfin_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install Jellyfin from official Debian repo
- [ ] Configure iGPU: create `render` group (GID from `igpu_render_gid`), add `jellyfin` user, verify `vainfo`
- [ ] Template server config: VA-API transcode, media paths, web on port 8096
- [ ] Set admin user from `.env` (`JELLYFIN_ADMIN_PASSWORD`)
- [ ] Configure log forwarding to rsyslog

### Milestone 3: Integration

- [ ] Add `jellyfin_lxc` to `site.yml` targeting `media_nodes` (combined with kodi + moonlight)
- [ ] Add `jellyfin_configure` play targeting `jellyfin` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `jellyfin_media_path` to `group_vars/all.yml`

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, Jellyfin active, web on port 8096
  - `/dev/dri/renderD128` exists, `vainfo` succeeds
  - Media path mounted
- [ ] Extend `molecule/default/cleanup.yml` for container 300

### Milestone 5: Documentation

- [ ] Create `docs/architecture/jellyfin-build.md`
- [ ] Document iGPU shared mount, media storage, software fallback
- [ ] Add CHANGELOG entry

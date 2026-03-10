# Moonlight Client

## Overview

An LXC container running `moonlight-embedded` for game streaming from the
Gaming Rig (project 13). Uses the iGPU for hardware video decode (VA-API)
and renders to the physical display via DRM/KMS. USB input devices are
passed through for controller and keyboard support.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 2 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (video decode + display output)
- Input: `/dev/input/*` bind mount (USB HID for controllers)
- VMID: 302

## Startup

- Auto-start: no (on-demand; user starts for game streaming)
- Boot priority: N/A
- Depends on: Gaming Rig Sunshine server (external), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no (this is the **client**; the rig is the server)

## Display Exclusivity

- Display-exclusive: **yes**
- Start Moonlight → Kiosk stops (hookscript)
- Stop Moonlight → Kiosk restarts (hookscript)
- iGPU access: shared (LXC bind mount, not exclusive passthrough)

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive hookscript (project 00)
- Gaming Rig with Sunshine installed (project 13) -- streaming server
- Physical display connected to host HDMI/DP
- USB controller connected to host

---

## Architectural Decisions

```
Decisions
├── Container type: LXC (not VM)
│   └── Lightweight; DRM/KMS display output works from unprivileged LXC with device passthrough
│
├── Streaming client: moonlight-embedded
│   └── Headless/framebuffer Moonlight; no X11/Wayland; minimal resource usage
│
├── Video decode: VA-API via Intel iGPU
│   └── Hardware decode of H.265/HEVC stream from Sunshine; CPU stays near idle
│
└── Input passthrough: USB HID via /dev/input/* bind mount + evdev
    └── Direct input events from USB controllers; udev rules for stable device names
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/moonlight_lxc/defaults/main.yml`:
  - `moonlight_ct_id: 302`, `moonlight_ct_memory: 512`, `moonlight_ct_cores: 1`
  - `moonlight_ct_disk: 2G`
  - `moonlight_ct_onboot: false` (on-demand, display-exclusive)
- [ ] Create `roles/moonlight_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with device mounts: `/dev/dri/*`, `/dev/input/*`, `/dev/uinput`
  - cgroup allowlist: DRI (226:*), input (13:*), uinput (10:223)
  - Attach display-exclusive hookscript
- [ ] Register in `moonlight` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/moonlight_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install `moonlight-embedded` from official release or build from source
- [ ] Install Mesa Intel VA-API drivers (`intel-media-va-driver` or `intel-media-va-driver-non-free`)
- [ ] Verify hardware decode: `vainfo` shows H.265 decode profile
- [ ] Template config: resolution (1080p), codec (H.265), bitrate, Sunshine server IP
- [ ] Server pairing: automate `moonlight pair` via pre-shared PIN from `.env`
- [ ] Create systemd service for `moonlight-embedded stream` on container boot

### Milestone 3: Integration

- [ ] Add `moonlight_lxc` to `site.yml` targeting `media_nodes` (combined with jellyfin + kodi)
- [ ] Add `moonlight_configure` play targeting `moonlight` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add Sunshine server IP to `group_vars/all.yml`

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container created, moonlight-embedded installed, DRI devices present
  - VA-API decode available (`vainfo`)
  - Config file templated with server IP
- [ ] Extend `molecule/default/cleanup.yml` for container 302

### Milestone 5: Documentation

- [ ] Create `docs/architecture/moonlight-build.md`
- [ ] Document DRM/KMS output, VA-API decode, pairing flow
- [ ] Add CHANGELOG entry

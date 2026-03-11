# Custom UX Kiosk

## Overview

A lightweight LXC container that displays a full-screen dashboard on the
physical display when no other display service is active. It is the default
idle state of the home entertainment box -- the "home screen" you see when
the device boots and nothing else is running.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 2 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (display output via DRM/KMS)
- VMID: 401

## Startup

- Auto-start: yes
- Boot priority: 6 (last to start; all services it displays must be up first)
- Depends on: Home Assistant (for Lovelace dashboard), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes** (default state)
- This is the service that starts when all other display services stop
- Start Kodi/Moonlight → Kiosk stops
- Stop Kodi/Moonlight → Kiosk restarts (via hookscript)
- Start Desktop VM → Kiosk stops + loses iGPU

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive hookscript (project 00)
- Home Assistant (project 07) -- serves the dashboard content
- Physical display connected to host HDMI/DP

---

## Architectural Decisions

```
Decisions
├── Display server: Cage (single-application Wayland compositor)
│   └── Minimal Wayland compositor that runs one app fullscreen; no shell, no window decorations
│
├── Application: Chromium in kiosk mode
│   └── Renders HA Lovelace dashboard; --kiosk --no-sandbox --ozone-platform=wayland
│
├── Dashboard: Home Assistant Lovelace panel
│   └── Integrates with HA ecosystem; live updates; customizable cards
│
└── Auto-start trigger: Proxmox hookscript
    └── Kiosk restarts automatically when other display services stop
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/kiosk_lxc/defaults/main.yml`:
  - `kiosk_ct_id: 401`, `kiosk_ct_memory: 512`, `kiosk_ct_cores: 1`
  - `kiosk_ct_disk: 2G`
  - `kiosk_ct_onboot: true`, `kiosk_ct_startup_order: 6`
  - `kiosk_dashboard_url` (Home Assistant Lovelace URL)
- [ ] Create `roles/kiosk_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with device mounts: `/dev/dri/*`
  - cgroup allowlist: DRI (226:*)
  - Attach display-exclusive hookscript
- [ ] Register in `kiosk` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/kiosk_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install Cage, Chromium
- [ ] Install Mesa Intel drivers
- [ ] Create systemd service for Cage + Chromium:
  - `cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland <dashboard_url>`
  - Restart on failure, start on boot
- [ ] Template `kiosk_dashboard_url` from role defaults
- [ ] Configure log forwarding to rsyslog

### Milestone 3: Integration

- [ ] Add `kiosk_lxc` to `site.yml` targeting `desktop_nodes` (combined with `desktop_vm`)
- [ ] Add `kiosk_configure` play targeting `kiosk` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `kiosk_dashboard_url` to `group_vars/all.yml`

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, Cage and Chromium installed
  - Systemd service enabled, DRI devices present
  - Dashboard URL configured
- [ ] Extend `molecule/default/cleanup.yml` for container 401

### Milestone 5: Display-Exclusive Orchestration

_Requires at least one other display-capable service (Kodi, Moonlight, or
Desktop VM). Relocated from shared-infrastructure project._

- [ ] Create Proxmox hookscript (`/var/lib/vz/snippets/display-exclusive.sh`):
  - On pre-start: stop all other display services
  - On post-stop of non-default service: start Kiosk (default)
  - Display service VMIDs read from a config variable
- [ ] Deploy hookscript via Ansible task in the infrastructure play
- [ ] Attach hookscript to display-exclusive containers/VMs
      (`pct set` / `qm set --hookscript`)
- [ ] Add Ansible pre-task in `site.yml` that enforces exclusion during deploys

**Verify:**

- [ ] Hookscript exists at `/var/lib/vz/snippets/display-exclusive.sh`
- [ ] Starting Kodi stops Kiosk automatically
- [ ] Stopping Kodi restarts Kiosk automatically
- [ ] Starting Desktop VM stops all LXC display services

**Rollback:**

- Remove hookscript from `/var/lib/vz/snippets/`
- Detach hookscript from containers/VMs (`pct set --delete hookscript`)

### Milestone 6: Documentation

- [ ] Create `docs/architecture/kiosk-build.md`
- [ ] Document Cage + Chromium, display-exclusive default state, dashboard config
- [ ] Add CHANGELOG entry

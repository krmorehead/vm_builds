# Home Assistant

## Overview

An LXC container running Home Assistant Core via Docker for home automation.
Manages smart devices, automations, and dashboards. Accessible locally and
remotely via the WireGuard tunnel.

## Type

LXC container (with Docker inside via nesting)

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 8 GB (database, integrations, backups)
- Network: LAN bridge, static IP
- VMID: 200

## Startup

- Auto-start: yes
- Boot priority: 5 (after network + observability)
- Depends on: OpenWrt Router, Pi-hole (DNS)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) -- for remote access

---

## Architectural Decisions

```
Decisions
├── Installation method: HA Container (Docker in LXC)
│   └── Lightweight; full HA Core with all integrations. Supervised is fragile in LXC. HAOS as VM wastes resources.
│
├── Docker-in-LXC: nesting enabled (features: nesting=1), cgroup delegation
│   └── Well-tested on Proxmox; required for Docker daemon inside unprivileged LXC
│
├── USB passthrough: device bind mount via lxc.mount.entry
│   └── For Zigbee/Z-Wave dongles; udev rules on host ensure stable /dev/ttyUSB* naming
│
└── Backup strategy: HA native snapshots + container-level vzdump
    └── Defense in depth: HA handles config, vzdump handles whole container
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/homeassistant_lxc/defaults/main.yml`:
  - `homeassistant_ct_id: 200`, `homeassistant_ct_memory: 1024`, `homeassistant_ct_cores: 2`
  - `homeassistant_ct_disk: 8G`, `homeassistant_ct_ip` (static)
  - `homeassistant_ct_features: "nesting=1"`
  - `homeassistant_ct_onboot: true`, `homeassistant_ct_startup_order: 5`
  - `homeassistant_usb_devices: []` (list of /dev/ttyUSB* for Zigbee/Z-Wave)
- [ ] Create `roles/homeassistant_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with nesting and static IP
  - Add `lxc.mount.entry` for USB devices (when list non-empty)
  - Configure cgroup delegation for Docker
- [ ] Register in `homeassistant` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/homeassistant_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install Docker (`docker-ce` from official repo)
- [ ] Create Docker compose for HA:
  - Image: `homeassistant/home-assistant:stable`
  - Volume: `/opt/homeassistant/config:/config`
  - Network: host mode (mDNS/SSDP discovery)
  - Restart: always
  - Device mounts for USB dongles (if configured)
- [ ] Template `configuration.yaml`: HTTP, recorder (SQLite, 10-day retention), logger
- [ ] Start compose, set admin credentials from `.env` (`HA_ADMIN_PASSWORD`)
- [ ] Configure log forwarding to rsyslog

### Milestone 3: Integration

- [ ] Add `homeassistant_lxc` to `site.yml` targeting `service_nodes`
- [ ] Add `homeassistant_configure` play targeting `homeassistant` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `HA_ADMIN_PASSWORD` to `test.env` and `.env` template

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, Docker running, HA container running
  - Web UI on port 8123, API returns valid response
- [ ] Extend `molecule/default/cleanup.yml` for container 200

### Milestone 5: Documentation

- [ ] Create `docs/architecture/homeassistant-build.md`
- [ ] Document Docker-in-LXC, USB passthrough, backup/restore
- [ ] Add CHANGELOG entry

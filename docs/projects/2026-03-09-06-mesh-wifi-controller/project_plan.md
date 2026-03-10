# Mesh WiFi Controller

## Overview

An LXC container running OpenWISP for centralized management of WiFi access
points across multiple OpenWrt mesh nodes. Provides a web UI and REST API for
managing SSIDs, channel plans, and transmit power. Complements Dawn (installed
on OpenWrt nodes in project 01) which handles real-time client steering.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 4 GB
- Network: LAN bridge, static IP
- VMID: 103

## Startup

- Auto-start: yes
- Boot priority: 4 (after network + observability)
- Depends on: OpenWrt Router (mesh established), Pi-hole (DNS)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no (single node doesn't need centralized management)
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router with 802.11s mesh operational (project 01)
- Multiple WiFi-capable nodes (2+ APs for meaningful use)

---

## Architectural Decisions

```
Decisions
├── Controller software: OpenWISP
│   └── Only mature open-source option for centralized OpenWrt AP management; web UI, REST API, SSH push
│
├── Client steering: Dawn on OpenWrt nodes (project 01, milestone 6)
│   └── OpenWrt-native (ubus), 802.11k/v/r at Layer 2; runs ON the AP, not on a controller
│
├── Architecture split: OpenWISP = config management, Dawn = real-time steering
│   └── Separation of concerns: policy vs execution
│
└── Resource note: OpenWISP requires Redis, Celery, Django, PostgreSQL
    └── 512 MB RAM is tight but feasible with tuning (single-worker Celery, small shared_buffers)
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/meshwifi_lxc/defaults/main.yml`:
  - `meshwifi_ct_id: 103`, `meshwifi_ct_memory: 512`, `meshwifi_ct_cores: 1`
  - `meshwifi_ct_disk: 4G`, `meshwifi_ct_ip` (static)
  - `meshwifi_ct_onboot: true`, `meshwifi_ct_startup_order: 4`
- [ ] Create `roles/meshwifi_lxc/tasks/main.yml`: include `proxmox_lxc`
- [ ] Register in `meshwifi` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/meshwifi_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install OpenWISP dependencies: Python 3, Redis, PostgreSQL, Nginx
- [ ] Install OpenWISP Controller via pip
- [ ] Template settings: local PostgreSQL, local Redis, single Celery worker, Nginx reverse proxy (self-signed cert)
- [ ] Configure admin user from `.env` (`OPENWISP_ADMIN_USER`, `OPENWISP_ADMIN_PASSWORD`)
- [ ] Set default WiFi template: SSIDs, channel plan, transmit power
- [ ] Configure SSH credentials for managed OpenWrt nodes

### Milestone 3: AP Registration

- [ ] Install `openwisp-config` agent on OpenWrt nodes (add to `openwrt_configure`)
- [ ] Configure agent to point to OpenWISP controller IP
- [ ] Register nodes via API or auto-registration
- [ ] Verify centralized config push: change SSID → propagates to all nodes
- [ ] Ensure Dawn client steering coexists with OpenWISP config management

### Milestone 4: Integration

- [ ] Add `meshwifi_lxc` to `site.yml` targeting `wifi_nodes`
- [ ] Add `meshwifi_configure` play targeting `meshwifi` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Order play AFTER OpenWrt configure

### Milestone 5: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, OpenWISP web UI on port 443
  - PostgreSQL and Redis active
  - At least one AP registered (if test node has WiFi)
- [ ] Extend `molecule/default/cleanup.yml` for container 103

### Milestone 6: Documentation

- [ ] Create `docs/architecture/meshwifi-build.md`
- [ ] Document OpenWISP + Dawn architecture split
- [ ] Add CHANGELOG entry

# Netdata Monitoring Agent

## Overview

A lightweight LXC container running a Netdata child agent that collects
system and container metrics from the Proxmox node. Streams to a Netdata
parent on the home server when the WireGuard tunnel is available. Provides
local dashboards even without the tunnel.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 128 MB
- Disk: 1 GB
- Network: LAN bridge
- VMID: 500

## Startup

- Auto-start: yes
- Boot priority: 3 (alongside Pi-hole and rsyslog)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: yes (all builds get monitoring)

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) -- **soft dependency**: streaming uses tunnel
  when available; Netdata functions fully as local dashboard without it

---

## Architectural Decisions

```
Decisions
├── Monitoring stack: Netdata child-parent streaming
│   └── Richer out-of-box than Prometheus+Grafana; built-in dashboards; child-parent fits remote topology
│
├── Host metrics access: bind mount /proc and /sys read-only into LXC
│   └── Needed for accurate host CPU, memory, disk, temperature; Proxmox API lacks per-interface and thermal data
│
├── WireGuard dependency: soft (optional)
│   └── Functions fully as local dashboard; streaming activates when parent is reachable
│
└── Data retention: minimal on child (dbengine, 1 hour)
    └── Parent handles long-term storage; child is ephemeral
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/netdata_lxc/defaults/main.yml`:
  - `netdata_ct_id: 500`, `netdata_ct_memory: 128`, `netdata_ct_cores: 1`
  - `netdata_ct_disk: 1G`
  - `netdata_ct_onboot: true`, `netdata_ct_startup_order: 3`
- [ ] Create `roles/netdata_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with bind mounts: `/proc` → `/host/proc`, `/sys` → `/host/sys` (read-only)
- [ ] Register in `netdata` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/netdata_configure/tasks/main.yml` (via `pct exec`)
- [ ] Install Netdata via official kickstart script
- [ ] Template `netdata.conf`:
  - Memory mode: `dbengine`, 1-hour retention
  - Web dashboard: listen on LAN IP, port 19999
  - Proc/sys paths: `/host/proc`, `/host/sys`
- [ ] Template `stream.conf` (conditional on `netdata_parent_ip`):
  - Destination: parent via WireGuard tunnel
  - API key from `.env` (`NETDATA_STREAM_API_KEY`)
  - Buffer on disconnect
- [ ] Enable cgroups monitoring for per-container metrics

### Milestone 3: Integration

- [ ] Add `netdata_lxc` to `site.yml` targeting `monitoring_nodes` (combined with `rsyslog_lxc`)
- [ ] Add `netdata_configure` play targeting `netdata` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `NETDATA_STREAM_API_KEY` and `netdata_parent_ip` to `.env` template (optional)

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, Netdata active, dashboard on port 19999
  - Host metrics visible (CPU, memory from /host/proc)
  - Streaming config present if parent IP set
- [ ] Extend `molecule/default/cleanup.yml` for container 500

### Milestone 5: Documentation

- [ ] Create `docs/architecture/netdata-build.md`
- [ ] Document metrics catalog and child-parent streaming
- [ ] Add CHANGELOG entry

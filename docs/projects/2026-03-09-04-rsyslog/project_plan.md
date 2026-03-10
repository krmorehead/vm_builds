# rsyslog

## Overview

A minimal LXC container running rsyslog as a centralized log collector.
All other containers and VMs forward their logs here. rsyslog ships
aggregated logs to the home server over the WireGuard tunnel when
available and buffers locally when the tunnel is down.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 64 MB
- Disk: 1 GB (local buffer for log spooling)
- Network: LAN bridge, static IP
- VMID: 501

## Startup

- Auto-start: yes
- Boot priority: 3 (available early for debugging subsequent deploys)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: yes (all builds get logging)

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) -- **soft dependency**: forwarding uses tunnel
  when available; rsyslog works fully without it (local collection + buffer)

---

## Architectural Decisions

```
Decisions
├── Log collector: rsyslog
│   └── Pre-installed on Debian, minimal footprint (~10 MB RAM), mature
│
├── Transport: TCP 514 with disk-assisted queue
│   └── Reliable delivery without RELP complexity; disk queue handles tunnel outages
│
├── Log format: RFC 5424 structured syslog
│   └── Standard, parseable by any central log server
│
└── WireGuard dependency: soft (optional)
    └── Collects and buffers locally without tunnel; forwarding activates when available
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/rsyslog_lxc/defaults/main.yml`:
  - `rsyslog_ct_id: 501`, `rsyslog_ct_memory: 64`, `rsyslog_ct_cores: 1`
  - `rsyslog_ct_disk: 1G`, `rsyslog_ct_ip` (static)
  - `rsyslog_ct_onboot: true`, `rsyslog_ct_startup_order: 3`
- [ ] Create `roles/rsyslog_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with static IP on LAN bridge
- [ ] Register in `rsyslog` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/rsyslog_configure/tasks/main.yml` (via `pct exec`)
- [ ] rsyslog is pre-installed on Debian; configure only
- [ ] Template `/etc/rsyslog.d/10-receive.conf`:
  - Listen on TCP 514, accept from LAN subnet only
- [ ] Template `/etc/rsyslog.d/20-forward.conf` (conditional on `rsyslog_home_server`):
  - Forward to home server via WireGuard tunnel
  - Disk-assisted queue for reliability during outages
- [ ] Configure logrotate: 7-day retention, compress
- [ ] Add `.env` variable: `RSYSLOG_HOME_SERVER` (optional)

### Milestone 3: Log Client Pattern

Reusable pattern so every future container auto-forwards logs.

- [ ] Add `rsyslog_client_config` variable to `group_vars/all.yml` (container IP + port)
- [ ] Document: each `<type>_configure` role templates a forwarding snippet when variable is defined
- [ ] Implement for OpenWrt: `log_ip` and `log_port` UCI settings
- [ ] Implement for Pi-hole: FTL syslog forwarding

### Milestone 4: Integration

- [ ] Add `rsyslog_lxc` to `site.yml` targeting `monitoring_nodes`
- [ ] Add `rsyslog_configure` play targeting `rsyslog` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Order play after OpenWrt, before most services

### Milestone 5: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, rsyslog active, listening on TCP 514
  - Test log message received from Proxmox host
  - Local spool directory exists
- [ ] Extend `molecule/default/cleanup.yml` for container 501

### Milestone 6: Documentation

- [ ] Create `docs/architecture/rsyslog-build.md`
- [ ] Document log flow and client integration pattern
- [ ] Add CHANGELOG entry

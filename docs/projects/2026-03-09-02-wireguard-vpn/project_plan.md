# WireGuard VPN Client

## Overview

A lightweight LXC container running a WireGuard client that maintains a
persistent VPN tunnel back to the home server. Other services on the node
can route through this tunnel for remote access.

This is the **first LXC container** -- its implementation is the proving
ground for the shared `proxmox_lxc` role.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 128 MB
- Disk: 1 GB
- Network: LAN bridge, WireGuard tunnel interface
- VMID: 101

## Startup

- Auto-start: yes
- Boot priority: 2 (after OpenWrt, before application services)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00, milestone 1)
- OpenWrt router operational (network)
- WireGuard server on home server (external dependency)

---

## Architectural Decisions

```
Decisions
├── LXC base: Debian 12
│   └── Consistent with all other containers; wireguard-tools in official repos
│
└── Configuration method: pct exec from Proxmox host
    └── No SSH, no bootstrap IP; all container commands run directly
```

---

## Milestones

### Milestone 1: LXC Provisioning (First Container)

- [ ] Create `roles/wireguard_lxc/defaults/main.yml`:
  - `wireguard_ct_id: 101`, `wireguard_ct_memory: 128`, `wireguard_ct_cores: 1`
  - `wireguard_ct_disk: 1G`, `wireguard_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `wireguard_ct_onboot: true`, `wireguard_ct_startup_order: 2`
- [ ] Create `roles/wireguard_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific parameters
- [ ] Verify end-to-end: template download → pct create → start → add_host
- [ ] Verify idempotency: re-run skips existing container

### Milestone 2: WireGuard Configuration

- [ ] Create `roles/wireguard_configure/tasks/main.yml` (runs via `pct exec`)
- [ ] Install `wireguard-tools`
- [ ] Add `.env` variables:
  - `WIREGUARD_PRIVATE_KEY`, `WIREGUARD_SERVER_PUBLIC_KEY`
  - `WIREGUARD_SERVER_ENDPOINT`, `WIREGUARD_ALLOWED_IPS`
- [ ] Template `/etc/wireguard/wg0.conf`
- [ ] Enable `wg-quick@wg0` service
- [ ] Configure persistent keepalive (25 seconds) for NAT traversal
- [ ] Enable IP forwarding (`sysctl net.ipv4.ip_forward=1`)
- [ ] Add routing rules so other containers can route through the tunnel

### Milestone 3: Integration

- [ ] Add `wireguard_lxc` provision play to `site.yml` targeting `vpn_nodes`
- [ ] Add `wireguard_configure` play targeting `wireguard` dynamic group
- [ ] Include `deploy_stamp` in the provision play
- [ ] Add `wireguard` dynamic group to `inventory/hosts.yml`
- [ ] Add `wireguard_ct_id: 101` to `group_vars/all.yml`
- [ ] Add WireGuard secrets to `test.env` and `.env` template
- [ ] Update `build.py` docstring with `wireguard` tag

### Milestone 4: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running (`pct status 101`)
  - WireGuard interface `wg0` exists and is up
  - Tunnel has a handshake (or simulated in test env)
  - Container reachable from Proxmox host
- [ ] Extend `molecule/default/cleanup.yml` to destroy container 101

### Milestone 5: Documentation

- [ ] Create `docs/architecture/wireguard-build.md`
- [ ] Add CHANGELOG entry

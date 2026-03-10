# Pi-hole

## Overview

An LXC container running Pi-hole for network-wide DNS-level ad and tracker
blocking. OpenWrt's dnsmasq forwards all DNS queries to Pi-hole. Pi-hole
forwards to encrypted upstream resolvers via Cloudflare DoH.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 256 MB
- Disk: 2 GB (blocklists and query logs)
- Network: LAN bridge, static IP
- VMID: 102

## Startup

- Auto-start: yes
- Boot priority: 3 (DNS should be available early)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- WireGuard VPN (project 02) -- validates LXC patterns first
- OpenWrt router operational (project 01) -- needs LAN connectivity

---

## Architectural Decisions

```
Decisions
├── DNS filtering: Pi-hole
│   └── Established, well-documented, unattended install, large blocklist ecosystem
│
├── LXC base: Debian 12
│   └── Official Pi-hole target; consistent with all other containers
│
├── DNS chain: clients → OpenWrt dnsmasq → Pi-hole → Cloudflare DoH
│   └── OpenWrt handles DHCP and presents single DNS IP; Pi-hole filters; DoH encrypts upstream
│
└── DHCP: disabled in Pi-hole (OpenWrt handles DHCP)
    └── Single DHCP server avoids conflicts; OpenWrt manages pools and VLANs
```

---

## Milestones

### Milestone 1: Provisioning

- [ ] Create `roles/pihole_lxc/defaults/main.yml`:
  - `pihole_ct_id: 102`, `pihole_ct_memory: 256`, `pihole_ct_cores: 1`
  - `pihole_ct_disk: 2G`, `pihole_ct_ip` (static, from group_vars)
  - `pihole_ct_onboot: true`, `pihole_ct_startup_order: 3`
- [ ] Create `roles/pihole_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with static IP on LAN bridge
  - Set DNS to upstream temporarily (NOT Pi-hole's own IP during install)
- [ ] Register in `pihole` dynamic group

### Milestone 2: Configuration

- [ ] Create `roles/pihole_configure/tasks/main.yml` (via `pct exec`)
- [ ] Template `setupVars.conf` for unattended install:
  - Interface, IPv4 address, upstream DNS (Cloudflare 1.1.1.1 + 1.0.0.1)
  - Web admin enabled, password from `.env` (`PIHOLE_WEB_PASSWORD`)
- [ ] Install Pi-hole via official unattended script
- [ ] Add custom blocklists via `pihole -a adlist`
- [ ] Disable Pi-hole DHCP server
- [ ] Set query logging retention to 7 days
- [ ] Set container DNS to `127.0.0.1` after install

### Milestone 3: OpenWrt DNS Forwarding

- [ ] Add `pihole_static_ip` to `group_vars/all.yml`
- [ ] Add conditional task in `openwrt_configure`:
  - Set dnsmasq `server` to Pi-hole IP when `pihole_static_ip` is defined
  - Configure `https-dns-proxy` as fallback when Pi-hole is down
- [ ] Test DNS failover: stop Pi-hole → clients still resolve via fallback

### Milestone 4: Integration

- [ ] Add `pihole_lxc` provision play to `site.yml` targeting `dns_nodes`
- [ ] Add `pihole_configure` play targeting `pihole` dynamic group
- [ ] Include `deploy_stamp` in the provision play
- [ ] Add `pihole` dynamic group to `inventory/hosts.yml`
- [ ] Add `PIHOLE_WEB_PASSWORD` to `test.env` and `.env` template
- [ ] Order play AFTER OpenWrt configure

### Milestone 5: Testing

- [ ] Extend `molecule/default/verify.yml`:
  - Container running, FTL daemon active
  - Web admin responds on port 80
  - DNS query resolves correctly, known ad domain blocked
- [ ] Extend `molecule/default/cleanup.yml` for container 102

### Milestone 6: Documentation

- [ ] Create `docs/architecture/pihole-build.md`
- [ ] Document DNS chain and failover behavior
- [ ] Add CHANGELOG entry

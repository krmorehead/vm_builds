# OpenWrt Router

## Overview

The OpenWrt router VM is the foundation of the network stack. Already
provisioned and configured by the existing `openwrt_vm` and `openwrt_configure`
roles. This project covers hardening, security, and feature additions.

## Type

VM (KVM/QEMU) -- already implemented

## Resources

- Cores: 2
- RAM: 512 MB
- Disk: 512 MB
- Network: all bridges (WAN on eth0/vmbr0, LAN on remaining bridges)
- PCI: WiFi passthrough (when hardware present)
- VMID: 100

## Startup

- Auto-start: yes
- Boot priority: 1 (must start first -- all services depend on network)
- Depends on: Proxmox host only

## Build Profiles

- Home Entertainment Box: yes (core)
- Minimal Router: yes (core)
- Gaming Rig: no

## Prerequisites

- None -- this is the existing foundation

---

## Architectural Decisions

```
Decisions
├── Intrusion prevention: banIP
│   └── OpenWrt-native, lightweight, maintained in official packages
│
├── DNS integration: forward dnsmasq to Pi-hole (project 03)
│   └── Pi-hole handles filtering, OpenWrt handles DHCP. No AdGuard on OpenWrt.
│
├── Encrypted upstream DNS: https-dns-proxy package
│   └── Lightweight DoH proxy for OpenWrt; DoT via stubby is alternative but DoH traverses firewalls better
│
├── VLAN topology
│   ├── VLAN 1 (untagged): management / trusted devices
│   ├── VLAN 10: IoT (restricted internet, no LAN access)
│   └── VLAN 20: guest (internet only, fully isolated)
│
└── Client steering: Dawn (802.11k/v/r)
    └── OpenWrt-native via ubus, real-time RSSI-based steering on each AP node
```

---

## Milestones

### Milestone 1: Security Hardening

- [ ] Set root password from `.env` (`OPENWRT_ROOT_PASSWORD`)
- [ ] Deploy SSH authorized keys, disable password auth
- [ ] Restrict SSH to LAN zone only (drop WAN SSH)
- [ ] Install and configure banIP for intrusion prevention
- [ ] Enable scheduled `opkg upgrade` for security updates
- [ ] Add firewall rules: WAN rate limiting, drop invalid packets, SYN flood protection

### Milestone 2: DNS Integration with Pi-hole

- [ ] Configure dnsmasq to forward to Pi-hole static IP (`pihole_static_ip` variable)
- [ ] Set up `https-dns-proxy` as fallback when Pi-hole is unreachable
- [ ] Add DNS rebinding protection in dnsmasq config
- [ ] Test chain: client → OpenWrt dnsmasq → Pi-hole → DoH upstream
- [ ] Ensure DHCP clients receive OpenWrt as DNS server

### Milestone 3: VLAN Support

- [ ] Configure 802.1Q VLAN tagging on LAN bridge ports
- [ ] Create firewall zones per VLAN with inter-zone rules
- [ ] Set up separate DHCP pools per VLAN (different subnets)
- [ ] Test isolation: IoT cannot reach management VLAN
- [ ] Map mesh WiFi SSIDs to VLANs (IoT SSID → VLAN 10, Guest → VLAN 20)

### Milestone 4: Syslog Forwarding

- [ ] Configure `log_ip` and `log_port` UCI settings → rsyslog container IP
- [ ] Set log level and protocol (TCP 514)
- [ ] Graceful fallback: log locally if rsyslog container not yet deployed

### Milestone 5: Monitoring & Metrics Export

- [ ] Install `prometheus-node-exporter-lua`
- [ ] Export: CPU, memory, bandwidth/interface, WiFi clients, DHCP leases, firewall hits
- [ ] Listen on LAN interface only
- [ ] Document Netdata scrape endpoint

### Milestone 6: Multi-Node Mesh Enhancements

- [ ] Install Dawn on each OpenWrt node for 802.11k/v/r client steering
- [ ] Configure Dawn via UCI: RSSI thresholds, steering behavior
- [ ] Add mesh peer monitoring (detect peer drops, log via syslog)
- [ ] Centralize mesh parameters in `group_vars/all.yml`
- [ ] Test multi-node convergence after node reboot

### Milestone 7: Testing & Documentation

- [ ] Extend `molecule/default/verify.yml`:
  - SSH key auth works, password auth rejected
  - DNS resolution through Pi-hole (when available)
  - banIP service running
  - Monitoring endpoint responds
  - VLAN isolation (if test hardware supports tagged ports)
- [ ] Update `docs/architecture/openwrt-build.md`
- [ ] Add CHANGELOG entry

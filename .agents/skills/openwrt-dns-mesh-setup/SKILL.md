---
name: openwrt-dns-mesh-setup
description: OpenWrt encrypted DNS and mesh configuration patterns. Use when setting up https-dns-proxy, configuring 802.11s mesh networks, or implementing DNS encryption on OpenWrt.
---

# OpenWrt DNS and Mesh Setup Rules

## Encrypted DNS Integration

1. `https-dns-proxy` on OpenWrt auto-configures dnsmasq on install: it adds itself as upstream DNS server and restarts dnsmasq. No manual dnsmasq configuration needed for basic DoH setup.

2. The configure task only needs to:
   - Install `https-dns-proxy` (with retries per network restart rules)
   - Optionally configure specific DoH providers via UCI
   - Verify DNS resolution works through the proxy

## Mesh Networking Setup

3. Mesh satellite nodes use 802.11s mesh for wireless connectivity without requiring PCIe passthrough.

4. The `iw` package must be pre-installed in custom image for namespace-aware WiFi detection via netlink.

## Container Networking Pattern

5. Container networking follows host topology:
   - LAN hosts (`router_nodes`, `lan_hosts`) → OpenWrt LAN subnet, LAN bridge
   - WAN hosts → `proxmox_wan_bridge`, `ansible_default_ipv4` subnet, DNS `8.8.8.8`
   - IP offset +200 for WAN containers to avoid collisions with LAN containers
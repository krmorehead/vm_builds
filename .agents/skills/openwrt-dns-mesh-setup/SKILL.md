---
name: openwrt-dns-mesh-setup
description: OpenWrt encrypted DNS and mesh configuration patterns. Use when setting up https-dns-proxy, configuring WDS WiFi backhaul, or implementing DNS encryption on OpenWrt.
---

# OpenWrt DNS and Mesh Setup Rules

## Encrypted DNS Integration

1. `https-dns-proxy` on OpenWrt auto-configures dnsmasq on install: it adds itself as upstream DNS server and restarts dnsmasq. No manual dnsmasq configuration needed for basic DoH setup.

2. The configure task only needs to:
   - Install `https-dns-proxy` (with retries per network restart rules)
   - Optionally configure specific DoH providers via UCI
   - Verify DNS resolution works through the proxy

## Mesh Networking Setup

3. Mesh satellite nodes use WDS AP/STA for wireless backhaul without requiring PCIe passthrough.

4. The `iw` package must be pre-installed in custom image for namespace-aware WiFi detection via netlink.

## Container Networking Pattern

5. Container networking follows host topology:
   - LAN hosts (`router_nodes`, `lan_hosts`) → OpenWrt LAN subnet (10.10.10.x), LAN bridge
   - WAN hosts → private NAT bridge (`vmbr_ct`), 10.99.{host_id}.{offset}/24, DNS `8.8.8.8`
   - WAN containers are isolated on per-host /24 subnets — no IP collision risk
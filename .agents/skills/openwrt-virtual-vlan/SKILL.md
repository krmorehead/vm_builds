---
name: openwrt-virtual-vlan
description: OpenWrt VLAN configuration in virtual environments using Proxmox bridges. Use when configuring VLANs on virtual OpenWrt deployments, managing virtualized network segmentation, or working with VM-based routers.
---

# OpenWrt Virtual VLAN Configuration

## Virtual Environment vs Physical Differences

1. On physical OpenWrt routers, VLANs use DSA or swconfig for port-based tagging. In a Proxmox VM, OpenWrt has no physical switch — only virtual NICs (`eth0`, `eth1`, etc.) backed by Proxmox bridges.

## Virtual VLAN Pattern

2. VLANs in virtual environment use 802.1Q VLAN devices on bridge ports:
   ```
   eth1 (LAN bridge port)
   ├── eth1.10 (IoT VLAN)
   ├── eth1.20 (Guest VLAN)
   └── eth1.30 (Management VLAN)
   ```

3. Proxmox bridges pass tagged frames by default. No `bridge-vlan-aware` or trunk configuration is needed on Proxmox side — VLAN tagging happens entirely within OpenWrt.

## Virtual vs Physical Constraints

4. NEVER use DSA or swconfig configurations for virtual OpenWrt deployments.
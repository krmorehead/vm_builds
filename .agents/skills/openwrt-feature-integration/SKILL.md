---
name: openwrt-feature-integration
description: OpenWrt feature integration via task files and play patterns. Use when adding new features, creating modular configuration, or organizing OpenWrt configuration tasks.
---

# OpenWrt Feature Integration Patterns

## Task File Structure

1. Post-baseline features (security, VLANs, DNS, mesh) are implemented as separate task files within `roles/openwrt_configure/tasks/`:

```
roles/openwrt_configure/tasks/
├── main.yml          # Baseline configuration (WAN, LAN, DHCP, firewall)
├── security.yml      # M1: SSH hardening, banIP
├── vlans.yml         # M2: VLAN segmentation
├── dns.yml           # M3: Encrypted DNS (https-dns-proxy)
└── mesh.yml          # M4: 802.11s mesh + Dawn steering
```

## Dual Play Pattern

2. Each feature gets TWO plays in `site.yml`:

**Configure play**: targets `openwrt` dynamic group with `include_role` using `tasks_from: <feature>.yml`

**Deploy stamp play**: targets `router_nodes` (Proxmox host) to record feature was applied

3. Both plays share a tag (e.g., `openwrt-security`) so they can be run independently via `--tags`.

## Benefits

4. This pattern avoids re-running baseline tasks when iterating on a feature and enables per-feature molecule scenarios that converge only the relevant task file.
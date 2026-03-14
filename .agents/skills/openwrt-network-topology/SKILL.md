---
name: openwrt-network-topology
description: OpenWrt bridge ordering and WAN detection patterns. Use when managing Proxmox bridges, configuring WAN/LAN interfaces, or debugging OpenWrt network topology issues.
---

# OpenWrt Network Topology Rules

## Bridge Assignment Rules

1. OpenWrt gets ALL bridges: WAN on `net0`/`eth0`, remaining bridges as LAN ports. Most other VMs need only ONE LAN bridge.
2. The WAN bridge is auto-detected by `proxmox_bridges` via the host's default route. NEVER hardcode a bridge as WAN. Override with `openwrt_wan_bridge` in `host_vars` only if auto-detection fails.
3. Order bridges so WAN is always `net0`/`eth0`:
   ```yaml
   _ordered_bridges: [_wan_bridge] + (proxmox_all_bridges | difference([_wan_bridge]) | sort)
   ```

## Bridge Ordering Bug Prevention

4. Previous bug: alphabetical bridge sorting made `vmbr0` always WAN. When the modem was on `vmbr0`, the Proxmox GUI became unreachable from LAN nodes.
5. NEVER sort bridges alphabetically. Always use explicit ordering with WAN first.

## Auto-Subnet Selection

6. To avoid collisions between WAN subnet and OpenWrt LAN subnet:
   - Detect upstream gateway prefix from Proxmox host's default route
   - Pass it to OpenWrt via `add_host` as `upstream_wan_prefix`
   - Iterate `openwrt_lan_subnet_candidates` and pick first whose prefix differs from WAN

## Proxmox Management IP

7. When OpenWrt is primary router, Proxmox host needs predictable IP on LAN bridge:
   - Compute LAN IP from OpenWrt's LAN subnet + offset (default `.2`)
   - `ip addr add` on LAN bridge (immediate, current session)
   - Upgrade LAN bridge in `ansible-bridges.conf` from `inet manual` to `inet dhcp`
   - Add DHCP static reservation mapping Proxmox host's LAN bridge MAC to computed IP
   - On reboot: DHCP client requests IP, OpenWrt assigns reserved one

8. NEVER use separate config file with `iface <bridge> inet dhcp`. It conflicts with `inet manual` stanza in bridges config.

9. NEVER leave stale IPs on non-LAN bridges in same subnet as LAN bridge. Two routes for same /24 on different bridges causes kernel to use wrong interface, breaking LAN VM connectivity.

## Bootstrap Connectivity Pattern

10. To reach OpenWrt at default `192.168.1.1` during initial setup:
    - Add temporary IP (`192.168.1.2`) to WAN bridge on Proxmox
    - SSH through ProxyJump via Proxmox host
    - After Phase 1 network restart, OpenWrt's LAN moves to non-WAN bridges
    - Remove bootstrap IP from WAN bridge, add to LAN bridge
    - After Phase 2, clean up bootstrap IP entirely

11. Previous bug: excluded `vmbr0` and tried connecting through `vmbr1` (which had no IP in OpenWrt's default config).
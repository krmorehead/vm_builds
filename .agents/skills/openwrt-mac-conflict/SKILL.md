---
name: openwrt-mac-conflict
description: OpenWrt WAN MAC address conflict detection and deferred application. Use when managing MAC addresses, cloning WAN MACs, or debugging network connectivity issues.
---

# OpenWrt MAC Conflict Detection Rules

## MAC Application Process

1. The `WAN_MAC` env variable is optional. NEVER apply it at the Proxmox NIC level during VM creation (`qm set --net0 macaddr=...`). ALWAYS go through the MAC conflict detection flow during the final configure phase.

2. If no conflict is detected, apply via UCI (`uci set network.wan.macaddr`). If a conflict IS detected, defer the MAC to `/etc/openwrt_wan_mac_deferred` on the VM. An init script auto-applies it on next boot when conflict is gone.

## Three-Layer Conflict Detection

3. Before applying a cloned WAN MAC, run a three-layer conflict check:

**Layer 1: Exact MAC in ARP table** (`/proc/net/arp`): catches direct L2 duplicates

**Layer 2: EUI-64 in IPv6 neighbor table** (`ip -6 neigh`): catches SLAAC address collisions where MAC itself isn't visible but its derived IPv6 address is

**Layer 3: Gateway OUI match**: if the WAN gateway's MAC shares the first 3 bytes (OUI) with the cloned MAC, the devices are almost certainly from the same router — a conflict waiting to happen

4. If any check triggers, save MAC to `/etc/openwrt_wan_mac_deferred` and NOT apply to UCI. Deploy `/etc/init.d/wan_mac_apply` — an OpenWrt init script (START=99) that runs on every boot.

## Deferred MAC Application

5. The init script re-runs the same three-layer conflict detection and, if conflict is gone (old router removed), applies the MAC via UCI and restarts network automatically. No manual intervention required.

6. The deferred file is cleaned at start of each run (`rm -f`) for idempotency. VM destruction during cleanup also removes it. The init script self-cleans by removing deferred file after applying MAC.

## MAC Conflict Bug Prevention

7. Duplicate MAC addresses on same L2 segment cause IPv6 DAD failures, corrupt uclient/libubox state, and cause `wget`/`opkg` segfaults — even when ICMP ping works.

8. Consumer routers use sequential MACs across ports — the WAN MAC and LAN MAC often share same OUI and differ by ±1 in last byte.

9. Previous bug: WAN MAC `08:B4:B1:1A:63:08` was applied while old router (LAN MAC `08:B4:B1:1A:63:09`, same OUI) was still on segment. IPv6 SLAAC generated same global address → DAD failure → corrupted network stack → `wget` EPERM, `opkg` failures, DNS timeouts. ICMP still worked, making root cause non-obvious without `dmesg` diagnostics.
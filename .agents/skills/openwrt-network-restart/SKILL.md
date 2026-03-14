---
name: openwrt-network-restart
description: OpenWrt network restart patterns and detached script execution. Use when managing OpenWrt network services, firewall restarts, or handling SSH connectivity during network changes.
---

# OpenWrt Network Restart Rules

## Two-Phase Restart Pattern

1. OpenWrt needs two network restarts because LAN IP changes mid-run.

**Phase 1** (WAN + LAN ports, keep default LAN IP):
   1. Configure WAN device, LAN bridge ports via UCI
   2. `uci commit` → detached script (firewall → dnsmasq → network → firewall → dropbear)
   3. Pause 30s for services to stabilize
   4. Migrate bootstrap IP from WAN bridge to LAN bridge on Proxmox
   5. Wait for SSH on LAN bridge, wait for WAN default route
   6. Restart firewall again (zone rebinding after interface change)
   7. Install packages (opkg) while connectivity works

**Phase 2** (final LAN IP + DHCP + WAN MAC):
   1. Set final LAN IP, netmask, DHCP params, and WAN MAC (if configured) via UCI
   2. `uci commit` → detached script (firewall → dnsmasq → network → firewall → dropbear)
   3. Pause 30s, clean up bootstrap IP

2. The split is necessary because changing the LAN IP in Phase 1 would break SSH mid-configure.

## Detached Restart Script Pattern

3. Use detached script pattern to survive SSH disconnects caused by network restarts:
   ```bash
   printf '#!/bin/sh\nsleep 1\n/etc/init.d/firewall restart\n/etc/init.d/dnsmasq restart\nsleep 2\n/etc/init.d/network restart\nsleep 5\n/etc/init.d/firewall restart\n/etc/init.d/dropbear restart\nrm -f /tmp/_restart_net.sh\n' \
     > /tmp/_restart_net.sh && chmod +x /tmp/_restart_net.sh && \
     start-stop-daemon -S -b -x /tmp/_restart_net.sh
   ```

4. Detached scripts report "success" when they launch — NOT when they complete. NEVER trust the launch result.

## Critical Restart Ordering

5. ALWAYS restart services in order: `firewall` → `dnsmasq` → `network` → `firewall` → `dropbear`. The first firewall/dnsmasq restart prepares for topology change; the second firewall restart rebinds zones after interface changes.

6. NEVER restart the firewall synchronously over SSH when WAN zone rules have changed. The firewall applies WAN zone rules (input REJECT) to the current SSH path, killing the connection. ALWAYS use detached scripts with `ignore_unreachable: true`.

7. After ANY network restart that changes interface assignments, ALWAYS restart the firewall before attempting outbound connections. Firewall zone bindings go stale when interfaces change.

## Network Operation Retries

8. Network operations (`wget`, DNS lookups) MUST have `retries` + `delay`. DNS, DHCP, and firewall state take seconds to settle after a restart.

## Detached Script Verification

9. ALWAYS verify expected outcome after pause:
   - `wait_for` on SSH port (proves dropbear restarted)
   - `wait_for` on WAN default route (proves network restarted)
   - Firewall restart task (proves firewall can be restarted = it's running)

10. If detached script fails silently, verification steps catch it.

11. Previous bug: detached script launched successfully but firewall restart inside failed. Pause completed, subsequent tasks got EPERM because firewall zones were stale. Verification pattern would have caught or self-healed this.

12. Previous bug: script omitted `firewall restart` → stale zones → `opkg update` got `EPERM`.

13. Previous bug: Firewall + dnsmasq were restarted synchronously before detached script → SSH connection killed because WAN zone rules (input REJECT) were applied to bootstrap SSH path.
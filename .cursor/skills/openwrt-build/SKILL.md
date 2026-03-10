---
name: openwrt-build
description: OpenWrt VM provisioning and configuration patterns. Use when modifying openwrt_vm or openwrt_configure roles, debugging OpenWrt network issues, working with UCI, opkg, firewall zones, WAN/LAN bridge ordering, bootstrap connectivity, or the two-phase restart pattern.
---

# OpenWrt Build Patterns

## Context

OpenWrt is a router VM — it consumes ALL Proxmox bridges (WAN + every LAN port) and controls network topology for the entire host. This makes it fundamentally different from service VMs that attach to a single LAN bridge. These patterns are specific to OpenWrt and should not be applied to other VM types.

## Rules

1. OpenWrt gets ALL bridges: WAN on `net0`/`eth0`, remaining bridges as LAN ports. Most other VMs need only ONE LAN bridge.
2. The WAN bridge is auto-detected by `proxmox_bridges` via the host's default route. NEVER hardcode a bridge as WAN. Override with `openwrt_wan_bridge` in `host_vars` only if auto-detection fails.
3. After ANY network restart that changes interface assignments, ALWAYS restart the firewall before attempting outbound connections. Firewall zone bindings go stale when interfaces change.
4. Network operations (`opkg update`, `wget`) MUST have `retries` + `delay`. DNS, DHCP, and firewall state take seconds to settle after a restart.
5. Detached restart scripts MUST restart services in order: `network` → `firewall` → `dropbear`. Omitting `firewall` causes `EPERM` on outbound traffic.
6. Switch opkg feeds from HTTPS to HTTP (`sed -i 's|https://|http://|g'`) before `opkg update` — the base image lacks TLS certificates.
7. The `WAN_MAC` env variable is optional. When set, the MAC is applied to `net0` at the Proxmox level (`qm set --net0 virtio,...,macaddr=XX:XX:XX:XX:XX:XX`), not inside OpenWrt. Omit for auto-generated MACs.

## WAN/LAN bridge ordering

`openwrt_vm` orders bridges so the WAN bridge is always `net0`/`eth0`:

```yaml
_ordered_bridges: [_wan_bridge] + (proxmox_all_bridges | difference([_wan_bridge]) | sort)
```

Previous bug: alphabetical bridge sorting made `vmbr0` always WAN. When the modem was on `vmbr0`, the Proxmox GUI became unreachable from LAN nodes.

## Two-phase restart pattern

OpenWrt needs two network restarts because the LAN IP changes mid-run:

**Phase 1** (WAN + LAN ports, keep default LAN IP):
1. Configure WAN device, LAN bridge ports via UCI
2. `uci commit` → restart firewall → detached network restart
3. Wait for SSH on LAN bridge, wait for WAN default route
4. Restart firewall again (zone rebinding after interface change)
5. Install packages (opkg) while connectivity works

**Phase 2** (final LAN IP + DHCP):
1. Set final LAN IP, netmask, DHCP params via UCI
2. `uci commit` → restart firewall → detached network restart
3. Clean up bootstrap IP

The split is necessary because changing the LAN IP in Phase 1 would break SSH mid-configure.

## Detached restart scripts

The detached script pattern survives SSH disconnects caused by network restarts:

```bash
printf '#!/bin/sh\nsleep 3\n/etc/init.d/network restart\nsleep 5\n/etc/init.d/firewall restart\n/etc/init.d/dropbear restart\nrm -f /tmp/_restart_net.sh\n' \
  > /tmp/_restart_net.sh && chmod +x /tmp/_restart_net.sh && \
  start-stop-daemon -S -b -x /tmp/_restart_net.sh
```

Previous bug: the script omitted `firewall restart`. After interfaces changed, firewall zones were stale. `opkg update` got `EPERM` because outbound traffic wasn't recognized as belonging to the WAN zone.

## Bootstrap connectivity

To reach OpenWrt at its default `192.168.1.1` during initial setup:

1. Add a temporary IP (`192.168.1.2`) to the WAN bridge on Proxmox
2. SSH through ProxyJump via the Proxmox host
3. After Phase 1 network restart, OpenWrt's LAN moves to non-WAN bridges
4. Remove bootstrap IP from WAN bridge, add to LAN bridge
5. After Phase 2, clean up bootstrap IP entirely

Previous bug: we excluded `vmbr0` and tried to connect through `vmbr1` (which had no IP in OpenWrt's default config).

## Proxmox LAN management IP

When OpenWrt is the primary router, the Proxmox host needs a static IP on the LAN bridge so the GUI is reachable from leaf nodes:

1. Compute LAN IP from OpenWrt's LAN subnet + offset (default `.2`)
2. `ip addr add` on the LAN bridge (immediate)
3. Persist to `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (survives reboot)
4. Write `.state/addresses.json` with both the management IP and the new LAN IP
5. Probe original management IP — if unreachable (topology changed), update `ansible_host` via `add_host`

## Auto-subnet selection

To avoid collisions between the WAN subnet and the OpenWrt LAN subnet:

1. Detect the upstream gateway prefix from the Proxmox host's default route
2. Pass it to OpenWrt via `add_host` as `upstream_wan_prefix`
3. Iterate `openwrt_lan_subnet_candidates` and pick the first whose prefix differs from WAN

## State file for cross-run IP discovery

`build.py` probes `PROXMOX_HOST` before running Ansible. If unreachable, it reads `.state/addresses.json` for cached alternative IPs. This handles cable-swap scenarios where the original management IP is no longer routable.

The state file is written by `openwrt_configure` and cleaned by both cleanup playbooks. It is gitignored.

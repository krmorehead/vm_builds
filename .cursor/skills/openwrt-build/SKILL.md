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
5. Detached restart scripts MUST restart services in order: `firewall` → `dnsmasq` → `network` → `firewall` → `dropbear`. The first firewall/dnsmasq restart prepares for the topology change; the second firewall restart rebinds zones after interface changes.
6. NEVER restart the firewall synchronously over SSH when WAN zone rules have changed. The firewall applies WAN zone rules (input REJECT) to the current SSH path, killing the connection. ALWAYS use detached scripts with `ignore_unreachable: true`.
7. Switch opkg feeds from HTTPS to HTTP (`sed -i 's|https://|http://|g'`) before `opkg update` — the base image lacks TLS certificates.
8. The `WAN_MAC` env variable is optional. NEVER apply it at the Proxmox NIC level during VM creation (`qm set --net0 macaddr=...`). ALWAYS go through the MAC conflict detection flow during the final configure phase. If no conflict is detected, apply via UCI (`uci set network.wan.macaddr`). If a conflict IS detected, defer the MAC to `/etc/openwrt_wan_mac_deferred` on the VM. An init script auto-applies it on the next boot when the conflict is gone.
9. Duplicate MAC addresses on the same L2 segment cause IPv6 DAD failures, corrupt uclient/libubox state, and cause `wget`/`opkg` segfaults — even when ICMP ping works. Consumer routers use sequential MACs across ports — the WAN MAC and LAN MAC often share the same OUI and differ by ±1 in the last byte.
10. BusyBox `ip neigh show` does NOT support IP filter arguments like full iproute2. ALWAYS use `/proc/net/arp` with `awk` to look up gateway MACs on OpenWrt. Similarly, avoid `ip -o`, `grep -oP`, and `grep -E` on OpenWrt.
11. BusyBox `tr -d '[:space:]'` deletes colons (`:`) because BusyBox treats `[:space:]` as a character set containing `[`, `:`, `s`, `p`, `a`, `c`, `e`, `]` — NOT as a POSIX character class. ALWAYS use explicit chars: `tr -d ' \t\n\r'`.
12. BusyBox `nc` does NOT support `-w` (timeout) flag. Use `(echo QUIT | nc HOST PORT) </dev/null` for TCP port checks. NEVER use `echo | nc -w 3` on OpenWrt.
13. When checking for the default route in scripts on OpenWrt, NEVER filter by device name (`ip route show default dev eth0`). OpenWrt's netifd may use interface aliases (e.g., `wan`, `eth0.2`) that differ from the physical device name. Use `ip route show default` without a device filter.

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

The split is necessary because changing the LAN IP in Phase 1 would break SSH mid-configure.

## Detached restart scripts

The detached script pattern survives SSH disconnects caused by network restarts:

```bash
printf '#!/bin/sh\nsleep 1\n/etc/init.d/firewall restart\n/etc/init.d/dnsmasq restart\nsleep 2\n/etc/init.d/network restart\nsleep 5\n/etc/init.d/firewall restart\n/etc/init.d/dropbear restart\nrm -f /tmp/_restart_net.sh\n' \
  > /tmp/_restart_net.sh && chmod +x /tmp/_restart_net.sh && \
  start-stop-daemon -S -b -x /tmp/_restart_net.sh
```

Previous bugs:
- Script omitted `firewall restart` → stale zones → `opkg update` got `EPERM`.
- Firewall + dnsmasq were restarted synchronously before the detached script → SSH connection killed because WAN zone rules (input REJECT) were applied to the bootstrap SSH path.

## Bootstrap connectivity

To reach OpenWrt at its default `192.168.1.1` during initial setup:

1. Add a temporary IP (`192.168.1.2`) to the WAN bridge on Proxmox
2. SSH through ProxyJump via the Proxmox host
3. After Phase 1 network restart, OpenWrt's LAN moves to non-WAN bridges
4. Remove bootstrap IP from WAN bridge, add to LAN bridge
5. After Phase 2, clean up bootstrap IP entirely

Previous bug: we excluded `vmbr0` and tried to connect through `vmbr1` (which had no IP in OpenWrt's default config).

## Proxmox LAN management IP

When OpenWrt is the primary router, the Proxmox host needs a predictable IP on the LAN bridge so the GUI is reachable from leaf nodes:

1. Compute LAN IP from OpenWrt's LAN subnet + offset (default `.2`)
2. `ip addr add` on the LAN bridge (immediate, current session)
3. Upgrade the LAN bridge in `ansible-bridges.conf` from `inet manual` to `inet dhcp`
4. Add a DHCP static reservation on OpenWrt mapping the Proxmox host's LAN bridge MAC to the computed IP
5. On reboot: the DHCP client on the LAN bridge requests an IP, OpenWrt always assigns the reserved one
6. Remove any stale LAN-subnet IPs from non-LAN bridges to prevent routing conflicts
7. Remove any separate `ansible-proxmox-lan.conf` (superseded by bridges.conf DHCP)
8. Write `.state/addresses.json` with both the management IP and the new LAN IP
9. Probe original management IP — if unreachable (topology changed), update `ansible_host` via `add_host`

NEVER use a separate config file with `iface <bridge> inet dhcp` — it conflicts with the `inet manual` stanza in the bridges config and `ifreload -a` won't start the DHCP client. ALWAYS modify the bridge stanza in-place.

NEVER leave stale IPs on non-LAN bridges in the same subnet as the LAN bridge. Two routes for the same /24 on different bridges causes the kernel to use the wrong interface, breaking all LAN VM connectivity.

## Auto-subnet selection

To avoid collisions between the WAN subnet and the OpenWrt LAN subnet:

1. Detect the upstream gateway prefix from the Proxmox host's default route
2. Pass it to OpenWrt via `add_host` as `upstream_wan_prefix`
3. Iterate `openwrt_lan_subnet_candidates` and pick the first whose prefix differs from WAN

## State file for cross-run IP discovery

`build.py` probes `PROXMOX_HOST` before running Ansible. If unreachable, it reads `.state/addresses.json` for cached alternative IPs. This handles cable-swap scenarios where the original management IP is no longer routable.

The state file is written by `openwrt_configure` and cleaned by both cleanup playbooks. It is gitignored.

## WAN MAC conflict detection

Before applying a cloned WAN MAC, the build runs a three-layer conflict check:

1. **Exact MAC in ARP table** (`/proc/net/arp`): catches direct L2 duplicates
2. **EUI-64 in IPv6 neighbor table** (`ip -6 neigh`): catches SLAAC address collisions where the MAC itself isn't visible but its derived IPv6 address is
3. **Gateway OUI match**: if the WAN gateway's MAC shares the first 3 bytes (OUI) with the cloned MAC, the devices are almost certainly from the same router — a conflict waiting to happen

If any check triggers, the MAC is saved to `/etc/openwrt_wan_mac_deferred` and
NOT applied to UCI. The build also deploys `/etc/init.d/wan_mac_apply` — an
OpenWrt init script (START=99) that runs on every boot. It re-runs the same
three-layer conflict detection and, if the conflict is gone (old router
removed), applies the MAC via UCI and restarts the network automatically. No
manual intervention required.

Previous bug: WAN MAC `08:B4:B1:1A:63:08` was applied while the old router
(LAN MAC `08:B4:B1:1A:63:09`, same OUI) was still on the segment. IPv6 SLAAC
generated the same global address → DAD failure → corrupted network stack →
`wget` EPERM, `opkg` failures, DNS timeouts. ICMP still worked, making the
root cause non-obvious without `dmesg` diagnostics.

The deferred file is cleaned at the start of each run (`rm -f`) for
idempotency. The VM destruction during cleanup also removes it. The init
script self-cleans by removing the deferred file after applying the MAC.

## Permanent diagnostics

The OpenWrt build includes diagnostic tasks at two key milestones:

**Bootstrap diagnostics** (`openwrt_vm`, after SSH bootstrap):
- VM status, bridge layout, bootstrap IP presence, dmesg errors

**Phase 1 diagnostics** (`openwrt_configure`, after WAN route + firewall restart):
- WAN route, WAN IP, LAN IP, DNS resolvers, firewall status, dmesg errors

**Final diagnostics** (`openwrt_configure`, end of build):
- VM status, onboot/startup config, LAN bridge IP, management config presence

These run on EVERY build. When a build fails, the diagnostic output from the
last successful milestone narrows the failure window.

## Detached script verification

Detached scripts report "success" when they launch — NOT when they complete.
NEVER trust the launch result. ALWAYS verify the expected outcome after the
pause:

1. `wait_for` on SSH port (proves dropbear restarted)
2. `wait_for` on WAN default route (proves network restarted)
3. Firewall restart task (proves firewall can be restarted = it's running)

If the detached script fails silently, these verification steps catch it.

Previous bug: detached script launched successfully but the firewall restart
inside it failed. The pause completed, and subsequent tasks got EPERM because
the firewall zones were stale. The verification pattern (`wait_for` + explicit
firewall restart) would have caught or self-healed this.

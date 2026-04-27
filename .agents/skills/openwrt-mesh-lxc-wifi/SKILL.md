---
name: openwrt-mesh-lxc-wifi
description: OpenWrt Mesh LXC container WiFi PHY management and namespace handling. Use when setting up mesh nodes, managing WiFi radios, or troubleshooting LXC container networking.
---

# OpenWrt Mesh LXC WiFi Management

## Container Setup Requirements

1. Mesh satellite nodes (`wifi_nodes:!router_nodes`) run OpenWrt in a **privileged LXC container** instead of a VM. This allows WiFi management via WDS AP/STA without requiring PCIe passthrough (IOMMU/VT-d).

2. Key differences from VM pattern:
   - No routing — mesh containers are NOT routers. They run WDS STA only
   - Uses OpenWrt rootfs tarball (`openwrt-*-rootfs.tar.gz`), not VM disk image
   - Must be privileged (`unprivileged: false`) for PHY namespace move
   - Must set `--ostype unmanaged` because Proxmox cannot auto-detect OpenWrt
   - Container readiness uses `ls /` (not `hostname`, which is absent in BusyBox)
   - `lxc_ct_skip_debian_cleanup: true` to avoid dpkg operations on OpenWrt
   - Proxmox hookscript re-moves WiFi PHY after container restarts

## WiFi PHY Namespace Management

3. The container receives host's WiFi PHY via `iw phy <phy> set netns <pid>` (network namespace move).

4. Load common WiFi kernel modules (`iwlwifi`, `ath9k`, etc.) on host BEFORE container creation and PHY move.

5. Detect PHYs in `/sys/class/ieee80211/`. If no PHYs found, hard-fail. All `wifi_nodes` are expected to have WiFi. Missing WiFi usually means stale vfio-pci bindings from previous run or missing firmware.

## WiFi Detection Patterns

6. **IMPORTANT:** Detect WiFi radios inside LXC containers with `iw phy` (netlink), NOT `ls /sys/class/ieee80211/` (sysfs). LXC containers bind-mount host's sysfs, which doesn't reflect network-namespace-specific entries like WiFi PHYs.

7. `iw phy` queries kernel via netlink and correctly sees PHYs moved into container's network namespace. The `iw` package must be pre-installed in custom image or via `opkg install iw`.

8. Previous bug: `ls /sys/class/ieee80211/` inside container returned empty despite successful `iw phy set netns` — sysfs showed host's view.

## Module Loading Constraints

9. **NEVER `modprobe` WiFi modules inside container via `pct_remote`.** `modprobe` inside container runs on HOST kernel (containers share kernel). If module reloads, new PHY appears in HOST namespace, not container namespace — effectively un-doing PHY namespace move.

10. Previous bug: `modprobe iwlwifi` inside container via `pct_remote` caused PHY to revert to host namespace. WiFi detection inside container then found zero radios despite successful namespace move.

## VFIO Binding Cleanup

11. `proxmox_pci_passthrough` cleans stale vfio bindings on non-router hosts. If WiFi was previously bound to vfio-pci, the role removes `blacklist-wifi.conf` and `vfio-pci.conf`, unbinds devices, and reloads drivers.

12. Previous bug: mesh1 WiFi was bound to vfio-pci from prior test cycle. `/sys/class/ieee80211/` was empty despite hardware being present.

## UCI Wireless Configuration

13. After WiFi PHY is namespace-moved into container, OpenWrt does NOT auto-generate `/etc/config/wireless`. The configure role MUST run `wifi config` inside container to generate wireless configuration from detected hardware BEFORE any `uci set wireless.radio*` commands.

14. Previous bug: `uci set wireless.radio0.disabled=0` failed on both mesh1 and mesh2. PHY was detected by `iw phy` (found `phy0`), but UCI wireless config had no matching `radio0` section because PHY was moved into namespace after container booted.

## WiFi Band Selection and Cross-Endpoint Negotiation

15. Bridge WiFi parameters (band, channel, htmode) are selected dynamically via
    cross-endpoint negotiation. The `scripts/wifi_negotiate.py` module receives
    capabilities from both AP and STA endpoints and computes the optimal shared
    config. NEVER hardcode band/channel for bridge containers.

16. The negotiation play in `site.yml` runs between provision and configure:
    - Collects capabilities from each bridge container via `wifi_setup.sh capabilities`
    - Runs `wifi_negotiate.py` on the controller to compute shared band/channel/htmode
    - Stores results as `wifi_negotiated_*` facts on bridge hosts
    - The configure role reads these facts; falls back to `auto` for standalone scenarios

17. Intel AX210 `iwlwifi` self-managed regulatory (LAR) blocks 5GHz AP mode
    (PASSIVE-SCAN on all 5GHz channels). The `lar_disable` module parameter does
    NOT exist in kernel 6.17+. However, 6GHz channels ARE AP-capable with
    self-managed regulatory. A sysfs rebind (`tasks/sysfs_wifi_rebind.yml`)
    in `proxmox_pci_passthrough` reinitializes the firmware regulatory state,
    enabling AP-capable 6GHz channels. NEVER use `modprobe -r` — use sysfs
    unbind + PCI rescan + explicit bind instead.

18. `wifi_setup.sh` now has a `capabilities` subcommand that outputs structured
    `KEY=value` data: supported bands, AP-capable channels, DFS channels, maximum
    widths, HE/VHT support, WDS support, and WPA3 support. This output is parsed
    by `wifi_negotiate.py` via `parse_capabilities()`.

19. Band priority: 6GHz > 5GHz > 2.4GHz. The negotiation selects the highest
    common band with AP-capable channels on both endpoints. 2.4GHz width is
    capped at 40MHz. Non-DFS channels are preferred.

20. Performance tuning is applied automatically during `wifi_setup.sh configure`:
    WiFi power save disabled, coexistence scanning disabled (`noscan=1`), DTIM
    period set to 3 for WDS. These are critical for dedicated backhaul links.

21. Previous bug: AX210 5GHz AP mode failed with PASSIVE-SCAN channels (LAR).
    5GHz remains blocked by self-managed regulatory. Negotiation correctly falls
    through to 6GHz (AP-capable) or 2.4GHz (always AP-capable). Module reload
    enables 6GHz AP channels on AX210.

22. Mesh containers (non-bridge) auto-detect correctly because their hardware
    (Centrino N 105, etc.) typically only supports 2.4GHz. The `wifi_setup.sh`
    dynamic probing handles this without negotiation.

## L2 Bridge Loop Hazard (CRITICAL)

19. **NEVER put both ends of a WiFi bridge on the same L2 broadcast domain.** When bridge-1 (AP) and bridge-2 (STA) containers both have eth0 on vmbr0 (same physical switch), the WiFi link creates a second L2 path between them, forming a broadcast storm loop:

```
bridge-1 vmbr0 → CT eth0 → br-lan → WiFi AP
    ~~~~ WiFi link ~~~~
bridge-2 WiFi STA → br-lan → CT eth0 → vmbr0 → switch → back to bridge-1
```

Every broadcast frame (ARP, DHCP, mDNS) loops infinitely, saturating the entire household LAN and WiFi. STP on OpenWrt's `br-lan` does NOT prevent this because the loop spans multiple external bridges (two Proxmox vmbr0 instances + physical switch) that don't participate in the same STP domain.

20. The correct bridge container topology depends on `wifi_role`:
    - **AP mode** (bridge-1): eth0 on `proxmox_wan_bridge` (vmbr0) — extends the household network over WiFi
    - **STA mode** (bridge-2): eth0 on the **backhaul bridge** (vmbr1) — receives WiFi traffic and outputs to the physical cable toward mesh2

This ensures no L2 loop: the WiFi link is the ONLY path between vmbr0 (via bridge-1) and vmbr1 (via bridge-2).

21. Previous catastrophe (2026-04-09): Agent changed bridge WiFi from non-functional 5GHz (channel 1 invalid) to working 2.4GHz channel 11. Both containers were on vmbr0 (same switch). The WiFi link completed the L2 loop, causing a broadcast storm that took down the entire household WiFi network. All units had to be powered off for recovery. The non-functional 5GHz config had been accidentally preventing the loop.

## Bridge Container Network Assignment

22. The `openwrt_bridge_lxc` provisioning role MUST assign the container bridge based on `wifi_role`:
    - `wifi_role: ap` → `proxmox_wan_bridge` (vmbr0)
    - `wifi_role: sta` → backhaul bridge (vmbr1, detected from non-management USB NIC)
    - If no backhaul bridge exists for STA mode → hard-fail (the architecture requires a physical cable)

23. NEVER use `bridge link show dev <iface> master <bridge>` exit code alone to detect bridge membership. The command returns exit code 0 with EMPTY output when the interface is NOT a member. ALWAYS check that the output is non-empty: `output=$(bridge link show dev "$iface" master "$br" 2>/dev/null); if [ -n "$output" ]; then ...`.
    - Previous catastrophe (2026-04-09): The bridge detection loop checked only exit code. `bridge link show dev enx00e04c68007a master vmbr0` returned 0 (empty output), so the loop matched vmbr0 (the FIRST bridge checked) instead of vmbr1 (the actual member). Bridge-2 STA container was provisioned on vmbr0. When WiFi associated, the L2 loop formed and the household network went down a second time.

## Safety Gates (MANDATORY)

24. The provisioning role includes three safety gates that MUST NOT be removed:
    - **Pre-provision gate**: If `wifi_role=sta` and `_backhaul_bridge == proxmox_wan_bridge`, hard-fail immediately. STA on the WAN bridge = guaranteed broadcast storm.
    - **Post-provision gate**: After `proxmox_lxc` creates the container, read back the actual `pct config` bridge assignment. If STA container is on the WAN bridge, stop the container and hard-fail. This catches bugs in detection logic or the LXC provisioning helper.
    - **Runtime storm guard**: A systemd service (`bridge-storm-guard.service`) monitors the WAN bridge multicast packet rate. If >500 packets/sec sustained for 3 checks (6 seconds), it stops the bridge container and exits. This is the last line of defense against loops caused by any mechanism.
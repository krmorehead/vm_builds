---
name: openwrt-mesh-lxc-wifi
description: OpenWrt Mesh LXC container WiFi PHY management and namespace handling. Use when setting up mesh nodes, managing WiFi radios, or troubleshooting LXC container networking.
---

# OpenWrt Mesh LXC WiFi Management

## Container Setup Requirements

1. Mesh satellite nodes (`wifi_nodes:!router_nodes`) run OpenWrt in a **privileged LXC container** instead of a VM. This allows WiFi management via 802.11s mesh without requiring PCIe passthrough (IOMMU/VT-d).

2. Key differences from VM pattern:
   - No routing — mesh containers are NOT routers. They run 802.11s only
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
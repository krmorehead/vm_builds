---
name: openwrt-image-builder
description: OpenWrt Image Builder patterns and custom image creation. Use when building OpenWrt images, managing packages, or configuring pre-installed software.
---

# OpenWrt Image Builder Rules

## Custom Image Requirement

1. Per the project's "Bake, don't configure at runtime" principle: all packages are in the custom image. Configure roles NEVER run `opkg install`. To add a package, update `build-images.sh` and rebuild.

2. Custom images are REQUIRED, there is no fallback, and configure roles NEVER run `opkg install`.

## Image Builder Workflow

3. The project uses OpenWrt Image Builder to create pre-configured images with packages pre-installed and sane defaults baked in. This eliminates EPERM/opkg failures during converge and significantly speeds up configuration.

4. Build: `./build-images.sh` (downloads Image Builder once, caches in `.image-builder-cache/`). Use `--clean` to force re-download.

## Image Types

5. Two images are produced:

**Mesh LXC rootfs** (`openwrt-mesh-lxc-*-rootfs.tar.gz`):
   - WiFi packages pre-installed (`wpad-mesh-openssl`, `iw`, `kmod-iwlwifi`, `kmod-mt76`, `kmod-ath9k`, `kmod-ath10k-ct`)
   - `iw` included for namespace-aware WiFi detection via netlink
   - Firewall stripped (`-firewall4`, `-nftables`)
   - No routing (`-dnsmasq`, `-ppp`, `-odhcpd-ipv6only`)
   - UCI defaults: `eth0` on DHCP, no WAN, no IPv6, HTTP opkg feeds

**Router VM image** (`openwrt-router-*-combined.img.gz`):
   - WiFi mesh packages pre-installed
   - Security packages (`banip`), DNS packages (`https-dns-proxy`), mesh steering (`dawn`), diagnostics (`curl`, `ip-full`, `tcpdump`)
   - No UCI defaults baked in — `openwrt_configure` handles all config dynamically based on detected topology

## Package Management Pattern

6. To add a package, add it to `build-images.sh` and rebuild. NEVER add packages during configure runs.
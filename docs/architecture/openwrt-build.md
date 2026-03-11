# OpenWrt Build: Requirements and Design

## Intent

Replace a consumer home router with a virtualized OpenWrt instance running on Proxmox. This gives us:

- Full control over routing, firewall, and DNS via UCI and LuCI.
- The ability to version-control and reproduce the entire router configuration.
- Hardware flexibility -- any x86 mini-PC with multiple NICs becomes a router.
- WiFi mesh networking across multiple nodes without proprietary firmware.

## Core Requirements

### 1. Full NIC Passthrough via Virtual Bridges

Every physical ethernet port on the Proxmox host gets its own dedicated virtual bridge (`vmbr0`, `vmbr1`, etc.). Each bridge is then attached to the OpenWrt VM as a separate `virtio` NIC.

**Why individual bridges instead of a single shared bridge?**

- Each port appears as a distinct `ethN` interface inside OpenWrt, allowing per-port WAN/LAN assignment.
- Proxmox retains management access to each bridge for monitoring and diagnostics.
- OpenWrt controls all Layer 2/3 decisions -- bridging, VLANs, firewall zones -- exactly as it would on physical hardware.

### 2. WAN Assignment

The first bridge in `proxmox_all_bridges` (typically `vmbr0`) maps to `eth0` inside the VM, which is designated WAN. All other `ethN` interfaces become LAN ports on a bridge.

The configure role waits up to 90 seconds for a DHCP default route to appear on the WAN interface, confirming upstream connectivity. If no route appears, the play fails with a clear error.

**Practical implication:** plug the upstream (ISP/router) cable into the physical NIC that corresponds to `vmbr0`. On multi-port mini-PCs, this is typically the first ethernet port. You can check bridge-to-NIC mappings on the Proxmox host with `brctl show`.

### 3. Collision-Free LAN Subnet

The WAN subnet is detected at runtime. The LAN subnet is selected from a prioritized candidate list, skipping any candidate whose /24 prefix matches the WAN network:

| Priority | Candidate      |
|----------|----------------|
| 1        | 10.10.10.0/24  |
| 2        | 192.168.2.0/24 |
| 3        | 172.16.0.0/24  |
| 4        | 192.168.10.0/24|

If WAN is on `192.168.2.x`, candidate 2 is skipped and `10.10.10.0/24` is used. This avoids the classic mistake of setting the LAN to `192.168.1.1` when the upstream is also on `192.168.1.0/24`.

### 4. Router Swap Workflow

The playbook is designed for a three-step router replacement:

1. **Stage** -- Deploy behind the existing router. OpenWrt gets a WAN IP via DHCP from the old router and auto-selects a non-colliding LAN subnet.
2. **Swap** -- Move the ISP uplink from the old router to the OpenWrt host's WAN port. OpenWrt picks up a new DHCP lease from the ISP.
3. **Downstream** (optional) -- Plug the old router into a LAN port. Disable its DHCP. It becomes a switch/AP on a sub-network.

No MAC cloning is needed. ISPs issue new DHCP leases to new MAC addresses within seconds.

### 5. WiFi PCIe Passthrough and 802.11s Mesh

WiFi cards cannot be virtualized effectively -- they need direct hardware access. The `proxmox_pci_passthrough` role handles this by:

1. Detecting WiFi interfaces via `/sys/class/net/*/wireless`.
2. Resolving their PCI bus addresses.
3. Enabling IOMMU in GRUB if not already active.
4. Validating that each WiFi device is in its own IOMMU group (a hard requirement for safe passthrough).
5. Blacklisting the host WiFi driver and binding the device to `vfio-pci`.
6. Attaching the device to the VM with `qm set --hostpciN`.

Once inside OpenWrt, the `openwrt_configure` role (Phase 2, after WAN is up):

1. Switches opkg feeds to HTTP (BusyBox `wget` lacks SSL support).
2. Installs WiFi driver packages (`kmod-iwlwifi`, firmware) via opkg.
3. Explicitly loads kernel modules with `modprobe` (opkg does not auto-load them).
4. Detects WiFi radios via `/sys/class/ieee80211/`.
5. If radios are found: replaces `wpad-basic` with `wpad-mesh-openssl`, enables each radio, and configures 802.11s mesh with WPA3-SAE encryption.

### 6. Firewall and DHCP Baseline

The default OpenWrt firewall zones are retained:

- **wan** zone: masquerading enabled, input rejected, forward rejected.
- **lan** zone: input accepted, output accepted, forward to wan accepted.

DHCP is configured on the LAN zone with sensible defaults (start offset 100, pool size 150, 12-hour leases).

## OpenWrt Image Management

The OpenWrt `.img` file is stored locally in the `images/` directory (gitignored). The playbook uploads it to the Proxmox host's `/tmp/`, imports it as a VM disk via `qm importdisk`, then deletes the temporary copy.

The image source is configurable via `openwrt_image_path` in `inventory/group_vars/all.yml`. It can point to a local file or a NAS mount. The project is designed to be self-hosted -- no downloads from the internet during provisioning.

## Bootstrap Connectivity

Fresh OpenWrt has no password on root and runs Dropbear SSH. The playbook establishes initial connectivity by:

1. Adding a temporary IP (`192.168.1.2/24`) to the auto-detected WAN bridge (the bridge backing the host's default route). OpenWrt's factory default puts LAN on `eth0`, so initially both WAN and the bootstrap IP share the same bridge.
2. Waiting for OpenWrt's default LAN IP (`192.168.1.1`) to respond on port 22.
3. Using `ProxyJump` through the Proxmox host for SSH, with `sshpass` for empty-password auth and SSH keepalives (`ServerAliveInterval=15`).
4. During Phase 1 of configuration, the bootstrap IP is migrated from the WAN bridge to the LAN bridge after networking restarts.
5. After configuration completes, the temporary IP is removed.

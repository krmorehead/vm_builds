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

### 2. Automatic WAN Detection

The playbook does not assume which port is WAN. Instead:

1. OpenWrt boots with all interfaces available.
2. The configure role waits for a DHCP lease to appear on any interface (up to 90 seconds).
3. Whichever interface gets a default route is designated WAN.
4. All other ethernet interfaces are assigned to a LAN bridge.

This means you can plug the uplink into any port and the system self-configures.

### 3. Collision-Free LAN Subnet

The WAN subnet is detected at runtime. The LAN subnet is selected from a prioritized candidate list, skipping any candidate whose /24 prefix matches the WAN network:

| Priority | Candidate      |
|----------|----------------|
| 1        | 10.10.10.0/24  |
| 2        | 192.168.2.0/24 |
| 3        | 172.16.0.0/24  |
| 4        | 192.168.10.0/24|

If WAN is on `192.168.2.x`, candidate 2 is skipped and `10.10.10.0/24` is used. This avoids the classic mistake of setting the LAN to `192.168.1.1` when the upstream is also on `192.168.1.0/24`.

### 4. Upstream MAC Cloning

When `openwrt_clone_wan_mac` is enabled (default: `true`), the playbook:

1. Reads the default gateway IP from the Proxmox host's routing table.
2. Pings the gateway to populate the ARP neighbor table.
3. Extracts the gateway's MAC address.
4. Assigns that MAC to the OpenWrt VM's WAN interface (`net0`).

This allows a seamless swap from a physical router to the OpenWrt VM -- the ISP/upstream device sees the same MAC address and continues to serve the same DHCP lease, avoiding re-authentication or lease expiry delays.

### 5. WiFi PCIe Passthrough and 802.11s Mesh

WiFi cards cannot be virtualized effectively -- they need direct hardware access. The `proxmox_pci_passthrough` role handles this by:

1. Detecting WiFi interfaces via `/sys/class/net/*/wireless`.
2. Resolving their PCI bus addresses.
3. Enabling IOMMU in GRUB if not already active.
4. Validating that each WiFi device is in its own IOMMU group (a hard requirement for safe passthrough).
5. Blacklisting the host WiFi driver and binding the device to `vfio-pci`.
6. Attaching the device to the VM with `qm set --hostpciN`.

Once inside OpenWrt, the `openwrt_configure` role:

1. Detects WiFi radios via `/sys/class/ieee80211/`.
2. Replaces the default `wpad-basic` with `wpad-mesh-openssl`.
3. Configures each radio as an 802.11s mesh point with WPA3-SAE encryption.

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

1. Selecting a LAN-side bridge on the Proxmox host.
2. Adding a temporary IP (`192.168.1.2/24`) to that bridge.
3. Waiting for OpenWrt's default LAN IP (`192.168.1.1`) to respond on port 22.
4. Using `ProxyJump` through the Proxmox host for SSH, with `sshpass` for empty-password auth.
5. After configuration completes, the temporary IP is removed in Play 3.

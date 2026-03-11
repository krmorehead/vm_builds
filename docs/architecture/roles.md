# Role Reference

## proxmox_backup

**Purpose:** Capture the current Proxmox host state (config files + VM backups) before any changes, providing a rollback point.

### How It Works

1. Creates `/var/lib/ansible-backup/` on the host.
2. Archives host config directories (`/etc/network/`, `/etc/modprobe.d/`, `/etc/default/grub`, `/etc/modules`, `/etc/pve/`) into `host-config.tar.gz`.
3. Lists all existing VMs via `qm list`.
4. Runs `vzdump` on each VM (skipped when `backup_vms` is false or no VMs exist).
5. Writes a `manifest.json` recording the timestamp, backed-up VMID list, and file paths.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `backup_dir` | `/var/lib/ansible-backup` | Directory on host for backup files |
| `backup_storage` | `""` | Proxmox storage name for vzdump (empty = use `backup_dir`) |
| `backup_vms` | `true` | Whether to back up existing VMs |
| `vzdump_mode` | `snapshot` | vzdump mode: `snapshot`, `stop`, or `suspend` |
| `vzdump_compress` | `zstd` | Compression: `zstd`, `gzip`, `lzo`, or `0` |

### Restore Modes

Restore is handled by `playbooks/cleanup.yml` with tag selection:

| Command | Tag | Behavior |
|---------|-----|----------|
| `./cleanup.sh restore` | `restore` | Restore host config only, leave VMs untouched |
| `./cleanup.sh full-restore` | `full-restore` | Destroy current VMs, restore backed-up VMs + host config |
| `./cleanup.sh clean` | `clean` | Destroy all VMs, restore host config (no VM restore) |

---

## proxmox_bridges

**Purpose:** Discover physical NICs on the Proxmox host and ensure each one is attached to a dedicated virtual bridge.

### How It Works

1. Lists all interfaces in `/sys/class/net/`.
2. Filters out virtual interfaces (loopback, existing bridges, taps, Docker, bonds) using regex patterns defined in `bridge_exclude_patterns`.
3. Checks each candidate for a `/sys/class/net/<nic>/device` symlink, confirming it is backed by real hardware.
4. Scans existing `vmbr*` bridges to find which NICs are already bridged.
5. For unbridged NICs, generates a bridge config in `/etc/network/interfaces.d/ansible-bridges.conf`, numbering sequentially above the highest existing bridge index.
6. Flushes handlers to reload networking before subsequent roles run.
7. Exports `proxmox_all_bridges` -- the sorted list of all physical-NIC-backed bridges.
8. Fails the play if fewer than 2 bridges exist (OpenWrt needs at minimum WAN + LAN).

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `bridge_exclude_patterns` | See `defaults/main.yml` | Regex list of interface names to skip |

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `proxmox_all_bridges` | list | All bridges backed by physical NICs, sorted |

---

## proxmox_pci_passthrough

**Purpose:** Configure PCIe passthrough for WiFi cards so they can be directly assigned to the OpenWrt VM.

### How It Works

1. Finds WiFi interfaces by checking for `/sys/class/net/*/wireless`.
2. Resolves each WiFi interface to its PCI bus address via sysfs.
3. Detects the kernel driver currently bound to each device.
4. Checks whether IOMMU is active via `dmesg`.
5. If IOMMU is inactive: adds `intel_iommu=on` or `amd_iommu=on` to GRUB, adds VFIO modules to `/etc/modules`, updates GRUB and initramfs, then reboots (if permitted).
6. Validates IOMMU group isolation -- each WiFi device must be the sole device in its IOMMU group. Fails with guidance if not.
7. Blacklists the detected WiFi driver via `/etc/modprobe.d/blacklist-wifi.conf`.
8. Configures `/etc/modprobe.d/vfio-pci.conf` to bind the device by vendor:device ID.
9. Exports `wifi_pci_devices` for the `openwrt_vm` role to consume.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `pci_passthrough_allow_reboot` | `false` | Allow automatic reboot to enable IOMMU |
| `wifi_driver_blacklist` | `[]` | Override auto-detected driver blacklist |

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `wifi_pci_devices` | list | PCI addresses (e.g., `["03:00.0"]`) |

---

## openwrt_vm

**Purpose:** Create, configure, and boot the OpenWrt VM on Proxmox, then establish bootstrap SSH connectivity.

### How It Works

1. Checks if the VM already exists (`qm status`). Skips creation tasks if it does.
2. Uploads the OpenWrt disk image to the Proxmox host.
3. Creates a VM shell via the Proxmox API (no disk yet) -- uses `q35` machine type when WiFi passthrough is active.
4. Imports the disk with `qm importdisk`, parses the resulting volume name, attaches it as `scsi0`, and sets boot order.
5. Resizes the disk to `openwrt_vm_disk_size`.
6. Attaches one `virtio` NIC per bridge from `proxmox_all_bridges`.
7. Attaches WiFi PCIe devices from `wifi_pci_devices` via `hostpci`.
8. Cleans up the temporary image file.
9. Starts the VM via the Proxmox API.
10. Adds a temporary IP to a LAN bridge and waits for OpenWrt SSH.
11. Adds the VM to Ansible's in-memory inventory with ProxyJump SSH config.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `openwrt_image_path` | `images/openwrt.img` | Path to the OpenWrt disk image |
| `openwrt_vm_id` | `100` | Proxmox VM ID |
| `openwrt_vm_name` | `openwrt-router` | VM display name |
| `openwrt_vm_memory` | `512` | RAM in MB |
| `openwrt_vm_cores` | `2` | CPU cores |
| `openwrt_vm_disk_size` | `512M` | Boot disk size |
| `openwrt_bootstrap_gw` | `192.168.1.1` | OpenWrt default LAN IP |
| `openwrt_bootstrap_ip` | `192.168.1.2` | Temporary IP for Proxmox bridge |
| `openwrt_bootstrap_cidr` | `24` | Bootstrap subnet prefix length |
| `proxmox_storage` | `local-lvm` | Proxmox storage target for disk import |

---

## openwrt_configure

**Purpose:** Configure the running OpenWrt VM's network, firewall, DHCP, and WiFi mesh via UCI commands over SSH.

### How It Works

All configuration is done with `ansible.builtin.raw` since OpenWrt has no Python. Commands are UCI set/commit operations. The role uses a **two-phase restart** pattern to maintain SSH connectivity and internet access throughout.

**Phase 1 (WAN + LAN ports):**

1. Identifies the WAN device (passed from `openwrt_vm`) and sets remaining `ethN` interfaces as LAN ports.
2. If auto-subnet is enabled, computes the target LAN subnet but does NOT apply it yet (LAN stays at factory default `192.168.1.1`).
3. Configures WAN (DHCP) and WAN6 (DHCPv6), sets LAN bridge ports.
4. Commits and restarts firewall/DHCP synchronously, then restarts networking via detached script (preserves SSH).
5. Migrates the Proxmox bootstrap IP from the WAN bridge to the LAN bridge.
6. Waits for SSH to reconnect and WAN default route to appear.

**Phase 2 (packages + final LAN IP):**

7. Switches opkg feeds to HTTP (BusyBox `wget` lacks SSL), installs WiFi driver packages, loads kernel modules.
8. If WiFi radios are detected: removes default `wpad`, installs `wpad-mesh-openssl`, configures 802.11s mesh interfaces with WPA3-SAE.
9. Applies the auto-selected LAN IP and DHCP settings.
10. Commits and does a final detached network restart.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `openwrt_lan_auto_subnet` | `true` | Auto-select LAN subnet |
| `openwrt_lan_subnet_candidates` | See defaults | Prioritized subnet list |
| `openwrt_lan_ip` | `10.10.10.1` | Fallback LAN IP |
| `openwrt_lan_netmask` | `255.255.255.0` | LAN netmask |
| `openwrt_dhcp_start` | `100` | DHCP range start offset |
| `openwrt_dhcp_limit` | `150` | DHCP pool size |
| `openwrt_dhcp_leasetime` | `12h` | DHCP lease duration |
| `openwrt_wifi_driver_packages` | `[kmod-iwlwifi, iwlwifi-firmware-iwl8265]` | Kernel modules/firmware for WiFi |
| `openwrt_mesh_enabled` | `true` | Enable 802.11s mesh |
| `openwrt_mesh_id` | `vm-builds-mesh` | Mesh network SSID |
| `openwrt_mesh_key` | From `MESH_KEY` env | WPA3-SAE passphrase |
| `openwrt_mesh_channel` | `auto` | WiFi channel |
| `openwrt_mesh_encryption` | `sae` | Encryption method |

---

## proxmox_lxc

**Purpose:** Reusable role for provisioning LXC containers on Proxmox. Each service's `<type>_lxc` role consumes this via `include_role` with service-specific variables.

### How It Works

1. Validates required parameters (`lxc_ct_id`, `lxc_ct_hostname`).
2. Checks if the container already exists (`pct status`). Skips creation if present.
3. Uploads the LXC template from the controller's `images/` directory to the Proxmox host's template cache (if not already cached).
4. Builds and executes `pct create` with all parameters (resources, networking, storage, unprivileged flag).
5. Applies mount entries and container features via `pct set`.
6. Sets `onboot` and `startup` order (unconditional, self-healing).
7. Starts the container and waits for readiness (`pct exec -- hostname`).
8. Registers the container in a dynamic Ansible group via `add_host` with the `community.proxmox.proxmox_pct_remote` connection plugin (no SSH needed).

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `lxc_ct_id` | _(required)_ | Proxmox container ID |
| `lxc_ct_hostname` | _(required)_ | Container hostname |
| `lxc_ct_memory` | `256` | RAM in MB |
| `lxc_ct_swap` | `256` | Swap in MB |
| `lxc_ct_cores` | `1` | CPU cores |
| `lxc_ct_disk` | `"4"` | Root disk size in GB |
| `lxc_ct_template` | `proxmox_lxc_default_template` | Template filename |
| `lxc_ct_template_path` | `proxmox_lxc_template_path` | Local path to template file |
| `lxc_ct_template_storage` | `local` | Proxmox storage for template cache |
| `lxc_ct_bridge` | Second bridge from `proxmox_all_bridges` | Network bridge |
| `lxc_ct_ip` | `dhcp` | IP configuration (`dhcp` or `<ip>/<cidr>`) |
| `lxc_ct_gateway` | `""` | Default gateway (empty for DHCP) |
| `lxc_ct_nameserver` | `""` | DNS server (empty for DHCP) |
| `lxc_ct_features` | `[]` | Container features (e.g., `["nesting=1"]`) |
| `lxc_ct_mount_entries` | `[]` | Device bind mounts for `/dev/dri` etc. |
| `lxc_ct_unprivileged` | `true` | Run as unprivileged container |
| `lxc_ct_onboot` | `true` | Start on host boot |
| `lxc_ct_startup_order` | `5` | Proxmox boot priority (lower = earlier) |
| `lxc_ct_dynamic_group` | `""` | Ansible group for `add_host` registration |
| `lxc_ct_storage` | `proxmox_storage` | Proxmox storage pool for rootfs |

### Usage Pattern

Service-specific LXC roles consume `proxmox_lxc` via `include_role`:

```yaml
- name: Provision Pi-hole container
  ansible.builtin.include_role:
    name: proxmox_lxc
  vars:
    lxc_ct_id: "{{ pihole_ct_id }}"
    lxc_ct_hostname: pihole
    lxc_ct_dynamic_group: pihole
    lxc_ct_memory: 256
    lxc_ct_cores: 1
    lxc_ct_disk: "4"
```

---

## proxmox_igpu

**Purpose:** Detect the Intel iGPU, ensure the `i915` driver is loaded, install VA-API tools, verify Quick Sync hardware transcoding, and export facts for containers and VMs that need GPU access.

### How It Works

1. Detects Intel VGA controller via `lspci`.
2. If no Intel GPU found, sets `igpu_available: false` and skips remaining tasks.
3. Checks if `i915` kernel module is loaded. If not: removes any blacklist entries from `/etc/modprobe.d/` and loads the module via `modprobe`.
4. Waits for `/dev/dri/renderD128` to appear after driver load.
5. Dynamically finds the card device (`/dev/dri/cardN`) by matching the `i915` driver in sysfs (does not hardcode `card0`).
6. Reads `render` and `video` group GIDs for container permission mapping.
7. Verifies DNS resolution (falls back to `8.8.8.8` if broken).
8. Disables Proxmox enterprise repos (renames to `.disabled`), adds the no-subscription repo.
9. Installs `vainfo` and `intel-media-va-driver` via apt.
10. Runs `vainfo` to verify VA-API profiles are available (H.264, HEVC, VP8, VP9).

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `igpu_available` | bool | Whether an Intel iGPU was detected |
| `igpu_pci_address` | string | PCI bus address (e.g., `00:02.0`) |
| `igpu_render_device` | string | Render node path (e.g., `/dev/dri/renderD128`) |
| `igpu_card_device` | string | Card device path (e.g., `/dev/dri/card1`) |
| `igpu_render_gid` | string | GID of the `render` group on the host |
| `igpu_video_gid` | string | GID of the `video` group on the host |

### Container Usage

LXC containers that need iGPU access use bind mounts via `proxmox_lxc`:

```yaml
lxc_ct_mount_entries:
  - "mp0: /dev/dri/renderD128,mp=/dev/dri/renderD128"
lxc_ct_features:
  - "mount=cgroup"
```

The container must add the user to the `render` and `video` groups using
the GIDs exported by this role.

### Host State Changes

Unlike read-only detection roles, `proxmox_igpu` modifies host state:

- Loads `i915` kernel module (persists until reboot)
- Renames enterprise repos to `.disabled` in `/etc/apt/sources.list.d/`
- Creates `/etc/apt/sources.list.d/pve-no-subscription.sources`
- Installs `vainfo` and `intel-media-va-driver` packages
- May update `/etc/resolv.conf` if DNS is broken

Cleanup restores enterprise repos and removes the no-subscription file.

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
| `openwrt_image_path` | `images/openwrt-router-24.10.0-x86-64-combined.img.gz` | Path to the custom OpenWrt router image (built by `build-images.sh`) |
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
| `openwrt_mesh_enabled` | `true` | Enable 802.11s mesh |
| `openwrt_mesh_id` | `vm-builds-mesh` | Mesh network SSID |
| `openwrt_mesh_key` | From `MESH_KEY` env | WPA3-SAE passphrase |
| `openwrt_mesh_channel` | `auto` | WiFi channel |
| `openwrt_mesh_encryption` | `sae` | Encryption method |

### Post-Baseline Task Files

`openwrt_configure` includes additional task files for features that build
on top of the baseline configuration. Each is invoked via `include_role`
with `tasks_from:` in a dedicated `site.yml` play.

| Task file | Milestone | Tag | Key variables |
|-----------|-----------|-----|---------------|
| `security.yml` | M1 | `openwrt-security` | `openwrt_root_password`, `openwrt_ssh_pubkey`, `openwrt_ssh_private_key` |
| `vlans.yml` | M2 | `openwrt-vlans` | `openwrt_vlan_iot_id`, `openwrt_vlan_guest_id`, subnet/IP defaults |
| `dns.yml` | M3 | `openwrt-dns` | `openwrt_dns_doh_primary`, `openwrt_dns_doh_secondary` |
| `mesh.yml` | M4 | `openwrt-mesh` | `openwrt_dawn_rssi_threshold`, `openwrt_dawn_steering_mode` |
| `pihole_dns.yml` | M3 (Pi-hole) | `openwrt-pihole-dns` | `pihole_ct_ip_offset` |
| `syslog.yml` | M3 (rsyslog) | `openwrt-syslog` | `rsyslog_ct_ip_offset` |

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
7. Starts the container and waits for readiness (`pct exec -- ls /` — BusyBox/OpenWrt compatible).
8. Registers the container in a dynamic Ansible group via `add_host` with the `community.proxmox.proxmox_pct_remote` connection plugin (no SSH needed). Uses `ansible_play_hosts` (not `ansible_play_hosts_all`) to avoid registering containers for failed hosts.

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
| `lxc_ct_bridge` | Second bridge from `proxmox_all_bridges` | Network bridge (must match host topology: LAN hosts use LAN bridge; WAN hosts use `proxmox_wan_bridge`) |
| `lxc_ct_ip` | `dhcp` | IP configuration (`dhcp` or `<ip>/<cidr>`) |
| `lxc_ct_gateway` | `""` | Default gateway (empty for DHCP) |
| `lxc_ct_nameserver` | `""` | DNS server (empty for DHCP; WAN hosts typically use `8.8.8.8`) |
| `lxc_ct_features` | `[]` | Container features (e.g., `["nesting=1"]`) |
| `lxc_ct_mount_entries` | `[]` | Device bind mounts for `/dev/dri` etc. |
| `lxc_ct_ostype` | `""` | OS type for `pct create` (empty = auto-detect; `unmanaged` for OpenWrt) |
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

## wireguard_lxc

**Purpose:** Provision a WireGuard VPN LXC container on Proxmox via the shared `proxmox_lxc` role, with the host-side `wireguard` kernel module loaded and persisted.

### How It Works

1. Loads the `wireguard` kernel module on the Proxmox host (`modprobe wireguard`). LXC containers share the host kernel and cannot load modules themselves.
2. Persists the module across reboots via `/etc/modules-load.d/wireguard.conf`.
3. Delegates to `proxmox_lxc` with service-specific vars: VMID 101, hostname `wireguard`, 128 MB RAM, 1 core, 1 GB disk, `nesting=1` feature (required for iptables NAT), auto-start priority 2.
4. `proxmox_lxc` handles template upload, `pct create`, start, and `add_host` registration in the `wireguard` dynamic group with `pct_remote` connection.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `wireguard_ct_hostname` | `wireguard` | Container hostname |
| `wireguard_ct_memory` | `128` | RAM in MB |
| `wireguard_ct_cores` | `1` | CPU cores |
| `wireguard_ct_disk` | `"1"` | Root disk size in GB |
| `wireguard_ct_template` | `wireguard_lxc_template` | Custom LXC template (wireguard-tools + iptables baked in) |
| `wireguard_ct_template_path` | `wireguard_lxc_template_path` | Local path to template image |
| `wireguard_ct_onboot` | `true` | Start on host boot |
| `wireguard_ct_startup_order` | `2` | Boot priority (after OpenWrt) |
| `wireguard_ct_features` | `["nesting=1"]` | Container features |

### Host State Changes

- Loads `wireguard` kernel module
- Creates `/etc/modules-load.d/wireguard.conf`

Cleanup must remove the config file and `modprobe -r wireguard`.

---

## wireguard_configure

**Purpose:** Configure the WireGuard VPN client inside the LXC container with tunnel credentials, IP forwarding, and NAT. Auto-generates keys when env vars are not provided.

### How It Works

1. **Key generation** (when `WIREGUARD_PRIVATE_KEY` is empty):
   - Generates client keypair inside the container via `wg genkey` / `wg pubkey`.
   - Generates a dummy server keypair for config validity (tunnel won't connect but config is syntactically valid).
   - Writes all generated values to `.env.generated` on the controller (append mode, gitignored). Includes the client public key for server-side configuration.

2. **Configuration** (runs always, using provided or generated values):
   - Templates `/etc/wireguard/wg0.conf` with mode `0600`.
   - Enables and starts `wg-quick@wg0` service.
   - Applies IP forwarding (sysctl file baked into image by `build-images.sh`).
   - Configures iptables MASQUERADE on wg0 for NAT. Saves via `netfilter-persistent` (package baked into image).

### Key Variables (all optional -- auto-generated when empty)

| Variable | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `wireguard_private_key` | `WIREGUARD_PRIVATE_KEY` | `""` (auto-gen) | Client private key |
| `wireguard_client_address` | `WIREGUARD_CLIENT_ADDRESS` | `10.0.0.2/24` | Client tunnel IP |
| `wireguard_server_public_key` | `WIREGUARD_SERVER_PUBLIC_KEY` | `""` (auto-gen dummy) | Server public key |
| `wireguard_server_endpoint` | `WIREGUARD_SERVER_ENDPOINT` | `198.51.100.1:51820` | Server host:port |
| `wireguard_allowed_ips` | `WIREGUARD_ALLOWED_IPS` | `10.0.0.0/24` | Routed subnets |
| `wireguard_preshared_key` | `WIREGUARD_PRESHARED_KEY` | `""` (omitted) | Optional PSK |
| `wireguard_dns` | `WIREGUARD_DNS` | `""` (omitted) | Tunnel DNS servers |
| `wireguard_keepalive` | -- | `25` | PersistentKeepalive seconds |

### `.env.generated` Pattern

When keys are auto-generated, the role writes them to `.env.generated` on the controller using `blockinfile` (append, not overwrite). The user copies needed values to `.env` for subsequent runs. The file is gitignored and cleaned up during molecule cleanup.

```
# BEGIN WireGuard (auto-generated)
WIREGUARD_PRIVATE_KEY=<generated>
WIREGUARD_PUBLIC_KEY=<generated>
WIREGUARD_CLIENT_ADDRESS=10.0.0.2/24
WIREGUARD_SERVER_PUBLIC_KEY=<generated-dummy>
WIREGUARD_SERVER_ENDPOINT=198.51.100.1:51820
WIREGUARD_ALLOWED_IPS=10.0.0.0/24
# END WireGuard (auto-generated)
```

---

## proxmox_igpu

**Purpose:** Detect the iGPU (Intel or AMD), ensure the driver is loaded, install VA-API tools, verify hardware transcoding, and export facts for containers and VMs that need GPU access.

### How It Works

1. Detects Intel VGA controller and AMD VGA/Display controller via `lspci`.
2. If no GPU found, hard-fails. Sets `igpu_vendor` to `intel` or `amd`.
3. Checks if the vendor driver (`i915` for Intel, `amdgpu` for AMD) is loaded. If not: removes any blacklist entries and loads the module.
4. Waits for `/dev/dri/renderD128` to appear after driver load.
5. Dynamically finds the card device (`/dev/dri/cardN`) by matching the driver in sysfs (does not hardcode `card0`).
6. Reads `render` and `video` group GIDs for container permission mapping.
7. Verifies DNS resolution (falls back to `8.8.8.8` if broken).
8. Syncs system clock via NTP to prevent GPG signature verification failures from clock skew.
9. Disables Proxmox enterprise repos (renames to `.disabled`), adds the no-subscription repo.
10. Installs `vainfo` and the appropriate VA-API driver (`intel-media-va-driver` for Intel, `mesa-va-drivers` for AMD).
11. Runs `vainfo` with the correct LIBVA_DRIVER_NAME (`iHD` for Intel, `radeonsi` for AMD).

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `igpu_available` | bool | Whether an iGPU was detected |
| `igpu_vendor` | string | GPU vendor (`intel`, `amd`, or `none`) |
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

- Syncs system clock via NTP before apt operations (prevents GPG "Not live until" errors from clock skew)
- Loads `i915` (Intel) or `amdgpu` (AMD) kernel module (persists until reboot)
- Renames enterprise repos to `.disabled` in `/etc/apt/sources.list.d/`
- Creates `/etc/apt/sources.list.d/pve-no-subscription.sources`
- Installs `vainfo` and vendor-specific VA-API driver (`intel-media-va-driver` for Intel, `mesa-va-drivers` for AMD)
- May update `/etc/resolv.conf` if DNS is broken

Cleanup restores enterprise repos and removes the no-subscription file.

---

## pihole_lxc

**Purpose:** Provision a Pi-hole DNS filtering LXC container on Proxmox via the shared `proxmox_lxc` role.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `pihole_ct_hostname` | `pihole` | Container hostname |
| `pihole_ct_memory` | `256` | RAM in MB |
| `pihole_ct_cores` | `1` | CPU cores |
| `pihole_ct_disk` | `"2"` | Root disk size in GB |
| `pihole_ct_template` | `pihole_lxc_template` | Custom Pi-hole template (built by `build-images.sh`) |
| `pihole_ct_onboot` | `true` | Start on host boot |
| `pihole_ct_startup_order` | `3` | Boot priority |
| `pihole_ct_features` | `["nesting=1"]` | Container features |

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `pihole_static_ip` | string | Computed container IP for downstream DNS config |

---

## pihole_configure

**Purpose:** Configure Pi-hole DNS filtering with host-specific settings (web password, upstream DNS, gravity update) via pihole-FTL CLI.

### Key Variables

| Variable | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `pihole_web_password` | `PIHOLE_WEB_PASSWORD` | `""` | Web admin password |
| `pihole_upstream_dns_1` | -- | `1.1.1.1` | Primary upstream DNS |
| `pihole_upstream_dns_2` | -- | `1.0.0.1` | Secondary upstream DNS |
| `pihole_query_logging_days` | -- | `7` | Query log retention days |

---

## rsyslog_lxc

**Purpose:** Provision an rsyslog centralized log collector LXC container on Proxmox via the shared `proxmox_lxc` role.

### How It Works

1. Verifies the custom rsyslog template exists on the controller (hard-fails if missing).
2. Reads LAN gateway/CIDR from `env_generated_path`.
3. Branches on host topology: LAN hosts (behind OpenWrt) use the LAN bridge and OpenWrt LAN subnet; WAN hosts use `proxmox_wan_bridge` and the host's WAN subnet with IP offset +200.
4. Delegates to `proxmox_lxc` with service-specific vars: VMID 501, hostname `rsyslog`, 64 MB RAM, 1 core, 1 GB disk, auto-start priority 3.
5. `proxmox_lxc` handles template upload, `pct create`, start, and `add_host` registration in the `rsyslog` dynamic group with `pct_remote` connection.

No special LXC features required — rsyslog is a pure userspace daemon.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `rsyslog_ct_hostname` | `rsyslog` | Container hostname |
| `rsyslog_ct_memory` | `64` | RAM in MB |
| `rsyslog_ct_cores` | `1` | CPU cores |
| `rsyslog_ct_disk` | `"1"` | Root disk size in GB |
| `rsyslog_ct_template` | `rsyslog_lxc_template` | Custom rsyslog template (built by `build-images.sh`) |
| `rsyslog_ct_onboot` | `true` | Start on host boot |
| `rsyslog_ct_startup_order` | `3` | Boot priority |

### Exported Facts

| Fact | Type | Description |
|------|------|-------------|
| `rsyslog_static_ip` | string | Computed container IP for downstream syslog config |

### Host State Changes

None — rsyslog is pure userspace. No kernel modules, no host config files.

---

## rsyslog_configure

**Purpose:** Configure rsyslog with host-specific forwarding rules. Base config (TCP listener, spool directory, logrotate) is baked into the image.

### How It Works

1. When `RSYSLOG_HOME_SERVER` is set: templates `/etc/rsyslog.d/20-forward.conf` inside the container with disk-assisted queue for reliable delivery during WireGuard tunnel outages.
2. When `RSYSLOG_HOME_SERVER` is empty: ensures `20-forward.conf` is absent. rsyslog operates in local collection + buffer mode only.
3. Restarts rsyslog if config changed.

### Key Variables

| Variable | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `rsyslog_home_server` | `RSYSLOG_HOME_SERVER` | `""` | Forward logs to this IP (optional; empty = local only) |

### What Is NOT in This Role (Baked in Image)

- TCP listener (`/etc/rsyslog.d/10-receive.conf`) — imtcp module + template
- Remote routing (`/etc/rsyslog.d/50-remote-route.conf`) — per-hostname file write + stop
- Spool directory (`/var/spool/rsyslog/`)
- Logrotate config for remote logs

---

## netdata_lxc

**Purpose:** Provision a Netdata monitoring agent LXC container on Proxmox via the shared `proxmox_lxc` role, with read-only bind mounts for host `/proc` and `/sys`.

### How It Works

1. Verifies the custom Netdata template exists on the controller (hard-fails if missing).
2. Reads LAN gateway/CIDR from `env_generated_path`.
3. Branches on host topology: LAN hosts (behind OpenWrt) use the LAN bridge and OpenWrt LAN subnet; WAN hosts use `proxmox_wan_bridge` and the host's WAN subnet with IP offset +200.
4. Delegates to `proxmox_lxc` with service-specific vars: VMID 500, hostname `netdata`, 128 MB RAM, 1 core, 2 GB disk, privileged with `nesting=1`, auto-start priority 3, bind mounts for `/proc` and `/sys`.
5. `proxmox_lxc` handles template upload, `pct create`, feature flags, mount entries, start, and `add_host` registration in the `netdata` dynamic group with `pct_remote` connection.

### Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `netdata_ct_hostname` | `netdata` | Container hostname |
| `netdata_ct_memory` | `128` | RAM in MB |
| `netdata_ct_cores` | `1` | CPU cores |
| `netdata_ct_disk` | `"2"` | Root disk size in GB |
| `netdata_ct_template` | `netdata_lxc_template` | Custom Netdata template (built by `build-images.sh`) |
| `netdata_ct_onboot` | `true` | Start on host boot |
| `netdata_ct_startup_order` | `3` | Boot priority |
| `netdata_ct_unprivileged` | `false` | Privileged container (required for bind mounts) |
| `netdata_ct_features` | `["nesting=1"]` | LXC features for systemd sandboxing |
| `netdata_ct_mount_entries` | `/proc,mp=/host/proc,ro=1` and `/sys,mp=/host/sys,ro=1` | Host metrics bind mounts |

### Host State Changes

None — Netdata is pure userspace. No kernel modules, no host config files. The bind mounts are container-level configuration managed by `pct set`.

---

## netdata_configure

**Purpose:** Configure optional Netdata child-parent streaming for remote metrics aggregation. Base config (dbengine retention, proc/sys paths, dashboard) is baked into the image.

### How It Works

1. Detects the Netdata config directory (`/etc/netdata/` or `/opt/netdata/etc/netdata/`).
2. When `NETDATA_PARENT_IP` is set: templates `stream.conf` inside the container with destination, API key, and buffer settings.
3. When `NETDATA_PARENT_IP` is empty: ensures `stream.conf` is absent. Netdata operates as a standalone local dashboard.
4. Restarts Netdata if config changed.
5. Post-restart health check waits for the API to respond.

The systemd drop-in override (disabling `LogNamespace`, `ProtectSystem`, etc.)
is baked into the image by `build-images.sh` — it is NOT deployed by this role.

### Key Variables

| Variable | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `netdata_stream_api_key` | `NETDATA_STREAM_API_KEY` | `""` | API key for parent streaming (optional) |
| `netdata_parent_ip` | `NETDATA_PARENT_IP` | `""` | Parent Netdata IP via WireGuard (optional; empty = local only) |

### What Is NOT in This Role (Baked in Image)

- Netdata agent and service
- `netdata.conf` with dbengine retention (1 hour), proc/sys paths, dashboard port 19999
- Cgroups plugin for per-container metrics
- Web dashboard server

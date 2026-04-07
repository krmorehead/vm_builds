# WiFi Bridge Build

## Purpose

The Dedicated WiFi Bridge service creates a transparent L2 WiFi link between
two standalone Proxmox mini-PCs (`bridge-1`, `bridge-2`). The link extends a
LAN subnet to a remote location without routing, NAT, or IP reconfiguration.
Devices plugged into `bridge-2`'s ethernet appear on the same broadcast domain
as `bridge-1`.

## Hardware

Each bridge unit is a small-form-factor PC with:
- Intel AX210 WiFi adapter (WiFi 6E, supports 5 GHz HE80)
- Built-in NIC with Wake-on-LAN support
- iGPU (Intel HD Graphics 630 — detected by `proxmox_igpu`)

**Important**: The AX210 does NOT support 802.11s mesh mode. The firmware only
supports: IBSS, managed, AP, AP/VLAN, monitor, P2P-client, P2P-GO, P2P-device.
The bridge uses WDS (AP + STA with 4-address mode) instead.

## Image

The bridge containers reuse the existing OpenWrt mesh LXC template built by
`build-images.sh --only mesh`. No separate image build is needed.

### Template path

- Source: `images/openwrt-mesh-lxc-x86-64-rootfs.tar.gz`
- Variables: `openwrt_lxc_template` and `openwrt_lxc_template_path` in
  `group_vars/all.yml`

## Architecture

```
bridge-1 (AP side)                    bridge-2 (STA side)
┌──────────────────────┐              ┌──────────────────────┐
│ Proxmox Host         │              │ Proxmox Host         │
│ 192.168.86.230       │              │ 192.168.86.231       │
│                      │              │                      │
│  ┌────────────────┐  │  WDS link    │  ┌────────────────┐  │
│  │ OpenWrt Bridge  │  │ ◄──────────►│  │ OpenWrt Bridge  │  │
│  │ LXC (VMID 104) │  │  WiFi 5GHz  │  │ LXC (VMID 104) │  │
│  │  role: AP       │  │  HE80       │  │  role: STA      │  │
│  │                 │  │  WPA3-SAE   │  │                 │  │
│  │ br-lan:         │  │             │  │ br-lan:         │  │
│  │  eth0 + wds0    │  │             │  │  eth0 + wds0    │  │
│  └────────────────┘  │              │  └────────────────┘  │
│                      │              │                      │
│  eth0 ◄── WAN bridge │              │  eth0 ◄── USB NIC   │
│  (management)        │              │  (backhaul)          │
└──────────────────────┘              └──────────────────────┘
```

### Key Design Decisions

- **WDS AP/STA (not 802.11s)**: The AX210 firmware does not support mesh mode.
  Uses WDS (4-address mode) with one side as AP and the other as STA. This
  provides the same transparent L2 forwarding as mesh.
- **Transparent L2 bridging**: `br-lan` bridges `eth0` (ethernet backhaul) and
  the WiFi interface. No routing or NAT — pure L2 forwarding with STP.
- **Dedicated SSID**: Uses `bridge-dedicated` (hidden AP). Prevents interference
  with other WiFi networks.
- **5 GHz band**: The AX210 self-managed regulatory domain doesn't expose 6 GHz
  frequencies without proper LAR (Location-Aware Regulatory) initialization.
  5 GHz with HE80 provides reliable ~800 Mbps throughput.
- **WPA3-SAE encryption**: Strong authentication via `MESH_KEY` env var.
- **PHY namespace move**: WiFi PHY is moved into the LXC container's network
  namespace (same pattern as `openwrt_mesh_lxc`). Hookscript persists across
  reboots.
- **Per-host WiFi role**: `wifi_role` in `host_vars` determines whether
  the container runs as AP (`bridge-1`) or STA (`bridge-2`).

### Backhaul

- **bridge-1**: Ethernet via the host's WAN bridge (default management NIC).
- **bridge-2**: USB NIC auto-detected at provisioning time. The LXC container's
  `eth0` is connected to a bridge backed by the USB NIC.

## Roles

### `openwrt_bridge_lxc` (provision)

- Targets: `bridge_nodes`
- VMID: 104 (defined as `bridge_ct_id` in `group_vars/all.yml`)
- Memory: 512 MB, Cores: 2, Disk: 1 GB
- Uses `include_role: proxmox_lxc` with `lxc_ct_ostype: unmanaged`
- Asserts WiFi PHY availability before provisioning
- Detects USB NIC for `bridge-2` backhaul bridge
- Deploys hookscript for WiFi PHY namespace persistence
- Forwards `wifi_role` to dynamic host

### `openwrt_bridge_configure` (configure)

- Targets: `openwrt_bridge` (dynamic group populated by provisioning)
- Connection: `community.proxmox.proxmox_pct_remote`
- Delegates to shared `tasks/configure_wifi_wds.yml` with bridge-specific vars
- WDS AP interface (bridge-1) or WDS STA interface (bridge-2) based on `wifi_role`
- Band selection, country code, STP, and WiFi verification handled by shared task

## Container IP

- IP offset: 27 (defined as `bridge_ct_ip_offset` in `group_vars/all.yml`)
- Bridge units are directly on the WAN subnet, so containers use
  `offset + 200 = .227` to avoid collisions
- No per-host indexing needed (each bridge host gets one container)

## Molecule Scenarios

### `bridge-lxc` (per-feature)

Tests the full bridge provisioning and configuration on both bridge hosts:
infrastructure -> provision -> configure -> verify -> cleanup.

### `mesh-ax210` (cross-hardware)

Validates the existing `openwrt_mesh_lxc` and `openwrt_mesh_configure` roles
on AX210 hardware (the bridge hosts). Confirms the mesh image and WDS
configuration work with WiFi 6E hardware.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `BRIDGE_1_HOST` | IP address of bridge-1 (e.g., 192.168.86.230) |
| `BRIDGE_2_HOST` | IP address of bridge-2 (e.g., 192.168.86.231) |
| `BRIDGE_1_API_TOKEN` | Proxmox API token for bridge-1 |
| `BRIDGE_2_API_TOKEN` | Proxmox API token for bridge-2 |
| `MESH_KEY` | WPA3-SAE pre-shared key for the WDS link |

## Deployment

```bash
# Deploy bridge service only
./run.sh --tags bridge

# Rollback bridge containers
./cleanup.sh rollback bridge
```

## Related

- `openwrt_mesh_lxc` / `openwrt_mesh_configure` — mesh satellite roles
  (different SSID: `vm-builds-backhaul`, different target group)
- `tasks/configure_wifi_wds.yml` — shared WDS configuration (used by both
  bridge and mesh configure roles)
- `proxmox_pci_passthrough` — WiFi detection, firmware loading, `iw` install
- `docs/architecture/openwrt-build.md` — OpenWrt image and mesh patterns

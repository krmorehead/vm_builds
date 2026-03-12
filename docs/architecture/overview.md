# Project Overview

## Purpose

**vm_builds** is an Ansible project that automates the provisioning and
configuration of virtual machines and LXC containers on Proxmox VE. The
primary target is a "home entertainment box" -- a single small-form-factor
PC that replaces multiple consumer devices (router, NAS, media server, smart
home hub, desktop) with a fully software-defined stack.

**A single command should take a bare Proxmox host and produce fully
functional, production-ready VMs and containers** -- no manual Proxmox UI
interaction, no SSH-and-paste workflows, no guesswork.

## Design Philosophy

- **Idempotent and repeatable.** Every run converges to the same state.
  A cleanup + re-run cycle always produces a working result.
- **Hardware-agnostic.** NICs, WiFi cards, and PCI topology are discovered
  at runtime. No hardcoded interface names or PCI addresses.
- **Environment-driven secrets.** Credentials injected via `.env` files.
  Everything else is safe to commit.
- **Backup before change.** Every run starts with host config tar + VM
  `vzdump`. Three restore modes: config-only, full rollback, clean reset.
- **Decomposed roles.** Each concern gets its own role. Provisioning is
  separate from configuration. iGPU detection is separate from WiFi
  passthrough. Roles never cross-reference each other's defaults.
- **Extensible.** Two-role-per-service pattern (`<type>_lxc` + `<type>_configure`
  or `<type>_vm` + `<type>_configure`) and shared infrastructure roles keep
  new service types consistent.

---

## Target Architecture

### Home Entertainment Box (Primary Build)

Small-form-factor PC, Intel or AMD CPU (iGPU for VA-API transcoding), 8 GB RAM, 2+ ethernet
ports. All containers and VMs run directly on the Proxmox host as siblings.

```
Proxmox Host (Debian)
├── Network Tier
│   └── OpenWrt Router         VM   VMID 100   cores=2  RAM=512MB     auto-start priority 1
│       ├── WireGuard VPN Client   LXC  VMID 101   cores=1  RAM=128MB     auto-start priority 2
│       ├── Pi-hole                LXC  VMID 102   cores=1  RAM=256MB     auto-start priority 3
│       └── Mesh WiFi Controller   LXC  VMID 103   cores=1  RAM=512MB     auto-start priority 4
│
├── Observability Tier
│   ├── Netdata Agent          LXC  VMID 500   cores=1  RAM=128MB     auto-start priority 3
│   └── rsyslog Collector      LXC  VMID 501   cores=1  RAM=64MB      auto-start priority 3
│
├── Service Tier
│   └── Home Assistant         LXC  VMID 200   cores=2  RAM=1024MB    auto-start priority 5
│
├── Media Tier
│   ├── Jellyfin               LXC  VMID 300   cores=2  RAM=2048MB    auto-start priority 5   iGPU: transcode
│   ├── Kodi                   LXC  VMID 301   cores=2  RAM=1024MB    on-demand               iGPU: display
│   └── Moonlight Client       LXC  VMID 302   cores=1  RAM=512MB     on-demand               iGPU: display
│
└── Desktop Tier
    ├── Debian Desktop         VM   VMID 400   cores=2  RAM=1024MB    on-demand               iGPU: exclusive
    └── Custom UX Kiosk        LXC  VMID 401   cores=1  RAM=512MB     auto-start priority 6   iGPU: display (default)
```

### Gaming Rig (Separate Build)

Separate physical machine dedicated to gaming and game streaming.

```
Proxmox Host (gaming hardware)
├── Gaming VM              VM   VMID 600   cores=4-8  RAM=8-16GB  auto-start   discrete GPU
├── Netdata Agent          LXC  VMID 500   cores=1  RAM=128MB     auto-start
└── rsyslog Collector      LXC  VMID 501   cores=1  RAM=64MB      auto-start
```

### Minimal Router (Lightweight Build)

Nodes that only need routing and monitoring.

```
Proxmox Host
├── OpenWrt Router         VM   VMID 100   auto-start priority 1
├── WireGuard VPN Client   LXC  VMID 101   auto-start priority 2
├── Pi-hole                LXC  VMID 102   auto-start priority 3
├── Netdata Agent          LXC  VMID 500   auto-start priority 3
└── rsyslog Collector      LXC  VMID 501   auto-start priority 3
```

---

## Build Profiles

A host composes its build by belonging to one or more **flavor groups** in the
inventory. Shared infrastructure (`proxmox_backup`, `proxmox_bridges`,
`proxmox_pci_passthrough`, `proxmox_igpu`) runs on every host in `proxmox`.

Hosts on the OpenWrt LAN subnet (not directly reachable from the controller)
belong to `lan_hosts`, which automatically configures SSH ProxyJump through
the primary Proxmox host. See the `multi-node-ssh` skill for details.

```
Build Profiles
├── Home Entertainment Box (home, primary — directly reachable, 192.168.86.201)
│   ├── router_nodes       → OpenWrt
│   ├── vpn_nodes          → WireGuard
│   ├── dns_nodes          → Pi-hole
│   ├── wifi_nodes         → Mesh WiFi Controller
│   ├── monitoring_nodes   → Netdata, rsyslog
│   ├── service_nodes      → Home Assistant
│   ├── media_nodes        → Jellyfin, Kodi, Moonlight
│   └── desktop_nodes      → Debian Desktop, UX Kiosk
│
├── AI Node (ai — directly reachable, 192.168.86.220)
│   └── vpn_nodes          → WireGuard
│
├── Mesh Node 2 (mesh2 — directly reachable, 192.168.86.211)
│   ├── vpn_nodes          → WireGuard
│   └── wifi_nodes         → OpenWrt Mesh LXC (WiFi PHY namespace move)
│
├── LAN Satellite (mesh1 — via ProxyJump through home, requires OpenWrt running)
│   ├── lan_hosts          → ProxyJump SSH config (group_vars/lan_hosts.yml)
│   ├── vpn_nodes          → WireGuard (same service as primary host)
│   └── wifi_nodes         → OpenWrt Mesh LXC (WiFi PHY namespace move)
│
├── Minimal Router
│   ├── router_nodes       → OpenWrt
│   ├── vpn_nodes          → WireGuard
│   ├── dns_nodes          → Pi-hole
│   └── monitoring_nodes   → Netdata, rsyslog
│
└── Gaming Rig
    ├── gaming_nodes       → Gaming VM
    └── monitoring_nodes   → Netdata, rsyslog
```

---

## Boot Order

Proxmox `onboot` and `startup` settings control which services start
automatically when the host reboots. Dependencies flow top-to-bottom:
network must be up before DNS, DNS before application services, etc.

```
Boot Sequence (Home Entertainment Box)
├── Priority 1 ── Network
│   └── OpenWrt Router (VMID 100)             All other services depend on this
│
├── Priority 2 ── VPN
│   └── WireGuard VPN (VMID 101)              Tunnel to home server for remote management
│
├── Priority 3 ── Core Infrastructure         Start simultaneously
│   ├── Pi-hole (VMID 102)                    DNS filtering
│   ├── rsyslog (VMID 501)                    Log collection
│   └── Netdata (VMID 500)                    Monitoring
│
├── Priority 4 ── WiFi Management
│   └── Mesh WiFi Controller (VMID 103)       Needs OpenWrt mesh established first
│
├── Priority 5 ── Application Services        Start simultaneously
│   ├── Home Assistant (VMID 200)             Home automation
│   └── Jellyfin (VMID 300)                   Media server (iGPU transcode, no display)
│
├── Priority 6 ── Default Display
│   └── Custom UX Kiosk (VMID 401)            Dashboard shown when idle
│
└── On-Demand ── Manual Start Only
    ├── Kodi (VMID 301)                       Stops Kiosk on start, restarts Kiosk on stop
    ├── Moonlight Client (VMID 302)           Stops Kiosk on start, restarts Kiosk on stop
    └── Debian Desktop (VMID 400)             Stops Kiosk + takes exclusive iGPU
```

---

## Display Output & iGPU Sharing

The iGPU (Intel or AMD) serves two distinct purposes on the home entertainment box:

```
iGPU Usage
├── Transcoding (no display)
│   └── Jellyfin
│       ├── Uses /dev/dri/renderD128 only (VA-API encode/decode)
│       ├── Does NOT drive a physical display
│       └── Runs alongside any display service (shared access)
│
└── Display Output (physical screen)
    ├── Custom UX Kiosk (default)
    │   └── Cage + Chromium, shared iGPU via bind mount
    ├── Kodi (on-demand)
    │   └── kodi-standalone GBM/DRM, shared iGPU via bind mount
    ├── Moonlight Client (on-demand)
    │   └── moonlight-embedded DRM/KMS, shared iGPU via bind mount
    └── Debian Desktop (on-demand, most disruptive)
        └── Full iGPU passthrough via hostpci (exclusive access)
```

### Sharing Rules

```
iGPU Access Model
├── LXC Containers (Kiosk, Kodi, Moonlight, Jellyfin)
│   ├── Access via /dev/dri/* device bind mount
│   ├── Host keeps i915/amdgpu driver loaded
│   ├── Multiple containers share renderD128 simultaneously (transcode)
│   └── Only one container drives the physical display at a time
│
└── Desktop VM
    ├── Access via hostpci (vfio-pci exclusive passthrough)
    ├── Host LOSES /dev/dri/* while VM is running
    ├── All LXC iGPU bind mounts break
    └── Jellyfin falls back to software transcoding
```

### Display Transitions

```
Display-Exclusive State Machine
├── Idle State (default)
│   └── Kiosk running, Kodi/Moonlight/Desktop stopped
│
├── Start Kodi or Moonlight
│   ├── 1. Stop Kiosk
│   ├── 2. Start requested service
│   └── 3. On stop → restart Kiosk
│
└── Start Desktop VM
    ├── 1. Stop Kiosk, Kodi, Moonlight
    ├── 2. iGPU unbound from i915/amdgpu, bound to vfio-pci
    ├── 3. Desktop VM gets exclusive GPU
    ├── 4. Jellyfin switches to software transcoding
    └── 5. On stop → iGPU returns to i915/amdgpu, Kiosk restarts
```

Managed by Proxmox hookscripts on the host, with Ansible pre-tasks
as enforcement during playbook runs.

### iGPU vs PCI Passthrough -- Role Decomposition

```
PCI Device Handling (separate roles)
├── proxmox_pci_passthrough
│   ├── Purpose: WiFi detection on all hosts; exclusive vfio-pci binding on router_nodes only
│   ├── Method: Detect WiFi interfaces + PCI addresses on all hosts;
│   │          unbind from host driver and bind to vfio-pci only on router_nodes
│   │          (non-router hosts keep the host WiFi driver for PHY namespace move)
│   ├── Exports: wifi_pci_devices (all hosts with WiFi)
│   └── Consumer: openwrt_vm (WiFi passthrough); openwrt_mesh_lxc (WiFi PHY list)
│
└── proxmox_igpu
    ├── Purpose: iGPU detection, driver/VA-API setup, fact export for containers and VMs
    ├── Requirement: iGPU MUST be present (Intel or AMD; hard fail if absent)
    ├── Method: Keep host driver loaded (i915/amdgpu), install VA-API tools, export device paths
    ├── Exports: igpu_available, igpu_vendor, igpu_pci_address, igpu_render_device,
    │           igpu_card_device, igpu_render_gid, igpu_video_gid
    ├── LXC consumers (shared bind mount): jellyfin_lxc, kodi_lxc, moonlight_lxc, kiosk_lxc
    └── VM consumer (exclusive hostpci): desktop_vm (takes GPU from host when running)
```

---

## Network Topology

### Physical Layout

```
ISP Router (192.168.86.x supernet)
  |
Switch
  |            |                  |
Home          AI Node          Mesh2
(primary)     192.168.86.220   192.168.86.211
192.168.86.201
  |
  |-- OpenWrt VM (10.10.10.1)
  |     |
  |     LAN bridge (10.10.10.x)
  |       |
  |     Mesh1 (10.10.10.210)
```

- **home**, **ai**, **mesh2**: directly reachable on the supernet (no ProxyJump)
- **mesh1**: behind home's OpenWrt, reachable via ProxyJump through home
- All 4 nodes run shared infrastructure (backup, bridges, PCI, iGPU)
- All 4 nodes are in `vpn_nodes` — WireGuard containers deploy on all 4

### Logical Layout

```
Internet
└── Upstream ISP Router
    └── WAN (DHCP)
        ├── Proxmox Host "ai" (192.168.86.220, directly reachable)
        │   └── WireGuard VPN (VMID 101)
        │
        ├── Proxmox Host "mesh2" (192.168.86.211, directly reachable)
        │   ├── WireGuard VPN (VMID 101)
        │   └── OpenWrt Mesh LXC (VMID 103, WiFi PHY namespace move)
        │
        └── OpenWrt VM on "home" (VMID 100)
            ├── eth0 ← auto-detected WAN bridge (bridge with default route)
            ├── eth1..N ← remaining bridges (LAN)
            ├── wlan0 ← PCIe passthrough (802.11s mesh)
            │
            └── LAN Network (all other services connect here)
                ├── Proxmox Host "home" (LAN management IP on LAN bridge, 10.10.10.2)
                ├── Proxmox Host "mesh1" (LAN node, 10.10.10.210, via ProxyJump)
                │   ├── SSH: controller → home (.201) → mesh1 (.210 via LAN bridge)
                │   └── OpenWrt Mesh LXC (VMID 103, WiFi PHY namespace move)
                │
                ├── WireGuard VPN (VMID 101, on home and mesh1)
                │   └── wg0 tunnel → home server
                │       ├── rsyslog forwards logs through tunnel
                │       ├── Netdata streams metrics through tunnel
                │       └── Remote management access
                │
                ├── Pi-hole (VMID 102) ← OpenWrt forwards DNS here
                ├── Mesh WiFi Controller (VMID 103)
                ├── Home Assistant (VMID 200)
                ├── Jellyfin (VMID 300)
                ├── Kodi (VMID 301)
                ├── Moonlight Client (VMID 302)
                ├── Debian Desktop (VMID 400)
                ├── Custom UX Kiosk (VMID 401)
                ├── Netdata (VMID 500)
                └── rsyslog (VMID 501)
```

### WAN Bridge Detection

The WAN bridge is detected automatically at runtime by `proxmox_bridges`:
the bridge carrying the Proxmox host's default route is the one connected
to the upstream network. The `openwrt_vm` role then orders NICs so the
WAN bridge always maps to `net0`/`eth0`, with remaining bridges becoming
LAN ports (`net1+`/`eth1+`).

Override auto-detection by setting `openwrt_wan_bridge` in `host_vars`
(e.g., `openwrt_wan_bridge: vmbr1`).

### Proxmox LAN Management IP

After OpenWrt configures the LAN subnet, `openwrt_configure` gives the
Proxmox host a predictable IP on the LAN bridge (default: `.2` in the
LAN subnet, e.g., `10.10.10.2`). This ensures the Proxmox GUI is reachable
from leaf nodes regardless of which physical port connects upstream.

The IP is **dynamic at startup, then stable**:

1. `ip addr add` applies the IP immediately for the current session
2. The LAN bridge stanza in `ansible-bridges.conf` is upgraded from
   `inet manual` to `inet dhcp`
3. A DHCP static reservation on OpenWrt maps the Proxmox host's LAN
   bridge MAC to the computed IP
4. On every boot, the DHCP client acquires the same reserved IP
5. Any stale LAN-subnet IPs on non-LAN bridges are removed to prevent
   routing conflicts (duplicate /24 routes on different bridges)

This replaces the earlier `ansible-proxmox-lan.conf` approach, which
used a separate static config file that conflicted with the bridge's
`inet manual` stanza and broke `ifreload -a`.

### WAN MAC Address Cloning

When replacing an existing router, the ISP may tie its DHCP lease to the
old router's MAC address. Set `WAN_MAC` in `.env` to clone the old MAC
onto OpenWrt's WAN interface.

Before applying the MAC, the build runs a three-layer conflict detection:
1. Exact MAC match in the ARP table
2. EUI-64 match in the IPv6 neighbor table (SLAAC collision)
3. Gateway MAC shares OUI with the cloned MAC (same-device indicator)

If no conflict is detected, the MAC is applied via UCI
(`network.wan.macaddr`) during the final configure phase. If a conflict
IS detected (e.g., old router still connected on the same L2 segment),
the MAC is saved to `/etc/openwrt_wan_mac_deferred` on the VM and an
init script is deployed. On every boot, the init script re-runs the
conflict detection and automatically applies the MAC once the
conflicting device is removed — no manual intervention needed.

Omit `WAN_MAC` entirely to use the auto-generated virtio MAC (default).

### WiFi Strategy: VM Passthrough vs LXC PHY Namespace Move

The project supports two WiFi strategies, selected by group membership:

| Strategy | Target hosts | Method | Requirements |
|----------|-------------|--------|-------------|
| PCIe passthrough | `router_nodes` | `vfio-pci` binding, full device isolation | VT-d/IOMMU in BIOS, q35 machine type |
| PHY namespace move | `wifi_nodes:!router_nodes` | `iw phy set netns` into LXC container | Privileged container, `iw` tool on host |

**PCIe passthrough** (router nodes): The WiFi PCI device is unbound from the host
driver and bound to `vfio-pci`. The OpenWrt VM gets exclusive access. This is the
full router build with WAN/LAN routing, DHCP, firewall, and mesh root.

**PHY namespace move** (mesh satellite nodes): The host WiFi driver stays loaded.
After the OpenWrt LXC container starts, the WiFi PHY is moved into the container's
network namespace via `iw phy <phy> set netns <pid>`. The container sees the radio
and configures 802.11s mesh interfaces via UCI. No routing — mesh peer only.

A Proxmox hookscript (`/var/lib/vz/snippets/mesh-wifi-phy-<CT_ID>.sh`) re-does the
PHY move after container restart, ensuring persistence across host reboots.

### Bridge Mapping (dynamic)

| Bridge | Role | OpenWrt interface |
|--------|------|-------------------|
| `proxmox_wan_bridge` (auto-detected) | WAN | `eth0` |
| All other bridges | LAN | `eth1..N` |

### LXC Container Networking (host topology)

LXC container networking must match host topology:

- **Hosts behind OpenWrt** (`router_nodes`, `lan_hosts`): Containers use the LAN bridge (second bridge) and OpenWrt LAN subnet (gateway from `env_generated_path`).
- **Hosts directly on WAN** (e.g., `ai`, `mesh2`): Containers use `proxmox_wan_bridge` and the host's WAN subnet (`ansible_default_ipv4.gateway`/prefix). IP offset +200 avoids collisions with LAN containers. DNS: `8.8.8.8`.

---

## Playbook Execution Order

The playbook (`playbooks/site.yml`) runs plays in sequence. Plays targeting
flavor groups the host doesn't belong to are automatically skipped.

### Current (v1.4) — Phased Multi-Node

```
site.yml (current — phased for multi-node)
│
├── Phase 1: Primary hosts (proxmox:!lan_hosts — directly reachable)
│   ├── Play 0:  proxmox:!lan_hosts  [backup]     proxmox_backup, deploy_stamp
│   ├── Play 1:  proxmox:!lan_hosts  [infra]      pre_tasks: NTP clock sync; proxmox_bridges, proxmox_pci_passthrough, proxmox_igpu, deploy_stamp
│   ├── Play 2:  router_nodes        [openwrt]    openwrt_vm, deploy_stamp
│   └── Play 3:  openwrt             [openwrt]    openwrt_configure
│
├── Phase 2: LAN satellites (reachable after OpenWrt creates the LAN)
│   ├── Play 4:  router_nodes        [lan-satellite]  bootstrap_lan_host (loop lan_hosts)
│   ├── Play 5:  lan_hosts           [lan-satellite]  proxmox_backup, deploy_stamp
│   └── Play 6:  lan_hosts           [lan-satellite]  pre_tasks: NTP clock sync; proxmox_bridges, proxmox_pci_passthrough, proxmox_igpu, deploy_stamp
│
├── Phase 3: Services (flavor groups span primary + LAN hosts)
│   ├── Play 7:  vpn_nodes           [wireguard]  wireguard_lxc, deploy_stamp
│   ├── Play 8:  wireguard           [wireguard]  wireguard_configure
│   ├── Play 9:  wifi_nodes:!router_nodes [mesh-wifi]  openwrt_mesh_lxc, deploy_stamp
│   └── Play 10: openwrt_mesh        [mesh-wifi]  openwrt_mesh_configure
│
├── Per-feature plays (opt-in via --tags <name>, tagged with [never]):
│   ├── Play 11: openwrt             [openwrt-security]   include_role: openwrt_configure/security.yml
│   ├── Play 12: router_nodes        [openwrt-security]   deploy_stamp (openwrt_security)
│   ├── Play 13: openwrt             [openwrt-vlans]      include_role: openwrt_configure/vlans.yml
│   ├── Play 14: router_nodes        [openwrt-vlans]      deploy_stamp (openwrt_vlans)
│   ├── Play 15: openwrt             [openwrt-dns]        include_role: openwrt_configure/dns.yml
│   ├── Play 16: router_nodes        [openwrt-dns]        deploy_stamp (openwrt_dns)
│   ├── Play 17: openwrt             [openwrt-mesh]       include_role: openwrt_configure/mesh.yml
│   └── Play 18: router_nodes        [openwrt-mesh]       deploy_stamp (openwrt_mesh)
│
└── Play 19: proxmox:!lan_hosts      [cleanup]    Remove bootstrap IP
```

The phased approach ensures LAN hosts (behind the OpenWrt router) are only
contacted after the router is provisioned and the LAN bridge exists. Service
plays in Phase 3 use flavor groups that span all hosts (e.g., `vpn_nodes`
includes `home`, `mesh1`, `ai`, and `mesh2`), so Ansible runs tasks on all
4 hosts in parallel within each play.

Future integration plays (added by downstream projects when implemented):
- `openwrt-pihole-dns` — added by Pi-hole LXC project
- `openwrt-syslog` — added by rsyslog LXC project
- `openwrt-monitoring` — added by monitoring project

### Target (Full Build)

```
site.yml (target)
│
├── Phase: Backup
│   └── Play 0:  proxmox          [backup]      proxmox_backup, deploy_stamp
│
├── Phase: Infrastructure
│   └── Play 1:  proxmox          [infra]       proxmox_bridges, proxmox_pci_passthrough, proxmox_igpu, deploy_stamp
│
├── Phase: Network Tier
│   ├── Play 2:  router_nodes     [openwrt]     openwrt_vm, deploy_stamp
│   ├── Play 3:  openwrt          [openwrt]     openwrt_configure
│   ├── Play 4:  vpn_nodes        [wireguard]   wireguard_lxc, deploy_stamp
│   ├── Play 5:  wireguard        [wireguard]   wireguard_configure
│   ├── Play 6:  dns_nodes        [pihole]      pihole_lxc, deploy_stamp
│   └── Play 7:  pihole           [pihole]      pihole_configure
│
├── Phase: Observability Tier
│   ├── Play 8:  monitoring_nodes [monitoring]  rsyslog_lxc, netdata_lxc, deploy_stamp
│   ├── Play 9:  rsyslog          [monitoring]  rsyslog_configure
│   └── Play 10: netdata          [monitoring]  netdata_configure
│
├── Phase: WiFi Management
│   ├── Play 11: wifi_nodes       [wifi]        meshwifi_lxc, deploy_stamp
│   └── Play 12: meshwifi         [wifi]        meshwifi_configure
│
├── Phase: Services
│   ├── Play 13: service_nodes    [services]    homeassistant_lxc, deploy_stamp
│   └── Play 14: homeassistant    [services]    homeassistant_configure
│
├── Phase: Media
│   ├── Play 15: media_nodes      [media]       jellyfin_lxc, kodi_lxc, moonlight_lxc, deploy_stamp
│   ├── Play 16: jellyfin         [media]       jellyfin_configure
│   ├── Play 17: kodi             [media]       kodi_configure
│   └── Play 18: moonlight        [media]       moonlight_configure
│
├── Phase: Desktop
│   ├── Play 19: desktop_nodes    [desktop]     desktop_vm, kiosk_lxc, deploy_stamp
│   ├── Play 20: desktop          [desktop]     desktop_configure
│   └── Play 21: kiosk            [desktop]     kiosk_configure
│
├── Phase: Gaming
│   ├── Play 22: gaming_nodes     [gaming]      gaming_vm, deploy_stamp
│   └── Play 23: gaming           [gaming]      gaming_configure
│
└── Phase: Cleanup
    └── Play 24: proxmox          [cleanup]     Remove bootstrap IPs, set startup order
```

Provision plays for services in the same tier targeting the same flavor group
are combined (e.g., `rsyslog_lxc` + `netdata_lxc` both on `monitoring_nodes`).
Configure plays stay separate because they target different dynamic groups.

---

## Two-Role Pattern

Every service has exactly two roles:

```
Service Role Pattern
├── <type>_vm (for VMs) or <type>_lxc (for containers)
│   ├── Targets: flavor group (e.g., router_nodes, media_nodes)
│   ├── VM roles: qm create, disk import, NIC attach, start, add_host
│   ├── LXC roles: include proxmox_lxc helper, then add_host
│   └── Always paired with deploy_stamp
│
└── <type>_configure
    ├── Targets: dynamic group (populated by add_host during provisioning)
    ├── LXC: configured via pct exec (no SSH, no bootstrap IP needed)
    └── VM: configured via SSH through ProxyJump
```

### Shared Infrastructure Roles

```
Shared Roles (run once per host, before any service roles)
├── proxmox_backup
│   ├── Runs on: proxmox (all hosts)
│   ├── Purpose: Tar host config + vzdump all VMs
│   └── Exports: backup manifest
│
├── proxmox_bridges
│   ├── Runs on: proxmox
│   ├── Purpose: Discover physical NICs, create virtual bridges
│   └── Exports: proxmox_all_bridges, proxmox_wan_bridge
│
├── proxmox_pci_passthrough
│   ├── Runs on: proxmox
│   ├── Purpose: vfio-pci binding for exclusive devices (WiFi, discrete GPU)
│   └── Exports: wifi_pci_devices (future: gpu_pci_devices)
│
├── proxmox_igpu
│   ├── Runs on: proxmox
│   ├── Purpose: Detect iGPU (Intel or AMD), load driver, install VA-API, export device info
│   ├── Requirement: iGPU MUST be present — Intel (i915) or AMD (amdgpu) (hard fail if absent)
│   └── Exports: igpu_available, igpu_vendor, igpu_pci_address, igpu_render_device,
│                igpu_card_device, igpu_render_gid, igpu_video_gid
│
├── proxmox_lxc (helper -- included by other roles, not a standalone play)
│   ├── Purpose: Template download, pct create, networking, start, add_host
│   ├── Parameterized: ct_id, ct_memory, ct_cores, ct_disk, ct_bridge, lxc_ct_ostype, etc.
│   └── Consumed by: every <type>_lxc role via include_role
│
└── deploy_stamp
    ├── Runs on: proxmox
    ├── Purpose: Write project version + play history to /etc/ansible/facts.d/
    └── Exports: ansible_local.vm_builds
```

### Service Roles

```
Service Roles
├── Network Tier
│   ├── openwrt_vm / openwrt_configure           VM   VMID 100   router_nodes   → openwrt
│   ├── wireguard_lxc / wireguard_configure       LXC  VMID 101   vpn_nodes      → wireguard
│   ├── pihole_lxc / pihole_configure             LXC  VMID 102   dns_nodes      → pihole
│   ├── meshwifi_lxc / meshwifi_configure         LXC  VMID 103   wifi_nodes     → meshwifi
│   └── openwrt_mesh_lxc / openwrt_mesh_configure  LXC  VMID 103   wifi_nodes:!router_nodes → openwrt_mesh
│
├── Observability Tier
│   ├── netdata_lxc / netdata_configure           LXC  VMID 500   monitoring_nodes → netdata
│   └── rsyslog_lxc / rsyslog_configure           LXC  VMID 501   monitoring_nodes → rsyslog
│
├── Service Tier
│   └── homeassistant_lxc / homeassistant_configure  LXC  VMID 200  service_nodes → homeassistant
│
├── Media Tier
│   ├── jellyfin_lxc / jellyfin_configure         LXC  VMID 300   media_nodes    → jellyfin
│   ├── kodi_lxc / kodi_configure                 LXC  VMID 301   media_nodes    → kodi
│   └── moonlight_lxc / moonlight_configure       LXC  VMID 302   media_nodes    → moonlight
│
├── Desktop Tier
│   ├── desktop_vm / desktop_configure            VM   VMID 400   desktop_nodes  → desktop
│   └── kiosk_lxc / kiosk_configure               LXC  VMID 401   desktop_nodes  → kiosk
│
└── Gaming
    └── gaming_vm / gaming_configure              VM   VMID 600   gaming_nodes   → gaming
```

---

## VMID Allocation

```
VMID Ranges
├── 100-199  Network
│   ├── 100  OpenWrt Router
│   ├── 101  WireGuard VPN
│   ├── 102  Pi-hole
│   └── 103  Mesh WiFi Controller
│
├── 200-299  Core Services
│   └── 200  Home Assistant
│
├── 300-399  Media
│   ├── 300  Jellyfin
│   ├── 301  Kodi
│   └── 302  Moonlight Client
│
├── 400-499  Desktop / UI
│   ├── 400  Debian Desktop
│   └── 401  Custom UX Kiosk
│
├── 500-599  Observability
│   ├── 500  Netdata
│   └── 501  rsyslog
│
└── 600-699  Gaming
    └── 600  Gaming VM
```

All VMIDs are defined in `inventory/group_vars/all.yml`.

---

## Variable Scoping

```
Variable Locations
├── Secrets & Environment
│   ├── Source: .env file (gitignored)
│   ├── Required vars: lookup('env', 'VAR_NAME') in group_vars/proxmox.yml
│   ├── Optional vars: lookup('env', 'VAR_NAME') in role defaults with fallback
│   └── Contains: API tokens, passphrases, WAN MAC, service passwords
│
├── Shared Parameters
│   ├── Source: inventory/group_vars/all.yml
│   └── Contains: VMIDs, image paths, LXC template name, storage pool
│
├── Proxmox Connection
│   ├── Source: inventory/group_vars/proxmox.yml
│   └── Contains: API auth, SSH settings, connection parameters
│
├── Per-Host Overrides
│   ├── Source: inventory/host_vars/<hostname>.yml
│   └── Contains: host IP, reboot policy, hardware-specific settings
│
├── Role Defaults
│   ├── Source: roles/<role>/defaults/main.yml
│   ├── Contains: role-specific parameters (VMID, memory, cores, disk)
│   └── Rule: NEVER cross-reference another role's defaults
│
└── Cross-Role Data
    ├── Method: set_fact (cacheable) or add_host
    └── Contains: runtime facts, dynamic group membership
```

---

## Project Structure

```
vm_builds/
├── ansible.cfg
├── build.py                      Python entry point (env validation, playbook runner)
├── setup.sh                      Bootstrap .venv + pip + ansible-galaxy
├── run.sh                        Source .env, run ansible-playbook
├── cleanup.sh                    Restore / full-restore / clean subcommands
├── test.env                      Test machine config (committed)
├── .env                          Production secrets (gitignored)
│
├── inventory/
│   ├── hosts.yml                 Hosts + flavor groups + empty dynamic groups
│   ├── group_vars/
│   │   ├── all.yml               VMIDs, image paths, LXC templates, storage
│   │   ├── proxmox.yml           API auth, SSH settings
│   │   └── lan_hosts.yml         ProxyJump SSH config for LAN-reachable hosts
│   └── host_vars/
│       ├── home.yml              Per-host overrides (primary node, direct SSH)
│       ├── mesh1.yml             LAN node (10.10.10.210, ProxyJump via home)
│       ├── ai.yml                AI node (192.168.86.220, direct SSH)
│       └── mesh2.yml             Mesh node 2 (192.168.86.211, direct SSH)
│
├── playbooks/
│   ├── site.yml                  Main orchestration playbook
│   └── cleanup.yml               Tag-driven restore playbook
│
├── roles/
│   ├── Shared Infrastructure
│   │   ├── proxmox_backup/
│   │   ├── proxmox_bridges/
│   │   ├── proxmox_pci_passthrough/
│   │   ├── proxmox_igpu/
│   │   ├── proxmox_lxc/          Shared LXC provisioning helper
│   │   └── deploy_stamp/
│   │
│   ├── Network Tier
│   │   ├── openwrt_vm/
│   │   ├── openwrt_configure/
│   │   ├── wireguard_lxc/
│   │   ├── wireguard_configure/
│   │   ├── pihole_lxc/
│   │   ├── pihole_configure/
│   │   ├── meshwifi_lxc/
│   │   ├── meshwifi_configure/
│   │   ├── openwrt_mesh_lxc/
│   │   └── openwrt_mesh_configure/
│   │
│   ├── Observability Tier
│   │   ├── rsyslog_lxc/
│   │   ├── rsyslog_configure/
│   │   ├── netdata_lxc/
│   │   └── netdata_configure/
│   │
│   ├── Service Tier
│   │   ├── homeassistant_lxc/
│   │   └── homeassistant_configure/
│   │
│   ├── Media Tier
│   │   ├── jellyfin_lxc/
│   │   ├── jellyfin_configure/
│   │   ├── kodi_lxc/
│   │   ├── kodi_configure/
│   │   ├── moonlight_lxc/
│   │   └── moonlight_configure/
│   │
│   ├── Desktop Tier
│   │   ├── desktop_vm/
│   │   ├── desktop_configure/
│   │   ├── kiosk_lxc/
│   │   └── kiosk_configure/
│   │
│   └── Gaming
│       ├── gaming_vm/
│       └── gaming_configure/
│
├── tasks/
│   ├── reconstruct_openwrt_group.yml     Reusable dynamic group reconstruction (OpenWrt)
│   ├── reconstruct_wireguard_group.yml   Reusable dynamic group reconstruction (WireGuard)
│   ├── bootstrap_lan_host.yml           SSH key check, DHCP lease, API token for LAN nodes
│   └── cleanup_lan_host.yml             Reusable per-LAN-host cleanup (SSH from primary)
│
├── molecule/
│   ├── default/                   Full integration tests (home, mesh1, ai, mesh2 — 4-node)
│   ├── openwrt-security/          Per-feature: security hardening
│   ├── openwrt-vlans/             Per-feature: VLAN segmentation
│   ├── openwrt-dns/               Per-feature: encrypted DNS
│   ├── openwrt-mesh/              Per-feature: mesh enhancements
│   ├── wireguard-lxc/             Per-feature: WireGuard VPN container
│   └── mesh1-infra/               Lightweight infra-only on mesh1 (quick iteration)
│
├── images/                        VM/LXC images (gitignored, built by build-images.sh)
│
├── image-builder/                 OpenWrt Image Builder config
│   └── files-mesh-lxc/           UCI defaults baked into mesh LXC rootfs
│
├── docs/
│   ├── architecture/              Design documentation
│   └── projects/                  Per-service project plans
│
└── .cursor/
    ├── rules/                     Always-on coding conventions
    └── skills/                    On-demand knowledge for LLM sessions
```

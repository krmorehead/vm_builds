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

Small-form-factor PC, Intel CPU (iGPU for Quick Sync), 8 GB RAM, 2+ ethernet
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

```
Build Profiles
├── Home Entertainment Box
│   ├── router_nodes       → OpenWrt
│   ├── vpn_nodes          → WireGuard
│   ├── dns_nodes          → Pi-hole
│   ├── wifi_nodes         → Mesh WiFi Controller
│   ├── monitoring_nodes   → Netdata, rsyslog
│   ├── service_nodes      → Home Assistant
│   ├── media_nodes        → Jellyfin, Kodi, Moonlight
│   └── desktop_nodes      → Debian Desktop, UX Kiosk
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

The Intel iGPU serves two distinct purposes on the home entertainment box:

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
│   ├── Host keeps i915 driver loaded
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
    ├── 2. iGPU unbound from i915, bound to vfio-pci
    ├── 3. Desktop VM gets exclusive GPU
    ├── 4. Jellyfin switches to software transcoding
    └── 5. On stop → iGPU returns to i915, Kiosk restarts
```

Managed by Proxmox hookscripts on the host, with Ansible pre-tasks
as enforcement during playbook runs.

### iGPU vs PCI Passthrough -- Role Decomposition

```
PCI Device Handling (separate roles)
├── proxmox_pci_passthrough
│   ├── Purpose: Exclusive device passthrough (WiFi, discrete GPU)
│   ├── Method: Unbind from host driver, bind to vfio-pci
│   ├── Exports: wifi_pci_devices, gpu_pci_devices
│   └── Consumer: openwrt_vm (WiFi), gaming_vm (discrete GPU)
│
└── proxmox_igpu
    ├── Purpose: iGPU detection and fact export for containers and VMs
    ├── Method: Keep host i915 driver loaded, export device paths and PCI address
    ├── Exports: igpu_render_device, igpu_card_device, igpu_render_gid, igpu_pci_address
    ├── LXC consumers (shared bind mount): jellyfin_lxc, kodi_lxc, moonlight_lxc, kiosk_lxc
    └── VM consumer (exclusive hostpci): desktop_vm (takes GPU from host when running)
```

---

## Network Topology

```
Internet
└── Upstream ISP Router
    └── WAN (DHCP)
        └── OpenWrt VM (VMID 100)
            ├── eth0 ← auto-detected WAN bridge (bridge with default route)
            ├── eth1..N ← remaining bridges (LAN)
            ├── wlan0 ← PCIe passthrough (802.11s mesh)
            │
            └── LAN Network (all other services connect here)
                ├── Proxmox Host (LAN management IP on LAN bridge)
                ├── WireGuard VPN (VMID 101)
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

After OpenWrt configures the LAN subnet, the `openwrt_configure` role adds
a static IP to the LAN bridge on the Proxmox host (default: `.2` in the
LAN subnet, e.g., `10.10.10.2`). This ensures the Proxmox GUI is reachable
from leaf nodes regardless of which physical port connects upstream.

The IP is persisted to `/etc/network/interfaces.d/ansible-proxmox-lan.conf`
so it survives reboots.

### WAN MAC Address Cloning

When replacing an existing router, the ISP may tie its DHCP lease to the
old router's MAC address. Set `WAN_MAC` in `.env` to clone the old MAC
onto OpenWrt's WAN NIC (`net0`). This is applied at the Proxmox VM level
so the cloned MAC appears on the wire — no OpenWrt configuration needed.

Omit `WAN_MAC` entirely to use the auto-generated virtio MAC (default).

### Bridge Mapping (dynamic)

| Bridge | Role | OpenWrt interface |
|--------|------|-------------------|
| `proxmox_wan_bridge` (auto-detected) | WAN | `eth0` |
| All other bridges | LAN | `eth1..N` |

All LXC containers and VMs (except OpenWrt) attach to a LAN bridge.

---

## Playbook Execution Order

The playbook (`playbooks/site.yml`) runs plays in sequence. Plays targeting
flavor groups the host doesn't belong to are automatically skipped.

### Current (v1.0)

```
site.yml (current)
├── Play 0:  proxmox        [backup]     proxmox_backup, deploy_stamp
├── Play 1:  proxmox        [infra]      proxmox_bridges, proxmox_pci_passthrough, deploy_stamp
├── Play 2:  router_nodes   [openwrt]    openwrt_vm, deploy_stamp
├── Play 3:  openwrt        [openwrt]    openwrt_configure
└── Play 4:  proxmox        [cleanup]    Remove bootstrap IP
```

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
│   └── Exports: proxmox_all_bridges
│
├── proxmox_pci_passthrough
│   ├── Runs on: proxmox
│   ├── Purpose: vfio-pci binding for exclusive devices (WiFi, discrete GPU)
│   └── Exports: wifi_pci_devices, gpu_pci_devices
│
├── proxmox_igpu
│   ├── Runs on: proxmox
│   ├── Purpose: Detect Intel iGPU, verify Quick Sync, export device info
│   └── Exports: igpu_render_device, igpu_render_gid, igpu_available
│
├── proxmox_lxc (helper -- included by other roles, not a standalone play)
│   ├── Purpose: Template download, pct create, networking, start, add_host
│   ├── Parameterized: ct_id, ct_memory, ct_cores, ct_disk, ct_bridge, etc.
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
│   └── meshwifi_lxc / meshwifi_configure         LXC  VMID 103   wifi_nodes     → meshwifi
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
│   │   └── proxmox.yml           API auth, SSH settings
│   └── host_vars/
│       └── home.yml              Per-host overrides
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
│   │   └── meshwifi_configure/
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
├── molecule/default/              Integration tests
├── images/                        VM disk images (gitignored)
│
├── docs/
│   ├── architecture/              Design documentation
│   └── projects/                  Per-service project plans
│
└── .cursor/
    ├── rules/                     Always-on coding conventions
    └── skills/                    On-demand knowledge for LLM sessions
```

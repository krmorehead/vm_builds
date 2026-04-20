# Roadmap

## Current State (v1.1)

Full-stack home infrastructure automation deploying 15 services across 6
Proxmox nodes (home, mesh1, ai, mesh2, bridge-1, bridge-2):

- **Network**: OpenWrt router VM, WireGuard VPN, Pi-hole DNS, Mesh WiFi, WiFi Bridge
- **Services**: Home Assistant, rsyslog log collector, Netdata monitoring
- **Media**: Jellyfin (hardware transcoding), Kodi (direct display), Moonlight (streaming client)
- **Desktop**: Debian Desktop VM (KDE + GNOME), Custom UX Kiosk
- **Gaming**: Gaming LXC with Sunshine streaming server (opt-in)
- **Management**: NiceGUI Web UI with deploy profiles, fleet monitoring, kiosk dashboard
- **Fleet monitoring**: Callhome heartbeat agents on every container, central API, circuit breaker
- **Testing**: Molecule integration tests (~20 scenarios), pytest suite, TDD workflow
- **Images**: All services use pre-built images (`build-images.sh`); zero runtime package installs
- **Entry point**: `build.py` handles env validation, host probing, state fallback; Web UI wraps it

## Active Projects

Project plans live in `docs/projects/`. Each follows the conventions in the
`project-planning` skill: milestones with inline verify, rollback, and
dependency tracking.

### `2026-03-09-01` OpenWrt Router (Hardening & Features)

Establishes the **baseline testing model** and **per-feature rollback
conventions**, then adds security hardening, VLANs, encrypted DNS, and mesh
enhancements on top of the existing router.

Key deliverables:
- Per-feature molecule scenarios (fast iteration without full rebuild)
- Per-feature rollback tags in `cleanup.yml`
- Root password, SSH keys, banIP intrusion prevention
- VLAN segmentation (management, IoT, guest)
- Encrypted upstream DNS via `https-dns-proxy` (DoH)
- Dawn client steering for 802.11k/v/r mesh

Blocked milestones (waiting on LXC projects):
- Pi-hole DNS forwarding chain
- Syslog forwarding to rsyslog collector
- Prometheus metrics export

### `2026-03-11-00` Multi-Node Test Infrastructure ✓

Adds a second Proxmox test node (`mesh1`) behind the OpenWrt router's LAN,
establishes reusable SSH ProxyJump patterns, and validates shared infrastructure
roles on different hardware.

Delivered:
- `lan_hosts` inventory group with ProxyJump through primary host
- `tasks/bootstrap_lan_host.yml` for DHCP lease + API token provisioning
- `molecule/mesh1-infra/` scenario (converge, verify, cleanup)
- Scalable env var convention: `<HOSTNAME>_API_TOKEN` with dynamic lookup
- `proxmox_bridges` single-NIC tolerance for non-router hosts
- `proxmox_pci_passthrough` IOMMU group validation and graceful degradation
- `multi-node-ssh` skill documenting LAN host patterns

### `2026-03-09-00` Shared Infrastructure ✓

Framework for LXC container provisioning, iGPU detection, VMID allocation,
flavor groups, and auto-start configuration. Complete.

Delivered:
- `proxmox_lxc` shared role (parameterized, reusable, `pct_remote` connection)
- `proxmox_igpu` role (i915 loading, Quick Sync verification, vainfo, fact export)
- Full VMID allocation scheme (100–699) in `group_vars/all.yml`
- Inventory flavor groups and build profiles (`docs/architecture/build-profiles.md`)
- Auto-start configuration (startup order table, `proxmox_lxc` native support)
- Proxmox repo management (enterprise → no-subscription, DNS fallback)
- Per-feature Molecule scenarios (`proxmox-lxc`, `proxmox-igpu`)

Relocated to other projects:
- Display-exclusive hookscripts → Custom UX Kiosk (`2026-03-09-12`, M5)
- WiFi passthrough coexistence → OpenWrt Router (`2026-03-09-01`, M0)
- Resource validation → future operations project

## Short-Term Goals

### OpenWrt Hardening (project 01, M1)
- Set a root password and deploy SSH keys (disable password auth).
- Install and configure `banIP` for intrusion prevention.
- Firewall tightening: SYN flood protection, invalid packet drop.

### VLAN Support (project 01, M2)
- Tag LAN ports with VLAN IDs for network segmentation (IoT, guest, management).
- Create separate firewall zones and DHCP pools per VLAN.

### Encrypted DNS (project 01, M3)
- Install `https-dns-proxy` for DNS-over-HTTPS to upstream resolvers.
- DNS rebinding protection in dnsmasq.

### Multi-Node Mesh (project 01, M4)
- Deploy Dawn (802.11k/v/r) for client steering across mesh nodes.
- Centralized mesh configuration across all nodes.

### ~~LXC Framework (project 00, M1–M4)~~ ✓
- ~~Shared `proxmox_lxc` role for container provisioning.~~ Done.
- ~~iGPU detection for media containers.~~ Done.
- ~~Flavor groups and build profiles in inventory.~~ Done.

### `2026-03-09-02` WireGuard VPN Client ✓

First LXC container in the project. Lightweight container running a WireGuard
client that maintains a persistent VPN tunnel. Other services route through
this tunnel for remote access.

Delivered:
- `wireguard_lxc` role (thin wrapper around `proxmox_lxc`, host-side kernel module)
- `wireguard_configure` role (key auto-generation, `.env.generated` pattern, wg0
  config, IP forwarding, iptables NAT/MASQUERADE)
- Per-feature molecule scenario (`wireguard-lxc`)
- `tasks/reconstruct_wireguard_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`wireguard-rollback` tag)
- Full verify coverage: container state, auto-start, nesting, kernel module,
  wg0 interface, service enabled, IP forwarding, NAT, `.env.generated`

### `2026-03-09-03` Pi-hole DNS Filtering ✓

LXC container running Pi-hole for network-wide DNS-level ad and tracker
blocking. OpenWrt's dnsmasq forwards DNS queries to Pi-hole.

Delivered:
- `pihole_lxc` role (thin wrapper around `proxmox_lxc`, custom Debian 12 template)
- `pihole_configure` role (pihole-FTL CLI config, web password, upstream DNS, gravity update)
- `openwrt_configure/tasks/pihole_dns.yml` (dnsmasq server list: Pi-hole + DoH fallback)
- Per-feature molecule scenarios (`pihole-lxc`, `openwrt-pihole-dns`)
- `tasks/reconstruct_pihole_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`pihole-rollback`, `openwrt-pihole-dns-rollback`)
- Pi-hole image build section in `build-images.sh` (Debian 12 + Pi-hole baked in)
- Full verify coverage: container state, auto-start, nesting, FTL, web admin, DNS, ad blocking

### `2026-03-09-04` rsyslog Log Collector ✓

Minimal LXC container running rsyslog as a centralized log collector.
All containers and VMs forward their logs here. Supports optional forwarding
to a home server via WireGuard tunnel.

Delivered:
- `rsyslog_lxc` role (thin wrapper around `proxmox_lxc`, topology-aware networking)
- `rsyslog_configure` role (optional forwarding via `RSYSLOG_HOME_SERVER`, disk-assisted queue)
- Custom Debian 12 template with rsyslog TCP receiver pre-configured (built by `build-images.sh`)
- `openwrt_configure/tasks/syslog.yml` (UCI log_ip/log_port/log_proto forwarding)
- Per-feature molecule scenarios (`rsyslog-lxc`, `openwrt-syslog`)
- `tasks/reconstruct_rsyslog_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`rsyslog-rollback`, `openwrt-syslog-rollback`)
- Full verify coverage: container state, auto-start, service, TCP listener, spool dir, log reception

### `2026-03-09-05` Netdata Monitoring Agent ✓

Lightweight LXC container running Netdata for host-level monitoring. Bind
mounts `/proc` and `/sys` read-only for CPU, memory, disk, and temperature
metrics. Optional child-parent streaming via WireGuard (soft dependency).

Delivered:
- `netdata_lxc` role (thin wrapper around `proxmox_lxc`, bind mounts for host metrics)
- `netdata_configure` role (optional streaming via `NETDATA_PARENT_IP` + `NETDATA_STREAM_API_KEY`)
- Custom Debian 12 template with Netdata pre-installed and pre-configured (built by `build-images.sh`)
- Per-feature molecule scenario (`netdata-lxc`)
- `tasks/reconstruct_netdata_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`netdata-rollback` tag)
- Full verify coverage: container state, auto-start, bind mounts, service, dashboard, host metrics

### `2026-03-09-08` Jellyfin Media Server ✓

LXC container running Jellyfin media server with Intel Quick Sync hardware transcoding via iGPU device passthrough. Serves media to clients locally and remotely, offloading transcoding to GPU for minimal CPU usage.

Delivered:
- `jellyfin_lxc` role (thin wrapper around `proxmox_lxc`, iGPU + media path mounting)
- `jellyfin_configure` role (admin user setup, iGPU access, transcoding config, VA-API verification)
- Custom Debian 12 template with Jellyfin + VA-API drivers baked in (built by `build-images.sh`)
- Per-feature molecule scenario (`jellyfin-lxc`)
- `tasks/reconstruct_jellyfin_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`jellyfin-rollback` tag)
- Full integration testing in `molecule/default/verify.yml`
- Hardware-accelerated transcoding with automatic software fallback
- Support for both Intel (i915) and AMD (amdgpu) GPUs via dual VA-API drivers

### `2026-03-09-09` Kodi Media Player ✓

LXC container running Kodi as a local media player and home theater frontend. Renders directly to the physical display via GBM/DRM using the shared iGPU. HDMI audio output via ALSA bind mount. On-demand container (not auto-start).

Delivered:
- `kodi_lxc` role (device mounts for DRI/sound/input, cgroup allowlists)
- `kodi_configure` role (iGPU render group, ALSA HDMI audio, web interface)
- Custom Debian 12 template with kodi-standalone + GBM/DRM + Mesa + libcec baked in
- Per-feature molecule scenario (`kodi-lxc`)
- `tasks/reconstruct_kodi_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`kodi-rollback` tag)
- Full integration testing in `molecule/default/verify.yml`
- Display-exclusive support (hookscript attachment, deployed by Kiosk project)

### `2026-03-21-14` Sunshine Streaming Server

Windows 11 VM with iGPU PCI passthrough running Sunshine as a Moonlight
streaming host. Uses vfio-pci for hardware encoding on all 4 test nodes.
In production, this deploys to `gaming_nodes` (dedicated gaming hardware)
where the Gaming Rig project extends it with discrete GPU passthrough.

Delivered:
- `gaming_vm` role (q35/OVMF VM, iGPU vfio-pci binding, QEMU Guest Agent IP discovery)
- `gaming_configure` role (Sunshine credentials, firewall rules, GZDoom verification)
- Windows 11 image build section in `build-images.sh` (Tiny11 + autounattend + Sunshine + GZDoom)
- Per-feature molecule scenario (`sunshine-vm`) — all 4 nodes
- `tasks/reconstruct_gaming_group.yml` for dynamic group reconstruction
- Rollback plays in `playbooks/cleanup.yml` (`gaming-rollback` tag)
- Opt-in via `--tags gaming` (mutually exclusive with media container iGPU use)

### `2026-03-09-11` Debian Desktop LXC ✓

Full Debian 12 LXC container with KDE Plasma (Windows-style) and GNOME (Mac-style)
desktop sessions. Shares iGPU DRI render node via bind mount (managed by
display-exclusive hookscript). KasmVNC provides remote desktop access.

Delivered:
- `desktop_lxc` role (LXC container with DRI render node sharing)
- `desktop_configure` role (KDE + GNOME, GPU drivers, KasmVNC display service)
- KDE configured as Windows-style UX (bottom taskbar, dark Breeze theme)
- GNOME configured as Mac-style UX (Dash to Dock, dark Adwaita, Caps→Super)
- LXC rootfs tarball built via `build-images.sh` with KDE, GNOME, KasmVNC baked in
- Per-feature molecule scenario (`desktop-vm`)
- Rollback plays in `playbooks/cleanup.yml` (`desktop-rollback` tag)
- Display-exclusive hookscript attachment (deployed by Kiosk project)

### `2026-03-09-06` Home Assistant ✓

LXC container running Home Assistant via Docker-in-LXC. Provides home
automation with a web dashboard.

Delivered:
- `homeassistant_lxc` role (Docker-ready LXC with nesting)
- `homeassistant_configure` role (Docker compose, admin user, integration config)
- Per-feature molecule scenario (`homeassistant-lxc`)
- `tasks/reconstruct_homeassistant_group.yml` for dynamic group reconstruction

### `2026-03-09-10` Moonlight Streaming Client ✓

LXC container running Moonlight for streaming from a Sunshine server on a
different host. Uses iGPU for hardware decode and renders to physical display.

Delivered:
- `moonlight_lxc` role (iGPU + input device passthrough)
- `moonlight_configure` role (Sunshine server endpoint, display config)
- Per-feature molecule scenario (`moonlight-lxc`)
- Display-exclusive hookscript for Kiosk handoff

### `2026-03-09-12` Custom UX Kiosk ✓

LXC container running a NiceGUI-based kiosk dashboard (Home Hub). Manages
display-exclusive apps (Moonlight, Kodi, Desktop) via Proxmox hookscripts.
Deploys to all `desktop_nodes`.

Delivered:
- `kiosk_lxc` role (DRI + input + ALSA passthrough, Cage Wayland compositor)
- `kiosk_configure` role (config.json with service URLs, callhome agent)
- Proxmox hookscript for display-exclusive app lifecycle
- NiceGUI kiosk server (`scripts/webui/kiosk_server.py`)
- Per-feature molecule scenario (`kiosk-lxc`)

### `2026-03-09-13` Mesh WiFi LXC ✓

OpenWrt LXC containers with WiFi PHY namespace-moved from the host. WDS
STA links back to the router's hidden WDS AP for transparent L2 backhaul.

Delivered:
- `openwrt_mesh_lxc` role (PHY namespace move, kernel module loading)
- `openwrt_mesh_configure` role (WDS STA, batman-adv toggle)
- `tasks/configure_wifi_wds.yml` shared WDS configuration
- Per-feature molecule scenario (`mesh-wifi`)

### `2026-03-09-14` WiFi Bridge LXC ✓

Dedicated WiFi bridge containers for transparent L2 WDS AP/STA linking
between two bridge nodes.

Delivered:
- `openwrt_bridge_lxc` role (WiFi PHY passthrough, bridge topology)
- `openwrt_bridge_configure` role (WDS AP/STA configuration)
- Per-feature molecule scenario (`bridge-lxc`)

### `2026-03-09-15` Gaming LXC ✓

Fedora-based LXC container with GPU render device sharing for Sunshine
game streaming. Replaces the earlier Windows Gaming VM approach.

Delivered:
- `gaming_lxc` role (DRI render device, Fedora 41 template)
- `gaming_lxc_configure` role (Sunshine + dsda-doom, VA-API)
- Per-feature molecule scenario (`gaming-lxc`)
- Opt-in via `--tags gaming` (not included in Full Deploy)

### NiceGUI Web UI ✓

Full management interface for the project: deploy services, monitor fleet
health, manage kiosk displays.

Delivered:
- `scripts/webui/app.py` — main entry point (13 pages)
- Deploy profiles: Full, Home Unit, Mesh Unit, Gamer Unit, Bridge Units, etc.
- Live Ansible output streaming with cancel and dry-run
- Fleet health dashboard with heartbeat monitoring
- Kiosk server (`kiosk_server.py`) for per-host Home Hub display
- Manager API for SSH-based host operations
- `scripts/webui.sh` launcher script

### Callhome Fleet Monitoring ✓

Inverted data flow: containers heartbeat health to a central API, replacing
SSH polling for readiness checks.

Delivered:
- `scripts/callhome.py` (Python agent for Debian containers)
- `scripts/callhome.sh` (BusyBox agent for OpenWrt containers)
- Composable extensions: network, wireguard, docker, config_files, wifi
- Fleet readiness gate in verify.yml
- Circuit breaker for detecting container deaths mid-run
- API endpoints: `/api/checkin`, `/api/fleet/ready`, `/api/fleet/stale`

### 4-Tier Management Architecture ✓

Cluster-based management hierarchy for multi-node household networks
and national/remote hosts.

Delivered:
- `BaseManager` → `NodeManager` → `ClusterManager` OOP hierarchy in `manager.py`
- SuperManager (`app.py`): global fleet view across all clusters
- ClusterManager (`kiosk_server.py`, `IS_CLUSTER_MANAGER=true`): subnet-scoped fleet
  view, event broadcast (batman, bridge-wifi), child Manager discovery via
  `CHILD_MANAGER_IPS` on LAN (10.10.10.x)
- NodeManager (`kiosk_server.py`, default): per-host container ops, heartbeat relay
- Event propagation: batman enable/disable broadcasts from Cluster Manager to all
  Node Managers, which execute locally via `pct exec` → `batman_trigger.sh`
- Host-qualified status keys (`home/router-100`, `mesh1/mesh-103`) prevent collisions
- Dynamic container discovery via `pct status` — only probes running containers
- `kiosk_configure` role generates `CHILD_MANAGER_IPS` from `kiosk_static_ip` facts

## Medium-Term Goals

### Additional VM/LXC Types
- The project name is `vm_builds` (plural) — the architecture supports multiple service types.
- Each service type gets its own role pair: `<type>_lxc` + `<type>_configure` (or `<type>_vm`).
- VMID ranges pre-allocated: 100s network, 200s services, 300s media, 400s desktop, 500s observability, 600s gaming.
- See `docs/architecture/overview.md` for the full target architecture and `.cursor/skills/vm-lifecycle/SKILL.md` for implementation patterns.

### Backup and Recovery
- Automated VM snapshots before configuration changes.
- Export VM configs and disk images to NAS for disaster recovery.
- One-command restore from backup.

### CI/CD Pipeline
- Run Molecule tests automatically on push (GitHub Actions or similar).
- Lint and syntax checks on every commit.
- Integration tests on a schedule against the dedicated test node.

### Image Build Pipeline
- Build custom OpenWrt images with pre-installed packages using the OpenWrt Image Builder.
- Include `wpad-mesh-openssl`, monitoring agents, and custom UCI defaults in the image itself, reducing post-boot configuration time.

## Long-Term Vision

### Infrastructure as Code for the Home Network
- The entire home network — routing, switching, WiFi, DNS, firewall, VPN, monitoring — is defined in this repository.
- A new Proxmox node can be added to the inventory and fully provisioned in minutes.
- Configuration drift is detected and corrected by scheduled playbook runs.
- The repository serves as living documentation of the network topology.

### Hardware Abstraction
- Support heterogeneous hardware: x86 mini-PCs, ARM SBCs, rack servers.
- Automatic detection of hardware capabilities and appropriate role selection.
- Graceful degradation when hardware features (WiFi, multiple NICs) are absent.

### Multi-Site (enabled by 4-tier architecture)
- Each remote site is a **cluster** with its own Cluster Manager
- Remote clusters connect via WireGuard VPN to the SuperManager
- SuperManager provides global fleet view across all clusters
- Per-cluster autonomy: batman mode, WiFi management, and service health
  are managed locally by each Cluster Manager without SuperManager involvement
- Cross-site visibility without cross-site control — each cluster is
  tightly managed by its local end user

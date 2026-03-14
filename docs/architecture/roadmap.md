# Roadmap

## Current State (v1.0)

A single playbook that provisions and configures an OpenWrt router VM on Proxmox with:

- Dynamic NIC discovery and bridge-per-port passthrough.
- WiFi PCIe passthrough with IOMMU setup and in-VM driver installation.
- 802.11s mesh networking on all detected radios.
- First-bridge WAN assignment with collision-free LAN subnet selection.
- Router replacement workflow (stage/swap/downstream).
- Baseline firewall, DHCP, and DNS (dnsmasq).
- Environment-driven secrets (`.env`).
- Backup/restore with `vzdump` and host config tar archives.
- Integration test framework (Molecule) against a dedicated test node.
- LLM-optimized rules and skills for AI-assisted development continuity.

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

### Multi-Site
- Extend to multiple physical locations with site-to-site VPN (WireGuard).
- Centralized management with per-site inventory files.
- Cross-site mesh networking for seamless roaming.

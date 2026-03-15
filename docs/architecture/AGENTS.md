# Architecture Documentation Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the docs/architecture/ directory. These rules focus on system architecture, role dependencies, and documentation patterns.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., @.cursor/rules/project-structure.mdc), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Architecture Patterns:**
- @.agents/skills/project-structure-rules - Project architecture principles
- @.agents/skills/vm-lifecycle-architecture - VM lifecycle and service model
- @.agents/skills/vm-provisioning-patterns - Provisioning architecture patterns
- @.agents/skills/vm-cleanup-maintenance - Cleanup architecture and maintenance

**Service Integration:**
- @.agents/skills/openwrt-feature-integration - Feature integration patterns
- @.agents/skills/service-config-validation - Service configuration patterns
- @.agents/skills/systemd-lxc-compatibility - Service compatibility patterns

**Image Management:**
- @.agents/skills/image-management-patterns - Image management strategies
- @.agents/skills/openwrt-image-builder - OpenWrt image building patterns

## Development Guidelines

For project structure and architecture patterns: @.cursor/rules/project-structure.mdc

## Cross-Coverage Rules

### From Other Directories
- **VM lifecycle**: Reference @.agents/skills/vm-lifecycle-architecture for deployment patterns
- **Role patterns**: Use @.agents/skills/vm-provisioning-patterns for provisioning architecture
- **Image patterns**: Apply @.agents/skills/image-management-patterns for build vs configure decisions
- **Cleanup completeness**: Reference @.agents/skills/vm-cleanup-maintenance for cleanup architecture
- **Service validation**: Use @.agents/skills/service-config-validation for configuration patterns

## What This Project Is

An Ansible project that automates VM and LXC container provisioning on Proxmox VE. Currently deploys an OpenWrt router VM with shared LXC infrastructure ready for service containers.

## Core Design Principles

These apply to EVERY service, VM, and container in this project — not just OpenWrt. They govern how we build images, write roles, and plan features.

### Bake, Don't Configure at Runtime

Every VM and container starts from a purpose-built image with its packages and base configuration already baked in. The image arrives ready to run.

**NEVER** install packages (`opkg install`, `apt install`, `pip install`) during converge/configure. Runtime installation is fragile (network down, repo 404, version drift, DNS failure) and slow.

- When a new package is needed, add it to the service's image build and rebuild
- Configure roles contain ZERO `opkg install`, `apt-get install`, or equivalent commands
- This applies equally to firmware images (OpenWrt), LXC rootfs tarballs, VM disk images, and any other deployable artifact

### One Path, No Fallbacks

Every deployment scenario has exactly one tested code path. **NEVER** add "try X, fall back to Y" logic. 

If a prerequisite is missing (image not built, hardware not present, config not set), **FAIL** with a clear message telling the operator exactly how to fix it.

### Follow Community Standards

Before writing custom automation, check if the upstream project already has an official tool or recommended approach for exactly this use case. Use it.

## Architecture Pattern: Two-Role Per Service

Every service type has exactly two roles:
- `<type>_vm` + `<type>_configure` — for VMs (KVM/QEMU): provision via qm, configure via SSH
- `<type>_lxc` + `<type>_configure` — for containers: provision via `include_role: proxmox_lxc`, configure via `community.proxmox.proxmox_pct_remote`
- `<type>_mesh_lxc` + `<type>_mesh_configure` — for LXC containers with host device namespace move (e.g., WiFi PHY)

### Shared Infrastructure Roles

Run once per host before any service roles:
- `proxmox_backup` — tar host config + vzdump existing VMs
- `proxmox_bridges` — discover NICs, create virtual bridges, export `proxmox_all_bridges` fact
- `proxmox_pci_passthrough` — IOMMU/vfio-pci for WiFi, export `wifi_pci_devices` fact (requires VT-d in BIOS)
- `proxmox_igpu` — detect iGPU (Intel or AMD), verify VA-API, export `igpu_*` facts (hard-fails if no iGPU)
- `proxmox_lxc` — shared LXC provisioning helper (consumed via `include_role`, not standalone)

## Playbook Execution Order (site.yml) — Phased

### Phase 1: Primary Hosts (proxmox:!lan_hosts — directly reachable)
- Play 0: `proxmox_backup` — tag: `backup`, deploy_stamp: `backup`
- Play 1: `proxmox_bridges`, `pci_passthrough`, `igpu` — tag: `infra`, deploy_stamp: `infrastructure`
- Play 2: `openwrt_vm` (router_nodes) — tag: `openwrt`, deploy_stamp: `openwrt_vm`
- Play 3: `openwrt_configure` (openwrt) — tag: `openwrt`

### Phase 2: LAN Satellites (reachable after OpenWrt creates the LAN)
- Play 4: Bootstrap LAN hosts (router_nodes) — tag: `lan-satellite`
- Play 5: `proxmox_backup` (lan_hosts) — tag: `lan-satellite`, `backup`, deploy_stamp: `backup`
- Play 6: `proxmox_bridges`, `pci_passthrough`, `igpu` (lan_hosts) — tag: `lan-satellite`, `infra`, deploy_stamp: `infrastructure`

### Phase 3: Services (flavor groups span both primary + LAN hosts)
- Play 7: `pihole_lxc` (dns_nodes) — tag: `pihole`, deploy_stamp: `pihole_lxc`
- Play 8: `pihole_configure` (pihole) — tag: `pihole`
- Play 9: `rsyslog_lxc` (monitoring_nodes) — tag: `monitoring`, deploy_stamp: `rsyslog_lxc`
- Play 10: `rsyslog_configure` (rsyslog) — tag: `monitoring`
- Play 11: `netdata_lxc` (monitoring_nodes) — tag: `monitoring`, deploy_stamp: `netdata_lxc`
- Play 12: `netdata_configure` (netdata) — tag: `monitoring`
- Play 13: `wireguard_lxc` (vpn_nodes) — tag: `wireguard`, deploy_stamp: `wireguard_lxc`
- Play 14: `wireguard_configure` (wireguard) — tag: `wireguard`
- Play 15: `openwrt_mesh_lxc` (wifi_nodes:!router_nodes) — tag: `mesh-wifi`, deploy_stamp: `openwrt_mesh_lxc`
- Play 16: `openwrt_mesh_configure` (openwrt_mesh) — tag: `mesh-wifi`

## Network Topology

```
ISP Router (192.168.86.x supernet)
  |
Switch
  |            |                  |
Home          AI Node          Mesh2
(primary)     192.168.86.220   192.168.86.211
  |
  |-- OpenWrt VM (10.10.10.1)
  |     |
  |     LAN bridge (10.10.10.x)
  |       |
  |     Mesh1 (10.10.10.210)
```

**Network Rules:**
- **home**, **ai**, **mesh2**: directly reachable on the supernet (no ProxyJump)
- **mesh1**: behind home's OpenWrt, reachable via ProxyJump through home

## Device Flavors (Inventory Groups)

Hosts belong to child groups under `proxmox` that determine which services they receive.

- `router_nodes` — OpenWrt router VM (home only)
- `vpn_nodes` — WireGuard VPN (home, mesh1, ai, mesh2 — all 4 nodes)
- `dns_nodes` — Pi-hole
- `wifi_nodes` — Mesh WiFi Controller (router_nodes on primary); OpenWrt Mesh LXC on wifi_nodes:!router_nodes (mesh1, mesh2)
- `monitoring_nodes` — Netdata, rsyslog
- `service_nodes` — Home Assistant
- `media_nodes` — Jellyfin, Kodi, Moonlight
- `desktop_nodes` — Desktop VM, UX Kiosk
- `gaming_nodes` — Gaming VM (separate physical machine)
- `lan_hosts` — Satellite Proxmox nodes behind the OpenWrt router (accessed via ProxyJump; mesh1 only)

A host can belong to multiple flavor groups.

## VMID Allocation

- **100-199**: Network (100 OpenWrt, 101 WireGuard, 102 Pi-hole, 103 Mesh WiFi)
- **200-299**: Services (200 Home Assistant)
- **300-399**: Media (300 Jellyfin, 301 Kodi, 302 Moonlight)
- **400-499**: Desktop (400 Desktop VM, 401 Kiosk)
- **500-599**: Observability (500 Netdata, 501 rsyslog)
- **600-699**: Gaming (600 Gaming VM)
- **999**: reserved for molecule test containers

All VMIDs defined in `group_vars/all.yml`.

## Key Files Reference

| File | Purpose |
|------|---------|
| `build.py` | Single entry point: env validation, host probing, playbook runner |
| `setup.sh` | Bootstrap .venv + pip + ansible-galaxy |
| `run.sh` | Convenience wrapper — delegates to `build.py` |
| `cleanup.sh` | Restore / full-restore / clean / rollback — delegates to `build.py` |
| `build-images.sh` | Builds custom images (mesh LXC, router VM, Pi-hole, rsyslog, Netdata, WireGuard) |
| `test.env` | Test machine config (committed, IP: 192.168.86.201) |
| `.env` | Production secrets (gitignored) |
| `test.env.generated` | Auto-generated secrets during test runs (gitignored) |
| `.env.generated` | Auto-generated secrets during production runs (gitignored) |
| `.state/addresses.json` | Cached host IPs for cross-run discovery (gitignored) |

**Architecture Implementation Patterns:**
- Reference @.agents/skills/build-entry-point for entry point architecture
- Use @.agents/skills/vm-provisioning-patterns for VM lifecycle patterns
- Apply @.agents/skills/image-management-patterns for build automation patterns

## Architecture Documentation Standards

- Use descriptive bridge names ("WAN bridge", "LAN bridge") instead of hardcoded names (vmbr0, vmbr1)
- Document role exports clearly - what facts do they export?
- Keep diagrams current with actual implementation
- Include network topology assumptions in documentation
- Document VM lifecycle patterns and dependencies

This directory contains system architecture documentation that must stay synchronized with the actual implementation.
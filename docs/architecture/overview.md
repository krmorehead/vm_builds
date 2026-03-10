# Project Overview

## Purpose

**vm_builds** is an Ansible project that automates the provisioning and configuration of virtual machines on Proxmox VE. The primary target is an OpenWrt router VM that replaces a physical consumer router, giving full software-defined control over the home network. The project is designed to expand to additional VM types (Home Assistant, Pi-hole, NAS, etc.) using consistent patterns.

The project is designed around a key principle: **a single command should take a bare Proxmox host and produce fully functional, production-ready VMs** -- no manual Proxmox UI interaction, no SSH-and-paste workflows, no guesswork.

## Design Philosophy

- **Idempotent and repeatable.** Every run should converge to the same state regardless of starting conditions. A cleanup + re-run cycle should always produce a working result.
- **Hardware-agnostic.** The playbook discovers physical NICs, WiFi cards, and PCI topology at runtime. It does not hardcode interface names, bridge numbers, or PCI addresses. It should work on any small-form-factor PC with 2+ ethernet ports.
- **Environment-driven secrets.** No credentials live in the repository. API tokens and passphrases are injected via `.env` files, making it safe to commit everything else.
- **No third-party monkeypatching.** OpenWrt has no Python runtime, so all configuration is done via `ansible.builtin.raw` with UCI commands. This avoids fragile compatibility shims that break across Ansible versions.
- **Backup before change.** Every playbook run begins by backing up the host configuration (tar) and existing VMs (`vzdump`). Three restore modes are available: config-only, full rollback (VMs + config), and clean reset (destroy all + restore config). This works in both production and test scenarios.
- **Test-friendly.** A dedicated test Proxmox node can be provisioned and torn down programmatically using `./cleanup.sh clean`, which destroys all VMs and restores the host to the state captured before the last playbook run.
- **Extensible.** The two-role-per-VM pattern (`<type>_vm` + `<type>_configure`) and shared infrastructure roles ensure new VM types integrate cleanly without architectural drift.

## High-Level Architecture

```
 Linux Mint (Control Node)
 ┌─────────────────────────┐
 │  .venv/                 │
 │  ansible-playbook       │
 │  proxmoxer (API client) │
 └──────┬──────────────────┘
        │
        │  Proxmox API (token auth)
        │  SSH (key auth)
        │
 ┌──────▼──────────────────────────────────────────────┐
 │  Proxmox VE Host                                    │
 │                                                     │
 │  Physical NICs ──► Virtual Bridges (vmbr0..N)       │
 │  WiFi PCIe     ──► vfio-pci ──► hostpci passthrough │
 │                                                     │
 │  ┌────────────────────────────────┐                 │
 │  │  OpenWrt VM (VMID 100)        │                 │
 │  │                               │                 │
 │  │  eth0 (WAN) ◄── vmbr0        │                 │
 │  │  eth1 (LAN) ◄── vmbr1        │                 │
 │  │  eth2 (LAN) ◄── vmbr2        │                 │
 │  │  wlan0      ◄── PCIe pass    │                 │
 │  │         └── 802.11s mesh      │                 │
 │  └────────────────────────────────┘                 │
 │                                                     │
 │  ┌────────────────────────────────┐                 │
 │  │  Future VM (VMID 200)         │  ◄── future    │
 │  │  eth0 ◄── vmbr1 (LAN bridge)  │                 │
 │  └────────────────────────────────┘                 │
 └─────────────────────────────────────────────────────┘
```

## Execution Flow

The playbook (`playbooks/site.yml`) runs plays in sequence:

### Play 0: Backup (targets Proxmox host)

0. **State backup** (`proxmox_backup`) -- Tars up host config directories (`/etc/network/`, `/etc/modprobe.d/`, `/etc/pve/`, etc.) and runs `vzdump` on every existing VM. Writes a manifest to `/var/lib/ansible-backup/` so the cleanup playbook knows what to restore.

### Play 1: Provision (targets Proxmox host)

1. **Bridge creation** (`proxmox_bridges`) -- Discovers every physical NIC, checks which already have bridges, and creates new `vmbr` interfaces for any unbridged NICs. Ensures at least 2 bridges exist (WAN + LAN minimum). Exports `proxmox_all_bridges` fact.
2. **PCI passthrough** (`proxmox_pci_passthrough`) -- Detects WiFi PCIe devices, enables IOMMU if needed (with optional reboot), validates IOMMU group isolation, blacklists host WiFi drivers, and binds devices to `vfio-pci`. Exports `wifi_pci_devices` fact.
3. **VM creation** (`openwrt_vm`) -- Uploads the OpenWrt disk image, creates the VM shell via Proxmox API, imports the disk, attaches virtual NICs to bridges, passes through WiFi PCIe devices, boots the VM, and establishes a temporary bootstrap SSH connection through the Proxmox host.

*Future VM provision roles (`homeassistant_vm`, etc.) insert here.*

### Play 2+: Configure (targets VM dynamic groups)

4. **OpenWrt configuration** (`openwrt_configure`) -- Uses a two-phase restart pattern. Phase 1 configures WAN (eth0) and LAN bridge ports, restarts networking while keeping LAN at the factory-default IP, then migrates the bootstrap IP from the WAN bridge to the LAN bridge. Phase 2 installs WiFi driver packages (switching opkg feeds to HTTP for BusyBox compatibility), loads kernel modules, configures 802.11s mesh on detected radios, applies the auto-selected collision-free LAN IP and DHCP settings, and performs a final network restart.

*Future VM configure plays (`homeassistant_configure`, etc.) follow as separate plays.*

### Final Play: Cleanup (targets Proxmox host)

5. **Bootstrap cleanup** -- Removes the temporary IP address that was added to a LAN bridge for initial SSH access.

## Multi-VM Expansion

### Two-role pattern

Every VM type consists of:
- `<type>_vm` — provisions the VM on Proxmox (image upload, API create, disk import, NIC attach, start, `add_host`)
- `<type>_configure` — configures the running VM (packages, services, settings)

These are always separate roles in separate plays. The provision role targets `proxmox` hosts. The configure role targets the dynamic group created by `add_host`.

### Shared infrastructure

These roles run **once per host**, regardless of how many VMs exist:
- `proxmox_backup` — host config + VM backups
- `proxmox_bridges` — NIC discovery, bridge creation → exports `proxmox_all_bridges`
- `proxmox_pci_passthrough` — IOMMU/vfio-pci → exports `wifi_pci_devices`

### VMID allocation

| Range | Purpose | Current |
|-------|---------|---------|
| 100-199 | Network VMs | 100 = OpenWrt |
| 200-299 | Service VMs | *(reserved)* |

All VMIDs are defined in `inventory/group_vars/all.yml`.

### Variable isolation

- Role defaults are prefixed with the VM type: `openwrt_vm_id`, `homeassistant_vm_id`.
- Shared params (storage pool, etc.) live in `group_vars/all.yml`.
- Cross-role data passes through `set_fact` or `add_host`, never direct default references.

### Bridge allocation

OpenWrt is the router — it consumes ALL bridges. Service VMs behind the router need only one bridge (the LAN bridge, typically `proxmox_all_bridges[1]`).

## Project Structure

```
vm_builds/
├── ansible.cfg              # Ansible configuration
├── requirements.yml         # Galaxy collections
├── setup.sh                 # One-time environment bootstrap
├── run.sh                   # Run playbook with .env
├── cleanup.sh               # Restore / full-restore / clean (tar + vzdump)
├── test.env                 # Test environment variables (committed)
├── .env                     # Production secrets (gitignored)
│
├── inventory/
│   ├── hosts.yml            # Host inventory + empty dynamic groups
│   ├── group_vars/
│   │   ├── all.yml          # VM parameters (IDs, image paths, storage)
│   │   └── proxmox.yml      # API auth, SSH settings
│   └── host_vars/
│       └── home.yml         # Per-host overrides (IP, reboot policy)
│
├── playbooks/
│   ├── site.yml             # Main orchestration playbook
│   └── cleanup.yml          # Tag-driven restore playbook
│
├── roles/
│   ├── proxmox_backup/      # Shared: host config + VM backup (tar + vzdump)
│   ├── proxmox_bridges/     # Shared: NIC discovery and bridge creation
│   ├── proxmox_pci_passthrough/  # Shared: WiFi IOMMU/vfio setup
│   ├── openwrt_vm/          # OpenWrt: VM lifecycle management
│   └── openwrt_configure/   # OpenWrt: UCI configuration
│
├── molecule/
│   └── default/             # Integration test scenario
│
├── images/                  # VM disk images (gitignored)
│
├── docs/
│   └── architecture/        # Design documentation
│
└── .cursor/
    ├── rules/               # Always-on coding conventions (for LLM sessions)
    └── skills/              # On-demand knowledge (for LLM sessions)
```

# Project Overview

## Purpose

**vm_builds** is an Ansible project that automates the provisioning and configuration of virtual machines on Proxmox VE. The primary target is an OpenWrt router VM that replaces a physical consumer router, giving full software-defined control over the home network.

The project is designed around a key principle: **a single command should take a bare Proxmox host and produce a fully functional, production-ready router VM** -- no manual Proxmox UI interaction, no SSH-and-paste workflows, no guesswork.

## Design Philosophy

- **Idempotent and repeatable.** Every run should converge to the same state regardless of starting conditions. A cleanup + re-run cycle should always produce a working result.
- **Hardware-agnostic.** The playbook discovers physical NICs, WiFi cards, and PCI topology at runtime. It does not hardcode interface names, bridge numbers, or PCI addresses. It should work on any small-form-factor PC with 2+ ethernet ports.
- **Environment-driven secrets.** No credentials live in the repository. API tokens and passphrases are injected via `.env` files, making it safe to commit everything else.
- **No third-party monkeypatching.** OpenWrt has no Python runtime, so all configuration is done via `ansible.builtin.raw` with UCI commands. This avoids fragile compatibility shims that break across Ansible versions.
- **Backup before change.** Every playbook run begins by backing up the host configuration (tar) and existing VMs (`vzdump`). Three restore modes are available: config-only, full rollback (VMs + config), and clean reset (destroy all + restore config). This works in both production and test scenarios.
- **Test-friendly.** A dedicated test Proxmox node can be provisioned and torn down programmatically using `./cleanup.sh clean`, which destroys all VMs and restores the host to the state captured before the last playbook run.

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
 └─────────────────────────────────────────────────────┘
```

## Execution Flow

The playbook (`playbooks/site.yml`) runs four plays in sequence:

### Play 0: Backup (targets Proxmox host)

0. **State backup** (`proxmox_backup`) -- Tars up host config directories (`/etc/network/`, `/etc/modprobe.d/`, `/etc/pve/`, etc.) and runs `vzdump` on every existing VM. Writes a manifest to `/var/lib/ansible-backup/` so the cleanup playbook knows what to restore.

### Play 1: Provision (targets Proxmox host)

1. **Bridge creation** (`proxmox_bridges`) -- Discovers every physical NIC, checks which already have bridges, and creates new `vmbr` interfaces for any unbridged NICs. Ensures at least 2 bridges exist (WAN + LAN minimum).
2. **PCI passthrough** (`proxmox_pci_passthrough`) -- Detects WiFi PCIe devices, enables IOMMU if needed (with optional reboot), validates IOMMU group isolation, blacklists host WiFi drivers, and binds devices to `vfio-pci`.
3. **VM creation** (`openwrt_vm`) -- Uploads the OpenWrt disk image, creates the VM shell via Proxmox API, imports the disk, attaches virtual NICs to bridges, passes through WiFi PCIe devices, optionally clones the upstream router's MAC address onto the WAN interface, boots the VM, and establishes a temporary bootstrap SSH connection through the Proxmox host.

### Play 2: Configure (targets OpenWrt VM)

4. **OpenWrt configuration** (`openwrt_configure`) -- Waits for WAN DHCP, detects the WAN interface, auto-selects a LAN subnet that avoids collisions with the WAN network, assigns LAN ports to a bridge, configures DHCP, sets up firewall zones, installs mesh WiFi packages, and configures 802.11s mesh on all detected radios.

### Play 3: Cleanup (targets Proxmox host)

5. **Bootstrap cleanup** -- Removes the temporary IP address that was added to a LAN bridge for initial SSH access.

## Project Structure

```
vm_builds/
├── ansible.cfg              # Ansible configuration
├── requirements.yml         # Galaxy collections
├── setup.sh                 # One-time environment bootstrap
├── run.sh                   # Run playbook with .env
├── cleanup.sh               # LVM snapshot save/restore for test resets
├── test.env                 # Test environment variables (committed)
├── .env                     # Production secrets (gitignored)
│
├── inventory/
│   ├── hosts.yml            # Host inventory
│   ├── group_vars/
│   │   ├── all.yml          # VM parameters (ID, memory, image path)
│   │   └── proxmox.yml      # API auth, SSH settings
│   └── host_vars/
│       └── home.yml         # Per-host overrides (IP, reboot policy)
│
├── playbooks/
│   └── site.yml             # Main orchestration playbook
│
├── roles/
│   ├── proxmox_bridges/     # NIC discovery and bridge creation
│   ├── proxmox_pci_passthrough/  # WiFi IOMMU/vfio setup
│   ├── openwrt_vm/          # VM lifecycle management
│   └── openwrt_configure/   # OpenWrt UCI configuration
│
├── molecule/
│   └── default/             # Integration test scenario
│
├── images/
│   └── openwrt.img          # OpenWrt disk image (gitignored)
│
└── docs/
    └── architecture/        # This documentation
```

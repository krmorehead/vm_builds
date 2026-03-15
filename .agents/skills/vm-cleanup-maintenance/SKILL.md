---
name: vm-cleanup-maintenance
description: VM cleanup completeness, performance optimization, and maintenance patterns. Use when managing cleanup operations, optimizing role performance, or handling system maintenance tasks.
---

# VM Cleanup & Maintenance Rules

## Cleanup Completeness Requirement

1. When a role deploys a file to the Proxmox host or the controller, ALWAYS add it to both cleanup playbooks (`molecule/default/cleanup.yml` and `playbooks/cleanup.yml`).

2. Current ansible-managed files that must be cleaned:
   - `/etc/network/interfaces.d/ansible-bridges.conf` (may be modified in-place to `inet dhcp`)
   - `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (legacy, removed by converge if present)
   - `/etc/network/interfaces.d/ansible-temp-lan.conf` (test workaround, cleaned up)
   - `/etc/modprobe.d/blacklist-wifi.conf`
   - `/etc/modprobe.d/vfio-pci.conf`
   - `/etc/ansible/facts.d/vm_builds.fact`
   - `/tmp/openwrt.img` (edge case: left behind if build fails mid-upload)
   - `/var/lib/vz/template/cache/*.tar.zst` (LXC templates)
   - `.state/addresses.json` (controller, via `delegate_to: localhost`)

## VM Destruction Requirements

3. Cleanup MUST destroy both project VMs and containers using **explicit VMIDs** from `group_vars/all.yml`. NEVER use blanket `qm list` / `pct list` iteration — it destroys non-project resources on shared hosts.

4. Check existence with `qm status` / `pct status` before attempting stop + destroy.

5. Current project VMIDs: OpenWrt VM (100), WireGuard (101), Pi-hole (102), Mesh WiFi (103), Netdata (500), rsyslog (501).

## Hardware Detection: Hard-Fail by Default

6. NEVER add "graceful skip" for hardware expected on every host. Roles MUST hard-fail when required hardware is missing. Silent skips mask fixable problems behind warnings that are easy to miss.

7. | Hardware   | Expectation     | Detection role               |
   |------------|----------------|------------------------------|
   | iGPU       | REQUIRED       | `proxmox_igpu` (hard-fail)   |
   | WiFi + VT-d| REQUIRED       | `proxmox_pci_passthrough` (hard-fail) |
   | NIC count  | Dynamic OK     | `proxmox_bridges` (2+ only for `router_nodes`) |

8. Previous bug: `proxmox_pci_passthrough` silently skipped passthrough when IOMMU groups were invalid. Root cause was VT-d disabled in BIOS — a 30-second fix masked for an entire test cycle.

## Configure Role Performance (pct_remote overhead)

9. Each task in an LXC configure role opens a new paramiko SSH connection to the Proxmox host, then spawns `pct exec` inside the container. This overhead (15-60 seconds per task) makes LXC configure roles significantly slower than SSH-based configure roles.

10. **MINIMIZE** the number of tasks in LXC configure roles. Every task that can be baked into the image MUST be.

11. Base system config (systemd overrides, default configs, package configs) is ALWAYS the same across all containers → belongs in the image.

12. Host-specific config (IPs, streaming endpoints, peer keys, passwords) varies per container → belongs in the configure role.

13. When in doubt, ask: "Does this value change between containers?" If no, bake it into the image.

## Image vs Configure Role Separation

14. **What belongs in the image (`build-images.sh`):**
    - Package installation (the bake principle)
    - systemd overrides for LXC compatibility
    - Base service config files (dbengine retention, logging paths, proc/sys paths)
    - Logrotate configs
    - Static config that every container shares

15. **What belongs in the configure role:**
    - Streaming/replication endpoints that depend on host topology
    - Passwords and API keys from env vars
    - DNS upstream servers based on container location (LAN vs WAN)
    - Optional features gated on env vars

## Performance Optimization Example

16. Previous optimization: Netdata `configure` role had 6 tasks (mkdir, copy override, daemon_reload, detect config dir, set streaming, health check). Moving 3 tasks (systemd override) to the image build reduced per-feature test time from 110s to 68s (38% speedup) and full integration from 23.5m to 16.6m (29% speedup).

## Host-Level Apt Prerequisites

17. Roles that install packages on the Proxmox HOST must handle three prerequisites:
    - **Clock sync**: Sync via NTP before `apt-get update`
    - **DNS**: After cleanup destroys the router VM, check DNS with `getent hosts deb.debian.org` and fall back to `8.8.8.8` / `1.1.1.1`
    - **Enterprise repos**: `pve-enterprise.sources` and `ceph.sources` require a subscription. Rename both to `.disabled` and add the `pve-no-subscription` repo. ALWAYS restore them in cleanup.
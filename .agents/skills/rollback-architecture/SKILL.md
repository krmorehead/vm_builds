---
name: rollback-architecture
description: Rollback architecture and layered rollback model patterns. Use when designing rollback strategies, understanding rollback layers, or planning reversible feature deployments.
---

# Rollback Architecture Rules

## Core Rollback Principle

1. Every feature MUST define a rollback procedure. If you can't describe how to undo it, you can't ship it.

2. Without layered rollback, a broken VLAN config requires destroying the router VM and rebuilding from scratch — a 5-minute operation that should take 30 seconds.

## Rollback Layers Model

3. The project uses a layered rollback model:
   ```
   Rollback Model
   ├── Per-feature rollback (targeted, fast, < 30s)
   │   ├── Reverts: UCI config, installed packages, firewall rules, cron jobs, auth state
   │   ├── Preserves: VM, base OS, all other features
   │   ├── Trigger: cleanup.sh rollback <feature-name> [env-file]
   │   │            → build.py --playbook cleanup --tags openwrt-<feature>-rollback
   │   ├── Prerequisite: dynamic group reconstruction play runs first
   │   └── Example: remove banIP, revert SSH config, clear root password, restart dropbear
   │
   ├── Per-VM rollback (existing: full-restore or clean)
   │   ├── Reverts: VM to pre-change vzdump snapshot or destroys it
   │   ├── Preserves: host config, other VMs
   │   └── Trigger: cleanup.sh full-restore|clean [env-file]
   │
   └── Full host restore (existing: restore)
       ├── Reverts: host config files, bridges, PCI bindings
       ├── Preserves: nothing (full reset)
       └── Trigger: cleanup.sh restore [env-file]
   ```

## Rollback Strategy Priority

4. Per-feature rollback is the first line of defense. Full-host restore (existing `restore`, `full-restore`, `clean` tags) is the escape hatch when per-feature rollback fails.

5. `cleanup.yml` supports both full modes AND per-feature rollback tags. They are additive — a full restore still works even after per-feature tags are added.

## Baseline Concept

6. The **baseline** is the state after the router is provisioned and configured by `site.yml` (plays 0-4). All per-feature work builds on top of this.

   ```
   Baseline State (after site.yml converge)
   ├── OpenWrt VM running (VMID 100)
   ├── WAN interface has DHCP IP from upstream
   ├── LAN subnet configured (collision-free)
   ├── DHCP serving on LAN
   ├── Firewall zones: WAN reject, LAN accept
   ├── Proxmox LAN management IP on LAN bridge (DHCP)
   ├── deploy_stamp: backup, infrastructure, openwrt_vm plays recorded
   ├── .state/addresses.json written
   └── Backup manifest with host config + VM vzdump
   ```

7. Features add to the baseline. Per-feature rollback removes just the feature, returning to baseline. Full `molecule/default` test rebuilds from scratch; per-feature scenarios start from the baseline.

## Rollback Implementation Rules

8. Per-feature rollback uses Ansible tags in `cleanup.yml`. Convention: `--tags <feature>-rollback` runs the inverse of `--tags <feature>`.

9. NEVER implement rollback by re-converging from scratch. Rollback undoes only the specific changes, leaving everything else intact.

10. Rollback procedures MUST be tested. Each per-feature molecule scenario includes a rollback verification step in its test sequence.
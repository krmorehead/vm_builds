---
name: rollback-patterns
description: Migration and rollback conventions for vm_builds. Use when adding per-feature rollback, designing deploy_stamp tracking, implementing UCI config revert, planning version-aware convergence, extending cleanup.yml with rollback tags, or designing incremental playbook runs.
---

# Rollback Patterns

## Context

Every feature change must be reversible without tearing down the entire stack.
The project uses a layered rollback model: per-feature rollback for config
changes, per-VM rollback for provisioning, and full-host restore for
infrastructure changes. Without this, a broken VLAN config requires destroying
the router VM and rebuilding from scratch — a 5-minute operation that should
take 30 seconds.

## Rules

1. Every feature MUST define a rollback procedure. If you can't describe how to undo it, you can't ship it.
2. Per-feature rollback uses Ansible tags in `cleanup.yml`. Convention: `--tags <feature>-rollback` runs the inverse of `--tags <feature>`.
3. `deploy_stamp` records what ran. Rollback tasks MUST check `ansible_local.vm_builds.plays` to determine what needs undoing. NEVER attempt to roll back a feature that was never applied.
4. UCI changes on OpenWrt are atomic per `uci commit`. ALWAYS group related UCI changes into a single commit. Rollback reverses them with `uci delete` or `uci set` to defaults, then `uci commit`.
5. Package installation rollback uses `opkg remove`. ALWAYS list installed packages in the feature's rollback procedure.
6. NEVER implement rollback by re-converging from scratch. Rollback undoes only the specific changes, leaving everything else intact.
7. Rollback procedures MUST be tested. Each per-feature molecule scenario includes a rollback verification step in its test sequence.
8. Full-host restore (existing `restore`, `full-restore`, `clean` tags) is the escape hatch when per-feature rollback fails. Per-feature rollback is the first line of defense.
9. `cleanup.yml` supports both full modes AND per-feature rollback tags. They are additive — a full restore still works even after per-feature tags are added.
10. Rollback plays targeting dynamic groups (e.g., `openwrt`) MUST be preceded by a group reconstruction play in `cleanup.yml`. The dynamic group is ephemeral — `add_host` state does not persist across `ansible-playbook` invocations. Without reconstruction, rollback plays have no hosts to target.
11. Rollback MUST fully restore the baseline auth/connection state. If a feature deploys SSH keys and disables password auth, rollback MUST re-enable password auth AND clear the root password (restoring empty-password baseline). Partial auth rollback leaves the system in an undefined state — neither key nor password works.
12. The group reconstruction play MUST detect the current auth method (key vs. password) by checking for `OPENWRT_SSH_PRIVATE_KEY` env var and `deploy_stamp` state. After a security hardening rollback, the auth method reverts to password — reconstruction must handle both.

## Rollback layers

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

## Per-feature rollback pattern (OpenWrt UCI)

Each feature in `cleanup.yml` gets a tagged block that reverses
its changes. The block targets the `openwrt` group (or `proxmox`
if the changes are host-side).

```yaml
# In playbooks/cleanup.yml
- name: Rollback security hardening
  hosts: openwrt
  tags: [openwrt-security-rollback, never]
  gather_facts: false
  tasks:
    - name: Remove banIP and revert SSH config
      ansible.builtin.raw: |
        opkg remove banip 2>/dev/null
        uci delete dropbear.@dropbear[0].PasswordAuth 2>/dev/null
        uci delete dropbear.@dropbear[0].RootPasswordAuth 2>/dev/null
        uci commit dropbear
        /etc/init.d/dropbear restart
```

The `never` tag prevents rollback from running during full cleanup.
It only runs when explicitly requested: `--tags openwrt-security-rollback`.

## Dynamic group reconstruction for rollback

Rollback plays in `cleanup.yml` target dynamic groups (`openwrt`, `pihole`,
etc.) that are populated by `add_host` during converge. But `cleanup.yml`
runs as a separate `ansible-playbook` invocation — the groups are empty.

ALWAYS add a reconstruction play at the top of `cleanup.yml`, tagged with
ALL rollback tags so it runs whenever any rollback is invoked:

```yaml
# In playbooks/cleanup.yml — BEFORE any rollback plays
- name: Reconstruct openwrt dynamic group
  hosts: router_nodes
  tags: [openwrt-security-rollback, openwrt-vlans-rollback, openwrt-dns-rollback, never]
  gather_facts: true
  tasks:
    - name: Include group reconstruction
      ansible.builtin.include_tasks: tasks/reconstruct_openwrt_group.yml
```

The reconstruction task file:
1. Verifies VM 100 is running via `qm status`
2. Detects the OpenWrt LAN IP from Proxmox bridge state
3. Detects the current SSH auth method (key vs. password) by checking
   `OPENWRT_SSH_PRIVATE_KEY` and `ansible_local.vm_builds.plays`
4. Registers the host via `add_host` with the correct SSH arguments

Extract this logic into a reusable task file (`tasks/reconstruct_openwrt_group.yml`)
consumed by converge, verify, and cleanup entry points.

## cleanup.sh rollback subcommand

`cleanup.sh` supports a `rollback` subcommand that delegates to `build.py`:

```bash
./cleanup.sh rollback security test.env
# → build.py --playbook cleanup --tags openwrt-security-rollback
```

The subcommand maps the feature name to the rollback tag using the convention
`openwrt-<feature>-rollback`. This is implemented in `build.py` with pytest
coverage for the argument mapping.

## deploy_stamp for migration decisions

`deploy_stamp` writes `/etc/ansible/facts.d/vm_builds.fact` with:
- `project_version` from `group_vars/all.yml`
- `last_run` UTC timestamp
- `plays` map: play name → `{version, timestamp}`

On subsequent runs, read `ansible_local.vm_builds.plays` to decide
what's already applied:

```yaml
- name: Check if security hardening already applied
  ansible.builtin.set_fact:
    security_already_applied: >-
      {{ (ansible_local.vm_builds.plays.openwrt_security is defined)
         | default(false) }}
  when: ansible_local.vm_builds is defined
```

Use this for idempotent feature application — skip expensive operations
when the feature is already at the current version.

## Baseline concept

The **baseline** is the state after the router is provisioned and configured
by `site.yml` (plays 0-4). All per-feature work builds on top of this.

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

Features add to the baseline. Per-feature rollback removes just the feature,
returning to baseline. Full `molecule/default` test rebuilds from scratch;
per-feature scenarios start from the baseline.

## Version-aware convergence

When `project_version` in `group_vars/all.yml` advances:

1. `deploy_stamp` compares the new version against `ansible_local.vm_builds.project_version`
2. Feature plays check their own play entry version against current
3. If the versions match, skip expensive operations (package installs, downloads)
4. If the versions differ, re-apply the feature (idempotent convergence)

This is the migration path: bump `project_version`, update the feature
play, run converge. The play detects the version mismatch and re-applies.
No separate migration playbook needed.

## Tag naming conventions

```
Feature Tags
├── Apply: openwrt-security, openwrt-vlans, openwrt-dns, openwrt-mesh
├── Rollback: openwrt-security-rollback, openwrt-vlans-rollback, ...
└── Future LXC: pihole, pihole-rollback, wireguard, wireguard-rollback, ...
```

Apply tags go in `site.yml` plays. Rollback tags go in `cleanup.yml` plays.
Both use the `never` meta-tag so they don't run unless explicitly requested.

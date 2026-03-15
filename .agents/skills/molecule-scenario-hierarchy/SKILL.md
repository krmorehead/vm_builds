---
name: molecule-scenario-hierarchy
description: Molecule scenario hierarchy, baseline testing model, and layered vs standalone scenarios. Use when setting up molecule scenarios, managing baseline dependencies, or understanding test architecture.
---

# Molecule Scenario Hierarchy

## Baseline Testing Model

The **baseline** is the state after `molecule/default` converges successfully: router VM running, WAN/LAN configured, DHCP serving, firewall active, all 4 nodes reachable. All per-feature molecule scenarios start from this baseline and only converge/revert their own changes.

**CRITICAL: The OpenWrt baseline stays up.** Only tear down the specific containers being tested. Full `molecule test` is reserved for final validation only.

## Scenario Structure

```
molecule/default/              Full integration (home, mesh1, ai, mesh2 — 4-node)
├── openwrt-security/          Per-feature (assumes baseline exists)
├── wireguard-lxc/             Standalone role scenario
└── mesh1-infra/               Lightweight infra-only on mesh1
```

Each per-feature scenario assumes the baseline exists and uses tagged plays for rapid iteration.

## Primary Workflow

Per-feature scenarios are the main test loop:
1. `molecule converge` (once) — build the full baseline with all 4 nodes
2. `molecule converge -s <scenario>` + `molecule verify -s <scenario>` — iterate
3. Each scenario tears down only its own containers, verifies, then cleans up
4. Baseline (OpenWrt, bridges, PCI, iGPU) is assumed to exist and left running

**Final validation only:** `molecule test` runs the full clean-state pipeline and destroys everything at the end.

Full `molecule test` takes 4-5 minutes. Per-feature scenarios take 30-60 seconds.

## Two Kinds of Scenarios

**Layered feature scenarios** (e.g., `openwrt-security`, `openwrt-vlans`):
- assume the baseline exists (router VM running)
- converge only their tagged plays
- verify only their assertions
- clean up only their changes

**Standalone role scenarios** (e.g., `proxmox-lxc`, `proxmox-igpu`):
- test a single shared infrastructure role in isolation
- converge the role, verify its output, and clean up any artifacts
- do NOT depend on the baseline — they can run against a bare Proxmox host

Standalone scenarios are the right pattern for shared roles that run on the Proxmox host before any VMs are created. Layered scenarios are for features that build on top of existing VM/container state.

Previous learning: `proxmox-lxc` and `proxmox-igpu` scenarios were developed independently of the default integration test, allowing rapid iteration on iGPU driver/vendor issues without waiting for the full 4-minute default test each time.

## Per-Feature Scenario Setup

Each per-feature scenario needs its own `molecule.yml` that shares the platform config with `default` but uses a different test sequence:

```yaml
# molecule/openwrt-security/molecule.yml
scenario:
  test_sequence:
    - dependency
    - syntax
    - converge
    - verify
    - cleanup
```

No initial cleanup phase — the baseline must already exist. If it doesn't, converge will fail fast with a clear error.

## Per-Feature Converge Pattern

Per-feature converge playbooks run only their tagged plays. They MUST populate the `openwrt` dynamic group first since the baseline's `add_host` state doesn't persist across molecule runs.

```yaml
# molecule/openwrt-security/converge.yml
---
- name: Reconstruct openwrt dynamic group from baseline
  hosts: router_nodes
  gather_facts: true
  tasks:
    - name: Verify VM 100 is running
      ansible.builtin.command:
        cmd: qm status 100
      register: _vm_status
      changed_when: false
      failed_when: "'running' not in _vm_status.stdout"

    - name: Include reusable group reconstruction
      ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

- name: Apply security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Include security hardening tasks
      ansible.builtin.include_role:
        name: openwrt_configure
        tasks_from: security.yml
```

## Standalone Role Scenario Setup

Standalone scenarios test a single role without any baseline dependency. Use these for shared infrastructure roles:

```yaml
# molecule/proxmox-igpu/converge.yml
---
- name: Test proxmox_igpu role
  hosts: proxmox
  gather_facts: true
  roles:
    - proxmox_igpu
```

Key differences from layered scenarios:
1. **No baseline dependency** — no need to populate dynamic groups first
2. **Cleanup restores host state** — enterprise repos, config files, etc.
3. **VMID 999 for throwaway resources** — standalone LXC tests use VMID 999
4. **Include cleanup in `test_sequence`** — standalone scenarios SHOULD include cleanup

## Running Per-Feature Tests

```bash
# Establish baseline (once, or when baseline is stale)
molecule converge

# Iterate on a feature
molecule test -s openwrt-security     # converge + verify + cleanup
molecule converge -s openwrt-security # apply only (for debugging)
molecule verify -s openwrt-security   # check only (after manual fixes)

# Full integration (before commit)
molecule test
```

## Per-Feature Scenario Group Membership

Per-feature scenarios MUST include all groups that affect topology branching in their platform config. If a role uses `router_nodes` or `lan_hosts` group membership to determine LAN vs WAN networking, the per-feature scenario must include those groups even if the scenario doesn't test router functionality.

Previous bug: rsyslog-lxc per-feature scenario was missing `router_nodes` group for the home host. The LXC provisioning role used `router_nodes` membership to choose between LAN and WAN bridges. Without the group, home was treated as a WAN host, and the container was placed on the wrong bridge.
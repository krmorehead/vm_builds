---
name: rollback-group-reconstruction
description: Dynamic group reconstruction for rollback play patterns. Use when setting up rollback plays that target dynamic groups, managing ephemeral groups, or reconstructing host connections.
---

# Dynamic Group Reconstruction for Rollback

## Group Reconstruction Requirement

1. Rollback plays targeting dynamic groups (e.g., `openwrt`) MUST be preceded by a group reconstruction play in `cleanup.yml`. The dynamic group is ephemeral — `add_host` state does not persist across `ansible-playbook` invocations.

2. Without reconstruction, rollback plays have no hosts to target.

## Group Reconstruction Implementation

3. ALWAYS add a reconstruction play at the top of `cleanup.yml`, tagged with ALL rollback tags so it runs whenever any rollback is invoked:

   ```yaml
   # In playbooks/cleanup.yml — BEFORE any rollback plays
   - name: Reconstruct openwrt dynamic group
     hosts: router_nodes
     tags: [openwrt-security-rollback, openwrt-vlans-rollback, openwrt-dns-rollback, openwrt-mesh-rollback, never]
     gather_facts: true
     tasks:
       - name: Include group reconstruction
         ansible.builtin.include_tasks: tasks/reconstruct_openwrt_group.yml
   ```

## Reconstruction Task File Pattern

4. Extract reconstruction logic into a reusable task file (`tasks/reconstruct_openwrt_group.yml`) consumed by converge, verify, and cleanup entry points.

5. The reconstruction task file:
   1. Verifies VM 100 is running via `qm status`
   2. Detects the OpenWrt LAN IP from Proxmox bridge state
   3. Detects the current SSH auth method (key vs. password) by checking `OPENWRT_SSH_PRIVATE_KEY` and `ansible_local.vm_builds.plays`
   4. Registers the host via `add_host` with the correct SSH arguments

## Auth Method Detection

6. The group reconstruction play MUST detect the current auth method (key vs. password) by checking for `OPENWRT_SSH_PRIVATE_KEY` env var and `deploy_stamp` state.

7. After a security hardening rollback, the auth method reverts to password — reconstruction must handle both.

## Version-Aware Convergence Integration

8. When `project_version` in `group_vars/all.yml` advances:
   - `deploy_stamp` compares the new version against `ansible_local.vm_builds.project_version`
   - Feature plays check their own play entry version against current
   - If the versions match, skip expensive operations (package installs, downloads)
   - If the versions differ, re-apply the feature (idempotent convergence)

9. This is the migration path: bump `project_version`, update the feature play, run converge. The play detects the version mismatch and re-applies. No separate migration playbook needed.
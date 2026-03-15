---
name: vm-lifecycle-architecture
description: VM lifecycle architecture patterns and two-role service model. Use when creating new VM types, structuring service roles, or understanding playbook execution order.
---

# VM Lifecycle Architecture Rules

## Two-Role Service Pattern

1. Every service type gets exactly two roles: `<type>_vm` or `<type>_lxc` (provision) and `<type>_configure` (setup). NEVER mix provisioning and configuration in one role.

2. LXC provision roles (`<type>_lxc`) MUST use `include_role: proxmox_lxc` with service-specific vars. NEVER duplicate container creation logic.

## Shared Infrastructure

3. Shared roles (`proxmox_bridges`, `proxmox_backup`, `proxmox_pci_passthrough`, `proxmox_igpu`) run ONCE per host. They export facts consumed by all service roles.

4. Service-specific variables live in role `defaults/main.yml`. Shared variables live in `group_vars/all.yml`. NEVER put service-specific defaults in group_vars.

## Playbook Execution Order (site.yml)

5. Standard playbook execution order:
   ```
   Play 0: proxmox_backup             (targets: proxmox, tag: backup)
            deploy_stamp: backup
   Play 1: proxmox_bridges            (targets: proxmox — shared infra)
            proxmox_pci_passthrough
            proxmox_igpu
            deploy_stamp: infrastructure
   Play 2: <type>_vm                  (targets: <flavor_group> — VM provision)
            deploy_stamp: <type>_vm
   Play 3: <type>_configure           (targets: <type> — dynamic group)
   Play 4+: Feature plays             (targets: <type> — dynamic group, per-feature tags)
            deploy_stamp on <flavor_group> after each feature play
   Play N: Bootstrap cleanup          (targets: proxmox — remove temp networking)
   ```

## VMID Allocation

6. VMIDs: 100s network, 200s services, 300s media, 400s desktop, 500s observability, 600s gaming. All defined in `group_vars/all.yml`.

## Role Structure Requirements

7. The `<type>_vm` role adds the VM to dynamic inventory via `add_host`. The `<type>_configure` role runs in a separate play targeting that group.

8. NEVER reference another role's defaults. Use `set_fact` with `cacheable: true` or `add_host` variables to pass data.

9. Every provision play targeting Proxmox hosts MUST include `deploy_stamp` as its last role.

10. Every new role MUST have `meta/main.yml` with `author`, `license: proprietary`, `role_name`, `description`, `min_ansible_version`, and `platforms`.

11. Provision plays target **flavor groups** (e.g., `router_nodes`, `service_nodes`), NOT `proxmox` directly. Shared infra targets `proxmox`.

## Feature Play Pattern

12. Post-baseline features are implemented as separate task files in the configure role, NOT as separate roles. This avoids cross-role variable dependencies and keeps the configure role as the single owner of VM configuration.

13. Each feature gets a PAIR of plays in `site.yml`:
    - Configure play targeting dynamic group with `include_role` using `tasks_from: <feature>.yml`
    - `deploy_stamp` play targeting flavor group to record feature application
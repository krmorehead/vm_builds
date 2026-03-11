# Shared Infrastructure

## Overview

Cross-cutting infrastructure work that enables provisioning and managing LXC
containers alongside the existing VM workflow. This is not a VM or container
itself — it is the framework, conventions, and shared roles that every
subsequent project depends on.

## Type

Cross-cutting infrastructure

## Prerequisites

- OpenWrt router project baseline (M0 of `2026-03-09-01-openwrt-router`)
  must be complete — the rollback and testing conventions established there
  are used throughout this project.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, VMID allocation, flavor groups |
| `ansible-testing` | Molecule scenarios, verify assertions, baseline model |
| `rollback-patterns` | Per-feature rollback, deploy_stamp tracking |
| `proxmox-host-safety` | Safe commands, PCI cleanup, bridge teardown |
| `project-planning` | Milestone structure and conventions |

## Scope

```
Shared Infrastructure Scope
├── proxmox_lxc         Shared LXC provisioning role (parameterized, reusable)
├── proxmox_igpu        Dedicated iGPU detection and fact export role
├── VMID allocation     Expand group_vars/all.yml with full ID scheme
├── Flavor groups       Inventory groups for build profiles
├── Display exclusion   Proxmox hookscripts for display-exclusive services
├── Auto-start config   Proxmox onboot + startup order for all services
└── Resource validation Optional pre-flight check for CPU/RAM budget
```

---

## Architectural Decisions

```
Decisions
├── Naming convention: <type>_lxc + <type>_configure (containers), <type>_vm + <type>_configure (VMs)
│   └── Clearly communicates provisioning method; mirrors existing openwrt_vm pattern
│
├── LXC provisioning: shared proxmox_lxc role consumed via include_role
│   └── Template download, pct create, networking, add_host handled once; no boilerplate per service
│
├── LXC configuration method: pct exec from Proxmox host
│   └── No SSH needed, no bootstrap IP, no ProxyJump; simpler than the VM bootstrap pattern
│
├── iGPU handling: separate proxmox_igpu role (NOT an extension of proxmox_pci_passthrough)
│   └── iGPU uses device bind mounts (shared); WiFi/dGPU uses vfio-pci (exclusive). Different patterns.
│
├── Display exclusion: Proxmox hookscripts + Ansible pre-tasks
│   └── Hookscripts work outside Ansible (manual start/stop); pre-tasks enforce during deploys
│
└── LXC template: Debian 12 standard for all containers
    └── Consistent base, broad package support, all services officially support Debian
```

---

## Milestones

### Milestone 1: Shared `proxmox_lxc` Role

_Self-contained. No external dependencies._

Create a reusable Ansible role that provisions LXC containers on Proxmox.
Each service's `<type>_lxc` role consumes this via `include_role`.

See: `vm-lifecycle` skill (two-role pattern, add_host, deploy_stamp).

- [ ] Create `roles/proxmox_lxc/defaults/main.yml` with parameters:
  - `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_memory`, `lxc_ct_cores`, `lxc_ct_disk`
  - `lxc_ct_template`, `lxc_ct_bridge`, `lxc_ct_ip`, `lxc_ct_nameserver`, `lxc_ct_gateway`
  - `lxc_ct_features` (e.g., `nesting=1` for Docker-in-LXC)
  - `lxc_ct_mount_entries` (list of device bind mounts)
  - `lxc_ct_onboot` (bool), `lxc_ct_startup_order` (int)
  - `lxc_ct_dynamic_group` (Ansible group name for `add_host`)
- [ ] Create `roles/proxmox_lxc/tasks/main.yml`:
  - Check if container exists (`pct status`); skip creation if present
  - Download LXC template if not cached (`pveam download`)
  - Create container (`pct create`) with all parameters
  - Apply mount entries and features
  - Set `onboot` and `startup` order
  - Start container and wait for readiness (`pct exec -- hostname`)
  - Register in dynamic group via `add_host`
- [ ] Create `roles/proxmox_lxc/meta/main.yml` with required metadata
- [ ] Document role parameters in `docs/architecture/roles.md`

**Verify:**

- [ ] Provision a test container (VMID from test range) via `include_role`
- [ ] Container is running: `pct status <id>` returns `running`
- [ ] Container is in expected dynamic group (Ansible inventory check)
- [ ] Idempotent: second run skips creation, container still running
- [ ] Container destroyed cleanly by molecule cleanup

**Rollback:**

Container destruction is handled by molecule cleanup (`pct stop` + `pct destroy`).
No per-feature rollback needed — the role is idempotent and guarded by existence check.

---

### Milestone 2: Dedicated `proxmox_igpu` Role

_Self-contained. Gracefully skipped when no Intel GPU is present._

Detect the Intel iGPU, verify Quick Sync, and export facts for LXC
container consumption. Deliberately separate from `proxmox_pci_passthrough`
because the iGPU stays on the host driver (shared) rather than being bound
to vfio-pci (exclusive).

See: `proxmox-host-safety` skill (PCI handling, driver coexistence).

- [ ] Create `roles/proxmox_igpu/tasks/main.yml`:
  - Detect Intel GPU via `lspci` (VGA compatible controller, Intel)
  - Verify `/dev/dri/renderD128` exists
  - Verify Quick Sync via `vainfo` (install `vainfo` if missing)
  - Read host `render` group GID
  - Export facts: `igpu_render_device`, `igpu_card_device`, `igpu_render_gid`,
    `igpu_pci_address`, `igpu_available`
- [ ] Ensure `i915` driver is loaded (not blacklisted by WiFi passthrough)
- [ ] Add to infrastructure play in `site.yml` (after `proxmox_pci_passthrough`)
- [ ] Skip gracefully if no Intel GPU detected (`igpu_available: false`)
- [ ] Test coexistence with WiFi PCIe passthrough on same host
- [ ] Create `roles/proxmox_igpu/meta/main.yml` with required metadata

**Verify:**

- [ ] When iGPU present: all four facts exported, `/dev/dri/renderD128` exists
- [ ] When iGPU absent: `igpu_available` is `false`, no errors
- [ ] `i915` driver loaded: `lsmod | grep i915` returns output
- [ ] WiFi passthrough still works when iGPU role runs on same host

**Rollback:**

Fact export only — no host state changes. No rollback needed.

---

### Milestone 3: VMID Allocation & Group Vars

_Self-contained. No external dependencies._

Extend `group_vars/all.yml` with the full VMID scheme and default LXC
template. This is a data-only change with no runtime impact.

- [ ] Add all VMID constants:
  - 100-199 Network: `wireguard_ct_id: 101`, `pihole_ct_id: 102`,
    `meshwifi_ct_id: 103`
  - 200-299 Services: `homeassistant_ct_id: 200`
  - 300-399 Media: `jellyfin_ct_id: 300`, `kodi_ct_id: 301`,
    `moonlight_ct_id: 302`
  - 400-499 Desktop: `desktop_vm_id: 400`, `kiosk_ct_id: 401`
  - 500-599 Observability: `netdata_ct_id: 500`, `rsyslog_ct_id: 501`
  - 600-699 Gaming: `gaming_vm_id: 600`
- [ ] Add `proxmox_lxc_default_template: debian-12-standard_12.7-1_amd64.tar.zst`

**Verify:**

- [ ] All VMID variables resolvable in Ansible (`ansible-inventory --list`
      shows them under `all.yml`)
- [ ] No VMID collisions (all values unique)
- [ ] Existing `molecule test` (default) still passes (no regressions)

**Rollback:**

Data-only change to `group_vars/all.yml`. Revert via git.

---

### Milestone 4: Inventory Flavor Groups & Build Profiles

_Self-contained. No external dependencies._

Expand `inventory/hosts.yml` so hosts opt into services via group membership.

See: `vm-lifecycle` skill (flavor groups, inventory structure).

- [ ] Add flavor groups under `proxmox.children`:
  - `vpn_nodes`, `dns_nodes`, `wifi_nodes`
  - `service_nodes`, `media_nodes`, `desktop_nodes`
  - `monitoring_nodes`, `gaming_nodes`
- [ ] Add empty dynamic groups for runtime population:
  - `wireguard`, `pihole`, `meshwifi`, `homeassistant`
  - `jellyfin`, `kodi`, `moonlight`, `desktop`, `kiosk`, `gaming`
  - `netdata`, `rsyslog` (existing `openwrt` group already present)
- [ ] Assign test host `home` to home entertainment box flavor groups
- [ ] Create `docs/architecture/build-profiles.md`
- [ ] Update `molecule/default/molecule.yml` platform groups to include
      all new flavor groups

**Verify:**

- [ ] `ansible-inventory --graph` shows all flavor groups and dynamic groups
- [ ] Test host `home` appears in expected flavor groups
- [ ] Empty dynamic groups don't cause Ansible warnings
- [ ] Existing `molecule test` still passes (plays targeting empty groups skip)

**Rollback:**

Inventory structure change. Revert via git. No host state changes.

---

### Milestone 5: Display-Exclusive Orchestration

_Blocked on: at least two display-capable LXC services implemented
(Kiosk + Kodi or Kiosk + Moonlight). Cannot test transitions without
multiple display services._

Implement the hookscript system so only one display service runs at a time.

- [ ] Create Proxmox hookscript (`/var/lib/vz/snippets/display-exclusive.sh`):
  - On pre-start: stop all other display services
  - On post-stop of non-default service: start Kiosk (default)
  - Display service VMIDs read from a config variable
- [ ] Deploy hookscript via Ansible task in the infrastructure play
- [ ] Attach hookscript to display-exclusive containers/VMs
      (`pct set` / `qm set --hookscript`)
- [ ] Add Ansible pre-task in `site.yml` that enforces exclusion during deploys
- [ ] Test transitions: Kiosk → Kodi → Kiosk, Kiosk → Desktop → Kiosk

**Verify:**

- [ ] Hookscript exists at `/var/lib/vz/snippets/display-exclusive.sh`
- [ ] Starting Kodi stops Kiosk automatically
- [ ] Stopping Kodi restarts Kiosk automatically
- [ ] Starting Desktop VM stops all LXC display services

**Rollback:**

- Remove hookscript from `/var/lib/vz/snippets/`
- Detach hookscript from containers/VMs (`pct set --delete hookscript`)

---

### Milestone 6: Auto-Start Configuration

_Self-contained. No external dependencies (uses VMID constants from M3)._

Set Proxmox `onboot` and `startup` order for all services. Runs
unconditionally to self-heal (not guarded by existence check).

- [ ] Define startup order lookup table in `group_vars/all.yml`:
      maps VMID → priority number
- [ ] Add tasks to each provision role (or final cleanup play):
  - `pct set <id> --onboot 1 --startup order=<N>` for auto-start containers
  - `qm set <id> --onboot 1 --startup order=<N>` for auto-start VMs
  - `--onboot 0` for on-demand services (Kodi, Moonlight, Desktop)

**Verify:**

- [ ] Each auto-start VM/container has `onboot: 1` in config
- [ ] Startup order matches architecture spec (router=1, VPN=2, etc.)
- [ ] On-demand services have `onboot: 0`
- [ ] Order survives host reboot (verify after `reboot` if safe)

**Rollback:**

Auto-start is set unconditionally and self-heals. To disable, set
`--onboot 0` on specific VMIDs.

---

### Milestone 7: Resource Validation (optional)

_Self-contained. Low priority — implement when multiple services are
provisioned and resource contention becomes a real risk._

Pre-flight check that warns if the host lacks resources for its
flavor groups.

- [ ] Collect host CPU cores and total RAM via Ansible facts
- [ ] Sum resource requirements from all enabled flavor groups
      (VMID constants + resource defaults from roles)
- [ ] Warn (or fail with `--strict`) if total exceeds available minus
      512 MB overhead
- [ ] Account for display-exclusive pairs (don't double-count Desktop + Kiosk)

**Verify:**

- [ ] Warning emitted when resource budget exceeds available
- [ ] No warning when resources are sufficient
- [ ] `--strict` mode fails the play instead of warning

**Rollback:**

Read-only validation — no host state changes. No rollback needed.

---

### Milestone 8: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Update `docs/architecture/roles.md` with `proxmox_lxc` and
      `proxmox_igpu` role documentation
- [ ] Update `docs/architecture/roadmap.md` to reference project plans
- [ ] Update `.cursor/rules/project-structure.mdc` with LXC naming
      conventions and updated checklist
- [ ] Update `vm-lifecycle` skill with LXC-specific patterns learned
      during implementation
- [ ] Add CHANGELOG entry
- [ ] Bump `project_version` in `group_vars/all.yml`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes
- [ ] Full `molecule test` passes
- [ ] Documentation matches implemented behavior

**Rollback:** N/A — documentation-only milestone.

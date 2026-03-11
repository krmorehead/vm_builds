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
├── proxmox_lxc         Shared LXC provisioning role (parameterized, reusable)    ✓
├── proxmox_igpu        Dedicated iGPU detection and fact export role              ✓
├── VMID allocation     Expand group_vars/all.yml with full ID scheme             ✓
├── Flavor groups       Inventory groups for build profiles                        ✓
├── Auto-start config   Proxmox onboot + startup order for all services           ✓
├── Display exclusion   → relocated to Kiosk project (2026-03-09-12, M5)
└── Resource validation → relocated to future operations project
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

- [x] Create `roles/proxmox_lxc/defaults/main.yml` with parameters:
  - `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_memory`, `lxc_ct_cores`, `lxc_ct_disk`
  - `lxc_ct_template`, `lxc_ct_template_path`, `lxc_ct_bridge`, `lxc_ct_ip`, `lxc_ct_nameserver`, `lxc_ct_gateway`
  - `lxc_ct_features` (e.g., `nesting=1` for Docker-in-LXC)
  - `lxc_ct_mount_entries` (list of device bind mounts)
  - `lxc_ct_onboot` (bool), `lxc_ct_startup_order` (int)
  - `lxc_ct_dynamic_group` (Ansible group name for `add_host`)
- [x] Create `roles/proxmox_lxc/tasks/main.yml`:
  - Check if container exists (`pct status`); skip creation if present
  - Upload LXC template from controller if not cached
  - Create container (`pct create`) with all parameters
  - Apply mount entries and features
  - Set `onboot` and `startup` order (unconditional, self-healing)
  - Start container and wait for readiness (`pct exec -- hostname`)
  - Register in dynamic group via `add_host` with `community.proxmox.proxmox_pct_remote`
- [x] Create `roles/proxmox_lxc/meta/main.yml` with required metadata
- [x] Document role parameters in `docs/architecture/roles.md`

**Verify:**

- [x] Provision a test container (VMID 999) via `include_role`
- [x] Container is running: `pct status 999` returns `running`
- [x] Container is in expected dynamic group (Ansible inventory check)
- [x] Idempotent: second run skips creation, container still running
- [x] Container destroyed cleanly by molecule cleanup
- [x] `community.proxmox.proxmox_pct_remote` connection works (`ansible.builtin.ping`)

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

- [x] Create `roles/proxmox_igpu/tasks/main.yml`:
  - Detect Intel GPU via `lspci` (VGA compatible controller, Intel)
  - Load `i915` driver if not loaded (removes blacklist entries, modprobes)
  - Detect card device dynamically (finds the card backed by i915, not hardcoded to card0)
  - Verify `/dev/dri/renderD128` and card device exist
  - Read host `render` and `video` group GIDs
  - Export facts: `igpu_render_device`, `igpu_card_device`, `igpu_render_gid`,
    `igpu_video_gid`, `igpu_pci_address`, `igpu_available`
- [x] Ensure DNS resolution works (falls back to 8.8.8.8 if broken)
- [x] Disable enterprise repos (rename to `.disabled`) and add no-subscription repo
- [x] Install `vainfo` and `intel-media-va-driver` for Quick Sync verification
- [x] Verify VA-API profiles via `vainfo` (asserts profiles are reported)
- [x] Add to infrastructure play in `site.yml` (after `proxmox_pci_passthrough`)
- [x] Skip gracefully if no Intel GPU detected (`igpu_available: false`)
- [x] Create `roles/proxmox_igpu/meta/main.yml` with required metadata

_WiFi PCIe passthrough coexistence test relocated to OpenWrt router project
(`2026-03-09-01`, M0 verify section)._

**Verify:**

- [x] When iGPU present: all facts exported, devices exist on disk, i915 loaded and bound
- [x] When iGPU absent: `igpu_available` is `false`, all facts empty, no errors
- [x] i915 driver: actively loaded if missing, asserts after load
- [x] vainfo: installed, reports VA-API profiles (H.264, HEVC, VP8, VP9)
- [x] Enterprise repos disabled, no-subscription repo configured

**Rollback:**

Enterprise repo changes and DNS fallback are the only host state changes.
Cleanup restores enterprise repos and removes no-subscription repo.

---

### Milestone 3: VMID Allocation & Group Vars

_Self-contained. No external dependencies._

Extend `group_vars/all.yml` with the full VMID scheme and default LXC
template. This is a data-only change with no runtime impact.

- [x] Add all VMID constants:
  - 100-199 Network: `wireguard_ct_id: 101`, `pihole_ct_id: 102`,
    `meshwifi_ct_id: 103`
  - 200-299 Services: `homeassistant_ct_id: 200`
  - 300-399 Media: `jellyfin_ct_id: 300`, `kodi_ct_id: 301`,
    `moonlight_ct_id: 302`
  - 400-499 Desktop: `desktop_vm_id: 400`, `kiosk_ct_id: 401`
  - 500-599 Observability: `netdata_ct_id: 500`, `rsyslog_ct_id: 501`
  - 600-699 Gaming: `gaming_vm_id: 600`
- [x] Add `proxmox_lxc_default_template: debian-12-standard_12.12-1_amd64.tar.zst`
- [x] Add `proxmox_lxc_template_path` for self-hosted template upload
- [x] Add `proxmox_startup_order` lookup table and `proxmox_ondemand_services` list

**Verify:**

- [x] All VMID variables resolvable in Ansible (`ansible-inventory --list`)
- [x] No VMID collisions (13 unique VMIDs verified)
- [x] Existing `molecule test` (default) still passes (no regressions)

**Rollback:**

Data-only change to `group_vars/all.yml`. Revert via git.

---

### Milestone 4: Inventory Flavor Groups & Build Profiles

_Self-contained. No external dependencies._

Expand `inventory/hosts.yml` so hosts opt into services via group membership.

See: `vm-lifecycle` skill (flavor groups, inventory structure).

- [x] Add flavor groups under `proxmox.children`:
  - `vpn_nodes`, `dns_nodes`, `wifi_nodes`
  - `service_nodes`, `media_nodes`, `desktop_nodes`
  - `monitoring_nodes`, `gaming_nodes`
- [x] Add empty dynamic groups for runtime population:
  - `wireguard`, `pihole`, `meshwifi`, `homeassistant`
  - `jellyfin`, `kodi`, `moonlight`, `desktop`, `kiosk`, `gaming`
  - `netdata`, `rsyslog` (existing `openwrt` group already present)
- [x] Assign test host `home` to home entertainment box flavor groups
- [x] Create `docs/architecture/build-profiles.md`
- [x] Update `molecule/default/molecule.yml` platform groups to include
      all new flavor groups

**Verify:**

- [x] `ansible-inventory --graph` shows all flavor groups and dynamic groups
- [x] Test host `home` appears in expected flavor groups
- [x] Empty dynamic groups don't cause Ansible warnings
- [x] Existing `molecule test` still passes (plays targeting empty groups skip)

**Rollback:**

Inventory structure change. Revert via git. No host state changes.

---

### ~~Milestone 5: Display-Exclusive Orchestration~~ (relocated)

Relocated to Custom UX Kiosk project (`2026-03-09-12`, M5). Requires at
least two display-capable services to test transitions.

---

### Milestone 6: Auto-Start Configuration

_Self-contained. No external dependencies (uses VMID constants from M3)._

Set Proxmox `onboot` and `startup` order for all services. Runs
unconditionally to self-heal (not guarded by existence check).

- [x] Define startup order lookup table in `group_vars/all.yml`:
      `proxmox_startup_order` maps VMID → priority number
- [x] Define on-demand service list in `group_vars/all.yml`:
      `proxmox_ondemand_services` lists VMIDs that should NOT auto-start
- [x] `proxmox_lxc` role natively handles `lxc_ct_onboot` and
      `lxc_ct_startup_order` parameters — applied to every container
- [x] `openwrt_vm` role already sets `--onboot 1 --startup order=1`

Per-VM/container application of these settings happens within each service's
provision role. The infrastructure (lookup table + role parameters) is complete.

**Verify:**

- [x] Startup order lookup table and on-demand list are defined
- [x] `proxmox_lxc` applies `onboot` and `startup` settings during provisioning
- [x] `openwrt_vm` applies `onboot` and `startup` settings

**Rollback:**

Auto-start is set unconditionally and self-heals. To disable, set
`--onboot 0` on specific VMIDs.

---

### ~~Milestone 7: Resource Validation~~ (relocated)

Relocated to a future operations project. Cannot be meaningfully tested
until multiple services are provisioned and resource contention is a real
risk. The VMID allocation and flavor group infrastructure (M3, M4) provide
the data model this feature will consume.

---

### Milestone 8: Documentation

_Self-contained. Run after all implemented milestones._

- [x] Update `docs/architecture/roles.md` with `proxmox_lxc` and
      `proxmox_igpu` role documentation
- [x] Update `docs/architecture/roadmap.md` to reference project plans
- [x] Update `.cursor/rules/project-structure.mdc` with LXC naming
      conventions and updated checklist
- [x] Update `vm-lifecycle` skill with LXC-specific patterns learned
      during implementation
- [x] Add CHANGELOG entry
- [x] Bump `project_version` in `group_vars/all.yml`
- [x] Create `docs/architecture/build-profiles.md`

**Verify:**

- [x] `ansible-lint && yamllint .` passes
- [x] Full `molecule test` passes
- [x] Documentation matches implemented behavior

**Rollback:** N/A — documentation-only milestone.

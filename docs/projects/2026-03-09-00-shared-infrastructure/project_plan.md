# Shared Infrastructure

## Overview

Cross-cutting infrastructure work that enables provisioning and managing LXC
containers alongside the existing VM workflow. This is not a VM or container
itself -- it is the framework, conventions, and shared roles that every
subsequent project depends on.

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

Create a reusable Ansible role that provisions LXC containers on Proxmox.
Each service's `<type>_lxc` role consumes this via `include_role`.

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
- [ ] Create integration test in Molecule (provision + destroy a test container)
- [ ] Document role parameters in `docs/architecture/roles.md`

### Milestone 2: Dedicated `proxmox_igpu` Role

Detect the Intel iGPU, verify Quick Sync, and export facts for LXC
container consumption. Deliberately separate from `proxmox_pci_passthrough`
because the iGPU stays on the host driver (shared) rather than being bound
to vfio-pci (exclusive).

- [ ] Create `roles/proxmox_igpu/tasks/main.yml`:
  - Detect Intel GPU via `lspci` (VGA compatible controller, Intel)
  - Verify `/dev/dri/renderD128` exists
  - Verify Quick Sync via `vainfo` (install `vainfo` if missing)
  - Read host `render` group GID
  - Export facts: `igpu_render_device`, `igpu_card_device`, `igpu_render_gid`, `igpu_available`
- [ ] Ensure `i915` driver is loaded (not blacklisted by WiFi passthrough)
- [ ] Add to infrastructure play in `site.yml` (after `proxmox_pci_passthrough`)
- [ ] Skip gracefully if no Intel GPU detected (`igpu_available: false`)
- [ ] Test coexistence with WiFi PCIe passthrough on same host

### Milestone 3: VMID Allocation & Group Vars

Extend `group_vars/all.yml` with the full VMID scheme.

- [ ] Add all VMID constants:
  - 100-199 Network: `wireguard_ct_id: 101`, `pihole_ct_id: 102`, `meshwifi_ct_id: 103`
  - 200-299 Services: `homeassistant_ct_id: 200`
  - 300-399 Media: `jellyfin_ct_id: 300`, `kodi_ct_id: 301`, `moonlight_ct_id: 302`
  - 400-499 Desktop: `desktop_vm_id: 400`, `kiosk_ct_id: 401`
  - 500-599 Observability: `netdata_ct_id: 500`, `rsyslog_ct_id: 501`
  - 600-699 Gaming: `gaming_vm_id: 600`
- [ ] Add `proxmox_lxc_default_template: debian-12-standard_12.7-1_amd64.tar.zst`

### Milestone 4: Inventory Flavor Groups & Build Profiles

Expand `inventory/hosts.yml` so hosts opt into services via group membership.

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
- [ ] Update `molecule/default/molecule.yml` platform groups

### Milestone 5: Display-Exclusive Orchestration

Implement the hookscript system so only one display service runs at a time.

- [ ] Create Proxmox hookscript (`/var/lib/vz/snippets/display-exclusive.sh`):
  - On pre-start: stop all other display services
  - On post-stop of non-default service: start Kiosk (default)
  - Display service VMIDs read from a config variable
- [ ] Deploy hookscript via Ansible task in the infrastructure play
- [ ] Attach hookscript to display-exclusive containers/VMs (`pct set` / `qm set --hookscript`)
- [ ] Add Ansible pre-task in `site.yml` that enforces exclusion during deploys
- [ ] Test transitions: Kiosk → Kodi → Kiosk, Kiosk → Desktop → Kiosk

### Milestone 6: Auto-Start Configuration

Set Proxmox `onboot` and `startup` order for all services.

- [ ] Define startup order in `group_vars/all.yml` as a lookup table
- [ ] Add tasks to each provision role (or final cleanup play) that set:
  - `pct set <id> --onboot 1 --startup order=<N>` for auto-start containers
  - `qm set <id> --onboot 1 --startup order=<N>` for auto-start VMs
  - `--onboot 0` for on-demand services (Kodi, Moonlight, Desktop)
- [ ] Verify boot order matches architecture spec

### Milestone 7: Resource Validation (optional)

Pre-flight check that warns if the host lacks resources for its flavor groups.

- [ ] Collect host CPU cores and total RAM via Ansible facts
- [ ] Sum resource requirements from all enabled flavor groups
- [ ] Warn (or fail with `--strict`) if total exceeds available minus 512 MB overhead
- [ ] Account for display-exclusive pairs (don't double-count Desktop + Kiosk)

### Milestone 8: Documentation

- [ ] Update `docs/architecture/overview.md` (done)
- [ ] Update `docs/architecture/roles.md` with `proxmox_lxc` and `proxmox_igpu`
- [ ] Update `docs/architecture/roadmap.md` to reference project plans
- [ ] Update `.cursor/rules/project-structure.mdc` with LXC naming conventions
- [ ] Add CHANGELOG entry

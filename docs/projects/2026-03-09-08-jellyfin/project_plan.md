# Jellyfin

## Overview

An LXC container running Jellyfin media server with Intel Quick Sync
hardware transcoding via iGPU device passthrough. Serves media to clients
locally and remotely. Offloads transcoding to GPU, keeping CPU usage minimal.

## Type

LXC container

## Resources

- Cores: 2
- RAM: 2048 MB
- Disk: 8 GB (application + metadata; media on external mount)
- Network: LAN bridge, static IP
- iGPU: `/dev/dri/renderD128` bind mount (shared, not exclusive)
- VMID: 300

## Startup

- Auto-start: yes
- Boot priority: 5 (alongside Home Assistant)
- Depends on: OpenWrt Router, `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes (`media_nodes`)
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **no** (uses renderD128 for transcoding only, not display output)
- Runs alongside any display service (Kiosk, Kodi, Moonlight)
- Falls back to software transcoding when Desktop VM takes exclusive iGPU

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- Shared infrastructure: `proxmox_igpu` role (project 00, milestone 2)
- OpenWrt router operational (network)
- Media storage accessible (NFS/SMB mount or local disk)
- `jellyfin_ct_id: 300` already in `group_vars/all.yml`
- `media_nodes` flavor group and `jellyfin` dynamic group already in `inventory/hosts.yml`
- `media_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` exports `igpu_render_device`, `igpu_render_gid`, `igpu_vendor` (hard-fails if absent)
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`media_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Jellyfin containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case — media services only run on the Home
Entertainment Box profile, which always has OpenWrt. If `media_nodes` ever
includes a WAN-connected host, add the WireGuard-style topology branching
at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle-architecture` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `image-management-patterns` | Image build, local images/ directory, template management |
| `lxc-container-patterns` | LXC provisioning, pct_remote connection, container networking |
| `molecule-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-architecture` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-system-safety` | iGPU hard-fail detection, safe host commands, shell pipefail |
| `lan-ssh-patterns` | ProxyJump for testing on LAN nodes |
| `project-planning-structure` | Milestone structure, verify/rollback sections |

## iGPU Hard-Fail Requirement

**CRITICAL:** `proxmox_igpu` hard-fails if no iGPU is found (Intel or AMD).
Jellyfin depends on iGPU facts (`igpu_render_device`, `igpu_render_gid`,
`igpu_vendor`) from the infrastructure play. If `proxmox_igpu` fails,
Jellyfin provisioning must never run. This is enforced by play ordering in
`site.yml`: the infrastructure play (Play 1: `proxmox_igpu`) runs before
`media_nodes` provision plays. Media plays only execute when infrastructure
succeeds.

## Env Variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `JELLYFIN_ADMIN_PASSWORD` | Production: yes | Admin user password | Auto-generated for testing when empty |
| `jellyfin_media_path` | — | Host-side media mount path | `group_vars/all.yml` (e.g., `/mnt/media`) |

---

## Architectural Decisions

```
Decisions
├── Media server: Jellyfin
│   └── FOSS, no license, good VA-API support, active development
│
├── LXC base: Custom Debian 12 template with Jellyfin + VA-API baked in
│   ├── "Bake, don't configure at runtime" — Jellyfin packages and VA-API drivers baked into image
│   └── Configure role only applies host-specific settings (admin user, media paths, transcoding toggle)
│
├── Image build: Debian 12 standard + Jellyfin + VA-API in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs Jellyfin from official Debian repo (GPG key + apt source)
│   ├── Installs VA-API packages: vendor-specific (intel-media-va-driver or mesa-va-drivers)
│   │   └── Build includes BOTH Intel and AMD VA-API packages for portability
│   └── Pre-configures: web port 8096, hardware transcoding default, logging
│
├── Container privileges: unprivileged with device passthrough
│   └── More secure; /dev/dri/renderD128 via cgroup allowlist + GID mapping
│
├── iGPU access: device bind mount (shared) via proxmox_igpu facts
│   └── NOT full PCI passthrough; iGPU stays on host i915/amdgpu driver; multiple containers share
│
├── LXC features: none required
│   └── Jellyfin is a standard userspace daemon — no nesting, no iptables
│   └── Device passthrough handled by cgroup allowlist + bind mount, not LXC features
│
└── Media storage: NFS mount from home server / NAS
    └── Large libraries don't fit on local disk; NFS is transparent to Jellyfin
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Jellyfin provisions on `media_nodes` (currently
`home` only). It runs alongside other Phase 3 plays. Jellyfin, Kodi, and
Moonlight share the `[media]` tag and provision in the same play on
`media_nodes`.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/jellyfin-lxc/` which only touches
VMID 300. The OpenWrt baseline and other containers stay running.

```
Scenario Hierarchy (Jellyfin additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Jellyfin provision + configure
│
└── molecule/jellyfin-lxc/           Jellyfin container only (~30-60s)
    ├── converge: provision + configure Jellyfin container
    ├── verify: Jellyfin-specific assertions
    └── cleanup: destroy container 300 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on Jellyfin container (only touches VMID 300)
molecule converge -s jellyfin-lxc             # ~30s, provision + configure
molecule verify -s jellyfin-lxc               # ~10s, assertions only
molecule converge -s jellyfin-lxc             # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s jellyfin-lxc              # destroys container 300 only

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `jellyfin-lxc` | Container 300 | Container 300 only | None — OpenWrt, WireGuard, etc. untouched |

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained (blocked on: proxmox_igpu for VA-API driver selection)
 └── M1: Provisioning ── depends on M0, proxmox_igpu (igpu_render_device)
      └── M2: Configuration ── depends on M1
           └── M3: Testing & Integration ── depends on M1–M2
                └── M4: Documentation ── depends on M1–M3
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with Jellyfin and VA-API drivers
pre-installed. Per the project's "Bake, don't configure at runtime"
principle, all packages belong in the image. The configure role (M2) only
applies host-specific settings (admin user, media paths, transcoding toggle).

See: `image-management-patterns` skill.

**Implementation pattern:**
- Script: add Jellyfin image build section to `build-images.sh`
- Template path: `images/jellyfin-debian-12-amd64.tar.zst`
- Template vars: `jellyfin_lxc_template` and `jellyfin_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Add Jellyfin official repo (GPG key + apt source)
3. Install Jellyfin server and web packages
4. Install VA-API drivers — include BOTH Intel (`intel-media-va-driver`)
   and AMD (`mesa-va-drivers`) packages for build portability. At runtime,
   only the matching driver loads based on the actual GPU.
5. Install `vainfo` for transcoding verification
6. Pre-configure: web port 8096, hardware transcoding defaults
7. Clean apt caches, stop container
8. Export via `vzdump` and download template

**VA-API driver note:** Package names depend on Debian release. ALWAYS
verify with `apt-cache search` before adding to the build script. Previous
bug: `intel-media-va-driver-non-free` does not exist on newer Debian
releases; correct package is `intel-media-va-driver`.

- [ ] Add Jellyfin template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_jellyfin_lxc` function)
- [ ] Add `jellyfin_lxc_template` and `jellyfin_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Add `jellyfin_ct_ip_offset: 15` to `group_vars/all.yml`
  (after homeassistant at 14; see IP allocation table in project-structure)
- [ ] Add `jellyfin_media_path: /mnt/media` to `group_vars/all.yml`
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/jellyfin-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Jellyfin packages pre-installed
- [ ] Template contains VA-API drivers (Intel + AMD)
- [ ] `vainfo` binary present in template
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built). Blocked on: infrastructure play
(proxmox_igpu) — Play 1 in site.yml runs first. If proxmox_igpu hard-fails
(no iGPU), this play never runs._

Create the `jellyfin_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision and configure plays to `site.yml`, and verify the
container runs with iGPU device mount and media bind mount. Integration
with `site.yml` is consolidated here.

See: `lxc-container-patterns` skill (LXC provisioning pattern, deploy_stamp, device mounts).

**Implementation pattern:**
- Role: `roles/jellyfin_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[media]`,
  in Phase 3 (combined with Kodi and Moonlight when those exist)
- deploy_stamp included as last role in the provision play
- Dynamic group `jellyfin` populated by `proxmox_lxc` via `add_host`
- iGPU device: `igpu_render_device` from proxmox_igpu facts; cgroup allowlist
  `c 226:128 rwm`; media bind mount from `jellyfin_media_path`

**Already complete** (from shared infrastructure):
- `jellyfin_ct_id: 300` in `group_vars/all.yml`
- `media_nodes` flavor group and `jellyfin` dynamic group in `inventory/hosts.yml`
- `media_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` exports `igpu_render_device`, `igpu_render_gid` (hard-fails if absent)

- [ ] Create `roles/jellyfin_lxc/defaults/main.yml`:
  - `jellyfin_ct_hostname: jellyfin`
  - `jellyfin_ct_memory: 2048`, `jellyfin_ct_cores: 2`, `jellyfin_ct_disk: "8"`
  - `jellyfin_ct_template: "{{ jellyfin_lxc_template }}"` (custom Jellyfin image)
  - `jellyfin_ct_template_path: "{{ jellyfin_lxc_template_path }}"`
  - `jellyfin_ct_onboot: true`, `jellyfin_ct_startup_order: 5`
  - `jellyfin_ct_ip_offset: "{{ jellyfin_ct_ip_offset | default(15) }}"`
  - No `lxc_ct_features` needed (Jellyfin is standard userspace)
- [ ] Create `roles/jellyfin_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Compute container IP from LAN prefix + offset (LAN-only, no WAN branching)
  - Include `proxmox_lxc` with:
    - `lxc_ct_mount_entries`: iGPU device (`{{ igpu_render_device }},mp={{ igpu_render_device }}`),
      media bind mount (`{{ jellyfin_media_path }},mp=/media`)
    - `lxc_ct_features` or raw config for cgroup allowlist `c 226:128 rwm`
    - All standard vars: `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_dynamic_group`,
      memory, cores, disk, onboot, startup_order
- [ ] Create `roles/jellyfin_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `media_nodes`,
  tagged `[media]`, with `jellyfin_lxc` role and `deploy_stamp`
  (after infrastructure play, after OpenWrt configure)
- [ ] Add configure play to `site.yml` Phase 3, targeting `jellyfin` dynamic
  group, tagged `[media]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_jellyfin_group.yml`:
  - Verify container 300 is running (`pct status {{ jellyfin_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ jellyfin_ct_id }}`,
    `ansible_user: root`

**Note on `[media]` tag:** This tag is shared with Kodi and Moonlight (per
the target site.yml architecture, all three provision in the same play on
`media_nodes`). Configure plays remain separate since they target different
dynamic groups (`jellyfin`, `kodi`, `moonlight`).

**Verify:**

- [ ] Container 300 is running: `pct status 300` returns `running`
- [ ] Container is in `jellyfin` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 300` shows `onboot: 1`, `startup: order=5`
- [ ] iGPU device mounted: `pct exec 300 -- ls -la /dev/dri/renderD128` succeeds
- [ ] Media path mounted: `pct exec 300 -- ls /media` succeeds (or path exists)
- [ ] Correct static IP matches computed offset
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `jellyfin_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: **none** — Jellyfin does not deploy
host-side files (no kernel modules, no host config). Container cleanup is generic.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with host-specific settings: admin user,
media paths, iGPU render group mapping, and transcoding toggle. Jellyfin
packages and VA-API drivers are already baked into the image (M0). This
role only applies host-specific configuration.

See: `lxc-container-patterns` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/jellyfin_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/` (if needed), `meta/main.yml`
- site.yml: configure play targeting `jellyfin` dynamic group, tagged
  `[media]`, after the provision play
- Connection: `community.proxmox.proxmox_pct_remote` (pct exec from Proxmox host)

- [ ] Create `roles/jellyfin_configure/defaults/main.yml`:
  - `JELLYFIN_ADMIN_PASSWORD` via `lookup('env', 'JELLYFIN_ADMIN_PASSWORD') | default('', true)`
  - Auto-generate password when empty (testing)
- [ ] Create `roles/jellyfin_configure/tasks/main.yml` (via `pct_remote`):
  - Configure iGPU: create `render` group with GID from `igpu_render_gid`,
    add `jellyfin` user to group, verify `vainfo` succeeds
  - Template server config: VA-API transcode, media paths (`/media`),
    web on port 8096
  - Set admin user from env (`JELLYFIN_ADMIN_PASSWORD`) or generated value
  - Configure log forwarding to rsyslog (if rsyslog project complete)
- [ ] Create `roles/jellyfin_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- Jellyfin packages and service — baked
- VA-API drivers (Intel + AMD) — baked
- `vainfo` binary — baked
- Base web port 8096 configuration — baked

**Verify:**

- [ ] Jellyfin service running: `pct exec 300 -- systemctl is-active jellyfin-server`
- [ ] Web UI on port 8096: `pct exec 300 -- ss -tlnp` shows 8096
- [ ] `/dev/dri/renderD128` exists: `pct exec 300 -- ls -la /dev/dri/renderD128`
- [ ] `vainfo` succeeds: `pct exec 300 -- vainfo` returns VA-API info
- [ ] Media path accessible: `pct exec 300 -- ls /media` (or configured path)
- [ ] Admin user set (or auto-generated for testing)
- [ ] Idempotent: second run does not regenerate password when env var set

**Rollback:**

- Stop and disable service: `pct exec 300 -- systemctl disable --now jellyfin-server`
- Remove Jellyfin config: `pct exec 300 -- rm -rf /etc/jellyfin`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for fast Jellyfin-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, and run final validation.

See: `molecule-testing` skill (per-feature scenario setup, baseline workflow),
`molecule-verify` skill (verify completeness), `molecule-cleanup` skill (cleanup completeness).

#### 3a. Per-feature scenario: `molecule/jellyfin-lxc/`

Covers container provisioning + configuration. Only touches VMID 300.
Assumes baseline exists (OpenWrt running, LAN bridge up).

- [ ] Create `molecule/jellyfin-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - media_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      JELLYFIN_ADMIN_PASSWORD: ${JELLYFIN_ADMIN_PASSWORD:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/jellyfin-lxc/converge.yml`:
  ```yaml
  - name: Provision Jellyfin LXC container
    hosts: media_nodes
    gather_facts: false
    roles:
      - jellyfin_lxc

  - name: Reconstruct jellyfin dynamic group
    hosts: media_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_jellyfin_group.yml

  - name: Configure Jellyfin
    hosts: jellyfin
    gather_facts: true
    roles:
      - jellyfin_configure
  ```

- [ ] Create `molecule/jellyfin-lxc/verify.yml`:
  Jellyfin-specific assertions. Runs on `media_nodes` via `pct exec`.

- [ ] Create `molecule/jellyfin-lxc/cleanup.yml`:
  Destroys only container 300.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Jellyfin assertions:
  - Container 300 running, onboot=1, startup order=5
  - Jellyfin service active, web on port 8096
  - `/dev/dri/renderD128` exists, `vainfo` succeeds
  - Media path mounted
  - deploy_stamp contains `jellyfin_lxc` entry

- [ ] Verify generic container cleanup handles VMID 300

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `jellyfin-rollback` play:
  ```yaml
  - name: Rollback Jellyfin container
    hosts: media_nodes
    gather_facts: false
    tags: [jellyfin-rollback, never]
    tasks:
      - name: Stop and destroy Jellyfin container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ jellyfin_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ jellyfin_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Molecule env passthrough

- [ ] Add `JELLYFIN_ADMIN_PASSWORD` to `molecule/default/molecule.yml`
  `provisioner.env` (optional, empty for tests)

#### 3e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s jellyfin-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Jellyfin artifacts on host or controller

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/jellyfin-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - iGPU shared mount, cgroup allowlist `c 226:128 rwm`, GID mapping
  - VA-API driver package name caveat (Debian release-dependent)
  - Media storage, software fallback when Desktop VM takes iGPU
  - Baked config vs runtime config split
  - Test vs production workflow (JELLYFIN_ADMIN_PASSWORD)
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Jellyfin provision + configure plays
  - Role catalog: jellyfin_lxc, jellyfin_configure
- [ ] Update `docs/architecture/roles.md`:
  - Add `jellyfin_lxc` role documentation (purpose, key variables, iGPU device mount)
  - Add `jellyfin_configure` role documentation (purpose, env vars, GID mapping)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Jellyfin project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] iGPU hard-fail requirement documented
- [ ] VA-API package name caveat documented
- [ ] All env variables documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Kodi JellyCon**: Kodi (project 09) uses JellyCon add-on to connect to
  Jellyfin for media library access. Kodi's configure role templates the
  JellyCon connection settings using Jellyfin's container IP.
- **Moonlight**: Moonlight (project 10) is a game streaming client, not
  related to Jellyfin. Both share `media_nodes` and the `[media]` tag.
- **rsyslog**: Jellyfin logs can be forwarded to the rsyslog collector.
  The configure role templates a syslog forwarding snippet when rsyslog
  is available.
- **Desktop VM impact**: When the Desktop VM (project 11) starts, it takes
  exclusive iGPU access. Jellyfin falls back to software transcoding
  automatically — no action needed from this project.
- **iGPU vendor expansion**: The image includes both Intel and AMD VA-API
  drivers. At runtime, only the matching driver loads. This supports mixed
  hardware fleets.

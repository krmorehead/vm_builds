# Kodi

## Overview

An LXC container running Kodi as a local media player and home theater
frontend. Connects to Jellyfin for media library access. Renders directly
to the physical display via DRM/KMS using the shared iGPU. HDMI audio
output via ALSA bind mount.

## Type

LXC container

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 4 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (display output + decode; shared, NOT exclusive passthrough)
- Audio: `/dev/snd/*` bind mount (HDMI audio output)
- Device nodes: cgroup allowlist `c 226:* rwm` (DRI), `c 116:* rwm` (sound), `c 13:* rwm` (input)
- VMID: 301

## Startup

- Auto-start: **no** (`onboot: false` — on-demand container; user starts for media playback)
- Boot priority: N/A
- Depends on: Jellyfin (media backend), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes (`media_nodes`)
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes**
- Start Kodi → Kiosk stops (hookscript)
- Stop Kodi → Kiosk restarts (hookscript)
- iGPU access: shared (LXC bind mount, not exclusive passthrough)
- **Hookscript ownership:** The display-exclusive hookscript is deployed by the Custom UX Kiosk project (2026-03-09-12). Kodi just attaches it via `pct set`.

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive hookscript (project 00)
- Jellyfin (project 08) — media server backend
- Physical display connected to host HDMI/DP
- **iGPU:** `proxmox_igpu` hard-fails if absent. Supports Intel and AMD.
- `kodi_ct_id: 301` already in `group_vars/all.yml`
- `media_nodes` flavor group and `kodi` dynamic group already in `inventory/hosts.yml`
- `media_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` exports `igpu_render_device`, `igpu_render_gid`, `igpu_vendor`
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`media_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Kodi containers always use the OpenWrt LAN subnet on the LAN bridge.
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

---

## Architectural Decisions

```
Decisions
├── Display output: kodi-standalone via GBM/DRM
│   └── Direct rendering to display, no X11 or Wayland needed, minimal overhead
│
├── LXC base: Custom Debian 12 template with Kodi + GBM/DRM stack baked in
│   ├── "Bake, don't configure at runtime" — all packages baked into image
│   └── Configure role only applies host-specific settings (Jellyfin connection, audio, web interface)
│
├── Image build: Debian 12 standard + Kodi + GBM/DRM in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs kodi-standalone, kodi-gbm, Mesa Intel drivers, libcec
│   ├── Pre-configures kodi-standalone systemd service
│   └── Pre-configures advancedsettings.xml template
│
├── iGPU: shared bind mount via /dev/dri/*
│   └── Uses /dev/dri/* for display output + decode (shared, NOT exclusive passthrough)
│   └── proxmox_igpu hard-fails if absent — required on every host
│
├── Audio output: ALSA passthrough via /dev/snd/* bind mount
│   └── Direct HDMI audio through iGPU, lowest latency
│   └── Device nodes: c 116:* rwm in cgroup allowlist
│
├── LXC features: none required
│   └── Device passthrough handled by cgroup allowlist + bind mount, not LXC features
│
├── Jellyfin plugin: JellyCon
│   └── Well-maintained Kodi add-on; native library browsing in Kodi UI
│
└── Remote control: Kodi web interface + CEC (HDMI-CEC via libcec)
    └── Web remote from phone; CEC for TV remote control
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Kodi provisions on `media_nodes` (currently
`home` only). It runs alongside Jellyfin and Moonlight. All three share
the `[media]` tag and provision in the same play on `media_nodes`.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/kodi-lxc/` which only touches
VMID 301. The OpenWrt baseline and other containers stay running.

```
Scenario Hierarchy (Kodi additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Kodi provision + configure
│
└── molecule/kodi-lxc/               Kodi container only (~30-60s)
    ├── converge: provision + configure Kodi container
    ├── verify: Kodi-specific assertions
    └── cleanup: destroy container 301 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on Kodi container (only touches VMID 301)
molecule converge -s kodi-lxc                 # ~30s, provision + configure
molecule verify -s kodi-lxc                   # ~10s, assertions only
molecule converge -s kodi-lxc                 # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s kodi-lxc                  # destroys container 301 only

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `kodi-lxc` | Container 301 | Container 301 only | None — OpenWrt, WireGuard, etc. untouched |

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M1: Provisioning ── depends on M0, proxmox_igpu (igpu_render_device)
      └── M2: Configuration ── depends on M1
           └── M3: Testing & Integration ── depends on M1–M2
                └── M4: Documentation ── depends on M1–M3
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with Kodi, GBM/DRM stack, Mesa
drivers, and libcec pre-installed. Per the project's "Bake, don't configure
at runtime" principle, all packages belong in the image. The configure
role (M2) only applies host-specific settings (Jellyfin connection,
audio config, web interface).

See: `image-management-patterns` skill.

**Implementation pattern:**
- Script: add Kodi image build section to `build-images.sh`
- Template path: `images/kodi-debian-12-amd64.tar.zst`
- Template vars: `kodi_lxc_template` and `kodi_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Install `kodi-standalone`, `kodi-gbm`, Mesa Intel drivers, `libcec`
3. Pre-configure kodi-standalone systemd service
4. Pre-configure `advancedsettings.xml` template for buffer/cache tuning
5. Clean apt caches, stop container
6. Export via `vzdump` and download template

- [ ] Add Kodi template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_kodi_lxc` function)
- [ ] Add `kodi_lxc_template` and `kodi_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/kodi-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Kodi packages pre-installed (kodi-standalone, kodi-gbm)
- [ ] Template contains Mesa drivers and libcec
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built). Blocked on: infrastructure play
(proxmox_igpu) — hard-fails if no iGPU._

Create the `kodi_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision and configure plays to `site.yml`, and verify the
container runs. Uses `/dev/dri/*` for display + decode and `/dev/snd/*`
for HDMI audio. Attaches the display-exclusive hookscript (deployed by
project 2026-03-09-12). Integration with `site.yml` is consolidated here.

See: `lxc-container-patterns` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/kodi_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[media]`,
  in Phase 3 (combined with Jellyfin and Moonlight)
- deploy_stamp included as last role in the provision play
- Dynamic group `kodi` populated by `proxmox_lxc` via `add_host`
- Hookscript: attach via `pct set {{ kodi_ct_id }} --hookscript ...` (script deployed by Kiosk project)

**Already complete** (from shared infrastructure / inventory):
- `kodi_ct_id: 301` in `group_vars/all.yml`
- `media_nodes` flavor group and `kodi` dynamic group in `inventory/hosts.yml`
- `media_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` role exports `igpu_*` facts (hard-fails if no iGPU)

- [ ] Create `roles/kodi_lxc/defaults/main.yml`:
  - `kodi_ct_hostname: kodi`
  - `kodi_ct_memory: 1024`, `kodi_ct_cores: 2`, `kodi_ct_disk: "4"`
  - `kodi_ct_template: "{{ kodi_lxc_template }}"` (custom Kodi image)
  - `kodi_ct_template_path: "{{ kodi_lxc_template_path }}"`
  - `kodi_ct_onboot: false` (on-demand, display-exclusive)
  - No `lxc_ct_features` needed (device passthrough via cgroup allowlist)
- [ ] Create `roles/kodi_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_dynamic_group`, memory, cores,
    disk, onboot
  - Device mounts via `lxc_ct_mount_entries`: `/dev/dri`, `/dev/snd`, `/dev/input`
  - cgroup allowlist: DRI (`c 226:* rwm`), sound (`c 116:* rwm`), input (`c 13:* rwm`)
  - Attach display-exclusive hookscript via `pct set` (script path from Kiosk project)
- [ ] Create `roles/kodi_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `media_nodes`,
  tagged `[media]`, with `kodi_lxc` role and `deploy_stamp`
  (combined with Jellyfin and Moonlight in same play)
- [ ] Add configure play to `site.yml` Phase 3, targeting `kodi` dynamic
  group, tagged `[media]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_kodi_group.yml`:
  - Verify container 301 is running (`pct status {{ kodi_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ kodi_ct_id }}`,
    `ansible_user: root`

**Note on `[media]` tag:** This tag is shared with Jellyfin and Moonlight.
All three provision in the same play on `media_nodes`. Configure plays
remain separate since they target different dynamic groups.

**Verify:**

- [ ] Container 301 is running: `pct status 301` returns `running`
- [ ] Container is in `kodi` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 301` shows `onboot: 0`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] Device mounts present: `pct config 301` shows mp0/mp1/mp2 for DRI, snd, input
- [ ] cgroup allowlist includes `c 226:* rwm`, `c 116:* rwm`, `c 13:* rwm`
- [ ] Hookscript attached via `pct config 301`
- [ ] deploy_stamp contains `kodi_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). **Host-side cleanup: none** — Kodi does not deploy host
files. Container cleanup is generic.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with host-specific settings: JellyCon
add-on connection, ALSA HDMI audio, Kodi web interface, and
advancedsettings.xml. Kodi packages are already baked into the image (M0).
This role only applies host-specific configuration.

See: `lxc-container-patterns` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/kodi_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/advancedsettings.xml.j2`, `meta/main.yml`
- site.yml: configure play targeting `kodi` dynamic group, tagged
  `[media]`, after the provision play
- Connection: `community.proxmox.proxmox_pct_remote` (no SSH inside container)

- [ ] Create `roles/kodi_configure/defaults/main.yml`:
  - `kodi_jellyfin_url`, `kodi_jellyfin_token` (from `.env` via `lookup('env', ...) | default('', true)`)
  - `kodi_web_port: 8080`
- [ ] Create `roles/kodi_configure/tasks/main.yml` (via `pct_remote`):
  - Install JellyCon add-on, template connection settings (Jellyfin IP, credentials)
  - Configure ALSA HDMI audio output
  - Enable Kodi web interface on port 8080
  - Auto-start: enable `kodi-standalone` systemd service on container boot
  - Template `advancedsettings.xml` for buffer/cache tuning
- [ ] Create `roles/kodi_configure/templates/advancedsettings.xml.j2`
- [ ] Create `roles/kodi_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- Kodi packages (kodi-standalone, kodi-gbm) — baked
- Mesa Intel drivers — baked
- libcec — baked
- systemd service template — baked
- advancedsettings.xml skeleton — baked

**Verify:**

- [ ] Kodi packages installed: `pct exec 301 -- dpkg -l kodi-standalone kodi-gbm`
- [ ] DRI devices present: `pct exec 301 -- ls -la /dev/dri/`
- [ ] Sound devices present: `pct exec 301 -- ls -la /dev/snd/`
- [ ] JellyCon add-on installed (check addon dir or Kodi DB)
- [ ] Kodi web interface enabled on port 8080 (when Kodi is running)
- [ ] `kodi-standalone` systemd service enabled
- [ ] `advancedsettings.xml` templated
- [ ] Idempotent: second run does not change state

**Rollback:**

- Stop and disable service: `systemctl disable --now kodi` (or equivalent)
- Remove Kodi config: `rm -rf /root/.kodi` (or user-specific path)
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for fast Kodi-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, and run final validation.

See: `molecule-testing` skill (per-feature scenario setup, baseline workflow),
`molecule-verify` skill (verify completeness), `molecule-cleanup` skill (cleanup
completeness).

#### 3a. Per-feature scenario: `molecule/kodi-lxc/`

- [ ] Create `molecule/kodi-lxc/molecule.yml`:
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
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/kodi-lxc/converge.yml`:
  ```yaml
  - name: Provision Kodi LXC container
    hosts: media_nodes
    gather_facts: false
    roles:
      - kodi_lxc

  - name: Reconstruct kodi dynamic group
    hosts: media_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_kodi_group.yml

  - name: Configure Kodi
    hosts: kodi
    gather_facts: true
    roles:
      - kodi_configure
  ```

- [ ] Create `molecule/kodi-lxc/verify.yml`
- [ ] Create `molecule/kodi-lxc/cleanup.yml`:
  Destroys only container 301.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Kodi assertions:
  - Container 301 created and running
  - Kodi packages installed, DRI devices present, sound devices present
  - Kodi web interface on port 8080 (when running)
  - JellyCon add-on installed
  - Hookscript attached
  - deploy_stamp contains `kodi_lxc` entry

- [ ] Verify generic container cleanup handles VMID 301

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `kodi-rollback` play:
  ```yaml
  - name: Rollback Kodi container
    hosts: media_nodes
    gather_facts: false
    tags: [kodi-rollback, never]
    tasks:
      - name: Stop and destroy Kodi container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ kodi_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ kodi_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s kodi-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Kodi artifacts on host (container cleanup only)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/kodi-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - GBM/DRM output, display-exclusive transitions (Kiosk hookscript)
  - iGPU shared bind mount, audio `/dev/snd/*` bind mount
  - Remote control: web interface, CEC
  - `onboot: false` — on-demand container
  - Baked config vs runtime config split
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Kodi provision + configure plays
  - Verify media topology includes Kodi container
- [ ] Update `docs/architecture/roles.md`:
  - Add `kodi_lxc` role documentation (purpose, device mounts, key variables)
  - Add `kodi_configure` role documentation (purpose, JellyCon, ALSA)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Kodi project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] Display exclusivity and hookscript ownership (Kiosk project) documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Jellyfin**: Kodi connects to Jellyfin via JellyCon add-on for media
  library access. The configure role templates the JellyCon connection
  settings using Jellyfin's container IP.
- **Display exclusivity**: Kodi shares the display-exclusive hookscript
  with Moonlight (302), Desktop VM (400), and Kiosk (401). The hookscript
  is deployed by the Kiosk project (2026-03-09-12); Kodi only attaches.
- **Kiosk**: When Kodi stops, Kiosk auto-restarts as the default display
  state.
- **CEC**: HDMI-CEC via libcec allows TV remote control. CEC device
  passthrough may need additional cgroup rules depending on hardware.
- **rsyslog**: Kodi logs can be forwarded to the rsyslog collector via
  syslog configuration.

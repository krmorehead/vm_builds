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

- Home Entertainment Box: yes
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
- **iGPU:** `proxmox_igpu` hard-fails if absent. Every modern Intel CPU has an iGPU.
- Debian 12 LXC template in `images/` directory

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | iGPU hard-fail detection, safe host commands, shell pipefail |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Display output: kodi-standalone via GBM/DRM
│   └── Direct rendering to display, no X11 or Wayland needed, minimal overhead
│
├── iGPU: shared bind mount via /dev/dri/*
│   └── Uses /dev/dri/* for display output + decode (shared, NOT exclusive passthrough)
│   └── proxmox_igpu hard-fails if absent — required on every host
│
├── Audio output: ALSA passthrough via /dev/snd/* bind mount
│   └── Direct HDMI audio through iGPU, lowest latency
│   └── Device nodes: c 116:* rwm in cgroup allowlist
│
├── Jellyfin plugin: JellyCon
│   └── Well-maintained Kodi add-on; native library browsing in Kodi UI
│
└── Remote control: Kodi web interface + CEC (HDMI-CEC via libcec)
    └── Web remote from phone; CEC for TV remote control
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ─────── self-contained
 └── M2: Kodi Config ──────── self-contained, depends on M1
      └── M3: Integration ── self-contained, depends on M1+M2
           └── M4: Testing ── self-contained, depends on M1–M3
                └── M5: Docs ─ self-contained, depends on M1–M4
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `kodi_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.
Uses `/dev/dri/*` for display + decode and `/dev/snd/*` for HDMI audio.
Attaches the display-exclusive hookscript (deployed by project 2026-03-09-12).

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/kodi_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[media]`
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
  - `kodi_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `kodi_ct_onboot: false` (on-demand, display-exclusive)
  - `kodi_ct_startup_order: 0` (N/A for on-demand)
- [ ] Create `roles/kodi_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ kodi_ct_id }}"`, `lxc_ct_hostname: kodi`,
    `lxc_ct_dynamic_group: kodi`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`
  - Device mounts via `lxc_ct_mount_entries`: `/dev/dri`, `/dev/snd`, `/dev/input`
  - cgroup allowlist: DRI (`c 226:* rwm`), sound (`c 116:* rwm`), input (`c 13:* rwm`)
  - Attach display-exclusive hookscript via `pct set` (script path from Kiosk project)
- [ ] Create `roles/kodi_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` targeting `media_nodes`, tagged
  `[media]`, with `kodi_lxc` role and `deploy_stamp`
- [ ] Verify Debian 12 LXC template exists in `images/` directory

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

### Milestone 2: Kodi Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with Kodi packages, JellyCon add-on,
ALSA HDMI audio, and web interface. Uses `pct exec` via `pct_remote`.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/kodi_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/advancedsettings.xml.j2`, `meta/main.yml`
- site.yml: configure play targeting `kodi` dynamic group, tagged
  `[media]`, after the provision play
- Connection: `community.proxmox.proxmox_pct_remote` (no SSH inside container)

- [ ] Create `roles/kodi_configure/defaults/main.yml`:
  - `kodi_jellyfin_url`, `kodi_jellyfin_token` (from `.env` via `lookup('env', ...) | default('', true)`)
  - `kodi_web_port: 8080`
- [ ] Create `roles/kodi_configure/tasks/main.yml`:
  - Install `kodi-standalone`, `kodi-gbm`, Mesa Intel drivers, `libcec`
  - Install JellyCon add-on, template connection settings (Jellyfin IP, credentials)
  - Configure ALSA HDMI audio output
  - Enable Kodi web interface on port 8080
  - Auto-start: systemd service for `kodi-standalone` on container boot
  - Template `advancedsettings.xml` for buffer/cache tuning
- [ ] Create `roles/kodi_configure/templates/advancedsettings.xml.j2`
- [ ] Create `roles/kodi_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `kodi` dynamic group,
  tagged `[media]`, `gather_facts: true`, after the provision play
- [ ] Ensure idempotency: all tasks safe to re-run

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
- Uninstall packages: `apt-get remove -y kodi-standalone kodi-gbm libcec6 ...`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Self-contained. Depends on M1 and M2._

Wire Kodi into `site.yml`, ensure dynamic group and inventory are
complete. Per-feature scenarios use `tasks/reconstruct_kodi_group.yml`.

See: `vm-lifecycle` skill (site.yml play order, deploy_stamp pairing).

**Implementation pattern:**
- site.yml: provision play (M1) + configure play (M2) already added
- `tasks/reconstruct_kodi_group.yml`: verify container running, `add_host` with
  `ansible_connection: community.proxmox.proxmox_pct_remote`, `proxmox_vmid: 301`

- [ ] Verify `kodi_lxc` provision play in `site.yml` targeting `media_nodes`
- [ ] Verify `kodi_configure` play targeting `kodi` dynamic group
- [ ] Create `tasks/reconstruct_kodi_group.yml`:
  - Verify container 301 is running (`pct status {{ kodi_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ kodi_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction (no SSH auth — pct_remote always)
- [ ] Ensure `kodi` dynamic group and `kodi_ct_id` in inventory/group_vars
- [ ] Update `build.py` docstring with `media` tag if applicable

**Verify:**

- [ ] `ansible-playbook site.yml --tags media` runs without error
- [ ] `reconstruct_kodi_group.yml` succeeds when container 301 is running
- [ ] Configure play targets `kodi` group after reconstruction

**Rollback:** N/A — integration only; revert via git.

---

### Milestone 4: Testing & Integration

_Self-contained. Depends on M1, M2, and M3._

Wire up molecule testing, extend verify.yml and cleanup. Container cleanup
is generic (`pct list` iteration). Host-side cleanup: none.

See: `ansible-testing` skill (per-feature scenario setup, verify
completeness, baseline workflow).

- [ ] Extend `molecule/default/verify.yml` with Kodi assertions:
  - Container 301 created and running
  - Kodi packages installed, DRI devices present, sound devices present
  - Kodi web interface on port 8080 (when running)
  - JellyCon add-on installed
  - Run assertions via `pct exec 301 --` from Proxmox host
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 301 (already iterates `pct list` — confirm)
- [ ] **Host-side cleanup:** None. Kodi does not deploy host files.
- [ ] Create `molecule/kodi-lxc/` per-feature scenario:
  - `molecule.yml`: same platform as default, `media_nodes` in groups
  - `converge.yml`: reconstruct kodi group, run kodi_configure
  - `verify.yml`: reconstruct kodi group, run Kodi assertions
  - `cleanup.yml`: destroy container 301 (`pct stop` + `pct destroy`)
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [ ] Run `molecule test` (full integration) — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `molecule test -s kodi-lxc` passes (per-feature scenario)
- [ ] Verify assertions cover: container state, device mounts, packages,
  JellyCon, web interface, deploy_stamp
- [ ] Cleanup leaves no Kodi artifacts on host (container cleanup only)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/kodi-build.md`:
  - Requirements, design decisions, env variables
  - GBM/DRM output, display-exclusive transitions (Kiosk hookscript)
  - iGPU shared bind mount, audio `/dev/snd/*` bind mount
  - Remote control: web interface, CEC
  - `onboot: false` — on-demand container
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Kodi provision + configure plays
  - Verify media topology includes Kodi container
- [ ] Update `docs/architecture/roles.md`:
  - Add `kodi_lxc` role documentation (purpose, key variables)
  - Add `kodi_configure` role documentation (purpose, JellyCon, ALSA)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Kodi project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] Display exclusivity and hookscript ownership (Kiosk project) documented

**Rollback:** N/A — documentation-only milestone.

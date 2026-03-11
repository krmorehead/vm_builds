# Custom UX Kiosk

## Overview

A lightweight LXC container that displays a full-screen dashboard on the
physical display when no other display service is active. It is the **default
idle state** of the home entertainment box — the "home screen" you see when
the device boots and nothing else is running.

Kiosk auto-starts on boot (priority 6) and restarts when other display
services (Kodi, Moonlight, Desktop VM) stop. It uses the shared iGPU via
`/dev/dri/*` for display output through DRM/KMS.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 2 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (display output via DRM/KMS, shared)
- VMID: 401

## Startup

- Auto-start: **yes** (default display state)
- Boot priority: 6 (last to start; all services it displays must be up first)
- Depends on: Home Assistant (for Lovelace dashboard), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes** (default state)
- Kiosk is the DEFAULT display state — it auto-starts on boot and restarts
  when other display services stop
- Start Kodi/Moonlight/Desktop VM → Kiosk stops (via hookscript)
- Stop Kodi/Moonlight/Desktop VM → Kiosk restarts (via hookscript)
- Start Desktop VM → Kiosk stops + loses iGPU (Desktop VM takes exclusive
  passthrough)

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive
  hookscript (M5 of this project)
- Home Assistant (project 07) — serves the dashboard content
- Physical display connected to host HDMI/DP
- **iGPU:** `proxmox_igpu` hard-fails if absent. Every modern Intel CPU has
  an iGPU. Uses `/dev/dri/*` for display output via DRM/KMS (shared).
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
├── Display server: Cage (single-application Wayland compositor)
│   └── Minimal Wayland compositor that runs one app fullscreen; no shell, no window decorations
│
├── Application: Chromium in kiosk mode
│   └── Renders HA Lovelace dashboard; --kiosk --no-sandbox --ozone-platform=wayland
│
├── Dashboard: Home Assistant Lovelace panel
│   └── Integrates with HA ecosystem; live updates; customizable cards
│
├── iGPU: shared bind mount via /dev/dri/*
│   └── Uses /dev/dri/* for display output via DRM/KMS (shared, NOT exclusive passthrough)
│   └── proxmox_igpu hard-fails if absent — required on every host
│
├── Default display state: Kiosk
│   └── Auto-starts on boot (priority 6); restarts when other display services stop
│   └── Kodi, Moonlight, Desktop VM are on-demand; Kiosk is the idle "home screen"
│
└── Auto-start trigger: Proxmox hookscript
    └── Deployed to /var/lib/vz/snippets/display-exclusive.sh
    └── Kiosk restarts automatically when other display services stop
    └── Display-exclusive VMIDs: 301 (Kodi), 302 (Moonlight), 400 (Desktop), 401 (Kiosk)
```

---

## Milestone Dependency Graph

```
M5: Display-Exclusive Orchestration ── CROSS-CUTTING: affects ALL display services
│   (301 Kodi, 302 Moonlight, 400 Desktop VM, 401 Kiosk)
│   Shared infrastructure; deploys hookscript; must run before M1 can attach
│
├── M1: LXC Provisioning ─────── self-contained, depends on M5 (hookscript)
│    └── M2: Configuration ───── self-contained, depends on M1
│         └── M3: Integration ─── self-contained, depends on M1+M2
│              └── M4: Testing ─── self-contained, depends on M1–M3
│                   └── M6: Docs ─ self-contained, depends on M1–M5
```

---

## Milestones

### Milestone 5: Display-Exclusive Orchestration

**CROSS-CUTTING.** Affects ALL display services (Kodi 301, Moonlight 302,
Desktop VM 400, Kiosk 401). Shared infrastructure concern, not Kiosk-specific.
Must run before M1 so the hookscript exists for attachment.

See: `proxmox-host-safety` skill (safe host commands, cleanup completeness).

**Implementation pattern:**
- Deploy hookscript to `/var/lib/vz/snippets/display-exclusive.sh`
- Hookscript logic: on pre-start of non-default display service, stop all
  other display services; on post-stop of non-default service, start Kiosk
- Attach via `pct set` / `qm set --hookscript` to display-exclusive
  containers/VMs
- Display-exclusive VMIDs: 301 (Kodi), 302 (Moonlight), 400 (Desktop VM),
  401 (Kiosk)
- Add hookscript removal to BOTH `molecule/default/cleanup.yml` AND
  `playbooks/cleanup.yml` (cleanup completeness rule)

- [ ] Create Proxmox hookscript (`/var/lib/vz/snippets/display-exclusive.sh`):
  - On pre-start: stop all other display services (301, 302, 400, 401)
  - On post-stop of non-default service: start Kiosk (401, default)
  - Display service VMIDs read from a config variable
- [ ] Deploy hookscript via Ansible task in the infrastructure play
- [ ] Attach hookscript to display-exclusive containers/VMs
  (`pct set` / `qm set --hookscript`)
- [ ] Add hookscript removal to `molecule/default/cleanup.yml`:
  - Remove `/var/lib/vz/snippets/display-exclusive.sh`
- [ ] Add hookscript removal to `playbooks/cleanup.yml`:
  - Remove `/var/lib/vz/snippets/display-exclusive.sh`
- [ ] Add Ansible pre-task in `site.yml` that enforces exclusion during deploys

**Verify:**

- [ ] Hookscript exists at `/var/lib/vz/snippets/display-exclusive.sh`
- [ ] Starting Kodi (301) stops Kiosk (401) automatically
- [ ] Stopping Kodi restarts Kiosk automatically
- [ ] Starting Desktop VM (400) stops all LXC display services (301, 302, 401)

**Rollback:**

- Remove hookscript from `/var/lib/vz/snippets/display-exclusive.sh`
- Detach hookscript from containers/VMs (`pct set --delete hookscript`,
  `qm set --delete hookscript`)
- Ensure BOTH cleanup playbooks remove the hookscript (cleanup completeness)

---

### Milestone 1: LXC Provisioning

_Self-contained. Depends on M5 (hookscript must exist at
`/var/lib/vz/snippets/display-exclusive.sh`)._

Create the `kiosk_lxc` role as a thin wrapper around `proxmox_lxc`, add the
provision play to `site.yml`, and verify the container runs. Uses
`/dev/dri/*` for display output. Attaches the display-exclusive hookscript.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/kiosk_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `desktop_nodes`, tagged `[kiosk]`
- deploy_stamp included as last role in the provision play
- Dynamic group `kiosk` populated by `proxmox_lxc` via `add_host`
- Hookscript: attach via `pct set {{ kiosk_ct_id }} --hookscript ...`

- [ ] Create `roles/kiosk_lxc/defaults/main.yml`:
  - `kiosk_ct_id: 401`, `kiosk_ct_memory: 512`, `kiosk_ct_cores: 1`
  - `kiosk_ct_disk: "2G"`
  - `kiosk_ct_onboot: true`, `kiosk_ct_startup_order: 6`
  - `kiosk_dashboard_url` (Home Assistant Lovelace URL; override in
    `group_vars/all.yml`)
- [ ] Create `roles/kiosk_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with device mounts: `/dev/dri/*`
  - cgroup allowlist: DRI (226:*)
  - Attach display-exclusive hookscript (`pct set --hookscript`)
- [ ] Create `roles/kiosk_lxc/meta/main.yml` with required metadata
- [ ] Register in `kiosk` dynamic group via `add_host` in `proxmox_lxc`

**Verify:**

- [ ] Container 401 is running: `pct status 401` returns `running`
- [ ] Container is in `kiosk` dynamic group
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 401` shows `onboot: 1`,
  `startup: order=6`
- [ ] DRI devices present: `pct exec 401 -- ls /dev/dri`
- [ ] Hookscript attached: `pct config 401` shows `hookscript` line
- [ ] deploy_stamp contains `kiosk_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: hookscript removal in M5 (BOTH playbooks).

---

### Milestone 2: Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with Cage, Chromium, Mesa Intel drivers, and
the systemd service. Uses `pct exec` via `community.proxmox.proxmox_pct_remote`.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/kiosk_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/`, `meta/main.yml`
- site.yml: configure play targeting `kiosk` dynamic group, tagged `[kiosk]`
- Connection: `ansible_connection: community.proxmox.proxmox_pct_remote`

- [ ] Create `roles/kiosk_configure/defaults/main.yml`:
  - `kiosk_dashboard_url` via `lookup('env', 'KIOSK_DASHBOARD_URL') |
    default('', true)` or from `group_vars/all.yml`
- [ ] Create `roles/kiosk_configure/tasks/main.yml`:
  - Install Cage, Chromium via `apt` (with retries/delay per skill)
  - Install Mesa Intel drivers
  - Create systemd service for Cage + Chromium:
    - `cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland
      {{ kiosk_dashboard_url }}`
  - Restart on failure, start on boot
  - Template `kiosk_dashboard_url` from role defaults
  - Configure log forwarding to rsyslog
- [ ] Create `roles/kiosk_configure/meta/main.yml` with required metadata

**Verify:**

- [ ] Cage and Chromium installed: `pct exec 401 -- which cage chromium`
- [ ] Systemd service enabled: `pct exec 401 -- systemctl is-enabled
  kiosk-dashboard`
- [ ] DRI devices present: `pct exec 401 -- ls /dev/dri`
- [ ] Dashboard URL configured in service unit
- [ ] Idempotent: second run does not change state

**Rollback:**

- Stop and disable service: `pct exec 401 -- systemctl disable --now
  kiosk-dashboard`
- Uninstall packages: `pct exec 401 -- apt-get remove -y cage chromium`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Self-contained. Depends on M1 and M2._

Wire up `site.yml` plays, add `kiosk_dashboard_url` to `group_vars/all.yml`,
and create `tasks/reconstruct_kiosk_group.yml` for per-feature scenarios.

See: `vm-lifecycle` skill (site.yml play structure, dynamic group
reconstruction).

**Implementation pattern:**
- site.yml: provision play targeting `desktop_nodes`, configure play targeting
  `kiosk` dynamic group
- `tasks/reconstruct_kiosk_group.yml`: verify container 401 running,
  `add_host` with `proxmox_pct_remote` connection
- `group_vars/all.yml`: add `kiosk_dashboard_url`

- [ ] Add `kiosk_lxc` to `site.yml` targeting `desktop_nodes` (combined with
  `desktop_vm`)
- [ ] Add `kiosk_configure` play targeting `kiosk` dynamic group
- [ ] Include `deploy_stamp`, add dynamic group + VMID to inventory/group_vars
- [ ] Add `kiosk_dashboard_url` to `group_vars/all.yml` (Home Assistant
  Lovelace URL)
- [ ] Create `tasks/reconstruct_kiosk_group.yml`:
  - Verify container 401 is running (`pct status {{ kiosk_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ kiosk_ct_id }}`,
    `ansible_user: root`
  - Consumed by per-feature molecule converge/verify/cleanup

**Verify:**

- [ ] Full `site.yml` run provisions and configures Kiosk
- [ ] `reconstruct_kiosk_group.yml` succeeds when container 401 is running
- [ ] `kiosk_dashboard_url` in `group_vars/all.yml` is used by configure role

**Rollback:** N/A — integration wiring; revert via git.

---

### Milestone 4: Testing

_Self-contained. Depends on M1, M2, and M3._

Extend molecule verify and cleanup. Create per-feature scenario if needed.
Ensure hookscript is cleaned in BOTH playbooks.

See: `ansible-testing` skill (verify completeness, per-feature scenario,
cleanup completeness).

**Implementation pattern:**
- `molecule/default/verify.yml`: add Kiosk assertions (run via `pct exec`)
- `molecule/default/cleanup.yml`: generic container cleanup handles 401;
  hookscript removal in M5
- `molecule/kiosk-lxc/` per-feature scenario (optional): converge, verify,
  cleanup with `reconstruct_kiosk_group.yml`

- [ ] Extend `molecule/default/verify.yml`:
  - Container 401 running
  - Cage and Chromium installed
  - Systemd service enabled, DRI devices present
  - Dashboard URL configured
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 401 (already iterates `pct list` — confirm)
- [ ] Ensure hookscript at `/var/lib/vz/snippets/display-exclusive.sh` is
  in removal list in BOTH `molecule/default/cleanup.yml` AND
  `playbooks/cleanup.yml` (cleanup completeness rule)
- [ ] Create `molecule/kiosk-lxc/` per-feature scenario (optional):
  - converge.yml: reconstruct kiosk group, run kiosk_configure
  - verify.yml: reconstruct kiosk group, run Kiosk assertions
  - cleanup.yml: destroy container 401, remove hookscript
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, DRI devices,
  service state, dashboard URL, deploy_stamp
- [ ] Cleanup leaves no Kiosk artifacts; hookscript removed from BOTH
  playbooks

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 6: Documentation

_Self-contained. Depends on M1–M5._

- [ ] Create `docs/architecture/kiosk-build.md`:
  - Cage + Chromium, display-exclusive default state, dashboard config
  - iGPU shared bind mount, `proxmox_igpu` hard-fail
  - Hookscript location, VMID list (301, 302, 400, 401)
  - `kiosk_dashboard_url` in `group_vars/all.yml`
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Kiosk provision + configure plays
  - Display-exclusive orchestration (M5)
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior

**Rollback:** N/A — documentation-only milestone.

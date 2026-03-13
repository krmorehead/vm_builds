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

- Home Entertainment Box: yes (`desktop_nodes`)
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

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`
- Home Assistant (project 07) — serves the dashboard content
- Physical display connected to host HDMI/DP
- **iGPU:** `proxmox_igpu` hard-fails if absent. Supports Intel and AMD.
- `kiosk_ct_id: 401` already in `group_vars/all.yml`
- `desktop_nodes` flavor group and `kiosk` dynamic group already in `inventory/hosts.yml`
- `desktop_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` exports `igpu_render_device`, `igpu_render_gid`, `igpu_vendor`
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`desktop_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Kiosk containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case — desktop/kiosk services only run on the Home
Entertainment Box profile, which always has OpenWrt. If `desktop_nodes` ever
includes a WAN-connected host, add the WireGuard-style topology branching
at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness, image management |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | iGPU hard-fail detection, safe host commands, shell pipefail, cleanup completeness |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Display server: Cage (single-application Wayland compositor)
│   └── Minimal Wayland compositor that runs one app fullscreen; no shell, no window decorations
│
├── LXC base: Custom Debian 12 template with Cage + Chromium + Mesa baked in
│   ├── "Bake, don't configure at runtime" — all packages baked into image
│   └── Configure role only applies host-specific settings (dashboard URL, systemd service)
│
├── Image build: Debian 12 standard + Cage + Chromium + Mesa in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs Cage, Chromium, Mesa Intel + AMD drivers
│   └── Pre-configures systemd service template for Cage + Chromium
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
├── LXC features: none required
│   └── Device passthrough handled by cgroup allowlist + bind mount, not LXC features
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

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Kiosk provisions on `desktop_nodes` (currently
`home` only). M5 (display-exclusive hookscript) runs in the infrastructure
phase. Kiosk and Desktop VM share the `[desktop]` tag.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/kiosk-lxc/` which only touches
VMID 401 and the hookscript. The OpenWrt baseline and other containers
stay running.

```
Scenario Hierarchy (Kiosk additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including hookscript deploy + Kiosk provision + configure
│
└── molecule/kiosk-lxc/              Kiosk container only (~30-60s)
    ├── converge: deploy hookscript + provision + configure Kiosk container
    ├── verify: Kiosk-specific assertions + hookscript existence
    └── cleanup: destroy container 401 + remove hookscript
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on Kiosk container (only touches VMID 401 + hookscript)
molecule converge -s kiosk-lxc                # ~30s, provision + configure
molecule verify -s kiosk-lxc                  # ~10s, assertions only
molecule converge -s kiosk-lxc                # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s kiosk-lxc                 # destroys container 401, removes hookscript

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `kiosk-lxc` | Container 401 + hookscript | Container 401 + hookscript | None — OpenWrt, WireGuard, etc. untouched |

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M5: Display-Exclusive Orchestration ── CROSS-CUTTING: affects ALL display services
      │   (301 Kodi, 302 Moonlight, 400 Desktop VM, 401 Kiosk)
      │   Shared infrastructure; deploys hookscript; must run before M1 can attach
      │
      └── M1: Provisioning ── depends on M0 + M5 (hookscript)
           └── M2: Configuration ── depends on M1
                └── M3: Testing & Integration ── depends on M1–M2, M5
                     └── M4: Documentation ── depends on M1–M3, M5
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with Cage, Chromium, and Mesa drivers
pre-installed. Per the project's "Bake, don't configure at runtime"
principle, all packages belong in the image. The configure role (M2) only
applies host-specific settings (dashboard URL, systemd service config).

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add Kiosk image build section to `build-images.sh`
- Template path: `images/kiosk-debian-12-amd64.tar.zst`
- Template vars: `kiosk_lxc_template` and `kiosk_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Install Cage, Chromium, Mesa Intel + AMD drivers
3. Pre-configure systemd service template for Cage + Chromium in kiosk mode
4. Clean apt caches, stop container
5. Export via `vzdump` and download template

- [ ] Add Kiosk template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_kiosk_lxc` function)
- [ ] Add `kiosk_lxc_template` and `kiosk_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/kiosk-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Cage and Chromium pre-installed
- [ ] Template contains Mesa drivers (Intel + AMD)
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

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

### Milestone 1: Provisioning

_Depends on M0 (template must be built) and M5 (hookscript must exist at
`/var/lib/vz/snippets/display-exclusive.sh`)._

Create the `kiosk_lxc` role as a thin wrapper around `proxmox_lxc`, add the
provision and configure plays to `site.yml`, and verify the container runs.
Uses `/dev/dri/*` for display output. Attaches the display-exclusive
hookscript. Integration with `site.yml` is consolidated here.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/kiosk_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `desktop_nodes`, tagged `[kiosk]`,
  in Phase 3
- deploy_stamp included as last role in the provision play
- Dynamic group `kiosk` populated by `proxmox_lxc` via `add_host`
- Hookscript: attach via `pct set {{ kiosk_ct_id }} --hookscript ...`

**Already complete** (from shared infrastructure / inventory):
- `kiosk_ct_id: 401` in `group_vars/all.yml`
- `desktop_nodes` flavor group and `kiosk` dynamic group in `inventory/hosts.yml`
- `desktop_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` role exports `igpu_*` facts (hard-fails if no iGPU)

- [ ] Create `roles/kiosk_lxc/defaults/main.yml`:
  - `kiosk_ct_hostname: kiosk`
  - `kiosk_ct_memory: 512`, `kiosk_ct_cores: 1`, `kiosk_ct_disk: "2"`
  - `kiosk_ct_template: "{{ kiosk_lxc_template }}"` (custom Kiosk image)
  - `kiosk_ct_template_path: "{{ kiosk_lxc_template_path }}"`
  - `kiosk_ct_onboot: true`, `kiosk_ct_startup_order: 6`
  - `kiosk_dashboard_url` (Home Assistant Lovelace URL; override in
    `group_vars/all.yml`)
  - No `lxc_ct_features` needed (device passthrough via cgroup allowlist)
- [ ] Create `roles/kiosk_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` with device mounts: `/dev/dri/*`
  - cgroup allowlist: DRI (226:*)
  - Attach display-exclusive hookscript (`pct set --hookscript`)
- [ ] Create `roles/kiosk_lxc/meta/main.yml` with required metadata
- [ ] Register in `kiosk` dynamic group via `add_host` in `proxmox_lxc`
- [ ] Add provision play to `site.yml` Phase 3, targeting `desktop_nodes`,
  tagged `[kiosk]`, with `kiosk_lxc` role and `deploy_stamp`
- [ ] Add configure play to `site.yml` Phase 3, targeting `kiosk` dynamic
  group, tagged `[kiosk]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_kiosk_group.yml`:
  - Verify container 401 is running (`pct status {{ kiosk_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ kiosk_ct_id }}`,
    `ansible_user: root`

**Note on `[desktop]` tag:** Kiosk uses `[kiosk]` tag, not `[desktop]`.
Desktop VM (project 11) uses `[desktop]`. Both target `desktop_nodes`
but have separate tags because the Desktop VM is significantly more
disruptive (exclusive iGPU passthrough).

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

_Depends on M1 (container must be running)._

Configure the running container with the dashboard URL and systemd service.
Cage, Chromium, and Mesa drivers are already baked into the image (M0).
This role only applies host-specific configuration.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/kiosk_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/`, `meta/main.yml`
- site.yml: configure play targeting `kiosk` dynamic group, tagged `[kiosk]`
- Connection: `ansible_connection: community.proxmox.proxmox_pct_remote`

- [ ] Create `roles/kiosk_configure/defaults/main.yml`:
  - `kiosk_dashboard_url` via `lookup('env', 'KIOSK_DASHBOARD_URL') |
    default('', true)` or from `group_vars/all.yml`
- [ ] Create `roles/kiosk_configure/tasks/main.yml` (via `pct_remote`):
  - Create systemd service for Cage + Chromium:
    - `cage -- chromium --kiosk --no-sandbox --ozone-platform=wayland
      {{ kiosk_dashboard_url }}`
  - Restart on failure, start on boot
  - Template `kiosk_dashboard_url` from role defaults
  - Configure log forwarding to rsyslog
- [ ] Create `roles/kiosk_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- Cage binary — baked
- Chromium binary — baked
- Mesa Intel + AMD drivers — baked
- Systemd service template skeleton — baked

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
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2, M5._

Create per-feature molecule scenario for fast Kiosk-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, and run final validation.

See: `ansible-testing` skill (verify completeness, per-feature scenario,
cleanup completeness), `rollback-patterns` skill (cleanup completeness).

#### 3a. Per-feature scenario: `molecule/kiosk-lxc/`

- [ ] Create `molecule/kiosk-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - desktop_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      KIOSK_DASHBOARD_URL: ${KIOSK_DASHBOARD_URL:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/kiosk-lxc/converge.yml`
- [ ] Create `molecule/kiosk-lxc/verify.yml`
- [ ] Create `molecule/kiosk-lxc/cleanup.yml`:
  Destroys container 401 and removes hookscript.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Kiosk assertions:
  - Container 401 running, onboot=1, startup order=6
  - Cage and Chromium installed, DRI devices present
  - Systemd service enabled, dashboard URL configured
  - Hookscript exists at `/var/lib/vz/snippets/display-exclusive.sh`
  - deploy_stamp contains `kiosk_lxc` entry

- [ ] Verify generic container cleanup handles VMID 401
- [ ] Ensure hookscript at `/var/lib/vz/snippets/display-exclusive.sh` is
  in removal list in BOTH `molecule/default/cleanup.yml` AND
  `playbooks/cleanup.yml` (cleanup completeness rule)

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `kiosk-rollback` play:
  ```yaml
  - name: Rollback Kiosk container
    hosts: desktop_nodes
    gather_facts: false
    tags: [kiosk-rollback, never]
    tasks:
      - name: Stop and destroy Kiosk container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ kiosk_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ kiosk_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true

      - name: Remove display-exclusive hookscript
        ansible.builtin.file:
          path: /var/lib/vz/snippets/display-exclusive.sh
          state: absent
  ```

#### 3d. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s kiosk-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Kiosk artifacts; hookscript removed from BOTH playbooks

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3, M5._

- [ ] Create `docs/architecture/kiosk-build.md`:
  - Image build process (build-images.sh section)
  - Cage + Chromium, display-exclusive default state, dashboard config
  - iGPU shared bind mount, `proxmox_igpu` hard-fail
  - Hookscript location, VMID list (301, 302, 400, 401)
  - `kiosk_dashboard_url` in `group_vars/all.yml`
  - Baked config vs runtime config split
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Kiosk provision + configure plays
  - Display-exclusive orchestration (M5)
- [ ] Update `docs/architecture/roles.md`:
  - Add `kiosk_lxc` role documentation (purpose, hookscript, key variables)
  - Add `kiosk_configure` role documentation (purpose, dashboard URL)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Kiosk project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] Display exclusivity, hookscript deployment, and VMID list documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Home Assistant**: The Kiosk displays a Home Assistant Lovelace dashboard.
  The dashboard URL is configured via `kiosk_dashboard_url` and points to
  the HA container's web UI.
- **Display exclusivity**: Kiosk owns the hookscript deployment. Kodi (301),
  Moonlight (302), and Desktop VM (400) attach to the same hookscript but
  do not deploy it.
- **Desktop VM impact**: When the Desktop VM starts, it takes exclusive
  iGPU access via vfio-pci. Kiosk cannot run without the iGPU. The
  hookscript stops Kiosk before the Desktop VM starts, and restarts it
  when the Desktop VM stops (and iGPU returns to i915/amdgpu).
- **rsyslog**: Kiosk logs can be forwarded to the rsyslog collector via
  syslog configuration in the configure role.

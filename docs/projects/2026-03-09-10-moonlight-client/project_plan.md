# Moonlight Client

## Overview

An LXC container running `moonlight-embedded` for game streaming from the
Gaming Rig (project 13). Uses the iGPU for hardware video decode (VA-API)
and renders to the physical display via DRM/KMS. USB input devices are
passed through for controller and keyboard support.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 2 GB
- Network: LAN bridge
- iGPU: `/dev/dri/*` bind mount (video decode + display output, shared)
- Input: `/dev/input/*` + `/dev/uinput` bind mount (USB HID for controllers)
- VMID: 302

## Startup

- Auto-start: **no** (`onboot: false` — on-demand container)
- Boot priority: N/A
- Depends on: Gaming Rig Sunshine server (external), `proxmox_igpu` role

## Build Profiles

- Home Entertainment Box: yes (`media_nodes`)
- Minimal Router: no
- Gaming Rig: no (this is the **client**; the rig is the server)

## Display Exclusivity

- **Display-exclusive: yes.** Moonlight takes over the display when started.
- Start Moonlight → Kiosk stops (hookscript)
- Stop Moonlight → Kiosk restarts (hookscript)
- Hookscript deployed by Kiosk project (`2026-03-09-12`). Moonlight role
  attaches to the same hookscript; it does not deploy it.

## Prerequisites

- Shared infrastructure: `proxmox_lxc`, `proxmox_igpu`, display-exclusive
  hookscript (project 00; hookscript implementation in Kiosk project 2026-03-09-12)
- Gaming Rig with Sunshine installed (project 13) — streaming server
- Physical display connected to host HDMI/DP
- USB controller connected to host
- `moonlight_ct_id: 302` already in `group_vars/all.yml`
- `media_nodes` flavor group and `moonlight` dynamic group already in `inventory/hosts.yml`
- `media_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- `proxmox_igpu` exports `igpu_render_device`, `igpu_render_gid`, `igpu_vendor` (hard-fails if absent)
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`media_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Moonlight containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case — media services only run on the Home
Entertainment Box profile, which always has OpenWrt. If `media_nodes` ever
includes a WAN-connected host, add the WireGuard-style topology branching
at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness, image management |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | iGPU hard-fail detection, safe host commands, shell pipefail |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Container type: LXC (not VM)
│   └── Lightweight; DRM/KMS display output works from unprivileged LXC with device passthrough
│
├── LXC base: Custom Debian 12 template with moonlight-embedded + VA-API baked in
│   ├── "Bake, don't configure at runtime" — all packages baked into image
│   └── Configure role only applies host-specific settings (Sunshine server IP, resolution, pairing)
│
├── Image build: Debian 12 standard + moonlight-embedded + VA-API in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs moonlight-embedded from official release
│   ├── Installs VA-API drivers (Intel + AMD for portability)
│   │   └── VA-API driver package name depends on Debian release. ALWAYS verify with
│   │       apt-cache search before adding. Previous bug: intel-media-va-driver-non-free
│   │       does not exist on newer Debian releases; correct package is intel-media-va-driver
│   └── Pre-configures systemd service template for moonlight-embedded
│
├── Streaming client: moonlight-embedded
│   └── Headless/framebuffer Moonlight; no X11/Wayland; minimal resource usage
│
├── Video decode: VA-API via iGPU (Intel or AMD)
│   ├── Uses /dev/dri/* for VA-API video decode + display output (shared bind mount)
│   ├── proxmox_igpu hard-fails if iGPU absent — REQUIRED on every host
│   └── Supports both Intel (i915) and AMD (amdgpu) via igpu_vendor fact
│
├── Input passthrough: USB HID via /dev/input/* + /dev/uinput bind mount
│   ├── Device nodes in cgroup allowlist: input (13:*), uinput (10:223)
│   └── Direct input events from USB controllers; udev rules for stable device names
│
├── LXC features: none required
│   └── Device passthrough handled by cgroup allowlist + bind mount, not LXC features
│
├── onboot: false — on-demand container
│   └── User starts for game streaming; not part of boot sequence
│
├── Gaming Rig Sunshine server: external dependency
│   └── Testing verifies config validity (vainfo, config file, pairing flow),
│       not actual streaming. No Sunshine server in molecule.
│
└── Container cleanup: generic (pct stop + pct destroy)
    └── Host-side cleanup: none. No host files deployed by this role.
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Moonlight provisions on `media_nodes` (currently
`home` only). It runs alongside Jellyfin and Kodi. All three share the
`[media]` tag and provision in the same play on `media_nodes`.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/moonlight-lxc/` which only touches
VMID 302. The OpenWrt baseline and other containers stay running.

```
Scenario Hierarchy (Moonlight additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Moonlight provision + configure
│
└── molecule/moonlight-lxc/          Moonlight container only (~30-60s)
    ├── converge: provision + configure Moonlight container
    ├── verify: Moonlight-specific assertions
    └── cleanup: destroy container 302 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on Moonlight container (only touches VMID 302)
molecule converge -s moonlight-lxc            # ~30s, provision + configure
molecule verify -s moonlight-lxc              # ~10s, assertions only
molecule converge -s moonlight-lxc            # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s moonlight-lxc             # destroys container 302 only

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `moonlight-lxc` | Container 302 | Container 302 only | None — OpenWrt, WireGuard, etc. untouched |

### Testing limitations

Testing verifies config validity (vainfo, config file, systemd service),
NOT actual streaming. No Sunshine server is available in molecule. Pairing
verification is skipped when `MOONLIGHT_PAIR_PIN` is empty.

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

Build a custom Debian 12 LXC template with moonlight-embedded and VA-API
drivers pre-installed. Per the project's "Bake, don't configure at runtime"
principle, all packages belong in the image. The configure role (M2) only
applies host-specific settings (Sunshine server IP, resolution, codec).

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add Moonlight image build section to `build-images.sh`
- Template path: `images/moonlight-debian-12-amd64.tar.zst`
- Template vars: `moonlight_lxc_template` and `moonlight_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Install moonlight-embedded from official release (deb package or build)
3. Install VA-API drivers — include BOTH Intel (`intel-media-va-driver`)
   and AMD (`mesa-va-drivers`) packages for build portability
4. Install `vainfo` for decode verification
5. Pre-configure systemd service template for `moonlight-embedded stream`
6. Clean apt caches, stop container
7. Export via `vzdump` and download template

**VA-API driver note:** Package names depend on Debian release. ALWAYS
verify with `apt-cache search` before adding to the build script.

- [ ] Add Moonlight template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_moonlight_lxc` function)
- [ ] Add `moonlight_lxc_template` and `moonlight_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/moonlight-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains moonlight-embedded pre-installed
- [ ] Template contains VA-API drivers (Intel + AMD)
- [ ] `vainfo` binary present in template
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built). Blocked on: infrastructure play
(proxmox_igpu) — hard-fails if no iGPU._

Create the `moonlight_lxc` role as a thin wrapper around `proxmox_lxc`,
add device bind mounts for iGPU and input, add the provision and configure
plays to `site.yml`, and verify the container runs. Integration with
`site.yml` is consolidated here.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).
See: `proxmox-host-safety` skill (iGPU hard-fail — `proxmox_igpu` runs
before this play and hard-fails if absent).

**Implementation pattern:**
- Role: `roles/moonlight_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[media]`,
  in Phase 3 (combined with Jellyfin and Kodi)
- deploy_stamp included as last role in the provision play
- Dynamic group `moonlight` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure):
- `moonlight_ct_id: 302` in `group_vars/all.yml`
- `media_nodes` flavor group and `moonlight` dynamic group in `inventory/hosts.yml`
- `media_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/moonlight_lxc/defaults/main.yml`:
  - `moonlight_ct_hostname: moonlight`
  - `moonlight_ct_memory: 512`, `moonlight_ct_cores: 1`, `moonlight_ct_disk: "2"`
  - `moonlight_ct_template: "{{ moonlight_lxc_template }}"` (custom Moonlight image)
  - `moonlight_ct_template_path: "{{ moonlight_lxc_template_path }}"`
  - `moonlight_ct_onboot: false` (on-demand, display-exclusive)
  - `moonlight_ct_mount_entries`: `/dev/dri`, `/dev/input`, `/dev/uinput`
  - No `lxc_ct_features` needed (device passthrough via cgroup allowlist)
  - cgroup allowlist: DRI (226:*), input (13:*), uinput (10:223)
- [ ] Create `roles/moonlight_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_dynamic_group`, memory, cores,
    disk, onboot, mount_entries, cgroup allowlist
  - Attach display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12)
- [ ] Create `roles/moonlight_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `media_nodes`,
  tagged `[media]`, with `moonlight_lxc` role and `deploy_stamp`
  (combined with Jellyfin and Kodi in same play)
- [ ] Add configure play to `site.yml` Phase 3, targeting `moonlight` dynamic
  group, tagged `[media]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_moonlight_group.yml`:
  - Verify container 302 is running (`pct status {{ moonlight_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ moonlight_ct_id }}`,
    `ansible_user: root`

**Note on `[media]` tag:** This tag is shared with Jellyfin and Kodi (per
the target site.yml architecture, all three provision in the same play on
`media_nodes`). Configure plays remain separate since they target different
dynamic groups.

**Verify:**

- [ ] Container 302 is running: `pct status 302` returns `running`
- [ ] Container is in `moonlight` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 302` shows `onboot: 0`
- [ ] Device bind mounts present: `pct config 302` shows mp0/mp1/mp2 for
  `/dev/dri`, `/dev/input`, `/dev/uinput`
- [ ] cgroup allowlist includes DRI, input, uinput
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `moonlight_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: **none** — this role deploys no
host files.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with host-specific streaming settings:
Sunshine server IP, resolution, codec, and pairing. moonlight-embedded
and VA-API drivers are already baked into the image (M0). This role
only applies host-specific configuration.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).
See: `proxmox-host-safety` skill (package name verification — ALWAYS
`apt-cache search` before adding VA-API driver).

**Implementation pattern:**
- Role: `roles/moonlight_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/moonlight.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `moonlight` dynamic group, tagged
  `[media]`, after the provision play

**Env variables** (optional — Sunshine server IP from group_vars or .env):

| Variable | Purpose | Example |
|----------|---------|---------|
| `MOONLIGHT_SERVER_IP` | Sunshine server hostname/IP | `192.168.1.50` |
| `MOONLIGHT_PAIR_PIN` | Pre-shared PIN for `moonlight pair` | `1234` |

- [ ] Create `roles/moonlight_configure/defaults/main.yml`:
  - `moonlight_server_ip` via `lookup('env', 'MOONLIGHT_SERVER_IP') | default('', true)`
  - `moonlight_pair_pin` via `lookup('env', 'MOONLIGHT_PAIR_PIN') | default('', true)`
  - `moonlight_resolution`, `moonlight_codec`, `moonlight_bitrate` defaults
- [ ] Create `roles/moonlight_configure/tasks/main.yml` (via `pct_remote`):
  - Verify hardware decode: `vainfo` shows H.265 decode profile
  - Template config: resolution (1080p), codec (H.265), bitrate, Sunshine server IP
  - Server pairing: automate `moonlight pair` via pre-shared PIN from `.env`
    (skip if PIN not set — manual pairing)
  - Enable systemd service for `moonlight-embedded stream` on container boot
- [ ] Create `roles/moonlight_configure/templates/moonlight.conf.j2`
- [ ] Create `roles/moonlight_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- moonlight-embedded binary — baked
- VA-API drivers (Intel + AMD) — baked
- `vainfo` binary — baked
- Systemd service template — baked

**Verify:**

- [ ] `moonlight-embedded` installed: `pct exec 302 -- which moonlight`
- [ ] VA-API decode available: `pct exec 302 -- vainfo` shows H.265 profile
- [ ] Config file exists with correct server IP
- [ ] Systemd service configured: `pct exec 302 -- systemctl is-enabled moonlight`
- [ ] Idempotent: second run does not re-pair or overwrite pairing state

**Rollback:**

- Stop and disable service: `pct exec 302 -- systemctl disable --now moonlight`
- Remove config: `pct exec 302 -- rm -f /etc/moonlight.conf`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for fast Moonlight-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, and run final validation.

See: `ansible-testing` skill (per-feature scenario setup, verify
completeness, baseline workflow), `rollback-patterns` skill (cleanup
completeness).

#### 3a. Per-feature scenario: `molecule/moonlight-lxc/`

- [ ] Create `molecule/moonlight-lxc/molecule.yml`:
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
      MOONLIGHT_SERVER_IP: ${MOONLIGHT_SERVER_IP:-}
      MOONLIGHT_PAIR_PIN: ${MOONLIGHT_PAIR_PIN:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/moonlight-lxc/converge.yml`:
  ```yaml
  - name: Provision Moonlight LXC container
    hosts: media_nodes
    gather_facts: false
    roles:
      - moonlight_lxc

  - name: Reconstruct moonlight dynamic group
    hosts: media_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_moonlight_group.yml

  - name: Configure Moonlight
    hosts: moonlight
    gather_facts: true
    roles:
      - moonlight_configure
  ```

- [ ] Create `molecule/moonlight-lxc/verify.yml`
- [ ] Create `molecule/moonlight-lxc/cleanup.yml`:
  Destroys only container 302.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Moonlight assertions:
  - Container 302 created: `pct status 302` returns `running`
  - moonlight-embedded installed, DRI devices present
  - VA-API decode available: `vainfo` shows H.265 profile
  - Config file templated with server IP
  - deploy_stamp contains `moonlight_lxc` entry

- [ ] Verify generic container cleanup handles VMID 302

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `moonlight-rollback` play:
  ```yaml
  - name: Rollback Moonlight container
    hosts: media_nodes
    gather_facts: false
    tags: [moonlight-rollback, never]
    tasks:
      - name: Stop and destroy Moonlight container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ moonlight_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ moonlight_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Molecule env passthrough

- [ ] Add `MOONLIGHT_SERVER_IP` and `MOONLIGHT_PAIR_PIN` to
  `molecule/default/molecule.yml` `provisioner.env` (optional, empty)

#### 3e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s moonlight-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Moonlight artifacts on host or controller

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/moonlight-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - DRM/KMS output, VA-API decode, pairing flow
  - Display exclusivity and hookscript behavior
  - VA-API driver package name caveat (Debian release-dependent)
  - Baked config vs runtime config split
  - Test vs production workflow (config validity, no Sunshine in molecule)
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Moonlight provision + configure plays
  - Verify media topology includes Moonlight container
- [ ] Update `docs/architecture/roles.md`:
  - Add `moonlight_lxc` role documentation (purpose, device mounts, key variables)
  - Add `moonlight_configure` role documentation (purpose, env vars, VA-API, pairing)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Moonlight project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] VA-API package name caveat documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Gaming Rig**: Moonlight streams from the Gaming Rig's Sunshine server
  (project 13). Pairing requires both services operational. The Gaming Rig
  is on separate hardware — Moonlight connects over the LAN.
- **Display exclusivity**: Moonlight shares the display-exclusive hookscript
  with Kodi (301), Desktop VM (400), and Kiosk (401). The hookscript is
  deployed by the Kiosk project (2026-03-09-12); Moonlight only attaches.
- **Kiosk**: When Moonlight stops, Kiosk auto-restarts as the default
  display state.
- **rsyslog**: Moonlight logs can be forwarded to the rsyslog collector
  via standard syslog configuration.

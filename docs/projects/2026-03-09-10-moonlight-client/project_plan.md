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

- Home Entertainment Box: yes
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
├── Container type: LXC (not VM)
│   └── Lightweight; DRM/KMS display output works from unprivileged LXC with device passthrough
│
├── Streaming client: moonlight-embedded
│   └── Headless/framebuffer Moonlight; no X11/Wayland; minimal resource usage
│
├── Video decode: VA-API via Intel iGPU
│   ├── Uses /dev/dri/* for VA-API video decode + display output (shared bind mount)
│   ├── proxmox_igpu hard-fails if iGPU absent — REQUIRED on every host
│   └── VA-API driver package name depends on Debian release. ALWAYS verify with
│       apt-cache search before adding. Previous bug: intel-media-va-driver-non-free
│       does not exist on newer Debian releases; correct package may be
│       intel-media-va-driver
│
├── Input passthrough: USB HID via /dev/input/* + /dev/uinput bind mount
│   ├── Device nodes in cgroup allowlist: input (13:*), uinput (10:223)
│   └── Direct input events from USB controllers; udev rules for stable device names
│
├── onboot: false — on-demand container
│   └── User starts for game streaming; not part of boot sequence
│
├── Configuration method: pct exec from Proxmox host
│   ├── community.proxmox.proxmox_pct_remote connection plugin
│   └── Verify via pct exec; no SSH needed inside container
│
├── Gaming Rig Sunshine server: external dependency
│   └── Testing verifies config validity (vainfo, config file, pairing flow),
│       not actual streaming. No Sunshine server in molecule.
│
└── Container cleanup: generic (pct stop + pct destroy)
    └── Host-side cleanup: none. No host files deployed by this role.
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ─────── self-contained
 └── M2: Moonlight Config ─── self-contained, depends on M1
      └── M3: Integration ─── self-contained, depends on M1+M2
           └── M4: Testing ─── self-contained, depends on M1–M3
                └── M5: Docs ─ self-contained, depends on M1–M4
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `moonlight_lxc` role as a thin wrapper around `proxmox_lxc`,
add device bind mounts for iGPU and input, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).
See: `proxmox-host-safety` skill (iGPU hard-fail — `proxmox_igpu` runs
before this play and hard-fails if absent).

**Implementation pattern:**
- Role: `roles/moonlight_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[moonlight]`
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
  - `moonlight_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `moonlight_ct_onboot: false` (on-demand, display-exclusive)
  - `moonlight_ct_mount_entries`: `/dev/dri`, `/dev/input`, `/dev/uinput`
  - `moonlight_ct_features`: cgroup allowlist for DRI (226:*), input (13:*), uinput (10:223)
- [ ] Create `roles/moonlight_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ moonlight_ct_id }}"`, `lxc_ct_hostname: moonlight`,
    `lxc_ct_dynamic_group: moonlight`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_mount_entries`, `lxc_ct_features`
  - Attach display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12)
- [ ] Create `roles/moonlight_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` targeting `media_nodes`, tagged
  `[moonlight]`, with `moonlight_lxc` role and `deploy_stamp`
- [ ] Verify Debian 12 LXC template exists in `images/` directory

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

### Milestone 2: Moonlight Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with moonlight-embedded, VA-API drivers,
config template, and server pairing. Configure plays target `moonlight`
dynamic group via `pct_remote`.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).
See: `proxmox-host-safety` skill (package name verification — ALWAYS
`apt-cache search` before adding VA-API driver).

**Implementation pattern:**
- Role: `roles/moonlight_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/moonlight.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `moonlight` dynamic group, tagged
  `[moonlight]`, after the provision play

**Env variables** (optional — Sunshine server IP from group_vars or .env):

| Variable | Purpose | Example |
|----------|---------|---------|
| `MOONLIGHT_SERVER_IP` | Sunshine server hostname/IP | `192.168.1.50` |
| `MOONLIGHT_PAIR_PIN` | Pre-shared PIN for `moonlight pair` | `1234` |

- [ ] Create `roles/moonlight_configure/defaults/main.yml`:
  - `moonlight_server_ip` via `lookup('env', 'MOONLIGHT_SERVER_IP') | default('', true)`
  - `moonlight_pair_pin` via `lookup('env', 'MOONLIGHT_PAIR_PIN') | default('', true)`
  - `moonlight_resolution`, `moonlight_codec`, `moonlight_bitrate` defaults
- [ ] Create `roles/moonlight_configure/tasks/main.yml`:
  - Install `moonlight-embedded` from official release or build from source
  - **VA-API driver**: ALWAYS verify package name with `apt-cache search intel-media`
    before adding. Use `intel-media-va-driver` or `intel-media-va-driver-non-free`
    depending on Debian release. Previous bug: `intel-media-va-driver-non-free`
    does not exist on newer Debian; correct package is `intel-media-va-driver`
  - Verify hardware decode: `vainfo` shows H.265 decode profile
  - Template config: resolution (1080p), codec (H.265), bitrate, Sunshine server IP
  - Server pairing: automate `moonlight pair` via pre-shared PIN from `.env`
    (skip if PIN not set — manual pairing)
  - Create systemd service for `moonlight-embedded stream` on container boot
- [ ] Create `roles/moonlight_configure/templates/moonlight.conf.j2`
- [ ] Create `roles/moonlight_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `moonlight` dynamic group,
  tagged `[moonlight]`, `gather_facts: true`, after the provision play

**Verify:**

- [ ] `moonlight-embedded` installed: `pct exec 302 -- which moonlight`
- [ ] VA-API decode available: `pct exec 302 -- vainfo` shows H.265 profile
- [ ] Config file exists with correct server IP
- [ ] Systemd service configured: `pct exec 302 -- systemctl is-enabled moonlight`
- [ ] Idempotent: second run does not re-pair or overwrite pairing state

**Rollback:**

- Stop and disable service: `pct exec 302 -- systemctl disable --now moonlight`
- Remove config: `pct exec 302 -- rm -f /etc/moonlight.conf`
- Uninstall packages: `pct exec 302 -- apt-get remove -y moonlight-embedded intel-media-va-driver`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Self-contained. Depends on M1 and M2._

Wire up site.yml plays, add Sunshine server IP to group_vars, ensure
dynamic group and VMID are in inventory. Create `tasks/reconstruct_moonlight_group.yml`
for per-feature scenarios and cleanup/rollback.

See: `vm-lifecycle` skill (dynamic group reconstruction, add_host ephemeral).
See: `project-planning` skill (implementation pattern, deploy_stamp pairing).

**Implementation pattern:**
- site.yml: provision play (M1) + configure play (M2) already added
- `tasks/reconstruct_moonlight_group.yml`: verify container 302 running,
  `add_host` with `ansible_connection: community.proxmox.proxmox_pct_remote`,
  `ansible_host: {{ ansible_host }}`, `proxmox_vmid: {{ moonlight_ct_id }}`,
  `ansible_user: root`

- [ ] Add `moonlight_lxc` provision play to `site.yml` targeting `media_nodes`
  (combined with jellyfin + kodi)
- [ ] Add `moonlight_configure` play targeting `moonlight` dynamic group
- [ ] Include `deploy_stamp` as last role in provision play
- [ ] Add Sunshine server IP to `group_vars/all.yml` (or host_vars)
- [ ] Create `tasks/reconstruct_moonlight_group.yml`:
  - Verify container 302 is running: `pct status {{ moonlight_ct_id }}`
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ moonlight_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction — pct_remote is always the connection method

**Verify:**

- [ ] Full `molecule converge` runs provision + configure without error
- [ ] `reconstruct_moonlight_group.yml` successfully restores dynamic group
  when run as separate playbook invocation

**Rollback:** N/A — integration wiring only; revert via git.

---

### Milestone 4: Testing

_Self-contained. Depends on M1, M2, M3._

Wire up molecule testing, create the per-feature scenario, and verify
end-to-end. Testing verifies config validity (vainfo, config file, pairing
flow), not actual streaming. No Sunshine server in molecule.

See: `ansible-testing` skill (per-feature scenario setup, verify completeness,
baseline workflow).

- [ ] Extend `molecule/default/verify.yml` with Moonlight assertions:
  - Container 302 created: `pct status 302` returns `running`
  - moonlight-embedded installed: `pct exec 302 -- which moonlight`
  - DRI devices present: `pct exec 302 -- ls /dev/dri`
  - VA-API decode available: `pct exec 302 -- vainfo` shows H.265 profile
  - Config file templated with server IP
  - All assertions run from Proxmox host via `pct exec 302 --`
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 302 (already iterates `pct list` — confirm)
- [ ] Host-side cleanup: **none** — this role deploys no host files
- [ ] Create `molecule/moonlight-lxc/` per-feature scenario:
  - `molecule.yml`: same platform as default, `media_nodes` in groups
  - `converge.yml`: reconstruct moonlight group, run moonlight_configure
  - `verify.yml`: reconstruct moonlight group, run Moonlight assertions
  - `cleanup.yml`: destroy container 302 (`pct stop` + `pct destroy`)
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [ ] Update `build.py` docstring with `moonlight` tag
- [ ] Run `molecule test` (full integration) — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `molecule test -s moonlight-lxc` passes (per-feature scenario)
- [ ] Verify assertions cover all categories: container state, auto-start,
  device mounts, VA-API, config file, systemd service
- [ ] Cleanup leaves no Moonlight artifacts on host (none expected)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/moonlight-build.md`:
  - Requirements, design decisions, env variables
  - DRM/KMS output, VA-API decode, pairing flow
  - Display exclusivity and hookscript behavior
  - VA-API driver package name caveat (Debian release–dependent)
  - Test vs production workflow (config validity, no Sunshine in molecule)
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Moonlight provision + configure plays
  - Verify media topology includes Moonlight container
- [ ] Update `docs/architecture/roles.md`:
  - Add `moonlight_lxc` role documentation (purpose, key variables)
  - Add `moonlight_configure` role documentation (purpose, env vars,
    VA-API, pairing)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Moonlight project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] VA-API package name caveat documented

**Rollback:** N/A — documentation-only milestone.

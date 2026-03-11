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

- Home Entertainment Box: yes
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

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | iGPU hard-fail detection, safe host commands, shell pipefail |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

## iGPU Hard-Fail Requirement

**CRITICAL:** `proxmox_igpu` hard-fails if no Intel iGPU is found. Jellyfin depends on
iGPU facts (`igpu_render_device`, `igpu_render_gid`) from the infrastructure play.
If `proxmox_igpu` fails, Jellyfin provisioning must never run. This is enforced by
play ordering in `site.yml`: the infrastructure play (Play 1: `proxmox_igpu`) runs
before `media_nodes` provision plays. Media plays only execute when infrastructure
succeeds.

## Env Variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `JELLYFIN_ADMIN_PASSWORD` | Production: yes | Admin user password | Auto-generated for testing when empty |
| `jellyfin_media_path` | — | Host-side media mount path | `group_vars/all.yml` (e.g., `/mnt/media`) |

## iGPU Device Mount

- Device: `/dev/dri/renderD128` (path from `igpu_render_device` fact)
- Cgroup allowlist: `c 226:128 rwm` (major:minor for renderD128)
- GID mapping: `igpu_render_gid` from `proxmox_igpu` facts — create `render` group
  inside container with matching GID, add `jellyfin` user to group
- Unprivileged container: device bind mount via `lxc_ct_mount_entries`, cgroup
  allowlist in container config

---

## Architectural Decisions

```
Decisions
├── Media server: Jellyfin
│   └── FOSS, no license, good VA-API support, active development
│
├── Container privileges: unprivileged with device passthrough
│   └── More secure; /dev/dri/renderD128 via cgroup allowlist + GID mapping
│
├── iGPU access: device bind mount (shared) via proxmox_igpu facts
│   └── NOT full PCI passthrough; iGPU stays on host i915 driver; multiple containers share
│
└── Media storage: NFS mount from home server / NAS
    └── Large libraries don't fit on local disk; NFS is transparent to Jellyfin
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ─────── blocked on: proxmox_igpu (infra play)
 └── M2: Jellyfin Config ── depends on M1
      └── M3: Integration ─ depends on M1+M2
           └── M4: Testing ─ depends on M1–M3
                └── M5: Docs ─ depends on M1–M4
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Blocked on: infrastructure play (proxmox_igpu) — Play 1 in site.yml runs first.
If proxmox_igpu hard-fails (no iGPU), this play never runs._

Create the `jellyfin_lxc` role as a thin wrapper around `proxmox_lxc`, add the
provision play to `site.yml`, and verify the container runs with iGPU device
mount and media bind mount.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp, device mounts).

**Implementation pattern:**
- Role: `roles/jellyfin_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `media_nodes`, tagged `[media]` or `[jellyfin]`
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

- [ ] Add `jellyfin_media_path: /mnt/media` to `group_vars/all.yml`
- [ ] Create `roles/jellyfin_lxc/defaults/main.yml`:
  - `jellyfin_ct_hostname: jellyfin`
  - `jellyfin_ct_memory: 2048`, `jellyfin_ct_cores: 2`, `jellyfin_ct_disk: "8"`
  - `jellyfin_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `jellyfin_ct_onboot: true`, `jellyfin_ct_startup_order: 5`
  - `jellyfin_ct_ip` (static, e.g., from host_vars or computed)
- [ ] Create `roles/jellyfin_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with:
    - `lxc_ct_mount_entries`: iGPU device (`{{ igpu_render_device }},mp={{ igpu_render_device }}`),
      media bind mount (`{{ jellyfin_media_path }},mp=/media`)
    - `lxc_ct_features` or raw config for cgroup allowlist `c 226:128 rwm`
    - `lxc_ct_id: "{{ jellyfin_ct_id }}"`, `lxc_ct_hostname: jellyfin`,
      `lxc_ct_dynamic_group: jellyfin`, `lxc_ct_memory`, `lxc_ct_cores`,
      `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`
- [ ] Create `roles/jellyfin_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` targeting `media_nodes`, tagged
  `[media]` or `[jellyfin]`, with `jellyfin_lxc` role and `deploy_stamp`
  (after infrastructure play, after OpenWrt configure)

**Verify:**

- [ ] Container 300 is running: `pct status 300` returns `running`
- [ ] Container is in `jellyfin` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 300` shows `onboot: 1`, `startup: order=5`
- [ ] iGPU device mounted: `pct exec 300 -- ls -la /dev/dri/renderD128` succeeds
- [ ] Media path mounted: `pct exec 300 -- ls /media` succeeds (or path exists)
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `jellyfin_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: **none** — Jellyfin does not deploy
host-side files (no kernel modules, no host config). Container cleanup is generic.

---

### Milestone 2: Jellyfin Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with Jellyfin from official Debian repo,
VA-API transcoding, admin user, and log forwarding.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/jellyfin_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/` (if needed), `meta/main.yml`
- site.yml: configure play targeting `jellyfin` dynamic group, tagged
  `[media]` or `[jellyfin]`, after the provision play
- Connection: `community.proxmox.proxmox_pct_remote` (pct exec from Proxmox host)

- [ ] Create `roles/jellyfin_configure/defaults/main.yml`:
  - `JELLYFIN_ADMIN_PASSWORD` via `lookup('env', 'JELLYFIN_ADMIN_PASSWORD') | default('', true)`
  - Auto-generate password when empty (testing)
- [ ] Create `roles/jellyfin_configure/tasks/main.yml`:
  - Install Jellyfin from official Debian repo (add GPG key + apt source)
  - Configure iGPU: create `render` group with GID from `igpu_render_gid`,
    add `jellyfin` user to group, verify `vainfo` succeeds
  - Template server config: VA-API transcode, media paths (`/media`),
    web on port 8096
  - Set admin user from env (`JELLYFIN_ADMIN_PASSWORD`) or generated value
  - Configure log forwarding to rsyslog (if rsyslog project complete)
- [ ] Create `roles/jellyfin_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `jellyfin` dynamic group,
  tagged `[media]` or `[jellyfin]`, `gather_facts: true`, after the provision play

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
- Remove Jellyfin packages and config: `apt-get remove -y jellyfin jellyfin-server`
- Remove `/etc/jellyfin` if present
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Self-contained. Depends on M1 and M2._

Wire up site.yml plays, ensure dynamic group reconstruction for per-feature
scenarios, and add `jellyfin_media_path` to group_vars.

See: `vm-lifecycle` skill (site.yml play structure, deploy_stamp pairing).

- [ ] Add `jellyfin_lxc` provision play to `site.yml` targeting `media_nodes`
  (combined with kodi + moonlight when those exist)
- [ ] Add `jellyfin_configure` play targeting `jellyfin` dynamic group
- [ ] Include `deploy_stamp` in provision play
- [ ] Ensure `jellyfin_media_path` in `group_vars/all.yml`
- [ ] Create `tasks/reconstruct_jellyfin_group.yml`:
  - Verify container 300 is running (`pct status {{ jellyfin_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ jellyfin_ct_id }}`,
    `ansible_user: root`
  - Required for per-feature molecule scenarios and any standalone
    ansible-playbook invocation (converge, verify, cleanup)

**Verify:**

- [ ] Full `ansible-playbook playbooks/site.yml --tags jellyfin` runs end-to-end
- [ ] Dynamic group `jellyfin` populated after provision
- [ ] `reconstruct_jellyfin_group.yml` successfully re-registers container

**Rollback:** N/A — integration wiring; revert via git.

---

### Milestone 4: Testing

_Self-contained. Depends on M1, M2, M3._

Extend molecule verify and cleanup. Container cleanup is generic; host-side
cleanup: none.

See: `ansible-testing` skill (verify completeness, per-feature scenario setup).

- [ ] Extend `molecule/default/verify.yml`:
  - Container 300 running, Jellyfin active, web on port 8096
  - `/dev/dri/renderD128` exists, `vainfo` succeeds
  - Media path mounted
  - Auto-start configured
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 300 (already iterates `pct list` — confirm)
- [ ] Host-side cleanup: **none** — Jellyfin does not deploy host files
- [ ] Create `molecule/jellyfin-lxc/` per-feature scenario (optional):
  - `molecule.yml`: same platform as default, `media_nodes` in groups
  - `converge.yml`: reconstruct jellyfin group, run jellyfin_configure
  - `verify.yml`: reconstruct jellyfin group, run Jellyfin assertions
  - `cleanup.yml`: destroy container 300 (generic LXC cleanup)
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, iGPU device,
  vainfo, media path, Jellyfin service, web port
- [ ] Cleanup leaves no Jellyfin artifacts on host (none expected)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/jellyfin-build.md`:
  - Requirements, design decisions, env variables
  - iGPU shared mount, cgroup allowlist `c 226:128 rwm`, GID mapping
  - Media storage, software fallback when iGPU unavailable
  - Test vs production workflow (JELLYFIN_ADMIN_PASSWORD)
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Jellyfin provision + configure plays
  - Role catalog: jellyfin_lxc, jellyfin_configure
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Jellyfin project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] iGPU hard-fail requirement documented
- [ ] All env variables documented

**Rollback:** N/A — documentation-only milestone.

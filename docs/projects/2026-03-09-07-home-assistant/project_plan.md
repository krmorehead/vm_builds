# Home Assistant

## Overview

An LXC container running Home Assistant Core via Docker for home automation.
Manages smart devices, automations, and dashboards. Accessible locally and
remotely via the WireGuard tunnel.

## Type

LXC container (with Docker inside via nesting)

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 8 GB (database, integrations, backups)
- Network: LAN bridge, static IP
- VMID: 200

## Startup

- Auto-start: yes
- Boot priority: 5 (after network + observability)
- Depends on: OpenWrt Router, Pi-hole (DNS)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) — for remote access

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | Safe host commands, shell pipefail requirements |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Installation method: HA Container (Docker in LXC)
│   └── Lightweight; full HA Core with all integrations. Supervised is fragile in LXC. HAOS as VM wastes resources.
│
├── Docker-in-LXC: nesting enabled (features: nesting=1), cgroup delegation
│   └── Pass `lxc_ct_features: ["nesting=1"]` to `proxmox_lxc`. Well-tested on Proxmox; required for Docker daemon inside unprivileged LXC.
│   └── Configure cgroup delegation for Docker (cgroup v2: /sys/fs/cgroup delegation; unprivileged containers need key=value in lxc.cgroup2)
│
├── USB passthrough: OPTIONAL — device bind mount via lxc_ct_mount_entries
│   └── For Zigbee/Z-Wave dongles; udev rules on host ensure stable /dev/ttyUSB* naming
│   └── `homeassistant_usb_devices: []` — empty list means no USB passthrough. Plan MUST handle empty list (skip mount entries entirely)
│
└── Backup strategy: HA native snapshots + container-level vzdump
    └── Defense in depth: HA handles config, vzdump handles whole container
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ──── self-contained
 └── M2: Configuration ─── depends on M1
      └── M3: Integration ─ depends on M1+M2
           └── M4: Testing ─ depends on M1–M3
                └── M5: Documentation ─ depends on M1–M4
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `homeassistant_lxc` role as a thin wrapper around `proxmox_lxc`
with nesting enabled for Docker. Add the provision play to `site.yml`, and
verify the container runs. USB passthrough is optional — when
`homeassistant_usb_devices` is empty, skip device mounts entirely.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/homeassistant_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `service_nodes`, tagged `[homeassistant]`
- deploy_stamp included as last role in the provision play
- Dynamic group `homeassistant` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure project 00):
- `homeassistant_ct_id: 200` in `group_vars/all.yml`
- `service_nodes` flavor group and `homeassistant` dynamic group in `inventory/hosts.yml`
- `service_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/homeassistant_lxc/defaults/main.yml`:
  - `homeassistant_ct_id: 200`, `homeassistant_ct_memory: 1024`, `homeassistant_ct_cores: 2`
  - `homeassistant_ct_disk: 8G`, `homeassistant_ct_ip` (static, from group_vars)
  - `homeassistant_ct_features: ["nesting=1"]` — required for Docker in LXC
  - `homeassistant_ct_onboot: true`, `homeassistant_ct_startup_order: 5`
  - `homeassistant_usb_devices: []` — list of /dev/ttyUSB* for Zigbee/Z-Wave; empty = no USB passthrough
- [ ] Create `roles/homeassistant_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with `lxc_ct_features: "{{ homeassistant_ct_features }}"` (nesting=1)
  - Pass `lxc_ct_mount_entries` built from `homeassistant_usb_devices` only when non-empty
  - Configure cgroup delegation for Docker daemon (cgroup v2 key=value in container config)
- [ ] Create `roles/homeassistant_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` targeting `service_nodes`, tagged
  `[homeassistant]`, with `homeassistant_lxc` role and `deploy_stamp`

**Verify:**

- [ ] Container 200 is running: `pct status 200` returns `running`
- [ ] Container is in `homeassistant` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Nesting enabled: `pct config 200` shows `features: nesting=1`
- [ ] Auto-start configured: `pct config 200` shows `onboot: 1`,
  `startup: order=5`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `homeassistant_lxc` play entry
- [ ] Empty `homeassistant_usb_devices` does not add any lxc.mount.entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: none — this milestone deploys no
host-side files.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with Docker, Home Assistant via Docker
Compose, and log forwarding. Uses `pct exec` from the Proxmox host —
no SSH server needed inside the container.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/homeassistant_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/` (compose, configuration.yaml), `meta/main.yml`
- site.yml: configure play targeting `homeassistant` dynamic group, tagged
  `[homeassistant]`, after the provision play

**Env variables:**

| Variable | Required (production) | Auto-generated (testing) | Purpose |
|----------|------------------------|---------------------------|---------|
| `HA_ADMIN_PASSWORD` | yes | yes (random) | Admin credentials for HA web UI |

Resolved via `lookup('env', 'HA_ADMIN_PASSWORD') | default('', true)` in role
defaults. When empty (e.g., test.env), role auto-generates a random password.
Add to `test.env` (empty for auto-gen) and `.env` template (required for
production). NEVER add to `REQUIRED_ENV` in `build.py` — Home Assistant is
flavor-specific (`service_nodes`).

- [ ] Create `roles/homeassistant_configure/defaults/main.yml`:
  - `ha_admin_password` via `lookup('env', 'HA_ADMIN_PASSWORD') | default('', true)`
  - HA image: `homeassistant/home-assistant:stable`
  - Config path: `/opt/homeassistant/config`
- [ ] Create `roles/homeassistant_configure/tasks/main.yml` (via `pct exec`):
  - Install Docker (`docker-ce` from official repo)
  - Create Docker compose for HA:
    - Image: `homeassistant/home-assistant:stable`
    - Volume: `/opt/homeassistant/config:/config`
    - Network: host mode (mDNS/SSDP discovery)
    - Restart: always
    - Device mounts for USB dongles only when `homeassistant_usb_devices` non-empty
  - Template `configuration.yaml`: HTTP, recorder (SQLite, 10-day retention), logger
  - Start compose, set admin credentials from env or auto-generated value
  - Configure log forwarding to rsyslog (if rsyslog project deployed)
- [ ] Create `roles/homeassistant_configure/templates/` (compose, configuration.yaml)
- [ ] Create `roles/homeassistant_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `homeassistant` dynamic group,
  tagged `[homeassistant]`, `gather_facts: true`, after the provision play

**Verify:**

- [ ] Docker daemon running: `pct exec 200 -- systemctl is-active docker`
- [ ] HA container running: `pct exec 200 -- docker ps` shows home-assistant
- [ ] Web UI on port 8123: `pct exec 200 -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8123` returns 200 or 301
- [ ] API returns valid response: `pct exec 200 -- curl -s http://127.0.0.1:8123/api/` returns JSON
- [ ] configuration.yaml present with expected structure
- [ ] Idempotent: second run does not recreate containers unnecessarily

**Rollback:**

- Stop and remove HA container: `pct exec 200 -- docker compose down`
- Remove config directory: `pct exec 200 -- rm -rf /opt/homeassistant`
- Uninstall Docker: `pct exec 200 -- apt-get remove -y docker-ce`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Depends on M1 and M2._

Wire up site.yml plays, inventory, env vars, and dynamic group reconstruction
for per-feature molecule scenarios and cleanup/rollback entry points.

See: `vm-lifecycle` skill (site.yml play order), `rollback-patterns` skill
(cleanup completeness).

**Implementation pattern:**
- site.yml: provision play (M1) + configure play (M2) already added in M1/M2
- Ensure play order: OpenWrt configure → Home Assistant provision → Home Assistant configure
- Add `tasks/reconstruct_homeassistant_group.yml` for per-feature converge/verify/cleanup

- [ ] Add `homeassistant_lxc` provision play to `site.yml` targeting `service_nodes`
  (if not already in M1)
- [ ] Add `homeassistant_configure` play targeting `homeassistant` dynamic group
  (if not already in M2)
- [ ] Include `deploy_stamp` in the provision play
- [ ] Add `homeassistant` dynamic group to `inventory/hosts.yml` (already present)
- [ ] Add `HA_ADMIN_PASSWORD` to `test.env` (empty for auto-gen) and `.env` template
- [ ] Order play AFTER OpenWrt configure and AFTER Pi-hole (DNS dependency)
- [ ] Create `tasks/reconstruct_homeassistant_group.yml`:
  - Verify container 200 is running (`pct status {{ homeassistant_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ homeassistant_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction — pct_remote is always the connection method

**Verify:**

- [ ] Full `molecule converge` runs without errors
- [ ] Home Assistant provision and configure plays execute in correct order
- [ ] `reconstruct_homeassistant_group.yml` successfully registers `homeassistant` host
  when run standalone (e.g., from per-feature converge)

**Rollback:** N/A — integration wiring; revert via git.

---

### Milestone 4: Testing

_Depends on M1–M3._

Extend molecule verify and cleanup. Per-feature scenario uses baseline
workflow — `molecule converge` preserves OpenWrt baseline so layered
scenarios remain accessible.

See: `ansible-testing` skill (verify completeness, baseline workflow,
per-feature scenario setup), `multi-node-ssh` skill (ProxyJump for LAN nodes).

**Implementation pattern:**
- Extend `molecule/default/verify.yml` with Home Assistant assertions
- Verify generic container cleanup handles VMID 200 (already iterates
  `pct list` — no hardcoded VMIDs)
- Container cleanup is generic. Host-side cleanup: none — no host files deployed.

- [ ] Extend `molecule/default/verify.yml`:
  - Container running: `pct status 200` returns `running`
  - Docker daemon active
  - HA container running
  - Web UI on port 8123, API returns valid response
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles container 200 (pct list iteration — confirm no changes needed)
- [ ] No host-side cleanup required — Home Assistant config lives inside container
- [ ] Create `molecule/homeassistant-lxc/` per-feature scenario (optional):
  - `converge.yml`: reconstruct homeassistant group, run homeassistant_configure
  - `verify.yml`: reconstruct homeassistant group, run Home Assistant assertions
  - `cleanup.yml`: destroy container 200 (generic LXC cleanup)
  - Baseline workflow: run `molecule converge` (default) first to establish
    OpenWrt + Home Assistant; then `molecule converge -s homeassistant-lxc` for iteration
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, Docker, HA container,
  web UI, API, deploy_stamp
- [ ] Cleanup leaves no Home Assistant artifacts on host or controller
- [ ] Baseline workflow: after `molecule test`, `molecule converge` restores
  baseline for layered scenarios (per `ansible-testing` skill)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Depends on M1–M4._

- [ ] Create `docs/architecture/homeassistant-build.md`:
  - Requirements, design decisions, env variables
  - Docker-in-LXC: nesting=1, cgroup delegation
  - USB passthrough: optional, empty list handling
  - Backup/restore strategy
  - Test vs production workflow differences
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Home Assistant provision + configure plays
  - Role catalog: add homeassistant_lxc, homeassistant_configure
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Home Assistant project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented (HA_ADMIN_PASSWORD, auto-generation for testing)
- [ ] USB passthrough optional behavior documented

**Rollback:** N/A — documentation-only milestone.

# Netdata Monitoring Agent

## Overview

A lightweight LXC container running a Netdata child agent that collects
system and container metrics from the Proxmox node. Streams to a Netdata
parent on the home server when the WireGuard tunnel is available. Provides
local dashboards even without the tunnel.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 128 MB
- Disk: 1 GB
- Network: LAN bridge
- VMID: 500

## Startup

- Auto-start: yes
- Boot priority: 3 (alongside Pi-hole and rsyslog)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: yes (all builds get monitoring)

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) — **soft dependency**: streaming uses tunnel
  when available; Netdata functions fully as local dashboard without it

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
├── Monitoring stack: Netdata child-parent streaming
│   └── Richer out-of-box than Prometheus+Grafana; built-in dashboards; child-parent fits remote topology
│
├── Host metrics access: bind mount /proc and /sys read-only into LXC
│   └── Needed for accurate host CPU, memory, disk, temperature; Proxmox API lacks per-interface and thermal data
│   └── Bind mounts passed to proxmox_lxc via role vars: lxc_ct_mount_entries with full spec
│       (e.g., /proc,mp=/host/proc,ro=1 and /sys,mp=/host/sys,ro=1)
│
├── WireGuard dependency: soft (optional)
│   └── Functions fully as local dashboard; streaming activates when parent is reachable
│
└── Data retention: minimal on child (dbengine, 1 hour)
    └── Parent handles long-term storage; child is ephemeral
```

---

## Milestone Dependency Graph

```
M1: Provisioning ─────── self-contained
 └── M2: Configuration ─ self-contained, depends on M1
      └── M3: Integration ─ self-contained, depends on M1+M2
           └── M4: Testing ─── self-contained, depends on M1–M3
                └── M5: Docs ─ self-contained, depends on M1–M4
```

---

## Milestones

### Milestone 1: Provisioning

_Self-contained. No external dependencies._

Create the `netdata_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.
Netdata and rsyslog provisioning can be combined in the same site.yml
play (both target `monitoring_nodes`); configure plays are separate
(different dynamic groups: `netdata` vs `rsyslog`).

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/netdata_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `monitoring_nodes`, tagged `[netdata]`
- deploy_stamp included as last role in the provision play
- Dynamic group `netdata` populated by `proxmox_lxc` via `add_host`
- Bind mounts: pass `lxc_ct_mount_entries` to `proxmox_lxc` via include_role vars.
  Format: full pct spec per entry (e.g. `/proc,mp=/host/proc,ro=1` and
  `/sys,mp=/host/sys,ro=1`). If `proxmox_lxc`'s task uses `{{ item }},mp={{ item }}`
  (same host/container path), it may need enhancement to accept full specs.

**Already complete** (from shared infrastructure / group_vars):
- `netdata_ct_id: 500` in `inventory/group_vars/all.yml`
- `monitoring_nodes` flavor group and `netdata` dynamic group in `inventory/hosts.yml`
- `monitoring_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_startup_order[500]: 3` in `inventory/group_vars/all.yml`
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/netdata_lxc/defaults/main.yml`:
  - `netdata_ct_hostname: netdata`
  - `netdata_ct_memory: 128`, `netdata_ct_cores: 1`, `netdata_ct_disk: "1"`
  - `netdata_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `netdata_ct_onboot: true`, `netdata_ct_startup_order: 3`
  - `netdata_ct_mount_entries: ["/proc,mp=/host/proc,ro=1", "/sys,mp=/host/sys,ro=1"]`
- [ ] Create `roles/netdata_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ netdata_ct_id }}"`, `lxc_ct_hostname: netdata`,
    `lxc_ct_dynamic_group: netdata`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`,
    `lxc_ct_mount_entries: "{{ netdata_ct_mount_entries }}"`
- [ ] Create `roles/netdata_lxc/meta/main.yml` with required metadata
  (`author`, `license: proprietary`, `role_name`, `description`,
  `min_ansible_version`, `platforms`)
- [ ] Add provision play to `site.yml` targeting `monitoring_nodes`, tagged
  `[netdata]`, with `netdata_lxc` role and `deploy_stamp` (combined with
  `rsyslog_lxc` in same play when rsyslog exists; configure plays remain
  separate per dynamic group)
- [ ] Verify Debian 12 LXC template exists in `images/` directory

**Verify:**

- [ ] Container 500 is running: `pct status 500` returns `running`
- [ ] Container is in `netdata` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 500` shows `onboot: 1`,
  `startup: order=3`
- [ ] Bind mounts present: `pct config 500` shows mp entries for
  `/proc`→`/host/proc` and `/sys`→`/host/sys` (read-only)
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `netdata_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: none — Netdata does not deploy
host-side config files.

---

### Milestone 2: Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with Netdata via official kickstart,
templated config for local dashboard and optional parent streaming.
Uses `pct exec` (proxmox_pct_remote) — no SSH server inside container.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/netdata_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/netdata.conf.j2`, `templates/stream.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `netdata` dynamic group, tagged
  `[netdata]`, after the provision play

**Env variables** (all optional — soft dependency on WireGuard):

| Variable | Purpose | Example |
|----------|---------|---------|
| `NETDATA_STREAM_API_KEY` | API key for parent streaming | `uuid-from-parent` |
| `netdata_parent_ip` | Parent Netdata IP (e.g. via WireGuard) | `10.0.0.1` |

Both are soft-deps: when unset, Netdata runs as local-only dashboard.
Add to `.env` template; use `lookup('env', ...) | default('', true)` in
role defaults. NEVER add to REQUIRED_ENV in build.py.

- [ ] Create `roles/netdata_configure/defaults/main.yml`:
  - `netdata_stream_api_key: "{{ lookup('env', 'NETDATA_STREAM_API_KEY') | default('', true) }}"`
  - `netdata_parent_ip: "{{ lookup('env', 'netdata_parent_ip') | default('', true) }}"`
  - `netdata_retention_minutes: 60`
  - `netdata_web_port: 19999`
- [ ] Create `roles/netdata_configure/tasks/main.yml`:
  - Install Netdata via official kickstart script
    (with retries/delay for apt — container may need time for initial fetch)
  - Template `netdata.conf`:
    - Memory mode: `dbengine`, 1-hour retention
    - Web dashboard: listen on LAN IP, port 19999
    - Proc/sys paths: `/host/proc`, `/host/sys`
  - Template `stream.conf` (conditional on `netdata_parent_ip`):
    - Destination: parent via WireGuard tunnel
    - API key from `.env` (`NETDATA_STREAM_API_KEY`)
    - Buffer on disconnect
  - Enable cgroups monitoring for per-container metrics
  - Enable and start Netdata service
- [ ] Create `roles/netdata_configure/templates/netdata.conf.j2`
- [ ] Create `roles/netdata_configure/templates/stream.conf.j2`
- [ ] Create `roles/netdata_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `netdata` dynamic group,
  tagged `[netdata]`, `gather_facts: true`, after the provision play
- [ ] Ensure idempotency: all tasks safe to re-run (template overwrite,
  service enable is idempotent)

**Verify:**

- [ ] Netdata service running: `pct exec 500 -- systemctl is-active netdata`
- [ ] Dashboard on port 19999: `pct exec 500 -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:19999`
- [ ] Host metrics visible: `/host/proc` and `/host/sys` exist and contain
  expected data (CPU, memory from host)
- [ ] `netdata.conf` has correct proc/sys paths and retention
- [ ] `stream.conf` present if `netdata_parent_ip` set; absent or disabled
  when `netdata_parent_ip` empty
- [ ] Cgroups plugin enabled for per-container metrics
- [ ] Idempotent: second run does not break config

**Rollback:**

- Stop and disable service: `systemctl disable --now netdata` (inside container)
- Remove config files: `/etc/netdata/netdata.conf`, `/etc/netdata/stream.conf`
- Uninstall Netdata (if desired): remove packages installed by kickstart
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Integration

_Self-contained. Depends on M1 and M2._

Wire up site.yml, add `NETDATA_STREAM_API_KEY` and `netdata_parent_ip` to
`.env` template, create `tasks/reconstruct_netdata_group.yml` for
per-feature scenarios and rollback.

See: `vm-lifecycle` skill (dynamic group reconstruction).

- [ ] Add `netdata_lxc` to `site.yml` provision play targeting
  `monitoring_nodes` (combined with `rsyslog_lxc` in same play when
  rsyslog exists)
- [ ] Add `netdata_configure` play targeting `netdata` dynamic group
- [ ] Include `deploy_stamp` in the provision play
- [ ] Add `NETDATA_STREAM_API_KEY` and `netdata_parent_ip` to `.env` template
  (optional, documented as soft-deps on WireGuard)
- [ ] Create `tasks/reconstruct_netdata_group.yml`:
  - Verify container 500 is running (`pct status {{ netdata_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ netdata_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction (no SSH auth detection needed —
    pct_remote is always the connection method)
- [ ] Update `build.py` docstring with `netdata` tag

**Verify:**

- [ ] Full `molecule converge` runs without error
- [ ] `netdata` dynamic group populated after provision
- [ ] `reconstruct_netdata_group.yml` successfully reconstructs group

**Rollback:** N/A — integration wiring; revert via git.

---

### Milestone 4: Testing

_Self-contained. Depends on M1–M3._

Extend molecule verify and cleanup. Container cleanup is generic (pct
list iteration); no host-side cleanup needed for Netdata.

See: `ansible-testing` skill (verify completeness, baseline workflow).

- [ ] Extend `molecule/default/verify.yml` with Netdata assertions:
  - Container 500 running
  - Netdata active, dashboard on port 19999
  - Host metrics visible (CPU, memory from /host/proc)
  - Streaming config present if parent IP set
  - All assertions run from Proxmox host via `pct exec 500 --`
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 500 (already iterates `pct list` — confirm)
- [ ] Add host cleanup for Netdata: none (no host-side files deployed)
- [ ] Create `molecule/netdata-lxc/` per-feature scenario:
  - `molecule.yml`: same platform as default, `monitoring_nodes` in groups,
    optional Netdata env vars in provisioner env (empty)
  - `converge.yml`: reconstruct netdata group, run netdata_configure
  - `verify.yml`: reconstruct netdata group, run Netdata assertions
  - `cleanup.yml`: destroy container 500 (`pct stop` + `pct destroy`)
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [ ] Run `molecule test` (full integration) — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `molecule test -s netdata-lxc` passes (per-feature scenario)
- [ ] No Netdata env vars required in `test.env` (local-only mode works)
- [ ] Verify assertions cover: container state, auto-start, bind mounts,
  service state, dashboard, host metrics, optional streaming config
- [ ] Cleanup leaves no Netdata artifacts on host (container cleanup only)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/netdata-build.md`:
  - Requirements, design decisions, env variables
  - Bind mount requirements (/proc, /sys read-only via proxmox_lxc vars)
  - Child-parent streaming flow and WireGuard soft dependency
  - Metrics catalog and local vs parent dashboard behavior
  - Test vs production workflow differences
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Netdata provision + configure plays
  - Verify monitoring topology includes Netdata container
- [ ] Update `docs/architecture/roles.md`:
  - Add `netdata_lxc` role documentation (purpose, bind mounts, key variables)
  - Add `netdata_configure` role documentation (purpose, env vars,
    streaming, cgroups)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Netdata project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with optional/soft-dependency behavior

**Rollback:** N/A — documentation-only milestone.

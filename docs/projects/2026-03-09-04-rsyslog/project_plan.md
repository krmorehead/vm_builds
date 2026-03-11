# rsyslog

## Overview

A minimal LXC container running rsyslog as a centralized log collector.
All other containers and VMs forward their logs here. rsyslog ships
aggregated logs to the home server over the WireGuard tunnel when
available and buffers locally when the tunnel is down.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 64 MB
- Disk: 1 GB (local buffer for log spooling)
- Network: LAN bridge, static IP
- VMID: 501

## Startup

- Auto-start: yes
- Boot priority: 3 (available early for debugging subsequent deploys)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: yes (all builds get logging)

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) — **soft dependency**: forwarding uses tunnel
  when available; rsyslog works fully without it (local collection + buffer)

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
├── Log collector: rsyslog
│   ├── Pre-installed on Debian — no package install needed, only configuration
│   └── Minimal footprint (~10 MB RAM), mature, RFC 5424 support
│
├── Transport: TCP 514 with disk-assisted queue
│   └── Reliable delivery without RELP complexity; disk queue handles tunnel outages
│
├── Log format: RFC 5424 structured syslog
│   └── Standard, parseable by any central log server
│
├── WireGuard dependency: soft (optional)
│   └── Collects and buffers locally without tunnel; forwarding activates when available
│
└── Env variable: RSYSLOG_HOME_SERVER
    ├── Optional — lookup('env', 'RSYSLOG_HOME_SERVER') | default('', true)
    ├── When set: forward logs to home server via WireGuard tunnel
    ├── When empty: local collection + buffer only (fully functional)
    └── NEVER add to REQUIRED_ENV in build.py
```

---

## Milestone Dependency Graph

```
M1: Provisioning ─────── self-contained
 └── M2: Configuration ── depends on M1
      └── M3: Log Client Pattern ── cross-cutting, depends on M1+M2
           └── M4: Integration ──── depends on M1–M3
                └── M5: Testing ──── depends on M1–M4
                     └── M6: Docs ── depends on M1–M5
```

---

## Milestones

### Milestone 1: Provisioning

_Self-contained. No external dependencies._

Create the `rsyslog_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/rsyslog_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `monitoring_nodes`, tagged `[monitoring]`
- deploy_stamp included as last role in the provision play
- Dynamic group `rsyslog` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure project 00):
- `rsyslog_ct_id: 501` in `group_vars/all.yml`
- `monitoring_nodes` flavor group and `rsyslog` dynamic group in `inventory/hosts.yml`
- `monitoring_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/rsyslog_lxc/defaults/main.yml`:
  - `rsyslog_ct_hostname: rsyslog`
  - `rsyslog_ct_memory: 64`, `rsyslog_ct_cores: 1`, `rsyslog_ct_disk: "1"`
  - `rsyslog_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `rsyslog_ct_onboot: true`, `rsyslog_ct_startup_order: 3`
  - `rsyslog_ct_ip` (static IP on LAN subnet — from host_vars or computed)
- [ ] Create `roles/rsyslog_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ rsyslog_ct_id }}"`, `lxc_ct_hostname: rsyslog`,
    `lxc_ct_dynamic_group: rsyslog`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`, static IP on LAN bridge
- [ ] Create `roles/rsyslog_lxc/meta/main.yml` with required metadata
  (`author`, `license: proprietary`, `role_name`, `description`,
  `min_ansible_version`, `platforms`)
- [ ] Add provision play to `site.yml` targeting `monitoring_nodes`, tagged
  `[monitoring]`, with `rsyslog_lxc` role and `deploy_stamp` (after OpenWrt
  configure, before cleanup)

**Verify:**

- [ ] Container 501 is running: `pct status 501` returns `running`
- [ ] Container is in `rsyslog` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 501` shows `onboot: 1`,
  `startup: order=3`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `rsyslog_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: **none** — rsyslog deploys no
host-side files (no kernel modules, no host config).

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with rsyslog receive/forward rules,
logrotate, and optional home-server forwarding. rsyslog is pre-installed
on Debian — no package install needed, only configuration.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/rsyslog_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/10-receive.conf.j2`, `templates/20-forward.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `rsyslog` dynamic group, tagged
  `[monitoring]`, after the provision play

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `RSYSLOG_HOME_SERVER` | no | Forward logs to home server via WireGuard | `10.0.0.1` |

When empty: local collection + buffer only. rsyslog works fully without it.
Soft-dep on WireGuard: forwarding uses tunnel when available.

- [ ] Create `roles/rsyslog_configure/defaults/main.yml`:
  - `rsyslog_home_server: "{{ lookup('env', 'RSYSLOG_HOME_SERVER') | default('', true) }}"`
- [ ] Create `roles/rsyslog_configure/tasks/main.yml`:
  - Template `/etc/rsyslog.d/10-receive.conf`:
    - Listen on TCP 514, accept from LAN subnet only
  - Template `/etc/rsyslog.d/20-forward.conf` (conditional on `rsyslog_home_server | length > 0`):
    - Forward to home server via WireGuard tunnel
    - Disk-assisted queue for reliability during outages
  - Configure logrotate: 7-day retention, compress
  - Restart rsyslog: `systemctl restart rsyslog`
- [ ] Create `roles/rsyslog_configure/templates/10-receive.conf.j2`
- [ ] Create `roles/rsyslog_configure/templates/20-forward.conf.j2`
- [ ] Create `roles/rsyslog_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `rsyslog` dynamic group,
  tagged `[monitoring]`, `gather_facts: true`, after the provision play

**Verify:**

- [ ] rsyslog listening on TCP 514: `ss -tlnp | grep 514` or `pct exec 501 -- ss -tlnp | grep 514`
- [ ] `/etc/rsyslog.d/10-receive.conf` exists with correct content
- [ ] When `RSYSLOG_HOME_SERVER` set: `/etc/rsyslog.d/20-forward.conf` exists
- [ ] When empty: `20-forward.conf` absent or disabled
- [ ] logrotate config present: `/etc/logrotate.d/rsyslog` or equivalent
- [ ] Local spool directory exists: `/var/spool/rsyslog` or configured path
- [ ] Idempotent: second run produces no changes

**Rollback:**

- Remove templated configs: `rm -f /etc/rsyslog.d/10-receive.conf /etc/rsyslog.d/20-forward.conf`
- Restore default rsyslog config if needed
- Restart rsyslog: `systemctl restart rsyslog`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Log Client Pattern

_Cross-cutting concern. Depends on M1 and M2._

Reusable pattern so every future container auto-forwards logs to the
rsyslog collector. This adds a variable that other configure roles can
include when templating their forwarding config.

See: `vm-lifecycle` skill (cross-role data pattern: set_fact with
cacheable or group_vars).

- [ ] Add `rsyslog_client_config` to `group_vars/all.yml`:
  - Container IP + port (e.g., `{ host: "{{ rsyslog_ct_ip }}", port: 514 }`)
  - Or use `set_fact` with `cacheable: true` from rsyslog_configure when
    the container IP is known at runtime
- [ ] Document: each `<type>_configure` role templates a forwarding snippet
  when `rsyslog_client_config` is defined
- [ ] Implement for OpenWrt: `log_ip` and `log_port` UCI settings
  (blocked on OpenWrt syslog feature play)
- [ ] Implement for Pi-hole: FTL syslog forwarding (blocked on Pi-hole project)

**Implementation pattern:**
- `group_vars/all.yml`: add `rsyslog_client_config` (or set_fact from
  rsyslog_configure after container IP is known)
- Each consuming role: conditional `include` or `template` task when
  variable is defined

**Verify:**

- [ ] `rsyslog_client_config` is available to configure roles
- [ ] OpenWrt syslog forwarding (when implemented) sends logs to rsyslog
- [ ] Pi-hole FTL forwarding (when implemented) sends logs to rsyslog

**Rollback:**

- Remove `rsyslog_client_config` from group_vars
- Revert client forwarding config in each consuming role

---

### Milestone 4: Integration

_Depends on M1–M3._

Wire rsyslog into site.yml, ensure correct play ordering, and add
inventory/group_vars entries.

See: `vm-lifecycle` skill (playbook execution order, deploy_stamp pairing).

- [ ] Add `rsyslog_lxc` to `site.yml` targeting `monitoring_nodes`
  (may be combined with `netdata_lxc` in same play)
- [ ] Add `rsyslog_configure` play targeting `rsyslog` dynamic group
- [ ] Include `deploy_stamp` in provision play
- [ ] Order play after OpenWrt configure, before most services
- [ ] Ensure `monitoring_nodes` and `rsyslog` dynamic group exist in
  inventory (already present)
- [ ] Add `RSYSLOG_HOME_SERVER` to molecule provisioner env in
  `molecule/default/molecule.yml` (optional, empty for tests):
  ```yaml
  RSYSLOG_HOME_SERVER: ${RSYSLOG_HOME_SERVER:-}
  ```

**Verify:**

- [ ] Full `ansible-playbook site.yml` runs without error
- [ ] rsyslog container starts before services that forward logs
- [ ] deploy_stamp records rsyslog plays

**Rollback:** N/A — integration only; revert site.yml changes.

---

### Milestone 5: Testing & Integration

_Depends on M1–M4._

Wire up molecule testing, extend verify.yml, and add reconstruct task
for per-feature scenarios.

See: `ansible-testing` skill (verify completeness, per-feature scenario,
baseline workflow).

- [ ] Create `tasks/reconstruct_rsyslog_group.yml`:
  - Verify container 501 is running (`pct status {{ rsyslog_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ rsyslog_ct_id }}`,
    `ansible_user: root`
- [ ] Extend `molecule/default/verify.yml`:
  - Container 501 running: `pct status 501` returns `running`
  - rsyslog active: `pct exec 501 -- systemctl is-active rsyslog`
  - Listening on TCP 514: `pct exec 501 -- ss -tlnp | grep 514`
  - Test log message received: send from Proxmox host via `logger`, verify
    in container spool or log file
  - Local spool directory exists: `pct exec 501 -- test -d /var/spool/rsyslog`
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 501 (already iterates `pct list` — confirm)
- [ ] Host-side cleanup: **none** for rsyslog (no host-side files deployed)
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, service state,
  TCP listener, log reception, spool directory
- [ ] Cleanup leaves no rsyslog artifacts on host (container destroyed via
  generic pct iteration)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 6: Documentation

_Depends on M1–M5._

- [ ] Create `docs/architecture/rsyslog-build.md`:
  - Requirements, design decisions, env variables
  - Log flow and client integration pattern
  - RSYSLOG_HOME_SERVER optional behavior, WireGuard soft dependency
  - rsyslog pre-installed on Debian — configuration only
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add rsyslog provision + configure plays (if not present)
  - Verify rsyslog container in topology
- [ ] Update `docs/architecture/roadmap.md`:
  - Add rsyslog project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with optional behavior

**Rollback:** N/A — documentation-only milestone.

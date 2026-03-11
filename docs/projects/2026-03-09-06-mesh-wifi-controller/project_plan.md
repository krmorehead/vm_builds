# Mesh WiFi Controller

## Overview

An LXC container running OpenWISP for centralized management of WiFi access
points across multiple OpenWrt mesh nodes. Provides a web UI and REST API for
managing SSIDs, channel plans, and transmit power. Complements Dawn (installed
on OpenWrt nodes in project 01) which handles real-time client steering.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 512 MB
- Disk: 4 GB
- Network: LAN bridge, static IP
- VMID: 103

## Startup

- Auto-start: yes
- Boot priority: 4 (after network + observability)
- Depends on: OpenWrt Router (mesh established), Pi-hole (DNS)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: no (single node doesn't need centralized management)
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router with 802.11s mesh operational (project 01)
- Multiple WiFi-capable nodes (2+ APs for meaningful use)

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | Safe host commands, shell pipefail requirements |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes, managing mesh satellite nodes |
| `openwrt-build` | OpenWrt mesh configuration, Dawn client steering, UCI patterns |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Controller software: OpenWISP
│   └── Only mature open-source option for centralized OpenWrt AP management; web UI, REST API, SSH push
│
├── Client steering: Dawn on OpenWrt nodes (project 01, milestone 6)
│   └── OpenWrt-native (ubus), 802.11k/v/r at Layer 2; runs ON the AP, not on a controller
│
├── Architecture split: OpenWISP = config management, Dawn = real-time steering
│   └── Separation of concerns: policy vs execution
│
├── Resource note: OpenWISP requires Redis, Celery, Django, PostgreSQL
│   └── 512 MB RAM is tight but feasible with tuning. Document in architecture:
│       - Single Celery worker (CELERY_WORKER_CONCURRENCY=1)
│       - PostgreSQL shared_buffers reduced (e.g., 64MB)
│       - Redis maxmemory 64MB
│       - Consider 1 GB RAM if OOM occurs in production
│
├── Container cleanup: generic
│   └── LXC destruction handled by `molecule/default/cleanup.yml` (`pct list` iteration)
│       and `playbooks/cleanup.yml`. No host-side files deployed by meshwifi roles.
│
└── Host-side cleanup: none
    └── meshwifi_lxc and meshwifi_configure do not deploy files to the Proxmox host.
        Container destruction is the only cleanup; no modprobe, bridge, or credential changes.
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ─────── self-contained
 └── M2: OpenWISP Config ─── self-contained, depends on M1
      └── M3: AP Registration ─── blocked on: OpenWrt mesh operational (project 01)
                                   AND multiple WiFi-capable nodes (mesh1 from 2026-03-11-00)
           └── M4: Testing ─────── self-contained, depends on M1+M2 (+ M3 for AP assertions)
                └── M5: Docs ──── self-contained, depends on M1–M4
```

**M3 dependency note:** AP registration requires (1) OpenWrt mesh being operational
(802.11s + Dawn from project 01), and (2) multiple WiFi-capable nodes. The
multi-node testing project (`2026-03-11-00`) provides mesh1 as a second
WiFi-capable node for testing AP registration end-to-end.

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `meshwifi_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/meshwifi_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `wifi_nodes`, tagged `[meshwifi]`
- deploy_stamp included as last role in the provision play
- Dynamic group `meshwifi` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure):
- `meshwifi_ct_id: 103` in `group_vars/all.yml`
- `wifi_nodes` flavor group and `meshwifi` dynamic group in `inventory/hosts.yml`
- `wifi_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/meshwifi_lxc/defaults/main.yml`:
  - `meshwifi_ct_id: 103`, `meshwifi_ct_memory: 512`, `meshwifi_ct_cores: 1`
  - `meshwifi_ct_disk: "4"`, `meshwifi_ct_ip` (static)
  - `meshwifi_ct_onboot: true`, `meshwifi_ct_startup_order: 4`
  - `meshwifi_ct_template: "{{ proxmox_lxc_default_template }}"`
- [ ] Create `roles/meshwifi_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ meshwifi_ct_id }}"`, `lxc_ct_hostname: meshwifi`,
    `lxc_ct_dynamic_group: meshwifi`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`, `lxc_ct_ip`
- [ ] Create `roles/meshwifi_lxc/meta/main.yml` with required metadata
  (`author`, `license: proprietary`, `role_name`, `description`,
  `min_ansible_version`, `platforms`)
- [ ] Add provision play to `site.yml` targeting `wifi_nodes`, tagged
  `[meshwifi]`, with `meshwifi_lxc` role and `deploy_stamp` (after OpenWrt
  configure, before cleanup)
- [ ] Verify Debian 12 LXC template exists in `images/` directory

**Verify:**

- [ ] Container 103 is running: `pct status 103` returns `running`
- [ ] Container is in `meshwifi` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 103` shows `onboot: 1`,
  `startup: order=4`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `meshwifi_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: none.

---

### Milestone 2: OpenWISP Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with OpenWISP dependencies: Python 3, Redis,
PostgreSQL, Nginx. Install OpenWISP Controller via pip, template settings,
and configure admin user from env vars.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/meshwifi_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/` (settings, Nginx config), `meta/main.yml`
- site.yml: configure play targeting `meshwifi` dynamic group, tagged
  `[meshwifi]`, after the provision play
- Connection: `community.proxmox.proxmox_pct_remote` (pct exec from Proxmox host)

**Env variables:**

| Variable | Required (production) | Auto-generated (testing) | Purpose |
|----------|------------------------|---------------------------|---------|
| `OPENWISP_ADMIN_USER` | yes | yes (default: admin) | Admin username |
| `OPENWISP_ADMIN_PASSWORD` | yes | yes (random) | Admin password |

When env vars are empty (e.g., in `test.env`), role auto-generates values.
Production deployments MUST set both in `.env`.

- [ ] Create `roles/meshwifi_configure/defaults/main.yml`:
  - `OPENWISP_ADMIN_USER` via `lookup('env', 'OPENWISP_ADMIN_USER') | default('admin', true)`
  - `OPENWISP_ADMIN_PASSWORD` via `lookup('env', 'OPENWISP_ADMIN_PASSWORD') | default('', true)`
  - Tuning vars for 512 MB: `postgresql_shared_buffers: 64MB`, `redis_maxmemory: 64MB`,
    `celery_worker_concurrency: 1`
- [ ] Create `roles/meshwifi_configure/tasks/main.yml`:
  - Install dependencies: Python 3, Redis, PostgreSQL, Nginx (with `retries: 3`, `delay: 5`)
  - Install OpenWISP Controller via pip
  - Template settings: local PostgreSQL, local Redis, single Celery worker,
    Nginx reverse proxy (self-signed cert)
  - Configure admin user (from env or auto-generated)
  - Set default WiFi template: SSIDs, channel plan, transmit power
  - Configure SSH credentials for managed OpenWrt nodes
- [ ] Add configure play to `site.yml` targeting `meshwifi` dynamic group,
  tagged `[meshwifi]`, `gather_facts: true`, after the provision play
- [ ] Ensure idempotency: all tasks safe to re-run

**Verify:**

- [ ] OpenWISP web UI reachable on port 443: `pct exec 103 -- curl -k -s -o /dev/null -w '%{http_code}' https://localhost/` returns 200
- [ ] PostgreSQL active: `pct exec 103 -- systemctl is-active postgresql`
- [ ] Redis active: `pct exec 103 -- systemctl is-active redis-server`
- [ ] Celery worker running: `pct exec 103 -- pgrep -f celery`
- [ ] Admin user can log in (via curl or browser)
- [ ] Idempotent: second run produces no changes

**Rollback:**

- Stop and remove OpenWISP services
- Uninstall packages: OpenWISP, PostgreSQL, Redis, Nginx
- Remove config files and data directories
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: AP Registration

_Blocked on: OpenWrt mesh operational (project 01) AND multiple WiFi-capable
nodes (mesh1 from 2026-03-11-00)._

Install `openwisp-config` agent on OpenWrt nodes, configure agent to point to
OpenWISP controller, and verify centralized config push. This **cross-cuts**
with the `openwrt_configure` role — the agent is installed on OpenWrt, not in
the meshwifi container.

See: `vm-lifecycle` skill (post-baseline feature play pattern),
`openwrt-build` skill (UCI patterns, mesh configuration).

**Implementation pattern (post-baseline feature play):**

Per `vm-lifecycle` skill: post-baseline features are separate task files in
the configure role, NOT separate roles. AP registration adds a task file to
`openwrt_configure` and a **separate feature play** in `site.yml` — NOT part
of the base openwrt_configure baseline.

- Task file: `roles/openwrt_configure/tasks/openwisp_agent.yml`
- site.yml: TWO plays (paired per vm-lifecycle pattern):
  1. Configure play targeting `openwrt` dynamic group, tag `openwrt-openwisp`,
     `never`, `include_role` with `tasks_from: openwisp_agent.yml`
  2. deploy_stamp play targeting `router_nodes`, tag `openwrt-openwisp`, `never`
- Molecule scenario: `molecule/openwrt-openwisp/` (or extend mesh scenario)
- The meshwifi container (M1+M2) must be running for the agent to register

- [ ] Create `roles/openwrt_configure/tasks/openwisp_agent.yml`:
  - Install `openwisp-config` agent via opkg (with retries per openwrt-build)
  - Configure agent to point to OpenWISP controller IP (from `meshwifi_ct_ip` or
    inventory)
  - Register nodes via API or auto-registration
  - Ensure Dawn client steering coexists with OpenWISP config management
- [ ] Add feature play pair to `site.yml` (configure on `openwrt`, deploy_stamp
  on `router_nodes`), both tagged `openwrt-openwisp`, `never`
- [ ] Verify centralized config push: change SSID → propagates to all nodes

**Verify:**

- [ ] `openwisp-config` agent installed on OpenWrt: `opkg list-installed | grep openwisp`
- [ ] Agent configured with controller URL
- [ ] At least one AP registered in OpenWISP UI (when test node has WiFi)
- [ ] Config change in OpenWISP propagates to OpenWrt node
- [ ] Dawn steering still operational (no conflict)

**Rollback (`--tags openwrt-openwisp-rollback`):**

- Uninstall `openwisp-config` from OpenWrt nodes
- Remove agent config files
- deploy_stamp rollback removes play entry

---

### Milestone 4: Testing & Integration

_Self-contained. Depends on M1 and M2. AP assertions (if any) depend on M3._

Wire up molecule testing, create the per-feature scenario, add
`reconstruct_meshwifi_group.yml`, and verify end-to-end.

See: `ansible-testing` skill (per-feature scenario setup, verify
completeness, baseline workflow).

- [ ] Create `tasks/reconstruct_meshwifi_group.yml`:
  - Verify container 103 is running (`pct status {{ meshwifi_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ meshwifi_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction (no SSH auth detection — pct_remote
    always)
- [ ] Add `OPENWISP_ADMIN_USER` and `OPENWISP_ADMIN_PASSWORD` to molecule
  provisioner env in `molecule/default/molecule.yml` (empty values —
  triggers auto-generation)
- [ ] Extend `molecule/default/verify.yml` with meshwifi assertions
  (all items from M1 and M2 verify sections, run from Proxmox host
  via `pct exec 103 --` commands)
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 103 (already iterates `pct list` — confirm)
- [ ] Host-side cleanup: none (meshwifi deploys no host files)
- [ ] Create `molecule/meshwifi-lxc/` per-feature scenario:
  - `molecule.yml`: same platform as default, `wifi_nodes` in groups,
    OpenWISP env vars in provisioner env (empty)
  - `converge.yml`: reconstruct meshwifi group, run meshwifi_configure
  - `verify.yml`: reconstruct meshwifi group, run meshwifi assertions
  - `cleanup.yml`: destroy container 103 (`pct stop` + `pct destroy`)
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [ ] Update `build.py` docstring with `meshwifi` tag
- [ ] Run `molecule test` (full integration) — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `molecule test -s meshwifi-lxc` passes (per-feature scenario)
- [ ] No OpenWISP env vars needed in `test.env` (auto-generation works)
- [ ] Verify assertions cover: container state, auto-start, OpenWISP UI,
  PostgreSQL, Redis, deploy_stamp
- [ ] Cleanup leaves no meshwifi artifacts (container destroyed, no host files)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/meshwifi-build.md`:
  - Requirements, design decisions, env variables
  - OpenWISP + Dawn architecture split
  - 512 MB RAM tuning (Celery, PostgreSQL, Redis)
  - Test vs production workflow differences
  - AP registration cross-cut with openwrt_configure
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add meshwifi provision + configure plays
  - Add openwrt-openwisp feature play (when M3 implemented)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Mesh WiFi Controller to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with auto-generation behavior

**Rollback:** N/A — documentation-only milestone.

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

- Home Entertainment Box: yes (`wifi_nodes`)
- Minimal Router: no (single node doesn't need centralized management)
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router with 802.11s mesh operational (project 01)
- Multiple WiFi-capable nodes (2+ APs for meaningful use)
- `meshwifi_ct_id: 103` already in `group_vars/all.yml`
- `wifi_nodes` flavor group and `meshwifi` dynamic group already in `inventory/hosts.yml`
- `wifi_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`wifi_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
MeshWiFi containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case — WiFi controller only runs where OpenWrt
mesh nodes exist, which is always behind the OpenWrt router. If `wifi_nodes`
ever includes a WAN-connected host, add the WireGuard-style topology
branching at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness, image management |
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
├── LXC base: Custom Debian 12 template with full OpenWISP stack baked in
│   ├── "Bake, don't configure at runtime" — all packages baked into image
│   ├── OpenWISP stack: Django, PostgreSQL, Redis, Celery, Nginx
│   └── Configure role only applies host-specific settings (admin user, SSH creds, WiFi templates)
│
├── Image build: Debian 12 standard + full OpenWISP stack in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs: Python 3, Redis, PostgreSQL, Nginx
│   ├── Installs OpenWISP Controller via pip (pre-configured with local services)
│   └── Most complex image build after Pi-hole (many dependencies)
│
├── Client steering: Dawn on OpenWrt nodes (project 01, milestone 6)
│   └── OpenWrt-native (ubus), 802.11k/v/r at Layer 2; runs ON the AP, not on a controller
│
├── Architecture split: OpenWISP = config management, Dawn = real-time steering
│   └── Separation of concerns: policy vs execution
│
├── LXC features: none required
│   └── OpenWISP is a standard web application — no nesting, no iptables needed
│
├── Resource note: OpenWISP requires Redis, Celery, Django, PostgreSQL
│   └── 512 MB RAM is tight but feasible with tuning. Document in architecture:
│       - Single Celery worker (CELERY_WORKER_CONCURRENCY=1)
│       - PostgreSQL shared_buffers reduced (e.g., 64MB)
│       - Redis maxmemory 64MB
│       - Consider 1 GB RAM if OOM occurs in production
│
├── AP Registration: opkg exception to bake principle
│   └── `openwisp-config` agent is installed on OpenWrt nodes via opkg at configure time
│   └── This is a DOCUMENTED EXCEPTION: the agent runs on the OpenWrt VM (not the
│       controller container), and OpenWrt images are built separately via build-images.sh.
│       Adding openwisp-config to the OpenWrt image build is the preferred approach when
│       the mesh configuration is finalized.
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

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, MeshWiFi provisions on `wifi_nodes` (currently
`home` only for the controller; mesh nodes get the agent). MeshWiFi has
its own `[meshwifi]` tag.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/meshwifi-lxc/` which only touches
VMID 103. The OpenWrt baseline and other containers stay running.

```
Scenario Hierarchy (MeshWiFi additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including MeshWiFi provision + configure
│
└── molecule/meshwifi-lxc/           MeshWiFi container only (~60-90s)
    ├── converge: provision + configure MeshWiFi container
    ├── verify: MeshWiFi-specific assertions (OpenWISP UI, services)
    └── cleanup: destroy container 103 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on MeshWiFi container (only touches VMID 103)
molecule converge -s meshwifi-lxc             # ~60s, provision + configure
molecule verify -s meshwifi-lxc               # ~10s, assertions only
molecule converge -s meshwifi-lxc             # ~60s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s meshwifi-lxc              # destroys container 103 only

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `meshwifi-lxc` | Container 103 | Container 103 only | None — OpenWrt, WireGuard, etc. untouched |

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M1: Provisioning ── depends on M0
      └── M2: Configuration ── depends on M1
           └── M3: AP Registration ── blocked on: OpenWrt mesh operational (project 01)
                │                      AND multiple WiFi-capable nodes (mesh1)
                └── M4: Testing & Integration ── depends on M1–M2 (+ M3 for AP assertions)
                     └── M5: Documentation ── depends on M1–M4
```

**M3 dependency note:** AP registration requires (1) OpenWrt mesh being operational
(802.11s + Dawn from project 01), and (2) multiple WiFi-capable nodes. The
multi-node testing project (`2026-03-11-00`) provides mesh1 as a second
WiFi-capable node for testing AP registration end-to-end.

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with the full OpenWISP stack
pre-installed. This is the most complex image build — OpenWISP requires
Django, PostgreSQL, Redis, Celery, Nginx, and numerous Python packages.
Per the project's "Bake, don't configure at runtime" principle, all
packages belong in the image. The configure role (M2) only applies
host-specific settings (admin user, SSH credentials, WiFi templates).

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add MeshWiFi image build section to `build-images.sh`
- Template path: `images/meshwifi-debian-12-amd64.tar.zst`
- Template vars: `meshwifi_lxc_template` and `meshwifi_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Install system packages: Python 3, Redis, PostgreSQL, Nginx, build deps
3. Install OpenWISP Controller via pip (with all dependencies)
4. Pre-configure PostgreSQL database and user
5. Pre-configure Redis with `maxmemory 64MB`
6. Pre-configure Nginx reverse proxy (self-signed cert)
7. Pre-configure Celery with single worker (`CELERY_WORKER_CONCURRENCY=1`)
8. Pre-configure Django settings for 512 MB RAM
9. Clean pip caches, apt caches, stop all services, stop container
10. Export via `vzdump` and download template

- [ ] Add MeshWiFi template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_meshwifi_lxc` function)
- [ ] Add `meshwifi_lxc_template` and `meshwifi_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Add `meshwifi_ct_ip_offset: 13` to `group_vars/all.yml`
  (after netdata at 12)
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/meshwifi-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains OpenWISP, PostgreSQL, Redis, Nginx pre-installed
- [ ] PostgreSQL database and user pre-configured
- [ ] Django migrations applied, static files collected
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built)._

Create the `meshwifi_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision and configure plays to `site.yml`, and verify the
container runs. Integration with `site.yml` is consolidated here.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/meshwifi_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `wifi_nodes`, tagged `[meshwifi]`,
  in Phase 3
- deploy_stamp included as last role in the provision play
- Dynamic group `meshwifi` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure):
- `meshwifi_ct_id: 103` in `group_vars/all.yml`
- `wifi_nodes` flavor group and `meshwifi` dynamic group in `inventory/hosts.yml`
- `wifi_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/meshwifi_lxc/defaults/main.yml`:
  - `meshwifi_ct_hostname: meshwifi`
  - `meshwifi_ct_memory: 512`, `meshwifi_ct_cores: 1`, `meshwifi_ct_disk: "4"`
  - `meshwifi_ct_template: "{{ meshwifi_lxc_template }}"` (custom OpenWISP image)
  - `meshwifi_ct_template_path: "{{ meshwifi_lxc_template_path }}"`
  - `meshwifi_ct_onboot: true`, `meshwifi_ct_startup_order: 4`
  - `meshwifi_ct_ip_offset: "{{ meshwifi_ct_ip_offset | default(13) }}"`
  - No `lxc_ct_features` needed (OpenWISP is standard userspace)
- [ ] Create `roles/meshwifi_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Compute container IP from LAN prefix + offset (LAN-only, no WAN branching)
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id`, `lxc_ct_hostname`, `lxc_ct_dynamic_group`, memory, cores,
    disk, onboot, startup_order, static IP
- [ ] Create `roles/meshwifi_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `wifi_nodes`,
  tagged `[meshwifi]`, with `meshwifi_lxc` role and `deploy_stamp`
  (after OpenWrt configure, before cleanup)
- [ ] Add configure play to `site.yml` Phase 3, targeting `meshwifi` dynamic
  group, tagged `[meshwifi]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_meshwifi_group.yml`:
  - Verify container 103 is running (`pct status {{ meshwifi_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ meshwifi_ct_id }}`,
    `ansible_user: root`

**Verify:**

- [ ] Container 103 is running: `pct status 103` returns `running`
- [ ] Container is in `meshwifi` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 103` shows `onboot: 1`,
  `startup: order=4`
- [ ] Correct static IP matches computed offset
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `meshwifi_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: none.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with host-specific OpenWISP settings:
admin user, SSH credentials for managed nodes, and default WiFi templates.
OpenWISP stack is already baked into the image (M0). This role only
applies host-specific configuration.

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
  - `openwisp_admin_user` via `lookup('env', 'OPENWISP_ADMIN_USER') | default('admin', true)`
  - `openwisp_admin_password` via `lookup('env', 'OPENWISP_ADMIN_PASSWORD') | default('', true)`
  - Tuning vars for 512 MB: `postgresql_shared_buffers: 64MB`, `redis_maxmemory: 64MB`,
    `celery_worker_concurrency: 1`
- [ ] Create `roles/meshwifi_configure/tasks/main.yml` (via `pct_remote`):
  - Template Django settings with host-specific values
  - Configure admin user (from env or auto-generated)
  - Set default WiFi template: SSIDs, channel plan, transmit power
  - Configure SSH credentials for managed OpenWrt nodes
  - Start all services: PostgreSQL, Redis, Celery, Nginx, Django
- [ ] Create `roles/meshwifi_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- OpenWISP packages and dependencies — baked
- PostgreSQL database and user — baked
- Redis configuration — baked
- Nginx reverse proxy with self-signed cert — baked
- Celery worker configuration — baked
- Django static files — baked

**Verify:**

- [ ] OpenWISP web UI reachable on port 443: `pct exec 103 -- curl -k -s -o /dev/null -w '%{http_code}' https://localhost/` returns 200
- [ ] PostgreSQL active: `pct exec 103 -- systemctl is-active postgresql`
- [ ] Redis active: `pct exec 103 -- systemctl is-active redis-server`
- [ ] Celery worker running: `pct exec 103 -- pgrep -f celery`
- [ ] Admin user can log in (via curl or browser)
- [ ] Idempotent: second run produces no changes

**Rollback:**

- Stop all OpenWISP services
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

**Documented exception: opkg install on OpenWrt nodes.**
`openwisp-config` is installed on the OpenWrt VM via `opkg` at configure
time. This is a documented exception to the "bake, don't configure at
runtime" principle because: (1) the agent runs on the OpenWrt VM, not on
the controller container; (2) OpenWrt images are built separately via
`build-images.sh`; (3) adding `openwisp-config` to the OpenWrt image build
is the preferred long-term approach when the mesh configuration is finalized.

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

_Depends on M1 and M2. AP assertions (if any) depend on M3._

Create per-feature molecule scenario for fast MeshWiFi-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, add molecule env passthrough, and run
final validation.

See: `ansible-testing` skill (per-feature scenario setup, verify
completeness, baseline workflow), `rollback-patterns` skill (cleanup
completeness).

#### 4a. Per-feature scenario: `molecule/meshwifi-lxc/`

- [ ] Create `molecule/meshwifi-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - wifi_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      OPENWISP_ADMIN_USER: ${OPENWISP_ADMIN_USER:-}
      OPENWISP_ADMIN_PASSWORD: ${OPENWISP_ADMIN_PASSWORD:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/meshwifi-lxc/converge.yml`:
  ```yaml
  - name: Provision MeshWiFi LXC container
    hosts: wifi_nodes
    gather_facts: false
    roles:
      - meshwifi_lxc

  - name: Reconstruct meshwifi dynamic group
    hosts: wifi_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_meshwifi_group.yml

  - name: Configure MeshWiFi
    hosts: meshwifi
    gather_facts: true
    roles:
      - meshwifi_configure
  ```

- [ ] Create `molecule/meshwifi-lxc/verify.yml`
- [ ] Create `molecule/meshwifi-lxc/cleanup.yml`:
  Destroys only container 103.

#### 4b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with MeshWiFi assertions:
  - Container 103 running, onboot=1, startup order=4
  - OpenWISP UI reachable on port 443
  - PostgreSQL, Redis, Celery active
  - deploy_stamp contains `meshwifi_lxc` entry

- [ ] Verify generic container cleanup handles VMID 103

#### 4c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `meshwifi-rollback` play:
  ```yaml
  - name: Rollback MeshWiFi container
    hosts: wifi_nodes
    gather_facts: false
    tags: [meshwifi-rollback, never]
    tasks:
      - name: Stop and destroy MeshWiFi container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ meshwifi_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ meshwifi_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 4d. Molecule env passthrough

- [ ] Add `OPENWISP_ADMIN_USER` and `OPENWISP_ADMIN_PASSWORD` to molecule
  provisioner env in `molecule/default/molecule.yml` (empty values —
  triggers auto-generation)

#### 4e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s meshwifi-lxc` — per-feature cycle passes
- [ ] No OpenWISP env vars needed in `test.env` (auto-generation works)
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no MeshWiFi artifacts (container destroyed, no host files)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Depends on M1–M4._

- [ ] Create `docs/architecture/meshwifi-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - OpenWISP + Dawn architecture split
  - 512 MB RAM tuning (Celery, PostgreSQL, Redis)
  - opkg exception for openwisp-config agent
  - Baked config vs runtime config split
  - Test vs production workflow differences
  - AP registration cross-cut with openwrt_configure
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add MeshWiFi provision + configure plays
  - Add openwrt-openwisp feature play (when M3 implemented)
- [ ] Update `docs/architecture/roles.md`:
  - Add `meshwifi_lxc` role documentation (purpose, key variables)
  - Add `meshwifi_configure` role documentation (purpose, env vars, tuning)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Mesh WiFi Controller to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with auto-generation behavior
- [ ] opkg exception documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **OpenWrt mesh**: AP registration (M3) requires the OpenWrt mesh to be
  operational. The `openwisp-config` agent on OpenWrt nodes enables
  centralized management of SSIDs, channels, and power settings.
- **Dawn coexistence**: Dawn handles real-time client steering at Layer 2.
  OpenWISP handles policy-level configuration. Both must coexist on the
  same OpenWrt nodes without conflict.
- **rsyslog**: OpenWISP logs can be forwarded to the rsyslog collector.
  Django and Celery logging can be configured to use syslog.
- **Multi-node expansion**: When additional WiFi-capable nodes join the
  mesh, each node gets the `openwisp-config` agent via M3's AP registration
  play. The controller container stays on the primary host.

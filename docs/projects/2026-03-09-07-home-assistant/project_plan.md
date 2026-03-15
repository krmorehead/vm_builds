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

- Home Entertainment Box: yes (`service_nodes`)
- Minimal Router: no
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network)
- WireGuard VPN (project 02) — for remote access
- `homeassistant_ct_id: 200` already in `group_vars/all.yml`
- `service_nodes` flavor group and `homeassistant` dynamic group already in `inventory/hosts.yml`
- `service_nodes` already in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`service_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Home Assistant containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case — Home Assistant only runs on the Home
Entertainment Box profile, which always has OpenWrt. If `service_nodes` ever
includes a WAN-connected host, add the WireGuard-style topology branching
at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle-architecture` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `image-management-patterns` | Image build, local images/ directory, template management |
| `lxc-container-patterns` | LXC provisioning, pct_remote connection, container networking |
| `molecule-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-architecture` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-safety-rules` | Safe host commands, shell pipefail requirements |
| `lan-ssh-patterns` | ProxyJump for testing on LAN nodes |
| `project-planning-structure` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Installation method: HA Container (Docker in LXC)
│   └── Lightweight; full HA Core with all integrations. Supervised is fragile in LXC. HAOS as VM wastes resources.
│
├── LXC base: Custom Debian 12 template with Docker CE pre-installed
│   ├── "Bake, don't configure at runtime" — Docker CE packages baked into image
│   ├── Docker pull of HA container image is a DOCUMENTED EXCEPTION to bake principle
│   │   └── Container images are versioned, pinned, and idempotent. Unlike apt install,
│   │       docker pull of a specific tag is deterministic and does not depend on repo
│   │       availability at converge time (image can be pre-pulled in build)
│   └── Configure role handles HA compose template and host-specific config
│
├── Image build: Debian 12 standard + Docker CE in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   ├── Installs Docker CE from official Docker repo (GPG key + apt source)
│   ├── Pre-pulls HA container image to avoid runtime download
│   └── Optionally pre-installs docker-compose plugin
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

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Home Assistant provisions on `service_nodes`
(currently `home` only). It runs alongside other Phase 3 plays. Home
Assistant has its own `[homeassistant]` tag.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/homeassistant-lxc/` which only
touches VMID 200. The OpenWrt baseline and other containers stay running.

```
Scenario Hierarchy (Home Assistant additions)
├── molecule/default/                   Full integration (4-node, ~4-5 min)
│   └── Runs everything including HA provision + configure
│
└── molecule/homeassistant-lxc/        HA container only (~60-90s)
    ├── converge: provision + configure HA container
    ├── verify: HA-specific assertions
    └── cleanup: destroy container 200 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                              # ~4-5 min, all 4 nodes

# 2. Iterate on HA container (only touches VMID 200)
molecule converge -s homeassistant-lxc         # ~60s, provision + configure
molecule verify -s homeassistant-lxc           # ~10s, assertions only
molecule converge -s homeassistant-lxc         # ~60s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s homeassistant-lxc          # destroys container 200 only

# 4. Final validation before commit
molecule test                                  # full clean-state, ~4-5 min
molecule converge                              # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `homeassistant-lxc` | Container 200 | Container 200 only | None — OpenWrt, WireGuard, etc. untouched |

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M1: Provisioning ── depends on M0
      └── M2: Configuration ── depends on M1
           └── M3: Testing & Integration ── depends on M1–M2
                └── M4: Documentation ── depends on M1–M3
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with Docker CE pre-installed. Per
the project's "Bake, don't configure at runtime" principle, Docker packages
belong in the image. The HA container image is optionally pre-pulled during
the build (see Documented Exception below).

See: `image-management-patterns` skill.

**Implementation pattern:**
- Script: add Home Assistant image build section to `build-images.sh`
- Template path: `images/homeassistant-debian-12-amd64.tar.zst`
- Template vars: `homeassistant_lxc_template` and `homeassistant_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
   with `features: nesting=1` (required for Docker inside LXC)
2. Add Docker CE official repo (GPG key + apt source)
3. Install Docker CE and docker-compose plugin
4. Pre-pull `homeassistant/home-assistant:stable` container image
5. Clean apt caches, stop Docker daemon, stop container
6. Export via `vzdump` and download template

**Documented exception: Docker pull as part of build:**
Docker `pull` of a pinned image tag is deterministic and versioned, unlike
`apt install` which depends on live repo availability. Pre-pulling the HA
image during build avoids runtime downloads. If the image must be updated,
rebuild the template or pull at configure time (idempotent).

- [ ] Add Home Assistant template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_homeassistant_lxc` function)
- [ ] Add `homeassistant_lxc_template` and `homeassistant_lxc_template_path`
  to `group_vars/all.yml`
- [ ] Add `homeassistant_ct_ip_offset: 14` to `group_vars/all.yml`
  (after meshwifi at 13)
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/homeassistant-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Docker CE packages pre-installed
- [ ] Docker daemon starts inside the template container
- [ ] HA container image is pre-pulled (or pull succeeds at configure time)
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built)._

Create the `homeassistant_lxc` role as a thin wrapper around `proxmox_lxc`
with nesting enabled for Docker. Add the provision and configure plays to
`site.yml`, and verify the container runs. Integration with `site.yml` is
consolidated here. USB passthrough is optional — when
`homeassistant_usb_devices` is empty, skip device mounts entirely.

See: `lxc-container-patterns` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/homeassistant_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `service_nodes`, tagged `[homeassistant]`,
  in Phase 3
- deploy_stamp included as last role in the provision play
- Dynamic group `homeassistant` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure project 00):
- `homeassistant_ct_id: 200` in `group_vars/all.yml`
- `service_nodes` flavor group and `homeassistant` dynamic group in `inventory/hosts.yml`
- `service_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/homeassistant_lxc/defaults/main.yml`:
  - `homeassistant_ct_hostname: homeassistant`
  - `homeassistant_ct_memory: 1024`, `homeassistant_ct_cores: 2`, `homeassistant_ct_disk: "8"`
  - `homeassistant_ct_template: "{{ homeassistant_lxc_template }}"` (custom Docker image)
  - `homeassistant_ct_template_path: "{{ homeassistant_lxc_template_path }}"`
  - `homeassistant_ct_onboot: true`, `homeassistant_ct_startup_order: 5`
  - `homeassistant_ct_features: ["nesting=1"]` — required for Docker in LXC
  - `homeassistant_ct_ip_offset: "{{ homeassistant_ct_ip_offset | default(14) }}"`
  - `homeassistant_usb_devices: []` — list of /dev/ttyUSB* for Zigbee/Z-Wave; empty = no USB passthrough
- [ ] Create `roles/homeassistant_lxc/tasks/main.yml`:
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Compute container IP from LAN prefix + offset (LAN-only, no WAN branching)
  - Include `proxmox_lxc` with `lxc_ct_features: "{{ homeassistant_ct_features }}"` (nesting=1)
  - Pass `lxc_ct_mount_entries` built from `homeassistant_usb_devices` only when non-empty
  - Configure cgroup delegation for Docker daemon (cgroup v2 key=value in container config)
- [ ] Create `roles/homeassistant_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `service_nodes`,
  tagged `[homeassistant]`, with `homeassistant_lxc` role and `deploy_stamp`
- [ ] Add configure play to `site.yml` Phase 3, targeting `homeassistant`
  dynamic group, tagged `[homeassistant]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_homeassistant_group.yml`:
  - Verify container 200 is running (`pct status {{ homeassistant_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ homeassistant_ct_id }}`,
    `ansible_user: root`

**Verify:**

- [ ] Container 200 is running: `pct status 200` returns `running`
- [ ] Container is in `homeassistant` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Nesting enabled: `pct config 200` shows `features: nesting=1`
- [ ] Auto-start configured: `pct config 200` shows `onboot: 1`,
  `startup: order=5`
- [ ] Correct static IP matches computed offset
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

Configure the running container with Home Assistant via Docker Compose
and host-specific settings. Docker CE is already baked into the image (M0).
This role only applies host-specific config: compose template, admin
credentials, and HA configuration.yaml.

See: `lxc-container-patterns` skill (LXC configure connection, pct_remote pattern).

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
- [ ] Create `roles/homeassistant_configure/tasks/main.yml` (via `pct_remote`):
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

**What is NOT in this role (baked into image M0):**
- Docker CE packages and daemon — baked
- docker-compose plugin — baked
- HA container image (pre-pulled) — baked

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
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for fast HA-only iteration, extend
`molecule/default/verify.yml` for full integration, add rollback plays
to `playbooks/cleanup.yml`, and run final validation.

See: `molecule-testing` skill (per-feature scenario setup, baseline workflow),
`molecule-verify` skill (verify completeness), `molecule-cleanup` skill (cleanup completeness).

#### 3a. Per-feature scenario: `molecule/homeassistant-lxc/`

Covers container provisioning + configuration. Only touches VMID 200.
Assumes baseline exists (OpenWrt running, LAN bridge up).

- [ ] Create `molecule/homeassistant-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - service_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      HA_ADMIN_PASSWORD: ${HA_ADMIN_PASSWORD:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/homeassistant-lxc/converge.yml`:
  ```yaml
  - name: Provision Home Assistant LXC container
    hosts: service_nodes
    gather_facts: false
    roles:
      - homeassistant_lxc

  - name: Reconstruct homeassistant dynamic group
    hosts: service_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_homeassistant_group.yml

  - name: Configure Home Assistant
    hosts: homeassistant
    gather_facts: true
    roles:
      - homeassistant_configure
  ```

- [ ] Create `molecule/homeassistant-lxc/verify.yml`
- [ ] Create `molecule/homeassistant-lxc/cleanup.yml`:
  Destroys only container 200.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Home Assistant assertions:
  - Container 200 running, nesting=1, onboot=1, startup order=5
  - Docker daemon active, HA container running
  - Web UI on port 8123, API returns valid response
  - deploy_stamp contains `homeassistant_lxc` entry

- [ ] Verify generic container cleanup handles VMID 200

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `homeassistant-rollback` play:
  ```yaml
  - name: Rollback Home Assistant container
    hosts: service_nodes
    gather_facts: false
    tags: [homeassistant-rollback, never]
    tasks:
      - name: Stop and destroy Home Assistant container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ homeassistant_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ homeassistant_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Molecule env passthrough

- [ ] Add `HA_ADMIN_PASSWORD` to `molecule/default/molecule.yml`
  `provisioner.env` (optional, empty for tests)

#### 3e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s homeassistant-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Home Assistant artifacts on host or controller

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/homeassistant-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - Docker-in-LXC: nesting=1, cgroup delegation
  - Docker pull as documented exception to bake principle
  - USB passthrough: optional, empty list handling
  - Backup/restore strategy
  - Baked config vs runtime config split
  - Test vs production workflow differences
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Home Assistant provision + configure plays
  - Role catalog: add homeassistant_lxc, homeassistant_configure
- [ ] Update `docs/architecture/roles.md`:
  - Add `homeassistant_lxc` role documentation (purpose, nesting, USB passthrough)
  - Add `homeassistant_configure` role documentation (purpose, env vars, Docker compose)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Home Assistant project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented (HA_ADMIN_PASSWORD, auto-generation for testing)
- [ ] USB passthrough optional behavior documented
- [ ] Docker-in-LXC exception to bake principle documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Kiosk dashboard**: The Custom UX Kiosk (project 12) displays a Home
  Assistant Lovelace dashboard. Kiosk's `kiosk_dashboard_url` points to
  this container's web UI.
- **rsyslog**: Home Assistant logs can be forwarded to the rsyslog collector.
  Docker logging driver can be configured to send to syslog.
- **WireGuard remote access**: Remote access to the HA web UI routes through
  the WireGuard tunnel.
- **USB device expansion**: Additional Zigbee/Z-Wave dongles can be added
  via `homeassistant_usb_devices` in `host_vars`. The provisioning role
  dynamically builds mount entries from this list.

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
- Network: bridge selected by host topology, static IP
- VMID: 501

## Startup

- Auto-start: yes
- Boot priority: 3 (available early for debugging subsequent deploys)
- Depends on: OpenWrt Router (on LAN hosts), upstream gateway (on WAN hosts)

## Build Profiles

- Home Entertainment Box: yes (`monitoring_nodes`)
- Minimal Router: yes (`monitoring_nodes`)
- Gaming Rig: yes (`monitoring_nodes`) — all builds get logging

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network) — required only on LAN-connected hosts
- WireGuard VPN (project 02) — **soft dependency**: forwarding uses tunnel
  when available; rsyslog works fully without it (local collection + buffer)
- `rsyslog_ct_id: 501` already in `group_vars/all.yml`
- `monitoring_nodes` flavor group and `rsyslog` dynamic group already in `inventory/hosts.yml`
- `monitoring_nodes` already in `molecule/default/molecule.yml` platform groups (under `home`)
- `proxmox_lxc` role operational with `pct_remote` connection support
- Debian 12 standard template in `images/` (base for custom image build)

## Network topology assumption

`monitoring_nodes` hosts may be behind OpenWrt OR directly on the WAN.
In the current inventory only `home` is in `monitoring_nodes` (behind
OpenWrt = LAN), but the target architecture includes `monitoring_nodes`
on ALL build profiles — including Gaming Rig, which has no OpenWrt
router. When `monitoring_nodes` expands to WAN-connected hosts, the
provisioning role MUST branch on topology (identical to the WireGuard
pattern).

| Host topology | Bridge | Subnet | Gateway | DNS |
|---------------|--------|--------|---------|-----|
| Behind OpenWrt (`router_nodes`, `lan_hosts`) | `proxmox_all_bridges[1]` (LAN) | OpenWrt LAN (e.g., `10.10.10.0/24`) | LAN gateway from `env_generated_path` | LAN gateway |
| Directly on WAN (all others) | `proxmox_wan_bridge` | Host's WAN subnet | `ansible_default_ipv4.gateway` | `8.8.8.8` |

WAN-connected containers use IP offset +200 to avoid collisions with
LAN containers. See Container IP addressing in M1 for the full scheme.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness, image management |
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
│   ├── Pre-installed on Debian — baseline is baked into custom image
│   └── Minimal footprint (~10 MB RAM), mature, RFC 5424 support
│
├── LXC base: Custom Debian 12 template with rsyslog pre-configured
│   ├── "Bake, don't configure at runtime" — TCP listener, spool dir,
│   │   logrotate all pre-configured in the image
│   └── Configure role only applies host-specific forwarding rules
│
├── Image build: Debian 12 standard + rsyslog config in build-images.sh
│   ├── Simpler than Pi-hole — rsyslog is already in Debian
│   ├── Build pre-configures: imtcp module, spool directory, logrotate
│   └── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole)
│
├── Transport: TCP 514 with disk-assisted queue
│   └── Reliable delivery without RELP complexity; disk queue handles tunnel outages
│
├── Log format: RFC 5424 structured syslog
│   └── Standard, parseable by any central log server
│
├── Container networking: host topology-aware (LAN vs WAN)
│   ├── LAN hosts: container on LAN bridge, OpenWrt LAN subnet
│   ├── WAN hosts: container on WAN bridge, host's WAN subnet
│   └── Same branching pattern as WireGuard provisioning
│
├── LXC features: none required
│   └── rsyslog is a pure userspace daemon — no iptables, no cgroups, no nesting
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

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, rsyslog provisions on `monitoring_nodes` (currently
`home` only). It runs alongside other Phase 3 plays — WireGuard containers
deploy on all 4 nodes in parallel, unaffected by rsyslog's single-node
deployment.

Play ordering within Phase 3: rsyslog should provision early so log
collection is available when later services start. Place rsyslog plays
before WireGuard in Phase 3.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/rsyslog-lxc/` which only touches
VMID 501. The OpenWrt baseline, WireGuard containers, and Pi-hole stay
running.

```
Scenario Hierarchy (rsyslog additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including rsyslog provision + configure
│
├── molecule/rsyslog-lxc/            rsyslog container only (~30-60s)
│   ├── converge: provision + configure rsyslog container
│   ├── verify: rsyslog-specific assertions
│   └── cleanup: destroy container 501 only (baseline untouched)
│
└── molecule/openwrt-syslog/         OpenWrt syslog forwarding only (~30s)
    ├── converge: reconstruct openwrt group, apply syslog.yml
    ├── verify: UCI log_ip/log_port, log messages in rsyslog container
    └── cleanup: rollback UCI syslog config (rsyslog container untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                            # ~4-5 min, all 4 nodes

# 2. Iterate on rsyslog container (only touches VMID 501)
molecule converge -s rsyslog-lxc             # ~30s, provision + configure
molecule verify -s rsyslog-lxc               # ~10s, assertions only
# ... make changes to rsyslog_lxc or rsyslog_configure ...
molecule converge -s rsyslog-lxc             # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s rsyslog-lxc              # destroys container 501 only

# 4. Final validation before commit
molecule test                                # full clean-state, ~4-5 min
molecule converge                            # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `rsyslog-lxc` | Container 501 | Container 501 only | None — OpenWrt, WireGuard, Pi-hole untouched |
| `openwrt-syslog` | UCI syslog config | UCI syslog config only | None — container 501 stays running |

### Cross-scenario isolation

rsyslog's two per-feature scenarios are independent:
- `rsyslog-lxc` can run without `openwrt-syslog` (container works, just no
  log forwarding from OpenWrt)
- `openwrt-syslog` requires rsyslog container running (converge will fail-fast
  if 501 is absent)
- Neither touches WireGuard (101), Pi-hole (102), mesh (103), or OpenWrt VM (100)

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M1: Provisioning ── depends on M0
      └── M2: Configuration ── depends on M1
           └── M3: OpenWrt Syslog Forwarding ── depends on M2 + OpenWrt baseline (per-feature, opt-in)
                └── M4: Testing & Integration ── depends on M1–M3
                     └── M5: Documentation ── depends on M1–M4

Deferred to downstream projects:
  - Pi-hole FTL syslog forwarding → pihole_configure (Pi-hole project)
  - WireGuard syslog forwarding → wireguard_configure (WireGuard project)
  - Per-service log client config → each service's own configure role
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with rsyslog pre-configured for
TCP log reception, spool directory, and logrotate. Per the project's
"Bake, don't configure at runtime" principle, all base configuration
belongs in the image. The configure role (M2) only applies host-specific
forwarding rules.

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add rsyslog image build section to `build-images.sh`
- Template path: `images/rsyslog-debian-12-amd64.tar.zst`
- Template vars: `rsyslog_lxc_template` and `rsyslog_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole build). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Configure rsyslog inside the container:
   - Enable `imtcp` module for TCP reception on port 514
   - Create `/var/spool/rsyslog/` spool directory
   - Configure disk-assisted queue defaults
   - Set up logrotate (7-day retention, compress, daily rotation)
   - Restrict TCP reception to RFC 1918 subnets (safe default)
3. Clean apt caches, stop container
4. Export via `vzdump` and download template

Unlike Pi-hole, no package installation is needed — rsyslog and logrotate
are already in the Debian standard template. The build only pre-configures.

- [ ] Add rsyslog template build section to `build-images.sh`
  (follow Pi-hole pattern: `build_rsyslog_lxc` function)
- [ ] Add `rsyslog_lxc_template` and `rsyslog_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Add `rsyslog_ct_ip_offset: 11` to `group_vars/all.yml`
  (after Pi-hole at 10; WAN offset: 211)
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/rsyslog-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains pre-configured rsyslog TCP listener
- [ ] Spool directory `/var/spool/rsyslog/` exists in template
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built)._

Create the `rsyslog_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision and configure plays to `site.yml`, and verify the
container runs. Integration with `site.yml` is consolidated here (not
deferred to a separate milestone).

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp,
LXC container networking).

**Implementation pattern:**
- Role: `roles/rsyslog_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `monitoring_nodes`, tagged `[monitoring]`,
  in Phase 3 before WireGuard (per target architecture: Observability Tier)
- deploy_stamp included as last role in the provision play
- Dynamic group `rsyslog` populated by `proxmox_lxc` via `add_host`

**Container IP addressing:**

rsyslog uses a static IP computed from the host's network topology
(identical to the WireGuard pattern). Default offset: 11.

| Host topology | IP computation | Example |
|---------------|---------------|---------|
| Behind OpenWrt | `<LAN_prefix>.{{ rsyslog_ct_ip_offset }}` | `10.10.10.11/24` |
| Directly on WAN | `<WAN_prefix>.{{ rsyslog_ct_ip_offset + 200 }}` | `192.168.86.211/24` |

The IP is read from `env_generated_path` (LAN_GATEWAY) for LAN hosts,
and from `ansible_default_ipv4` for WAN hosts, with defaults for
first-run scenarios.

**Already complete** (from shared infrastructure project 00):
- `rsyslog_ct_id: 501` in `group_vars/all.yml`
- `monitoring_nodes` flavor group and `rsyslog` dynamic group in `inventory/hosts.yml`
- `monitoring_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/rsyslog_lxc/defaults/main.yml`:
  - `rsyslog_ct_hostname: rsyslog`
  - `rsyslog_ct_memory: 64`, `rsyslog_ct_cores: 1`, `rsyslog_ct_disk: "1"`
  - `rsyslog_ct_template: "{{ rsyslog_lxc_template }}"` (custom rsyslog image)
  - `rsyslog_ct_template_path: "{{ rsyslog_lxc_template_path }}"`
  - `rsyslog_ct_onboot: true`, `rsyslog_ct_startup_order: 3`
  - `rsyslog_ct_ip_offset: "{{ rsyslog_ct_ip_offset | default(11) }}"`
  - No `lxc_ct_features` needed (rsyslog is pure userspace)
- [ ] Create `roles/rsyslog_lxc/tasks/main.yml`:
  - Read LAN gateway/CIDR from `env_generated_path` (same pattern as WireGuard)
  - Branch on host topology: compute container IP, bridge, gateway, DNS
    per the network topology table above
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` with static IP on the appropriate bridge
  - For LAN hosts: DNS set to LAN gateway (OpenWrt)
  - For WAN hosts: DNS set to `8.8.8.8`
- [ ] Create `roles/rsyslog_lxc/meta/main.yml` with required metadata
  (`author`, `license: proprietary`, `role_name`, `description`,
  `min_ansible_version`, `platforms`)
- [ ] Add provision play to `site.yml` Phase 3, targeting `monitoring_nodes`,
  tagged `[monitoring]`, with `rsyslog_lxc` role and `deploy_stamp`
  (before WireGuard, after Pi-hole — per target architecture observability tier)
- [ ] Add configure play to `site.yml` Phase 3, targeting `rsyslog` dynamic
  group, tagged `[monitoring]`, `gather_facts: true`, after the provision play
- [ ] Create `tasks/reconstruct_rsyslog_group.yml`:
  - Verify container 501 is running (`pct status {{ rsyslog_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ rsyslog_ct_id }}`,
    `ansible_user: root`

**Note on `[monitoring]` tag:** This tag is shared with Netdata (per the
target site.yml architecture, both rsyslog and netdata provision in the
same play on `monitoring_nodes`). Until Netdata is implemented, the tag
exclusively governs rsyslog. When Netdata is added, the provision play
will include both `rsyslog_lxc` and `netdata_lxc` roles — configure plays
remain separate since they target different dynamic groups.

**Verify:**

- [ ] Container 501 is running: `pct status 501` returns `running`
- [ ] Container is in `rsyslog` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 501` shows `onboot: 1`,
  `startup: order=3`
- [ ] Correct bridge assignment (LAN bridge on LAN hosts, WAN bridge on WAN hosts)
- [ ] Correct static IP matches computed offset
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

Configure the running container with rsyslog forwarding rules (when
`RSYSLOG_HOME_SERVER` is set). The base rsyslog TCP receiver, spool
directory, and logrotate are already baked into the image (M0). This
role only applies host-specific forwarding config.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/rsyslog_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/20-forward.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `rsyslog` dynamic group, tagged
  `[monitoring]`, after the provision play

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `RSYSLOG_HOME_SERVER` | no | Forward logs to home server via WireGuard | `10.0.0.1` |

When empty: local collection + buffer only. rsyslog works fully without it.
Soft-dep on WireGuard: forwarding uses tunnel when available.
NEVER add to `REQUIRED_ENV` in `build.py` — monitoring is flavor-specific.

- [ ] Create `roles/rsyslog_configure/defaults/main.yml`:
  - `rsyslog_home_server: "{{ lookup('env', 'RSYSLOG_HOME_SERVER') | default('', true) }}"`
- [ ] Create `roles/rsyslog_configure/tasks/main.yml` (via `pct_remote`):
  - Template `/etc/rsyslog.d/20-forward.conf` (conditional on
    `rsyslog_home_server | length > 0`):
    - Forward to home server via WireGuard tunnel
    - Disk-assisted queue for reliability during outages
  - When `rsyslog_home_server` is empty: ensure `20-forward.conf` is absent
  - Restart rsyslog inside the container: handler or
    `pct exec 501 -- systemctl restart rsyslog`
- [ ] Create `roles/rsyslog_configure/templates/20-forward.conf.j2`
- [ ] Create `roles/rsyslog_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- TCP listener config (`/etc/rsyslog.d/10-receive.conf`) — baked
- Spool directory (`/var/spool/rsyslog/`) — baked
- Logrotate config — baked
- `imtcp` module loading — baked

**Verify:**

- [ ] rsyslog listening on TCP 514: `pct exec 501 -- ss -tlnp | grep 514`
- [ ] `/etc/rsyslog.d/10-receive.conf` exists (baked in image)
- [ ] When `RSYSLOG_HOME_SERVER` set: `/etc/rsyslog.d/20-forward.conf` exists
  with correct target
- [ ] When `RSYSLOG_HOME_SERVER` empty: `20-forward.conf` absent
- [ ] Local spool directory exists: `pct exec 501 -- test -d /var/spool/rsyslog`
- [ ] rsyslog service active: `pct exec 501 -- systemctl is-active rsyslog`
- [ ] Logrotate config present
- [ ] Idempotent: second run produces no changes

**Rollback:**

- Remove forwarding config: `pct exec 501 -- rm -f /etc/rsyslog.d/20-forward.conf`
- Restart rsyslog: `pct exec 501 -- systemctl restart rsyslog`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: OpenWrt Syslog Forwarding

_Depends on M2 (rsyslog must be running) and OpenWrt baseline.
Per-feature, opt-in via `--tags openwrt-syslog`._

Configure OpenWrt to forward its system logs to the rsyslog collector.
This is a **post-baseline feature play** within `openwrt_configure` — it
modifies the existing role via a separate task file, not a new role.
Follows the Pi-hole DNS forwarding pattern exactly.

See: `openwrt-build` skill (UCI syslog configuration, post-baseline
feature pattern).

**Implementation pattern:**
- Task file: `roles/openwrt_configure/tasks/syslog.yml`
- site.yml: three plays in the per-feature section (tagged `[never]`):
  (0) Reconstruct openwrt group, tagged
  `[openwrt-syslog, openwrt-syslog-rollback, never]`
  (1) Configure on `openwrt` dynamic group with `tasks_from: syslog.yml`,
  tagged `[openwrt-syslog, never]`
  (2) deploy_stamp on `router_nodes`, tagged `[openwrt-syslog, never]`
- playbooks/cleanup.yml: add `openwrt-syslog-rollback` to the existing
  openwrt reconstruction play's tag list, plus a new rollback play

**rsyslog IP resolution:**
`rsyslog_static_ip` is computed in the task file from `env_generated_path`
(LAN_GATEWAY prefix + `rsyslog_ct_ip_offset`), consistent with M1's IP
scheme.

- [ ] Create `roles/openwrt_configure/tasks/syslog.yml`:
  - Read `LAN_GATEWAY` from `env_generated_path` to derive rsyslog IP
  - `uci set system.@system[0].log_ip=<rsyslog_ip>`
  - `uci set system.@system[0].log_port=514`
  - `uci set system.@system[0].log_proto=tcp`
  - `uci commit system`
  - `/etc/init.d/log restart`
- [ ] Add reconstruction play to `site.yml` per-feature section,
  tagged `[openwrt-syslog, openwrt-syslog-rollback, never]`
- [ ] Add configure play to `site.yml` per-feature section,
  tagged `[openwrt-syslog, never]`
- [ ] Add deploy_stamp play to `site.yml` per-feature section,
  tagged `[openwrt-syslog, never]`
- [ ] Add `openwrt-syslog-rollback` to the existing openwrt
  reconstruction play's tag list in `playbooks/cleanup.yml`
- [ ] Add rollback play to `playbooks/cleanup.yml` (see Rollback below)

**Verify:**

- [ ] OpenWrt log_ip set to rsyslog container IP:
  `uci get system.@system[0].log_ip`
- [ ] OpenWrt log_port set to 514:
  `uci get system.@system[0].log_port`
- [ ] Log messages from OpenWrt appear in rsyslog container:
  send a test log via `logread -f` and verify in container
- [ ] deploy_stamp contains `openwrt_syslog` play entry

**Rollback (`--tags openwrt-syslog-rollback`):**

- Remove syslog remote settings:
  `uci delete system.@system[0].log_ip`
  `uci delete system.@system[0].log_port`
  `uci delete system.@system[0].log_proto`
  `uci commit system && /etc/init.d/log restart`
- Add rollback play in `playbooks/cleanup.yml`:
  - Reconstruct openwrt group (already handled by shared reconstruction play)
  - Remove syslog forwarding settings
  - `uci commit system && /etc/init.d/log restart`

---

### Milestone 4: Testing & Integration

_Depends on M1–M3._

Create per-feature molecule scenario for fast rsyslog-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, add molecule env passthrough, and run
final validation.

See: `ansible-testing` skill (verify completeness, per-feature scenario
setup, baseline workflow), `rollback-patterns` skill (cleanup completeness).

#### 4a. Per-feature scenario: `molecule/rsyslog-lxc/`

Covers container provisioning + configuration. Only touches VMID 501.
Assumes baseline exists (OpenWrt running, LAN bridge up). Follows the
Pi-hole `molecule/pihole-lxc/` pattern exactly.

- [ ] Create `molecule/rsyslog-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - monitoring_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      RSYSLOG_HOME_SERVER: ${RSYSLOG_HOME_SERVER:-}
  scenario:
    test_sequence:    # no initial cleanup — baseline must exist
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```
  Only `home` in `monitoring_nodes`. Single-node — fast.

- [ ] Create `molecule/rsyslog-lxc/converge.yml`:
  ```yaml
  - name: Provision rsyslog LXC container
    hosts: monitoring_nodes
    gather_facts: false
    roles:
      - rsyslog_lxc

  - name: Reconstruct rsyslog dynamic group
    hosts: monitoring_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_rsyslog_group.yml

  - name: Configure rsyslog
    hosts: rsyslog
    gather_facts: true
    roles:
      - rsyslog_configure
  ```
  Provision → reconstruct → configure. No OpenWrt, no WireGuard, no Pi-hole.

- [ ] Create `molecule/rsyslog-lxc/verify.yml`:
  rsyslog-specific assertions only. Runs on `monitoring_nodes` (Proxmox host)
  using `pct exec` — no dynamic group needed for most checks:
  - Container 501 running, onboot=1, startup order=3
  - rsyslog service active
  - Listening on TCP 514
  - Spool directory exists
  - Test log message received: send from Proxmox host via
    `logger --tcp -n <container_ip> -P 514`, verify in container log
  - deploy_stamp contains `rsyslog_lxc` entry

- [ ] Create `molecule/rsyslog-lxc/cleanup.yml`:
  Destroys only container 501. Does NOT touch OpenWrt, WireGuard, or Pi-hole:
  ```yaml
  - name: Destroy rsyslog container
    hosts: monitoring_nodes
    gather_facts: false
    tasks:
      - name: Stop rsyslog container
        ansible.builtin.command:
          cmd: pct stop {{ rsyslog_ct_id }}
        failed_when: false
        changed_when: true

      - name: Wait for container to stop
        ansible.builtin.pause:
          seconds: 2

      - name: Destroy rsyslog container
        ansible.builtin.command:
          cmd: pct destroy {{ rsyslog_ct_id }} --purge
        failed_when: false
        changed_when: true
  ```
  No host-side files to clean (rsyslog has no kernel modules or modprobe
  config — everything lives inside the container).

#### 4b. Per-feature scenario: `molecule/openwrt-syslog/`

Covers the OpenWrt syslog forwarding feature (M3). Only touches UCI
config on the OpenWrt VM. Assumes both the OpenWrt baseline AND rsyslog
container are running. Follows the `openwrt-pihole-dns` pattern.

- [ ] Create `molecule/openwrt-syslog/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - router_nodes
        - monitoring_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      MESH_KEY: ${MESH_KEY}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/openwrt-syslog/converge.yml`:
  ```yaml
  - name: Reconstruct openwrt dynamic group
    hosts: router_nodes
    gather_facts: true
    tasks:
      - name: Verify VM 100 is running
        ansible.builtin.command:
          cmd: qm status 100
        register: _vm_status
        changed_when: false
        failed_when: "'running' not in _vm_status.stdout"

      - name: Include reusable group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

  - name: Apply syslog forwarding
    hosts: openwrt
    gather_facts: false
    tasks:
      - name: Include syslog tasks
        ansible.builtin.include_role:
          name: openwrt_configure
          tasks_from: syslog.yml
  ```

- [ ] Create `molecule/openwrt-syslog/verify.yml`:
  Reconstruct openwrt group, then verify UCI log_ip/log_port settings
  and test log message reception in rsyslog container.

- [ ] Create `molecule/openwrt-syslog/cleanup.yml`:
  ```yaml
  - name: Rollback syslog forwarding
    ansible.builtin.import_playbook: ../../playbooks/cleanup.yml
    tags: [openwrt-syslog-rollback]
  ```
  Reverts UCI config only. rsyslog container and OpenWrt VM stay up.

#### 4c. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with rsyslog assertions
  (new play targeting `monitoring_nodes`):
  - Container 501 running, onboot=1, startup order=3
  - rsyslog service active, TCP 514 listening
  - Spool directory exists
  - Test log reception from Proxmox host
  - deploy_stamp contains `rsyslog_lxc` entry
  These assertions run as part of the full 4-node integration and execute
  on `home` while WireGuard assertions run on all 4 nodes in parallel.

- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles container 501 (`pct list` iteration — confirm no changes needed)

#### 4d. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `rsyslog-rollback` play:
  - Stop and destroy rsyslog container (`pct stop 501 && pct destroy 501`)
  - Tagged `[rsyslog-rollback, never]`
  - Runs on `monitoring_nodes` (no dynamic group needed — operates on
    Proxmox host)
  ```yaml
  - name: Rollback rsyslog container
    hosts: monitoring_nodes
    gather_facts: false
    tags: [rsyslog-rollback, never]
    tasks:
      - name: Stop and destroy rsyslog container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ rsyslog_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ rsyslog_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

- [ ] Add `rsyslog-rollback` to the cached template removal task in
  `playbooks/cleanup.yml` (add `rsyslog-*.tar.zst` to the glob)

Now add the `openwrt-syslog-rollback` to the reconstruction play and add
the rollback play itself:

- [ ] Add `openwrt-syslog-rollback` to the existing openwrt
  reconstruction play's tag list in `playbooks/cleanup.yml`

- [ ] Add `openwrt-syslog-rollback` play:
  - Remove syslog forwarding settings from UCI
  - `uci commit system && /etc/init.d/log restart`
  - Tagged `[openwrt-syslog-rollback, never]`

#### 4e. Molecule env passthrough

- [ ] Add `RSYSLOG_HOME_SERVER` to `molecule/default/molecule.yml`
  `provisioner.env` (optional, empty for tests):
  ```yaml
  RSYSLOG_HOME_SERVER: ${RSYSLOG_HOME_SERVER:-}
  ```

#### 4f. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s rsyslog-lxc` — per-feature cycle passes
- [ ] Run `molecule test -s openwrt-syslog` — syslog forwarding cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no rsyslog artifacts on host or controller
- [ ] Baseline workflow: after `molecule test`, `molecule converge` restores
  baseline; then `molecule converge -s rsyslog-lxc` works without full rebuild

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, service state,
  TCP listener, log reception, spool directory
- [ ] Cleanup leaves no rsyslog artifacts on host (container destroyed via
  generic pct iteration)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 5: Documentation

_Depends on M1–M4._

- [ ] Create `docs/architecture/rsyslog-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - Log flow and client integration pattern
  - RSYSLOG_HOME_SERVER optional behavior, WireGuard soft dependency
  - Baked config vs runtime config split
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add rsyslog provision + configure plays in Phase 3
  - Role catalog: add rsyslog_lxc, rsyslog_configure
  - Verify rsyslog container in topology diagrams
- [ ] Update `docs/architecture/roles.md`:
  - Add rsyslog_lxc role documentation (purpose, key variables)
  - Add rsyslog_configure role documentation (purpose, env vars, templates)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add rsyslog project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with optional behavior
- [ ] `roles.md` entries match actual role exports

**Rollback:** N/A — documentation-only milestone.

---

## Log Client Pattern (downstream services)

OpenWrt syslog forwarding is implemented in M3 of this project (following
the Pi-hole DNS forwarding precedent — each service owns its OpenWrt
integration).

Other service log forwarding is deferred to each service's own project.
Each service decides how to forward its own logs to the rsyslog collector.

**Deferred integration points:**
- **Pi-hole**: FTL syslog forwarding via `pihole-FTL --config`.
  Implemented in `pihole_configure` (Pi-hole project).
- **WireGuard**: Standard rsyslog client config in the container.
  Implemented in `wireguard_configure` (WireGuard project).
- **Future services**: Each service's configure role templates a syslog
  forwarding snippet when `rsyslog_ct_ip_offset` is defined in
  `group_vars/all.yml`.

**Service discovery:** rsyslog's static IP (from this project's offset
scheme) is the stable endpoint. Downstream services reference it via
`rsyslog_ct_ip_offset` in `group_vars/all.yml` and the same IP computation
used by the provisioning role.

---

## Future Integration Considerations

- **Netdata**: Both rsyslog and Netdata share `monitoring_nodes` and the
  `[monitoring]` tag. When Netdata is implemented, the provision play
  includes both `rsyslog_lxc` and `netdata_lxc` roles. Configure plays
  remain separate. Netdata may also want to monitor rsyslog's health.
- **Multi-node expansion**: When `monitoring_nodes` expands beyond `home`,
  each node gets its own rsyslog container. The WAN/LAN topology branching
  (M1) handles IP and bridge assignment automatically.
- **WireGuard tunnel forwarding**: When rsyslog forwarding is enabled
  (`RSYSLOG_HOME_SERVER`), logs route through the WireGuard tunnel on the
  same host. The rsyslog container's gateway reaches the WireGuard container
  via the shared bridge.
- **Log aggregation across nodes**: Each node collects its own logs locally.
  Cross-node aggregation (all logs to a single rsyslog instance) is a future
  enhancement requiring WireGuard site-to-site tunnels. Not in scope for
  this project.
- **VLANs**: If VLAN segmentation is deployed (project 01, M2), rsyslog
  may need firewall rules allowing syslog from VLAN subnets. This is a
  downstream concern for the VLAN project.

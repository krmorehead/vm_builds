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
- Network: bridge selected by host topology, static IP
- VMID: 500

## Startup

- Auto-start: yes
- Boot priority: 3 (alongside Pi-hole and rsyslog)
- Depends on: OpenWrt Router (on LAN hosts), upstream gateway (on WAN hosts)

## Build Profiles

- Home Entertainment Box: yes (`monitoring_nodes`)
- Minimal Router: yes (`monitoring_nodes`)
- Gaming Rig: yes (`monitoring_nodes`) — all builds get monitoring

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- OpenWrt router operational (network) — required only on LAN-connected hosts
- WireGuard VPN (project 02) — **soft dependency**: streaming uses tunnel
  when available; Netdata functions fully as local dashboard without it
- `netdata_ct_id: 500` already in `group_vars/all.yml`
- `monitoring_nodes` flavor group and `netdata` dynamic group already in `inventory/hosts.yml`
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
and rsyslog pattern).

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
├── Monitoring stack: Netdata child-parent streaming
│   └── Richer out-of-box than Prometheus+Grafana; built-in dashboards; child-parent fits remote topology
│
├── LXC base: Custom Debian 12 template with Netdata pre-installed
│   ├── "Bake, don't configure at runtime" — Netdata packages baked into image
│   └── Configure role only applies host-specific streaming config
│
├── Image build: Debian 12 standard + Netdata kickstart in build-images.sh
│   ├── Remote build on Proxmox via pct create/exec/vzdump (same as Pi-hole, rsyslog)
│   └── Build installs Netdata, pre-configures dbengine retention and proc/sys paths
│
├── Host metrics access: bind mount /proc and /sys read-only into LXC
│   └── Needed for accurate host CPU, memory, disk, temperature; Proxmox API lacks per-interface and thermal data
│   └── Bind mounts passed to proxmox_lxc via role vars: lxc_ct_mount_entries with full spec
│       (e.g., /proc,mp=/host/proc,ro=1 and /sys,mp=/host/sys,ro=1)
│
├── Container networking: host topology-aware (LAN vs WAN)
│   ├── LAN hosts: container on LAN bridge, OpenWrt LAN subnet
│   ├── WAN hosts: container on WAN bridge, host's WAN subnet
│   └── Same branching pattern as WireGuard and rsyslog provisioning
│
├── LXC features: none required
│   └── Netdata is a pure userspace daemon — no iptables, no cgroups, no nesting
│
├── WireGuard dependency: soft (optional)
│   └── Functions fully as local dashboard; streaming activates when parent is reachable
│
└── Data retention: minimal on child (dbengine, 1 hour)
    └── Parent handles long-term storage; child is ephemeral
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Netdata provisions on `monitoring_nodes` (currently
`home` only). It runs alongside other Phase 3 plays — rsyslog and
WireGuard containers deploy in the same phase. Netdata and rsyslog
share the `[monitoring]` tag and provision in the same play on
`monitoring_nodes`.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/netdata-lxc/` which only touches
VMID 500. The OpenWrt baseline, WireGuard containers, rsyslog, and
Pi-hole stay running.

```
Scenario Hierarchy (Netdata additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Netdata provision + configure
│
└── molecule/netdata-lxc/            Netdata container only (~30-60s)
    ├── converge: provision + configure Netdata container
    ├── verify: Netdata-specific assertions
    └── cleanup: destroy container 500 only (baseline untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                            # ~4-5 min, all 4 nodes

# 2. Iterate on Netdata container (only touches VMID 500)
molecule converge -s netdata-lxc             # ~30s, provision + configure
molecule verify -s netdata-lxc               # ~10s, assertions only
molecule converge -s netdata-lxc             # ~30s, re-converge

# 3. Clean up per-feature changes (baseline stays)
molecule cleanup -s netdata-lxc              # destroys container 500 only

# 4. Final validation before commit
molecule test                                # full clean-state, ~4-5 min
molecule converge                            # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `netdata-lxc` | Container 500 | Container 500 only | None — OpenWrt, WireGuard, rsyslog, Pi-hole untouched |

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

Build a custom Debian 12 LXC template with Netdata pre-installed. Per the
project's "Bake, don't configure at runtime" principle, all packages belong
in the image. The configure role (M2) only applies host-specific streaming
config and bind mount paths.

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add Netdata image build section to `build-images.sh`
- Template path: `images/netdata-debian-12-amd64.tar.zst`
- Template vars: `netdata_lxc_template` and `netdata_lxc_template_path`
  in `group_vars/all.yml`

**Build approach:**
Remote build on Proxmox via `pct create` + `pct exec` + `vzdump` (same
pattern as Pi-hole and rsyslog). Steps:
1. Create temp container (VMID 998) from Debian 12 standard template
2. Install Netdata inside the container via official kickstart script
3. Pre-configure `netdata.conf`: dbengine retention (1 hour), proc/sys
   paths (`/host/proc`, `/host/sys`), web dashboard port 19999
4. Enable cgroups monitoring for per-container metrics
5. Clean apt caches, stop container
6. Export via `vzdump` and download template

- [ ] Add Netdata template build section to `build-images.sh`
  (follow Pi-hole/rsyslog pattern: `build_netdata_lxc` function)
- [ ] Add `netdata_lxc_template` and `netdata_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Add `netdata_ct_ip_offset: 12` to `group_vars/all.yml`
  (after rsyslog at 11; WAN offset: 212)
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/netdata-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Netdata packages pre-installed
- [ ] `netdata.conf` has correct proc/sys paths and retention defaults
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: Provisioning

_Depends on M0 (template must be built)._

Create the `netdata_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision and configure plays to `site.yml`, and verify the
container runs. Integration with `site.yml` is consolidated here.
Netdata and rsyslog provisioning are combined in the same site.yml
play (both target `monitoring_nodes`); configure plays are separate
(different dynamic groups: `netdata` vs `rsyslog`).

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp,
LXC container networking).

**Implementation pattern:**
- Role: `roles/netdata_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `monitoring_nodes`, tagged `[monitoring]`,
  in Phase 3 (combined with `rsyslog_lxc` in same play)
- deploy_stamp included as last role in the provision play
- Dynamic group `netdata` populated by `proxmox_lxc` via `add_host`
- Bind mounts: pass `lxc_ct_mount_entries` to `proxmox_lxc` via include_role vars

**Container IP addressing:**

Netdata uses a static IP computed from the host's network topology
(identical to the WireGuard and rsyslog pattern). Default offset: 12.

| Host topology | IP computation | Example |
|---------------|---------------|---------|
| Behind OpenWrt | `<LAN_prefix>.{{ netdata_ct_ip_offset }}` | `10.10.10.12/24` |
| Directly on WAN | `<WAN_prefix>.{{ netdata_ct_ip_offset + 200 }}` | `192.168.86.212/24` |

**Already complete** (from shared infrastructure / group_vars):
- `netdata_ct_id: 500` in `inventory/group_vars/all.yml`
- `monitoring_nodes` flavor group and `netdata` dynamic group in `inventory/hosts.yml`
- `monitoring_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_startup_order[500]: 3` in `inventory/group_vars/all.yml`
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/netdata_lxc/defaults/main.yml`:
  - `netdata_ct_hostname: netdata`
  - `netdata_ct_memory: 128`, `netdata_ct_cores: 1`, `netdata_ct_disk: "1"`
  - `netdata_ct_template: "{{ netdata_lxc_template }}"` (custom Netdata image)
  - `netdata_ct_template_path: "{{ netdata_lxc_template_path }}"`
  - `netdata_ct_onboot: true`, `netdata_ct_startup_order: 3`
  - `netdata_ct_ip_offset: "{{ netdata_ct_ip_offset | default(12) }}"`
  - `netdata_ct_mount_entries: ["/proc,mp=/host/proc,ro=1", "/sys,mp=/host/sys,ro=1"]`
  - No `lxc_ct_features` needed (Netdata is pure userspace)
- [ ] Create `roles/netdata_lxc/tasks/main.yml`:
  - Read LAN gateway/CIDR from `env_generated_path` (same pattern as rsyslog)
  - Branch on host topology: compute container IP, bridge, gateway, DNS
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` with static IP on the appropriate bridge,
    `lxc_ct_mount_entries: "{{ netdata_ct_mount_entries }}"`
- [ ] Create `roles/netdata_lxc/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `monitoring_nodes`,
  tagged `[monitoring]`, with `netdata_lxc` role and `deploy_stamp`
  (combined with `rsyslog_lxc` in same play)
- [ ] Add configure play to `site.yml` Phase 3, targeting `netdata` dynamic
  group, tagged `[monitoring]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_netdata_group.yml`:
  - Verify container 500 is running (`pct status {{ netdata_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ netdata_ct_id }}`,
    `ansible_user: root`

**Note on `[monitoring]` tag:** This tag is shared with rsyslog (per the
target site.yml architecture, both netdata and rsyslog provision in the
same play on `monitoring_nodes`). Configure plays remain separate since
they target different dynamic groups (`netdata` vs `rsyslog`).

**Verify:**

- [ ] Container 500 is running: `pct status 500` returns `running`
- [ ] Container is in `netdata` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 500` shows `onboot: 1`,
  `startup: order=3`
- [ ] Bind mounts present: `pct config 500` shows mp entries for
  `/proc`→`/host/proc` and `/sys`→`/host/sys` (read-only)
- [ ] Correct bridge assignment (LAN bridge on LAN hosts, WAN bridge on WAN hosts)
- [ ] Correct static IP matches computed offset
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `netdata_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup: none — Netdata does not deploy
host-side config files.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with host-specific streaming config and
optional parent connection. Netdata packages are already baked into the
image (M0). This role only applies host-specific streaming and dashboard
settings.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/netdata_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/stream.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `netdata` dynamic group, tagged
  `[monitoring]`, after the provision play

**Env variables** (all optional — soft dependency on WireGuard):

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `NETDATA_STREAM_API_KEY` | no | API key for parent streaming | `uuid-from-parent` |
| `NETDATA_PARENT_IP` | no | Parent Netdata IP (via WireGuard) | `10.0.0.1` |

Both are soft-deps: when unset, Netdata runs as local-only dashboard.
NEVER add to REQUIRED_ENV in build.py — monitoring is flavor-specific.

- [ ] Create `roles/netdata_configure/defaults/main.yml`:
  - `netdata_stream_api_key: "{{ lookup('env', 'NETDATA_STREAM_API_KEY') | default('', true) }}"`
  - `netdata_parent_ip: "{{ lookup('env', 'NETDATA_PARENT_IP') | default('', true) }}"`
- [ ] Create `roles/netdata_configure/tasks/main.yml` (via `pct_remote`):
  - Template `stream.conf` (conditional on `netdata_parent_ip | length > 0`):
    - Destination: parent via WireGuard tunnel
    - API key from env (`NETDATA_STREAM_API_KEY`)
    - Buffer on disconnect
  - When `netdata_parent_ip` is empty: ensure `stream.conf` is absent or disabled
  - Restart Netdata inside the container
- [ ] Create `roles/netdata_configure/templates/stream.conf.j2`
- [ ] Create `roles/netdata_configure/meta/main.yml` with required metadata

**What is NOT in this role (baked into image M0):**
- Netdata packages and service — baked
- `netdata.conf` base config (dbengine, retention, proc/sys paths) — baked
- Cgroups plugin configuration — baked
- Web dashboard on port 19999 — baked

**Verify:**

- [ ] Netdata service running: `pct exec 500 -- systemctl is-active netdata`
- [ ] Dashboard on port 19999: `pct exec 500 -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:19999`
- [ ] Host metrics visible: `/host/proc` and `/host/sys` exist and contain
  expected data (CPU, memory from host)
- [ ] `netdata.conf` has correct proc/sys paths and retention (baked in image)
- [ ] `stream.conf` present if `NETDATA_PARENT_IP` set; absent or disabled
  when empty
- [ ] Cgroups plugin enabled for per-container metrics (baked in image)
- [ ] Idempotent: second run does not break config

**Rollback:**

- Remove streaming config: `pct exec 500 -- rm -f /etc/netdata/stream.conf`
- Restart Netdata: `pct exec 500 -- systemctl restart netdata`
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for fast Netdata-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, add molecule env passthrough, and run
final validation.

See: `ansible-testing` skill (verify completeness, per-feature scenario
setup, baseline workflow), `rollback-patterns` skill (cleanup completeness).

#### 3a. Per-feature scenario: `molecule/netdata-lxc/`

Covers container provisioning + configuration. Only touches VMID 500.
Assumes baseline exists (OpenWrt running, LAN bridge up). Follows the
rsyslog `molecule/rsyslog-lxc/` pattern exactly.

- [ ] Create `molecule/netdata-lxc/molecule.yml`:
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
      NETDATA_STREAM_API_KEY: ${NETDATA_STREAM_API_KEY:-}
      NETDATA_PARENT_IP: ${NETDATA_PARENT_IP:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/netdata-lxc/converge.yml`:
  ```yaml
  - name: Provision Netdata LXC container
    hosts: monitoring_nodes
    gather_facts: false
    roles:
      - netdata_lxc

  - name: Reconstruct netdata dynamic group
    hosts: monitoring_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_netdata_group.yml

  - name: Configure Netdata
    hosts: netdata
    gather_facts: true
    roles:
      - netdata_configure
  ```

- [ ] Create `molecule/netdata-lxc/verify.yml`:
  Netdata-specific assertions. Runs on `monitoring_nodes` via `pct exec`.

- [ ] Create `molecule/netdata-lxc/cleanup.yml`:
  Destroys only container 500.

#### 3b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Netdata assertions:
  - Container 500 running, onboot=1, startup order=3
  - Netdata service active, dashboard on port 19999
  - Host metrics visible (CPU, memory from /host/proc)
  - Bind mounts present
  - deploy_stamp contains `netdata_lxc` entry

- [ ] Verify generic container cleanup handles VMID 500

#### 3c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `netdata-rollback` play:
  ```yaml
  - name: Rollback Netdata container
    hosts: monitoring_nodes
    gather_facts: false
    tags: [netdata-rollback, never]
    tasks:
      - name: Stop and destroy Netdata container
        ansible.builtin.shell:
          cmd: |
            pct stop {{ netdata_ct_id }} 2>/dev/null || true
            sleep 2
            pct destroy {{ netdata_ct_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Molecule env passthrough

- [ ] Add `NETDATA_STREAM_API_KEY` and `NETDATA_PARENT_IP` to
  `molecule/default/molecule.yml` `provisioner.env` (optional, empty)

#### 3e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s netdata-lxc` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Netdata artifacts on host or controller

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/netdata-build.md`:
  - Image build process (build-images.sh section)
  - Requirements, design decisions, env variables
  - Bind mount requirements (/proc, /sys read-only via proxmox_lxc vars)
  - Child-parent streaming flow and WireGuard soft dependency
  - Baked config vs runtime config split
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Netdata provision + configure plays
  - Verify monitoring topology includes Netdata container
- [ ] Update `docs/architecture/roles.md`:
  - Add `netdata_lxc` role documentation (purpose, bind mounts, key variables)
  - Add `netdata_configure` role documentation (purpose, env vars, streaming)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Netdata project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented with optional/soft-dependency behavior
- [ ] `roles.md` entries match actual role exports

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **rsyslog**: Both Netdata and rsyslog share `monitoring_nodes` and the
  `[monitoring]` tag. The provision play includes both roles. Configure
  plays remain separate. Netdata may want to forward its own logs to
  rsyslog.
- **Multi-node expansion**: When `monitoring_nodes` expands beyond `home`,
  each node gets its own Netdata container. The WAN/LAN topology branching
  (M1) handles IP and bridge assignment automatically.
- **WireGuard tunnel streaming**: When parent streaming is enabled
  (`NETDATA_PARENT_IP`), metrics route through the WireGuard tunnel on the
  same host.
- **Pi-hole**: Netdata can monitor Pi-hole's FTL service via its built-in
  Pi-hole plugin. Requires Pi-hole container IP discovery.
- **iGPU monitoring**: Netdata's GPU plugin can monitor iGPU utilization
  (transcoding load, temperature) if the render device is bind-mounted.
  This is a future enhancement.

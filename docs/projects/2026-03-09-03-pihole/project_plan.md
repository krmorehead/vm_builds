# Pi-hole

## Overview

An LXC container running Pi-hole for network-wide DNS-level ad and tracker
blocking. OpenWrt's dnsmasq forwards all DNS queries to Pi-hole. Pi-hole
forwards to encrypted upstream resolvers via Cloudflare DoH.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 256 MB
- Disk: 2 GB (blocklists and query logs)
- Network: LAN bridge, static IP (offset scheme on LAN subnet)
- VMID: 102
- Features: `nesting=1` (Pi-hole may configure iptables rules for port 53/80)

## Startup

- Auto-start: yes
- Boot priority: 3 (DNS should be available early)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes (`dns_nodes`)
- Minimal Router: yes (`dns_nodes`)
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- WireGuard VPN (project 02) — validates LXC patterns first
- OpenWrt router operational (project 01) — needs LAN connectivity
- `pihole_ct_id: 102` already in `group_vars/all.yml`
- `dns_nodes` flavor group and `pihole` dynamic group already in `inventory/hosts.yml`
- `dns_nodes` already in `molecule/default/molecule.yml` platform groups (under `home`)
- `proxmox_lxc` role operational with `pct_remote` connection support

## Network topology assumption

`dns_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Pi-hole containers always use the OpenWrt LAN subnet on the LAN bridge.
There is no WAN-connected case (unlike WireGuard which deploys on `ai`
and `mesh2`). If `dns_nodes` ever includes a WAN-connected host, add
the WireGuard-style topology branching at that time.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | Safe host commands, shell pipefail requirements |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `openwrt-build` | DNS forwarding via UCI, dnsmasq configuration |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── DNS filtering: Pi-hole
│   └── Established, well-documented, unattended install, large blocklist ecosystem
│
├── LXC base: Custom Debian 12 template with Pi-hole baked in
│   ├── Official Pi-hole target; consistent with all other containers
│   └── "Bake, don't configure at runtime" — no curl|bash at converge time
│
├── Image build: debootstrap + Pi-hole unattended install in build-images.sh
│   ├── All packages baked into the template image
│   └── Configure role only applies host-specific config (password, DNS, blocklists)
│
├── DNS chain: clients → OpenWrt dnsmasq → Pi-hole → Cloudflare DoH
│   └── OpenWrt handles DHCP and presents single DNS IP; Pi-hole filters; DoH encrypts upstream
│
├── LXC features: nesting=1
│   └── Pi-hole may use iptables for port management; nesting=1 required in unprivileged containers
│
├── Container IP: static, offset from LAN subnet gateway
│   └── pihole_ct_ip_offset: 5 → e.g., 10.10.10.5 on default LAN
│
└── DHCP: disabled in Pi-hole (OpenWrt handles DHCP)
    └── Single DHCP server avoids conflicts; OpenWrt manages pools and VLANs
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Ansible runs flavor-group plays in sequence, but
**within each play, all hosts in the group execute in parallel**:

```
Phase 3 execution (4 nodes)
├── Play 9:  dns_nodes [pihole]        home provisions Pi-hole (1 host)
├── Play 10: pihole    [pihole]        home configures Pi-hole (1 host)
├── Play 7:  vpn_nodes [wireguard]     home + mesh1 + ai + mesh2 in parallel (4 hosts)
├── Play 8:  wireguard [wireguard]     4 containers configured in parallel
├── Play 11: wifi_nodes:!router_nodes  mesh1 + mesh2 in parallel (2 hosts)
└── Play 12: openwrt_mesh              2 containers configured in parallel
```

Pi-hole itself only runs on `home` (the only `dns_nodes` member), so it
doesn't parallelize across hosts. But it runs concurrently with the rest
of Phase 3 — the 4-node parallelism in WireGuard and mesh plays is
unaffected. Total test time is dominated by the slowest phase, not the
sum of all phases.

Play ordering within Phase 3 matters: Pi-hole should provision before
WireGuard so that when WireGuard comes up, DNS is already available
via Pi-hole. Adjust play order in `site.yml` accordingly (Pi-hole plays
9–10 before WireGuard plays 11–12).

### Per-feature scenarios (fast iteration)

Day-to-day development uses per-feature scenarios that **only touch
Pi-hole**. The OpenWrt baseline, WireGuard containers, and mesh LXC
containers stay running. This keeps iteration cycles under 60 seconds
instead of the 4–5 minute full rebuild.

```
Scenario Hierarchy (Pi-hole additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Pi-hole provision + configure
│
├── molecule/pihole-lxc/              Pi-hole container only (~30-60s)
│   ├── converge: provision + configure Pi-hole container
│   ├── verify: Pi-hole-specific assertions
│   └── cleanup: destroy container 102 only (baseline untouched)
│
└── molecule/openwrt-pihole-dns/      DNS forwarding only (~30s)
    ├── converge: reconstruct openwrt group, apply pihole_dns.yml
    ├── verify: dnsmasq server list, DNS resolution through Pi-hole
    └── cleanup: rollback dnsmasq config (Pi-hole container untouched)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                            # ~4-5 min, all 4 nodes

# 2. Iterate on Pi-hole container (only touches VMID 102)
molecule converge -s pihole-lxc              # ~30s, provision + configure
molecule verify -s pihole-lxc                # ~10s, assertions only
# ... make changes to pihole_lxc or pihole_configure ...
molecule converge -s pihole-lxc              # ~30s, re-converge

# 3. Iterate on DNS forwarding (only touches OpenWrt dnsmasq config)
molecule converge -s openwrt-pihole-dns      # ~20s, UCI changes only
molecule verify -s openwrt-pihole-dns        # ~10s
# ... make changes to pihole_dns.yml ...
molecule converge -s openwrt-pihole-dns      # ~20s, re-converge

# 4. Clean up per-feature changes (baseline stays)
molecule cleanup -s pihole-lxc               # destroys container 102 only
molecule cleanup -s openwrt-pihole-dns       # reverts dnsmasq only

# 5. Final validation before commit
molecule test                                # full clean-state, ~4-5 min
molecule converge                            # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `pihole-lxc` | Container 102 | Container 102 only | None — OpenWrt, WireGuard, mesh untouched |
| `openwrt-pihole-dns` | dnsmasq config | dnsmasq config only | None — container 102 stays running |

### Cross-scenario isolation

Pi-hole's two per-feature scenarios are independent:
- `pihole-lxc` can run without `openwrt-pihole-dns` (container works, just no DNS forwarding)
- `openwrt-pihole-dns` requires Pi-hole container running (converge will fail-fast if 102 is absent)
- Neither touches WireGuard (101), mesh (103), or OpenWrt VM (100)

---

## Milestone Dependency Graph

```
M0: Image Build ─────── self-contained
 └── M1: LXC Provisioning ─── depends on M0
      └── M2: Configuration ─── depends on M1
           └── M3: OpenWrt DNS ─ depends on M2 + OpenWrt baseline (per-feature, opt-in)
                └── M4: Testing & Docs ─ depends on M1–M3
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a custom Debian 12 LXC template with Pi-hole pre-installed. Per the
project's "Bake, don't configure at runtime" principle, all packages belong
in the image. The configure role (M2) only applies host-specific topology
config.

See: `vm-lifecycle` skill (image management section).

**Implementation pattern:**
- Script: add Pi-hole image build section to `build-images.sh`
- Template path: `images/pihole-debian-12-amd64.tar.zst`
- Template var: `pihole_lxc_template` and `pihole_lxc_template_path` in
  `group_vars/all.yml`

**Build approach:**
1. Start from the same Debian 12 standard template used by other containers
2. Extract rootfs, chroot in, run Pi-hole unattended install with a minimal
   `setupVars.conf` (placeholder interface, placeholder IP, no web password)
3. Clean apt caches, repack as `.tar.zst`
4. OR: use `pct create` + `pct exec` to install Pi-hole, then `vzdump` to
   export the template (simpler, uses existing Proxmox tools)

The build approach is a design decision to be resolved during implementation.
Either way, the resulting template has Pi-hole packages installed and FTL
ready to configure.

- [ ] Add Pi-hole template build section to `build-images.sh`
- [ ] Add `pihole_lxc_template` and `pihole_lxc_template_path` to
  `group_vars/all.yml`
- [ ] Build template and place in `images/` (gitignored)
- [ ] Document build prerequisites in `docs/architecture/pihole-build.md`

**Verify:**

- [ ] Template file exists at the configured path
- [ ] Template contains Pi-hole packages (`pihole-FTL`, `lighttpd`)
- [ ] Template is usable by `pct create` without errors

**Rollback:**

Delete the template file from `images/` and remove the vars from
`group_vars/all.yml`. Revert via git.

---

### Milestone 1: LXC Provisioning

_Depends on M0 (template must be built)._

Create the `pihole_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/pihole_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `dns_nodes`, tagged `[pihole]`, in
  Phase 3 after WireGuard (plays 7–8) — becomes new play 9
- deploy_stamp included as last role in the provision play
- Dynamic group `pihole` populated by `proxmox_lxc` via `add_host`

**Container IP addressing:**

Pi-hole uses a static IP on the LAN subnet. Since `dns_nodes` are always
behind OpenWrt, the IP is computed as:
`<LAN_GATEWAY_prefix>.pihole_ct_ip_offset` (default offset: 5).

Example: LAN gateway `10.10.10.1` → Pi-hole IP `10.10.10.5/24`.

The IP is read from `env_generated_path` (LAN_GATEWAY), with a default
fallback to `10.10.10.1` for first-run scenarios (identical to WireGuard
pattern).

- [ ] Create `roles/pihole_lxc/defaults/main.yml`:
  - `pihole_ct_hostname: pihole`
  - `pihole_ct_memory: 256`, `pihole_ct_cores: 1`, `pihole_ct_disk: "2"`
  - `pihole_ct_template: "{{ pihole_lxc_template }}"` (custom Pi-hole image)
  - `pihole_ct_template_path: "{{ pihole_lxc_template_path }}"`
  - `pihole_ct_onboot: true`, `pihole_ct_startup_order: 3`
  - `pihole_ct_features: ["nesting=1"]`
  - `pihole_ct_ip_offset: 5`
- [ ] Create `roles/pihole_lxc/tasks/main.yml`:
  - Read LAN gateway/CIDR from `env_generated_path` (same pattern as WireGuard)
  - Compute container IP: `<LAN_prefix>.{{ pihole_ct_ip_offset }}`
  - Verify template exists, hard-fail with message pointing to `./build-images.sh`
  - Include `proxmox_lxc` with static IP on LAN bridge (`proxmox_all_bridges[1]`)
  - Set DNS to `_lan_gateway` (OpenWrt) — NOT Pi-hole's own IP during bootstrap
- [ ] Register in `pihole` dynamic group via `proxmox_lxc` `lxc_ct_dynamic_group`
- [ ] Add provision play to `site.yml` Phase 3, after WireGuard (play 9),
  targeting `dns_nodes`, tagged `[pihole]`, with `pihole_lxc` and `deploy_stamp`
- [ ] Add configure play to `site.yml` Phase 3 (play 10), targeting `pihole`
  dynamic group, tagged `[pihole]`, `gather_facts: true`
- [ ] Add `PIHOLE_WEB_PASSWORD` to `molecule/default/molecule.yml` provisioner.env
- [ ] Add `PIHOLE_WEB_PASSWORD` to `test.env` (test value)
- [ ] Create `tasks/reconstruct_pihole_group.yml`:
  - Verify container 102 is running (`pct status {{ pihole_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ pihole_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction — `pct_remote` is always the connection

**Verify:**

- [ ] Container 102 is running: `pct status 102` returns `running`
- [ ] Container is in `pihole` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 102` shows `onboot: 1`,
  `startup: order=3`
- [ ] Features configured: `pct config 102` shows `nesting=1`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `pihole_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). No host-side files deployed by this milestone (template
is in `images/`, cleaned by cache removal; no modprobe or module-load
files).

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with Pi-hole host-specific settings.
Pi-hole packages are already installed in the template (M0). This role
only applies runtime-specific config: web admin password, upstream DNS,
blocklists, DHCP disable. Uses `pct exec` from the Proxmox host — no SSH
server needed inside the container.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/pihole_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/setupVars.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `pihole` dynamic group, tagged
  `[pihole]`, after the provision play (play 10 in Phase 3)

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `PIHOLE_WEB_PASSWORD` | yes | Web admin password | `secret123` |

Resolved via `lookup('env', 'PIHOLE_WEB_PASSWORD') | default('', true)`
in role defaults. Add to `test.env` and `.env` template. NEVER add to
`REQUIRED_ENV` in `build.py` — Pi-hole is flavor-specific (`dns_nodes`).

- [ ] Create `roles/pihole_configure/defaults/main.yml`:
  - `pihole_web_password` via `lookup('env', 'PIHOLE_WEB_PASSWORD') | default('', true)`
  - `pihole_upstream_dns_1: "1.1.1.1"`, `pihole_upstream_dns_2: "1.0.0.1"`
  - `pihole_query_logging_days: 7`
- [ ] Create `roles/pihole_configure/tasks/main.yml` (via `pct exec` / `pct_remote`):
  - Template `setupVars.conf` with host-specific values:
    - Interface (`eth0`), IPv4 address (from provisioning), upstream DNS
    - Web admin enabled, password from `.env` (`PIHOLE_WEB_PASSWORD`)
  - Reconfigure Pi-hole with updated `setupVars.conf` (`pihole -r repair`)
  - Add custom blocklists via `pihole -a adlist`
  - Disable Pi-hole DHCP server
  - Set query logging retention to 7 days
  - Set container DNS to `127.0.0.1` after configure
  - Update gravity database: `pihole -g`
- [ ] Create `roles/pihole_configure/templates/setupVars.conf.j2`
- [ ] Create `roles/pihole_configure/meta/main.yml` with required metadata

**Verify:**

- [ ] FTL daemon active: `pct exec 102 -- pihole status` shows FTL running
- [ ] Web admin responds on port 80: `pct exec 102 -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/admin/` returns 200
- [ ] DNS query resolves correctly: `pct exec 102 -- dig @127.0.0.1 google.com +short` returns IP
- [ ] Known ad domain blocked: `pct exec 102 -- dig @127.0.0.1 doubleclick.net +short` returns 0.0.0.0 or NXDOMAIN
- [ ] DHCP disabled in Pi-hole: config confirms no DHCP server
- [ ] Idempotent: second run does not reconfigure

**Rollback:**

- Stop Pi-hole services: `pct exec 102 -- pihole disable`
- Full container destruction via M1 rollback (generic LXC cleanup)

---

### Milestone 3: OpenWrt DNS Forwarding

_Depends on M2 (Pi-hole must be running) and OpenWrt baseline.
Per-feature, opt-in via `--tags openwrt-pihole-dns`._

Configure OpenWrt's dnsmasq to forward DNS queries to Pi-hole. This is a
**post-baseline feature play** within `openwrt_configure` — it modifies
the existing role via a separate task file, not a new role.

See: `openwrt-build` skill (DNS forwarding via UCI, dnsmasq configuration,
post-baseline feature pattern).

**Implementation pattern:**
- Task file: `roles/openwrt_configure/tasks/pihole_dns.yml`
- site.yml: three plays in the per-feature section (tagged `[never]`):
  (0) Reconstruct openwrt group, tagged `[openwrt-pihole-dns, openwrt-pihole-dns-rollback, never]`
  (1) Configure on `openwrt` dynamic group with `tasks_from: pihole_dns.yml`,
  tagged `[openwrt-pihole-dns, never]`
  (2) deploy_stamp on `router_nodes`, tagged `[openwrt-pihole-dns, never]`
- playbooks/cleanup.yml: add `openwrt-pihole-dns-rollback` to the
  existing openwrt reconstruction play's tag list, plus a new rollback play
- All tasks guarded by `pihole_static_ip is defined`

**Pi-hole IP resolution:**
`pihole_static_ip` is computed in the task file from `env_generated_path`
(LAN_GATEWAY prefix + `pihole_ct_ip_offset`), consistent with M1's IP
scheme. This avoids a hard dependency on `group_vars` — the value is
derived at runtime from the same source of truth.

- [ ] Create `roles/openwrt_configure/tasks/pihole_dns.yml`:
  - Read `LAN_GATEWAY` from `env_generated_path` to derive Pi-hole IP
  - Set dnsmasq `server` to Pi-hole IP
  - Configure `https-dns-proxy` as primary upstream when Pi-hole is unavailable:
    dnsmasq server list: Pi-hole first, then `127.0.0.1#5053` (DoH proxy)
  - `uci commit` and restart dnsmasq
- [ ] Add reconstruction play to `site.yml` per-feature section,
  tagged `[openwrt-pihole-dns, openwrt-pihole-dns-rollback, never]`
- [ ] Add configure play to `site.yml` per-feature section,
  tagged `[openwrt-pihole-dns, never]`
- [ ] Add deploy_stamp play to `site.yml` per-feature section,
  tagged `[openwrt-pihole-dns, never]`
- [ ] Add `openwrt-pihole-dns-rollback` to the existing openwrt
  reconstruction play's tag list in `playbooks/cleanup.yml`
- [ ] Add rollback play to `playbooks/cleanup.yml` (see Rollback below)

**Verify:**

- [ ] dnsmasq forwards to Pi-hole IP (check UCI: `uci show dhcp.@dnsmasq[0]`)
- [ ] DNS resolution succeeds through Pi-hole (query from client returns filtered result)
- [ ] deploy_stamp contains `openwrt_pihole_dns` play entry

**Rollback (`--tags openwrt-pihole-dns-rollback`):**

- Revert dnsmasq server list to forward only to https-dns-proxy
- `uci commit && /etc/init.d/dnsmasq restart`
- Add rollback play in `playbooks/cleanup.yml`:
  - Reconstruct openwrt group (already handled by shared reconstruction play)
  - Remove Pi-hole from dnsmasq server list
  - `uci commit dhcp && /etc/init.d/dnsmasq restart`

---

### Milestone 4: Testing & Documentation

_Depends on M1–M3._

Create per-feature molecule scenarios for fast Pi-hole-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, and update architecture docs.

See: `ansible-testing` skill (verify completeness, baseline workflow,
per-feature scenario setup), `multi-node-ssh` skill (ProxyJump for LAN
nodes), `rollback-patterns` skill (cleanup completeness).

#### 4a. Per-feature scenario: `molecule/pihole-lxc/`

Covers container provisioning + configuration. Only touches VMID 102.
Assumes baseline exists (OpenWrt running, LAN bridge up). Follows the
WireGuard `molecule/wireguard-lxc/` pattern exactly.

- [ ] Create `molecule/pihole-lxc/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - dns_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      PIHOLE_WEB_PASSWORD: ${PIHOLE_WEB_PASSWORD}
  scenario:
    test_sequence:    # no initial cleanup — baseline must exist
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```
  Only `home` in `dns_nodes`. Only `PIHOLE_WEB_PASSWORD` env var added
  beyond the standard set. Single-node — fast.

- [ ] Create `molecule/pihole-lxc/converge.yml`:
  ```yaml
  - name: Provision Pi-hole LXC container
    hosts: dns_nodes
    gather_facts: false
    roles:
      - pihole_lxc

  - name: Reconstruct pihole dynamic group
    hosts: dns_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks: ../../tasks/reconstruct_pihole_group.yml

  - name: Configure Pi-hole
    hosts: pihole
    gather_facts: true
    roles:
      - pihole_configure
  ```
  Provision → reconstruct → configure. No OpenWrt, no WireGuard, no mesh.

- [ ] Create `molecule/pihole-lxc/verify.yml`:
  Pi-hole-specific assertions only. Runs on `dns_nodes` (Proxmox host)
  using `pct exec` — no dynamic group needed for most checks:
  - Container 102 running, onboot=1, startup order=3, nesting=1
  - FTL daemon active
  - Web admin responds on port 80
  - DNS resolves correctly, known ad domain blocked
  - deploy_stamp contains `pihole_lxc` entry

- [ ] Create `molecule/pihole-lxc/cleanup.yml`:
  Destroys only container 102. Does NOT touch OpenWrt, WireGuard, or mesh:
  ```yaml
  - name: Destroy Pi-hole container
    hosts: dns_nodes
    gather_facts: false
    tasks:
      - name: Stop Pi-hole container
        ansible.builtin.command:
          cmd: pct stop {{ pihole_ct_id }}
        failed_when: false
        changed_when: true

      - name: Wait for container to stop
        ansible.builtin.pause:
          seconds: 2

      - name: Destroy Pi-hole container
        ansible.builtin.command:
          cmd: pct destroy {{ pihole_ct_id }} --purge
        failed_when: false
        changed_when: true
  ```
  No host-side files to clean (Pi-hole has no kernel modules or modprobe
  config — everything lives inside the container).

#### 4b. Per-feature scenario: `molecule/openwrt-pihole-dns/`

Covers the OpenWrt dnsmasq DNS forwarding feature (M3). Only touches
UCI config on the OpenWrt VM. Assumes both the OpenWrt baseline AND
Pi-hole container are running. Follows the `openwrt-security` pattern.

- [ ] Create `molecule/openwrt-pihole-dns/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - router_nodes
        - dns_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      MESH_KEY: ${MESH_KEY}
      PIHOLE_WEB_PASSWORD: ${PIHOLE_WEB_PASSWORD}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/openwrt-pihole-dns/converge.yml`:
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

  - name: Apply Pi-hole DNS forwarding
    hosts: openwrt
    gather_facts: false
    tasks:
      - name: Include Pi-hole DNS tasks
        ansible.builtin.include_role:
          name: openwrt_configure
          tasks_from: pihole_dns.yml
  ```

- [ ] Create `molecule/openwrt-pihole-dns/verify.yml`:
  Reconstruct openwrt group, then verify dnsmasq server list and DNS
  resolution through Pi-hole.

- [ ] Create `molecule/openwrt-pihole-dns/cleanup.yml`:
  ```yaml
  - name: Rollback Pi-hole DNS forwarding
    ansible.builtin.import_playbook: ../../playbooks/cleanup.yml
    tags: [openwrt-pihole-dns-rollback]
  ```
  Reverts dnsmasq config only. Pi-hole container and OpenWrt VM stay up.

#### 4c. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Pi-hole assertions
  (new play targeting `dns_nodes`):
  - Container 102 running, onboot=1, startup order=3, nesting=1
  - FTL daemon active, web admin responds, DNS resolves, ads blocked
  - deploy_stamp contains `pihole_lxc` entry
  These assertions run as part of the full 4-node integration and execute
  on `home` while WireGuard assertions run on all 4 nodes in parallel.

- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles container 102 (`pct list` iteration — confirm no changes needed)

#### 4d. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `pihole-rollback` play:
  - Stop and destroy Pi-hole container (`pct stop 102 && pct destroy 102`)
  - Tagged `[pihole-rollback, never]`
  - Runs on `dns_nodes` (no dynamic group needed — operates on Proxmox host)

- [ ] Add `openwrt-pihole-dns-rollback` to the existing openwrt
  reconstruction play's tag list in `playbooks/cleanup.yml`

- [ ] Add `openwrt-pihole-dns-rollback` play:
  - Revert dnsmasq server list (remove Pi-hole, keep https-dns-proxy)
  - `uci commit dhcp && /etc/init.d/dnsmasq restart`
  - Tagged `[openwrt-pihole-dns-rollback, never]`

#### 4e. Molecule env passthrough

- [ ] Add `PIHOLE_WEB_PASSWORD` to `molecule/default/molecule.yml`
  `provisioner.env` (for full integration tests)

#### 4f. Documentation

- [ ] Create `docs/architecture/pihole-build.md`:
  - Image build process, requirements, design decisions
  - DNS chain and failover behavior
  - Env variables
  - Test vs production workflow differences
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Pi-hole provision + configure plays in Phase 3
  - Add openwrt-pihole-dns per-feature plays to the per-feature section
  - Role catalog: add pihole_lxc, pihole_configure
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Pi-hole project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

#### 4g. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s pihole-lxc` — per-feature cycle passes
- [ ] Run `molecule test -s openwrt-pihole-dns` — DNS forwarding cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Pi-hole artifacts on host or controller
- [ ] Baseline workflow: after `molecule test`, `molecule converge` restores
  baseline; then `molecule converge -s pihole-lxc` works without full rebuild

**Rollback:** N/A — test infrastructure and documentation; revert via git.

---

## Future Integration Considerations

- **Monitoring**: Netdata/rsyslog projects may want to monitor Pi-hole's
  FTL service and query logs. Pi-hole's static IP (from this project)
  provides the stable endpoint they need.
- **VLANs**: If VLAN segmentation is deployed (project 01, M2), Pi-hole
  may need to listen on VLAN interfaces or have firewall rules allowing
  DNS from VLAN subnets. This is a downstream concern for the VLAN project.
- **Multi-node DNS**: If `dns_nodes` ever includes non-router hosts (e.g.,
  `ai`, `mesh2`), the provisioning role needs WireGuard-style topology
  branching (LAN vs WAN subnet). Currently unnecessary — all `dns_nodes`
  are behind OpenWrt.

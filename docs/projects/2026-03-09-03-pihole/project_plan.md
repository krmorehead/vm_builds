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
- Network: LAN bridge, static IP
- VMID: 102

## Startup

- Auto-start: yes
- Boot priority: 3 (DNS should be available early)
- Depends on: OpenWrt Router

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: no

## Prerequisites

- Shared infrastructure: `proxmox_lxc` role (project 00)
- WireGuard VPN (project 02) — validates LXC patterns first
- OpenWrt router operational (project 01) — needs LAN connectivity

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
├── LXC base: Debian 12
│   └── Official Pi-hole target; consistent with all other containers
│
├── DNS chain: clients → OpenWrt dnsmasq → Pi-hole → Cloudflare DoH
│   └── OpenWrt handles DHCP and presents single DNS IP; Pi-hole filters; DoH encrypts upstream
│
└── DHCP: disabled in Pi-hole (OpenWrt handles DHCP)
    └── Single DHCP server avoids conflicts; OpenWrt manages pools and VLANs
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ──── self-contained
 └── M2: Configuration ─── depends on M1
      └── M3: OpenWrt DNS ─ depends on M2 + OpenWrt baseline
           └── M4: Integration ─ depends on M1–M3
                └── M5: Testing ─ depends on M1–M4
                     └── M6: Documentation ─ depends on M1–M5
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `pihole_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/pihole_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `dns_nodes`, tagged `[pihole]`
- deploy_stamp included as last role in the provision play
- Dynamic group `pihole` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure project 00):
- `pihole_ct_id: 102` in `group_vars/all.yml`
- `dns_nodes` flavor group and `pihole` dynamic group in `inventory/hosts.yml`
- `dns_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/pihole_lxc/defaults/main.yml`:
  - `pihole_ct_id: 102`, `pihole_ct_memory: 256`, `pihole_ct_cores: 1`
  - `pihole_ct_disk: 2G`, `pihole_ct_ip` (static, from group_vars)
  - `pihole_ct_onboot: true`, `pihole_ct_startup_order: 3`
- [ ] Create `roles/pihole_lxc/tasks/main.yml`:
  - Include `proxmox_lxc` with static IP on LAN bridge
  - Set DNS to upstream temporarily (NOT Pi-hole's own IP during install)
- [ ] Register in `pihole` dynamic group via `proxmox_lxc` `lxc_ct_dynamic_group`
- [ ] Add provision play to `site.yml` targeting `dns_nodes`, tagged `[pihole]`,
  with `pihole_lxc` role and `deploy_stamp`

**Verify:**

- [ ] Container 102 is running: `pct status 102` returns `running`
- [ ] Container is in `pihole` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 102` shows `onboot: 1`,
  `startup: order=3`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] deploy_stamp contains `pihole_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). No host-side files deployed by this milestone.

---

### Milestone 2: Configuration

_Depends on M1 (container must be running)._

Configure the running container with Pi-hole via official unattended script,
custom blocklists, and web admin. Uses `pct exec` from the Proxmox host —
no SSH server needed inside the container.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/pihole_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/setupVars.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `pihole` dynamic group, tagged
  `[pihole]`, after the provision play

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `PIHOLE_WEB_PASSWORD` | yes | Web admin password for unattended install | `secret123` |

Resolved via `lookup('env', 'PIHOLE_WEB_PASSWORD')` in role defaults.
Add to `test.env` and `.env` template. NEVER add to `REQUIRED_ENV` in
`build.py` — Pi-hole is flavor-specific (`dns_nodes`).

- [ ] Create `roles/pihole_configure/defaults/main.yml`:
  - `pihole_web_password` via `lookup('env', 'PIHOLE_WEB_PASSWORD') | default('', true)`
  - Upstream DNS: Cloudflare 1.1.1.1 + 1.0.0.1
- [ ] Create `roles/pihole_configure/tasks/main.yml` (via `pct exec`):
  - Template `setupVars.conf` for unattended install:
    - Interface, IPv4 address, upstream DNS (Cloudflare 1.1.1.1 + 1.0.0.1)
    - Web admin enabled, password from `.env` (`PIHOLE_WEB_PASSWORD`)
  - Install Pi-hole via official unattended script
  - Add custom blocklists via `pihole -a adlist`
  - Disable Pi-hole DHCP server
  - Set query logging retention to 7 days
  - Set container DNS to `127.0.0.1` after install
- [ ] Create `roles/pihole_configure/templates/setupVars.conf.j2`
- [ ] Create `roles/pihole_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `pihole` dynamic group,
  tagged `[pihole]`, `gather_facts: true`, after the provision play

**Verify:**

- [ ] FTL daemon active: `pct exec 102 -- pihole status` shows FTL running
- [ ] Web admin responds on port 80: `pct exec 102 -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/admin/` returns 200
- [ ] DNS query resolves correctly: `pct exec 102 -- dig @127.0.0.1 google.com +short` returns IP
- [ ] Known ad domain blocked: `pct exec 102 -- dig @127.0.0.1 doubleclick.net +short` returns 0.0.0.0 or NXDOMAIN
- [ ] DHCP disabled in Pi-hole: config confirms no DHCP server
- [ ] Idempotent: second run does not reinstall

**Rollback:**

- Stop Pi-hole services: `pct exec 102 -- pihole disable`
- Uninstall Pi-hole: `pct exec 102 -- pihole uninstall` (or full container
  destruction via M1 rollback)

---

### Milestone 3: OpenWrt DNS Forwarding

_Depends on M2 (Pi-hole must be running) and OpenWrt baseline._

Configure OpenWrt's dnsmasq to forward DNS queries to Pi-hole. This is a
**post-baseline feature play** within `openwrt_configure` — it modifies
the existing role via a separate task file, not a new role.

See: `openwrt-build` skill (DNS forwarding via UCI, dnsmasq configuration,
post-baseline feature pattern).

**Implementation pattern:**
- Task file: `roles/openwrt_configure/tasks/pihole_dns.yml` (not merged into
  `dns.yml` — avoids re-running M3 encrypted DNS tasks when only Pi-hole
  forwarding changes)
- site.yml: two plays — (1) configure on `openwrt` dynamic group with
  `tasks_from: pihole_dns.yml`, tag `[openwrt-pihole-dns]`; (2) deploy_stamp
  on `router_nodes`, tag `[openwrt-pihole-dns]`
- All tasks guarded by `pihole_static_ip is defined`

- [ ] Add `pihole_static_ip` to `group_vars/all.yml` (or derive from
  `pihole_ct_ip` in dns_nodes context)
- [ ] Create `roles/openwrt_configure/tasks/pihole_dns.yml`:
  - Set dnsmasq `server` to Pi-hole IP when `pihole_static_ip` is defined
  - Configure `https-dns-proxy` as fallback when Pi-hole is down:
    - dnsmasq server list: Pi-hole first, then `127.0.0.1#5053`
  - `uci commit` and restart dnsmasq
- [ ] Add both plays to `site.yml` (configure + deploy_stamp), tagged
  `[openwrt-pihole-dns]`
- [ ] Order play AFTER OpenWrt baseline configure and AFTER Pi-hole configure
- [ ] Test DNS failover: stop Pi-hole → clients still resolve via fallback

**Verify:**

- [ ] dnsmasq forwards to Pi-hole IP (check UCI: `uci show dhcp.@dnsmasq[0]`)
- [ ] DNS resolution succeeds through Pi-hole (query from client returns filtered result)
- [ ] Fallback works: stop Pi-hole, DNS still resolves via https-dns-proxy
- [ ] deploy_stamp contains `openwrt_pihole_dns` play entry

**Rollback (`--tags openwrt-pihole-dns-rollback`):**

- Revert dnsmasq server list to forward only to https-dns-proxy
- `uci commit && /etc/init.d/dnsmasq restart`

---

### Milestone 4: Integration

_Depends on M1–M3._

Wire up site.yml plays, inventory, env vars, and dynamic group reconstruction
for per-feature molecule scenarios and cleanup/rollback entry points.

See: `vm-lifecycle` skill (site.yml play order), `rollback-patterns` skill
(cleanup completeness).

**Implementation pattern:**
- site.yml: provision play (M1) + configure play (M2) already added in M1/M2
- Ensure play order: OpenWrt configure → Pi-hole provision → Pi-hole configure
- Add `tasks/reconstruct_pihole_group.yml` for per-feature converge/verify/cleanup

- [ ] Add `pihole_lxc` provision play to `site.yml` targeting `dns_nodes`
  (if not already in M1)
- [ ] Add `pihole_configure` play targeting `pihole` dynamic group
  (if not already in M2)
- [ ] Include `deploy_stamp` in the provision play
- [ ] Add `pihole` dynamic group to `inventory/hosts.yml` (already present)
- [ ] Add `PIHOLE_WEB_PASSWORD` to `test.env` and `.env` template
- [ ] Order play AFTER OpenWrt configure
- [ ] Create `tasks/reconstruct_pihole_group.yml`:
  - Verify container 102 is running (`pct status {{ pihole_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ pihole_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction — pct_remote is always the connection method

**Verify:**

- [ ] Full `molecule converge` runs without errors
- [ ] Pi-hole provision and configure plays execute in correct order
- [ ] `reconstruct_pihole_group.yml` successfully registers `pihole` host
  when run standalone (e.g., from per-feature converge)

**Rollback:** N/A — integration wiring; revert via git.

---

### Milestone 5: Testing

_Depends on M1–M4._

Extend molecule verify and cleanup. Per-feature scenario uses baseline
workflow — `molecule converge` preserves OpenWrt baseline so layered
scenarios remain accessible. ProxyJump patterns from `multi-node-ssh` apply
when testing from LAN nodes.

See: `ansible-testing` skill (verify completeness, baseline workflow,
per-feature scenario setup), `multi-node-ssh` skill (ProxyJump for LAN nodes).

**Implementation pattern:**
- Extend `molecule/default/verify.yml` with Pi-hole assertions
- Verify generic container cleanup handles VMID 102 (already iterates
  `pct list` — no hardcoded VMIDs)
- Add any Pi-hole-specific host files to BOTH `molecule/default/cleanup.yml`
  AND `playbooks/cleanup.yml` (cleanup completeness rule)
- Create `molecule/pihole-lxc/` per-feature scenario (optional, if desired)

- [ ] Extend `molecule/default/verify.yml`:
  - Container running: `pct status 102` returns `running`
  - FTL daemon active
  - Web admin responds on port 80
  - DNS query resolves correctly, known ad domain blocked
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles container 102 (pct list iteration — confirm no changes needed)
- [ ] Add any Pi-hole-deployed host files to removal list in BOTH
  `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`
  (currently none — Pi-hole config lives inside container)
- [ ] Create `molecule/pihole-lxc/` per-feature scenario (optional):
  - `converge.yml`: reconstruct pihole group, run pihole_configure
  - `verify.yml`: reconstruct pihole group, run Pi-hole assertions
  - `cleanup.yml`: destroy container 102, remove any host artifacts
  - Baseline workflow: run `molecule converge` (default) first to establish
    OpenWrt + Pi-hole; then `molecule converge -s pihole-lxc` for iteration
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: container state, auto-start, FTL, web admin,
  DNS resolution, ad blocking, deploy_stamp
- [ ] Cleanup leaves no Pi-hole artifacts on host or controller
- [ ] Baseline workflow: after `molecule test`, `molecule converge` restores
  baseline for layered scenarios (per `ansible-testing` skill)

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 6: Documentation

_Depends on M1–M5._

- [ ] Create `docs/architecture/pihole-build.md`:
  - Requirements, design decisions, env variables
  - DNS chain and failover behavior
  - Test vs production workflow differences
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Pi-hole provision + configure plays
  - Role catalog: add pihole_lxc, pihole_configure
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Pi-hole project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented

**Rollback:** N/A — documentation-only milestone.

# Container NAT Networking

## Status: IN PROGRESS

## Overview

WAN containers currently share the 192.168.86.0/24 broadcast domain with
household devices. The IP formula (`offset + 150 + group_index`) produces
cross-service collisions (.172, .173, .174) and device collisions because
different services use different flavor groups with different sizes. No offset
range can fix this — the formula is fundamentally broken for a shared broadcast
domain.

This project gives each Proxmox host a private internal bridge (`vmbr_ct`)
with its own /24 subnet and host-side NAT. Containers get private IPs using
just their service offset (no group indexing), making collisions impossible.

## Type

Cross-cutting infrastructure (container networking)

## Prerequisites

- `proxmox_bridges` role operational on all 6 hosts
- All LXC provisioning roles use `proxmox_lxc` shared helper
- WAN/LAN networking computed via `tasks/lxc_wan_or_lan_network.yml`

## Skills

| Skill | When to use |
|---|---|
| `proxmox-network-safety` | Bridge management, interface safety |
| `proxmox-cleanup-safety` | Cleanup completeness, file removal |
| `lxc-container-patterns` | LXC provisioning, networking |
| `testing-workflow` | TDD, molecule test patterns |

---

## Architectural Decisions

```
Container NAT networking
├── Why NAT instead of a different IP range?
│   └── Shared broadcast domain = collision risk forever
│       ├── Household devices (.40, .50, .95, .157, .172) occupy unknown IPs
│       ├── Cross-service formula (offset + base + group_index) collides
│       │   when services have different flavor group sizes
│       └── NAT isolates containers completely — zero collision risk
├── Why per-host /24 instead of a single shared subnet?
│   └── Each host is its own failure domain
│       ├── No cross-host ARP conflicts
│       ├── Simple: offset alone determines IP (no group indexing)
│       └── Scales to any number of hosts without recalculation
├── Why not NAT for LAN hosts (home, mesh1)?
│   └── OpenWrt LAN (10.10.10.x) is already isolated
│       ├── LAN containers serve clients on that subnet directly
│       └── Pi-hole, Jellyfin, etc. need direct L2 reachability
├── Why not NAT for OpenWrt mesh/bridge LXC?
│   └── They need L2 access for WiFi bridging
│       ├── PHY namespace moves require direct network access
│       └── Their +200 formula on bridge_nodes doesn't collide
│           (bridge-1/bridge-2 don't host other WAN services)
└── What about inbound connections (WireGuard)?
    └── Host-level port forwarding (DNAT)
        ├── Only WireGuard needs inbound (UDP 51820)
        └── All other services are outbound-only or accessed via pct exec
```

## Network topology (after)

```
ISP Router (192.168.86.1)
  |
  vmbr0 (192.168.86.0/24) — hosts only, no containers
  |
  home (.201)         — LAN containers on 10.10.10.x (via OpenWrt, unchanged)
  mesh1 (10.10.10.210) — LAN containers on 10.10.10.x (via OpenWrt, unchanged)
  ai (.220)           — vmbr_ct (10.99.3.1/24, MASQUERADE via vmbr0)
  mesh2 (.211)        — vmbr_ct (10.99.4.1/24, MASQUERADE via vmbr0)
  bridge-1 (.230)     — vmbr_ct (10.99.5.1/24, MASQUERADE via vmbr0)
  bridge-2 (.231)     — vmbr_ct (10.99.6.1/24, MASQUERADE via vmbr0)
```

## IP addressing scheme

Each host gets `container_subnet_id` in host_vars. Container subnet is
`10.99.{container_subnet_id}.0/24`. Bridge gateway is `.1`.

| Host | container_subnet_id | Subnet | Gateway |
|---|---|---|---|
| home | 1 | 10.99.1.0/24 | 10.99.1.1 |
| mesh1 | 2 | 10.99.2.0/24 | 10.99.2.1 |
| ai | 3 | 10.99.3.0/24 | 10.99.3.1 |
| mesh2 | 4 | 10.99.4.0/24 | 10.99.4.1 |
| bridge-1 | 5 | 10.99.5.0/24 | 10.99.5.1 |
| bridge-2 | 6 | 10.99.6.0/24 | 10.99.6.1 |

Container IPs use just the service offset — no group indexing:

| Service | Offset | IP on any host |
|---|---|---|
| WireGuard | 3 | 10.99.X.3 |
| Pi-hole | 10 | 10.99.X.10 |
| rsyslog | 12 | 10.99.X.12 |
| Home Assistant | 14 | 10.99.X.14 |
| Jellyfin | 15 | 10.99.X.15 |
| Kodi | 16 | 10.99.X.16 |
| Moonlight | 17 | 10.99.X.17 |
| Gaming | 18 | 10.99.X.18 |
| Kiosk | 19 | 10.99.X.19 |
| Netdata | 21 | 10.99.X.21 |

Collisions are impossible: each host has its own /24, and offsets are unique
within a host by design.

Note: LAN hosts (home, mesh1) still use 10.10.10.x for their containers
(OpenWrt LAN). The container_subnet_id is defined but unused on LAN hosts —
it exists for future flexibility and consistency.

---

## Milestones

## Milestone dependency graph

```
M0 (host_vars + bridge infra) ← self-contained
├── M1 (WAN networking task) ← depends on M0
│   └── M2 (WireGuard port forwarding) ← depends on M1
└── M3 (cleanup + verify + docs) ← depends on M1
```

### Milestone 0: Container bridge infrastructure

_Self-contained. No external dependencies._

Add `container_subnet_id` to all host_vars and extend `proxmox_bridges` to
create an internal container bridge with NAT on WAN hosts.

See: `proxmox-network-safety` skill.

**Implementation pattern:**
- Modified files: `inventory/host_vars/*.yml`, `roles/proxmox_bridges/tasks/main.yml`,
  `roles/proxmox_bridges/templates/container-bridge.conf.j2`
- No new roles. Extends existing infrastructure role.

- [ ] Add `container_subnet_id` to all 6 host_vars files (home=1, mesh1=2, ai=3, mesh2=4, bridge-1=5, bridge-2=6)
- [ ] Add `container_bridge_name` default to `roles/proxmox_bridges/defaults/main.yml` (default: `vmbr_ct`)
- [ ] Create `roles/proxmox_bridges/templates/container-bridge.conf.j2` — internal bridge
  with static IP (`10.99.{{ container_subnet_id }}.1/24`), no physical port, no STP
- [ ] Add tasks to `roles/proxmox_bridges/tasks/main.yml` (after existing bridge discovery):
  - Deploy `container-bridge.conf.j2` to `/etc/network/interfaces.d/ansible-container-bridge.conf`
  - Bring up the bridge via `ifup vmbr_ct`
  - Enable IP forwarding (`sysctl net.ipv4.ip_forward=1`)
  - Add iptables MASQUERADE rule: `-t nat -A POSTROUTING -s 10.99.{{ container_subnet_id }}.0/24 -o {{ proxmox_wan_bridge }} -j MASQUERADE`
  - Add iptables FORWARD rules: allow established and related, allow from container subnet
  - Condition: only on WAN hosts (`'router_nodes' not in group_names and 'lan_hosts' not in group_names`)
- [ ] Export `proxmox_container_bridge` fact (cacheable): bridge name for container placement
  - WAN hosts: `vmbr_ct`
  - LAN hosts: `proxmox_all_bridges[1]` (OpenWrt LAN bridge, unchanged)

**Verify:**
- [ ] `vmbr_ct` bridge exists on all WAN hosts (ai, mesh2, bridge-1, bridge-2)
- [ ] `vmbr_ct` has IP `10.99.X.1/24` matching the host's `container_subnet_id`
- [ ] `vmbr_ct` does NOT exist on LAN hosts (home, mesh1) — they use OpenWrt LAN
- [ ] iptables NAT rule exists for the container subnet
- [ ] IP forwarding is enabled on WAN hosts
- [ ] `proxmox_container_bridge` fact is set correctly on all hosts

**Rollback:**
Remove `/etc/network/interfaces.d/ansible-container-bridge.conf`. Flush
iptables nat table. Delete the `vmbr_ct` bridge (`ip link delete vmbr_ct`).
Remove `container_subnet_id` from host_vars.

---

### Milestone 1: WAN container networking via NAT bridge

_Depends on: M0._

Modify `tasks/lxc_wan_or_lan_network.yml` to use the container bridge with
private IPs instead of the WAN bridge with household subnet IPs.

See: `lxc-container-patterns` skill.

**Implementation pattern:**
- Modified file: `tasks/lxc_wan_or_lan_network.yml`
- No role changes needed — all 5 WAN/LAN services (wireguard, kiosk, gaming,
  netdata, rsyslog) consume the shared task and pass results to `proxmox_lxc`

- [ ] Modify the WAN path in `tasks/lxc_wan_or_lan_network.yml`:
  - `_lxc_net_ip`: `10.99.{{ container_subnet_id }}.{{ lxc_net_ip_offset }}`
    (no group indexing, no +150 base)
  - `_lxc_net_gateway`: `10.99.{{ container_subnet_id }}.1`
  - `_lxc_net_cidr`: `24`
  - `_lxc_net_nameserver`: `10.99.{{ container_subnet_id }}.1` (host bridge
    acts as DNS forwarder via systemd-resolved or direct upstream)
  - `_lxc_net_bridge`: `{{ proxmox_container_bridge }}`
  (actually: use `8.8.8.8` for nameserver since the host bridge doesn't
  run a DNS resolver — containers need external DNS for callhome)
- [ ] Remove the `+ 150 + groups[lxc_net_flavor_group].index(inventory_hostname)`
  formula from the WAN IP computation
- [ ] Update the debug message to show `NAT bridge` instead of `WAN subnet`

**Verify:**
- [ ] WAN containers get 10.99.X.{offset} IPs (not 192.168.86.x)
- [ ] WAN containers are on `vmbr_ct` bridge (not `proxmox_wan_bridge`)
- [ ] WAN containers can reach the internet (outbound NAT working)
- [ ] WAN containers can reach the callhome API server
- [ ] LAN containers are unchanged (10.10.10.x on OpenWrt LAN)
- [ ] No IP collisions exist between any containers on any host

**Rollback:**
Revert `tasks/lxc_wan_or_lan_network.yml` from git. WAN containers return
to 192.168.86.x addressing on the WAN bridge.

---

### Milestone 2: WireGuard port forwarding

_Depends on: M1._

WireGuard on WAN hosts needs inbound UDP 51820 from the internet. With NAT,
the container is on a private subnet — host-level DNAT forwards traffic.

See: `lxc-container-patterns` skill.

**Implementation pattern:**
- Modified file: `roles/wireguard_lxc/tasks/main.yml`
- Only affects WAN hosts — LAN hosts' WireGuard is already reachable on
  the OpenWrt LAN

- [ ] Add DNAT rule after container provisioning in `roles/wireguard_lxc/tasks/main.yml`:
  - `iptables -t nat -A PREROUTING -i {{ proxmox_wan_bridge }} -p udp --dport 51820 -j DNAT --to-destination {{ _lxc_net_ip }}:51820`
  - Condition: `when: not (_lxc_net_on_lan | bool)`
- [ ] Add FORWARD allow rule for the DNAT'd traffic:
  - `iptables -A FORWARD -i {{ proxmox_wan_bridge }} -o {{ proxmox_container_bridge }} -p udp --dport 51820 -j ACCEPT`
  - Condition: `when: not (_lxc_net_on_lan | bool)`

**Verify:**
- [ ] WireGuard on WAN hosts (ai, mesh2) accepts incoming VPN connections
- [ ] DNAT rule exists in iptables on WAN hosts with WireGuard
- [ ] WireGuard on LAN hosts (home, mesh1) is unchanged (no DNAT needed)

**Rollback:**
Remove the DNAT and FORWARD rules from iptables. WireGuard returns to
direct WAN bridge placement (handled by M1 rollback).

---

### Milestone 3: Cleanup, verification, and documentation

_Depends on: M1._

Update cleanup playbooks, verify.yml, and project documentation.

- [ ] Add to `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`:
  - Remove `/etc/network/interfaces.d/ansible-container-bridge.conf`
  - Flush iptables nat table (`iptables -t nat -F`)
  - Delete `vmbr_ct` bridge (`ip link delete vmbr_ct`)
  - Remove iptables FORWARD rules for container subnet
- [ ] Add verify assertions to `molecule/default/verify.yml`:
  - WAN hosts: `vmbr_ct` exists with correct IP
  - WAN hosts: iptables NAT rule for container subnet
  - WAN containers: correct 10.99.X.{offset} IPs
  - WAN containers: outbound internet connectivity (curl test)
  - WAN containers: callhome heartbeat arriving at API
  - No 192.168.86.x IPs on any container (regression check)
- [ ] Update `.cursor/rules/project-structure.mdc`:
  - Update "LXC container IP allocation" section for NAT scheme
  - Update IP collision documentation
  - Remove +150 WAN offset references
- [ ] Update `.cursor/rules/proxmox-safety.mdc`:
  - Update "LXC container networking must match host topology" section
  - Document NAT bridge pattern
- [ ] Update `project-plan-review.mdc`:
  - Update "Container IP offset" check (no more WAN collision math)
- [ ] Run `molecule test` — full validation on all 6 hosts
- [ ] Run `pytest tests/ -v` — Python test suite
- [ ] Run `ansible-lint && yamllint .` — lint checks

**Verify:**
- [ ] All tests pass with exit code 0
- [ ] No references to +150 WAN offset remain in rules/skills
- [ ] Cleanup destroys the container bridge and NAT rules

**Rollback:**
Revert doc/cleanup changes from git. No host-side impact.

---

## Testing Strategy

### Parallelism

This project modifies infrastructure (M0) and the shared networking task (M1).
All services that use `lxc_wan_or_lan_network.yml` are affected simultaneously.
The E2E test validates all services together.

### Per-feature scenario impact

Per-feature scenarios that test WAN services (wireguard-lxc, kiosk-lxc,
gaming-lxc, netdata-lxc, rsyslog-lxc) will use the new NAT bridge. Their
cleanup playbooks need updated to remove the container bridge.

### Day-to-day workflow

```bash
set -a && source test.env && set +a

# Iterate on bridge infrastructure
molecule converge && molecule verify

# Full clean-state validation
molecule test
```

### Teardown table

| Scenario | Creates | Destroys | Baseline impact |
|---|---|---|---|
| `molecule test` | vmbr_ct + NAT rules + all containers | All of the above | Clean slate |
| `molecule cleanup` | Nothing | vmbr_ct, NAT rules, containers | Clean slate |

## Future Integration Considerations

- When adding a new LXC service, just assign it a unique offset in
  `group_vars/all.yml`. The offset alone determines the IP — no group
  indexing, no collision checks needed.
- If a future service needs inbound connections from the WAN (like WireGuard),
  add a DNAT rule in its provisioning role, conditioned on `not _lxc_net_on_lan`.
- The `container_subnet_id` variable can be used for other per-host isolation
  patterns beyond container networking.

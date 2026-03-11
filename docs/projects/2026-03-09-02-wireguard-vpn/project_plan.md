# WireGuard VPN Client

## Overview

A lightweight LXC container running a WireGuard client that maintains a
persistent VPN tunnel back to the home server. Other services on the node
can route through this tunnel for remote access.

This is the **first LXC container** -- its implementation is the proving
ground for the shared `proxmox_lxc` role.

## Type

LXC container

## Resources

- Cores: 1
- RAM: 128 MB
- Disk: 1 GB
- Network: LAN bridge (DHCP), WireGuard tunnel interface (wg0)
- VMID: 101

## Startup

- Auto-start: yes
- Boot priority: 2 (after OpenWrt, before application services)
- Depends on: OpenWrt Router (network connectivity)

## Build Profiles

- Home Entertainment Box: yes
- Minimal Router: yes
- Gaming Rig: no

## Prerequisites

- Multi-node test infrastructure project (`2026-03-11-00`) complete —
  ProxyJump patterns and new test node validated
- Shared infrastructure complete: `proxmox_lxc` role operational
  (project 00, milestone 1) ✓
- OpenWrt router operational (provides LAN network and internet access)
- WireGuard server running at remote endpoint (external dependency for
  production; auto-generated dummy credentials for testing)
- Debian 12 LXC template in `images/` directory

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, LXC provisioning via `proxmox_lxc`, deploy_stamp, cleanup completeness |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, fact scoping |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | Safe host commands, kernel module loading, shell pipefail requirements |
| `multi-node-ssh` | ProxyJump patterns for testing on LAN nodes (from project 2026-03-11-00) |
| `project-planning` | Milestone structure, verify/rollback sections, dependency tracking |

---

## Architectural Decisions

```
Decisions
├── Container type: LXC (not VM)
│   ├── WireGuard is userspace tools + kernel module — no hardware access needed
│   ├── 128 MB RAM, 1 GB disk — VM overhead is unjustified
│   └── pct_remote connection: no SSH server, no bootstrap IP, no ProxyJump
│
├── Base image: Debian 12
│   ├── Consistent with all other containers
│   ├── wireguard-tools in official repos (apt install, no PPAs)
│   └── Kernel 6.x includes WireGuard module natively
│
├── Configuration method: pct exec from Proxmox host
│   ├── community.proxmox.proxmox_pct_remote connection plugin
│   ├── No SSH needed inside the container — simpler than VM bootstrap
│   └── Eliminates SSH auth transition concerns from VM workflows
│
├── WireGuard kernel module: loaded on Proxmox host
│   ├── LXC containers share the host kernel — no in-container module loading
│   ├── modprobe wireguard on host before container starts wg-quick
│   ├── Persist via /etc/modules-load.d/wireguard.conf for reboots
│   └── Unprivileged LXC containers can create wg interfaces with host module loaded
│
├── Container networking: LAN bridge with DHCP
│   ├── proxmox_lxc defaults to proxmox_all_bridges[1] (first LAN bridge)
│   ├── Container gets IP from OpenWrt DHCP on the LAN
│   ├── wg0 tunnel interface runs inside the container
│   └── No special bridge needed — standard LAN connectivity
│
├── Routing strategy: NAT on wg0, selective routing by consuming services
│   ├── WireGuard container enables IP forwarding and MASQUERADE on wg0
│   ├── Other services (rsyslog, Netdata) configure their own routes to
│   │   send specific traffic through the WireGuard container's LAN IP
│   ├── OpenWrt does NOT need policy routing — each service decides locally
│   └── Full-tunnel (AllowedIPs 0.0.0.0/0) is supported but not default
│
├── Key generation: auto-generate with .env.generated storage
│   ├── If WIREGUARD_PRIVATE_KEY is set in .env, use provided values (production)
│   ├── If not set, generate client keypair inside the container after
│   │   wireguard-tools is installed (wg genkey / wg pubkey)
│   ├── Generated keys written to .env.generated on the controller
│   │   (gitignored, delegate_to: localhost)
│   ├── User copies values from .env.generated → .env for persistence
│   ├── Server-side vars (endpoint, server pubkey) also auto-generated
│   │   with dummy values when empty — produces a valid but non-connecting config
│   ├── .env.generated accumulates across services (append, not overwrite)
│   └── Molecule cleanup removes .env.generated (fresh keys each test run)
│
├── Test strategy: config-valid, no tunnel connectivity
│   ├── Test runs auto-generate all keys (no env vars needed in test.env)
│   ├── Dummy server endpoint uses RFC 5737 198.51.100.1:51820 (non-routable)
│   ├── Verify: wg0 interface exists, config file correct, service enabled
│   ├── Do NOT verify: handshake completion, tunnel data transfer
│   └── .env.generated is written during test, cleaned up afterwards
│
└── Env variable pattern: optional with auto-generation fallback
    ├── All WIREGUARD_* vars are optional (lookup + default in role defaults)
    ├── If required vars missing, role generates client keys and uses
    │   dummy server values — always produces a working config
    ├── NEVER add to REQUIRED_ENV in build.py (VPN is flavor-specific)
    └── Generated values stored in .env.generated for user to harvest
```

---

## Milestone Dependency Graph

```
M1: LXC Provisioning ─────── self-contained
 └── M2: WireGuard Config ── self-contained, depends on M1
      └── M3: Testing ─────── self-contained, depends on M1+M2
           └── M4: Docs ───── self-contained, depends on M1–M3
```

---

## Milestones

### Milestone 1: LXC Provisioning

_Self-contained. No external dependencies._

Create the `wireguard_lxc` role as a thin wrapper around `proxmox_lxc`,
add the provision play to `site.yml`, and verify the container runs.

See: `vm-lifecycle` skill (LXC provisioning pattern, deploy_stamp).

**Implementation pattern:**
- Role: `roles/wireguard_lxc/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `vpn_nodes`, tagged `[wireguard]`
- deploy_stamp included as last role in the provision play
- Dynamic group `wireguard` populated by `proxmox_lxc` via `add_host`

**Already complete** (from shared infrastructure project 00):
- `wireguard_ct_id: 101` in `group_vars/all.yml`
- `vpn_nodes` flavor group and `wireguard` dynamic group in `inventory/hosts.yml`
- `vpn_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_lxc` role operational with `pct_remote` connection support

- [ ] Create `roles/wireguard_lxc/defaults/main.yml`:
  - `wireguard_ct_hostname: wireguard`
  - `wireguard_ct_memory: 128`, `wireguard_ct_cores: 1`, `wireguard_ct_disk: "1"`
  - `wireguard_ct_template: "{{ proxmox_lxc_default_template }}"`
  - `wireguard_ct_onboot: true`, `wireguard_ct_startup_order: 2`
- [ ] Create `roles/wireguard_lxc/tasks/main.yml`:
  - Load `wireguard` kernel module on Proxmox host (`ansible.builtin.command:
    modprobe wireguard`, delegated to inventory_hostname since the play
    already targets `vpn_nodes` / Proxmox)
  - Persist module across reboots: write `/etc/modules-load.d/wireguard.conf`
  - Include `proxmox_lxc` role with service-specific vars:
    `lxc_ct_id: "{{ wireguard_ct_id }}"`, `lxc_ct_hostname: wireguard`,
    `lxc_ct_dynamic_group: wireguard`, `lxc_ct_memory`, `lxc_ct_cores`,
    `lxc_ct_disk`, `lxc_ct_onboot`, `lxc_ct_startup_order`
- [ ] Create `roles/wireguard_lxc/meta/main.yml` with required metadata
  (`author`, `license: proprietary`, `role_name`, `description`,
  `min_ansible_version`, `platforms`)
- [ ] Add provision play to `site.yml` targeting `vpn_nodes`, tagged
  `[wireguard]`, with `wireguard_lxc` role and `deploy_stamp` (Play 4,
  after Configure OpenWrt, before cleanup)
- [ ] Verify Debian 12 LXC template exists in `images/` directory
  (prerequisite — document download URL in setup instructions)

**Verify:**

- [ ] Container 101 is running: `pct status 101` returns `running`
- [ ] Container is in `wireguard` dynamic group (`add_host` registered)
- [ ] `pct_remote` connection works: `ansible.builtin.ping` succeeds
- [ ] Auto-start configured: `pct config 101` shows `onboot: 1`,
  `startup: order=2`
- [ ] Idempotent: re-run skips creation, container still running
- [ ] `wireguard` kernel module loaded on host: `lsmod` shows `wireguard`
- [ ] `/etc/modules-load.d/wireguard.conf` exists on host
- [ ] deploy_stamp contains `wireguard_lxc` play entry

**Rollback:**

Container destruction handled by generic LXC cleanup in
`molecule/default/cleanup.yml` (`pct list` iteration → `pct stop` +
`pct destroy`). Host-side cleanup:
- Remove `/etc/modules-load.d/wireguard.conf` from Proxmox host
- `modprobe -r wireguard` on host (safe if no other consumers)

---

### Milestone 2: WireGuard Configuration

_Self-contained. Depends on M1 (container must be running)._

Configure the running container with WireGuard tunnel credentials,
IP forwarding, and NAT. Auto-generates keys when env vars are not
provided and stores them in `.env.generated` for the user to harvest.

See: `vm-lifecycle` skill (LXC configure connection, pct_remote pattern).

**Implementation pattern:**
- Role: `roles/wireguard_configure/defaults/main.yml`, `tasks/main.yml`,
  `templates/wg0.conf.j2`, `meta/main.yml`
- site.yml: configure play targeting `wireguard` dynamic group, tagged
  `[wireguard]`, after the provision play

**Env variables** (all optional — auto-generated when empty):

| Variable | Auto-generated | Purpose | Example |
|----------|---------------|---------|---------|
| `WIREGUARD_PRIVATE_KEY` | yes (wg genkey) | Client private key | `oK56DE...=` |
| `WIREGUARD_CLIENT_ADDRESS` | yes (default: 10.0.0.2/24) | Client tunnel IP | `10.0.0.2/24` |
| `WIREGUARD_SERVER_PUBLIC_KEY` | yes (dummy keypair) | Server public key | `Xk9B3m...=` |
| `WIREGUARD_SERVER_ENDPOINT` | yes (dummy: 198.51.100.1:51820) | Server host:port | `vpn.example.com:51820` |
| `WIREGUARD_ALLOWED_IPS` | yes (default: 10.0.0.0/24) | Routed subnets | `10.0.0.0/24` |
| `WIREGUARD_PRESHARED_KEY` | no (omitted if empty) | Optional PSK | `pR45xA...=` |
| `WIREGUARD_DNS` | no (omitted if empty) | Tunnel DNS servers | `1.1.1.1` |
| `WIREGUARD_KEEPALIVE` | yes (default: 25) | Keepalive seconds | `25` |

When auto-generating:
- Client keypair generated inside the container via `wg genkey` / `wg pubkey`
- Dummy server keypair also generated (config is valid but tunnel won't connect)
- All generated values written to `.env.generated` on the controller
- `.env.generated` is gitignored; user copies needed values to `.env` for
  subsequent runs

- [ ] Create `roles/wireguard_configure/defaults/main.yml`:
  - All env vars via `lookup('env', ...) | default('', true)`
  - `wireguard_keepalive` defaults to `25`
  - `wireguard_allowed_ips` defaults to `10.0.0.0/24`
  - `wireguard_client_address` defaults to `10.0.0.2/24`
  - `wireguard_server_endpoint` defaults to `198.51.100.1:51820`
- [ ] Create `roles/wireguard_configure/tasks/main.yml`:
  - **Key generation phase** (runs when `WIREGUARD_PRIVATE_KEY` is empty):
    - Generate client keypair inside the container:
      `wg genkey` → private key, pipe to `wg pubkey` → public key
    - Generate dummy server keypair for config validity:
      `wg genkey` → dummy server private, pipe to `wg pubkey` → dummy server public
    - Set facts for all generated values
    - Write `.env.generated` on controller via `delegate_to: localhost`
      (append mode — does not overwrite other services' entries):
      ```
      # WireGuard (auto-generated <timestamp>)
      WIREGUARD_PRIVATE_KEY=<generated>
      WIREGUARD_PUBLIC_KEY=<generated>
      WIREGUARD_CLIENT_ADDRESS=10.0.0.2/24
      WIREGUARD_SERVER_PUBLIC_KEY=<generated-dummy>
      WIREGUARD_SERVER_ENDPOINT=198.51.100.1:51820
      WIREGUARD_ALLOWED_IPS=10.0.0.0/24
      ```
    - Include client PUBLIC key in `.env.generated` (needed for server-side
      config — the server admin uses this to add the client as a peer)
  - **Configuration phase** (runs always, using provided or generated values):
    - Install `wireguard-tools` via `apt-get update && apt-get install -y
      wireguard-tools` (with `retries: 3` and `delay: 5` — container apt
      may need time for initial package list fetch)
    - Template `/etc/wireguard/wg0.conf` from `templates/wg0.conf.j2`
      with `mode: '0600'`:
      `[Interface]` with PrivateKey, Address, optional DNS;
      `[Peer]` with PublicKey, Endpoint, AllowedIPs, PersistentKeepalive,
      optional PresharedKey
    - Enable and start service: `systemctl enable --now wg-quick@wg0`
    - Enable IP forwarding (persistent across reboots):
      write `/etc/sysctl.d/99-wireguard.conf` with `net.ipv4.ip_forward=1`,
      then `sysctl -p /etc/sysctl.d/99-wireguard.conf`
    - Configure NAT for forwarded traffic:
      `iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE` (check first)
      or `-A` if not present; install `iptables-persistent` for auto-restore
      on boot; `netfilter-persistent save`
- [ ] Create `roles/wireguard_configure/templates/wg0.conf.j2`
- [ ] Create `roles/wireguard_configure/meta/main.yml` with required metadata
- [ ] Add configure play to `site.yml` targeting `wireguard` dynamic group,
  tagged `[wireguard]`, `gather_facts: true`, after the provision play
- [ ] Ensure idempotency: all tasks safe to re-run (key generation skipped
  when env vars present, iptables check-before-add, systemctl enable is
  idempotent, template overwrite is safe, sysctl write is idempotent)

**Verify:**

- [ ] `wg0` interface exists: `ip link show wg0` succeeds
- [ ] Config file exists with correct permissions:
  `/etc/wireguard/wg0.conf` mode `0600`
- [ ] `wg show wg0` reports configured endpoint and allowed IPs
- [ ] `wg-quick@wg0` service enabled: `systemctl is-enabled wg-quick@wg0`
- [ ] IP forwarding active: `sysctl net.ipv4.ip_forward` returns `1`
- [ ] IP forwarding persistent: `/etc/sysctl.d/99-wireguard.conf` exists
- [ ] NAT rule present: `iptables -t nat -L POSTROUTING` shows MASQUERADE
  on wg0
- [ ] `.env.generated` exists on controller with WireGuard section
  (when keys were auto-generated)
- [ ] `.env.generated` contains client PUBLIC key (for server-side config)
- [ ] Idempotent: second run does not regenerate keys when env vars are set

**Rollback:**

- Stop and disable service: `systemctl disable --now wg-quick@wg0`
- Remove config: `rm -f /etc/wireguard/wg0.conf`
- Remove sysctl override:
  `rm -f /etc/sysctl.d/99-wireguard.conf && sysctl -w net.ipv4.ip_forward=0`
- Remove NAT rules:
  `iptables -t nat -D POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null`
- Remove persistent iptables: `rm -f /etc/iptables/rules.v4`
- Uninstall packages: `apt-get remove -y wireguard-tools iptables-persistent`
- Remove `.env.generated` on controller (delegate_to: localhost)
- Full container destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Self-contained. Depends on M1 and M2._

Wire up molecule testing, create the per-feature scenario, and verify
end-to-end. No manual WireGuard env vars needed in test.env — keys are
auto-generated during converge and cleaned up afterwards.

See: `ansible-testing` skill (per-feature scenario setup, verify
completeness, fact scoping).

- [ ] Add `.env.generated` to `.gitignore`
- [ ] Add `WIREGUARD_PRIVATE_KEY` and other WireGuard vars to molecule
  provisioner env in `molecule/default/molecule.yml` (empty values —
  triggers auto-generation):
  ```yaml
  WIREGUARD_PRIVATE_KEY: ${WIREGUARD_PRIVATE_KEY:-}
  WIREGUARD_CLIENT_ADDRESS: ${WIREGUARD_CLIENT_ADDRESS:-}
  WIREGUARD_SERVER_PUBLIC_KEY: ${WIREGUARD_SERVER_PUBLIC_KEY:-}
  WIREGUARD_SERVER_ENDPOINT: ${WIREGUARD_SERVER_ENDPOINT:-}
  ```
- [ ] Extend `molecule/default/verify.yml` with WireGuard assertions
  (all items from M1 and M2 verify sections, run from Proxmox host
  via `pct exec 101 --` commands)
- [ ] Verify generic container cleanup in `molecule/default/cleanup.yml`
  handles VMID 101 (already iterates `pct list` — confirm)
- [ ] Add host cleanup for `/etc/modules-load.d/wireguard.conf` to BOTH
  `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`
  (cleanup completeness rule)
- [ ] Add `.env.generated` cleanup to `molecule/default/cleanup.yml`
  (delegate_to: localhost, remove file)
- [ ] Create `tasks/reconstruct_wireguard_group.yml`:
  - Verify container 101 is running (`pct status {{ wireguard_ct_id }}`)
  - Register via `add_host` with:
    `ansible_connection: community.proxmox.proxmox_pct_remote`,
    `ansible_host: {{ ansible_host }}` (Proxmox host IP),
    `proxmox_vmid: {{ wireguard_ct_id }}`,
    `ansible_user: root`
  - Simpler than OpenWrt reconstruction (no SSH auth detection needed —
    pct_remote is always the connection method)
- [ ] Create `molecule/wireguard-lxc/` per-feature scenario:
  - `molecule.yml`: same platform as default, no initial cleanup phase,
    `vpn_nodes` in groups, WireGuard env vars in provisioner env (empty)
  - `converge.yml`: reconstruct wireguard group, run wireguard_configure
  - `verify.yml`: reconstruct wireguard group, run WireGuard assertions
  - `cleanup.yml`: destroy container 101 (`pct stop` + `pct destroy`),
    remove `.env.generated` on controller
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [ ] Update `build.py` docstring with `wireguard` tag
- [ ] Run `molecule test` (full integration) — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `molecule test -s wireguard-lxc` passes (per-feature scenario)
- [ ] No WireGuard env vars needed in `test.env` (auto-generation works)
- [ ] `.env.generated` created during converge, removed during cleanup
- [ ] Verify assertions cover all categories: container state, auto-start,
  network config, service state, IP forwarding, NAT, deploy_stamp
- [ ] Cleanup leaves no WireGuard artifacts on host or controller

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 4: Documentation

_Self-contained. Run after all implemented milestones._

- [ ] Create `docs/architecture/wireguard-build.md`:
  - Requirements, design decisions, env variables
  - Key generation flow and `.env.generated` pattern
  - Routing strategy: how other services route through the tunnel
  - Test vs production workflow differences
  - Kernel module requirements on the Proxmox host
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add WireGuard provision + configure plays
  - Verify network topology diagram includes WireGuard container
- [ ] Update `docs/architecture/roles.md`:
  - Add `wireguard_lxc` role documentation (purpose, key variables)
  - Add `wireguard_configure` role documentation (purpose, env vars,
    key generation, routing, NAT)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add WireGuard project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] `.env.generated` pattern documented in wireguard-build.md
- [ ] All env variables documented with auto-generation behavior

**Rollback:** N/A — documentation-only milestone.

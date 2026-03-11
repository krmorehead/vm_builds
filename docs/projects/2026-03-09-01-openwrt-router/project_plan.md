# OpenWrt Router

## Overview

The OpenWrt router VM is the foundation of the network stack. Already
provisioned and configured by the existing `openwrt_vm` and `openwrt_configure`
roles, it serves as the **baseline** that all future work builds on. This
project covers three concerns:

1. **Test & rollback infrastructure** — establish the baseline snapshot model,
   per-feature molecule scenarios, and per-feature rollback conventions that
   every subsequent project inherits.
2. **Security hardening & feature additions** — root credentials, SSH lockdown,
   intrusion prevention, VLANs, encrypted DNS, mesh enhancements.
3. **Integration points** — Pi-hole DNS forwarding, syslog forwarding, and
   metrics export (blocked on their respective LXC projects).

## Type

VM (KVM/QEMU) — already implemented

## Resources

- Cores: 2
- RAM: 512 MB
- Disk: 512 MB
- Network: all bridges (WAN on eth0, LAN on remaining bridges)
- PCI: WiFi passthrough (when hardware present)
- VMID: 100

## Startup

- Auto-start: yes
- Boot priority: 1 (must start first — all services depend on network)
- Depends on: Proxmox host only

## Build Profiles

- Home Entertainment Box: yes (core)
- Minimal Router: yes (core)
- Gaming Rig: no

## Prerequisites

- None — this is the existing foundation

## Skills

| Skill | When to use |
|-------|-------------|
| `openwrt-build` | UCI config, firewall zones, bootstrap, restart patterns |
| `ansible-testing` | Molecule scenarios, baseline testing, TDD iteration |
| `rollback-patterns` | Per-feature rollback, deploy_stamp tracking, baseline concept |
| `vm-lifecycle` | Two-role pattern, deploy_stamp, cleanup completeness |
| `proxmox-host-safety` | Safe commands, bridge teardown, cleanup file lists |
| `project-planning` | Milestone structure and conventions |

---

## Architectural Decisions

```
Decisions
├── Baseline testing model
│   ├── "Router provisioned + configured" = reusable baseline state
│   ├── molecule/default/ rebuilds everything (full integration, ~5 min)
│   ├── molecule/<feature>/ starts from baseline (per-feature, ~30-60s)
│   └── Per-feature rollback returns to baseline without rebuild
│
├── Rollback strategy: per-feature tags in cleanup.yml
│   ├── Each feature has apply tag (site.yml) and rollback tag (cleanup.yml)
│   ├── Rollback tags use `never` meta-tag — only run when explicitly requested
│   ├── UCI changes reversed with uci delete/set + commit
│   └── Full restore (existing) remains the escape hatch
│
├── Intrusion prevention: banIP
│   └── OpenWrt-native, lightweight, maintained in official packages
│
├── DNS strategy: two phases
│   ├── Phase 1 (M3, self-contained): https-dns-proxy for encrypted upstream
│   │   └── Lightweight DoH proxy, no dependency on Pi-hole
│   └── Phase 2 (M5, blocked): forward dnsmasq to Pi-hole
│       └── Requires pihole_lxc provisioning (separate project)
│
├── VLAN topology
│   ├── VLAN 1 (untagged): management / trusted devices
│   ├── VLAN 10: IoT (restricted internet, no LAN access)
│   └── VLAN 20: guest (internet only, fully isolated)
│
├── Client steering: Dawn (802.11k/v/r)
│   └── OpenWrt-native via ubus, real-time RSSI-based steering per AP node
│
├── Feature integration: task files in openwrt_configure
│   ├── Each feature is a task file in openwrt_configure/tasks/
│   │   (security.yml, vlans.yml, dns.yml, mesh.yml)
│   ├── main.yml remains the baseline configuration (unchanged)
│   ├── site.yml adds one play per feature targeting the openwrt dynamic group
│   │   using include_role with tasks_from to run the specific task file
│   ├── deploy_stamp runs in a paired play targeting router_nodes (Proxmox host)
│   │   since the stamp file lives on the host, not in the VM
│   └── Feature defaults live in openwrt_configure/defaults/main.yml
│       alongside existing baseline defaults
│
├── Per-feature scenario bootstrap (dynamic group reconstruction)
│   ├── Per-feature converge.yml starts with a "baseline check" play
│   │   targeting router_nodes (Proxmox host)
│   │   ├── Runs qm status on VM 100 — fails fast if VM not running
│   │   ├── Reads the VM's LAN IP from the Proxmox LAN bridge
│   │   ├── Detects auth mode: if OPENWRT_SSH_PRIVATE_KEY is set
│   │   │   and deploy_stamp shows openwrt_security was applied,
│   │   │   use key auth; otherwise use sshpass (empty password)
│   │   └── Reconstructs the openwrt dynamic group via add_host with
│   │       ProxyJump, ConnectTimeout=10, ServerAliveInterval=15
│   │       (per proxmox-host-safety skill SSH stability requirements)
│   ├── Same reconstruction is needed in per-feature verify.yml
│   │   (facts don't persist across Molecule phases)
│   └── Extracted into reusable task file to avoid duplication across
│       converge, verify, and cleanup entry points
│
├── Dynamic group persistence across invocations
│   ├── The openwrt dynamic group is ephemeral (add_host during converge)
│   ├── Rollback, per-feature converge, and per-feature verify all run
│   │   as separate ansible-playbook invocations — group is empty
│   ├── Solution: every entry point that needs the openwrt group
│   │   starts with a reconstruction play on router_nodes/proxmox
│   │   that discovers the VM's LAN IP and calls add_host
│   ├── Auth mode detection: check OPENWRT_SSH_PRIVATE_KEY env var
│   │   and whether security hardening was applied (deploy_stamp)
│   └── This reconstruction play is a reusable pattern: extract into
│       a shared task file or include_tasks to avoid duplication
│
└── cleanup.sh rollback extension
    ├── New subcommand: ./cleanup.sh rollback <feature-name>
    ├── Maps to: build.py --playbook cleanup --tags <feature-name>-rollback
    └── Feature names validated by build.py (known list from cleanup.yml tags)
```

---

## Milestone Dependency Graph

```
M0: Baseline & Test Infrastructure
 └── M1: Security Hardening  ─────────────────────── self-contained
      └── M2: VLAN Support  ──────────────────────── self-contained
           └── M3: Encrypted DNS  ─────────────────── self-contained
                └── M4: Multi-Node Mesh  ──────────── self-contained (needs WiFi HW)
                     ├── M5: Pi-hole DNS Forwarding ── blocked on pihole_lxc
                     ├── M6: Syslog Forwarding  ────── blocked on rsyslog_lxc
                     └── M7: Monitoring Export  ────── blocked on monitoring
```

Self-contained milestones (M0–M4) are ordered by logical dependency:
security before VLANs (VLANs inherit security posture), VLANs before DNS
(DNS zones may be VLAN-aware), DNS before mesh (mesh nodes need consistent
DNS). Blocked milestones (M5–M7) are fully specified and ready to implement
once their blockers resolve.

---

## Milestones

### Milestone 0: Baseline & Test Infrastructure

_Self-contained. No external dependencies._

Establish the "router provisioned and configured" state as the reusable
baseline for all future work. Add molecule scenario infrastructure for
per-feature testing and per-feature rollback support in `cleanup.yml`.
This milestone produces no user-visible features but is the foundation
for every subsequent milestone and project.

See: `ansible-testing` skill (baseline model, scenario setup),
`rollback-patterns` skill (rollback layers, tag conventions).

**Baseline definition:**

- [ ] Document the baseline state in `docs/architecture/baseline.md`:
      what `site.yml` produces, what assertions cover it, what invariants
      future features must preserve
- [ ] Audit existing `molecule/default/verify.yml` against the baseline
      definition — add any missing assertions
- [ ] Ensure deploy_stamp assertions cover all expected plays
      (`backup`, `infrastructure`, `openwrt_vm`)

**Molecule scenario infrastructure:**

- [ ] Create `molecule/openwrt-security/` scenario (template for all
      per-feature scenarios):
  - `molecule.yml`: same platform as default, no initial cleanup phase
  - `converge.yml`:
    - Play 1 (`router_nodes`): verify VM 100 is running via `qm status`,
      fail fast with clear error if baseline missing, reconstruct
      `openwrt` dynamic group via `add_host` with the same ProxyJump
      SSH config and connection vars that `openwrt_vm` uses
    - Play 2 (`openwrt`): run security tasks via `include_role` with
      `tasks_from: security.yml`
  - `verify.yml`: starts with the same group reconstruction play
    (facts from converge don't persist across Molecule phases —
    see ansible-testing skill, fact scoping section), then runs
    security-specific assertions against the `openwrt` group
  - `cleanup.yml`: run `playbooks/cleanup.yml --tags openwrt-security-rollback`
- [ ] Extract the dynamic group reconstruction logic into a reusable
      task file (e.g., `roles/openwrt_configure/tasks/reconstruct_group.yml`)
      so converge, verify, and cleanup can all `include_tasks` without
      duplicating the VM discovery + auth detection + `add_host` logic
- [ ] Document the scenario hierarchy and workflow in the ansible-testing
      skill (already drafted — validate against implementation)

**Rollback infrastructure:**

- [ ] Add a shared "Reconstruct openwrt dynamic group" play to
      `playbooks/cleanup.yml` that discovers the running VM's LAN IP
      and calls `add_host` (same pattern as per-feature converge.yml).
      Tag it with ALL rollback tags so it runs before any rollback play.
      Must detect auth mode (key vs password) for SSH args — same
      detection logic as per-feature scenario bootstrap.
- [ ] Add per-feature rollback play stubs to `playbooks/cleanup.yml`:
  - `openwrt-security-rollback`
  - `openwrt-vlans-rollback`
  - `openwrt-dns-rollback`
  - `openwrt-mesh-rollback`
  - Each tagged with `[<name>, never]` to prevent accidental execution
  - Each MUST check `deploy_stamp` before undoing: skip rollback if
    the feature was never applied (rollback-patterns skill, rule 3)
- [ ] Add `project_version` field to backup manifest in `proxmox_backup`
      role (enables version-aware restore decisions)
- [ ] Extend `cleanup.sh` with a `rollback` subcommand that passes
      per-feature tags through `build.py` to `ansible-playbook`
      (e.g., `./cleanup.sh rollback openwrt-security`)
- [ ] Add pytest coverage for the new `rollback` subcommand in
      `tests/test_build.py`

**Verify:**

- [ ] `molecule test` (default) still passes end-to-end
- [ ] WiFi PCIe passthrough still works when `proxmox_igpu` role runs on
      same host (relocated from shared-infrastructure M2)
- [ ] `molecule converge -s openwrt-security` succeeds when baseline exists
- [ ] `molecule converge -s openwrt-security` fails fast with clear error
      when baseline does not exist
- [ ] Per-feature rollback tags are reachable:
      `ansible-playbook playbooks/cleanup.yml --list-tags` shows them
- [ ] `./cleanup.sh rollback openwrt-security` passes the tag through
      `build.py` to `ansible-playbook` (pytest + manual verification)
- [ ] Backup manifest contains `project_version`

**Rollback:** N/A — this milestone adds rollback infrastructure itself.

---

### Milestone 1: Security Hardening

_Self-contained. No external dependencies._

Harden the OpenWrt VM: root credentials, SSH lockdown, intrusion prevention,
and firewall tightening. All changes are UCI-based and reversible via the
`openwrt-security-rollback` tag.

See: `openwrt-build` skill (UCI patterns, opkg installation, firewall zones).

**Implementation pattern:** Create `roles/openwrt_configure/tasks/security.yml`
with all security tasks below. Add two new plays to `site.yml`:
(1) a configure play targeting `openwrt` that uses `include_role:
openwrt_configure` with `tasks_from: security.yml`, and (2) a deploy_stamp
play targeting `router_nodes`. Both tagged `[openwrt-security]`.

**SSH auth transition (critical ordering):** The baseline connects to OpenWrt
via `sshpass` (empty password) with `PubkeyAuthentication=no`. After this
milestone disables password auth, that connection method breaks. The task
file must:
1. Deploy the public key (while still connected via password auth)
2. Verify key auth works by reconnecting with the key
3. Disable password auth in dropbear
4. Re-register the `openwrt` host via `add_host` with updated SSH args
   (remove `sshpass`, remove `PubkeyAuthentication=no`, add
   `ansible_ssh_private_key_file`)
Subsequent plays (M2+) pick up the new args automatically.

Per-feature scenarios (M2+) must detect the auth mode during their
baseline check play: try key auth first (check if `OPENWRT_SSH_PRIVATE_KEY`
is set and the key file exists), fall back to `sshpass` if not.

- [ ] Create `roles/openwrt_configure/tasks/security.yml` task file
- [ ] Add security play + paired deploy_stamp play to `site.yml`
      (tagged `[openwrt-security]`, after the existing `Configure OpenWrt` play)
- [ ] Add `OPENWRT_ROOT_PASSWORD` as optional env var in
      `openwrt_configure` role defaults (via `lookup('env', ...) | default`)
- [ ] Add `OPENWRT_SSH_PUBKEY` as optional env var (path to public key
      file or inline key content); write to `/etc/dropbear/authorized_keys`
- [ ] Add `OPENWRT_SSH_PRIVATE_KEY` as optional env var (path to the
      corresponding private key file on the controller); used for
      `ansible_ssh_private_key_file` after lockdown
- [ ] Set root password via `passwd` on the VM (only when
      `OPENWRT_ROOT_PASSWORD` is set)
- [ ] Verify key-based SSH works before proceeding (ordering is critical:
      key deployment MUST succeed before password auth is disabled, or
      the VM becomes unreachable and requires full rebuild)
- [ ] Disable password auth in dropbear (only when `OPENWRT_SSH_PUBKEY`
      is set — skip lockdown entirely if no key is provided):
  - `uci set dropbear.@dropbear[0].PasswordAuth='off'`
  - `uci set dropbear.@dropbear[0].RootPasswordAuth='off'`
  - `uci commit dropbear && /etc/init.d/dropbear restart`
- [ ] Restrict SSH to LAN zone only:
  - Remove or disable WAN→SSH firewall rule
  - Verify LAN→SSH remains open
- [ ] Install and configure banIP for intrusion prevention:
  - `opkg update && opkg install banip` with retries + delay per
    openwrt-build skill rule 4 (opkg feeds already switched to HTTP by
    `openwrt_configure` Phase 2; banIP's own blocklist downloads use
    `uclient-fetch` which supports HTTP feeds — no additional feed
    changes needed)
  - Configure blocklists via UCI (`uci set banip.global.ban_sources=...`)
  - `uci commit banip && /etc/init.d/banip enable && /etc/init.d/banip start`
- [ ] Add firewall hardening rules:
  - `uci set firewall.@defaults[0].syn_flood='1'`
  - `uci set firewall.@defaults[0].drop_invalid='1'`
  - `uci commit firewall && /etc/init.d/firewall restart`
- [ ] Re-register `openwrt` host via `add_host` with key-based SSH args
      after lockdown (remove `sshpass`, remove `PubkeyAuthentication=no`,
      set `ansible_ssh_private_key_file` to `OPENWRT_SSH_PRIVATE_KEY`)
      so subsequent plays (M2+) use the new auth method
- [ ] Ensure idempotency: all tasks must be safe to re-run when hardening
      is already applied (check before set pattern for dropbear, banIP,
      firewall defaults)
- [ ] Register `openwrt_security` play in deploy_stamp

**Verify:**

- [ ] SSH key auth works from Proxmox host (when `OPENWRT_SSH_PUBKEY` set)
- [ ] SSH password auth rejected (when key auth is configured)
- [ ] SSH from WAN zone rejected (TCP connect test from WAN bridge)
- [ ] banIP service running: `/etc/init.d/banip status` returns running
- [ ] SYN flood protection enabled: `uci get firewall.@defaults[0].syn_flood` = 1
- [ ] Invalid packet drop enabled: `uci get firewall.@defaults[0].drop_invalid` = 1
- [ ] deploy_stamp contains `openwrt_security` play entry

**Rollback (`--tags openwrt-security-rollback`):**

- Remove banIP package (`opkg remove banip`)
- Re-enable password auth (`uci delete dropbear.@dropbear[0].PasswordAuth`,
  `uci delete dropbear.@dropbear[0].RootPasswordAuth`)
- Remove `/etc/dropbear/authorized_keys`
- Clear root password to restore empty-password baseline
  (`sed -i 's/^root:[^:]*:/root::/' /etc/shadow` — returns to factory
  default where `sshpass` with empty password works)
- Revert firewall hardening (`uci delete firewall.@defaults[0].syn_flood`,
  `uci delete firewall.@defaults[0].drop_invalid`)
- Restore WAN→SSH rule if it was removed
- `uci commit && /etc/init.d/dropbear restart && /etc/init.d/firewall restart`

---

### Milestone 2: VLAN Support

_Self-contained. No external dependencies._

Segment the LAN into management, IoT, and guest VLANs with per-VLAN
firewall zones and DHCP pools. VLAN IDs and subnets are defined in
role defaults for easy override via `host_vars`.

See: `openwrt-build` skill (UCI patterns, firewall zones).

**Implementation note:** Since this is a virtualized OpenWrt (no physical
switch chip), VLANs use standard Linux 802.1Q VLAN devices on the bridge
ports, not DSA or swconfig. The UCI model is `network.device` with `type
8021q` parent on `br-lan` ports. Proxmox virtual bridges pass tagged
frames by default — no VLAN-aware bridge mode changes needed on the host.
The Proxmox management IP remains on the untagged management VLAN
(existing `lan` zone), preserving GUI access.

**Implementation pattern:** Create `roles/openwrt_configure/tasks/vlans.yml`.
Add two new plays to `site.yml` (tagged `[openwrt-vlans]`): one targeting
`openwrt` for configuration, one targeting `router_nodes` for deploy_stamp.

- [ ] Create `roles/openwrt_configure/tasks/vlans.yml` task file
- [ ] Add VLAN play + paired deploy_stamp play to `site.yml`
      (tagged `[openwrt-vlans]`, after the security play)
- [ ] Create `molecule/openwrt-vlans/` scenario (follows template from M0)
- [ ] Define VLAN parameters in `openwrt_configure` role defaults:
  - `openwrt_vlan_iot_id: 10`, `openwrt_vlan_iot_subnet: 10.10.20.0/24`
  - `openwrt_vlan_guest_id: 20`, `openwrt_vlan_guest_subnet: 10.10.30.0/24`
  - Subnets chosen to avoid collision with WAN and management LAN
- [ ] Configure 802.1Q VLAN devices via UCI:
  - Create `network.vlan_iot` and `network.vlan_guest` interface sections
  - Attach to LAN bridge interfaces with VLAN tagging
- [ ] Create firewall zones per VLAN:
  - `vlan_iot` zone: forwarding to WAN allowed, forwarding to LAN denied
  - `vlan_guest` zone: forwarding to WAN allowed, all other forwarding denied
  - Management VLAN uses existing `lan` zone (no change)
- [ ] Set up DHCP pools per VLAN:
  - VLAN 10 (IoT): pool range from role defaults
  - VLAN 20 (Guest): pool range from role defaults
- [ ] Map mesh WiFi SSIDs to VLANs (when WiFi hardware present):
  - IoT SSID → VLAN 10
  - Guest SSID → VLAN 20
  - Management SSID → untagged (existing)
- [ ] Register `openwrt_vlans` play in deploy_stamp

**Verify:**

- [ ] VLAN interfaces exist on the VM (`ip link` shows vlan10, vlan20)
- [ ] Firewall zones configured: `uci show firewall` contains `vlan_iot`
      and `vlan_guest` zones
- [ ] DHCP pools active per VLAN subnet (check `uci show dhcp`)
- [ ] IoT zone cannot reach management subnet (cross-zone traffic test
      via `iptables -L` or nftables rule inspection)
- [ ] Guest zone has internet forwarding but no local access
- [ ] deploy_stamp contains `openwrt_vlans` play entry

**Rollback (`--tags openwrt-vlans-rollback`):**

- Delete VLAN interface UCI sections (`uci delete network.vlan_iot`, etc.)
- Delete VLAN firewall zones and forwarding rules
- Delete VLAN DHCP pools
- Remove VLAN-tagged WiFi SSIDs (if created)
- `uci commit && /etc/init.d/network restart && /etc/init.d/firewall restart`

---

### Milestone 3: Encrypted DNS

_Self-contained. No dependency on Pi-hole._

Install `https-dns-proxy` for encrypted upstream DNS resolution via DNS
over HTTPS (DoH). This is the self-contained DNS improvement that ships
independently. Pi-hole forwarding (M5) layers on top when available.

See: `openwrt-build` skill (opkg installation, dnsmasq config).

**Implementation pattern:** Create `roles/openwrt_configure/tasks/dns.yml`.
Add two new plays to `site.yml` (tagged `[openwrt-dns]`): one targeting
`openwrt` for configuration, one targeting `router_nodes` for deploy_stamp.

**Integration note:** `https-dns-proxy` auto-configures dnsmasq on install
(sets `noresolv` and adds its local listener as a server). The tasks below
make this explicit via UCI to ensure idempotency and allow customization
of resolver addresses. If the auto-config already applied the settings,
the UCI set commands are no-ops.

- [ ] Create `roles/openwrt_configure/tasks/dns.yml` task file
- [ ] Add DNS play + paired deploy_stamp play to `site.yml`
      (tagged `[openwrt-dns]`, after the VLAN play)
- [ ] Create `molecule/openwrt-dns/` scenario (follows template from M0)
- [ ] Install `https-dns-proxy` package (with retries + delay per
      openwrt-build skill rule 4):
  - `opkg update && opkg install https-dns-proxy`
- [ ] Configure upstream DoH resolvers:
  - Primary: Cloudflare (`1.1.1.1`, `1.0.0.1`)
  - Fallback: Google (`8.8.8.8`, `8.8.4.4`)
  - Configuration via UCI: `uci set https-dns-proxy.@https-dns-proxy[0]...`
- [ ] Configure dnsmasq to forward to local `https-dns-proxy`:
  - `uci set dhcp.@dnsmasq[0].noresolv='1'`
  - Add server entries pointing to `127.0.0.1#5053` (https-dns-proxy port)
- [ ] Add DNS rebinding protection in dnsmasq:
  - `uci set dhcp.@dnsmasq[0].rebind_protection='1'`
  - `uci set dhcp.@dnsmasq[0].rebind_localhost='1'`
- [ ] Verify DHCP clients receive OpenWrt as DNS server (no change needed —
      already the default)
- [ ] Register `openwrt_dns` play in deploy_stamp

**Verify:**

- [ ] `https-dns-proxy` service running: `/etc/init.d/https-dns-proxy status`
- [ ] DNS resolution works: `nslookup example.com` from OpenWrt returns result
- [ ] dnsmasq forwards to https-dns-proxy: check `noresolv` and server config
- [ ] DNS rebinding protection active: `uci get dhcp.@dnsmasq[0].rebind_protection` = 1
- [ ] External DNS resolution from a LAN client perspective (query via
      OpenWrt LAN IP)
- [ ] deploy_stamp contains `openwrt_dns` play entry

**Rollback (`--tags openwrt-dns-rollback`):**

- Remove `https-dns-proxy` package
- Revert dnsmasq to ISP defaults: `uci delete dhcp.@dnsmasq[0].noresolv`,
  remove server entries
- Remove rebinding protection settings
- `uci commit && /etc/init.d/dnsmasq restart`

---

### Milestone 4: Multi-Node Mesh Enhancements

_Self-contained. Requires WiFi hardware for full testing; gracefully skipped
when no radios are present._

Enhance 802.11s mesh networking with client steering (Dawn) and peer
monitoring. Centralizes mesh parameters so all nodes share the same config.

See: `openwrt-build` skill (mesh config, WiFi patterns).

**Implementation pattern:** Create `roles/openwrt_configure/tasks/mesh.yml`.
Add two new plays to `site.yml` (tagged `[openwrt-mesh]`): one targeting
`openwrt` for configuration, one targeting `router_nodes` for deploy_stamp.
All tasks are conditional on WiFi hardware being present (`wifi_pci_devices`
is non-empty). The molecule test machine may lack WiFi — the scenario
verifies graceful skip behavior rather than full Dawn functionality.

- [ ] Create `roles/openwrt_configure/tasks/mesh.yml` task file
- [ ] Add mesh play + paired deploy_stamp play to `site.yml`
      (tagged `[openwrt-mesh]`, after the DNS play)
- [ ] Create `molecule/openwrt-mesh/` scenario (follows template from M0)
- [ ] Install Dawn on each OpenWrt node (with retries + delay per
      openwrt-build skill rule 4):
  - `opkg update && opkg install dawn`
- [ ] Configure Dawn via UCI:
  - RSSI thresholds, steering behavior, band steering preferences
  - Parameters defined in `group_vars/all.yml` for multi-node consistency
- [ ] Add mesh peer monitoring:
  - Periodic mesh peer count check via UCI/cron
  - Log peer drop/join events via syslog
- [ ] Centralize mesh parameters in `group_vars/all.yml`:
  - `openwrt_mesh_id`, `openwrt_mesh_key` (already exist)
  - Add `openwrt_dawn_rssi_threshold`, `openwrt_dawn_steering_mode`
- [ ] Test multi-node convergence after node reboot (manual test with
      multiple nodes; automated test covers single-node Dawn config)
- [ ] Register `openwrt_mesh` play in deploy_stamp

**Verify:**

- [ ] Dawn service running (when WiFi hardware present):
      `/etc/init.d/dawn status`
- [ ] Dawn UCI config matches `group_vars` parameters
- [ ] Mesh peer count matches expected node count (single node in test)
- [ ] Dawn gracefully skipped when no WiFi radios detected
- [ ] deploy_stamp contains `openwrt_mesh` play entry

**Rollback (`--tags openwrt-mesh-rollback`):**

- Remove Dawn package (`opkg remove dawn`)
- Remove Dawn UCI config
- Remove mesh monitoring cron jobs
- `uci commit`

---

### Milestone 5: Pi-hole DNS Forwarding

_Blocked on: Pi-hole LXC project. Cannot test the full DNS forwarding chain
without a running Pi-hole instance._

Configure dnsmasq to forward DNS queries to Pi-hole for ad blocking and
filtering. Layers on top of the encrypted DNS from M3 — Pi-hole becomes the
primary resolver, with `https-dns-proxy` as the fallback.

**Implementation pattern:** Create a separate
`roles/openwrt_configure/tasks/pihole_dns.yml` task file (not merged into
`dns.yml` — avoids re-running M3 tasks when only Pi-hole forwarding
changes). Add a paired deploy_stamp play in `site.yml` tagged
`[openwrt-pihole-dns]`. All tasks guarded by
`pihole_static_ip is defined`.

- [ ] Configure dnsmasq to forward to Pi-hole static IP (`pihole_static_ip`
      from `group_vars/all.yml`)
- [ ] Keep `https-dns-proxy` as fallback when Pi-hole is unreachable:
  - dnsmasq server list: Pi-hole first, then `127.0.0.1#5053`
- [ ] Test full chain: client → OpenWrt dnsmasq → Pi-hole → DoH upstream
- [ ] Ensure graceful degradation: if Pi-hole is stopped, dnsmasq falls
      through to https-dns-proxy
- [ ] Register `openwrt_pihole_dns` play in deploy_stamp

**Verify:**

- [ ] dnsmasq forwards to Pi-hole IP (check server config)
- [ ] DNS resolution succeeds through Pi-hole (query returns filtered result)
- [ ] Fallback works: stop Pi-hole, DNS still resolves via https-dns-proxy
- [ ] deploy_stamp contains `openwrt_pihole_dns` play entry

**Rollback (`--tags openwrt-pihole-dns-rollback`):**

- Revert dnsmasq server list to forward only to https-dns-proxy
- `uci commit && /etc/init.d/dnsmasq restart`

---

### Milestone 6: Syslog Forwarding

_Blocked on: rsyslog LXC project (shared infrastructure). Cannot verify
log delivery without a running rsyslog collector._

Forward OpenWrt system logs to a central rsyslog collector for aggregation,
search, and alerting.

**Implementation pattern:** Create
`roles/openwrt_configure/tasks/syslog.yml`. Add two new plays to `site.yml`
tagged `[openwrt-syslog]`.

- [ ] Create `roles/openwrt_configure/tasks/syslog.yml` task file
- [ ] Configure syslog destination via UCI:
  - `uci set system.@system[0].log_ip='<rsyslog_ip>'`
  - `uci set system.@system[0].log_port='514'`
  - `uci set system.@system[0].log_proto='tcp'`
- [ ] Set appropriate log level:
  - `uci set system.@system[0].conloglevel='7'` (debug to console)
  - `uci set system.@system[0].cronloglevel='8'` (suppress cron noise)
- [ ] Graceful fallback: local logging continues regardless of remote
      availability (OpenWrt default behavior — `logd` always buffers locally)
- [ ] Register `openwrt_syslog` play in deploy_stamp

**Verify:**

- [ ] UCI `log_ip` and `log_port` set correctly
- [ ] Syslog messages arrive at rsyslog collector (when available)
- [ ] Local `logread` still shows recent entries (fallback works)
- [ ] deploy_stamp contains `openwrt_syslog` play entry

**Rollback (`--tags openwrt-syslog-rollback`):**

- Remove `log_ip`, `log_port`, `log_proto` from UCI system section
- `uci commit && /etc/init.d/log restart`

---

### Milestone 7: Monitoring & Metrics Export

_Blocked on: Netdata/monitoring infrastructure (shared infrastructure).
Cannot verify scrape integration without a running monitoring stack._

Export system and network metrics from OpenWrt for centralized monitoring.

**Implementation pattern:** Create
`roles/openwrt_configure/tasks/monitoring.yml`. Add two new plays to
`site.yml` tagged `[openwrt-monitoring]`.

- [ ] Create `roles/openwrt_configure/tasks/monitoring.yml` task file
- [ ] Install `prometheus-node-exporter-lua` and relevant collectors:
  - `opkg install prometheus-node-exporter-lua`
  - `opkg install prometheus-node-exporter-lua-nat_traffic`
  - `opkg install prometheus-node-exporter-lua-netstat`
  - `opkg install prometheus-node-exporter-lua-wifi`
  - `opkg install prometheus-node-exporter-lua-wifi_stations`
- [ ] Configure exporter to listen on LAN interface only:
  - Bind to LAN IP, not `0.0.0.0`
  - Verify exporter is NOT accessible from WAN
- [ ] Exported metrics: CPU, memory, bandwidth per interface, WiFi client
      count, DHCP lease count, firewall hit counters, NAT connection tracking
- [ ] Document the scrape endpoint (IP:port, path) in
      `docs/architecture/openwrt-build.md` for monitoring stack integration
- [ ] Register `openwrt_monitoring` play in deploy_stamp

**Verify:**

- [ ] Prometheus exporter service running
- [ ] Metrics endpoint responds on LAN IP:9100 (default port)
- [ ] Expected metric families present: `node_cpu`, `node_memory`,
      `node_network`, `wifi_stations`
- [ ] Exporter NOT listening on WAN interface (port scan from WAN bridge)
- [ ] deploy_stamp contains `openwrt_monitoring` play entry

**Rollback (`--tags openwrt-monitoring-rollback`):**

- Remove all `prometheus-node-exporter-lua*` packages
- `uci commit`

---

### Milestone 8: Documentation & Final Integration

_Self-contained. Run after all implemented milestones._

Consolidate documentation, verify full integration, and cut a release.

- [ ] Update `docs/architecture/openwrt-build.md` with all implemented
      features (security, VLANs, DNS, mesh, monitoring endpoints)
- [ ] Update `docs/architecture/overview.md` target site.yml diagram to
      include the new feature plays (openwrt-security, openwrt-vlans,
      openwrt-dns, openwrt-mesh) and their deploy_stamp pairs
- [ ] Update `docs/architecture/roles.md` to document new task files in
      `openwrt_configure` (security.yml, vlans.yml, dns.yml, mesh.yml)
      and their key variables
- [ ] Update `openwrt-build` skill (`.cursor/skills/openwrt-build/SKILL.md`)
      with lessons learned during implementation
- [ ] Update `rollback-patterns` skill with concrete examples from
      implemented per-feature rollback tags
- [ ] Ensure `molecule/default/verify.yml` includes ALL feature assertions
      (merge per-feature verify sections into the main verify)
- [ ] Run full `molecule test` with all self-contained features enabled —
      must pass end-to-end from clean state
- [ ] Add CHANGELOG entry summarizing all milestones
- [ ] Bump `project_version` in `group_vars/all.yml`

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] All per-feature molecule scenarios pass independently
- [ ] Per-feature rollback round-trip: apply feature, verify, rollback,
      verify baseline restored (for each self-contained milestone)
- [ ] `docs/architecture/openwrt-build.md` matches implemented features
- [ ] `docs/architecture/overview.md` target site.yml includes all new plays

**Rollback:** N/A — documentation-only milestone.

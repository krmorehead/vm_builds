# Multi-Node Test Infrastructure

## Overview

Add a second Proxmox test node behind the OpenWrt router's LAN subnet,
establish reusable SSH proxy patterns for reaching it from the controller,
and validate that our playbooks work across different hardware. This is the
foundation for multi-node testing (WiFi mesh, wired backhaul) and the first
time we manage a host that is only reachable via ProxyJump through another.

## Type

Cross-cutting infrastructure

## Prerequisites

- OpenWrt router baseline operational on `home` (192.168.86.201) —
  the 10.10.10.0/24 LAN only exists when the router VM is running
- New Proxmox node physically connected to the LAN switch behind OpenWrt
- SSH key from the controller already authorized on `home`

## Skills

| Skill | When to use |
|-------|-------------|
| `molecule-testing` | Molecule scenarios, multi-platform config, verify assertions |
| `vm-lifecycle-architecture` | Two-role pattern, flavor groups, inventory structure |
| `proxmox-network-safety` | Safe host commands, bridge teardown, kernel module handling |
| `openwrt-network-topology` | DHCP static leases, firewall rules, UCI patterns |
| `project-planning-structure` | Milestone structure and conventions |

---

## Architectural Decisions

```
Decisions
├── SSH access: ProxyJump through primary Proxmox host
│   ├── Controller (supernet 192.168.86.x) cannot route to 10.10.10.0/24 directly
│   ├── Primary host (.201) has bridges on both subnets (WAN + LAN)
│   ├── SSH: controller → .201 (direct) → .210 (via LAN bridge at 10.10.10.2)
│   ├── Pattern: ansible_ssh_common_args with -o ProxyJump=root@PROXMOX_HOST
│   └── Same pattern already used for OpenWrt VM access — generalize it
│
├── Inventory model: lan_hosts group for ProxyJump automation
│   ├── Hosts behind the router go into a lan_hosts child group under proxmox
│   ├── group_vars/lan_hosts.yml sets ProxyJump through PROXMOX_HOST
│   ├── Adding more LAN nodes = adding to lan_hosts group (no code changes)
│   └── Primary host (home) stays outside lan_hosts (directly reachable)
│
├── Host naming: mesh1 (target role is WiFi mesh satellite)
│   ├── End goal: wifi_nodes flavor group with optional wired backhaul
│   ├── May also join monitoring_nodes for observability
│   └── Does NOT join router_nodes — mesh nodes connect to existing OpenWrt mesh
│
├── API token: scalable per-host env var convention
│   ├── <HOSTNAME>_API_TOKEN (e.g., HOME_API_TOKEN, MESH1_API_TOKEN)
│   ├── group_vars/proxmox.yml resolves dynamically via inventory_hostname
│   ├── No per-host override needed — adding a host = adding an env var
│   └── PRIMARY_HOST replaces PROXMOX_HOST as the build.py entry point
│
├── DHCP reservation: stable IP for the new node
│   ├── OpenWrt DHCP static lease: MAC → 10.10.10.210
│   ├── Added via UCI during initial setup
│   └── Without reservation, DHCP may assign a different IP after router reboot
│
├── Supernet accessibility: ProxyJump now, routing later
│   ├── Immediate: SSH ProxyJump and SSH tunnels for browser access
│   ├── Future: OpenWrt firewall rule allowing supernet → LAN forwarding
│   │   (requires source-restricted rule: only 192.168.86.0/24 → LAN)
│   ├── Future: static route on upstream router for 10.10.10.0/24
│   └── Security: supernet routing is opt-in, not default
│
├── Dependency chain: .210 only reachable after OpenWrt on .201 runs
│   ├── Confirmed: .201 with no VMs has no 10.10.10.x IPs
│   ├── Full molecule test on .201 creates the LAN first, then .210 is reachable
│   ├── Plays targeting mesh1 MUST come after OpenWrt configure in site.yml
│   └── If .210 is unreachable, plays should fail with clear error, not hang
│
├── Molecule strategy: separate scenario for mesh1 (NOT in default test)
│   ├── Problem: default molecule test runs site.yml top-to-bottom; backup and
│   │   infra plays target proxmox group BEFORE OpenWrt runs — mesh1 is
│   │   unreachable at that point since the LAN doesn't exist yet
│   ├── Solution: mesh1 gets its own molecule scenario (molecule/mesh1-infra/)
│   │   that assumes the OpenWrt baseline already exists
│   ├── Same layered-scenario pattern as openwrt-security, openwrt-vlans, etc.
│   ├── Future: when site.yml gains a "LAN satellite" phase after OpenWrt,
│   │   mesh1 can optionally join the default test
│   └── The default molecule test continues to test home only
│
├── build.py: primary host probe only, Ansible handles the rest
│   ├── build.py probes PROXMOX_HOST (.201) — confirms the gateway is up
│   ├── Secondary hosts reached via ProxyJump (Ansible-native, no build.py change)
│   ├── If primary is down, nothing behind it works anyway
│   └── Future: optional secondary host probe via SSH ProxyJump
│
└── Hardware requirements and runtime detection
    ├── iGPU: REQUIRED on every host. proxmox_igpu hard-fails if not found.
    ├── WiFi: REQUIRED on every host. proxmox_pci_passthrough detects cards.
    ├── IOMMU/VT-d: REQUIRED in BIOS for WiFi passthrough. Role hard-fails
    │   if IOMMU is not active after reboot or groups are invalid.
    ├── Different NIC count/model → proxmox_bridges handles dynamically
    ├── Single NIC: proxmox_bridges 2-bridge minimum relaxed for non-router
    │   hosts (only enforced when host is in router_nodes group) ✓
    └── Playbooks MUST NOT break on hardware they don't expect, but
        MUST fail loudly if required hardware (iGPU, WiFi, VT-d) is missing
```

---

## Milestone Dependency Graph

```
M1: ProxyJump & Discovery ── requires OpenWrt baseline on home
 └── M2: Inventory Integration ── depends on M1
      └── M3: Cross-Hardware Validation ── depends on M2
           └── M4: Skill & Documentation ── depends on M1–M3
```

---

## Milestones

### Milestone 1: ProxyJump Pattern & Connectivity

_Requires OpenWrt baseline operational on `home`. Not self-contained._

Establish the SSH ProxyJump pattern for reaching hosts behind the
OpenWrt router. Discover the new node on the LAN, set up SSH key
auth, assign a stable IP, and verify end-to-end connectivity.

See: `proxmox-ssh-safety` skill (SSH keepalives, safe commands),
`openwrt-network-topology` skill (DHCP static leases via UCI).

**Implementation pattern:**
- Inventory: create `lan_hosts` child group under `proxmox`
- Group vars: `inventory/group_vars/lan_hosts.yml` with ProxyJump config
- Host vars: `inventory/host_vars/mesh1.yml` with target IP

**Bootstrap OpenWrt (if not already running):**

- [x] Ensure OpenWrt router is running on `home` (run
  `molecule converge` or `build.py --tags infra,openwrt` if needed)
- [x] Verify the Proxmox host has LAN bridge IP (10.10.10.2)

**Discover the new node:**

- [x] Discover mesh1's IP on the LAN (10.10.10.210)
- [x] Record MAC address

**Set up SSH key auth on mesh1:**

- [x] Copy controller's and primary host's SSH keys to mesh1
  (done via Proxmox GUI shell and `ssh-copy-id` through tunnel)
- [x] Verify key-based SSH works (no password prompt)

**Assign stable IP (10.10.10.210):**

- [x] DHCP static lease created on OpenWrt via `tasks/bootstrap_lan_host.yml`
- [x] Verify mesh1 is at 10.10.10.210

**Create inventory configuration:**

- [x] Create `inventory/group_vars/lan_hosts.yml` (uses `PRIMARY_HOST`)
- [x] Add `lan_hosts` child group in `inventory/hosts.yml`
- [x] Create `inventory/host_vars/mesh1.yml`
- [x] Verify Ansible connectivity in molecule converge

**Document access patterns:**

- [x] SSH tunnel documented in `lan-ssh-patterns` skill

**Verify:**

- [x] ProxyJump SSH from controller to mesh1 succeeds
- [x] Ansible ping succeeds via molecule converge
- [x] DHCP lease confirmed on OpenWrt
- [x] Key auth only (no passwords)

**Rollback:**

Inventory changes reverted via git. DHCP lease removed:
`uci delete dhcp.@host[-1] && uci commit dhcp && /etc/init.d/dnsmasq restart`

---

### Milestone 2: Inventory & Environment Integration

_Depends on M1 (connectivity verified)._

Configure the new node's Proxmox API token, add per-host environment
variables, and verify Ansible can fully manage it. Do NOT add mesh1 to
the default molecule test yet — that requires solving the ordering
constraint (mesh1 unreachable during early plays).

See: `vm-lifecycle-architecture` skill (flavor groups, inventory structure).

- [x] Create Proxmox API token on mesh1 (via `tasks/bootstrap_lan_host.yml`
  or manually via `pveum user token add root@pam ansible --privsep=0`)
- [x] Add `MESH1_API_TOKEN` to `test.env`
- [x] API token resolved dynamically via `group_vars/proxmox.yml`
  convention: `(inventory_hostname | upper) + '_API_TOKEN'`
  (no per-host override needed — simpler than original plan)
- [x] `mesh1` in `lan_hosts` group (not `router_nodes`)
- [x] Verify inventory graph: mesh1 under `proxmox` via `lan_hosts`
- [x] Verify facts gathered on mesh1 via molecule converge
- [x] Hardware profile: 1 NIC (nic0), 1 WiFi (wlp2s0), iGPU present,
  IOMMU groups not properly assigned (passthrough skipped gracefully)
- [x] Proxmox API verified in verify.yml (`pvesh get /version`)

**Verify:**

- [x] Ansible facts gathered without error
- [x] mesh1 in `proxmox` via `lan_hosts`, NOT in `router_nodes`
- [x] Proxmox API responds
- [x] Hardware profile documented above

**Rollback:**

Inventory changes reverted via git. API token removal:
`pveum user token remove root@pam ansible` (via ProxyJump SSH).

---

### Milestone 3: Cross-Hardware Validation

_Depends on M2 (node integrated into inventory)._

Run the shared infrastructure roles against the new hardware to validate
hardware-agnostic detection. Create a dedicated molecule scenario for
mesh1 (NOT part of the default test — see architectural decisions for why).

See: `proxmox-network-safety` skill (bridge teardown, PCI handling),
`molecule-testing` skill (per-feature scenario setup).

**Cross-hardware validation (via molecule):**

- [x] `proxmox_bridges` discovers 1 bridge (vmbr0) on mesh1's single NIC.
  2-bridge minimum relaxed for non-router hosts.
- [x] `proxmox_pci_passthrough` detects WiFi (wlp2s0, 02:00.0), enables
  IOMMU via GRUB, reboots. IOMMU groups not properly assigned on this
  hardware — passthrough gracefully skipped with warning.
- [x] `proxmox_igpu` detects Intel iGPU, installs vainfo, exports facts.
- [x] Three role fixes applied:
  - `proxmox_bridges`: 2-bridge check gated on `router_nodes` group
  - `proxmox_pci_passthrough`: IOMMU re-check after reboot + numeric
    group validation + graceful skip when invalid
  - verify.yml: checks system state directly (not cached Ansible facts)
- [x] Cleanup restores mesh1 to clean state (cleanup.yml)

**Molecule scenario:**

- [x] `molecule/mesh1-infra/` created:
  - `molecule.yml`: platforms home + mesh1, env `MESH1_API_TOKEN`
  - `converge.yml`: baseline check → bootstrap → ping → infra roles
  - `verify.yml`: bridge count, deploy_stamp, IOMMU status, PVE API
  - `cleanup.yml`: removes ansible-managed files only (NEVER credentials)
  - Test sequence: `dependency → syntax → converge → verify → cleanup`
- [x] `molecule test -s mesh1-infra` passes end-to-end
- [x] Default scenario (`molecule test`) also passes (no regression)

**Verify:**

- [x] Bridges discovered on mesh1 (1 bridge, single NIC)
- [x] PCI passthrough completes without error (skipped gracefully)
- [x] iGPU detection completes without error
- [x] deploy_stamp written to mesh1
- [x] `molecule test -s mesh1-infra` passes
- [x] Cleanup restores mesh1 to clean state

**Rollback:**

Manual cleanup on mesh1 (targeted file removal as listed above).

---

### Milestone 4: Skill & Documentation

_Self-contained. Run after all implemented milestones._

Create the `lan-ssh-patterns` skill documenting the ProxyJump patterns,
and update architecture docs with the multi-node topology.

- [x] Create `.agents/skills/lan-ssh-patterns/SKILL.md`:
  - ProxyJump pattern, `lan_hosts` group, SSH tunnel, API token convention,
    full "add new LAN node" checklist, dependency chain, troubleshooting
    (unreachable, permission denied, DHCP drift, API tokens), BAD/GOOD
    cleanup patterns. Follows writing-skills conventions (frontmatter,
    Rules, Patterns sections).
- [x] Update `docs/architecture/overview.md`:
  - Network topology shows mesh1 at 10.10.10.210
  - `lan_hosts` group and ProxyJump pattern documented
  - Build profiles reference `lan-ssh-patterns` skill
- [x] Update `docs/architecture/roadmap.md`:
  - Added `2026-03-11-00` as active project (✓ complete)
- [x] Update `.agents/skills/project-structure-rules/SKILL.md`:
  - `lan_hosts` in flavor groups, `PRIMARY_HOST`/`<HOSTNAME>_API_TOKEN`
    in variable scoping, `bootstrap_lan_host.yml` and `mesh1-infra/`
    in key files
- [x] CHANGELOG entry under `[Unreleased]`
- [x] Safety rules updated: cleanup NEVER removes credentials
  (`.agents/skills/proxmox-safety-rules/SKILL.md`, `.agents/skills/learn-from-mistakes/SKILL.md`,
  `molecule-testing`, `proxmox-safety-rules`, `lan-ssh-patterns` skills)

**Verify:**

- [x] Skill file follows writing-skills conventions
- [x] Architecture docs match implemented topology
- [x] `ansible-lint && yamllint .` passes

**Rollback:** N/A — documentation-only milestone.

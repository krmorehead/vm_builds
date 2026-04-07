# Dedicated WiFi Bridge

## Overview

Two Proxmox units form a transparent WiFi bridge to extend the OpenWrt LAN
subnet (10.10.10.x) to a remote location over a dedicated 802.11s
point-to-point link. **bridge-1** is wired into the LAN (via a switch
connected to Home's LAN bridge) and broadcasts via an Intel AX210
(WiFi 6E). **bridge-2** receives the WiFi signal and forwards traffic
through a USB NIC to **mesh2**, which acts as a wired backhaul mesh
point. Same subnet on both sides — no routing, no NAT, no new subnet.
Pure L2 forwarding.

Both bridge units have aftermarket AX210 WiFi 6E cards with no prior
driver or firmware verification. This plan includes host-side validation
as a prerequisite gate before any WiFi bridge service is deployed.

Both bridges run shared infrastructure only (backup, bridges, PCI
passthrough, iGPU) plus the bridge-specific service. They do NOT run
VPN, monitoring, DNS, media, desktop, or gaming services. This avoids
per-host IP index shifts that would break existing container IP
allocations on other nodes.

**Node count: 6** (home, mesh1, ai, mesh2, bridge-1, bridge-2). All six
run shared infrastructure plays in parallel. All six are included in
E2E molecule tests.

## Type

LXC container (OpenWrt bridge, per-node)

## Resources

| Resource | Value | Notes |
|----------|-------|-------|
| VMID | 104 | Network tier (100-199) |
| Cores | 2 | Dedicated to WiFi bridge forwarding |
| RAM | 512 MB | Headroom for bridge table + mesh state |
| Disk | 1 GB | OpenWrt rootfs is ~60MB; 1GB matches mesh LXC pattern |
| Network (bridge-1) | WAN bridge (supernet) | Container eth0 on `proxmox_wan_bridge` |
| Network (bridge-2) | USB NIC bridge (backhaul) | Container eth0 on USB NIC bridge (connected to mesh2) |
| WiFi | AX210 (PHY namespace move) | Host loads firmware, PHY moved to container |
| Image | Existing mesh LXC image | Reuse `openwrt_lxc_template_path` (see justification) |

## Startup

| Setting | Value |
|---------|-------|
| Auto-start | Yes (`onboot: true`) |
| Boot priority | 2 (after network, parallel with VPN) |
| Dependencies | None — both bridge nodes are on the supernet for management |

## Build Profiles

```
Dedicated WiFi Bridge (bridge-1 — 192.168.86.230)
├── proxmox            → shared infra (backup, bridges, PCI passthrough, iGPU)
└── bridge_nodes       → WiFi Bridge LXC (VMID 104)

Dedicated WiFi Bridge (bridge-2 — 192.168.86.231)
├── proxmox            → shared infra (backup, bridges, PCI passthrough, iGPU)
└── bridge_nodes       → WiFi Bridge LXC (VMID 104)
```

**Explicitly excluded** (both bridges are purpose-built units):
- `router_nodes` — no OpenWrt router VM
- `vpn_nodes` — no WireGuard (avoids IP index shift; see IP Allocation)
- `monitoring_nodes` — no Netdata/rsyslog (avoids IP index shift; see IP Allocation)
- `dns_nodes`, `service_nodes`, `media_nodes`, `streaming_nodes`,
  `desktop_nodes`, `gaming_nodes`, `wifi_nodes` — none applicable

VPN and monitoring are documented as optional future additions in the
"Future Integration Considerations" section. Adding either bridge to
these groups shifts per-host IP indices for all existing nodes.

## Prerequisites

| Prerequisite | Status | Notes |
|-------------|--------|-------|
| bridge-1 Proxmox host on supernet with SSH key auth | Done | `ssh root@192.168.86.230 hostname` |
| bridge-2 Proxmox host on supernet with SSH key auth | Done | `ssh root@192.168.86.231 hostname` |
| `BRIDGE_1_API_TOKEN` in `test.env` | Done | `799f56bf-...` |
| `BRIDGE_2_API_TOKEN` in `test.env` | Done | `7a1d50ee-...` |
| `BRIDGE_1_HOST=192.168.86.230` in `test.env` | Done | Line 4 |
| `BRIDGE_2_HOST=192.168.86.231` in `test.env` | Done | Line 5 |
| `firmware-iwlwifi` Debian package on BOTH hosts | Must verify | AX210 requires firmware files in `/lib/firmware/` |
| AX210 installed and detected by BIOS on BOTH hosts | Must verify | `lspci \| grep -i network` |
| USB NIC cable between bridge-2 and mesh2 | Done | Direct wired backhaul link |
| **iGPU present on both bridges** | **Must confirm** | `proxmox_igpu` hard-fails if absent (see below) |

### iGPU requirement

`proxmox_igpu` runs on ALL `proxmox` hosts and hard-fails if no iGPU
is found. Neither bridge consumes iGPU services (no media, desktop, or
gaming), but the infrastructure play will fail if no iGPU exists.

**Resolution options (pick one during M0):**
1. **Confirm both bridges have an iGPU** (most Intel/AMD desktop CPUs do).
   No code change needed. This is the expected case.
2. **If either bridge lacks an iGPU**: gate `proxmox_igpu` to skip on hosts
   not in any iGPU-consuming group (`media_nodes`, `desktop_nodes`,
   `gaming_nodes`). This is a separate infrastructure change.

## Skills

| Skill | When to use |
|-------|-------------|
| `openwrt-mesh-lxc-wifi` | WiFi PHY namespace move, hookscript, PHY detection |
| `lxc-container-patterns` | LXC provisioning via `proxmox_lxc` helper |
| `openwrt-build` | OpenWrt UCI configuration, wireless setup |
| `openwrt-busybox-constraints` | BusyBox ash limitations in pct_remote commands |
| `vm-lifecycle` | Two-role service model, `site.yml` integration |
| `molecule-testing` | Per-feature scenario, E2E test expansion |
| `molecule-cleanup` | VMID cleanup lists, credential safety |
| `proxmox-safety-rules` | Host safety, driver operations |

---

## Architectural Decisions

```
Decisions
├── WiFi link type: 802.11s mesh (NOT WDS)
│   ├── Standards-based, proven in this project (mesh nodes use it)
│   ├── Works with WiFi 6/6E hardware and modern mac80211 stack
│   ├── mesh_fwding=1 enables transparent L2 forwarding natively
│   ├── With exactly 2 peers, overhead is negligible vs point-to-point
│   ├── Rejected: WDS — legacy, poorly supported on WiFi 6E
│   └── Rejected: relayd — adds user-space relay overhead
│
├── Bridging: transparent L2 bridge (br-lan = eth0 + mesh0)
│   ├── OpenWrt bridges wired eth0 and wireless mesh0 into br-lan
│   ├── Container management IP on br-lan (bridged subnet, static or DHCP)
│   ├── All MAC addresses from bridge-2 side pass through transparently
│   ├── Linux bridge auto-sets promiscuous mode on ports — no manual config
│   ├── Proxmox bridge (veth to container) learns MACs dynamically
│   ├── No routing, no NAT, no iptables — pure L2 switching
│   └── STP enabled on both containers' br-lan as safety measure
│       (prevents broadcast storms if operator accidentally creates
│       a wired L2 path between both switches)
│
├── Image: reuse existing mesh LXC image (openwrt-mesh-lxc)
│   ├── Bridge and mesh containers need identical packages:
│   │   wpad-mesh-openssl (802.11s), iw (netlink config), kmod-iwlwifi
│   ├── Both strip packages not needed in LXC: firewall, dnsmasq, PPP
│   ├── AX210 firmware NOT needed in container image — HOST loads firmware
│   │   via PHY namespace move (host kernel module + firmware-iwlwifi)
│   ├── Container only uses mac80211 user-space tools via netlink
│   ├── Reuse openwrt_lxc_template / openwrt_lxc_template_path variables
│   │   (NOT custom bridge_lxc_template — same binary image)
│   ├── Image verification gate: bridge role calls verify_lxc_template.yml
│   │   which checks openwrt_lxc_template_path exists
│   ├── Trigger for separate image: if bridge needs packages NOT in mesh
│   │   image (e.g., iperf3 for throughput testing, dawn for steering).
│   │   Until then, sharing avoids maintaining a second OpenWrt build target
│   └── Previous success: mesh image already works on 3 different WiFi
│       chipsets (Intel 8265, Atheros, MediaTek) — chipset-agnostic
│
├── Mesh isolation: separate mesh_id from general mesh
│   ├── Bridge mesh_id = "bridge-dedicated" (vs general "vm-builds-mesh")
│   ├── Prevents bridge nodes from peering with mesh1/mesh2 mesh containers
│   ├── Reuses MESH_KEY for encryption (mesh_id provides L2 isolation)
│   └── If stronger isolation needed later: add BRIDGE_KEY env var
│
├── Performance tuning (WiFi 6E with AX210)
│   ├── Band selection: intelligent, prefer 6 GHz
│   │   ├── Scan `iw phy` for supported bands at configure time
│   │   ├── 6 GHz preferred: least interference, mandatory WPA3, highest
│   │   │   throughput — ideal for a dedicated point-to-point link
│   │   ├── 5 GHz acceptable: still strong for dedicated link if 6 GHz
│   │   │   unavailable (regulatory, DFS restrictions, environment)
│   │   ├── Role logs selected band so operator knows what's active
│   │   ├── AX210 supports 6 GHz — expect 6g in normal operation
│   │   └── Hard-fail only if NO usable band (2.4-only or zero radios)
│   ├── 160 MHz channel width (HE160 for maximum throughput)
│   ├── WPA3-SAE encryption (required for 6 GHz, good practice on 5 GHz)
│   ├── Disable power saving (consistent throughput over battery life)
│   ├── Dedicated link (no other clients) eliminates contention
│   └── Theoretical: ~2.4 Gbps (2x2 MIMO, 160 MHz, WiFi 6E on 6 GHz)
│       Practical: 800–1500 Mbps depending on band, distance, obstacles
│
├── Inventory: new bridge_nodes group (NOT wifi_nodes)
│   ├── wifi_nodes triggers openwrt_mesh_lxc (VMID 103, mesh purpose)
│   ├── bridge_nodes triggers openwrt_bridge_lxc (VMID 104, bridge purpose)
│   ├── Different service, different VMID, different configure role
│   ├── Both use PHY namespace move but with different UCI config
│   └── Neither bridge in vpn_nodes or monitoring_nodes (see IP Allocation)
│
├── Group membership: proxmox + bridge_nodes only
│   ├── Adding either bridge to indexed groups (vpn_nodes, monitoring_nodes)
│   │   shifts per-host indices for ALL existing nodes in those groups
│   ├── Index shift changes container IPs — breaks incremental deploys
│   │   and can cause IP collisions with other services
│   ├── Example: adding to monitoring_nodes (6 hosts) →
│   │   rsyslog on home shifts from offset 12+0=.12 to 12+2=.14
│   │   → COLLIDES with Home Assistant at offset 14 on home
│   ├── Clean-state molecule test works (everything recreated)
│   │   but production incremental deploy leaves stale IPs
│   └── Decision: keep bridges in minimal groups, document VPN/monitoring
│       as optional additions with explicit IP recalculation
│
├── WiFi stack hardening in proxmox_pci_passthrough
│   ├── Current gap: module loading only happens during stale vfio cleanup.
│   │   Fresh hosts (no prior vfio) get no module loading → WiFi interface
│   │   scan at line 4 finds nothing → "no WiFi detected" on bridge nodes.
│   ├── Fix: add unconditional WiFi module loading BEFORE the interface scan.
│   │   Move the module load block (iwlwifi, ath9k, etc.) to run on ALL hosts,
│   │   not just during stale vfio cleanup.
│   ├── Current gap: no firmware verification. modprobe iwlwifi succeeds
│   │   without firmware, but creates no PHY. Role reports "no WiFi" instead
│   │   of "firmware missing" — misleading.
│   ├── Fix: after module loading, if lspci shows a WiFi controller but
│   │   no interface appears, check firmware and hard-fail with specific
│   │   diagnostic (e.g., "AX210 detected but no PHY — install firmware-iwlwifi")
│   ├── Install firmware-iwlwifi via apt on Intel WiFi hosts — same pattern
│   │   as proxmox_igpu installing vainfo + intel-media-va-driver.
│   │   This is host infrastructure, not container runtime packaging.
│   ├── Current gap: no PHY verification. wifi_pci_devices exports PCI
│   │   addresses but never confirms PHYs exist in /sys/class/ieee80211/.
│   ├── Fix: export wifi_phy_count alongside wifi_pci_devices so downstream
│   │   roles (mesh_lxc, bridge_lxc) can rely on infrastructure having
│   │   verified the full WiFi stack, not just PCI detection.
│   └── Precedent: proxmox_igpu installs packages, loads drivers, verifies
│       DRI devices, exports rich facts. Same pattern for WiFi.
│
├── LXC container properties
│   ├── Privileged container (unprivileged: false) — required for
│   │   WiFi PHY namespace move (CAP_NET_ADMIN)
│   ├── ostype: unmanaged — Proxmox cannot auto-detect OpenWrt
│   ├── features: nesting=1 — required for network namespace operations
│   │   inside the container
│   ├── skip_debian_cleanup: true — OpenWrt is not Debian
│   └── No systemd sandboxing concern — OpenWrt uses procd, not systemd
│
├── Bridge-2 multi-NIC topology
│   ├── Bridge-2 has 2+ NICs: main NIC (supernet management) + USB NIC
│   │   (backhaul cable to mesh2)
│   ├── Container eth0 connects to USB NIC bridge (NOT supernet bridge)
│   ├── br-lan bridges mesh0 (WiFi from bridge-1) + eth0 (USB to mesh2)
│   ├── Container management IP on br-lan is on the bridged subnet
│   │   (supernet in test, LAN in production — transparent)
│   ├── pct_remote accesses container via Proxmox host (.231) — container
│   │   does NOT need its own reachable IP for Ansible management
│   └── Bridge selection: role detects which Proxmox bridge has the USB
│       NIC via sysfs driver/device inspection (not hardcoded)
│
└── Bridge-1 single-NIC topology (simpler)
    ├── Bridge-1 has 1 main NIC (supernet in test, LAN in production)
    ├── Container eth0 connects to proxmox_wan_bridge (supernet)
    ├── br-lan bridges eth0 (wired network) + mesh0 (WiFi to bridge-2)
    ├── Container management IP on br-lan is on the bridged subnet
    └── In production: eth0 would connect to the LAN bridge instead
        (host_vars override for bridge selection)
```

---

## Network Topology

This section documents every physical cable and wireless link in the
test environment, explains how Ansible reaches each node, and maps the
test topology to the production topology. Future-you: read this before
changing any cables or IP assignments.

### Physical cabling (what's plugged in right now)

Six Proxmox hosts. Two physical network segments. One WiFi link.

```
┌──────────────────────────────────────────────────────────────────┐
│                    SUPERNET SWITCH                                │
│              192.168.86.x  (management plane)                    │
│                                                                  │
│   Port 1     Port 2     Port 3     Port 4     Port 5     Port 6  │
└─────┬──────────┬──────────┬──────────┬──────────┬──────────┬─────┘
      │          │          │          │          │          │
    Home        AI       Mesh2    Bridge-1   Bridge-2     ISP
   .201       .220       .211      .230       .231      Router
  (PCIe)     (PCIe)     (PCIe)    (NIC)      (NIC)
      │
      │   ┌──────────────────────────────┐
      └───┤  Home's LAN bridge           │
          │  (vmbr1 or detected bridge)  │
          │  10.10.10.x via OpenWrt VM   │
          └──────────┬───────────────────┘
                     │
                   Mesh1
                   .210
                  (PCIe)


┌──────────────────────────────────────────────────────────────────┐
│                  USB NIC CABLE                                    │
│      Dedicated point-to-point link (isolated segment)            │
│                                                                  │
│   Bridge-2 USB NIC  ←─── cable ───→  Mesh2 USB NIC              │
│   (not on supernet)                  (not on supernet)           │
└──────────────────────────────────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────────┐
│                  WiFi LINK (6 GHz preferred)                     │
│      Dedicated 802.11s AX210-to-AX210                            │
│                                                                  │
│   Bridge-1 AX210    ←─── air ───→    Bridge-2 AX210              │
│   (namespace-moved                   (namespace-moved            │
│    into VMID 104)                     into VMID 104)             │
└──────────────────────────────────────────────────────────────────┘
```

**Key observations:**

- Every host has a management NIC on the supernet switch. Ansible
  reaches all 6 hosts directly over this switch — no ProxyCommand
  needed (except mesh1, which is behind OpenWrt on the LAN).
- The USB NIC cable is an **isolated segment**. It does not connect
  to the supernet switch. Only bridge-2 and mesh2 are on it.
- The WiFi link exists ONLY between bridge-1 and bridge-2 containers.
  It is isolated from the general mesh (different `mesh_id`).
- Mesh1 is the only LAN host. It reaches the supernet via OpenWrt's
  LAN bridge on Home. All other nodes are directly on the supernet.

### How Ansible reaches each node

| Node | IP | Reachable via | Connection type |
|------|----|---------------|-----------------|
| home | 192.168.86.201 | Direct SSH | `ansible_connection: ssh` |
| ai | 192.168.86.220 | Direct SSH | `ansible_connection: ssh` |
| mesh2 | 192.168.86.211 | Direct SSH | `ansible_connection: ssh` |
| bridge-1 | 192.168.86.230 | Direct SSH | `ansible_connection: ssh` |
| bridge-2 | 192.168.86.231 | Direct SSH | `ansible_connection: ssh` |
| mesh1 | 10.10.10.210 | ProxyCommand through home | `ssh -o ProxyCommand="ssh home -W %h:%p"` |
| containers | N/A | `pct exec` via Proxmox host | `community.proxmox.proxmox_pct_remote` |

Containers do NOT need their own reachable IP for Ansible management.
`pct_remote` SSHes to the Proxmox host and runs `pct exec` inside the
container. The container IP is only relevant for data-plane traffic.

### Data plane: how traffic flows through the bridge

This is the full path a packet takes from the supernet through the
bridge chain to mesh2's USB NIC. Every hop is a real, testable link.

```
  1. Supernet device (any host on 192.168.86.x)
  2. → Supernet switch
  3. → Bridge-1 host (.230) — Proxmox bridge (veth to container)
  4. → Bridge-1 container eth0
  5. → br-lan (transparent L2 bridge inside container)
  6. → mesh0 (802.11s WiFi interface)
  7. → [air — dedicated AX210 link, 6 GHz preferred]
  8. → Bridge-2 container mesh0
  9. → br-lan (transparent L2 bridge inside container)
 10. → eth0 (mapped to USB NIC Proxmox bridge)
 11. → Bridge-2 host (.231) — USB NIC Proxmox bridge
 12. → [cable — direct USB NIC link]
 13. → Mesh2 host (.211) — USB NIC
```

**Verification:** Assign a temp IP on mesh2's USB NIC. From bridge-1's
container, ping that IP. If it reaches mesh2, the entire chain works.

### L2 loop analysis

**No loop exists.** The bridge-2 container connects to the USB NIC
bridge, NOT the supernet bridge. There is exactly one L2 path from the
supernet to mesh2's USB NIC: through the bridge chain. The supernet
management NICs on bridge-2 and mesh2 are on a completely separate
Proxmox bridge from the USB NIC segment.

```
Supernet switch ──→ Bridge-1 ──WiFi──→ Bridge-2 ──USB──→ Mesh2
                                                          │
                    (NO path back to supernet switch     │
                     from this USB segment)              │
                                                         ▼
                                              Mesh2 USB NIC
                                              (dead end — not
                                               connected to any
                                               other switch)
```

STP is enabled on both containers' `br-lan` as a safety measure
against accidental loops from future topology changes (e.g., someone
plugs mesh2's USB NIC into the supernet switch).

### Mapping test topology to production

The test topology approximates production with one key difference:
**bridge-1 is on the supernet instead of the LAN**. Here's why,
and how to reason about it.

**Production target:**

```
LAN Switch (10.10.10.x — behind OpenWrt on Home)
  │
  ├── Mesh1 (.210)
  └── Bridge-1 (would get .X on LAN)
          │
      [AX210 WiFi]
          │
      Bridge-2 (remote location, no LAN access)
          │
      [USB NIC cable]
          │
      Mesh2 (wired backhaul mesh point)
```

In production, bridge-1 plugs into the same LAN switch as mesh1.
Its container's `eth0` maps to the LAN bridge, so traffic arriving
via WiFi from bridge-2 enters the LAN subnet directly. Mesh2 (behind
bridge-2) becomes a wired backhaul mesh point on the LAN.

**Why test on the supernet instead:**

1. **Ansible access**: bridge-1 on the LAN would require ProxyCommand
   through home (like mesh1). On the supernet, Ansible reaches it
   directly. Simpler debugging, faster iteration.
2. **Independence from OpenWrt**: if the OpenWrt VM is down, bridges
   are still reachable. The bridge service doesn't depend on OpenWrt.
3. **Same bridge code**: the configure role is subnet-agnostic. UCI
   mesh config doesn't reference IP addresses. Whether `eth0` maps to
   the LAN bridge or the supernet bridge, the L2 forwarding is
   identical.

**What changes for production deployment:**

| Setting | Test (supernet) | Production (LAN) |
|---------|-----------------|-------------------|
| bridge-1 `ansible_host` | 192.168.86.230 | 10.10.10.X (LAN IP) |
| bridge-1 container bridge | `proxmox_wan_bridge` | LAN bridge |
| bridge-1 Ansible access | Direct SSH | ProxyCommand via home |
| bridge-2 container bridge | USB NIC bridge | USB NIC bridge (same) |
| Configure role tasks | Identical | Identical |
| WiFi config (UCI) | Identical | Identical |

The ONLY changes are in `host_vars` (IP, bridge override) and
inventory (add bridge-1 to `lan_hosts` if behind OpenWrt). Zero
role changes. The configure role doesn't know or care which subnet
it's bridging.

**To physically move bridge-1 to LAN for testing:**

1. Unplug bridge-1 from the supernet switch
2. Plug bridge-1 into Home's LAN switch (same one mesh1 uses)
3. Bridge-1 gets a DHCP lease from OpenWrt (or set a static LAN IP)
4. Update `host_vars/bridge-1.yml`: `ansible_host` → LAN IP
5. Add `bridge-1` to `lan_hosts` group for ProxyCommand routing
6. Run `molecule converge` — everything else is automatic

This is a future step documented here so the path is clear.

### Network topology rules for contributors

1. **Management plane = supernet (192.168.86.x)**. Every host has at
   least one NIC here. Ansible always reaches hosts via the supernet
   (or via ProxyCommand through a supernet host for LAN nodes).
2. **Data plane = whatever the container bridges**. The bridge
   containers forward traffic between their `eth0` (wired) and
   `mesh0` (WiFi). The subnet they bridge is determined by which
   Proxmox bridge `eth0` maps to — configurable per host.
3. **USB NIC segment is isolated**. It connects only bridge-2 and
   mesh2. It is NOT on the supernet or LAN.
4. **WiFi segment is isolated**. The bridge `mesh_id`
   (`bridge-dedicated`) is different from the general mesh
   (`vm-builds-mesh`). Bridge containers never peer with mesh
   containers.
5. **Never create a wired path between bridge-1's data segment and
   bridge-2's data segment** outside the WiFi link. That creates an
   L2 loop. STP will handle it, but it adds 30s of convergence delay.

### Container IP Allocation

| Node | Offset | Index | Container IP | Collision check |
|------|--------|-------|--------------|-----------------|
| bridge-1 | 27+200+0 | 0 | .227 (WAN) | Safe |
| bridge-2 | 27+200+1 | 1 | .228 (WAN) | Safe |

**Note:** These IPs are on the bridged subnet. In the test topology,
Bridge-1's container is on the supernet and gets .227. Bridge-2's
container is on the USB NIC bridge — its management IP on br-lan
depends on whether traffic flows through the bridge (once both sides
are configured and mesh established, Bridge-2's br-lan IP is on the
same subnet as Bridge-1's, i.e., the supernet). For pct_remote
management, the container IP is irrelevant — Ansible reaches the
container via the Proxmox host.

**Offset 27 chosen to avoid all collisions:**

Existing WAN host IPs to avoid: home=.201, mesh2=.211, ai=.220,
bridge-1=.230, bridge-2=.231.

Existing WAN container IPs (offset + 200 + index):
- WireGuard: .203–.206 (offset 3, 4 hosts)
- Pi-hole: .210 (offset 10, 1 host)
- rsyslog: .212 (offset 12, 1 host currently)
- Netdata: .221–.224 (offset 21, 4 hosts in molecule)
- Bridge: **.227–.228** (offset 27, 2 hosts)

No collision. Offset 25 would collide with Netdata at .225 if
monitoring_nodes ever grows to 5 hosts (21 + 200 + 4 = .225).

### IP Index Shift Warning

Per-host IP offsets use `groups['flavor_group'].index(inventory_hostname)`.
Adding a new host to an indexed group **shifts indices for all
alphabetically-subsequent hosts**, changing their container IPs.

**Impact of adding bridges to vpn_nodes** (sorted: ai, bridge-1, bridge-2,
home, mesh1, mesh2):
- home WireGuard shifts from 3+1=.4 to 3+3=.6
- mesh1 shifts from 3+2=.5 to 3+4=.7
- mesh2 shifts from 3+3=.206 to 3+5=.208
- No IP collision, but breaks incremental deploys (existing containers
  keep old IPs)

**Impact of adding bridges to monitoring_nodes** (sorted: ai, bridge-1,
bridge-2, home, mesh1, mesh2):
- rsyslog on home shifts from 12+1=.13 to 12+3=**.15 — COLLIDES with
  Jellyfin at offset 15**
- Netdata indices shift similarly

**Decision:** Both bridges stay in `proxmox` + `bridge_nodes` only.

---

## Image Build Justification

The bridge service reuses the existing mesh LXC image
(`openwrt-mesh-lxc-24.10.0-x86-64-rootfs.tar.gz`). No separate
build-images.sh target is needed because:

1. **Identical package set**: Bridge and mesh both need `wpad-mesh-openssl`
   (802.11s), `iw` (netlink config), `kmod-iwlwifi` (Intel WiFi),
   and the same supporting kernel modules.
2. **Identical exclusions**: Both strip `firewall4`, `nftables`,
   `dnsmasq`, `ppp` — not needed in LXC containers.
3. **No AX210-specific packages**: The HOST loads firmware via
   `firmware-iwlwifi` (Debian). The container uses chipset-agnostic
   mac80211 tools. No container-side firmware package needed.
4. **Configuration is the only difference**: Bridge vs mesh is purely a
   UCI config distinction (different `mesh_id`, different bridge
   topology, different performance settings). Per "bake, don't configure":
   UCI config IS host-specific topology — it belongs in the configure role.

**When to create a separate bridge image:**
- If bridge nodes need packages not in the mesh image (e.g., `iperf3`
  for benchmarking, `tcpdump` for debugging, `dawn` for steering)
- If bridge nodes need different kernel modules (unlikely — mac80211 is
  shared)

---

## Testing Strategy

### 6-node molecule parallelism

Both bridges are added to the E2E molecule platforms, bringing the total
to 6 nodes (home, mesh1, ai, mesh2, bridge-1, bridge-2). Shared
infrastructure plays (backup, bridges, PCI, iGPU) run on all 6 nodes
in parallel. The bridge service play runs only on `bridge_nodes`.
Neither bridge is in any other service group, so no existing service
deploys on them (no index shifts).

### End-to-end backhaul testing

The USB NIC cable between bridge-2 and mesh2 enables actual end-to-end
backhaul testing without L2 loops:
1. Bridge-1 container bridges supernet + WiFi
2. WiFi link establishes between bridge-1 and bridge-2 containers
3. Bridge-2 container bridges WiFi + USB NIC to mesh2
4. Traffic from the supernet traverses the full bridge chain to mesh2

Verify tasks can ping mesh2's USB NIC IP from bridge-1's container to
confirm end-to-end connectivity.

### Cross-hardware mesh validation

Bridge-1 and Bridge-2 have AX210 WiFi 6E cards — different chipset from
the existing mesh nodes (Intel 8265 on home/mesh1, other chipsets on
mesh2). To validate the mesh roles are truly hardware-agnostic, a
dedicated scenario runs the **existing mesh LXC roles** (VMID 103) on the
bridge hardware instead of the bridge roles (VMID 104).

This is the key insight: **different scenarios assign different roles to
the same physical hardware**. In the bridge-lxc scenario, bridge-1 and
bridge-2 are `bridge_nodes` running VMID 104. In the mesh-ax210
scenario, they're `wifi_nodes` running VMID 103. Same hardware, different
purpose, validating role portability.

**`molecule/mesh-ax210/` scenario:**
- Platforms: bridge-1 and bridge-2 in `proxmox` + `wifi_nodes`
  (NOT `bridge_nodes` — they're acting as mesh nodes here)
- Converge: shared infrastructure → `openwrt_mesh_lxc` →
  `openwrt_mesh_configure` (the existing mesh plays, not bridge plays)
- Verify: WiFi PHY detected, mesh container running, mesh peers
  established, `iw station dump` shows AX210-to-AX210 mesh peering
- Cleanup: destroy VMID 103 on both bridges

This validates:
1. `openwrt_mesh_lxc` handles AX210 PHY detection and namespace move
2. `openwrt_mesh_configure` generates correct wireless config for AX210
3. Two AX210 devices can form an 802.11s mesh peer relationship
4. The mesh image works on hardware it was never tested on before

Running both `mesh-ax210` and `bridge-lxc` scenarios on the same
hardware (at different times) proves the roles are composable and the
hardware is multi-purpose.

### Per-feature scenario hierarchy

| Scenario | Purpose | Baseline dependency |
|----------|---------|-------------------|
| `molecule/bridge-lxc/` | WiFi bridge provision + configure on both bridges | Standalone |
| `molecule/mesh-ax210/` | Mesh roles on AX210 hardware (bridge HW as mesh nodes) | Standalone |
| `molecule/default/` | Full E2E with all 6 nodes | Includes bridge assertions |

### Day-to-day workflow

```bash
# Source env
set -a && source test.env && set +a

# Per-feature iteration (bridge only)
molecule test -s bridge-lxc

# Full integration (all services, all 6 nodes)
molecule test

# Lint before commit
ansible-lint && yamllint .

# Python tests (after build.py or data.py changes)
pytest tests/ -v
```

### Teardown table

| Action | Creates | Destroys | Baseline impact |
|--------|---------|----------|-----------------|
| bridge-lxc converge | VMID 104 on both bridges | None | None (standalone) |
| bridge-lxc cleanup | None | VMID 104 on both bridges | None |
| mesh-ax210 converge | VMID 103 on both bridges | None | None (standalone) |
| mesh-ax210 cleanup | None | VMID 103 on both bridges | None |
| default converge | VMID 104 + all services | None | Full baseline |
| default cleanup | None | All project VMIDs | Full teardown |

**Note:** `bridge-lxc` and `mesh-ax210` use different VMIDs (104 vs 103)
on the same hardware. NEVER run both concurrently — both require
exclusive access to the WiFi PHY. Run one, clean up, then run the other.

### Configure role task budget

The `openwrt_bridge_configure` role uses `ansible.builtin.raw` via
`pct_remote`. Estimated tasks per container:

1. Detect WiFi radios (iw phy, with retries) — 1 task
2. Generate wireless config (wifi config + fallback) — 3 tasks
3. Enable radios + set band/channel/htmode — 3 tasks
4. Create mesh interface — 1 task
5. Disable power management — 1 task
6. Commit + reload wireless — 2 tasks

**Total: ~11 raw tasks × 2 containers.** Each raw task via pct_remote
takes ~5-15s (faster than full module calls). Total configure time:
~2-6 minutes for both nodes. Acceptable — all tasks are host-specific
topology config (mesh_id, band, channel), not baked-in config.

---

## Milestone Dependency Graph

```
M0 (host onboarding + AX210 verification — both bridges) ← self-contained
├── M1 (bridge LXC provisioning + site.yml + molecule scenario) ← self-contained
│   └── M2 (bridge configuration + performance + backhaul verification) ← self-contained
└── M3 (documentation + WebUI + cleanup lists) ← self-contained
```

All milestones are self-contained with no external project dependencies.
M1→M2 is internal sequencing (configure requires a provisioned container).
M3 can proceed in parallel with M2 after M1 completes.

---

## Milestones

### Milestone 0: Host Onboarding + AX210 Verification

_Self-contained. No external dependencies._

Add both bridge-1 and bridge-2 to the project inventory, configure
access credentials, update build.py for 6-node host awareness, **harden
`proxmox_pci_passthrough` to properly detect, install firmware for, and
verify WiFi hardware**, and validate that shared infrastructure runs
correctly on all 6 nodes — including confirmation that the Intel AX210
WiFi 6E cards produce functional PHY devices on both bridge hosts.

See: `proxmox-safety-rules` skill, `molecule-testing` skill.

**Implementation pattern:**
- Files created: `inventory/host_vars/bridge-1.yml`,
  `inventory/host_vars/bridge-2.yml`
- Files modified: `inventory/hosts.yml`, `test.env` (done),
  `molecule/default/molecule.yml`, `scripts/webui/data.py`,
  `build.py` (OPTIONAL_HOST_VARS, KNOWN_HOSTS),
  `scripts/wol.sh` (if WoL capable), `tests/test_build.py`,
  **`roles/proxmox_pci_passthrough/tasks/main.yml`** (WiFi hardening)
- No site.yml changes (bridge service added in M1)
- No new molecule scenario (uses default scenario)

**Phase A — Inventory and config file edits** (quick, ~30 min):

- [x] Add `BRIDGE_1_HOST=192.168.86.230` to `test.env` (done)
- [x] Add `BRIDGE_2_HOST=192.168.86.231` to `test.env` (done)
- [x] Add `BRIDGE_1_API_TOKEN` to `test.env` (done)
- [x] Add `BRIDGE_2_API_TOKEN` to `test.env` (done)
- [ ] Create `inventory/host_vars/bridge-1.yml`:
  - `ansible_host: "{{ lookup('env', 'BRIDGE_1_HOST') }}"`
  - `pci_passthrough_allow_reboot: true`
  - `wol_capable: <true/false>` (user must confirm)
- [ ] Create `inventory/host_vars/bridge-2.yml`:
  - `ansible_host: "{{ lookup('env', 'BRIDGE_2_HOST') }}"`
  - `pci_passthrough_allow_reboot: true`
  - `wol_capable: <true/false>` (user must confirm)
- [ ] Add both hosts to `inventory/hosts.yml`:
  - Under `proxmox` → new child group `bridge_nodes` with
    `bridge-1: {}` and `bridge-2: {}`
  - NOT in `vpn_nodes`, `monitoring_nodes`, or any other service group
- [ ] Add empty `openwrt_bridge` dynamic group to `inventory/hosts.yml`
- [ ] Add both bridge platforms to `molecule/default/molecule.yml`:
  ```yaml
  - name: bridge-1
    groups:
      - proxmox
      - bridge_nodes
  - name: bridge-2
    groups:
      - proxmox
      - bridge_nodes
  ```
- [ ] Add `BRIDGE_1_API_TOKEN`, `BRIDGE_2_API_TOKEN`, `BRIDGE_1_HOST`,
      `BRIDGE_2_HOST` passthrough to molecule `provisioner.env`
- [ ] Update `build.py`:
  - Add `"BRIDGE_1_HOST"`, `"BRIDGE_2_HOST"` to `OPTIONAL_HOST_VARS`
  - Add `"BRIDGE_1"`, `"BRIDGE_2"` to `KNOWN_HOSTS`
- [ ] Add both bridges to `_HOST_MAP` in `scripts/webui/data.py`:
  - `"BRIDGE_1_HOST": "bridge-1"`, `"BRIDGE_2_HOST": "bridge-2"`
- [ ] Add `EnvVar("BRIDGE_1_HOST", ...)` and `EnvVar("BRIDGE_2_HOST", ...)`
      to `ENV_TEMPLATE` in `data.py`
- [ ] Add both bridges to `scripts/wol.sh` host tables (if WoL capable)
- [ ] Update `tests/test_build.py` `TestInfrastructureHealth` to probe
      both bridges at `BRIDGE_1_HOST` and `BRIDGE_2_HOST`
**Phase B — WiFi infrastructure hardening** (significant, ~2 hours):

- [ ] **Harden `proxmox_pci_passthrough` WiFi detection**:
  1. Move WiFi module loading BEFORE the interface scan. Currently
     modules only load during stale vfio cleanup (line 120). Add an
     unconditional module load block at the top of the role, before
     "Find network interfaces with wireless capability" (line 4).
     Use the same module list: `iwlwifi ath9k ath10k_pci mt76 mt7921e
     rtw88_pci rtw89_pci`. Add `sleep 2` for device settling.
  2. After module loading, if no WiFi interfaces appear but `lspci`
     shows a network controller, check firmware:
     - Detect WiFi PCI devices via `lspci -nn | grep -i 'Network controller'`
     - If devices found but no `/sys/class/net/*/wireless`: firmware
       likely missing
     - Check: `dpkg -l firmware-iwlwifi 2>/dev/null | grep -q '^ii'`
     - If Intel WiFi detected and firmware-iwlwifi not installed:
       install it via apt (same pattern as proxmox_igpu VA-API packages)
     - After installation: `modprobe -r iwlwifi && modprobe iwlwifi`
       to reload with firmware
     - Retry WiFi interface detection
  3. Add PHY verification: after WiFi detection succeeds, count PHYs
     in `/sys/class/ieee80211/` and export as `wifi_phy_count` fact
     alongside `wifi_pci_devices`
  4. Hard-fail with specific diagnostic if PCI device detected but no
     PHY after firmware install + module reload:
     "WiFi PCI device found at <addr> but no PHY created. Check
     dmesg | grep iwlwifi for firmware load errors."
  5. Remove duplicate module loading from the stale vfio cleanup block
     (now handled by the unconditional block at the top)
  6. Existing detection logic (interface scan → PCI address resolution →
     driver detection → vfio binding for router_nodes) is already dynamic
     and chipset-agnostic. No changes needed there.
- [ ] **Verify the hardened role works on existing nodes**: Run
      `molecule converge --tags infra` and confirm all 4 existing nodes
      (home, mesh1, ai, mesh2) still pass. WiFi-equipped nodes (home,
      mesh1, mesh2) should show `wifi_phy_count > 0`. Non-WiFi nodes
      (ai) should show `wifi_phy_count: 0` without failure.
- [ ] Verify SSH connectivity to both bridges:
      `ssh root@192.168.86.230 hostname`
      `ssh root@192.168.86.231 hostname`
- [ ] Verify shared infrastructure runs on all 6 nodes:
      `molecule converge` succeeds for backup, bridges, PCI passthrough
- [ ] Verify iGPU detection on both: `proxmox_igpu` exports
      `igpu_available: true`. If either lacks an iGPU, implement
      the skip-gate in `proxmox_igpu` (separate change, gated on
      iGPU-consuming group membership)
- [ ] Verify AX210 detected on both via the hardened PCI passthrough role:
      `wifi_pci_devices` is non-empty on bridge-1 AND bridge-2
- [ ] Verify WiFi PHY appears on both:
      `wifi_phy_count > 0` on bridge-1 AND bridge-2 after infrastructure
      play. If firmware-iwlwifi was auto-installed, confirm PHYs appeared
      after module reload.
- [ ] Verify USB NIC detected on bridge-2:
      `ip link` shows the USB NIC device on bridge-2
- [ ] Verify USB NIC cable link between bridge-2 and mesh2:
      Assign temp IPs on both USB NICs, ping between them

**Verify:**
- [ ] `ansible bridge-1 -m ping` succeeds
- [ ] `ansible bridge-2 -m ping` succeeds
- [ ] `proxmox_bridges` exports `proxmox_all_bridges` for both bridges
- [ ] `proxmox_pci_passthrough` detects AX210 WiFi on both bridges:
      - `wifi_pci_devices` is non-empty on each
      - `wifi_phy_count > 0` on each (new fact from hardening)
      - `firmware-iwlwifi` is installed on both hosts (auto-installed
        by the hardened role if missing)
- [ ] WiFi firmware loaded successfully: `dmesg | grep iwlwifi` shows
      "loaded firmware version" (not "failed to load" or "Direct firmware
      load failed") on both bridges
- [ ] Hardened role is backward-compatible: existing WiFi nodes (home,
      mesh1, mesh2) still pass with `wifi_phy_count > 0`. Non-WiFi node
      (ai) passes with `wifi_phy_count: 0`, no failure.
- [ ] `proxmox_igpu` exports `igpu_available: true` for both
      (or iGPU skip-gate is in place)
- [ ] Bridge-2 has at least 2 Proxmox bridges (supernet NIC + USB NIC)
- [ ] No other service containers deployed on either bridge (neither is
      in any service group)
- [ ] `pytest tests/test_build.py -v` passes (includes both bridge probes)
- [ ] All 6 hosts participate in `molecule converge` without failures

**Rollback:**
Remove both bridges from `inventory/hosts.yml` and delete
`inventory/host_vars/bridge-1.yml`, `inventory/host_vars/bridge-2.yml`.
Remove `openwrt_bridge` dynamic group. Remove both bridge platforms from
`molecule/default/molecule.yml`. Remove env passthrough from molecule
provisioner. Revert `build.py`, `data.py`, `wol.sh`, and
`test_build.py` changes. The `proxmox_pci_passthrough` WiFi hardening
is backward-compatible and should NOT be reverted — it improves detection
for all WiFi-equipped nodes, not just bridges.

---

### Milestone 1: Bridge LXC Provisioning + Integration

_Self-contained. Depends on M0 (both bridges onboarded with verified WiFi)._

Create the `openwrt_bridge_lxc` role that provisions an OpenWrt LXC
container on bridge nodes, detects and moves WiFi PHYs into the
container namespace, and deploys a hookscript for PHY persistence.
Add VMID 104 and IP offset to `group_vars/all.yml`, provision and
configure plays to `site.yml`, per-feature molecule scenario, and
cleanup entries in both cleanup playbooks.

The provisioning role must handle the asymmetric topology: bridge-1's
container connects to the WAN bridge (supernet), while bridge-2's
container connects to the USB NIC bridge (backhaul to mesh2). The
role detects which bridge to use based on available NICs.

See: `openwrt-mesh-lxc-wifi` skill, `lxc-container-patterns` skill,
`vm-lifecycle` skill.

**Implementation pattern:**
- Provision role: `roles/openwrt_bridge_lxc/defaults/main.yml` +
  `roles/openwrt_bridge_lxc/tasks/main.yml`
- Configure role: `roles/openwrt_bridge_configure/` (created in M2)
- site.yml plays (inserted after mesh-wifi plays, before desktop plays):
  - Play N: `hosts: bridge_nodes`, `gather_facts: false`,
    `tags: [bridge]`, roles: `openwrt_bridge_lxc` + `deploy_stamp`
  - Play N+1: `hosts: openwrt_bridge`, `gather_facts: false`,
    `tags: [bridge]`, roles: `openwrt_bridge_configure`
  - Tag `bridge` runs during normal converge (NOT `never`-tagged)
- Group reconstruction: `tasks/reconstruct_bridge_group.yml`
- Molecule scenario: `molecule/bridge-lxc/`
- Cleanup: VMID 104 added to `molecule/default/cleanup.yml`,
  `molecule/default/cleanup_lan_host.yml`, `tasks/cleanup_lan_host.yml`,
  AND `playbooks/cleanup.yml`

- [ ] Add `bridge_ct_id: 104` to `inventory/group_vars/all.yml`
- [ ] Add `bridge_ct_ip_offset: 27` to `inventory/group_vars/all.yml`
- [ ] Add `104: 2` to `proxmox_startup_order` in `group_vars/all.yml`
- [ ] Create `roles/openwrt_bridge_lxc/defaults/main.yml`:
  ```yaml
  openwrt_bridge_ct_hostname: openwrt-bridge
  openwrt_bridge_ct_memory: 512
  openwrt_bridge_ct_cores: 2
  openwrt_bridge_ct_disk: "1"
  openwrt_bridge_ct_onboot: true
  openwrt_bridge_ct_startup_order: 2
  openwrt_bridge_ct_features:
    - "nesting=1"
  ```
- [ ] Create `roles/openwrt_bridge_lxc/tasks/main.yml` following
      `openwrt_mesh_lxc` pattern:
  1. Release stale containers (check `pct status 104`, stop, destroy)
  2. **Skip duplicate module loading** — `proxmox_pci_passthrough`
     (hardened in M0) already loads WiFi modules, installs firmware,
     and exports `wifi_phy_count`. Bridge_lxc can assert
     `wifi_phy_count > 0` instead of re-loading modules.
     Fallback: if `wifi_phy_count` is undefined (role ran without
     hardening), load modules as `openwrt_mesh_lxc` does.
  3. Detect WiFi PHYs via `/sys/class/ieee80211/`
  4. Hard-fail if no PHY found
  5. Set container network — bridge selection logic:
     - Detect USB NICs via sysfs: check
       `/sys/class/net/*/device/subsystem` for `../../bus/usb`
     - **Exclude the management NIC**: filter out the interface that
       carries `ansible_default_ipv4.address` (the supernet management
       IP). The remaining USB NIC is the backhaul to mesh2.
     - If non-management USB NIC found (bridge-2): use its Proxmox
       bridge for container eth0
     - If no non-management USB NIC (bridge-1): use
       `proxmox_wan_bridge` for container eth0
     - IP = offset 27 + 200 + bridge_nodes.index(hostname)
     - Gateway = `ansible_default_ipv4.gateway` (supernet gateway)
  6. Verify mesh LXC image exists (`openwrt_lxc_template_path`)
  7. Provision via `include_role: proxmox_lxc` with:
     - `lxc_ct_ostype: unmanaged`
     - `lxc_ct_unprivileged: false`
     - `lxc_ct_skip_debian_cleanup: true`
     - `lxc_ct_dynamic_group: openwrt_bridge`
  8. Move WiFi PHY into container namespace
  9. Deploy hookscript
     (`/var/lib/vz/snippets/bridge-wifi-phy-104.sh`)
- [ ] Add provision play to `playbooks/site.yml` after mesh-wifi plays,
      before desktop plays:
  ```yaml
  - name: Provision WiFi Bridge LXC
    hosts: bridge_nodes
    gather_facts: false
    tags: [bridge]
    roles:
      - openwrt_bridge_lxc
      - role: deploy_stamp
        vars:
          deploy_stamp_play: openwrt_bridge_lxc

  - name: Configure WiFi Bridge
    hosts: openwrt_bridge
    gather_facts: false
    tags: [bridge]
    roles:
      - openwrt_bridge_configure
  ```
  Note: `gather_facts: false` is REQUIRED on the configure play.
  `openwrt_bridge` is a `pct_remote` dynamic group. `gather_facts: true`
  causes buffer overflow on hosts with many block devices.
- [ ] Create `tasks/reconstruct_bridge_group.yml` (calls
      `tasks/reconstruct_lxc_group.yml` with bridge-specific vars)
- [ ] Add VMID 104 to cleanup lists:
  - `molecule/default/cleanup.yml` → `project_ct_ids`
  - `molecule/default/cleanup_lan_host.yml` → container ID list
  - `tasks/cleanup_lan_host.yml` → container ID list
  - `playbooks/cleanup.yml` → container ID list + bridge-rollback tag
- [ ] Add hookscript path to cleanup file removal lists:
  `/var/lib/vz/snippets/bridge-wifi-phy-104.sh`
- [ ] Create `molecule/bridge-lxc/molecule.yml`:
  - Two platforms: `bridge-1` and `bridge-2`, both with
    groups `[proxmox, bridge_nodes]`
  - Env passthrough: `BRIDGE_1_API_TOKEN`, `BRIDGE_2_API_TOKEN`,
    `BRIDGE_1_HOST`, `BRIDGE_2_HOST`, `MESH_KEY`
- [ ] Create `molecule/bridge-lxc/prepare.yml` (assert mesh LXC image
      exists on both hosts — per two-tier testing pattern)
- [ ] Create `molecule/bridge-lxc/converge.yml`
- [ ] Create `molecule/bridge-lxc/cleanup.yml`
- [ ] Add bridge LXC assertions to `molecule/default/verify.yml`
- [ ] Create `molecule/mesh-ax210/molecule.yml` — cross-hardware mesh test:
  - Platforms: `bridge-1` and `bridge-2` in
    groups `[proxmox, wifi_nodes]` (NOT `bridge_nodes`)
  - Env passthrough: `BRIDGE_1_API_TOKEN`, `BRIDGE_2_API_TOKEN`,
    `BRIDGE_1_HOST`, `BRIDGE_2_HOST`, `MESH_KEY`
- [ ] Create `molecule/mesh-ax210/converge.yml` — runs shared infra +
      `openwrt_mesh_lxc` + `openwrt_mesh_configure` (existing mesh roles)
- [ ] Create `molecule/mesh-ax210/cleanup.yml` — destroys VMID 103
      on both bridges
- [ ] Create `molecule/mesh-ax210/verify.yml` — asserts mesh peering
      works on AX210: WiFi PHY detected, VMID 103 running, mesh peers
      established, `iw station dump` shows active peer

**Verify:**
- [ ] `pct status 104` returns `running` on BOTH bridges
- [ ] `pct config 104` shows: memory=512, cores=2, onboot=1 on both
- [ ] WiFi PHY visible inside both containers:
      `pct exec 104 -- iw phy` shows at least one Wiphy
- [ ] Bridge-1 container eth0 is on WAN bridge (supernet)
- [ ] Bridge-2 container eth0 is on USB NIC bridge (backhaul to mesh2)
- [ ] Hookscript deployed on both hosts at
      `/var/lib/vz/snippets/bridge-wifi-phy-104.sh`
- [ ] `molecule test -s bridge-lxc` passes
- [ ] `molecule test -s mesh-ax210` passes — mesh roles work on AX210
      hardware (bridge HW acting as mesh nodes with VMID 103)

**Rollback:**
Stop and destroy VMID 104 on both bridges:
`pct stop 104 && pct destroy 104` on bridge-1 and bridge-2.
Remove hookscripts. Remove plays from `site.yml`. Remove `bridge_ct_id`
and `bridge_ct_ip_offset` from `group_vars/all.yml`. Remove `104` from
`proxmox_startup_order`. Delete `roles/openwrt_bridge_lxc/`,
`tasks/reconstruct_bridge_group.yml`, and `molecule/bridge-lxc/`.
Remove VMID 104 from all cleanup lists. Delete `molecule/mesh-ax210/`.

---

### Milestone 2: Bridge Configuration + Performance Tuning + Backhaul

_Self-contained. Depends on M1 (both bridge LXCs provisioned with WiFi PHYs)._

Create the `openwrt_bridge_configure` role that sets up a transparent
L2 bridge inside both OpenWrt containers: configure 802.11s mesh with
a dedicated mesh_id, bridge the WiFi interface with the wired interface,
apply WiFi 6E performance tuning for maximum throughput on the dedicated
point-to-point link, and verify end-to-end backhaul connectivity through
the bridge pair to mesh2.

See: `openwrt-build` skill, `openwrt-busybox-constraints` skill,
`openwrt-mesh-lxc-wifi` skill.

**Implementation pattern:**
- Task file: `roles/openwrt_bridge_configure/tasks/main.yml`
- Defaults: `roles/openwrt_bridge_configure/defaults/main.yml`
- Configure play already added in M1 (targets `openwrt_bridge`)
- All commands via `ansible.builtin.raw` (OpenWrt BusyBox, no Python)
- Commands wrapped in `/bin/sh -c '...'` for pct_remote compatibility
  (host bash interprets before pct exec — see `openwrt-ssh-pct-remote` skill)
- NEVER use `export` in raw commands (lxc-attach tries to exec it)
- Molecule scenario: `molecule/bridge-lxc/` (extend with verify)

- [ ] Create `roles/openwrt_bridge_configure/defaults/main.yml`:
  ```yaml
  openwrt_bridge_mesh_id: "bridge-dedicated"
  openwrt_bridge_mesh_key: "{{ lookup('env', 'MESH_KEY') | default('changeme', true) }}"
  openwrt_bridge_encryption: sae
  openwrt_bridge_preferred_band: "6g"   # role scans iw phy, selects best available
  openwrt_bridge_htmode: "HE160"
  openwrt_bridge_channel: auto
  ```
- [ ] Create `roles/openwrt_bridge_configure/tasks/main.yml`:
  1. **Detect WiFi radios** via `iw phy` (netlink, namespace-aware).
     Use retries (10 retries, 3s delay) — PHY may take time after
     namespace move. Hard-fail if zero radios after retries.
  2. **Generate wireless config** from detected hardware:
     Run `wifi config` to populate `/etc/config/wireless`. If empty
     (expected for namespace-moved PHYs), create UCI sections manually
     from `iw phy` output. This is the single tested path — same
     deterministic pattern as `openwrt_mesh_configure`.
  3. **Enable each radio**: `uci set wireless.radioN.disabled=0`
  4. **Select best band**: Query `iw phy` for supported frequency
     bands. Prefer 6 GHz (least interference, highest throughput for
     dedicated link). If 6 GHz unavailable (regulatory, firmware),
     select 5 GHz with HE160. Log the selected band so operator knows
     what's active. Hard-fail only if no usable band exists (2.4-only
     or zero radios). Set `htmode` to match: HE160 for 6g/5g.
  5. **Create mesh interface** for each radio:
     - `mode=mesh`, `mesh_id=bridge-dedicated`
     - `encryption=sae`, `key=<MESH_KEY>`
     - `mesh_fwding=1` (transparent L2 forwarding)
     - `network=lan` (bridges WiFi into br-lan with eth0)
     - `mesh_rssi_threshold=0` (accept all signal levels for dedicated link)
  6. **Enable STP on br-lan**: `uci set network.lan.stp=1`
     Safety measure against accidental L2 loops.
  7. **Disable power management**: Ensure consistent throughput.
  8. **Commit all config**: `uci commit wireless && uci commit network`
  9. **Reload wireless**: `wifi reload` with retries (3 retries, 5s delay)
  10. **Verify mesh interface up**: Check `iw dev` shows mesh interface
      in mesh mode
- [ ] Create `molecule/bridge-lxc/verify.yml` with assertions:
  - WiFi radio detected inside both containers (`iw phy` count > 0)
  - Mesh interface exists on both (`iw dev` shows mesh interface)
  - Mesh mode active with mesh_id `bridge-dedicated` on both
  - br-lan bridge contains both eth0 and mesh interface on both
  - STP enabled on br-lan on both
  - 802.11s mesh peer visible (`iw station dump` shows peer on each)
  - End-to-end: traffic can traverse bridge-1 → WiFi → bridge-2 →
    USB NIC → mesh2 (ping or arping from bridge-1 container to
    mesh2's USB NIC IP)
- [ ] Update `molecule/bridge-lxc/converge.yml` to include configure play
      (requires group reconstruction before configure play)

**Verify:**
- [ ] WiFi radio enabled inside both bridge containers
      (`pct exec 104 -- iw dev` shows mesh interface)
- [ ] Best available band active on both radios — expect 6 GHz on AX210
      (`pct exec 104 -- iw dev mesh0 info` shows freq; log confirms
      band selection rationale)
- [ ] 802.11s mesh active with mesh_id `bridge-dedicated` on both
- [ ] Mesh peers detected on both sides
      (`pct exec 104 -- iw station dump` shows 1 peer)
- [ ] br-lan bridge members include eth0 and mesh interface on both
      (`pct exec 104 -- brctl show` lists both ports)
- [ ] STP enabled on both (`brctl showstp br-lan`)
- [ ] End-to-end backhaul connectivity:
      traffic from bridge-1 container reaches mesh2's USB NIC
- [ ] Both containers survive reboot via hookscript:
      `pct reboot 104` on both hosts, PHY re-moves after restart
- [ ] `molecule test -s bridge-lxc` passes end-to-end

**Rollback (`--tags bridge-rollback`):**
Stop and destroy container VMID 104 on both bridges. Remove hookscripts.
This is equivalent to M1 rollback — the configure role makes no
host-side changes (all config is inside the containers, destroyed
with them).

---

### Milestone 3: Documentation + Integration

_Self-contained. Depends on M1 (bridge service exists in site.yml)._

Update project documentation, WebUI service tags, architecture docs,
and cleanup lists to reflect the new bridge service and both bridge hosts.

See: `writing-skills` skill.

- [ ] Create `docs/architecture/bridge-build.md` documenting:
  - Transparent bridging architecture
  - WiFi 6E performance characteristics
  - AX210 driver/firmware requirements
  - Container network topology (br-lan = eth0 + mesh0)
  - Bridge-2 multi-NIC topology (USB NIC backhaul to mesh2)
  - Deployment constraints (no L2 loops between switches)
  - Test vs production topology differences
- [ ] Update `docs/architecture/overview.md`:
  - Add bridge-1 (.230) and bridge-2 (.231) to Physical Layout
  - Add "Dedicated WiFi Bridge" to Build Profiles section
  - Add VMID 104 to VMID Allocation table
  - Add `openwrt_bridge_lxc` / `openwrt_bridge_configure` to Service
    Roles section
  - Add bridge plays to Playbook Execution Order
  - Add `bridge_nodes` to Device Flavors
  - Update node count from 4 to 6
- [ ] Update `scripts/webui/data.py`:
  - Add `ServiceTag("bridge", "Dedicated WiFi Bridge", "Network",
    ["bridge-1", "bridge-2"])`
  - (ENV_TEMPLATE and _HOST_MAP already updated in M0)
- [ ] Add CHANGELOG entry under `[Unreleased]`
- [ ] Update `project-structure.mdc` rule:
  - Add `bridge_nodes` to device flavors section
  - Add VMID 104 to VMID allocation
  - Add bridge plays to site.yml listing
  - Add bridge-1 and bridge-2 to network topology
  - Add `BRIDGE_1_HOST`, `BRIDGE_2_HOST`, and both `_API_TOKEN` vars
    to env vars section
  - Add `tasks/reconstruct_bridge_group.yml` to key files table
  - Update node count references from 4 to 6
- [ ] Verify all cleanup lists include VMID 104 (cross-check M1)
- [ ] Verify `playbooks/cleanup.yml` has bridge-rollback tag

**Verify:**
- [ ] `docs/architecture/overview.md` mentions bridge service, VMID 104,
      bridge_nodes group, both bridge hosts
- [ ] `scripts/webui/data.py` `SERVICE_TAGS` includes `bridge`
- [ ] CHANGELOG has unreleased entry for WiFi bridge
- [ ] `ansible-lint && yamllint .` passes
- [ ] `pytest tests/ -v` passes
- [ ] `molecule test` passes with all 6 nodes

**Rollback:**
Revert documentation changes. Remove ServiceTag from `data.py`.
Remove CHANGELOG entry.

---

## Future Integration Considerations

- **Adding VPN to bridges**: If either bridge is later added to
  `vpn_nodes`, all WireGuard container IPs shift. home from .4 to .6,
  mesh1 from .5 to .7, mesh2 from .206 to .208. Run `molecule test`
  (clean state) to recreate all containers with correct IPs.
  Incremental deploy will leave stale IPs.

- **Adding monitoring to bridges**: If either bridge is added to
  `monitoring_nodes`, rsyslog and Netdata container IPs shift. With 6
  monitoring hosts, rsyslog on home shifts to .15, **colliding with
  Jellyfin**. This requires either: (a) increasing spacing between
  offsets project-wide, or (b) using a different IP allocation scheme.

- **Mesh + Bridge coexistence**: If a host needs both general mesh
  (VMID 103) AND dedicated bridge (VMID 104), it needs two WiFi PHYs.
  Current roles move ALL detected PHYs into their container. PHY
  partitioning (assign specific PHYs to specific containers) is a
  future enhancement. For now, a host is either mesh OR bridge.

- **Production LAN topology migration**: In production, bridge-1
  connects to the LAN switch (behind OpenWrt) instead of the supernet.
  The container's eth0 connects to the LAN bridge. This is a
  `host_vars` override (`openwrt_bridge_wan_bridge` → LAN bridge name).
  The configure role is unchanged — UCI config is subnet-agnostic.

- **Dynamic management IP for production moves**: When nodes move from
  supernet to LAN, their management IPs change. The existing
  `build.py` probing + `.state/addresses.json` fallback handles this.
  `host_vars` would use the LAN IP. The supernet IP remains as a
  fallback in the state file.

- **Failover and monitoring**: The bridge is a single point of failure
  for devices behind bridge-2. Adding monitoring (ping watchdog,
  throughput alerts) via Netdata on bridge-1 would provide early
  warning. This requires adding bridges to `monitoring_nodes` (see
  IP collision caveat above).

- **Multiple bridge pairs**: Each pair needs a unique `mesh_id`. The
  configure role already accepts `openwrt_bridge_mesh_id` as a default.
  For multiple pairs, override via `host_vars` or introduce a
  per-pair variable.

- **WoL chain for remote bridge-2**: When bridge-2 is at a remote
  location (only reachable via WiFi bridge), wake sequence:
  (1) WoL bridge-1, (2) wait for bridge LXC to start, (3) bridge-2
  management interface comes up via the WiFi bridge. Automate in
  `wol.sh` as a chained wake target.

- **Mesh2 as wired backhaul mesh point**: Once bridge-2 is forwarding
  supernet (or LAN) traffic to mesh2 via the USB NIC cable, mesh2's
  mesh LXC container can participate in the mesh network with wired
  backhaul instead of wireless-only. This improves mesh2's throughput
  and latency. The mesh container config doesn't change — it still
  creates a mesh interface and bridges it. The wired backhaul is
  transparent to the mesh layer.

## Resolved Planning Items

1. **WoL capability**: Discovered during M0 infrastructure converge.
   `ethtool` on each NIC shows WoL support. `host_vars` updated with
   the result. If USB-only NICs: `wol_capable: false`, excluded from
   `wol.sh`.

2. **iGPU presence**: Discovered during M0 when `proxmox_igpu` runs.
   If either bridge lacks an iGPU, the role hard-fails. Resolution:
   gate `proxmox_igpu` to skip on hosts not in any iGPU-consuming
   group (`media_nodes`, `desktop_nodes`, `gaming_nodes`). This is a
   separate infrastructure improvement, implemented inline during M0
   if needed.

3. **6 GHz regulatory**: 6 GHz is preferred. The configure role
   queries `iw phy` for supported bands at runtime and selects the
   best available — 6 GHz if supported, 5 GHz otherwise. AX210
   supports 6 GHz; expect 6g in normal operation.

4. **USB NIC bridge detection**: The `openwrt_bridge_lxc` role detects
   USB NICs via sysfs (`/sys/class/net/*/device/subsystem`) at
   provision time. It excludes the management NIC (the one carrying
   `ansible_default_ipv4.address`) and selects the remaining USB NIC's
   Proxmox bridge. Fully automated, no manual input needed.

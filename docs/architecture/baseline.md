# Baseline State

## Definition

The **baseline** is the system state after `site.yml` converges successfully
(plays 0–4). Every per-feature enhancement (security, VLANs, DNS, mesh)
builds on top of this state and can be rolled back to it without a full rebuild.

## What site.yml produces

```
Baseline State
├── Play 0: proxmox_backup
│   ├── /var/lib/ansible-backup/host-config.tar.gz
│   ├── /var/lib/ansible-backup/manifest.json (includes project_version)
│   └── vzdump archives for any pre-existing VMs
│
├── Play 1: shared infrastructure
│   ├── Virtual bridges (one per physical NIC)
│   ├── PCI passthrough (WiFi to vfio-pci, if present)
│   ├── iGPU driver + Quick Sync validation (if Intel GPU present)
│   └── deploy_stamp: backup, infrastructure plays recorded
│
├── Play 2: openwrt_vm
│   ├── VM 100 running, 2 cores, 512 MB RAM, 512 MB disk
│   ├── net0 on WAN bridge (auto-detected), netN on LAN bridges
│   ├── onboot=1, startup order=1
│   ├── WiFi PCIe passthrough (when hardware present)
│   └── deploy_stamp: openwrt_vm play recorded
│
├── Play 3: openwrt_configure
│   ├── WAN interface: DHCP from upstream (eth0)
│   ├── LAN subnet: collision-free selection (default 10.10.10.0/24)
│   ├── DHCP: start=100, limit=150, leasetime=12h
│   ├── Firewall: WAN=reject, LAN=accept, masquerading enabled
│   ├── WiFi mesh: 802.11s with WPA3-SAE (when radios present)
│   ├── WAN MAC: applied or deferred (when WAN_MAC set)
│   ├── Proxmox LAN management IP on LAN bridge (DHCP)
│   ├── DHCP static lease for Proxmox host
│   └── .state/addresses.json on controller
│
└── Play 4: bootstrap cleanup
    └── Temporary bootstrap IP removed from LAN bridge
```

## Invariants

These conditions MUST be true after baseline converge and MUST remain true
after any per-feature enhancement or rollback:

1. **VM 100 is running** with correct resource allocation
2. **WAN has a DHCP IP** from upstream and a default route
3. **LAN subnet does not collide** with WAN subnet
4. **DHCP is serving** on LAN with configured start/limit/leasetime
5. **Firewall zones** are active: WAN=reject, LAN=accept
6. **Proxmox LAN management IP** exists on LAN bridge and is reachable on port 8006
7. **DHCP reservation** exists for Proxmox host MAC → management IP
8. **deploy_stamp** records backup, infrastructure, openwrt_vm plays
9. **Backup manifest** exists with timestamp, host, host_config, VMs
10. **.state/addresses.json** exists with host and IPs array
11. **SSH to OpenWrt works** via ProxyJump through Proxmox host
    (password auth with empty password in baseline; key auth after M1)

## Assertion coverage

`molecule/default/verify.yml` validates all invariants above. The current
assertion categories:

| Category | Assertions |
|---|---|
| Bridge discovery | At least 2 physical bridges exist |
| WAN bridge | Detected from default route, is a known physical bridge |
| VM state | Running, onboot=1, startup order=1 |
| NIC topology | net0 on WAN bridge |
| LAN management | IP on LAN bridge, DHCP in bridges.conf, port 8006 accessible |
| DHCP config | start, limit, leasetime set; Proxmox host reservation exists |
| LAN subnet | Does not collide with WAN prefix |
| WAN MAC | Applied OR deferred (when WAN_MAC set); no artifacts when not set |
| WiFi/mesh | Mesh interfaces match radio count (when WiFi present) |
| Backup | Manifest exists with required fields, archive on disk |
| Deploy tracking | vm_builds.fact exists with expected plays |
| State file | addresses.json exists with host and IPs |
| GUI access | Port 8006 reachable from OpenWrt VM (leaf node view) |

## Per-feature scenario model

Per-feature molecule scenarios (`molecule/openwrt-<feature>/`) start from
the baseline and only converge their own changes:

```
molecule/default/           → full rebuild (cleanup → converge → verify → cleanup)
molecule/openwrt-security/  → security only (converge → verify → cleanup)
molecule/openwrt-vlans/     → VLANs only (converge → verify → cleanup)
molecule/openwrt-dns/       → DNS only (converge → verify → cleanup)
molecule/openwrt-mesh/      → mesh only (converge → verify → cleanup)
```

Each per-feature scenario:
1. Verifies the baseline exists (VM 100 running)
2. Reconstructs the `openwrt` dynamic group via `add_host`
3. Converges only the feature's task file
4. Verifies only the feature's assertions
5. Rolls back via the feature's cleanup tag

Per-feature rollback returns to baseline without a full rebuild (~30s vs ~5min).

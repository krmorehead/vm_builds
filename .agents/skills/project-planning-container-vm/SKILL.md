---
name: project-planning-container-vm
description: Container and VM planning constraints and capability requirements. Use when planning LXC containers, VM resources, or container/VM specific configurations.
---

# Container/VM Planning Constraints

## LXC Features and Capabilities

1. If the plan provisions an LXC container, verify that required features are declared:
   - `nesting=1`: needed for iptables/nftables inside unprivileged containers AND for systemd services that use sandboxing directives
   - `mount=cgroup`: needed for cgroup mounts (systemd containers)
   - `keyctl=1`: needed for kernel key management

2. If the service uses systemd sandboxing (most Debian services), the plan MUST also include a systemd drop-in override baked into the image via `build-images.sh`. NEVER deploy the override via the configure role — it's base system config.

3. Previous bug: WireGuard plan omitted `nesting=1`. The `iptables -t nat MASQUERADE` command would have failed with "Permission denied" at runtime.

4. Previous bug: Netdata LXC exited 226/NAMESPACE even as privileged. Root cause: `LogNamespace=netdata` in the systemd unit requires `CLONE_NEWNS`, forbidden without `nesting=1`. Fix: `nesting=1` + systemd override clearing `LogNamespace`.

## LXC Disk and Resource Planning

5. For LXC services, count the tasks in the proposed configure role. Each `pct_remote` task adds 15-60s of overhead. If a task deploys config that is identical across all containers, move it to the image build.

6. Verify the planned rootfs disk size accommodates the EXTRACTED template, not just the compressed size. Compressed templates can be 3-5x smaller. Minimum 2GB for services with databases or monitoring data.

## Network Topology Requirements

7. The plan MUST document which host topologies the service supports? If the flavor group could include hosts both behind OpenWrt (LAN) and directly on WAN, the plan MUST specify the topology branching strategy (bridge, subnet, gateway, DNS).

8. A "Network topology assumption" section is required for all LXC container and VM plans.

9. Previous bug: rsyslog plan only said "static IP on LAN bridge" but `monitoring_nodes` appears in ALL build profiles including Gaming Rig (no OpenWrt). WAN-connected hosts need different bridge, gateway, and DNS settings.

## Container IP Offset Planning

10. If the service uses static IPs computed from an offset, verify the offset is defined in `group_vars/all.yml` and doesn't collide with existing allocations.

11. Current allocations: WireGuard 3–6, Pi-hole 10, rsyslog 12, Netdata 13, HA 14, Jellyfin 15, MeshWiFi 20. WAN offsets add +200.

## WiFi PHY Requirements for Containers

12. If the plan provisions an LXC container that needs WiFi access, verify:
    - Container is privileged (`unprivileged: false`) — namespace moves require CAP_NET_ADMIN
    - `--ostype unmanaged` for OpenWrt containers (Proxmox can't auto-detect)
    - `lxc_ct_skip_debian_cleanup: true` for non-Debian containers
    - WiFi driver loading on the host before PHY detection
    - Hookscript for PHY persistence across container restarts

13. Previous bug: mesh1 WiFi was invisible because stale vfio-pci.conf and blacklist-wifi.conf from a prior run kept iwlwifi blacklisted and the device bound to vfio-pci.

## Bake vs Configure Separation

14. Per `project-structure.mdc`: if the plan mentions runtime package installation (`opkg install`, `apt install`), reject it. Packages AND base configuration belong in the image build. Configure roles only apply host-specific topology.

15. If the software is already in the base OS (e.g., rsyslog in Debian), the image build should pre-configure it — the configure role should not set up listeners, spool directories, or logrotate from scratch.
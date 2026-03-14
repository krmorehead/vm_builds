# Netdata Build

## Image Build

Netdata uses a custom Debian 12 LXC template with the Netdata monitoring agent
pre-installed and configured for host metrics collection. The template is built
remotely on a Proxmox host by `build-images.sh --host <ip>`:

1. Upload the base Debian 12 template to the Proxmox host (if not cached)
2. `pct create 996` — temporary unprivileged container with DHCP networking
3. `pct exec 996` — install Netdata via the official kickstart script,
   pre-configure `netdata.conf` with dbengine retention (1 hour),
   `/host/proc` and `/host/sys` paths, dashboard on port 19999,
   deploy systemd override for LXC compatibility, clean apt caches
4. `pct stop 996` — stop the container
5. `vzdump 996` — export the container filesystem as a zstd-compressed archive
6. `scp` — download the archive to `images/netdata-debian-12-amd64.tar.zst`
7. `pct destroy 996 --purge` — clean up

### What's Baked In

| Component | Path | Description |
|-----------|------|-------------|
| Netdata agent | `/usr/sbin/netdata` or `/opt/netdata/` | Full monitoring daemon |
| Config | `netdata.conf` | dbengine, proc/sys paths, dashboard port |
| systemd override | `netdata.service.d/lxc-override.conf` | Disables LogNamespace and sandboxing |
| Web dashboard | port 19999 | Local monitoring UI |

### Host Metrics via Bind Mounts

The container accesses host-level metrics via read-only bind mounts
configured by the `netdata_lxc` provisioning role:

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `/proc` | `/host/proc` | CPU, memory, disk, process metrics |
| `/sys` | `/host/sys` | Temperature, hardware sensors, device info |

The `netdata.conf` in the image sets `proc = /host/proc` and `sys = /host/sys`
so Netdata reads host metrics, not container-local metrics.

### Prerequisites

- The base Debian 12 template must exist in `images/`
- SSH access to a Proxmox host (passed via `--host`)
- Internet access from the Proxmox host during build (Netdata installed via kickstart)
- No root/sudo required on the controller

### Template Path

- Source: `images/netdata-debian-12-amd64.tar.zst`
- Variables: `netdata_lxc_template` and `netdata_lxc_template_path` in `group_vars/all.yml`

## Metrics Flow

```
Host /proc, /sys  →  bind mount  →  Netdata (VMID 500, dashboard 19999)
                                     ↓ (optional, when NETDATA_PARENT_IP set)
                                     Parent Netdata via WireGuard tunnel
```

- Netdata reads host CPU, memory, disk, temperature, and network metrics
  through bind-mounted `/host/proc` and `/host/sys`
- The local dashboard is always available at `http://<container-ip>:19999`
- When `NETDATA_PARENT_IP` is set, the child streams metrics to a parent
  Netdata instance through the WireGuard tunnel (soft dependency)

## Container Resources

| Resource | Value |
|----------|-------|
| VMID | 500 |
| Cores | 1 |
| RAM | 128 MB |
| Disk | 2 GB |
| Privileged | yes (required for bind mounts) |
| Network | Topology-aware (LAN or WAN bridge) |
| Features | nesting=1 (systemd sandboxing) |
| Bind mounts | `/proc` → `/host/proc` (ro), `/sys` → `/host/sys` (ro) |
| Auto-start | yes, priority 3 |

## Container IP

Static IP computed per-host using the monitoring_nodes group index:

- **LAN hosts** (behind OpenWrt): `<LAN_GATEWAY_prefix>.<offset + index>`
  Example: offset=13, home index=0 → netdata IP `10.10.10.13/24`
- **WAN hosts** (directly on supernet): `<WAN_prefix>.<offset + 200 + index>`
  Example: offset=13, ai index=2 → netdata IP `192.168.86.215/24`

The per-host index prevents IP collisions when multiple nodes have Netdata.
Verify tasks query the actual IP from `pct config` to avoid recalculating.

## Env Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `NETDATA_STREAM_API_KEY` | no | API key for parent streaming (empty = local only) |
| `NETDATA_PARENT_IP` | no | Parent Netdata IP via WireGuard (empty = local only) |

Both are soft dependencies on WireGuard. Netdata functions fully as a local
dashboard without either variable set.

## Roles

| Role | Purpose |
|------|---------|
| `netdata_lxc` | Provision container via `proxmox_lxc` with bind mounts |
| `netdata_configure` | Apply host-specific streaming config (optional) |

## Molecule Scenarios

| Scenario | What it tests | Runtime |
|----------|--------------|---------|
| `netdata-lxc` | Container provision + configure + dashboard | ~30-60s |
| `default` | Full integration (includes Netdata on all monitoring_nodes) | ~4-5 min |

## Test vs Production

- Test: `NETDATA_PARENT_IP` and `NETDATA_STREAM_API_KEY` in shell env (optional)
- Production: same variables in `.env` (set when parent Netdata + WireGuard ready)
- Both use the same custom template from `images/`

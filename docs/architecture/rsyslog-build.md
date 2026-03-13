# rsyslog Build

## Image Build

rsyslog uses a custom Debian 12 LXC template with rsyslog pre-configured for
TCP log reception. The template is built remotely on a Proxmox host by
`build-images.sh --host <ip>`:

1. Upload the base Debian 12 template to the Proxmox host (if not cached)
2. `pct create 997` — temporary unprivileged container with DHCP networking
3. `pct exec 997` — install rsyslog, create spool directory, write
   `/etc/rsyslog.d/10-receive.conf` (TCP listener on port 514, `RemoteLogFile`
   template), write `/etc/rsyslog.d/50-remote-route.conf` (route TCP messages
   to per-hostname files), write logrotate config for remote logs, run smoke
   test (`rsyslogd -N1` + service check), clean apt caches
4. `pct stop 997` — stop the container
5. `vzdump 997` — export the container filesystem as a zstd-compressed archive
6. `scp` — download the archive to `images/rsyslog-debian-12-amd64.tar.zst`
7. `pct destroy 997 --purge` — clean up

### What's Baked In

| Component | Path | Description |
|-----------|------|-------------|
| TCP listener | `/etc/rsyslog.d/10-receive.conf` | `imtcp` on port 514, `RemoteLogFile` template |
| Remote routing | `/etc/rsyslog.d/50-remote-route.conf` | Routes TCP msgs to per-hostname files, stops |
| Spool directory | `/var/spool/rsyslog/` | Queue storage (used by forwarding at runtime) |
| Remote log directory | `/var/log/remote/` | Per-hostname log files |
| Logrotate | `/etc/logrotate.d/rsyslog-remote` | Daily rotation, 7-day retention, compression |

### Config Processing Order

```
10-receive.conf      — loads imtcp module, defines RemoteLogFile template
20-forward.conf      — (optional, runtime) forwards ALL messages to home server
50-remote-route.conf — writes TCP-received messages to per-hostname files, stops
```

The numbering ensures optional forwarding config (deployed at runtime between
10 and 50) can intercept messages before they are routed to files and stopped.
Without `20-forward.conf`, remote messages are collected locally and local
messages follow standard processing.

### Prerequisites

- The base Debian 12 template must exist in `images/`
- SSH access to a Proxmox host (passed via `--host`)
- Internet access from the Proxmox host during build (rsyslog is installed via apt)
- No root/sudo required on the controller

### Template Path

- Source: `images/rsyslog-debian-12-amd64.tar.zst`
- Variables: `rsyslog_lxc_template` and `rsyslog_lxc_template_path` in `group_vars/all.yml`

## Log Flow

```
VMs / Containers  →  rsyslog (VMID 501, TCP 514)  →  /var/log/remote/<hostname>/<program>.log
                                                   ↓ (optional)
                                                   Home server via WireGuard (TCP 514)
```

- All network devices and containers forward syslog to the rsyslog collector
- Logs are stored locally per source hostname and program name
- When `RSYSLOG_HOME_SERVER` is set, logs are also forwarded to a home server
  via a disk-assisted queue (survives WireGuard tunnel outages)

## Container Resources

| Resource | Value |
|----------|-------|
| VMID | 501 |
| Cores | 1 |
| RAM | 64 MB |
| Disk | 1 GB |
| Network | Topology-aware (LAN or WAN bridge) |
| Features | none |
| Auto-start | yes, priority 3 |

## Container IP

Static IP computed per-host using the monitoring_nodes group index:

- **LAN hosts** (behind OpenWrt): `<LAN_GATEWAY_prefix>.<offset + index>`
  Example: offset=12, home index=1 → rsyslog IP `10.10.10.13/24`
- **WAN hosts** (directly on supernet): `<WAN_prefix>.<offset + 200 + index>`
  Example: offset=12, ai index=0 → rsyslog IP `192.168.86.212/24`

The per-host index prevents IP collisions when multiple nodes have rsyslog.
Ansible sorts group members alphabetically, so the index depends on the full
group composition. Verify tasks query the actual IP from `pct config` to
avoid recalculating.

## Env Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `RSYSLOG_HOME_SERVER` | no | IP to forward aggregated logs to (empty = local only) |

## Roles

| Role | Purpose |
|------|---------|
| `rsyslog_lxc` | Provision container via `proxmox_lxc` |
| `rsyslog_configure` | Apply host-specific config (optional forwarding) |
| `openwrt_configure/tasks/syslog.yml` | Forward OpenWrt logs to rsyslog |

## Molecule Scenarios

| Scenario | What it tests | Runtime |
|----------|--------------|---------|
| `rsyslog-lxc` | Container provision + configure + log reception | ~30-60s |
| `openwrt-syslog` | OpenWrt UCI syslog forwarding | ~30s |
| `default` | Full integration (includes rsyslog) | ~4-5 min |

## Test vs Production

- Test: `RSYSLOG_HOME_SERVER` in `test.env` (optional, empty by default)
- Production: `RSYSLOG_HOME_SERVER` in `.env` (set when WireGuard tunnel + home server ready)
- Both use the same custom template from `images/`

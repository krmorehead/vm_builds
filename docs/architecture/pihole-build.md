# Pi-hole Build

## Image Build

Pi-hole uses a custom Debian 12 LXC template with Pi-hole pre-installed.
The template is built remotely on a Proxmox host by `build-images.sh --host <ip>`:

1. Upload the base Debian 12 template to the Proxmox host (if not cached)
2. `pct create 998` — temporary unprivileged container with DHCP networking
3. Pre-seed `/etc/pihole/pihole.toml` with `dns.upstreams` (v6 unattended
   install requires this file; the installer treats it as an upgrade and
   skips interactive prompts)
4. Create `pihole` user/group per community convention
5. `pct exec 998` — install dependencies, run Pi-hole unattended installer
6. `pct stop 998` — stop the container
7. `vzdump 998` — export the container filesystem as a zstd-compressed archive
8. `scp` — download the archive to `images/pihole-debian-12-amd64.tar.zst`
9. `pct destroy 998 --purge` — clean up

### Prerequisites

- The base Debian 12 template must exist in `images/`
- SSH access to a Proxmox host (passed via `--host`)
- Internet access from the Proxmox host during build (Pi-hole installer downloads packages)
- No root/sudo required on the controller

### Template path

- Source: `images/pihole-debian-12-amd64.tar.zst`
- Variables: `pihole_lxc_template` and `pihole_lxc_template_path` in `group_vars/all.yml`

## DNS Chain

```
Clients → OpenWrt dnsmasq → Pi-hole (VMID 102) → Cloudflare DoH (1.1.1.1 / 1.0.0.1)
```

- OpenWrt handles DHCP and presents a single DNS IP to clients
- Pi-hole filters queries against blocklists
- Pi-hole forwards unblocked queries to upstream DNS (Cloudflare by default)
- https-dns-proxy on OpenWrt serves as fallback when Pi-hole is unavailable

## Container Resources

| Resource | Value |
|----------|-------|
| VMID | 102 |
| Cores | 1 |
| RAM | 256 MB |
| Disk | 2 GB |
| Network | LAN bridge, static IP |
| Features | `nesting=1` |
| Auto-start | yes, priority 3 |

## Container IP

Static IP on the LAN subnet, computed as:
`<LAN_GATEWAY_prefix>.<pihole_ct_ip_offset>` (default offset: 10)

Example: LAN gateway `10.10.10.1` → Pi-hole IP `10.10.10.10/24`

## Env Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `PIHOLE_WEB_PASSWORD` | no | Web admin password (empty = no password) |

## Roles

| Role | Purpose |
|------|---------|
| `pihole_lxc` | Provision container via `proxmox_lxc` |
| `pihole_configure` | Apply host-specific config (password, DNS, gravity) |
| `openwrt_configure/tasks/pihole_dns.yml` | Forward OpenWrt DNS to Pi-hole |

## Molecule Scenarios

| Scenario | What it tests | Runtime |
|----------|--------------|---------|
| `pihole-lxc` | Container provision + configure | ~30-60s |
| `openwrt-pihole-dns` | dnsmasq DNS forwarding to Pi-hole | ~30s |
| `default` | Full integration (includes Pi-hole) | ~4-5 min |

## Test vs Production

- Test: `PIHOLE_WEB_PASSWORD` in `test.env`
- Production: `PIHOLE_WEB_PASSWORD` in `.env`
- Both use the same custom template from `images/`

# Home Assistant Build

## Image Build

Home Assistant uses a custom Debian 12 LXC template with Docker CE pre-installed and the HA container image pre-pulled. The template is built remotely on a Proxmox host by `build-images.sh --host <ip>`:

1. Upload the base Debian 12 template to the Proxmox host (if not cached)
2. `pct create 994` — temporary unprivileged container with `features: nesting=1` (required for Docker)
3. Install Docker CE from official Docker repository (GPG key + apt source)
4. Install docker-compose plugin and configure cgroup delegation for unprivileged LXC
5. **Documented exception to bake principle**: Pre-pull `homeassistant/home-assistant:stable` container image
6. Configure Docker daemon with log rotation and cgroupfs driver
7. `pct stop 994` — stop the container and Docker daemon
8. `vzdump 994` — export the container filesystem as a zstd-compressed archive
9. `scp` — download the archive to `images/homeassistant-debian-12-amd64.tar.zst`
10. `pct destroy 994 --purge` — clean up

### Docker-in-LXC Requirements

The template includes several critical configurations for Docker inside unprivileged LXC containers:

- **`features: nesting=1`** — Required flag to allow containerized Docker daemon
- **Cgroup delegation** — Docker daemon configured with `native.cgroupdriver=cgroupfs` for cgroup v2 compatibility
- **Unprivileged container** — Run without root privileges for security
- **8GB disk** — Sufficient space for Docker images, containers, and HA data

### Prerequisites

- The base Debian 12 template must exist in `images/`
- SSH access to a Proxmox host (passed via `--host`)
- Internet access from the Proxmox host during build (Docker and HA image downloads)
- No root/sudo required on the controller

### Template path

- Source: `images/homeassistant-debian-12-amd64.tar.zst`
- Variables: `homeassistant_lxc_template` and `homeassistant_lxc_template_path` in `group_vars/all.yml`

## Architecture

```
Clients → OpenWrt Router → Home Assistant LXC (Docker) → Local Network Services
```

- OpenWrt handles routing and DHCP
- Home Assistant runs in Docker inside an LXC container
- Accessible locally and via WireGuard VPN for remote access
- Manages smart devices, automations, and dashboards

## Container Resources

| Resource | Value |
|----------|-------|
| VMID | 200 |
| Cores | 2 |
| RAM | 1024 MB |
| Disk | 8 GB |
| Network | LAN bridge, static IP |
| Features | `nesting=1`, `unprivileged=1` |
| Auto-start | yes, priority 5 |

## Container IP

Static IP on the LAN subnet, computed as:
`<LAN_GATEWAY_prefix>.<homeassistant_ct_ip_offset>` (default offset: 14)

Example: LAN gateway `10.10.10.1` → Home Assistant IP `10.10.10.14/24`

## Env Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `HA_ADMIN_PASSWORD` | no | Admin credentials for HA web UI (auto-generated if empty) |

## USB Device Passthrough (Optional)

Home Assistant supports USB passthrough for Zigbee/Z-Wave dongles via `homeassistant_usb_devices` variable:

- **Empty list** (default): No USB devices passthrough
- **Device list**: Bind mount specified `/dev/ttyUSB*` devices into container

The provisioning role dynamically builds mount entries from this list. When empty, no mount entries are added.

## Docker-in-LXC Exception to Bake Principle

While the project follows "bake, don't configure at runtime," Docker container image pulls represent a **documented exception**:

### Why Pre-pulling HA Container Images?

- **Deterministic versions**: Docker `pull` of a pinned image tag (`homeassistant/home-assistant:stable`) is versioned and deterministic
- **No runtime dependency**: Eliminates internet access requirement during configure role execution
- **Speed**: Avoids ~500MB download during container startup
- **Reliability**: Prevents configure role failures due to network issues or Docker Hub outages

### When to Update HA Images

- **Template rebuild**: Re-run `build-images.sh --only homeassistant` to update pre-pulled image
- **Configure-time pull**: Alternative approach if template rebuild is not desired (still idempotent)

## Backup and Restore Strategy

**Defense in depth approach:**

1. **Home Assistant native snapshots** — HA handles configuration and data backups
2. **Container-level vzdump** — Proxmox handles whole-container backups

Both layers provide redundancy:
- HA snapshots: granular, application-aware backups
- vzdump: complete container state recovery

## Roles

| Role | Purpose |
|------|---------|
| `homeassistant_lxc` | Provision container via `proxmox_lxc` with Docker requirements |
| `homeassistant_configure` | Apply Docker Compose configuration and HA setup |

## Molecule Scenarios

| Scenario | What it tests | Runtime |
|----------|--------------|---------|
| `homeassistant-lxc` | Container provision + configure | ~60-90s |
| `default` | Full integration (includes Home Assistant) | ~4-5 min |

## Test vs Production

- **Test**: `HA_ADMIN_PASSWORD` auto-generated if empty in `test.env`
- **Production**: `HA_ADMIN_PASSWORD` required in `.env`
- **Both**: Use the same custom template from `images/` with pre-pulled HA container

## Configuration Split

### Baked into Image (M0 - Build)
- Docker CE packages and daemon
- docker-compose plugin
- HA container image pre-pulled
- Cgroup delegation configuration
- Log rotation setup

### Runtime Configuration (M2 - Configure)
- Docker Compose file with HA container
- Host-specific network configuration
- Admin credentials setup
- configuration.yaml with HTTP, recorder, logger
- USB device mount entries (if any)

This split ensures:
- **Reliability**: Core Docker stack is tested and consistent
- **Flexibility**: Host-specific settings applied at runtime
- **Performance**: No runtime package installation delays
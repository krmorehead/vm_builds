# vm_builds

Ansible project that automates VM and LXC container provisioning on Proxmox VE.
Deploys 15 services across 6 nodes with baked images, a two-role architecture,
and a NiceGUI Web UI for deployment, fleet monitoring, and kiosk management.

## Production: deploy with the Web UI

```bash
./scripts/webui.sh                  # opens http://localhost:9001
```

That one command starts the full management interface. From there:

1. **Services** — pick a deploy profile or individual service tags
2. **Deploy** — stream live Ansible output, cancel, dry-run
3. **Dashboard** — fleet health, alerts, node status

### Deploy profiles

| Profile | What it deploys | Target hosts |
|---------|----------------|--------------|
| **Home Unit** | Router, DNS, VPN, monitoring, media, desktop, kiosk | home |
| **Mesh Unit** | VPN, mesh WiFi, Moonlight, kiosk | mesh1 |
| **Gamer Unit** | VPN, kiosk, Gaming LXC (Sunshine) | ai |
| **Bridge Units** | WiFi Bridge LXC | bridge-1, bridge-2 |
| **Full Deploy** | All services (except opt-in gaming) | all nodes |
| **Network Only** | Backup, infra, OpenWrt, LAN bootstrap | all nodes |
| **Core Services** | Network + DNS + VPN + monitoring | home, mesh1, ai, mesh2 |
| **Media Stack** | Jellyfin, Kodi, Moonlight | home, mesh1 |
| **Custom** | Manual tag selection | varies |

Select a profile on the **Services** page, then click **Deploy Selected →**.
The **Deploy** page shows live output and supports host limiting and dry runs.

### Web UI pages

| Page | Path | Purpose |
|------|------|---------|
| Dashboard | `/` | Fleet health score, alerts, quick actions |
| Home Hub | `/hub` | Kiosk service launcher with card tiles |
| Bridge | `/bridge` | WiFi bridge link status and topology |
| Mesh | `/mesh` | WDS mesh topology, batman toggle |
| Router | `/router` | OpenWrt WAN/LAN/DHCP/firewall/WiFi |
| Services | `/services` | Tag-based service selection, deploy profiles |
| Deploy | `/deploy` | Live Ansible output, cancel, dry run |
| Nodes | `/nodes` | Fleet node registry, heartbeat status |
| Hosts | `/hosts` | Proxmox host connectivity, SSH probe, WoL |
| Images | `/images` | Image build status, trigger rebuilds |
| Containers | `/containers` | Start/stop/restart LXC and QEMU guests |
| Environment | `/environment` | View/edit .env values, validate |
| Timeline | `/timeline` | Deploy timeline Gantt chart |

### Web UI options

```bash
./scripts/webui.sh                     # localhost:9001, uses .env (or test.env)
./scripts/webui.sh --env test.env      # test machine config
./scripts/webui.sh --port 8080         # custom port
./scripts/webui.sh --host 0.0.0.0      # bind to all interfaces (remote access)
```

---

## First-time setup

### 1. Clone and bootstrap

```bash
git clone <repo-url> && cd vm_builds
./setup.sh                           # creates .venv, installs everything
```

`setup.sh` will prompt for your sudo password once to:
- Install `wireguard-tools` (needed for the VPN tunnel to your fleet)
- Create `/etc/sudoers.d/vm-builds-wireguard` (passwordless sudo for `wg-quick`, `wg`, `install` only)
- Create `/etc/wireguard/` directory

This is a one-time setup. After this, `molecule converge` and `site.yml`
automatically configure the VPN tunnel on this machine without prompting.

### 2. SSH keys for Proxmox

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
ssh-copy-id root@<proxmox-ip>
```

### 3. Create Proxmox API tokens

On each Proxmox node: **Datacenter → Permissions → API Tokens** — User `root@pam`,
Token ID `ansible`, uncheck Privilege Separation. Copy the secret.

### 4. Configure environment

```bash
cp test.env .env      # edit with your Proxmox IPs + API tokens
```

| Variable | Required | Description |
|----------|----------|-------------|
| `PRIMARY_HOST` | Yes | IP of the primary Proxmox node |
| `HOME_API_TOKEN` | Yes | API token for the primary node |
| `MESH_KEY` | Yes | WPA3-SAE passphrase for WiFi mesh |
| `AI_HOST` | No | IP of GPU node |
| `AI_API_TOKEN` | No | API token for ai node |
| `MESH_2_HOST` | No | IP of mesh2 node |
| `MESH2_API_TOKEN` | No | API token for mesh2 |
| `MESH1_API_TOKEN` | No | API token for mesh1 (LAN satellite) |
| `WAN_MAC` | No | Clone old router MAC for ISP compatibility |
| `PIHOLE_WEB_PASSWORD` | No | Pi-hole admin password |
| `HA_ADMIN_PASSWORD` | No | Home Assistant admin password |
| `SUNSHINE_USER` / `SUNSHINE_PASSWORD` | No | Gaming LXC Sunshine credentials |
| `DESKTOP_USER` | No | Desktop LXC username |

### 5. Build images

All services use pre-built images. Build once (~15 min):

```bash
./scripts/build-images.sh                    # all images
./scripts/build-images.sh --only pihole      # single image (~2 min)
```

Targets: `router`, `mesh`, `pihole`, `rsyslog`, `netdata`, `wireguard`,
`homeassistant`, `jellyfin`, `kodi`, `moonlight`, `gaming`, `sunshine`,
`desktop`, `kiosk`.

### 6. Deploy

Start the Web UI and pick a profile (see above), or use CLI:

```bash
./run.sh                             # full deploy
./run.sh --tags openwrt,pihole       # specific services
./run.sh --check                     # dry run
```

---

## Architecture

```
 Controller               Proxmox Nodes (6)           Services
 ┌──────────┐            ┌──────────────────┐
 │  Ansible  │──API+SSH──▶│  home (primary)   │──▶ OpenWrt, WireGuard, Pi-hole,
 │  build.py │            │                  │    rsyslog, Netdata, HA, Jellyfin,
 │  Web UI   │            │                  │    Kodi, Desktop, Kiosk
 └──────────┘            ├──────────────────┤
                          │  mesh1 (LAN sat) │──▶ WireGuard, Mesh WiFi, Moonlight, Kiosk
                          ├──────────────────┤
                          │  ai              │──▶ WireGuard, Gaming LXC, Kiosk
                          ├──────────────────┤
                          │  mesh2           │──▶ WireGuard, Mesh WiFi, Kiosk
                          ├──────────────────┤
                          │  bridge-1/2      │──▶ WiFi Bridge LXC
                          └──────────────────┘
```

### Design principles

- **Bake, don't configure at runtime** — images ship with all packages.
  Ansible only applies host-specific config (IPs, keys, topology).
- **Two-role pattern** — `<type>_lxc` (provision) + `<type>_configure` (config).
- **One path, no fallbacks** — missing prerequisites fail with clear messages.
- **Hard-fail over graceful degradation** — expected hardware must be present.
- **Manager-first for runtime** — UI → Manager API → container-side scripts.

### Execution order (site.yml)

```
Phase 1: Primary hosts (directly reachable)
  Backup → Infrastructure → OpenWrt VM → OpenWrt configure

Phase 2: LAN satellites (reachable after OpenWrt creates the LAN)
  Bootstrap LAN hosts → Backup + Infrastructure on LAN

Phase 3: Services (span both primary + LAN hosts)
  Pi-hole → rsyslog → Netdata → Home Assistant → Jellyfin →
  Kodi → Moonlight → WireGuard → Mesh WiFi → WiFi Bridge →
  Desktop LXC → Kiosk → Gaming LXC

Phase 3d: Fleet heartbeat circuit breaker
  Hard-fail if any healthy container stopped heartbeating
```

### Fleet monitoring (callhome)

Containers heartbeat to a central API (`/api/checkin`), replacing SSH polling.
The API starts automatically during production (`build.py`) and test (`molecule prepare`).

- Composable extensions: network, wireguard, docker, config_files, wifi
- Fleet readiness gate: `/api/fleet/ready?services=...`
- Circuit breaker: `/api/fleet/stale` detects container deaths mid-run

### VMID allocation

| Range | Category | Services |
|-------|----------|----------|
| 100-199 | Network | OpenWrt (100), WireGuard (101), Pi-hole (102), Mesh WiFi (103), WiFi Bridge (104) |
| 200-299 | Services | Home Assistant (200) |
| 300-399 | Media | Jellyfin (300), Kodi (301), Moonlight (302) |
| 400-499 | Desktop | Desktop LXC (400), Kiosk (401) |
| 500-599 | Observability | Netdata (500), rsyslog (501) |
| 600-699 | Gaming | Gaming LXC (601) |

---

## Testing

```bash
source .venv/bin/activate

# Lint
ansible-lint && yamllint .

# Python tests (build.py + Web UI)
pytest tests/ -v

# Molecule integration (against test.env machine)
set -a && source test.env && set +a
molecule test -s pihole-lxc          # per-service
molecule test                        # full E2E (6 nodes)
molecule converge && molecule verify # day-to-day iteration
```

---

## Cleanup & restore

```bash
./cleanup.sh restore            # host config only
./cleanup.sh full-restore       # destroy VMs + restore + host config
./cleanup.sh clean              # destroy project VMs, restore host config
./cleanup.sh clean test.env     # specify env file
```

---

## Project structure

```
vm_builds/
├── build.py                     # Single entry point for Ansible runs
├── setup.sh                     # Bootstrap .venv + dependencies
├── run.sh / cleanup.sh          # Convenience wrappers → build.py
├── scripts/
│   ├── webui.sh                 # Launch the Web UI
│   ├── webui/                   # NiceGUI app (app.py, kiosk_server.py, pages/)
│   ├── build-images.sh          # Build all service images
│   ├── callhome.py              # Container heartbeat agent (Python)
│   ├── callhome.sh              # Container heartbeat agent (BusyBox/OpenWrt)
│   └── image-builder/           # Image build scripts and container files
├── inventory/
│   ├── hosts.yml                # Host inventory + flavor groups
│   ├── group_vars/all.yml       # VMIDs, image paths, shared config
│   ├── group_vars/proxmox.yml   # API auth, SSH settings
│   └── host_vars/               # Per-host overrides
├── playbooks/
│   ├── site.yml                 # Main orchestration
│   └── cleanup.yml              # Tag-driven restore
├── roles/                       # ~30 roles (see table below)
├── tasks/                       # Shared task files (group reconstruction, etc.)
├── molecule/                    # 24 test scenarios
├── tests/                       # pytest suite
├── docs/architecture/           # Design docs (19 files)
├── images/                      # Built images (gitignored)
├── .state/                      # Runtime state (gitignored)
├── test.env                     # Test machine config (committed)
└── .env                         # Production secrets (gitignored)
```

### Roles

**Shared infrastructure** (run once per host):
`proxmox_backup`, `proxmox_bridges`, `proxmox_pci_passthrough`, `proxmox_igpu`,
`proxmox_lxc`, `deploy_stamp`

**Service roles** (two-role pattern):

| Service | Provision | Configure |
|---------|-----------|-----------|
| OpenWrt Router | `openwrt_vm` | `openwrt_configure` |
| WireGuard VPN | `wireguard_lxc` | `wireguard_configure` |
| Pi-hole DNS | `pihole_lxc` | `pihole_configure` |
| rsyslog | `rsyslog_lxc` | `rsyslog_configure` |
| Netdata | `netdata_lxc` | `netdata_configure` |
| Home Assistant | `homeassistant_lxc` | `homeassistant_configure` |
| Jellyfin | `jellyfin_lxc` | `jellyfin_configure` |
| Kodi | `kodi_lxc` | `kodi_configure` |
| Moonlight | `moonlight_lxc` | `moonlight_configure` |
| Desktop | `desktop_lxc` | `desktop_configure` |
| Kiosk | `kiosk_lxc` | `kiosk_configure` |
| Gaming LXC | `gaming_lxc` | `gaming_lxc_configure` |
| Mesh WiFi | `openwrt_mesh_lxc` | `openwrt_mesh_configure` |
| WiFi Bridge | `openwrt_bridge_lxc` | `openwrt_bridge_configure` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `proxmoxer` import error | `source .venv/bin/activate` |
| `sshpass` not found | `sudo apt install sshpass` |
| SSH timeout on bootstrap | Check `openwrt_bootstrap_bridge` |
| No internet after router swap | Set `WAN_MAC=<old-mac>` in `.env` |
| VM gone after reboot | Re-run playbook (sets `onboot` unconditionally) |
| Host unreachable | **STOP.** Investigate immediately |
| `apt-get` hangs | Playbook syncs NTP + disables enterprise repos |

## Documentation

Architecture docs: `docs/architecture/` — [overview.md](docs/architecture/overview.md),
[roles.md](docs/architecture/roles.md), [roadmap.md](docs/architecture/roadmap.md),
[baseline.md](docs/architecture/baseline.md), plus per-service build docs.

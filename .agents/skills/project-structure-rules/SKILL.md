---
name: project-structure-rules
description: Project architecture and design principles for vm_builds Ansible project. Includes bake vs configure patterns, two-role service model, and deployment lifecycle.
---

# Project Structure and Architecture

Use when designing new services, understanding project architecture, or implementing VM/container provisioning patterns for the vm_builds project.

## Rules

1. NEVER install packages during configure roles - bake them into images instead
2. NEVER add "fallback" logic - fail with clear messages when prerequisites are missing
3. NEVER add "legacy image fallback" code in configure roles - if the image lacks baked content, REBUILD THE IMAGE
4. ALWAYS follow community standards before writing custom automation
5. ALWAYS use two-role pattern: `<type>_vm/lxc` + `<type>_configure` for each service
6. ALWAYS include `deploy_stamp` as last role in provision plays
7. NEVER hardcode VMIDs - use allocation ranges by service type
8. NEVER reference another role's defaults/main.yml directly
9. ALWAYS use `env_generated_path` for auto-generated secrets and dynamic config
10. ALWAYS follow the 6-step standard work cycle for image/role changes (see below)
11. Proxmox hosts are bakeable targets — treat them like any other machine you control. Host-level packages (socat), systemd units, iptables rules, and kernel parameters are all infrastructure that gets deployed and tested, not hand-configured

## Standard Work Cycle

Every change that touches `build-images.sh` or a configure role follows
this exact 6-step cycle:

1. **Update build-images.sh** — bake packages, static config, systemd units
2. **Build images IN PARALLEL on test units** — `--force --parallel` across 6 hosts (REQUIRED)
3. **Write tests and playbook updates** (while images build)
4. **Run E2E molecule test** (after images ready)
5. **Code review** (while E2E runs) — DRY, ARCH, KISS, OOP, test quality
6. **Manual playbook verification** (after E2E passes) — every play, every heartbeat

No rollback strategy needed — old image versions saved, just rebuild.

## Patterns

Image-first pattern:

```bash
# Build all packages into image during build-images.sh
# Build IN PARALLEL across hosts
./scripts/build-images.sh --host <ip> --only <target>

# Configure role only applies host-specific config
# roles/<type>_configure/tasks/main.yml
# NO opkg install, apt install, or pip install commands
# NO "if baked file missing, deploy at runtime" fallbacks
```

Two-role service pattern:

```yaml
# Provision role creates VM/container
- hosts: flavor_group
  roles:
    - <type>_vm  # or <type>_lxc
    - deploy_stamp

# Configure role applies topology-specific config  
- hosts: dynamic_group
  roles:
    - <type>_configure
```

VMID allocation (all defined in `group_vars/all.yml`):

```yaml
openwrt_vm_id: 100          # Network
wireguard_ct_id: 101
pihole_ct_id: 102
openwrt_mesh_ct_id: 103
openwrt_bridge_ct_id: 104

homeassistant_ct_id: 200    # Services
jellyfin_ct_id: 300         # Media
kodi_ct_id: 301
moonlight_ct_id: 302
desktop_vm_id: 400          # Desktop
kiosk_ct_id: 401
netdata_ct_id: 500          # Observability
rsyslog_ct_id: 501
gaming_ct_id: 601           # Gaming (opt-in)
```

## 4-Tier Management Architecture

The system uses a 4-tier hierarchy to manage clusters of Proxmox nodes:

```
Tier 1: SuperManager (app.py)
  │   Global fleet view, nodes.json persistent storage
  │   Receives relay heartbeats from ALL Cluster Managers
  │
Tier 2: ClusterManager (kiosk_server.py, IS_CLUSTER_MANAGER=true)
  │   Subnet-scoped fleet view (one household's network)
  │   Broadcasts events DOWN, relays UP to SuperManager
  │   Fleet-level ops: batman_fleet(), get_mesh_nodes(), bridge/wifi
  │
Tier 3: NodeManager (kiosk_server.py, default)
  │   Per-host container ops ONLY, relays heartbeats UP
  │   NEVER iterates other hosts, NEVER calls get_mesh_nodes()
  │
Tier 4: Container-side scripts (/usr/sbin/)
      batman_trigger.sh, wifi_setup.sh, callhome.py
      KEY=value output, called by Ansible AND Manager
```

### What is a cluster?

A **cluster** = a single household's network. The router node creates a
LAN subnet (10.10.10.x) via OpenWrt. All nodes (wired + WiFi mesh) converge
onto this flat subnet. The router node's kiosk is the Cluster Manager.
Remote/national hosts are single-node clusters (their own Cluster Manager).

### OOP class hierarchy (manager.py)

```
BaseManager → NodeManager → ClusterManager
                              (SuperManager = app.py using ClusterManager)
```

### Communication paths

- Container → NodeManager: `POST /api/checkin` (callhome agent, localhost)
- NodeManager → ClusterManager: `POST /api/checkin` (heartbeat relay via MANAGEMENT_SERVER)
- ClusterManager → SuperManager: `POST /api/checkin` (cluster relay via MANAGEMENT_SERVER)
- ClusterManager → NodeManager: `POST /api/manager/events` (event broadcast via CHILD_MANAGER_IPS)

### WAN host reachability (DNAT + socat)

WAN host kiosk containers live on private NAT subnets (10.99.x.x) that
are unreachable from the LAN. The `kiosk_lxc` role deploys host-level
infrastructure on each Proxmox host to expose port 9001:

- **WAN hosts (non-router)**: iptables DNAT on the WAN bridge forwarding
  port 9001 to the kiosk container IP. Works because container and host
  share the same 10.99.x.x NAT bridge — no hairpin issue.
- **Router node**: systemd `manager-api-proxy.service` running socat
  (WAN:9001 → kiosk LAN IP:9001). DNAT fails here because the kiosk is
  on the LAN bridge (vmbr1) while WAN traffic arrives on vmbr0 — hairpin
  NAT breaks conntrack. Socat operates in userspace, avoiding this.
- **LAN hosts**: no forwarding needed — direct LAN access.

The SuperManager relay (`supermanager-relay.service`) on the router node
forwards port 52525 through an SSH reverse tunnel to the controller
where the SM API runs. Both relay units use `Restart=always` so they
survive reboots and SSH session teardown.

`CHILD_MANAGER_IPS` uses container IPs for LAN hosts (directly reachable)
and host IPs for WAN hosts (via DNAT/socat). See `manager-api-pattern` skill.

Previous bug (2026-04-12): socat on port 9001 was started with `nohup &`
instead of a systemd unit. It died when ansible's SSH ControlMaster session
closed, breaking the heartbeat relay chain. WAN NodeManagers could not
reach the Cluster Manager, causing 0 of 6 hosts on the SuperManager.
Fix: deploy as `manager-api-proxy.service` with `Restart=always`.

## Anti-patterns

NEVER explain what Ansible is in project structure rules
NEVER use proxmox_lxc_default_template - create service-specific template vars
NEVER split provisioning and configuration into separate milestones
NEVER add graceful degradation for expected hardware (iGPU, WiFi, IOMMU)
NEVER put fleet-level operations on NodeManager — ClusterManager only
NEVER patch running containers — update build scripts, rebuild images, redeploy
NEVER use raw `ansible-playbook --tags openwrt` without `infra` — bridges undefined
NEVER treat Proxmox hosts as special snowflakes — they are machines you control, same as containers. Host-level systemd units, iptables rules, and packages are deployable infrastructure
NEVER use `nohup ... &` for persistent host-level services — deploy systemd units with Restart=always. Background processes die when SSH sessions close

---
name: project-structure-rules
description: Project architecture and design principles for vm_builds Ansible project. Includes bake vs configure patterns, two-role service model, and deployment lifecycle.
---

# Project Structure and Architecture

Use when designing new services, understanding project architecture, or implementing VM/container provisioning patterns for the vm_builds project.

## Rules

1. NEVER install packages during configure roles - bake them into images instead
2. NEVER add "fallback" logic - fail with clear messages when prerequisites are missing
3. ALWAYS follow community standards before writing custom automation
4. ALWAYS use two-role pattern: `<type>_vm/lxc` + `<type>_configure` for each service
5. ALWAYS include `deploy_stamp` as last role in provision plays
6. NEVER hardcode VMIDs - use allocation ranges by service type
7. NEVER reference another role's defaults/main.yml directly
8. ALWAYS use `env_generated_path` for auto-generated secrets and dynamic config

## Patterns

Image-first pattern:

```bash
# Build all packages into image during build-images.sh
./build-images.sh --only <target>

# Configure role only applies host-specific config
# roles/<type>_configure/tasks/main.yml
# NO opkg install, apt install, or pip install commands
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

### WAN host reachability (DNAT)

WAN host kiosk containers live on private NAT subnets (10.99.x.x) that
are unreachable from the LAN. The `kiosk_lxc` role deploys iptables DNAT
rules on WAN hosts forwarding port 9001 from the host's WAN bridge to
the container. This lets the Cluster Manager reach child Managers on WAN
hosts via the Proxmox host IP (`ansible_host`).

`CHILD_MANAGER_IPS` uses container IPs for LAN hosts (directly reachable)
and host IPs for WAN hosts (via DNAT). See `manager-api-pattern` skill.

## Anti-patterns

NEVER explain what Ansible is in project structure rules
NEVER use proxmox_lxc_default_template - create service-specific template vars
NEVER split provisioning and configuration into separate milestones
NEVER add graceful degradation for expected hardware (iGPU, WiFi, IOMMU)
NEVER put fleet-level operations on NodeManager — ClusterManager only
NEVER patch running containers — update build scripts, rebuild images, redeploy
NEVER use raw `ansible-playbook --tags openwrt` without `infra` — bridges undefined
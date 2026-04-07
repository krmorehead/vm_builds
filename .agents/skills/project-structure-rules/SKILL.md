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

## Anti-patterns

NEVER explain what Ansible is in project structure rules
NEVER use proxmox_lxc_default_template - create service-specific template vars
NEVER split provisioning and configuration into separate milestones
NEVER add graceful degradation for expected hardware (iGPU, WiFi, IOMMU)
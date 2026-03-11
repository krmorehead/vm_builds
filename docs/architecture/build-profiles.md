# Build Profiles

## Overview

A build profile is the set of services a Proxmox host receives, determined
by its membership in **flavor groups** in `inventory/hosts.yml`. Shared
infrastructure (backup, bridges, PCI passthrough, iGPU detection) runs on
every host in the `proxmox` group regardless of flavor membership. VM and
container provisioning plays target specific flavor groups.

## Flavor Groups

| Group | Services provisioned | VMID range |
|-------|---------------------|------------|
| `router_nodes` | OpenWrt router VM | 100 |
| `vpn_nodes` | WireGuard VPN | 101 |
| `dns_nodes` | Pi-hole DNS | 102 |
| `wifi_nodes` | Mesh WiFi controller | 103 |
| `service_nodes` | Home Assistant | 200 |
| `media_nodes` | Jellyfin, Kodi, Moonlight | 300-302 |
| `desktop_nodes` | Desktop VM, Custom UX Kiosk | 400-401 |
| `monitoring_nodes` | Netdata, rsyslog | 500-501 |
| `gaming_nodes` | Gaming VM | 600 |

## How It Works

Hosts are assigned to flavor groups in `inventory/hosts.yml`:

```yaml
proxmox:
  children:
    router_nodes:
      hosts:
        home: {}       # This host gets an OpenWrt router
    media_nodes:
      hosts:
        home: {}       # Same host also gets Jellyfin, Kodi, Moonlight
    gaming_nodes:
      hosts: {}        # No hosts assigned yet
```

Each provision play in `site.yml` targets a flavor group:

```yaml
- name: Provision OpenWrt VM
  hosts: router_nodes
  roles:
    - openwrt_vm

- name: Provision Jellyfin
  hosts: media_nodes
  roles:
    - jellyfin_lxc
```

If a host isn't in the group, the play is skipped entirely for that host.

## Dynamic Groups

Provision roles register VMs and containers in **dynamic groups** via
`add_host`. Configure plays target these groups:

```yaml
- name: Configure Jellyfin
  hosts: jellyfin
  roles:
    - jellyfin_configure
```

Dynamic groups are defined as empty in the static inventory to avoid
Ansible warnings. They are populated at runtime only when the provision
play runs.

## Example Profiles

### Home Entertainment Box

A mini-PC that serves as router, media server, and display device.

```
Flavor groups: router_nodes, vpn_nodes, dns_nodes, wifi_nodes,
               monitoring_nodes, service_nodes, media_nodes,
               desktop_nodes
```

### Dedicated Gaming Rig

A separate machine exclusively for GPU-passthrough gaming.

```
Flavor groups: gaming_nodes
```

### Network Appliance

A low-power device that only handles routing and DNS.

```
Flavor groups: router_nodes, vpn_nodes, dns_nodes
```

## Adding a New Profile

1. Define the flavor group in `inventory/hosts.yml` under `proxmox.children`.
2. Assign hosts to the group.
3. Add the group to `molecule/default/molecule.yml` platform groups.
4. Create the provision play in `site.yml` targeting the new group.

No code changes are needed to create a new profile -- profiles are purely
a composition of existing flavor groups in the inventory.

## Auto-Start and Boot Order

Each service has a startup priority in `group_vars/all.yml`:

| Priority | Services |
|----------|----------|
| 1 | OpenWrt Router, Gaming VM |
| 2 | WireGuard VPN |
| 3 | Pi-hole, Netdata, rsyslog |
| 4 | Mesh WiFi Controller |
| 5 | Home Assistant, Jellyfin |
| 6 | Custom UX Kiosk |

On-demand services (Kodi, Moonlight, Desktop VM) have `onboot: false`
and are started manually or by the display-exclusive hookscript.

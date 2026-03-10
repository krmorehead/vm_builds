---
name: vm-lifecycle
description: Patterns and step-by-step guide for adding new VM types to the vm_builds project. Use when creating new VM roles, extending the playbook for additional VMs, modifying site.yml, or designing shared infrastructure for multi-VM Proxmox hosts.
---

# VM Lifecycle Patterns

## Context

This project manages multiple VM types on Proxmox. Each VM follows a two-role pattern. Shared infrastructure (bridges, backups, PCI passthrough) runs once per host. This skill prevents architectural drift as new VM types are added.

## Rules

1. Every VM type gets exactly two roles: `<type>_vm` (provision) and `<type>_configure` (setup). NEVER mix provisioning and configuration in one role.
2. Shared roles (`proxmox_bridges`, `proxmox_backup`, `proxmox_pci_passthrough`) run ONCE per host. They export facts consumed by all VM roles.
3. VM-specific variables live in role `defaults/main.yml`. Shared variables live in `group_vars/all.yml`. NEVER put VM-specific defaults in group_vars.
4. Each `<type>_vm` role MUST check for existing VM before creating (`qm status <vmid>`). Guard ALL creation tasks with `when: not vm_exists | bool`.
5. VMIDs: 100-series for network VMs, 200-series for service VMs. Define in `group_vars/all.yml`.
6. The `<type>_vm` role adds the VM to dynamic inventory via `add_host`. The `<type>_configure` role runs in a separate play targeting that group.
7. NEVER hardcode bridge names or roles (e.g., `vmbr0 = WAN`). The WAN bridge is detected at runtime via `proxmox_wan_bridge` (set by `proxmox_bridges` from the host's default route). Order bridges dynamically: WAN first, then LAN sorted.
8. NEVER reference another role's defaults. Use `set_fact` with `cacheable: true` or `add_host` variables to pass data.
9. Every provision play targeting Proxmox hosts MUST include `deploy_stamp` as its last role to record the deployment in `/etc/ansible/facts.d/vm_builds.fact`.
10. Every new role MUST have `meta/main.yml` with `author`, `license: proprietary`, `role_name`, `description`, `min_ansible_version`, and `platforms`.
11. Provision plays target **flavor groups** (e.g., `router_nodes`, `service_nodes`), NOT `proxmox` directly. Shared infra targets `proxmox`.
12. NEVER use `local_action`. ALWAYS use `delegate_to: localhost` instead.
13. When a role deploys files to the host, ALWAYS add them to both cleanup playbooks (`molecule/default/cleanup.yml` and `playbooks/cleanup.yml`).

## Playbook execution order (site.yml)

```
Play 0: proxmox_backup             (targets: proxmox, tag: backup)
         deploy_stamp: backup
Play 1: proxmox_bridges            (targets: proxmox — shared infra)
         proxmox_pci_passthrough
         deploy_stamp: infrastructure
Play 2: openwrt_vm                 (targets: router_nodes — VM provision)
         deploy_stamp: openwrt_vm
Play 3: openwrt_configure          (targets: openwrt — dynamic group)
Play 4: Bootstrap cleanup          (targets: proxmox — remove temp IPs)
```

## Device flavors (inventory groups)

Hosts belong to child groups under `proxmox` that control which VMs they receive:

```yaml
proxmox:
  children:
    router_nodes:     # hosts that get OpenWrt
      hosts:
        home: {}
    service_nodes:    # hosts that get service VMs (future)
      hosts: {}
```

A host can belong to multiple flavor groups. Shared infra targets `proxmox` (runs on all). VM-specific plays target the flavor group.

## Step-by-step: adding a new VM type

Using `homeassistant` as the example:

### 1. Create the provision role

```
roles/homeassistant_vm/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
```

`meta/main.yml`:
```yaml
---
dependencies: []

galaxy_info:
  author: Kyle
  license: proprietary
  role_name: homeassistant_vm
  description: Provision a Home Assistant VM on Proxmox
  min_ansible_version: "2.15"
  platforms:
    - name: Debian
      versions:
        - bullseye
        - bookworm
```

`defaults/main.yml`:
```yaml
---
homeassistant_tmp_image: /tmp/haos.qcow2
homeassistant_bootstrap_bridge: ""
```

`tasks/main.yml` must follow this skeleton:
```yaml
---
- name: Check if HomeAssistant VM already exists
  ansible.builtin.command:
    cmd: qm status {{ homeassistant_vm_id }}
  register: vm_status
  failed_when: false
  changed_when: false

- name: Set VM existence flag
  ansible.builtin.set_fact:
    vm_exists: "{{ vm_status.rc == 0 }}"

# Upload image, create VM, import disk, attach NICs, start VM
# All creation tasks guarded with: when: not vm_exists | bool

# Bootstrap SSH (if needed) and add to dynamic inventory:
- name: Add HomeAssistant VM to dynamic inventory
  ansible.builtin.add_host:
    name: "{{ homeassistant_vm_name }}"
    groups: homeassistant
    ansible_host: "<bootstrap_ip>"
    # ... SSH args ...
```

### 2. Create the configure role

```
roles/homeassistant_configure/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
```

### 3. Add VMID to group_vars

`inventory/group_vars/all.yml`:
```yaml
homeassistant_vm_id: 200
homeassistant_vm_name: homeassistant
homeassistant_vm_memory: 2048
homeassistant_vm_cores: 2
homeassistant_vm_disk_size: 32G
homeassistant_image_path: images/haos.qcow2
```

### 4. Add dynamic group and flavor group to inventory

`inventory/hosts.yml`:
```yaml
all:
  children:
    proxmox:
      children:
        router_nodes:
          hosts:
            home: {}
        service_nodes:        # new flavor group
          hosts:
            home: {}
    openwrt:
      hosts: {}
    homeassistant:            # dynamic group, populated by add_host
      hosts: {}
```

### 5. Extend site.yml

```yaml
# New provision play (targets flavor group, includes deploy_stamp):
- name: Provision HomeAssistant VM
  hosts: service_nodes
  gather_facts: false
  roles:
    - homeassistant_vm
    - role: deploy_stamp
      vars:
        deploy_stamp_play: homeassistant_vm

# New configure play (targets dynamic group):
- name: Configure HomeAssistant
  hosts: homeassistant
  gather_facts: false
  roles:
    - homeassistant_configure
```

### 6. Update Molecule

Add the flavor group to `molecule/default/molecule.yml`:
```yaml
platforms:
  - name: home
    groups:
      - proxmox
      - router_nodes
      - service_nodes    # add new flavor group
```

Add assertions to `molecule/default/verify.yml`.

### 7. Documentation and versioning

1. Create `docs/architecture/homeassistant-build.md`
2. Add entry to `CHANGELOG.md` under `[Unreleased]`
3. Bump `project_version` in `group_vars/all.yml` when releasing

## WAN bridge detection and NIC ordering

`proxmox_bridges` detects the WAN bridge by checking which bridge carries the host's default route. This fact (`proxmox_wan_bridge`) is consumed by VM roles.

`openwrt_vm` orders bridges so the WAN bridge maps to `net0`/`eth0`:

```yaml
_ordered_bridges: [_wan_bridge] + (proxmox_all_bridges | difference([_wan_bridge]) | sort)
```

Override with `openwrt_wan_bridge` in `host_vars` if auto-detection picks wrong.

Previous bug: alphabetical bridge sorting made `vmbr0` always WAN. When the modem was on `vmbr0`, the Proxmox GUI became unreachable from LAN nodes.

## Proxmox LAN management IP

When a VM becomes the primary router, the Proxmox host needs a static IP on the LAN bridge so the GUI is reachable from LAN clients. Pattern:

1. Compute LAN IP from the router's LAN subnet + offset (default `.2`)
2. Add IP to LAN bridge via `ip addr add` (immediate)
3. Deploy persistent config to `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (survives reboot)
4. Write `.state/addresses.json` locally with both the original management IP and the new LAN IP
5. Probe original management IP — if unreachable (topology changed), update `ansible_host` via `add_host` so subsequent plays can connect

## State file for cross-run IP discovery

`build.py` probes `PROXMOX_HOST` before running Ansible. If unreachable, it reads `.state/addresses.json` for cached alternative IPs. This handles cable-swap scenarios where the original management IP is no longer routable.

The state file is written by `openwrt_configure` and cleaned by both cleanup playbooks. It is gitignored.

## Bridge allocation

```yaml
# OpenWrt: ALL bridges (WAN + all LAN ports)
proxmox_all_bridges → [vmbr0, vmbr1, vmbr2]

# Service VM: typically only needs ONE LAN bridge
_service_bridge: "{{ proxmox_all_bridges[1] | default(proxmox_all_bridges[0]) }}"
```

OpenWrt is special — it consumes all bridges because it IS the router. Most other VMs need a single bridge for LAN connectivity, typically the first LAN bridge (index 1, since index 0 is WAN).

## VMs that need internet during configure

If the configure role needs to download packages (like OpenWrt does):
1. The VM must have a NIC on a bridge with upstream connectivity.
2. Use the two-phase restart pattern if you also need to change the VM's IP.
3. For VMs behind the OpenWrt router, WAN access comes through the LAN bridge — no special handling needed.

## Deployment tracking

The `deploy_stamp` role writes `/etc/ansible/facts.d/vm_builds.fact` on Proxmox hosts after each play. On subsequent runs with `gather_facts: true`, the data is available as `ansible_local.vm_builds`:

```json
{
  "project_version": "1.0.0",
  "last_run": "2026-03-09T20:00:00Z",
  "plays": {
    "backup": { "version": "1.0.0", "timestamp": "..." },
    "infrastructure": { "version": "1.0.0", "timestamp": "..." },
    "openwrt_vm": { "version": "1.0.0", "timestamp": "..." },
    "homeassistant_vm": { "version": "1.1.0", "timestamp": "..." }
  }
}
```

Each play appends its entry without overwriting others. Query a host:
`ansible -m setup -a 'filter=ansible_local' <hostname>`

## Test strategy

- Molecule converge provisions ALL VMs in sequence (site.yml runs everything).
- Molecule verify checks each VM type with independent assertions.
- Cleanup destroys ALL VMs via `qm list` iteration — not hardcoded VMIDs.
- For VM-specific test scenarios, add separate Molecule scenarios: `molecule/homeassistant/`.

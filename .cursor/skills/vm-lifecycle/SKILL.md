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
7. NEVER hardcode bridge names. Consume `proxmox_all_bridges` and select by index.
8. NEVER reference another role's defaults. Use `set_fact` with `cacheable: true` or `add_host` variables to pass data.

## Step-by-step: adding a new VM type

Using `homeassistant` as the example:

### 1. Create the provision role

```
roles/homeassistant_vm/
├── defaults/main.yml
└── tasks/main.yml
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

### 4. Add dynamic group to inventory

`inventory/hosts.yml`:
```yaml
all:
  children:
    proxmox:
      hosts:
        home: {}
    openwrt:
      hosts: {}
    homeassistant:    # <-- new
      hosts: {}
```

### 5. Extend site.yml

```yaml
# After existing openwrt_vm in Play 1:
- name: Provision HomeAssistant VM
  hosts: proxmox
  gather_facts: false
  roles:
    - homeassistant_vm

# New play after openwrt_configure:
- name: Configure HomeAssistant
  hosts: homeassistant
  gather_facts: false
  roles:
    - homeassistant_configure
```

### 6. Extend molecule verify and cleanup

`molecule/default/verify.yml` — add assertions for the new VM (check running, SSH, services).

`molecule/default/cleanup.yml` — already iterates `qm list` to destroy all VMs; no changes needed unless VM-specific cleanup is required.

### 7. Add architecture doc

Create `docs/architecture/homeassistant-build.md` following the pattern of `openwrt-build.md`.

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

## Test strategy

- Molecule converge provisions ALL VMs in sequence (site.yml runs everything).
- Molecule verify checks each VM type with independent assertions.
- Cleanup destroys ALL VMs via `qm list` iteration — not hardcoded VMIDs.
- For VM-specific test scenarios, add separate Molecule scenarios: `molecule/homeassistant/`.

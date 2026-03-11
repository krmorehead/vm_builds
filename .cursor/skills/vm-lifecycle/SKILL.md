---
name: vm-lifecycle
description: General patterns for adding new VM types to the vm_builds project. Use when creating new VM roles, extending site.yml, modifying shared infrastructure, or designing inventory groups. For OpenWrt-specific patterns, see openwrt-build skill instead.
---

# VM Lifecycle Patterns

## Context

This project manages multiple VM types on Proxmox. Each VM follows a two-role pattern. Shared infrastructure runs once per host. This skill covers the GENERAL patterns that apply to ALL VM types. VM-specific patterns (OpenWrt networking, Home Assistant setup, etc.) belong in their own skills.

## Rules

1. Every VM type gets exactly two roles: `<type>_vm` (provision) and `<type>_configure` (setup). NEVER mix provisioning and configuration in one role.
2. Shared roles (`proxmox_bridges`, `proxmox_backup`, `proxmox_pci_passthrough`) run ONCE per host. They export facts consumed by all VM roles.
3. VM-specific variables live in role `defaults/main.yml`. Shared variables live in `group_vars/all.yml`. NEVER put VM-specific defaults in group_vars.
4. Each `<type>_vm` role MUST check for existing VM before creating (`qm status <vmid>`). Guard ALL creation tasks with `when: not vm_exists | bool`.
5. VMIDs: 100-series for network VMs, 200-series for service VMs. Define in `group_vars/all.yml`.
6. The `<type>_vm` role adds the VM to dynamic inventory via `add_host`. The `<type>_configure` role runs in a separate play targeting that group.
7. NEVER reference another role's defaults. Use `set_fact` with `cacheable: true` or `add_host` variables to pass data.
8. Every provision play targeting Proxmox hosts MUST include `deploy_stamp` as its last role.
9. Every new role MUST have `meta/main.yml` with `author`, `license: proprietary`, `role_name`, `description`, `min_ansible_version`, and `platforms`.
10. Provision plays target **flavor groups** (e.g., `router_nodes`, `service_nodes`), NOT `proxmox` directly. Shared infra targets `proxmox`.
11. Every VM MUST configure `--onboot 1 --startup order=N` via `qm set`. This task runs unconditionally to self-heal. Define `<type>_vm_startup_order` in role defaults.
12. When a role deploys files to the host, ALWAYS add them to both cleanup playbooks.
13. Optional env variables go in role `defaults/main.yml` via `lookup('env', ...) | default('', true)`. NEVER add optional vars to `REQUIRED_ENV` in `build.py`.
14. Every configured feature MUST have a corresponding assertion in `verify.yml`. "VM is running" is NOT sufficient — verify services, network topology, auto-start, and state files.

## Playbook execution order (site.yml)

```
Play 0: proxmox_backup             (targets: proxmox, tag: backup)
         deploy_stamp: backup
Play 1: proxmox_bridges            (targets: proxmox — shared infra)
         proxmox_pci_passthrough
         deploy_stamp: infrastructure
Play 2: <type>_vm                  (targets: <flavor_group> — VM provision)
         deploy_stamp: <type>_vm
Play 3: <type>_configure           (targets: <type> — dynamic group)
Play N: Bootstrap cleanup          (targets: proxmox — remove temp networking)
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

A host can belong to multiple flavor groups.

## Step-by-step: adding a new VM type

Using `homeassistant` as the example:

### 1. Create the provision role

```
roles/homeassistant_vm/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
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

# Auto-start (runs unconditionally to self-heal):
- name: Configure VM to start on boot
  ansible.builtin.command:
    cmd: >-
      qm set {{ homeassistant_vm_id }}
      --onboot 1
      --startup order={{ homeassistant_vm_startup_order }}

# Add to dynamic inventory:
- name: Add HomeAssistant VM to dynamic inventory
  ansible.builtin.add_host:
    name: "{{ homeassistant_vm_name }}"
    groups: homeassistant
    ansible_host: "<bootstrap_ip>"
```

### 2. Create the configure role

```
roles/homeassistant_configure/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
```

### 3. Add VMID to group_vars

```yaml
# inventory/group_vars/all.yml
homeassistant_vm_id: 200
homeassistant_vm_name: homeassistant
homeassistant_vm_memory: 2048
homeassistant_vm_cores: 2
homeassistant_vm_disk_size: 32G
homeassistant_image_path: images/haos.qcow2
```

### 4. Add dynamic group and flavor group to inventory

### 5. Extend site.yml with provision + configure plays

### 6. Update Molecule (add flavor group to platforms, add verify assertions)

### 7. Create VM-specific skill in `.cursor/skills/<type>-build/SKILL.md`

## Bridge allocation

The WAN bridge is auto-detected by `proxmox_bridges` via the host's default route (`proxmox_wan_bridge` fact). All physical-NIC-backed bridges are exported as `proxmox_all_bridges`.

Different VM types consume bridges differently:
- **Router VMs** (OpenWrt): ALL bridges — WAN on `net0`, remaining as LAN ports
- **Service VMs**: typically ONE LAN bridge — `proxmox_all_bridges[1]` (first non-WAN)
- **Isolated VMs**: a dedicated bridge if network isolation is required

## VMs that need internet during configure

If the configure role needs to download packages:
1. The VM must have a NIC on a bridge with upstream connectivity.
2. If the VM changes its own network topology mid-configure, use a multi-phase restart pattern (see VM-specific skill for details).
3. For VMs behind a router VM, WAN access comes through the LAN bridge — no special handling needed.

## Deployment tracking

The `deploy_stamp` role writes `/etc/ansible/facts.d/vm_builds.fact` on Proxmox hosts after each play. On subsequent runs with `gather_facts: true`, the data is available as `ansible_local.vm_builds`. Each play appends its entry without overwriting others.

## Diagnostics pattern

Every VM type SHOULD include diagnostic tasks at key milestones in its roles.
These run on every build and provide debug context when things fail.

Standard diagnostic milestones for any VM:

1. **Post-bootstrap** (`<type>_vm`): VM status, bridge layout, bootstrap IP, `dmesg` errors
2. **Post-configure** (`<type>_configure`): Service status, network state, final config
3. **Final report** (`<type>_configure`): Summary of all configured parameters

Rules:
- `changed_when: false` and `failed_when: false` — diagnostics MUST NOT break the build
- Register output and display via `debug: var:` so it appears in logs
- Include `dmesg` checks — kernel errors are often the root cause when app-level symptoms mislead
- Include protocol-level checks — ICMP ping working does NOT mean TCP/HTTP works

When troubleshooting adds ad-hoc debug tasks, ALWAYS generalize and make them
permanent before closing the issue.

## Detached/async operations

When a configure role restarts services that sever the SSH connection, use
detached scripts (see `async-job-patterns` rule). Key points:

- Launch returns success ≠ script completed successfully
- ALWAYS verify the expected outcome after the pause (`wait_for`, service check)
- NEVER restart services synchronously when the restart changes firewall/network rules affecting the current SSH path

## Cleanup completeness

When a role deploys a file to the Proxmox host or the controller, ALWAYS add
it to both cleanup playbooks (`molecule/default/cleanup.yml` and `playbooks/cleanup.yml`).

Current managed files:
- `/etc/network/interfaces.d/ansible-bridges.conf` (may be modified in-place to `inet dhcp`)
- `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (legacy, removed by converge if present)
- `/etc/network/interfaces.d/ansible-temp-lan.conf` (test workaround, cleaned up)
- `/etc/modprobe.d/blacklist-wifi.conf`
- `/etc/modprobe.d/vfio-pci.conf`
- `/etc/ansible/facts.d/vm_builds.fact`
- `/tmp/openwrt.img` (edge case: left behind if build fails mid-upload)
- `.state/addresses.json` (controller, via `delegate_to: localhost`)

## Rollback conventions

Every feature MUST define a rollback procedure. See the `rollback-patterns`
skill for full details. Summary:

- Per-feature rollback uses tags in `cleanup.yml`: `--tags <feature>-rollback`
- Rollback tags use the `never` meta-tag so they don't run during full cleanup
- Each feature's project plan milestone includes inline rollback steps
- `deploy_stamp` records which features are applied; rollback checks this

```yaml
# cleanup.yml pattern for per-feature rollback
- name: Rollback <feature>
  hosts: <target_group>
  tags: [<feature>-rollback, never]
  gather_facts: false
  tasks:
    - name: Revert changes
      # ... undo UCI, remove packages, restart services
```

## Test strategy

- `molecule/default/` is the full integration test (rebuilds everything from scratch).
- Per-feature scenarios (`molecule/<feature>/`) test incremental changes on top of the baseline.
- Per-feature scenarios assume the baseline exists (router VM running). They do NOT rebuild.
- Full `molecule test` before committing. Per-feature scenarios for fast iteration during development.
- See the `ansible-testing` skill for baseline model details and per-feature scenario setup.
- Cleanup destroys ALL VMs via `qm list` iteration — not hardcoded VMIDs.
- Reproduce production bugs on the test machine first (`test.env`). Only involve production when the test machine cannot reproduce.
- Use TDD: write the verify assertion first, confirm it fails, implement the fix, confirm it passes.

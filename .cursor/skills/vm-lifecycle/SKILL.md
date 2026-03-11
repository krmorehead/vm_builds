---
name: vm-lifecycle
description: General patterns for adding new VM types to the vm_builds project. Use when creating new VM roles, extending site.yml, modifying shared infrastructure, or designing inventory groups. For OpenWrt-specific patterns, see openwrt-build skill instead.
---

# VM Lifecycle Patterns

## Context

This project manages multiple VM and LXC container types on Proxmox. Each service follows a two-role pattern. Shared infrastructure runs once per host. This skill covers the GENERAL patterns that apply to ALL service types. Service-specific patterns (OpenWrt networking, Home Assistant setup, etc.) belong in their own skills.

## Rules

1. Every service type gets exactly two roles: `<type>_vm` or `<type>_lxc` (provision) and `<type>_configure` (setup). NEVER mix provisioning and configuration in one role.
2. LXC provision roles (`<type>_lxc`) MUST use `include_role: proxmox_lxc` with service-specific vars. NEVER duplicate container creation logic.
3. Shared roles (`proxmox_bridges`, `proxmox_backup`, `proxmox_pci_passthrough`, `proxmox_igpu`) run ONCE per host. They export facts consumed by all service roles.
4. Service-specific variables live in role `defaults/main.yml`. Shared variables live in `group_vars/all.yml`. NEVER put service-specific defaults in group_vars.
5. Each provision role MUST check for existing VM/container before creating. Guard ALL creation tasks with `when: not vm_exists | bool` (VMs) or `when: not lxc_exists | bool` (containers via `proxmox_lxc`).
6. VMIDs: 100s network, 200s services, 300s media, 400s desktop, 500s observability, 600s gaming. All defined in `group_vars/all.yml`.
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

## LXC container provisioning pattern

LXC containers use the shared `proxmox_lxc` role via `include_role`. Each
service's `<type>_lxc` role is a thin wrapper:

```yaml
# roles/pihole_lxc/tasks/main.yml
---
- name: Provision Pi-hole container
  ansible.builtin.include_role:
    name: proxmox_lxc
  vars:
    lxc_ct_id: "{{ pihole_ct_id }}"
    lxc_ct_hostname: pihole
    lxc_ct_dynamic_group: pihole
    lxc_ct_memory: 256
    lxc_ct_cores: 1
    lxc_ct_disk: "4"
    lxc_ct_onboot: true
    lxc_ct_startup_order: 3
```

The `proxmox_lxc` role handles: template upload, `pct create`, networking,
device bind mounts, auto-start, container start, readiness wait, and
`add_host` registration.

### LXC configure connection

Configure plays target the dynamic group populated by `add_host`. The
connection uses `community.proxmox.proxmox_pct_remote` which SSHes to the
Proxmox host and runs `pct exec` inside the container. No SSH or bootstrap
IP needed inside the container.

```yaml
# In site.yml
- name: Configure Pi-hole
  hosts: pihole
  gather_facts: true
  roles:
    - pihole_configure
```

The `add_host` in `proxmox_lxc` sets `ansible_connection`,
`ansible_host`, and `proxmox_vmid` automatically.

### LXC template management

Templates are stored locally in `images/` and uploaded to the Proxmox host
during provisioning. NEVER use `pveam download` — the host may not have
internet access or may have unreachable enterprise repos.

See **Image management** section below for the full pattern.

## Image management

ALL VM images and LXC templates are stored locally in the `images/` directory
(gitignored) and uploaded to the Proxmox host during provisioning. This is
a deliberate design decision, not a convenience shortcut.

### Why local images

1. **No internet dependency on Proxmox host.** The host may not have internet
   access (e.g., after cleanup destroys the router VM). Enterprise repos may
   be unreachable without a paid subscription. `pveam download` and `wget`
   both fail in this state.
2. **Reproducibility.** Pinning exact image versions locally ensures every
   build uses the same image. Remote repos can remove or rename versions.
3. **Speed.** Local uploads are faster than downloading from the internet,
   especially on slow WAN links.
4. **Future self-hosting.** Images can be served from a local NAS or HTTP
   server for multi-node deployments.

Previous bug: `pveam download` failed with `400 Parameter verification
failed. template: no such template` because the template name didn't match
the available list. Switching to local hosting eliminated the dependency.

### Directory layout

```
images/                                    (gitignored)
├── openwrt.img                            VM disk image (qcow2/raw)
├── debian-12-standard_12.12-1_amd64.tar.zst  LXC template
└── ...future images...
```

NEVER commit images to git. The `images/` directory is listed in `.gitignore`.
Document the expected image filename and download URL in role defaults and
in `docs/architecture/`.

### Image path variables

Each service defines its image path in `group_vars/all.yml`:

```yaml
openwrt_image_path: images/openwrt.img
proxmox_lxc_template_path: images/debian-12-standard_12.12-1_amd64.tar.zst
```

Roles reference these variables, not hardcoded paths.

### Upload pattern for VMs

```yaml
- name: Upload image to Proxmox
  ansible.builtin.copy:
    src: "{{ openwrt_image_path }}"
    dest: "/tmp/{{ openwrt_image_path | basename }}"
    mode: "0644"
  when: not vm_exists | bool

# ... qm importdisk, then clean up /tmp file
```

### Upload pattern for LXC templates

```yaml
- name: Upload LXC template to Proxmox
  ansible.builtin.copy:
    src: "{{ role_path }}/../../{{ lxc_ct_template_path }}"
    dest: "/var/lib/vz/template/cache/{{ lxc_ct_template }}"
    mode: "0644"
```

ALWAYS use `role_path` for the source path. Relative paths like
`../../images/...` break when Molecule runs from non-default scenarios
because the working directory is `molecule/<scenario>/`, not the project root.

Previous bug: `proxmox_lxc` template upload used a bare relative path.
It worked from `molecule/default/` but failed from `molecule/proxmox-lxc/`
with "Could not find or access". Fix was to use `{{ role_path }}/../../`.

### Adding a new image

1. Download the image to `images/` (use `wget`, browser, or `pveam` locally).
2. Add the path variable to `group_vars/all.yml`.
3. Reference the variable in the provision role's defaults and tasks.
4. Document the download URL and expected checksum in the role README or
   architecture doc.
5. Add the filename to `Before running tests` in the ansible-testing skill
   (e.g., "Verify LXC template exists: `ls images/*.tar.zst`").

### Per-feature molecule scenarios for LXC

Each LXC service gets a `molecule/<type>-lxc/` scenario that:
- Creates/destroys only the test container (VMID 999 for tests)
- Never touches the router VM or other services
- Runs in ~30-60 seconds (vs 4-5 min for full integration)
- Has no initial cleanup phase (assumes baseline exists)

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

## Host-level apt prerequisites

Roles that install packages on the Proxmox HOST (not inside VMs/containers) must handle two prerequisites:

1. **DNS**: After cleanup destroys the router VM, `/etc/resolv.conf` may point to a dead IP (`10.10.10.1`). Check DNS with `getent hosts deb.debian.org` and fall back to `8.8.8.8` / `1.1.1.1`.
2. **Enterprise repos**: `pve-enterprise.sources` and `ceph.sources` require a subscription. Without it, `apt-get update` hangs. Rename both to `.disabled` and add the `pve-no-subscription` repo. ALWAYS restore them in cleanup.
   - NEVER use `sed` or `Enabled: no` in deb822 files — unreliable.
   - ALWAYS rename with `mv` (e.g., `mv pve-enterprise.sources pve-enterprise.sources.disabled`).
   - The `proxmox_igpu` role implements this pattern — reference it.
   - ALWAYS disable BOTH `pve-enterprise.sources` AND `ceph.sources`. Missing either one causes `apt` to hang.

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
- `/var/lib/vz/template/cache/*.tar.zst` (LXC templates, uploaded by `proxmox_lxc`)
- `.state/addresses.json` (controller, via `delegate_to: localhost`)

Cleanup MUST destroy both VMs (`qm list` iteration) AND containers
(`pct list` iteration). NEVER hardcode VMIDs in cleanup.

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

## Documentation accuracy

When adding or modifying a role, ALWAYS update these in the same change:

1. **`docs/architecture/overview.md`** — two places: the GPU/PCI decomposition
   tree AND the role catalog lower in the file. Both MUST list the same exports.
2. **`docs/architecture/roles.md`** — full role documentation (purpose, key
   variables, exported facts, usage pattern).
3. **`CHANGELOG.md`** — add an entry under `[Unreleased]`.

NEVER document future exports as if they exist. Mark with "(future)" or omit.
NEVER hardcode bridge names (`vmbr0`). Use "WAN bridge" / "LAN bridge".

Previous bug: `overview.md` listed `gpu_pci_devices` as a current export
when it was only planned. The `proxmox_igpu` export list was missing 3 of
6 facts. `openwrt-build.md` hardcoded `vmbr0` as the bootstrap bridge.

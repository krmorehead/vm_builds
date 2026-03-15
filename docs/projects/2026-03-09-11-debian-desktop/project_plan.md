# Debian Desktop

## Overview

A full Debian VM with KDE and GNOME desktop sessions for general-purpose
computing. Takes **exclusive** iGPU access via `hostpci` passthrough, which
disrupts all iGPU-dependent LXC containers while running. This is the most
impactful member of the display-exclusive group and should only be started
when needed.

## Type

VM (KVM/QEMU)

## Resources

- Cores: 2
- RAM: 1024 MB
- Disk: 32 GB (OS + applications)
- Network: LAN bridge
- iGPU: exclusive passthrough via `hostpci` (entire GPU bound to vfio-pci)
- VMID: 400

## Startup

- Auto-start: no (on-demand; user starts for desktop tasks)
- Boot priority: N/A
- Depends on: `proxmox_igpu` role (provides PCI address for exclusive hostpci passthrough)

## Build Profiles

- Home Entertainment Box: yes (`desktop_nodes`)
- Minimal Router: no
- Gaming Rig: no

## Display Exclusivity

- Display-exclusive: **yes** (most disruptive)
- Start Desktop VM → Kiosk, Kodi, Moonlight all stop; iGPU unbound from i915
- Jellyfin falls back to software transcoding while Desktop VM runs
- Stop Desktop VM → iGPU returns to i915; Kiosk restarts

## Prerequisites

- Shared infrastructure: `proxmox_igpu` (hard-fails if absent), display-exclusive
  hookscript (deployed by Kiosk project `2026-03-09-12`)
- OpenWrt router operational (network, SSH via ProxyJump)
- Physical display connected to host HDMI/DP
- `desktop_vm_id: 400` already in `group_vars/all.yml`
- `desktop_nodes` flavor group and `desktop` dynamic group already in `inventory/hosts.yml`
- `desktop_nodes` already in `molecule/default/molecule.yml` platform groups

## Network topology assumption

`desktop_nodes` hosts are always behind OpenWrt (`router_nodes` or `lan_hosts`).
Desktop VMs always use the OpenWrt LAN subnet on the LAN bridge. There is
no WAN-connected case — desktop services only run on the Home Entertainment
Box profile, which always has OpenWrt.

## Documented exception: cloud image + apt install

**Desktop VMs are the exception to the "bake, don't configure at runtime"
principle.** Full desktop environments (KDE, GNOME, Firefox, etc.) are too
large and hardware-dependent for generic pre-built images. The practical
approach for desktop VMs is:

1. Start from a Debian 12 cloud image (lightweight, cloud-init enabled)
2. Install desktop packages at configure time via `apt`

This is documented as an exception because:
- Desktop environments total 2-4 GB of packages — impractical for a
  pre-built image that must be portable across hardware
- GPU driver selection depends on the actual GPU vendor (Intel vs AMD),
  known only at runtime via `igpu_vendor`
- The cloud image approach is the community standard for VM provisioning
  (Packer, Terraform, cloud-init all use this pattern)

If pre-building becomes practical (e.g., one dedicated hardware platform),
add a `build_desktop_vm` function to `build-images.sh` and switch to the
standard bake pattern.

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle-architecture` | Two-role pattern, VM provisioning, deploy_stamp, cleanup completeness |
| `image-management-patterns` | Image management, local images/ directory |
| `vm-provisioning-patterns` | VM provisioning via qm create, add_host, dynamic groups |
| `molecule-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-architecture` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-system-safety` | iGPU hard-fail detection, exclusive GPU passthrough, safe host commands, PCI cleanup |
| `lan-ssh-patterns` | ProxyJump for testing on LAN nodes |
| `project-planning-structure` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Guest OS: Debian 12
│   └── Same distro as Proxmox host; consistent, well-supported, stable
│
├── Base image: Debian cloud image + cloud-init for bootstrap
│   └── No interactive installer; cloud-init sets user, SSH keys, network at first boot
│   └── DOCUMENTED EXCEPTION to bake principle (see above)
│
├── VM provisioning: qm create (not proxmox_lxc)
│   └── This is a VM, not an LXC container — full KVM/QEMU stack
│
├── Configuration method: SSH via ProxyJump (standard VM pattern)
│   └── VMs don't support pct_remote; SSH key injected by cloud-init
│
├── BIOS: UEFI (OVMF), machine type q35
│   └── Required for modern GPU passthrough; legacy BIOS incompatible
│
├── iGPU access: exclusive passthrough via hostpci (vfio-pci)
│   └── VM needs full GPU driver stack (i915/amdgpu in guest); only option for display-out from VM
│   └── Most disruptive display-exclusive service — takes entire GPU from host
│   └── proxmox_igpu hard-fails if absent; Desktop VM depends on igpu_pci_address
│
├── Display-exclusive hookscript: deployed by Kiosk project (2026-03-09-12)
│   └── Desktop VM attaches via qm set --hookscript; does not deploy it
│
├── Display manager: SDDM
│   └── Handles KDE + GNOME session switching; lightweight; works with DRM/KMS
│
├── Desktop sessions
│   ├── KDE Plasma: Windows-style UX (taskbar, system tray, alt-tab)
│   └── GNOME: Mac-style UX (dock, activities, workspaces)
│
└── Image management: local images/ directory, uploaded during provisioning
    └── NEVER use pveam download; host may lack internet or enterprise repos
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

`molecule/default` converges all 4 nodes (home, mesh1, ai, mesh2). In
Phase 3 of `site.yml`, Desktop VM provisions on `desktop_nodes` (currently
`home` only). It runs after all media services since it's the most
disruptive display-exclusive service. Desktop VM uses the `[desktop]` tag.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/desktop-vm/` which only touches
VMID 400. The OpenWrt baseline and LXC containers stay running (Kiosk
will be stopped by the hookscript when the Desktop VM starts).

```
Scenario Hierarchy (Desktop VM additions)
├── molecule/default/                 Full integration (4-node, ~4-5 min)
│   └── Runs everything including Desktop VM provision + configure
│
└── molecule/desktop-vm/             Desktop VM only (~2-3 min)
    ├── converge: provision + configure Desktop VM
    ├── verify: Desktop VM assertions (SSH, packages, GPU)
    └── cleanup: destroy VM 400 + PCI cleanup (restore iGPU to host)
```

### Day-to-day workflow

```bash
# 1. Build baseline once (or restore after molecule test)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Iterate on Desktop VM (only touches VMID 400)
molecule converge -s desktop-vm               # ~2-3 min, provision + configure
molecule verify -s desktop-vm                 # ~15s, assertions only
molecule converge -s desktop-vm               # ~2-3 min, re-converge

# 3. Clean up per-feature changes (baseline stays, iGPU returns to host)
molecule cleanup -s desktop-vm                # destroys VM 400, PCI cleanup

# 4. Final validation before commit
molecule test                                 # full clean-state, ~4-5 min
molecule converge                             # restore baseline for next task
```

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything | Everything | Full rebuild required after |
| `default` (converge) | Everything | Nothing | Baseline preserved |
| `desktop-vm` | VM 400, vfio-pci binding | VM 400, vfio-pci unbind, driver reload | iGPU returns to host; Kiosk restarts |

---

## Milestone Dependency Graph

```
M1: Image Preparation ───── self-contained
 └── M2: VM Provisioning ── depends on M1, proxmox_igpu (igpu_pci_address)
      └── M3: Configuration ─ depends on M2 (cloud image + apt = documented exception)
           └── M4: Polish ──── depends on M3
                └── M5: Testing & Integration ── depends on M2–M4
                     └── M6: Documentation ── depends on M2–M5
```

---

## Milestones

### Milestone 1: Image Preparation

_Self-contained. No external dependencies._

Download the Debian 12 cloud image into `images/`, add `desktop_image_path`
to `group_vars/all.yml`, and verify cloud-init support.

See: `image-management-patterns` skill.

**Implementation pattern:**
- Add `desktop_image_path` to `inventory/group_vars/all.yml`
- Image stored in `images/` (gitignored), uploaded during provisioning
- NEVER use `pveam download` — host may lack internet

- [ ] Download Debian 12 cloud image (qcow2) into `images/`
- [ ] Add `desktop_image_path: images/debian-12-genericcloud-amd64.qcow2` to
  `group_vars/all.yml`
- [ ] Verify cloud-init support in the image (user-data, network-config)

**Verify:**

- [ ] Image file exists at `images/debian-12-genericcloud-amd64.qcow2` (or
  configured path)
- [ ] `desktop_image_path` resolves correctly in role defaults
- [ ] Image is valid qcow2: `qemu-img info` succeeds

**Rollback:** Remove image file and `desktop_image_path` from `group_vars/all.yml`.

---

### Milestone 2: VM Provisioning

_Blocked on: M1 (image), `proxmox_igpu` role (provides `igpu_pci_address`)._

Create the `desktop_vm` role using `qm create`, UEFI (OVMF), q35 machine type,
cloud-init for bootstrap, and exclusive iGPU passthrough via `hostpci0`. Attach
display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12). Add
the provision and configure plays to `site.yml`. Integration with `site.yml`
is consolidated here.

See: `vm-provisioning-patterns` skill (two-role pattern, qm create, add_host), `proxmox-system-safety`
skill (iGPU hard-fail, exclusive passthrough, PCI address from proxmox_igpu).

**Implementation pattern:**
- Role: `roles/desktop_vm/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `desktop_nodes`, tagged `[desktop]`,
  in Phase 3 (after media tier)
- deploy_stamp included as last role in the provision play
- Dynamic group `desktop` populated via `add_host` (SSH connection, not pct_remote)
- Hookscript: `qm set --hookscript local:snippets/display-exclusive.sh` (script
  deployed by Kiosk project; Desktop VM only attaches)

**Already complete** (from shared infrastructure / inventory):
- `desktop_vm_id: 400` in `group_vars/all.yml`
- `desktop_nodes` flavor group and `desktop` dynamic group in `inventory/hosts.yml`
- `desktop_nodes` in `molecule/default/molecule.yml` platform groups
- `proxmox_igpu` exports `igpu_pci_address`, `igpu_vendor` (hard-fails if absent)

- [ ] Create `roles/desktop_vm/defaults/main.yml`:
  - `desktop_vm_id: "{{ desktop_vm_id }}"`, `desktop_vm_memory: 1024`,
    `desktop_vm_cores: 2`, `desktop_vm_disk: "32G"`
  - `desktop_vm_onboot: false` (on-demand)
  - `desktop_vm_image_path: "{{ desktop_image_path }}"`
- [ ] Create `roles/desktop_vm/tasks/main.yml`:
  - Check VM exists: `qm list` → `vm_exists` fact
  - When not exists: `qm create` with UEFI BIOS (OVMF), `machine: q35`
  - Import disk: upload image to `/tmp/`, `qm importdisk`, delete temp file
  - Attach NIC on LAN bridge (`proxmox_all_bridges` — first LAN bridge)
  - Configure `hostpci0` for iGPU: `hostpci0: host={{ igpu_pci_address }}` (from
    `proxmox_igpu` facts; role hard-fails if absent)
  - Configure cloud-init: user, SSH keys, network (DHCP or static)
  - Attach display-exclusive hookscript via `qm set --hookscript`
  - Unconditional `qm set --onboot 0` (on-demand; self-heal if changed)
  - Start VM, wait for SSH (cloud-init completes)
  - Register in `desktop` dynamic group via `add_host` with
    `ansible_connection: ansible.builtin.ssh`, `ansible_host` (VM LAN IP),
    ProxyJump through Proxmox host
- [ ] Create `roles/desktop_vm/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `desktop_nodes`,
  tagged `[desktop]`, with `desktop_vm` role and `deploy_stamp`
  (after media tier)
- [ ] Add configure play to `site.yml` Phase 3, targeting `desktop` dynamic
  group, tagged `[desktop]`, `gather_facts: true`, after provision play
- [ ] Create `tasks/reconstruct_desktop_group.yml`:
  - Verify VM 400 is running: `qm status {{ desktop_vm_id }}`
  - Get VM LAN IP (from OpenWrt DHCP lease via VM MAC, or cloud-init metadata)
  - Register via `add_host` with:
    `ansible_connection: ansible.builtin.ssh`,
    `ansible_host: <vm_lan_ip>`,
    `ansible_ssh_common_args: -o ProxyJump=root@{{ ansible_host }} -o ServerAliveInterval=15 -o ServerAliveCountMax=4`,
    `ansible_user: <desktop_user>`
  - See: `lan-ssh-patterns` skill (ProxyJump keepalives)

**Note on `[desktop]` tag:** This tag is separate from `[kiosk]`. Desktop VM
is significantly more disruptive (exclusive iGPU passthrough) than Kiosk
(shared iGPU bind mount). Separate tags allow deploying Kiosk without
triggering the Desktop VM's heavy GPU unbind/rebind cycle.

**Verify:**

- [ ] VM 400 is running: `qm status 400` returns `running`
- [ ] VM uses UEFI: `qm config 400` shows `bios: ovmf`, `machine: q35`
- [ ] `hostpci0` set to `igpu_pci_address`: `qm config 400` shows correct PCI
- [ ] iGPU unbound from host when VM starts: `lspci -k` shows vfio-pci on iGPU
- [ ] Cloud-init complete: SSH to VM succeeds with key auth
- [ ] Hookscript attached: `qm config 400` shows `hookscript: local:snippets/display-exclusive.sh`
- [ ] VM in `desktop` dynamic group
- [ ] deploy_stamp contains `desktop_vm` play entry

**Rollback:**

VM destruction: generic `qm list` iteration in cleanup (`qm stop` + `qm destroy`).
PCI cleanup (see `proxmox-system-safety` skill): unbind vfio-pci, remove blacklist/vfio
config, reload original drivers, rescan PCI bus. Detach hookscript before destroy.

---

### Milestone 3: Configuration

_Blocked on: M2 (VM must be running, SSH accessible). Uses cloud image + apt
install (documented exception to bake principle)._

Configure the running VM via SSH + ProxyJump: install KDE, GNOME, SDDM,
GPU drivers (vendor-specific via `igpu_vendor`), base applications, and
user setup from `.env`.

See: `vm-lifecycle-architecture` skill (configure via SSH, dynamic group), `lan-ssh-patterns`
skill (ProxyJump for LAN nodes).

**Implementation pattern:**
- Role: `roles/desktop_configure/defaults/main.yml`, `tasks/main.yml`,
  `meta/main.yml`
- site.yml: configure play targeting `desktop` dynamic group, tagged `[desktop]`
- Connection: SSH via ProxyJump (ansible_host = Proxmox, ProxyJump to VM LAN IP)

**Env variables** (from `.env`):

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `DESKTOP_USER` | yes | Desktop user name | `kyle` |
| `DESKTOP_PASSWORD` | yes (hashed) | User password (bcrypt) | `$6$...` |
| `DESKTOP_SSH_PUBLIC_KEY` | yes | SSH key for login | `ssh-ed25519 AAAA...` |
| `DESKTOP_AUTOLOGIN` | no | Auto-login at boot | `true` / `false` (default: false) |

- [ ] Create `roles/desktop_configure/defaults/main.yml`:
  - All env vars via `lookup('env', ...) | default('', true)` for optional
  - `desktop_autologin` from `DESKTOP_AUTOLOGIN` (default: false)
- [ ] Create `roles/desktop_configure/tasks/main.yml`:
  - Install KDE Plasma: `task-kde-desktop`
  - Install GNOME: `task-gnome-desktop`
  - Install SDDM display manager, configure as default
  - User setup: create user from `DESKTOP_USER`, add to `video`, `render`, `audio`
  - Install GPU drivers: vendor-specific via `igpu_vendor` fact
    - Intel: `xserver-xorg-video-intel`, `mesa-vulkan-drivers`
    - AMD: `xserver-xorg-video-amdgpu`, `mesa-vulkan-drivers`
  - Install base applications: Firefox, file manager, terminal
  - Configure log forwarding to rsyslog
- [ ] Create `roles/desktop_configure/meta/main.yml` with required metadata

**Verify:**

- [ ] KDE and GNOME packages installed: `dpkg -l | grep -E 'task-kde|task-gnome'`
- [ ] SDDM installed and default: `systemctl get-default` or equivalent
- [ ] User exists with correct groups: `id {{ desktop_user }}` shows video, render, audio
- [ ] GPU drivers installed (vendor-appropriate package)
- [ ] Firefox and base apps present
- [ ] Idempotent: re-run does not fail

**Rollback:**

Remove packages, remove user, revert SDDM config. Full VM destruction is escape
hatch (M2 rollback).

---

### Milestone 4: Desktop Environment Polish

_Blocked on: M3 (base config complete)._

Apply session-specific polish: KDE taskbar/theme, GNOME dock/theme, session
switching, and optional auto-login controlled by `DESKTOP_AUTOLOGIN`.

See: `vm-lifecycle-architecture` skill (configure role task files).

**Implementation pattern:**
- Role: `roles/desktop_configure/tasks/polish.yml` (or inline in main.yml)
- Optional: separate task file for per-feature deploy_stamp if polish is tagged

- [ ] KDE session: taskbar at bottom, system tray, dark theme, window snapping
- [ ] GNOME session: dock at bottom, dash-to-dock extension, dark theme
- [ ] Verify session switching: log out of KDE → SDDM → log into GNOME
- [ ] Auto-login: controlled by `DESKTOP_AUTOLOGIN` in `.env`; configure SDDM
  autologin when true

**Verify:**

- [ ] KDE theme and layout applied
- [ ] GNOME theme and dock applied
- [ ] Session switch KDE ↔ GNOME works
- [ ] Auto-login respects `DESKTOP_AUTOLOGIN` (when true, SDDM skips login)

**Rollback:** Revert theme/layout configs. Remove auto-login if enabled.

---

### Milestone 5: Testing & Integration

_Depends on M2–M4._

Create per-feature molecule scenario for fast Desktop VM-only iteration,
extend `molecule/default/verify.yml` for full integration, add rollback
plays to `playbooks/cleanup.yml`, add VM and PCI cleanup to both cleanup
playbooks, and run final validation.

See: `molecule-testing` skill (per-feature scenario, baseline workflow),
`molecule-verify` skill (verify completeness), `proxmox-system-safety` skill (PCI cleanup after VM destroy).

#### 5a. Per-feature scenario: `molecule/desktop-vm/`

- [ ] Create `molecule/desktop-vm/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - desktop_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      DESKTOP_USER: ${DESKTOP_USER:-testuser}
      DESKTOP_PASSWORD: ${DESKTOP_PASSWORD:-}
      DESKTOP_SSH_PUBLIC_KEY: ${DESKTOP_SSH_PUBLIC_KEY:-}
      DESKTOP_AUTOLOGIN: ${DESKTOP_AUTOLOGIN:-false}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/desktop-vm/converge.yml`
- [ ] Create `molecule/desktop-vm/verify.yml`
- [ ] Create `molecule/desktop-vm/cleanup.yml`:
  Destroys VM 400 and runs PCI cleanup (vfio-pci unbind, driver reload).

#### 5b. Full integration (`molecule/default/`)

- [ ] Extend `molecule/default/verify.yml` with Desktop VM assertions:
  - VM 400 created, cloud-init complete, SSH accessible
  - SDDM installed, KDE + GNOME packages present
  - GPU passthrough configured (`hostpci0` set)
  - Hookscript attached
  - deploy_stamp contains `desktop_vm` entry

- [ ] Extend `molecule/default/cleanup.yml` for VM 400:
  - `qm stop 400` + `qm destroy 400`
  - PCI cleanup: unbind vfio-pci, remove vfio config, reload i915/amdgpu, rescan PCI
  - (See `proxmox-system-safety` skill, PCI device cleanup section)

#### 5c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `desktop-rollback` play:
  ```yaml
  - name: Rollback Desktop VM
    hosts: desktop_nodes
    gather_facts: false
    tags: [desktop-rollback, never]
    tasks:
      - name: Stop and destroy Desktop VM
        ansible.builtin.shell:
          cmd: |
            qm stop {{ desktop_vm_id }} 2>/dev/null || true
            sleep 3
            qm destroy {{ desktop_vm_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true

      - name: PCI cleanup - restore iGPU to host
        ansible.builtin.shell:
          cmd: |
            set -o pipefail
            echo 1 > /sys/bus/pci/rescan
          executable: /bin/bash
        changed_when: true
  ```

- [ ] Extend `playbooks/cleanup.yml` with same VM + PCI cleanup

#### 5d. Molecule env passthrough

- [ ] Add `DESKTOP_USER`, `DESKTOP_PASSWORD`, `DESKTOP_SSH_PUBLIC_KEY`,
  `DESKTOP_AUTOLOGIN` to `molecule/default/molecule.yml` `provisioner.env`

#### 5e. Final validation

- [ ] Run `molecule test` — full 4-node integration passes with exit code 0
- [ ] Run `molecule test -s desktop-vm` — per-feature cycle passes
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no Desktop artifacts; iGPU returns to i915/amdgpu
- [ ] PCI cleanup restores host GPU for next run

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 6: Documentation

_Depends on M2–M5._

- [ ] Create `docs/architecture/desktop-build.md`:
  - Requirements, design decisions, env variables
  - Cloud image + apt install as documented exception to bake principle
  - iGPU exclusive passthrough, cloud-init bootstrap
  - Session switching (KDE/GNOME), SDDM config
  - Display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12)
  - Test vs production workflow
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: Desktop provision + configure plays
  - Role catalog: desktop_vm, desktop_configure
- [ ] Update `docs/architecture/roles.md`:
  - Add `desktop_vm` role documentation (purpose, exclusive iGPU, key variables)
  - Add `desktop_configure` role documentation (purpose, env vars, cloud-init exception)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Desktop project to Active Projects
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented
- [ ] Display exclusivity and hookscript ownership (Kiosk project) documented
- [ ] Cloud image + apt exception documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Display exclusivity**: Desktop VM is the most disruptive display-exclusive
  service. Starting it stops Kiosk, Kodi, and Moonlight AND unbinds the
  iGPU from the host. Jellyfin falls back to software transcoding.
- **Kiosk hookscript**: Desktop VM attaches the hookscript deployed by the
  Kiosk project. When the Desktop VM stops, the hookscript restarts Kiosk
  and the iGPU returns to the host driver.
- **rsyslog**: Desktop VM logs can be forwarded to the rsyslog collector via
  standard rsyslog client configuration inside the VM.
- **Future: pre-built image**: If a single hardware platform is standardized,
  a pre-built desktop image via Packer/debootstrap could replace the cloud
  image + apt approach, bringing the Desktop VM in line with the bake
  principle.

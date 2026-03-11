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

- Home Entertainment Box: yes
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

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, VM provisioning, deploy_stamp, cleanup completeness, image management |
| `ansible-testing` | Molecule scenarios, verify assertions, per-feature scenario setup, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | iGPU hard-fail detection, exclusive GPU passthrough, safe host commands, PCI cleanup |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Guest OS: Debian 12
│   └── Same distro as Proxmox host; consistent, well-supported, stable
│
├── Base image: Debian cloud image + cloud-init for bootstrap
│   └── No interactive installer; cloud-init sets user, SSH keys, network at first boot
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
│   └── VM needs full GPU driver stack (i915 in guest); only option for display-out from VM
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

## Milestone Dependency Graph

```
M1: Image Preparation ───── self-contained
 └── M2: VM Provisioning ── depends on M1, proxmox_igpu (igpu_pci_address)
      └── M3: Configuration ─ depends on M2
           └── M4: Polish ──── depends on M3
                └── M5: Integration ─ depends on M1–M4
                     └── M6: Testing ─ depends on M1–M5
                          └── M7: Docs ─ depends on M1–M6
```

---

## Milestones

### Milestone 1: Image Preparation

_Self-contained. No external dependencies._

Download the Debian 12 cloud image into `images/`, add `desktop_image_path`
to `group_vars/all.yml`, and verify cloud-init support.

See: `vm-lifecycle` skill (image management, local images/ directory).

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
display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12).

See: `vm-lifecycle` skill (two-role pattern, qm create, add_host), `proxmox-host-safety`
skill (iGPU hard-fail, exclusive passthrough, PCI address from proxmox_igpu).

**Implementation pattern:**
- Role: `roles/desktop_vm/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `desktop_nodes`, tagged `[desktop]`
- deploy_stamp included as last role in the provision play
- Dynamic group `desktop` populated via `add_host` (SSH connection, not pct_remote)
- Hookscript: `qm set --hookscript local:snippets/display-exclusive.sh` (script
  deployed by Kiosk project; Desktop VM only attaches)

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
- [ ] Add provision play to `site.yml` targeting `desktop_nodes`, tagged
  `[desktop]`, with `desktop_vm` role and `deploy_stamp`
- [ ] Verify `desktop_vm_id: 400` in `group_vars/all.yml` (already defined)
- [ ] Verify `desktop_nodes` and `desktop` groups in `inventory/hosts.yml`

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
PCI cleanup (see `proxmox-host-safety` skill): unbind vfio-pci, remove blacklist/vfio
config, reload original drivers, rescan PCI bus. Detach hookscript before destroy.

---

### Milestone 3: Configuration

_Blocked on: M2 (VM must be running, SSH accessible)._

Configure the running VM via SSH + ProxyJump: install KDE, GNOME, SDDM,
Intel GPU drivers, base applications, and user setup from `.env`.

See: `vm-lifecycle` skill (configure via SSH, dynamic group), `multi-node-ssh`
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
  - Install Intel GPU drivers: `xserver-xorg-video-intel`, `mesa-vulkan-drivers`
  - Install base applications: Firefox, file manager, terminal
  - Configure log forwarding to rsyslog
- [ ] Add configure play to `site.yml` targeting `desktop` dynamic group,
  tagged `[desktop]`, after provision play

**Verify:**

- [ ] KDE and GNOME packages installed: `dpkg -l | grep -E 'task-kde|task-gnome'`
- [ ] SDDM installed and default: `systemctl get-default` or equivalent
- [ ] User exists with correct groups: `id {{ desktop_user }}` shows video, render, audio
- [ ] Intel drivers installed: `dpkg -l | grep xserver-xorg-video-intel`
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

See: `vm-lifecycle` skill (configure role task files).

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

### Milestone 5: Integration

_Blocked on: M1–M4._

Wire `desktop_vm` and `desktop_configure` into `site.yml`, ensure correct
play order (after media tier), add dynamic group and VMID to inventory.

See: `vm-lifecycle` skill (site.yml play order, deploy_stamp pairing).

**Implementation pattern:**
- site.yml: provision play targets `desktop_nodes`, configure play targets
  `desktop` dynamic group
- Order: Desktop play AFTER media tier (Jellyfin, Kodi, Moonlight)
- deploy_stamp on `desktop_nodes` after provision play

- [ ] Add `desktop_vm` provision play to `site.yml` targeting `desktop_nodes`
- [ ] Add `desktop_configure` play targeting `desktop` dynamic group
- [ ] Include `deploy_stamp` in provision play
- [ ] Order play AFTER media tier
- [ ] Verify `desktop_nodes` in `molecule/default/molecule.yml` platform groups

**Verify:**

- [ ] Full `ansible-playbook site.yml` runs without error (when targeting
  desktop_nodes host)
- [ ] deploy_stamp written on Proxmox host for desktop_vm play
- [ ] Play order correct: desktop after media services

**Rollback:** Remove plays from site.yml. deploy_stamp will show stale state
until next full run.

---

### Milestone 6: Testing

_Blocked on: M1–M5._

Extend molecule verify and cleanup, create `reconstruct_desktop_group.yml`
for per-feature scenarios, add VM 400 and PCI cleanup to cleanup playbooks.

See: `ansible-testing` skill (verify completeness, per-feature scenario,
baseline workflow), `proxmox-host-safety` skill (PCI cleanup after VM destroy).

**Implementation pattern:**
- `tasks/reconstruct_desktop_group.yml`: verify VM 400 running, `add_host` with
  SSH + ProxyJump connection (ansible_connection, ansible_host, ProxyJump)
- `molecule/default/verify.yml`: add Desktop VM assertions
- `molecule/default/cleanup.yml` and `playbooks/cleanup.yml`: VM 400 stop +
  destroy, PCI cleanup (vfio-pci unbind, driver reload)
- Per-feature scenario: `molecule/desktop-vm/` with converge that reconstructs
  desktop group first

- [ ] Create `tasks/reconstruct_desktop_group.yml`:
  - Verify VM 400 is running: `qm status {{ desktop_vm_id }}`
  - Get VM LAN IP (from OpenWrt DHCP lease via VM MAC, or cloud-init metadata)
  - Register via `add_host` with:
    `ansible_connection: ansible.builtin.ssh`,
    `ansible_host: <vm_lan_ip>`,
    `ansible_ssh_common_args: -o ProxyJump=root@{{ ansible_host }} -o ServerAliveInterval=15 -o ServerAliveCountMax=4`,
    `ansible_user: <desktop_user>`
  - Required for per-feature molecule scenarios (add_host is ephemeral)
  - See: `multi-node-ssh` skill (ProxyJump keepalives)
- [ ] Extend `molecule/default/verify.yml`:
  - VM 400 created, cloud-init complete, SSH accessible
  - SDDM installed, KDE + GNOME packages present
  - GPU passthrough configured (`hostpci0` set)
  - deploy_stamp contains desktop_vm
- [ ] Extend `molecule/default/cleanup.yml` for VM 400:
  - `qm stop 400` + `qm destroy 400`
  - PCI cleanup: unbind vfio-pci, remove vfio config, reload i915, rescan PCI
  - (See `proxmox-host-safety` skill, PCI device cleanup section)
- [ ] Extend `playbooks/cleanup.yml` with same VM + PCI cleanup
- [ ] Create `molecule/desktop-vm/` per-feature scenario (optional):
  - converge.yml: reconstruct desktop group, run desktop_configure
  - verify.yml: reconstruct, run Desktop assertions
  - cleanup.yml: VM destroy, PCI cleanup
- [ ] Run `molecule test` — must pass with exit code 0

**Verify:**

- [ ] Full `molecule test` passes with exit code 0
- [ ] Verify assertions cover: VM state, cloud-init, SSH, packages, hostpci0,
  deploy_stamp
- [ ] Cleanup leaves no Desktop artifacts; iGPU returns to i915
- [ ] PCI cleanup restores host GPU for next run

**Rollback:** N/A — test infrastructure only; revert via git.

---

### Milestone 7: Documentation

_Blocked on: M1–M6._

Create `docs/architecture/desktop-build.md`, update overview/roadmap, add
CHANGELOG entry.

See: `project-planning` skill (documentation accuracy).

- [ ] Create `docs/architecture/desktop-build.md`:
  - Requirements, design decisions, env variables
  - iGPU exclusive passthrough, cloud-init bootstrap
  - Session switching (KDE/GNOME), SDDM config
  - Display-exclusive hookscript (deployed by Kiosk project 2026-03-09-12)
  - Test vs production workflow
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: Desktop provision + configure plays
  - Role catalog: desktop_vm, desktop_configure
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Desktop project to Active Projects
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented
- [ ] Display exclusivity and hookscript ownership (Kiosk project) documented

**Rollback:** N/A — documentation-only milestone.

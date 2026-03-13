# Gaming Rig

## Overview

A dedicated Proxmox host running a Windows 11 VM with full discrete GPU
passthrough for gaming and Sunshine for Moonlight game streaming. This is a
**separate build profile** from the home entertainment box — it runs on
**different physical hardware**. The gaming rig host also joins
`monitoring_nodes` for Netdata + rsyslog (provisioned by the monitoring
projects, not this one).

## Type

VM (KVM/QEMU) on dedicated gaming hardware

## Resources

- Cores: 4-8 (depends on host CPU)
- RAM: 8-16 GB (depends on host memory)
- Disk: 128+ GB (OS + game storage)
- Network: LAN bridge
- GPU: discrete GPU via exclusive PCIe passthrough (vfio-pci)
- VMID: 600

## Hardware Requirements

```
Minimum Gaming Rig Hardware
├── CPU: 6+ cores (to leave 2 for Proxmox + monitoring after VM allocation)
├── RAM: 16+ GB (8 GB for VM, remainder for host + monitoring)
├── GPU: discrete NVIDIA or AMD GPU (for vfio-pci passthrough)
├── Storage: NVMe SSD (PCIe passthrough or virtio-scsi)
├── Network: 1 GbE minimum (for Moonlight streaming)
└── IOMMU: must support clean group isolation for GPU (VT-d/AMD-Vi REQUIRED in BIOS)
```

## Startup

- Auto-start: yes (on the gaming rig host, this is the primary service)
- Boot priority: 1
- Depends on: Proxmox host only

## Build Profiles

- Home Entertainment Box: no
- Minimal Router: no
- Gaming Rig: yes (core)
- The gaming rig host also belongs to `monitoring_nodes` for Netdata + rsyslog

## Network topology assumption

`gaming_nodes` is **separate physical hardware** that may or may not be
behind an OpenWrt router. The Gaming Rig build profile does NOT include
`router_nodes` — it connects directly to the upstream network (ISP router
or switch). The Windows VM uses the host's WAN bridge and gets its IP
from the upstream DHCP server.

This is fundamentally different from Home Entertainment Box services which
are always behind OpenWrt. There is no LAN/WAN branching decision — the
Gaming Rig always uses the WAN bridge.

## Prerequisites

- Shared infrastructure: `proxmox_pci_passthrough` role (project 00) — extended
  to handle discrete GPU binding via vfio-pci (exports `gpu_pci_devices` or
  equivalent for gaming hosts)
- Gaming hardware with IOMMU support (VT-d/AMD-Vi **REQUIRED** in BIOS —
  `proxmox_pci_passthrough` hard-fails if IOMMU is not active or groups are
  invalid)
- Windows 11 ISO or image in `images/` directory
- virtio-win ISO in `images/` directory
- `gaming_vm_id: 600` already in `group_vars/all.yml`
- `gaming_nodes` flavor group and `gaming` dynamic group already in `inventory/hosts.yml`

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle` | Two-role pattern, VM provisioning, deploy_stamp, cleanup completeness, image management |
| `ansible-testing` | Molecule scenarios, verify assertions, baseline workflow |
| `rollback-patterns` | Per-feature rollback tags, deploy_stamp tracking, cleanup.yml conventions |
| `proxmox-host-safety` | Discrete GPU passthrough, IOMMU group validation, safe host commands, PCI cleanup |
| `multi-node-ssh` | ProxyJump for testing on LAN nodes (if gaming rig is on LAN) |
| `build-conventions` | Entry point patterns for separate build profile |
| `project-planning` | Milestone structure, verify/rollback sections |

---

## Architectural Decisions

```
Decisions
├── Gaming OS: Windows 11
│   └── Required for AAA game compatibility; DirectX 12; widest game library
│
├── Image management: Windows ISO + autounattend.xml (install-from-media)
│   └── Windows is inherently install-from-ISO. This is NOT an exception to the bake
│       principle — it IS the bake approach for Windows. The ISO + autounattend.xml
│       produce a deterministic, unattended install with drivers pre-injected.
│
├── Streaming server: Sunshine
│   └── Open-source, cross-platform Moonlight host; replaces NVIDIA GeForce Experience
│
├── GPU passthrough: full discrete GPU via vfio-pci (NOT iGPU)
│   ├── Guest gets native GPU performance; host uses CPU-only rendering
│   ├── proxmox_pci_passthrough handles discrete GPU binding (same pattern as WiFi)
│   └── q35 machine type required for PCI passthrough; OVMF UEFI for Windows
│
├── IOMMU: REQUIRED — hard-fail if not active
│   ├── proxmox_pci_passthrough hard-fails if IOMMU is not active or groups invalid
│   ├── Gaming rig MUST have VT-d (Intel) or AMD-Vi enabled in BIOS
│   └── No graceful skip — masks fixable BIOS settings
│
├── Audio: virtual audio (PulseAudio or PipeWire) streamed through Sunshine
│   └── No physical audio out needed on gaming rig; Moonlight client handles playback
│
├── Looking Glass: deferred (not in scope for initial build)
│   └── Adds complexity; Sunshine covers streaming use case; revisit if local display needed
│
├── Configuration method: WinRM or SSH
│   └── gaming_configure connects via WinRM (ansible.windows) or SSH (OpenSSH on Windows)
│
└── Monitoring: provisioned by monitoring projects
    └── Gaming host joins monitoring_nodes; Netdata + rsyslog added by those projects
```

---

## Testing Strategy

### Parallelism in `molecule/default` (full integration)

The gaming rig is **separate physical hardware**. When gaming hardware is
available, it appears as a separate platform in `molecule/default/molecule.yml`
(conditional). When unavailable, gaming plays are skipped and only role
syntax and cleanup logic are validated.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/gaming-vm/` which requires actual
gaming hardware. The per-feature scenario provisions and configures only
VMID 600.

```
Scenario Hierarchy (Gaming Rig additions)
├── molecule/default/                 Full integration (requires gaming hardware)
│   └── Runs gaming provision + configure on gaming_nodes host
│
└── molecule/gaming-vm/              Gaming VM only (requires gaming hardware)
    ├── converge: provision + configure Gaming VM
    ├── verify: VM assertions (GPU passthrough, Sunshine, Windows)
    └── cleanup: destroy VM 600 + PCI cleanup (restore GPU to host)
```

### Hardware dependency

| Hardware state | What runs | What's validated |
|---------------|-----------|------------------|
| Gaming hardware present | Full converge + verify + cleanup | VM state, GPU passthrough, Sunshine, Windows config |
| No gaming hardware | Syntax check, cleanup logic | Role structure, variable templating, cleanup correctness |

### Day-to-day workflow

```bash
# Only when gaming hardware is available:
molecule converge -s gaming-vm                # ~5 min, provision + configure
molecule verify -s gaming-vm                  # ~30s, assertions
molecule cleanup -s gaming-vm                 # destroys VM 600, PCI cleanup

# Final validation (all hardware present):
molecule test                                 # full clean-state
```

---

## Milestone Dependency Graph

```
M1: Image & Driver Prep ─── self-contained
 └── M2: VM Provisioning ─── depends on M1, shared infra (proxmox_pci_passthrough)
      └── M3: Windows Config ─ depends on M2
           └── M4: Backup Strategy ─ depends on M2
                └── M5: Streaming Verify ─ depends on M3
                     └── M6: Monitoring ─── depends on monitoring projects (host group only)
                          └── M7: Testing & Integration ── depends on M2–M5
                               └── M8: Documentation ── depends on M1–M7
```

---

## Milestones

### Milestone 1: Image & Driver Preparation

_Self-contained. No external dependencies._

Download and stage Windows 11 ISO, virtio-win ISO, and create autounattend.xml.
All images stored locally in `images/` — never downloaded from Proxmox host.

See: `vm-lifecycle` skill (image management, local images/ directory).

**Implementation pattern:**
- Variables: `group_vars/all.yml` — `gaming_image_path`, `gaming_virtio_iso_path`
- Template: `roles/gaming_vm/templates/autounattend.xml.j2` (or `roles/gaming_vm/files/`)
- Env: `WINDOWS_PRODUCT_KEY` (optional) via `lookup('env', 'WINDOWS_PRODUCT_KEY') | default('', true)` in role defaults

- [ ] Download Windows 11 ISO (or create custom image) into `images/`
- [ ] Download virtio-win ISO (Red Hat) for storage and network drivers into `images/`
- [ ] Create `autounattend.xml` template:
  - License acceptance, partition layout, driver injection, admin user
  - Product key from `.env` (`WINDOWS_PRODUCT_KEY`) when set
- [ ] Add `gaming_image_path`, `gaming_virtio_iso_path` to `group_vars/all.yml`
- [ ] Document image download URLs in setup instructions

**Verify:**

- [ ] `images/` contains Windows 11 ISO (or image) at path in `gaming_image_path`
- [ ] `images/` contains virtio-win ISO at path in `gaming_virtio_iso_path`
- [ ] `autounattend.xml` template renders with optional product key
- [ ] Paths use `role_path` or `playbook_dir` for molecule compatibility

**Rollback:** Remove added vars from `group_vars/all.yml`; delete images manually if desired.

---

### Milestone 2: VM Provisioning

_Depends on M1 (images ready). Requires `proxmox_pci_passthrough` extended
for discrete GPU._ Add the provision and configure plays to `site.yml`.
Integration is consolidated here.

Create the `gaming_vm` role: q35 machine type, OVMF UEFI, virtio-scsi, discrete
GPU via hostpci. The GPU PCI address comes from `proxmox_pci_passthrough`
(gpu_pci_devices or equivalent — role must support discrete GPU detection on
gaming hosts).

See: `vm-lifecycle` skill (two-role pattern, qm create, add_host, deploy_stamp).
See: `proxmox-host-safety` skill (discrete GPU passthrough, IOMMU validation, q35).

**Implementation pattern:**
- Role: `roles/gaming_vm/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `gaming_nodes`, tagged `[gaming]`,
  in the appropriate phase for the gaming host
- deploy_stamp included as last role in the provision play
- Dynamic group `gaming` populated via `add_host` (WinRM/SSH connection vars)

**Already complete** (from shared infrastructure / inventory):
- `gaming_vm_id: 600` in `group_vars/all.yml`
- `gaming_nodes` flavor group and `gaming` dynamic group in `inventory/hosts.yml`

- [ ] Create `roles/gaming_vm/defaults/main.yml`:
  - `gaming_vm_id: 600`, `gaming_vm_memory: 8192` (or 16384), `gaming_vm_cores: 8`
  - `gaming_vm_disk: 128G`
  - `gaming_vm_onboot: true`, `gaming_vm_startup_order: 1`
  - `gaming_vm_machine: q35`, `gaming_vm_bios: ovmf`
- [ ] Create `roles/gaming_vm/tasks/main.yml`:
  - Check if VM exists (`qm status`); skip creation if present
  - `qm create` with q35 machine type, OVMF UEFI, virtio-scsi
  - Import Windows disk image (or attach ISO + autounattend for install)
  - Attach virtio-win ISO as second CD-ROM
  - Configure `hostpci0` for discrete GPU (PCI address from `proxmox_pci_passthrough` gpu_pci_devices)
  - Attach NIC on WAN bridge (virtio model) — gaming rig has no OpenWrt
  - CPU: host model, hidden KVM, `topoext` for AMD (or appropriate Intel flags)
  - Set `--onboot 1 --startup order=1` (unconditional, self-healing)
  - Start VM
  - Register in `gaming` dynamic group via `add_host` with WinRM/SSH connection vars
- [ ] Create `roles/gaming_vm/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` targeting `gaming_nodes`, tagged `[gaming]`,
  with `gaming_vm` role and `deploy_stamp`
- [ ] Add configure play to `site.yml` targeting `gaming` dynamic group,
  tagged `[gaming]`, after provision play
- [ ] Create `tasks/reconstruct_gaming_group.yml`:
  - Verify VM 600 is running (`qm status {{ gaming_vm_id }}`)
  - Register via `add_host` with WinRM/SSH connection vars,
    `ansible_host` (VM IP or Proxmox + port forward)
- [ ] Extend `proxmox_pci_passthrough` for discrete GPU detection on gaming hosts if not present:
  - Detect VGA/3D controllers (NVIDIA, AMD); export `gpu_pci_devices` fact
  - Same IOMMU validation, vfio-pci binding pattern as WiFi
  - Hard-fail if IOMMU not active or groups invalid

**Verify:**

- [ ] VM 600 is running: `qm status 600` returns `running`
- [ ] VM is in `gaming` dynamic group (`add_host` registered)
- [ ] GPU unbinds from host and binds to vfio-pci when VM starts
- [ ] Auto-start configured: `qm config 600` shows `onboot: 1`, `startup: order=1`
- [ ] Machine type is q35: `qm config 600` shows `machine: q35`
- [ ] OVMF UEFI: `qm config 600` shows `bios: ovmf`
- [ ] hostpci0 set to discrete GPU PCI address
- [ ] Idempotent: re-run skips creation, VM still running
- [ ] deploy_stamp contains `gaming_vm` play entry

**Rollback:**

VM destruction: generic `qm list` iteration in `molecule/default/cleanup.yml` and
`playbooks/cleanup.yml` — `qm stop` + `qm destroy`. PCI cleanup: vfio-pci unbind,
remove modprobe blacklist/vfio config for GPU, reload original driver
(`modprobe -r vfio_pci && modprobe <nvidia|amdgpu>`), rescan PCI bus. Add GPU
cleanup to BOTH cleanup playbooks. See: `proxmox-host-safety` skill (PCI device
cleanup after passthrough).

---

### Milestone 3: Windows Configuration

_Depends on M2 (VM running)._

Configure the Windows 11 guest via WinRM or SSH: virtio drivers, GPU drivers,
Sunshine, optional Steam, Windows Update policy, RDP.

See: `vm-lifecycle` skill (configure role, dynamic group targeting).

**Implementation pattern:**
- Role: `roles/gaming_configure/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: configure play targeting `gaming` dynamic group, tagged `[gaming]`
- Connection: `ansible.windows.winrm` or `ansible.builtin.ssh` (OpenSSH on Windows)
- Env: `SUNSHINE_USER`, `SUNSHINE_PASSWORD` (required for streaming); `WINDOWS_PRODUCT_KEY` (optional)

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `WINDOWS_PRODUCT_KEY` | no | Windows 11 license (omit for eval/trial) | `XXXXX-XXXXX-...` |
| `SUNSHINE_USER` | yes | Sunshine web UI / pairing username | `gamer` |
| `SUNSHINE_PASSWORD` | yes | Sunshine web UI / pairing password | `secret` |

- [ ] Create `roles/gaming_configure/defaults/main.yml`:
  - `SUNSHINE_USER`, `SUNSHINE_PASSWORD` via `lookup('env', ...)` — required (fail if empty)
  - `WINDOWS_PRODUCT_KEY` via `lookup('env', ...) | default('', true)` — optional
- [ ] Create `roles/gaming_configure/tasks/main.yml` (via WinRM or SSH):
  - Install virtio guest agent (from virtio-win ISO or package)
  - Install GPU drivers (NVIDIA or AMD, from package or URL)
  - Install Sunshine:
    - Download latest release
    - Configure: username/password from `.env` (`SUNSHINE_USER`, `SUNSHINE_PASSWORD`)
    - Set streaming quality defaults (1080p60 or 4K60 depending on GPU)
  - Install Steam (optional, via winget or Chocolatey)
  - Disable Windows Update auto-restart (gaming interruption prevention)
  - Enable RDP for backup remote access
- [ ] Create `roles/gaming_configure/meta/main.yml` with required metadata

**Verify:**

- [ ] WinRM or SSH connection works: `ansible.builtin.ping` (or win_ping) succeeds
- [ ] Virtio guest agent installed and running
- [ ] GPU drivers installed (nvidia-smi or AMD equivalent)
- [ ] Sunshine installed and configured with credentials
- [ ] Sunshine web UI accessible on LAN
- [ ] Windows Update auto-restart disabled
- [ ] RDP enabled
- [ ] Idempotent: second run does not reinstall or reconfigure unnecessarily

**Rollback:**

- Uninstall Sunshine, revert Windows Update policy, disable RDP (task-specific rollback)
- Full VM destruction is the escape hatch (M2 rollback)

---

### Milestone 4: Backup & Snapshot Strategy

_Depends on M2 (VM exists)._

Configure vzdump schedule, exclude game data, document restore procedure.

See: `rollback-patterns` skill (backup before changes).

- [ ] Configure vzdump schedule for Windows VM (via Proxmox or cron)
- [ ] Exclude game data directories from backup (large, re-downloadable)
- [ ] Create snapshot before major driver updates (document procedure)
- [ ] Document restore procedure in `docs/architecture/gaming-build.md`

**Verify:**

- [ ] vzdump schedule exists and targets VMID 600
- [ ] Exclusion list documented
- [ ] Restore procedure documented and tested

**Rollback:** Remove vzdump schedule; no other persistent changes.

---

### Milestone 5: Streaming Verification

_Depends on M3 (Sunshine configured)._

Verify Sunshine web UI, Moonlight pairing, streaming quality, wake-on-suspend.

- [ ] Verify Sunshine web UI accessible on LAN
- [ ] Pair Moonlight client (project 10) with Sunshine
- [ ] Test streaming: latency, resolution, controller input
- [ ] Verify Moonlight can wake VM if suspended

**Verify:**

- [ ] Sunshine web UI responds on expected port
- [ ] Moonlight client can discover and pair with Sunshine
- [ ] Streaming test passes (manual or automated)
- [ ] Wake-on-suspend documented

**Rollback:** N/A — verification only.

---

### Milestone 6: Monitoring Integration

_Blocked on: monitoring projects (Netdata, rsyslog)._

Gaming rig host joins `monitoring_nodes` flavor group. Netdata + rsyslog are
provisioned by the monitoring projects — this milestone only ensures the host
is in the group and documents what gets monitored.

- [ ] Gaming rig host joins `monitoring_nodes` flavor group in `inventory/hosts.yml`
- [ ] Document: GPU temperature, fan speed, streaming sessions, VM health
- [ ] Netdata + rsyslog containers provisioned by monitoring projects (not this project)

**Verify:**

- [ ] Gaming host appears in `monitoring_nodes` group
- [ ] When monitoring projects run, Netdata + rsyslog deploy to gaming host
- [ ] Documentation lists monitored metrics

**Rollback:** Remove host from `monitoring_nodes`; monitoring containers removed by their cleanup.

---

### Milestone 7: Testing & Integration

_Depends on M2–M5._

Wire up molecule testing, cleanup completeness, and final validation.

See: `vm-lifecycle` skill (site.yml plays, flavor group, deploy_stamp).
See: `ansible-testing` skill (verify assertions, cleanup completeness).
See: `build-conventions` skill (entry point, tags).

#### 7a. Per-feature scenario: `molecule/gaming-vm/`

- [ ] Create `molecule/gaming-vm/molecule.yml`:
  ```yaml
  platforms:
    - name: gaming-host
      groups:
        - proxmox
        - gaming_nodes
  provisioner:
    env:
      GAMING_HOST_API_TOKEN: ${GAMING_HOST_API_TOKEN}
      SUNSHINE_USER: ${SUNSHINE_USER:-}
      SUNSHINE_PASSWORD: ${SUNSHINE_PASSWORD:-}
      WINDOWS_PRODUCT_KEY: ${WINDOWS_PRODUCT_KEY:-}
  scenario:
    test_sequence:
      - dependency
      - syntax
      - converge
      - verify
      - cleanup
  ```

- [ ] Create `molecule/gaming-vm/converge.yml`
- [ ] Create `molecule/gaming-vm/verify.yml`
- [ ] Create `molecule/gaming-vm/cleanup.yml`:
  Destroys VM 600 and runs PCI cleanup.

#### 7b. Full integration (`molecule/default/`)

- [ ] Add `gaming_nodes` to `molecule/default/molecule.yml` platform groups
  (conditional on gaming hardware availability)
- [ ] Extend `molecule/default/verify.yml` with Gaming VM assertions
  (when hardware present):
  - VM 600 running, onboot=1, startup order=1
  - GPU passthrough configured, machine type q35, OVMF UEFI
  - Sunshine accessible, deploy_stamp present

- [ ] Extend `molecule/default/cleanup.yml` and `playbooks/cleanup.yml`:
  - VM: `qm list` iteration → `qm stop` + `qm destroy` for VMID 600
  - PCI: vfio-pci unbind, remove blacklist/vfio config, reload driver, rescan PCI

#### 7c. Rollback plays in `playbooks/cleanup.yml`

- [ ] Add `gaming-rollback` play:
  ```yaml
  - name: Rollback Gaming VM
    hosts: gaming_nodes
    gather_facts: false
    tags: [gaming-rollback, never]
    tasks:
      - name: Stop and destroy Gaming VM
        ansible.builtin.shell:
          cmd: |
            qm stop {{ gaming_vm_id }} 2>/dev/null || true
            sleep 3
            qm destroy {{ gaming_vm_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true

      - name: PCI cleanup - restore GPU to host
        ansible.builtin.shell:
          cmd: |
            set -o pipefail
            echo 1 > /sys/bus/pci/rescan
          executable: /bin/bash
        changed_when: true
  ```

#### 7d. Final validation

- [ ] Full `molecule test` passes when gaming hardware present (exit code 0)
- [ ] Without gaming hardware: role syntax valid, cleanup playbooks handle missing VM
- [ ] `ansible-lint && yamllint .` passes
- [ ] Cleanup leaves no gaming artifacts; PCI devices unbound from vfio-pci

**Rollback:** Revert site.yml plays, molecule config; cleanup restores baseline.

---

### Milestone 8: Documentation

_Depends on M1–M7._

- [ ] Create `docs/architecture/gaming-build.md`:
  - Image management (Windows ISO + autounattend.xml, virtio-win)
  - Requirements, design decisions, env variables
  - Discrete GPU passthrough (NOT iGPU), IOMMU requirements, q35/OVMF
  - Sunshine setup, Moonlight pairing
  - Separate build profile and hardware topology
  - Testing strategy (hardware-dependent)
- [ ] Update `docs/architecture/overview.md`:
  - site.yml diagram: add Gaming provision + configure plays
  - Gaming Rig section: discrete GPU, separate build profile
- [ ] Update `docs/architecture/roles.md`:
  - Add `gaming_vm` role documentation (purpose, discrete GPU, key variables)
  - Add `gaming_configure` role documentation (purpose, WinRM/SSH, Sunshine)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Gaming Rig project to Active Projects section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All env variables documented: `WINDOWS_PRODUCT_KEY`, `SUNSHINE_USER`, `SUNSHINE_PASSWORD`
- [ ] IOMMU hard-fail, PCI cleanup, separate hardware topology documented

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Moonlight client**: The Moonlight client (project 10) on the Home
  Entertainment Box streams from this Gaming Rig's Sunshine server.
  Pairing requires both services operational on the LAN.
- **Monitoring**: The gaming rig host joins `monitoring_nodes`. Netdata
  and rsyslog are provisioned by the monitoring projects — GPU temperature,
  fan speed, and streaming session metrics are collected automatically.
- **Looking Glass**: If local display output is needed (monitor directly
  connected to gaming rig), Looking Glass can provide low-latency shared
  memory display. Deferred to future enhancement.
- **Multi-GPU**: If the gaming rig has multiple discrete GPUs, the
  `proxmox_pci_passthrough` role must be extended to select the correct
  GPU for passthrough. Currently assumes a single discrete GPU.

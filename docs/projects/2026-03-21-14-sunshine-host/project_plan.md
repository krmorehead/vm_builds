# Sunshine Streaming Server

## Overview

A Windows 11 VM with iGPU PCI passthrough running Sunshine as a Moonlight
streaming host. This is an isolated, testable component of the broader
Gaming Rig build (project 13). Uses iGPU PCI passthrough for hardware
encoding on all 4 test nodes. In production, this deploys to `gaming_nodes`
(dedicated gaming hardware) where the Gaming Rig project extends it with
discrete GPU passthrough.

## Type

VM (KVM/QEMU) — Windows 11

## Resources

- Cores: 4 (testing), 4–8 (production — set via host_vars)
- RAM: 4096 MB (testing), 8192–16384 MB (production)
- Disk: 64 GB (testing with Doom), 128+ GB (production with game library)
- GPU: iGPU PCI passthrough via vfio-pci (testing); discrete GPU
  (production, Gaming Rig project)
- Network: management bridge (DHCP), auto-detected via host default route
- VMID: 600

## Hardware Requirements

```
Minimum Hardware (testing — all 4 test nodes qualify)
├── CPU: Intel or AMD with integrated GPU (iGPU)
├── RAM: 8+ GB (4 GB for VM + host overhead)
├── Storage: 64+ GB free on local-lvm
├── iGPU: Intel (i915) or AMD (amdgpu) — detected by proxmox_igpu
├── IOMMU: VT-d (Intel) or AMD-Vi REQUIRED in BIOS
└── iGPU must be in its own IOMMU group (typically group 0 or 1)
```

## Startup

- Auto-start: yes (`onboot: 1`, `startup order: 1`)
- Boot priority: 1 (primary service on gaming rig)
- Depends on: Proxmox host, iGPU available, IOMMU active

## Build Profiles

- Home Entertainment Box: no (iGPU used by Jellyfin/Kodi containers)
- Minimal Router: no
- Gaming Rig: yes (core) — this project creates the foundation

## Network topology assumption

`gaming_nodes` connects directly to the upstream network in production.
For testing on all 4 nodes, the VM uses the host's management bridge
(auto-detected via default route):

- WAN hosts (home, ai, mesh2): VM on WAN bridge, DHCP from ISP router
- LAN hosts (mesh1): VM on LAN bridge, DHCP from OpenWrt

There is no LAN/WAN branching in the role — the VM always attaches to the
host's management bridge (`proxmox_wan_bridge`), which is correct for both
topologies.

## Prerequisites

- Shared infrastructure: `proxmox_igpu` role (project 00) — detects iGPU,
  exports `igpu_pci_address`, `igpu_vendor`
- IOMMU enabled in BIOS on all target hosts (VT-d / AMD-Vi — hard-fail if
  not active)
- `gaming_vm_id: 600` already in `group_vars/all.yml`
- `gaming_nodes` flavor group and `gaming` dynamic group already in
  `inventory/hosts.yml`
- Proxmox VE with `qm` CLI available
- Windows 11 evaluation ISO in `images/` directory
- virtio-win ISO in `images/` directory

## Skills

| Skill | When to use |
|-------|-------------|
| `vm-lifecycle-architecture` | Two-role pattern, VM provisioning, deploy_stamp, cleanup completeness |
| `vm-provisioning-patterns` | qm create, add_host, dynamic groups, existence check |
| `image-management-patterns` | Image management, local images/ directory |
| `proxmox-system-safety` | iGPU passthrough, IOMMU validation, vfio-pci binding, PCI cleanup |
| `molecule-testing` | Molecule scenarios, verify assertions, baseline workflow |
| `rollback-architecture` | Per-feature rollback tags, deploy_stamp tracking, cleanup conventions |
| `project-planning-structure` | Milestone structure, verify/rollback sections |
| `lan-ssh-patterns` | ProxyJump for testing on mesh1 (LAN node) |

---

## Architectural Decisions

```
Decisions
├── Gaming OS: Windows 11 (evaluation for testing)
│   ├── Required for Sunshine + DirectX game compatibility
│   └── ISO + autounattend.xml = "bake" approach for Windows (documented exception)
│
├── Image management: Pre-built Windows disk image via build-images.sh
│   ├── Build once: ISO + autounattend → install → drivers → Sunshine → sysprep → export
│   ├── Deploy fast: qm importdisk from pre-built qcow2 (~2-3 min vs ~30 min ISO install)
│   ├── Same cache-and-reuse pattern as LXC template builds
│   └── Build VMID 992 (temp VM, destroyed after build)
│
├── GPU passthrough: iGPU via vfio-pci (NOT discrete GPU)
│   ├── All 4 test nodes have iGPUs; no discrete GPUs available for testing
│   ├── proxmox_igpu detects PCI address; gaming_vm binds to vfio-pci
│   ├── Host loses iGPU — acceptable on headless servers
│   ├── IOMMU REQUIRED — hard-fail if not active (same as WiFi passthrough)
│   └── Gaming Rig project (13) extends to discrete GPU passthrough (separate hardware)
│
├── VM configuration: q35 machine type, OVMF UEFI
│   ├── q35 required for PCI passthrough
│   ├── OVMF for Windows 11 Secure Boot compatibility
│   └── virtio-scsi for storage, virtio for network
│
├── Streaming server: Sunshine
│   ├── Open-source Moonlight host; replaces NVIDIA GameStream
│   ├── Hardware encoding via iGPU VA-API/QuickSync (Intel) or AMF (AMD)
│   └── Web UI for management (port 47990), streaming ports 47984-47989
│
├── Test game: GZDoom + Freedoom
│   ├── Free, open-source Doom engine + compatible IWAD (no licensing issues)
│   ├── GPU-accelerated rendering validates iGPU passthrough works end-to-end
│   ├── Lightweight enough for any iGPU (Intel or AMD)
│   └── "Classic honor" — Doom is the canonical GPU test
│
├── Ansible connection: OpenSSH on Windows
│   ├── Built into Windows 11, enabled via autounattend.xml
│   ├── Same SSH infrastructure as rest of project
│   └── Avoids WinRM certificate/authentication complexity
│
├── VM IP discovery: QEMU Guest Agent
│   ├── virtio-win includes guest agent installer
│   ├── Installed during image build (autounattend.xml)
│   ├── qm guest cmd <vmid> network-get-interfaces returns IP
│   └── Role discovers IP and registers via add_host
│
└── Mutually exclusive with proxmox_igpu host use
    ├── iGPU bound to vfio-pci → host loses GPU acceleration
    ├── Cannot coexist with Jellyfin/Kodi containers (need host iGPU)
    ├── Per-feature scenario tests Sunshine in isolation
    └── Full integration test does NOT include gaming_nodes (no conflict)
```

---

## Testing Strategy

### All 4 test nodes

The per-feature scenario `molecule/sunshine-vm/` runs on ALL 4 test nodes
(home, mesh1, ai, mesh2). Each node creates VM 600 with its iGPU passed
through via vfio-pci.

```
molecule/sunshine-vm/ (all 4 nodes, ~30-45 min first run)
├── home  — Intel iGPU (i915), WAN bridge
├── mesh1 — Intel iGPU (i915), LAN bridge (behind OpenWrt, ProxyJump)
├── ai    — AMD iGPU (amdgpu), WAN bridge
└── mesh2 — Intel iGPU (i915), WAN bridge
```

### Baseline dependency

mesh1 is a LAN host behind OpenWrt — requires the OpenWrt baseline running
for network connectivity. Run `molecule converge` (default scenario) first
to establish the baseline, then run the Sunshine per-feature scenario.

### Mutual exclusivity with media containers

iGPU passthrough (vfio-pci) is mutually exclusive with host iGPU use
(i915/amdgpu bind mounts for Jellyfin/Kodi). The per-feature scenario
runs Sunshine in ISOLATION — no other services that need host iGPU are
deployed during this test.

The full integration test (`molecule/default/`) does NOT include
`gaming_nodes` — gaming is separate hardware in production. iGPU
remains available to media containers in the default scenario.

### Per-feature scenarios (fast iteration)

Day-to-day development uses `molecule/sunshine-vm/` which provisions VM 600
on all 4 nodes. The OpenWrt baseline and other containers stay running (no
conflict because Sunshine uses vfio-pci, not bind mounts).

```
Scenario Hierarchy (Sunshine additions)
├── molecule/default/               Full integration (no gaming — separate hardware)
│   └── gaming_nodes NOT included (no iGPU conflict with media containers)
│
└── molecule/sunshine-vm/           Sunshine VM on all 4 nodes (~30-45 min)
    ├── converge: proxmox_igpu → gaming_vm → gaming_configure
    ├── verify: VM running, GPU passthrough, Sunshine web UI, Doom installed
    └── cleanup: destroy VM 600, unbind vfio-pci, reload GPU driver
```

### Day-to-day workflow

```bash
# 1. Ensure baseline is running (for mesh1 access)
molecule converge                             # ~4-5 min, all 4 nodes

# 2. Build Windows image (one-time or after changes)
./build-images.sh --host $PRIMARY_HOST --only sunshine

# 3. Run Sunshine per-feature scenario
molecule test -s sunshine-vm                  # ~30-45 min, all 4 nodes

# Or iterate:
molecule converge -s sunshine-vm              # ~15-20 min (import pre-built image)
molecule verify -s sunshine-vm                # ~30s, assertions
molecule cleanup -s sunshine-vm               # ~2 min, VM destroy + PCI cleanup
```

### What each scenario validates

| Node | iGPU | Bridge | Validates |
|------|------|--------|-----------|
| home | Intel (i915) | WAN | Intel iGPU passthrough, Sunshine encoding, GZDoom |
| mesh1 | Intel (i915) | LAN (OpenWrt) | LAN topology, ProxyJump, Intel iGPU |
| ai | AMD (amdgpu) | WAN | AMD iGPU passthrough, AMD driver compatibility |
| mesh2 | Intel (i915) | WAN | Multi-node Intel iGPU consistency |

### What each scenario tears down

| Scenario | Creates | Destroys | Baseline impact |
|----------|---------|----------|-----------------|
| `default` (test) | Everything except gaming | Everything except gaming | Full rebuild required after |
| `default` (converge) | Everything except gaming | Nothing | Baseline preserved |
| `sunshine-vm` | VM 600 on all 4 nodes | VM 600 + PCI cleanup on all 4 | None — OpenWrt, containers untouched |

### Testing limitations

Testing verifies the full Sunshine stack (VM creation, GPU passthrough,
Sunshine service, GZDoom installation) but does NOT test actual game
streaming to a Moonlight client. Moonlight pairing requires the Moonlight
client project (project 10) to be implemented.

Discrete GPU passthrough is deferred to the Gaming Rig project (project 13)
on dedicated gaming hardware.

---

## Milestone Dependency Graph

```
M0: Image Build ────── self-contained
 └── M1: VM Provisioning ── depends on M0, proxmox_igpu
      └── M2: Sunshine Config ── depends on M1
           └── M3: Testing & Integration ── depends on M1–M2
                └── M4: Documentation ── depends on M1–M3
```

---

## Milestones

### Milestone 0: Image Build

_Self-contained. No external dependencies._

Build a pre-installed Windows 11 disk image with virtio drivers, QEMU Guest
Agent, OpenSSH Server, iGPU drivers, Sunshine, and GZDoom + Freedoom
pre-installed. The image is built once on a Proxmox host and reused for
every test run (~2-3 min import vs ~30 min ISO install).

Per the project's "bake" principle: Windows ISO + autounattend.xml is the
documented exception for Windows VMs. The image build produces a
deterministic, fully configured disk image.

See: `image-management-patterns` skill.

**Implementation pattern:**
- Script: add `build_sunshine_vm` section to `build-images.sh`
- Image path: `images/sunshine-win11-amd64.qcow2`
- Template vars: `gaming_image_path` and `gaming_virtio_iso_path`
  in `group_vars/all.yml`

**Build approach (remote on Proxmox):**
1. Upload Windows 11 evaluation ISO and virtio-win ISO to Proxmox host
2. Create temp VM (VMID 992) with q35, OVMF, 64GB disk, 4GB RAM, 4 cores
3. Attach Windows ISO (ide2) and virtio-win ISO (ide3)
4. Inject autounattend.xml via floppy image or secondary ISO
5. Boot and wait for unattended installation
6. autounattend.xml handles:
   - UEFI/GPT partition layout, license acceptance
   - virtio storage and network driver injection from virtio-win
   - QEMU Guest Agent installation
   - OpenSSH Server capability enablement
   - Administrator password and auto-login for initial setup
   - Product key from `.env` (`WINDOWS_PRODUCT_KEY`) when set
7. Post-install via PowerShell (scripted in autounattend FirstLogonCommands):
   - iGPU drivers (Intel and AMD — generic driver packages for portability)
   - Sunshine (from GitHub releases, latest stable)
   - GZDoom + Freedoom WADs (portable zip extract)
   - Windows Firewall rules for Sunshine ports (47984-47990)
8. Sysprep (generalize) the installation
9. Shut down VM and export disk:
   `cp /var/lib/vz/images/992/vm-992-disk-0.qcow2 /tmp/`
10. Download to controller: `scp` to `images/sunshine-win11-amd64.qcow2`
11. Cleanup: destroy temp VM 992

**autounattend.xml key settings:**
- `<DiskConfiguration>`: single UEFI/GPT partition, 64GB
- `<UserAccounts>`: Administrator with default password
- `<FirstLogonCommands>`: enable OpenSSH, install QEMU GA, run post-install
  PowerShell script
- `<AutoLogon>`: enabled for initial setup
- Product key: from `WINDOWS_PRODUCT_KEY` env var if set, else evaluation mode

- [ ] Download Windows 11 evaluation ISO into `images/` (or document URL)
- [ ] Download virtio-win ISO into `images/` (or document URL)
- [ ] Create `autounattend.xml` template in `roles/gaming_vm/files/`
- [ ] Create post-install PowerShell script for Sunshine, GZDoom, drivers
- [ ] Add `build_sunshine_vm` function to `build-images.sh`
- [ ] Add `gaming_image_path`, `gaming_virtio_iso_path` to `group_vars/all.yml`
- [ ] Build image on test node and verify

**Verify:**

- [ ] Image file exists at `images/sunshine-win11-amd64.qcow2`
- [ ] Image boots in test VM: Windows reaches desktop
- [ ] OpenSSH Server is running and accessible
- [ ] QEMU Guest Agent responds to `qm guest cmd`
- [ ] Sunshine is installed and service is registered
- [ ] GZDoom + Freedoom WADs are present
- [ ] Windows Firewall rules allow Sunshine ports (47984-47990)

**Rollback:**

Delete image from `images/`, revert `group_vars/all.yml` and
`build-images.sh` additions via git.

---

### Milestone 1: VM Provisioning + iGPU Passthrough

_Depends on M0 (image must be built). Blocked on: `proxmox_igpu` for iGPU
detection (runs in infrastructure plays before gaming)._

Create the `gaming_vm` role: detect iGPU PCI address, validate IOMMU, bind
to vfio-pci, create q35/OVMF VM with the pre-built Windows disk image, pass
iGPU via hostpci, discover VM IP, register in dynamic group. Add provision
and configure plays to `site.yml`.

See: `vm-provisioning-patterns` skill (qm create, add_host, deploy_stamp).
See: `proxmox-system-safety` skill (IOMMU validation, vfio-pci binding, q35).

**Implementation pattern:**
- Role: `roles/gaming_vm/defaults/main.yml`, `tasks/main.yml`, `meta/main.yml`
- site.yml: provision play targeting `gaming_nodes`, tagged `[gaming, never]`
  (opt-in via `--tags gaming`), in Phase 3 after media services
- deploy_stamp included as last role in the provision play
- Dynamic group `gaming` populated via `add_host` (SSH connection vars)

**iGPU passthrough flow:**
1. Read `igpu_pci_address` from `proxmox_igpu` facts (set during infrastructure).
   Hard-fail if `igpu_pci_address` is not defined — `proxmox_igpu` must run
   first (the per-feature converge runs it explicitly).
2. Validate IOMMU is active: check `/sys/class/iommu/` is non-empty.
   Hard-fail if IOMMU not active — masks fixable BIOS settings.
3. Validate iGPU IOMMU group is isolated (single device in group, no shared
   bridges that would require ACS override).
4. Unbind from host driver:
   `echo <pci_addr> > /sys/bus/pci/devices/<pci_addr>/driver/unbind`
5. Bind to vfio-pci:
   `echo <vendor_id> <device_id> > /sys/bus/pci/drivers/vfio-pci/new_id`
6. Verify binding:
   `readlink /sys/bus/pci/devices/<pci_addr>/driver` → `vfio-pci`

**VM creation:**
- `qm create 600`:
  - `--machine q35 --bios ovmf --efidisk0 local-lvm:0,format=qcow2`
  - `--cores 4 --memory 4096 --cpu host,hidden=1`
  - `--scsihw virtio-scsi-pci --scsi0 local-lvm:0,import-from=<image_path>`
  - `--hostpci0 <igpu_pci_addr>,rombar=0`
  - `--net0 virtio,bridge=<mgmt_bridge>`
  - `--onboot 1 --startup order=1`
  - `--agent enabled=1` (QEMU Guest Agent)
- Start VM
- Wait for QEMU Guest Agent response
- Discover IP via `qm guest cmd 600 network-get-interfaces`
- Register in `gaming` dynamic group via `add_host` with:
  `ansible_connection: ssh`, `ansible_host: <vm_ip>`,
  `ansible_user: Administrator`, `ansible_ssh_pass: <default_password>`

**Already complete** (from shared infrastructure / inventory):
- `gaming_vm_id: 600` in `group_vars/all.yml`
- `gaming_nodes` flavor group and `gaming` dynamic group in `inventory/hosts.yml`

- [ ] Create `roles/gaming_vm/defaults/main.yml`:
  - `gaming_vm_memory: 4096`, `gaming_vm_cores: 4`
  - `gaming_vm_disk: "64"`, `gaming_vm_machine: q35`, `gaming_vm_bios: ovmf`
  - `gaming_vm_onboot: true`, `gaming_vm_startup_order: 1`
  - `gaming_vm_image: "{{ gaming_image_path }}"` (pre-built Windows image)
  - `gaming_vm_admin_password` via `lookup('env', ...) | default('Passw0rd!', true)`
- [ ] Create `roles/gaming_vm/tasks/main.yml`:
  - Verify image exists, hard-fail with message pointing to `./build-images.sh`
  - Assert `igpu_pci_address` is defined (hard-fail if proxmox_igpu hasn't run)
  - Validate IOMMU active and iGPU group isolated
  - Bind iGPU to vfio-pci
  - Check if VM exists (`qm status`); skip creation if present
  - `qm create` with q35, OVMF, import disk, hostpci0, virtio-net, agent
  - Set `--onboot 1 --startup order=1` (unconditional, self-healing)
  - Start VM
  - Wait for QEMU Guest Agent
  - Discover VM IP via guest agent
  - `add_host` with SSH connection vars
- [ ] Create `roles/gaming_vm/meta/main.yml` with required metadata
- [ ] Add provision play to `site.yml` Phase 3, targeting `gaming_nodes`,
  tagged `[gaming, never]`, with `gaming_vm` role and `deploy_stamp`
- [ ] Add configure play to `site.yml` Phase 3, targeting `gaming` dynamic
  group, tagged `[gaming, never]`, after the provision play
- [ ] Create `tasks/reconstruct_gaming_group.yml`:
  - Verify VM 600 is running (`qm status {{ gaming_vm_id }}`)
  - Discover IP via `qm guest cmd` network-get-interfaces
  - Register via `add_host` with SSH connection vars

**Note on `[gaming, never]` tag:** This tag is opt-in. Gaming plays do NOT
run during normal `molecule converge` or `site.yml` execution. Use
`--tags gaming` to invoke explicitly. This prevents iGPU passthrough from
conflicting with media containers in the default converge.

**Verify:**

- [ ] VM 600 is running: `qm status 600` returns `running`
- [ ] VM is in `gaming` dynamic group (`add_host` registered)
- [ ] Machine type is q35: `qm config 600` shows `machine: q35`
- [ ] OVMF UEFI: `qm config 600` shows `bios: ovmf`
- [ ] hostpci0 set to iGPU PCI address
- [ ] Auto-start configured: `qm config 600` shows `onboot: 1`, `startup: order=1`
- [ ] QEMU Guest Agent responds: `qm guest cmd 600 ping` succeeds
- [ ] VM has IP on management bridge
- [ ] SSH connection works: `ansible.builtin.ping` succeeds
- [ ] Idempotent: re-run skips creation, VM still running
- [ ] deploy_stamp contains `gaming_vm` play entry

**Rollback:**

VM destruction: `qm stop 600 && qm destroy 600 --purge`.
PCI cleanup: unbind from vfio-pci, remove modprobe configs
(`blacklist-igpu.conf`, `vfio-pci-igpu.conf`), reload original GPU driver
(`modprobe i915` or `modprobe amdgpu`), rescan PCI bus
(`echo 1 > /sys/bus/pci/rescan`). Add PCI cleanup to BOTH cleanup
playbooks. See: `proxmox-system-safety` skill (PCI device cleanup after
passthrough).

---

### Milestone 2: Sunshine + Game Configuration

_Depends on M1 (VM running, SSH accessible)._

Configure the Windows VM via SSH: verify iGPU is visible in Windows,
configure Sunshine credentials, verify GZDoom + Freedoom, set Windows
Firewall rules. All software is pre-installed in the image (M0); this
role only applies host-specific configuration.

See: `vm-lifecycle-architecture` skill (configure role, dynamic group targeting).

**Implementation pattern:**
- Role: `roles/gaming_configure/defaults/main.yml`, `tasks/main.yml`,
  `meta/main.yml`
- site.yml: configure play targeting `gaming` dynamic group, tagged
  `[gaming, never]`
- Connection: SSH to Windows (`ansible_connection: ssh`)

**Env variables:**

| Variable | Required | Purpose | Example |
|----------|----------|---------|---------|
| `SUNSHINE_USER` | yes (prod) | Sunshine web UI username | `gamer` |
| `SUNSHINE_PASSWORD` | yes (prod) | Sunshine web UI password | `secret` |
| `WINDOWS_PRODUCT_KEY` | no | Windows 11 license (omit for eval) | `XXXXX-XXXXX-...` |

For testing: `SUNSHINE_USER` and `SUNSHINE_PASSWORD` auto-generate random
values if empty and persist to `{{ env_generated_path }}` via `blockinfile`
(same pattern as WireGuard keys). This ensures Moonlight pairing (project 10)
can read the same credentials across runs.

**What M2 configures (host-specific):**
- Sunshine credentials (`SUNSHINE_USER`, `SUNSHINE_PASSWORD` from `.env`)
- Sunshine streaming quality settings (resolution, bitrate, FPS)
- Windows Firewall rules for Sunshine ports
- RDP for backup remote access

**What is NOT in M2 (baked into image M0):**
- Windows installation, virtio drivers, QEMU Guest Agent
- OpenSSH Server
- iGPU drivers (Intel/AMD)
- Sunshine binary and service
- GZDoom + Freedoom

- [ ] Create `roles/gaming_configure/defaults/main.yml`:
  - `sunshine_user` via `lookup('env', 'SUNSHINE_USER') | default('admin', true)`
  - `sunshine_password` via `lookup('env', 'SUNSHINE_PASSWORD') | default('', true)`
  - Auto-generate password if empty (for testing)
  - `sunshine_port: 47990` (web UI)
  - `sunshine_resolution: 1920x1080`, `sunshine_fps: 60`,
    `sunshine_bitrate: 20000`
- [ ] Create `roles/gaming_configure/tasks/main.yml` (via SSH to Windows):
  - Verify iGPU is visible: PowerShell `Get-WmiObject Win32_VideoController`
  - Verify Sunshine is installed: check service status
  - Configure Sunshine credentials (edit config file or use CLI)
  - Verify Sunshine web UI is accessible on port 47990
  - Verify GZDoom + Freedoom are installed and launchable
  - Ensure Windows Firewall allows Sunshine ports (47984-47990)
  - Enable RDP for backup access
- [ ] Create `roles/gaming_configure/meta/main.yml` with required metadata

**Verify:**

- [ ] iGPU visible in Windows: `Win32_VideoController` shows Intel/AMD GPU
- [ ] Sunshine service running: `Get-Service SunshineService` shows Running
- [ ] Sunshine web UI accessible: `curl http://<vm_ip>:47990` returns 200
- [ ] Sunshine credentials configured
- [ ] GZDoom executable exists at expected path
- [ ] Freedoom WAD exists at expected path
- [ ] Windows Firewall allows ports 47984-47990
- [ ] RDP is enabled
- [ ] Idempotent: second run does not reconfigure unnecessarily

**Rollback:**

- Stop Sunshine service: `Stop-Service SunshineService`
- Revert firewall rules: `Remove-NetFirewallRule -Name "Sunshine*"`
- Disable RDP
- Full VM destruction is the escape hatch (M1 rollback)

---

### Milestone 3: Testing & Integration

_Depends on M1–M2._

Create per-feature molecule scenario for all 4 test nodes, extend cleanup
lists with VMID 600, add rollback plays to `playbooks/cleanup.yml`, and
run final validation.

See: `molecule-testing` skill (per-feature scenario setup, baseline workflow),
`molecule-verify` skill (verify completeness), `molecule-cleanup` skill
(cleanup completeness).

#### 3a. Per-feature scenario: `molecule/sunshine-vm/`

- [ ] Create `molecule/sunshine-vm/molecule.yml`:
  ```yaml
  platforms:
    - name: home
      groups:
        - proxmox
        - gaming_nodes
        - router_nodes
    - name: mesh1
      groups:
        - proxmox
        - lan_hosts
        - gaming_nodes
    - name: ai
      groups:
        - proxmox
        - gaming_nodes
    - name: mesh2
      groups:
        - proxmox
        - gaming_nodes
  provisioner:
    env:
      HOME_API_TOKEN: ${HOME_API_TOKEN}
      MESH1_API_TOKEN: ${MESH1_API_TOKEN}
      AI_API_TOKEN: ${AI_API_TOKEN}
      MESH2_API_TOKEN: ${MESH2_API_TOKEN}
      PRIMARY_HOST: ${PRIMARY_HOST}
      AI_HOST: ${AI_HOST}
      MESH_2_HOST: ${MESH_2_HOST}
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

  Note: `home` needs `router_nodes` for mesh1 access (OpenWrt baseline).
  The scenario documents the baseline dependency.

- [ ] Create `molecule/sunshine-vm/converge.yml`:
  ```yaml
  - name: Detect iGPU on gaming nodes
    hosts: gaming_nodes
    gather_facts: true
    roles:
      - proxmox_igpu

  - name: Provision Sunshine VM
    hosts: gaming_nodes
    gather_facts: false
    roles:
      - gaming_vm

  - name: Reconstruct gaming dynamic group
    hosts: gaming_nodes
    gather_facts: false
    tasks:
      - name: Include group reconstruction
        ansible.builtin.include_tasks:
          file: ../../tasks/reconstruct_gaming_group.yml

  - name: Configure Sunshine
    hosts: gaming
    gather_facts: false
    roles:
      - gaming_configure
  ```

- [ ] Create `molecule/sunshine-vm/verify.yml`:
  Verify runs on `gaming_nodes` (the Proxmox host), NOT on the `gaming`
  dynamic group (which is lost between playbook invocations). Uses
  `qm status`/`qm config` for VM state assertions and `qm guest exec`
  for in-VM checks (same pattern as LXC verify using `pct exec`).
  Reconstruct `gaming` group first for IP discovery, then run host-side
  `qm` commands for assertions.
- [ ] Create `molecule/sunshine-vm/cleanup.yml`:
  Destroys VM 600 on each node + PCI cleanup (unbind vfio-pci, reload
  GPU driver, rescan PCI bus).

#### 3b. Full integration (`molecule/default/`)

Gaming plays are opt-in (`[gaming, never]` tags) and NOT included in the
default test sequence. `gaming_nodes` is NOT added to `molecule/default`
platforms because iGPU passthrough conflicts with media container bind
mounts.

- [ ] Add VMID 600 to `molecule/default/cleanup.yml` `project_vm_ids` list
  (cleanup must handle the case where VM doesn't exist)
- [ ] Add VMID 600 to `molecule/default/cleanup_lan_host.yml`
- [ ] Add VMID 600 to `tasks/cleanup_lan_host.yml`
- [ ] Add VMID 600 to `playbooks/cleanup.yml`

#### 3c. Rollback plays in `playbooks/cleanup.yml`

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
            set -o pipefail
            qm stop {{ gaming_vm_id }} 2>/dev/null || true
            sleep 3
            qm destroy {{ gaming_vm_id }} --purge 2>/dev/null || true
          executable: /bin/bash
        changed_when: true

      - name: PCI cleanup — restore iGPU to host
        ansible.builtin.shell:
          cmd: |
            set -o pipefail
            # Remove modprobe configs
            rm -f /etc/modprobe.d/blacklist-igpu.conf \
                  /etc/modprobe.d/vfio-pci-igpu.conf
            # Reload GPU driver
            modprobe i915 2>/dev/null || modprobe amdgpu 2>/dev/null || true
            # Rescan PCI bus
            echo 1 > /sys/bus/pci/rescan
          executable: /bin/bash
        changed_when: true
  ```

#### 3d. Molecule env passthrough

- [ ] Add `SUNSHINE_USER`, `SUNSHINE_PASSWORD`, `WINDOWS_PRODUCT_KEY` to
  `molecule/default/molecule.yml` `provisioner.env` (optional, empty defaults)

#### 3e. Final validation

- [ ] Run `molecule test -s sunshine-vm` — all 4 nodes pass (exit code 0)
- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Cleanup leaves no gaming artifacts: VM destroyed, iGPU back on host driver
- [ ] `molecule test` (default) still passes (no regression from gaming additions)

**Rollback:** Revert molecule config and site.yml plays via git.

---

### Milestone 4: Documentation

_Depends on M1–M3._

- [ ] Create `docs/architecture/sunshine-build.md`:
  - Image build process (Windows ISO + autounattend.xml + sysprep)
  - iGPU passthrough mechanism (vfio-pci, IOMMU validation)
  - Sunshine configuration and ports
  - GZDoom test game rationale
  - Testing strategy (all 4 nodes, mutual exclusivity with media containers)
  - Env variables and build prerequisites
- [ ] Update `docs/architecture/overview.md`:
  - Add `gaming_vm` + `gaming_configure` to role catalog
  - Add Gaming VM plays to site.yml diagram
- [ ] Update `docs/architecture/roles.md`:
  - Add `gaming_vm` role documentation (iGPU passthrough, q35, Windows)
  - Add `gaming_configure` role documentation (Sunshine, SSH to Windows)
- [ ] Update `docs/architecture/roadmap.md`:
  - Add Sunshine project to Active Projects section
- [ ] Update Gaming Rig project plan (project 13):
  - Note: iGPU PCI passthrough validated on all 4 nodes by Sunshine project
  - Discrete GPU passthrough extends this foundation
  - Reference Sunshine project for base VM creation and Sunshine setup
- [ ] Update `project-structure.mdc`:
  - Add `molecule/sunshine-vm/` to key files table
  - Add `gaming_vm`, `gaming_configure` to architecture pattern section
- [ ] Add CHANGELOG entry under `[Unreleased]`

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] Gaming Rig plan cross-references Sunshine project
- [ ] All env variables documented: `SUNSHINE_USER`, `SUNSHINE_PASSWORD`,
  `WINDOWS_PRODUCT_KEY`

**Rollback:** N/A — documentation-only milestone.

---

## Future Integration Considerations

- **Gaming Rig project (13)**: Extends this Sunshine VM with discrete GPU
  passthrough on dedicated gaming hardware. iGPU passthrough is validated
  here; discrete GPU adds NVIDIA/AMD driver-specific IOMMU group handling
  and potentially different VM configuration (more cores, more RAM, larger
  disk for game library).
- **Moonlight client (10)**: Streams from this Sunshine server. Pairing
  requires both services operational on the same network. When both projects
  are implemented, add an optional pairing verification test.
- **Monitoring**: The gaming rig host joins `monitoring_nodes`. Netdata and
  rsyslog are provisioned by the monitoring projects — GPU temperature,
  streaming sessions, and VM health are collected automatically.
- **Looking Glass**: Deferred. If local display is needed alongside
  streaming, Looking Glass provides low-latency shared memory display.
  Not needed for the headless streaming use case.
- **Display exclusivity**: On Home Entertainment Box hosts, the gaming VM
  would conflict with Kodi, Moonlight, Desktop VM, and Kiosk for iGPU
  access. The hookscript pattern (project 12) does not apply here because
  iGPU passthrough is full PCI passthrough, not a bind mount. Gaming is
  only on dedicated `gaming_nodes` hardware.

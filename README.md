# vm_builds

Ansible project for provisioning and configuring VMs on Proxmox VE. Currently deploys an **OpenWrt** router VM with full NIC bridge passthrough, WiFi PCIe passthrough with 802.11s mesh, collision-free LAN subnet selection, and baseline firewall/DHCP. Designed to expand to additional VM types using a consistent two-role architecture.

## Architecture

```
 Linux Mint               Proxmox Node              OpenWrt VM
 (Control)                                         ┌──────────┐
 ┌────────┐   API token   ┌──────────┐  vmbr0 ────│ eth0 WAN │
 │ Ansible │──────────────▶│ Proxmox  │  vmbr1 ────│ eth1 LAN │
 │         │──────SSH─────▶│          │  vmbr2 ────│ eth2 LAN │
 └────────┘               └──────────┘  PCIe  ────│ wlan0    │
      │                                            └──────────┘
      │       images/ (custom, via build-images.sh)    802.11s
      └──────▶ (local to project root)                mesh
```

**Play 0 -- Backup** (targets all Proxmox hosts, tag: `backup`):
Back up host config (`/etc/network`, `/etc/modprobe.d`, GRUB, `/etc/pve`)
and snapshot existing VMs with `vzdump` before making any changes.

**Play 1 -- Infrastructure** (targets all Proxmox hosts):
Discover physical NICs, create one virtual bridge per NIC, detect WiFi
PCIe devices and configure IOMMU/vfio-pci passthrough.

**Play 2 -- Provision** (targets `router_nodes` via API + SSH):
Upload the OpenWrt disk image, create and boot the VM, then establish
a temporary bootstrap connection to the VM's LAN side.

**Play 3 -- Configure** (targets OpenWrt VM via SSH through Proxmox):
Two-phase configuration. Phase 1: set WAN (eth0) to DHCP, assign
remaining interfaces to a LAN bridge, restart networking, migrate
bootstrap IP to the LAN bridge. Phase 2: install WiFi driver packages,
configure 802.11s mesh on detected radios, apply collision-free LAN
subnet, configure firewall zones and DHCP, final network restart.

**Play 4 -- Cleanup** (targets all Proxmox hosts):
Remove the temporary bootstrap IP from the Proxmox bridge.

Each Proxmox-targeted play records its run in `/etc/ansible/facts.d/`
via the `deploy_stamp` role. On subsequent runs, `ansible_local.vm_builds`
shows the version and timestamp of each play that has been applied.

---

## Prerequisites

| Component | Notes |
|---|---|
| Linux Mint (or any Debian-based distro) | Control machine |
| `sshpass` | System package -- required for initial OpenWrt login |
| SSH key access to Proxmox | Root SSH from control machine to each node |
| Proxmox VE 7.x / 8.x | Target hypervisor with 2+ physical NICs |
| Proxmox API token | Created in the PVE web UI (see below) |
| OpenWrt `.img` | Placed in `images/` directory (see step 7) |

---

## Setup

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y sshpass
```

### 2. Clone the repo

```bash
git clone <repo-url>
cd vm_builds
```

### 3. Run the setup script

This creates the Python virtual environment and installs all
dependencies (Ansible, Molecule, linters, Galaxy collections/roles):

```bash
./setup.sh
```

To update dependencies later, run `./setup.sh` again.

### 4. Set up SSH keys for Proxmox

If you do not already have a key pair:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

Copy it to each Proxmox node:

```bash
ssh-copy-id root@<proxmox-ip>
```

Verify passwordless login:

```bash
ssh root@<proxmox-ip> hostname
```

### 5. Create a Proxmox API token

On the Proxmox web UI go to **Datacenter > Permissions > API Tokens**:

- User: `root@pam`
- Token ID: `ansible`
- **Uncheck** Privilege Separation

Copy the token secret -- it is shown only once.

### 6. Configure your environment

Copy `test.env` to `.env` and fill in real values:

```bash
cp test.env .env
```

Edit `.env`:

```
HOME_API_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
PRIMARY_HOST=192.168.1.100
MESH_KEY=your-secure-mesh-passphrase
# WAN_MAC=AA:BB:CC:DD:EE:FF
```

| Variable | Required | Description |
|---|---|---|
| `HOME_API_TOKEN` | Yes | API token secret from step 5 |
| `PRIMARY_HOST` | Yes | IP address of your Proxmox node |
| `MESH_KEY` | Yes | WPA3-SAE passphrase for the 802.11s mesh network |
| `WAN_MAC` | No | Clone this MAC onto the WAN NIC to match your old router (avoids ISP DHCP issues) |

If your Proxmox **node name** (shown in the PVE UI sidebar) differs
from `home`, rename `inventory/host_vars/home.yml` to match and update
the hostname in `inventory/hosts.yml`.

Review `inventory/group_vars/all.yml` to confirm the image path and VM
settings match your environment.

### 7. Place the OpenWrt image

Download an OpenWrt image from https://downloads.openwrt.org.
Grab the **combined ext4** or **combined squashfs** `.img.gz` for the
`x86/64` target, decompress it, and place the `.img` at the configured
path:

```bash
./build-images.sh
```

This downloads the OpenWrt Image Builder (once, cached) and produces custom
images with all required packages pre-installed in `images/`.

### 8. Run

```bash
./run.sh
```

Or activate the venv manually:

```bash
source .venv/bin/activate
set -a; source .env; set +a
ansible-playbook playbooks/site.yml
```

At the end of the run, a debug message prints the chosen LAN address
(e.g. `10.10.10.1`) so you know where to reach the new router.

---

## Testing

Tests run against a dedicated Proxmox test machine. The `test.env` file
ships with the test machine's IP (`192.168.86.201`).

### Lint + syntax (no hardware needed)

```bash
source .venv/bin/activate
ansible-lint
yamllint .
ansible-playbook playbooks/site.yml --syntax-check
```

### Full test pipeline (Molecule)

Runs cleanup (reset host), syntax check, converge (full playbook),
verify (assertions), and cleanup (destroy the test VM) against the
test machine:

```bash
source .venv/bin/activate
set -a; source test.env; set +a
molecule test
```

Useful Molecule commands during development:

```bash
molecule converge    # run the playbook only (no verify/cleanup)
molecule verify      # re-run verification without re-converging
molecule destroy     # tear down the test VM
molecule test        # full pipeline: cleanup -> syntax -> converge -> verify -> cleanup
```

### What the verify step checks

- At least 2 physical-NIC-backed bridges exist on Proxmox
- OpenWrt VM is running
- SSH connectivity to the VM
- WAN interface has a DHCP lease
- LAN subnet does not collide with WAN
- WiFi radios are detected (if hardware present)
- 802.11s mesh interfaces are configured per radio

---

## Variable Reference

### Global (`inventory/group_vars/all.yml`)

| Variable | Default | Description |
|---|---|---|
| `project_version` | `1.0.0` | Project version stamped on managed hosts after each run |
| `openwrt_image_path` | `images/openwrt-router-24.10.0-x86-64-combined.img.gz` | Path to the custom OpenWrt router image (built by `build-images.sh`) |
| `openwrt_vm_id` | `100` | Proxmox VM ID |
| `openwrt_vm_name` | `openwrt-router` | VM display name |
| `openwrt_vm_memory` | `512` | RAM in MB |
| `openwrt_vm_cores` | `2` | CPU cores |
| `openwrt_vm_disk_size` | `512M` | Boot disk size after resize |
| `proxmox_storage` | `local-lvm` | Proxmox storage pool for the imported disk |

### Environment variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `PROXMOX_API_TOKEN_SECRET` | Yes | API token secret value |
| `PROXMOX_HOST` | Yes | IP address of the target Proxmox node |
| `MESH_KEY` | Yes | WPA3-SAE passphrase for 802.11s mesh |
| `WAN_MAC` | No | Clone old router's MAC onto OpenWrt WAN NIC for ISP compatibility |

### Bridge role (`roles/proxmox_bridges/defaults/main.yml`)

| Variable | Default | Description |
|---|---|---|
| `bridge_exclude_patterns` | *(see file)* | Regex patterns for interfaces to skip during NIC discovery |

### PCI passthrough role (`roles/proxmox_pci_passthrough/defaults/main.yml`)

| Variable | Default | Description |
|---|---|---|
| `pci_passthrough_allow_reboot` | `false` | Allow automatic Proxmox reboot to enable IOMMU |
| `wifi_driver_blacklist` | `[]` | Override auto-detected WiFi driver blacklist |

### OpenWrt VM role (`roles/openwrt_vm/defaults/main.yml`)

| Variable | Default | Description |
|---|---|---|
| `openwrt_tmp_image_dir` | `/tmp` | Temp upload directory on the Proxmox node |
| `openwrt_bootstrap_gw` | `192.168.1.1` | OpenWrt factory-default LAN IP (used for initial SSH) |
| `openwrt_bootstrap_ip` | `192.168.1.2` | Temp IP assigned to a Proxmox bridge for bootstrap |
| `openwrt_bootstrap_cidr` | `24` | Netmask for the bootstrap subnet |
| `openwrt_bootstrap_bridge` | *(auto)* | Override which bridge is used for bootstrapping |
| `openwrt_wan_mac` | *(from WAN_MAC env)* | Clone this MAC onto the WAN NIC (net0) for ISP compatibility |
| `openwrt_vm_startup_order` | `1` | Proxmox boot order priority (lower = earlier) |

### OpenWrt configure role (`roles/openwrt_configure/defaults/main.yml`)

| Variable | Default | Description |
|---|---|---|
| `openwrt_lan_auto_subnet` | `true` | Auto-select a LAN subnet that avoids the WAN range |
| `openwrt_lan_subnet_candidates` | *(see file)* | Ordered list of candidate `{ip, netmask}` subnets |
| `openwrt_lan_ip` | `10.10.10.1` | Fallback LAN gateway (used when auto-subnet is off) |
| `openwrt_lan_netmask` | `255.255.255.0` | LAN subnet mask |
| `openwrt_dhcp_start` | `100` | DHCP pool start offset |
| `openwrt_dhcp_limit` | `150` | DHCP pool size |
| `openwrt_dhcp_leasetime` | `12h` | DHCP lease duration |
| `openwrt_mesh_enabled` | `true` | Enable 802.11s mesh on detected WiFi radios |
| `openwrt_mesh_id` | `vm-builds-mesh` | Mesh network identifier (must match across nodes) |
| `openwrt_mesh_key` | *(from MESH_KEY env)* | WPA3-SAE passphrase for mesh encryption |
| `openwrt_mesh_channel` | `auto` | WiFi channel (must match across mesh nodes) |
| `openwrt_mesh_encryption` | `sae` | Mesh encryption mode |

---

## Adding a New Proxmox Node

1. Add a host entry under `proxmox` in `inventory/hosts.yml`.
2. Create `inventory/host_vars/<hostname>.yml` with
   `ansible_host: "{{ lookup('env', 'PRIMARY_HOST') }}"` or a static IP.
3. Ensure SSH key access works: `ssh root@<ip> hostname`.
4. Run the playbook.

---

## Adding a New VM Type

The project is named `vm_builds` (plural) because it's designed to manage multiple VM types. Every VM follows a two-role pattern:

- **`<type>_vm`** -- provision role (create VM, import disk, attach NICs, start, bootstrap)
- **`<type>_configure`** -- configure role (packages, services, settings inside the VM)

Shared infrastructure roles (`proxmox_backup`, `proxmox_bridges`, `proxmox_pci_passthrough`) run once per host before any VM-specific roles.

### Step-by-step

Using Home Assistant as an example:

1. **Create the roles:**

```
roles/homeassistant_vm/
├── defaults/main.yml      # homeassistant_vm_id, image path, etc.
└── tasks/main.yml         # check exists → upload image → create VM → start → add_host

roles/homeassistant_configure/
├── defaults/main.yml      # service-specific config
└── tasks/main.yml         # install packages, configure services
```

2. **Register the VMID** in `inventory/group_vars/all.yml`:

```yaml
homeassistant_vm_id: 200
homeassistant_vm_name: homeassistant
homeassistant_vm_memory: 2048
homeassistant_vm_cores: 2
homeassistant_image_path: images/haos.qcow2
```

VMID convention: 100-series for network VMs, 200-series for services.

3. **Add the dynamic group and flavor group** to `inventory/hosts.yml`:

```yaml
all:
  children:
    proxmox:
      children:
        router_nodes:
          hosts:
            home: {}
        service_nodes:         # new flavor group
          hosts:
            home: {}           # this host gets both router + service VMs
    openwrt:
      hosts: {}
    homeassistant:             # empty -- populated by add_host at runtime
      hosts: {}
```

4. **Wire into `playbooks/site.yml`:**

```yaml
# New provision play targeting the flavor group:
- name: Provision HomeAssistant VM
  hosts: service_nodes
  gather_facts: false
  roles:
    - homeassistant_vm
    - role: deploy_stamp
      vars:
        deploy_stamp_play: homeassistant_vm

# New configure play targeting the dynamic group:
- name: Configure HomeAssistant
  hosts: homeassistant
  gather_facts: false
  roles:
    - homeassistant_configure
```

5. **Add the flavor group to Molecule** in `molecule/default/molecule.yml`:

```yaml
platforms:
  - name: home
    groups:
      - proxmox
      - router_nodes
      - service_nodes    # add new flavor group
```

6. **Extend tests:**
   - Add assertions to `molecule/default/verify.yml`.
   - Cleanup already iterates `qm list` to destroy all VMs, so no changes needed unless the VM needs custom teardown.

7. **Place the image** in `images/`, add a doc at `docs/architecture/<type>-build.md`, and add a CHANGELOG entry.

### Key patterns to follow

- **Idempotency**: Always check `qm status <vmid>` before creating. Guard creation tasks with `when: not vm_exists | bool`.
- **Bridge selection**: Consume `proxmox_all_bridges` from the bridges role. Most service VMs only need one LAN bridge: `proxmox_all_bridges[1]`.
- **Dynamic inventory**: The `<type>_vm` role uses `add_host` to register the VM. The `<type>_configure` play targets that group.
- **Variable isolation**: Prefix all role defaults with the VM type name (`homeassistant_vm_id`, not `vm_id`). Never cross-reference another role's defaults.

---

## Project Structure

```
vm_builds/
├── setup.sh                           # Bootstrap venv + all deps
├── run.sh                             # Source .env and run playbook
├── cleanup.sh                         # Restore / full-restore / clean
├── test.env                           # Test machine env (committed)
├── ansible.cfg
├── requirements.yml
├── .gitignore
├── .ansible-lint
├── .yamllint.yml
├── .venv/                             # Python venv (created by setup.sh, gitignored)
├── inventory/
│   ├── hosts.yml                          # Host inventory + flavor groups + dynamic groups
│   ├── group_vars/
│   │   ├── all.yml                    # Project version, VM parameters
│   │   └── proxmox.yml               # API auth, SSH settings
│   └── host_vars/
│       └── home.yml                   # Per-host overrides
├── playbooks/
│   ├── site.yml                       # Main orchestration playbook
│   └── cleanup.yml                    # Tag-driven restore playbook
├── molecule/
│   └── default/
│       ├── molecule.yml
│       ├── converge.yml
│       ├── verify.yml
│       └── cleanup.yml
├── docs/
│   └── architecture/
│       ├── overview.md                # High-level architecture
│       ├── openwrt-build.md           # OpenWrt-specific design
│       ├── roles.md                   # Role reference
│       └── roadmap.md                 # Future plans
├── images/                            # VM disk images (gitignored)
└── roles/
    ├── deploy_stamp/                  # Record deployment state as local facts
    ├── proxmox_backup/                # Host config + VM backup
    ├── proxmox_bridges/               # NIC discovery, bridge creation
    ├── proxmox_pci_passthrough/       # WiFi IOMMU/vfio setup
    ├── openwrt_vm/                    # VM lifecycle management
    └── openwrt_configure/             # OpenWrt UCI configuration
```

---

## How It Works

### Router Swap Workflow

To replace an existing router with the OpenWrt VM:

1. **Stage**: Plug the OpenWrt host behind the current router. Run the
   playbook. The VM boots, gets a WAN IP from the existing router, and
   configures a non-colliding LAN subnet.
2. **Swap**: Move the ISP uplink cable from the old router to the
   OpenWrt host's WAN port. The VM picks up a new DHCP lease from the
   ISP. The LAN side continues serving the same subnet.
3. **Downstream** (optional): Plug the old router into a LAN port on
   the OpenWrt host. Disable DHCP on the old router. It becomes a
   switch/access point on a sub-network, and existing devices connected
   to it continue working.

No MAC cloning is needed. Most ISPs and upstream devices will issue a
new DHCP lease to a new MAC within seconds. The LAN-side IP and DHCP
pool are independent of the WAN MAC address.

### WiFi PCIe Passthrough

The `proxmox_pci_passthrough` role detects WiFi PCIe devices on the
Proxmox host, verifies IOMMU is active (configuring GRUB and rebooting
if needed), checks that each WiFi card is in an isolated IOMMU group,
blacklists the host WiFi driver, and binds the device to `vfio-pci`.
The `openwrt_vm` role then passes the device into the VM via `hostpci`.

If IOMMU is not active and `pci_passthrough_allow_reboot` is false
(the default), the playbook fails with instructions rather than
rebooting Proxmox unannounced. Set it to `true` or reboot manually.

If no WiFi hardware is detected, all passthrough tasks are skipped
and the playbook continues without mesh networking.

### 802.11s Mesh

When WiFi radios are detected inside the OpenWrt VM, the configure role
replaces the default `wpad-basic` package with `wpad-mesh-openssl`,
enables each radio, and creates a mesh point interface with the
configured mesh ID and SAE encryption key. The mesh interface is bridged
to the LAN network, so wired and wireless mesh clients share the same
subnet. All mesh nodes must use the same `openwrt_mesh_id`,
`openwrt_mesh_key`, and `openwrt_mesh_channel`.

### LAN Subnet Auto-Selection

The configure role reads the WAN's assigned IP, extracts the `/24`
network prefix, and picks the first entry from
`openwrt_lan_subnet_candidates` whose prefix does not match.
Default candidates: `10.10.10.0/24`, `192.168.2.0/24`, `172.16.0.0/24`,
`192.168.10.0/24`. Set `openwrt_lan_auto_subnet: false` to pin a
specific `openwrt_lan_ip` instead.

### Bootstrap Sequence

The configure role needs SSH access to the OpenWrt VM, but fresh
OpenWrt only listens on its LAN interface (`192.168.1.1`). The playbook
temporarily assigns `192.168.1.2/24` to a Proxmox bridge that connects
to a LAN NIC, then SSH-es through the Proxmox host as a jump box. After
configuration, the LAN IP typically changes (to avoid WAN collisions),
so the bootstrap SSH session drops. All UCI changes are committed before
the network restart.

### Backup & Restore

Every playbook run creates a backup (Play 0) in `/var/lib/ansible-backup/`
on the Proxmox host containing host config and VM snapshots. Use
`cleanup.sh` to restore:

```bash
./cleanup.sh restore            # restore host config only
./cleanup.sh full-restore       # destroy current VMs, restore backed-up VMs + host config
./cleanup.sh clean              # destroy all VMs, restore host config (test reset)
./cleanup.sh clean test.env     # specify which env file to source (default: test.env)
```

### Re-runs

VM creation is skipped when the VM already exists. Bridge creation is
idempotent. The bootstrap always connects to `192.168.1.1` (OpenWrt's
factory default), which will fail if the LAN IP was previously changed.
To re-provision, destroy the VM first (`qm destroy <vmid>`) and re-run.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `proxmoxer` import error | Not running inside the venv | `source .venv/bin/activate` |
| `sshpass` not found | Missing system package | `sudo apt install sshpass` |
| `HOME_API_TOKEN` empty | `.env` not sourced | `set -a; source .env; set +a` or use `./run.sh` |
| `node ... does not exist` | `proxmox_node` doesn't match PVE hostname | Set `proxmox_node` in host_vars |
| SSH timeout on bootstrap | Proxmox bridge not reaching OpenWrt LAN | Check `openwrt_bootstrap_bridge` |
| WAN route timeout | No upstream DHCP on the WAN bridge | Verify the WAN bridge has a physical NIC with upstream connectivity |
| No internet after router swap | ISP DHCP tied to old MAC | Set `WAN_MAC=<old-router-mac>` in `.env` and re-run |
| VM gone after Proxmox reboot | VM not set to auto-start | Re-run the playbook (auto-start is set unconditionally) |
| Play ends with unreachable host | Expected when LAN IP changes | Check debug output for the new LAN address |
| IOMMU reboot required | First-time WiFi passthrough | Re-run with `-e pci_passthrough_allow_reboot=true` |
| WiFi IOMMU group shared | Onboard WiFi shares group with NIC | Check BIOS ACS settings or add `pcie_acs_override` to GRUB |

---

## For AI / LLM Assistants

This project includes `.cursor/rules/` and `.cursor/skills/` files designed to prevent common mistakes and maintain architectural consistency across sessions. Key resources:

| Resource | Purpose |
|---|---|
| `.cursor/rules/project-structure.mdc` | **Start here.** Always-on context: architecture, variable scoping, VMID allocation, new-VM checklist |
| `.cursor/rules/ansible-conventions.mdc` | Coding patterns: FQCN, BusyBox constraints, two-phase restart, detached restart scripts |
| `.cursor/rules/proxmox-safety.mdc` | Hard safety rules: commands that kill SSH, bridge teardown, PCI cleanup, backup protocol |
| `.cursor/rules/learn-from-mistakes.mdc` | Workflow: update skills/rules when issues occur |
| `.cursor/skills/vm-lifecycle/SKILL.md` | Step-by-step for adding new VM types with code templates |
| `.cursor/skills/ansible-testing/SKILL.md` | Molecule pipeline, cleanup requirements, extending verify for new VMs |
| `.cursor/skills/proxmox-host-safety/SKILL.md` | Decision tree for validating commands against remote Proxmox hosts |
| `.cursor/skills/writing-skills/SKILL.md` | How to write new skills optimized for LLM consumption |

When starting work on this project, the `project-structure.mdc` rule loads automatically and provides the full architectural context. If adding a new VM type, read `vm-lifecycle/SKILL.md` first.

---

## Documentation

Detailed architecture documentation lives in `docs/architecture/`:

- **[overview.md](docs/architecture/overview.md)** -- Design philosophy, execution flow, project structure
- **[openwrt-build.md](docs/architecture/openwrt-build.md)** -- OpenWrt VM requirements and design decisions
- **[roles.md](docs/architecture/roles.md)** -- Detailed reference for each Ansible role
- **[roadmap.md](docs/architecture/roadmap.md)** -- Current state and future plans (hardening, VLANs, multi-VM, CI/CD)

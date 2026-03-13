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
7. The `<type>_vm` role adds the VM to dynamic inventory via `add_host`. The `<type>_configure` role runs in a separate play targeting that group.
8. NEVER reference another role's defaults. Use `set_fact` with `cacheable: true` or `add_host` variables to pass data.
9. Every provision play targeting Proxmox hosts MUST include `deploy_stamp` as its last role.
10. Every new role MUST have `meta/main.yml` with `author`, `license: proprietary`, `role_name`, `description`, `min_ansible_version`, and `platforms`.
11. Provision plays target **flavor groups** (e.g., `router_nodes`, `service_nodes`), NOT `proxmox` directly. Shared infra targets `proxmox`.
12. Every VM MUST configure `--onboot 1 --startup order=N` via `qm set`. This task runs unconditionally to self-heal. Define `<type>_vm_startup_order` in role defaults.
13. When a role deploys files to the host, ALWAYS add them to both cleanup playbooks.
14. Optional env variables go in role `defaults/main.yml` via `lookup('env', ...) | default('', true)`. NEVER add optional vars to `REQUIRED_ENV` in `build.py`.
15. Every configured feature MUST have a corresponding assertion in `verify.yml`. "VM is running" is NOT sufficient — verify services, network topology, auto-start, and state files.
16. Feature plays that target dynamic groups MUST be paired with a `deploy_stamp` play targeting the flavor group (Proxmox host). The VM doesn't store `vm_builds.fact` — the host does.
17. Post-baseline features are implemented as separate task files in the configure role, NOT as separate roles. This avoids cross-role variable dependencies and keeps the configure role as the single owner of VM configuration.
18. Every entry point that targets a dynamic group as a separate `ansible-playbook` invocation (per-feature converge, verify, cleanup/rollback) MUST reconstruct the group first. `add_host` state is ephemeral.
19. LXC container networking MUST match the host's actual topology. Hosts behind OpenWrt (`router_nodes`, `lan_hosts`) use the OpenWrt LAN subnet. Hosts directly on WAN use `ansible_default_ipv4.gateway/prefix`. NEVER hardcode all containers to the OpenWrt LAN subnet.
20. `add_host` loops in `proxmox_lxc` MUST use `ansible_play_hosts` (not `ansible_play_hosts_all`). The latter includes hosts that failed in earlier plays, creating phantom container registrations.
21. **Bake, don't configure at runtime** (see `project-structure.mdc` Design principles). Custom images are REQUIRED. Provision roles verify the image exists and hard-fail if missing. Configure roles NEVER install packages — all packages are baked into the image. To add a package, update the image build script and rebuild. Three documented exceptions exist (see below).
22. **One path, no fallbacks** (see `project-structure.mdc` Design principles). NEVER add stock/generic image fallback logic. One tested code path per feature. Missing prerequisites fail with an actionable error message.
23. **Follow community standards** (see `project-structure.mdc` Design principles). Check upstream tooling before writing custom workarounds.
24. **Documented exceptions to bake principle**: three cases where runtime installation is acceptable. Each MUST be explicitly documented in the project plan with rationale:
    - **Docker pull of pinned image tag**: deterministic and versioned. Used for Docker-in-LXC services (e.g., Home Assistant pre-pulls `homeassistant/home-assistant:stable`).
    - **Desktop VMs via cloud image + apt**: full desktop environments (KDE, GNOME, 2-4 GB packages) are too large and hardware-dependent for pre-built images. GPU drivers depend on `igpu_vendor` (Intel vs AMD), known only at runtime. Cloud image + cloud-init is the community standard for VM provisioning.
    - **Windows VMs via ISO + autounattend.xml**: install-from-ISO IS the bake approach for Windows. The ISO + autounattend.xml produce a deterministic, unattended install with virtio drivers pre-injected. This is NOT an exception — it is how Windows images are built.
    Any OTHER runtime package installation (`apt install`, `opkg install`, `pip install` during converge) is rejected. If you need a new package, add it to the image build script.

## Playbook execution order (site.yml)

```
Play 0: proxmox_backup             (targets: proxmox, tag: backup)
         deploy_stamp: backup
Play 1: proxmox_bridges            (targets: proxmox — shared infra)
         proxmox_pci_passthrough
         proxmox_igpu
         deploy_stamp: infrastructure
Play 2: <type>_vm                  (targets: <flavor_group> — VM provision)
         deploy_stamp: <type>_vm
Play 3: <type>_configure           (targets: <type> — dynamic group)
Play 4+: Feature plays             (targets: <type> — dynamic group, per-feature tags)
         deploy_stamp on <flavor_group> after each feature play
Play N: Bootstrap cleanup          (targets: proxmox — remove temp networking)
```

### Feature plays (post-baseline)

When a configure role grows beyond the initial baseline, new features are
added as separate task files in the configure role (e.g.,
`roles/<type>_configure/tasks/security.yml`). Each feature gets a PAIR of
plays in `site.yml`:

```yaml
- name: Apply <feature> to <type>
  hosts: <dynamic_group>
  tags: [<type>-<feature>, never]
  gather_facts: false
  tasks:
    - name: Include <feature> tasks
      ansible.builtin.include_role:
        name: <type>_configure
        tasks_from: <feature>.yml

- name: Record <feature> deployment
  hosts: <flavor_group>
  tags: [<type>-<feature>, never]
  gather_facts: true
  roles:
    - role: deploy_stamp
      vars:
        deploy_play: <type>_<feature>
```

The `never` tag prevents feature plays from running during full converge
(they are opt-in via `--tags`). The paired `deploy_stamp` play runs on the
Proxmox host (flavor group), not the VM, because `vm_builds.fact` lives on
the host.

This pattern enables:
- Per-feature molecule scenarios that converge only the relevant task file
- Version-aware convergence via `deploy_stamp`
- Independent feature rollback via `cleanup.yml` tags

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
`add_host` registration. For readiness: use `ls /` not `hostname` (BusyBox
containers may lack it). For OpenWrt LXC: use `--ostype unmanaged`. See
proxmox-safety rule.

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

For OpenWrt containers (no Python), use `ansible.builtin.raw` with commands
wrapped in `/bin/sh -c '...'`. See the `openwrt-build` skill section
"Shell syntax and PATH through pct_remote" for quoting rules.

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
images/                                              (gitignored)
├── openwrt-router-24.10.0-x86-64-combined.img.gz   Custom router VM (build-images.sh)
├── openwrt-mesh-lxc-24.10.0-x86-64-rootfs.tar.gz   Custom mesh LXC (build-images.sh)
├── debian-12-standard_12.12-1_amd64.tar.zst         LXC template
└── ...future images...
```

NEVER commit images to git. The `images/` directory is listed in `.gitignore`.
Document the expected image filename and download URL in role defaults and
in `docs/architecture/`.

### Custom images via Image Builder

`build-images.sh` uses the OpenWrt Image Builder to create pre-configured
images with packages pre-installed and UCI defaults baked in. This eliminates
runtime `opkg install` and resolves firewall/networking conflicts in LXC
containers.

Per the project's "Bake, don't configure at runtime" principle
(`project-structure.mdc`), custom images are REQUIRED. Provision roles
verify the image exists and hard-fail with an actionable message if missing:

```yaml
- name: Fail if image is missing
  ansible.builtin.fail:
    msg: "Image not found: {{ image_path }}. Run ./build-images.sh to build it."
  when: not (image_stat.stat.exists | default(false))
```

Image paths are defined in `group_vars/all.yml`. When adding a new service,
define its image path there and add an existence check in the provision role.

### Upload pattern for VMs

```yaml
- name: Upload image to Proxmox
  ansible.builtin.copy:
    src: "{{ openwrt_image_path }}"
    dest: "/tmp/openwrt-upload"
    mode: "0644"
  when: not vm_exists | bool

- name: Decompress gzip image
  ansible.builtin.command:
    cmd: gunzip -f /tmp/openwrt-upload
  when:
    - not vm_exists | bool
    - openwrt_image_path.endswith('.gz')

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

Different VM/container types consume bridges differently:
- **Router VMs** (OpenWrt): ALL bridges — WAN on `net0`, remaining as LAN ports
- **Service VMs**: typically ONE LAN bridge — `proxmox_all_bridges[1]` (first non-WAN)
- **LXC containers on LAN hosts** (`router_nodes`, `lan_hosts`): `proxmox_all_bridges[1]` (LAN bridge)
- **LXC containers on WAN hosts**: `proxmox_wan_bridge` — NEVER use `proxmox_all_bridges[1]` here; the second bridge may not have internet
- **Isolated VMs**: a dedicated bridge if network isolation is required

## VMs that need internet during configure

If the configure role needs to download packages:
1. The VM must have a NIC on a bridge with upstream connectivity.
2. If the VM changes its own network topology mid-configure, use a multi-phase restart pattern (see VM-specific skill for details).
3. For VMs behind a router VM, WAN access comes through the LAN bridge — no special handling needed.

## Host-level apt prerequisites

Roles that install packages on the Proxmox HOST (not inside VMs/containers) must handle three prerequisites:

1. **Clock sync**: Sync via NTP before `apt-get update`. GPG verification fails with "Not live until" when the clock is behind. Use `chronyc -a 'burst 4/4'` + `sleep 6` + `chronyc -a makestep` (or `ntpdate -b pool.ntp.org` if chrony unavailable).
2. **DNS**: After cleanup destroys the router VM, `/etc/resolv.conf` may point to a dead IP (`10.10.10.1`). Check DNS with `getent hosts deb.debian.org` and fall back to `8.8.8.8` / `1.1.1.1`.
3. **Enterprise repos**: `pve-enterprise.sources` and `ceph.sources` require a subscription. Without it, `apt-get update` hangs. Rename both to `.disabled` and add the `pve-no-subscription` repo. ALWAYS restore them in cleanup.
   - NEVER use `sed` or `Enabled: no` in deb822 files — unreliable.
   - ALWAYS rename with `mv` (e.g., `mv pve-enterprise.sources pve-enterprise.sources.disabled`).
   - The `proxmox_igpu` role implements this pattern — reference it.
   - ALWAYS disable BOTH `pve-enterprise.sources` AND `ceph.sources`. Missing either one causes `apt` to hang.

## Deployment tracking

The `deploy_stamp` role writes `/etc/ansible/facts.d/vm_builds.fact` on Proxmox hosts after each play. On subsequent runs with `gather_facts: true`, the data is available as `ansible_local.vm_builds`. Each play appends its entry without overwriting others.

## Service config validation

Configure roles that deploy config files into LXC containers SHOULD validate
the config before restarting the service. If the config is invalid, the
service won't start and the container loses the service until the config is
fixed.

Pattern: use an Ansible handler chain where validation runs before restart:
```yaml
# handlers/main.yml
- name: Validate config
  ansible.builtin.command:
    cmd: <service> --check-config  # e.g., rsyslogd -N1, nginx -t
  listen: _restart_service
  changed_when: false

- name: Restart service
  ansible.builtin.command:
    cmd: systemctl restart <service>
  listen: _restart_service
```

Handlers with the same `listen` event run in definition order. If validation
fails, the chain stops and the restart never executes.

After flush_handlers, add a health check with retries to confirm the service
came up:
```yaml
- name: Wait for service
  ansible.builtin.command:
    cmd: systemctl is-active <service>
  retries: 5
  delay: 2
  until: result.stdout | trim == 'active'
```

Previous bug: rsyslog `20-forward.conf` deployment had no config validation.
An invalid template would have crashed rsyslog on restart, killing log
reception for all upstream senders.

## Config file ordering for optional runtime configs

When baked image configs need to interoperate with optional runtime configs,
use numbered filenames in `/etc/<service>.d/` to control processing order:

```
10-base.conf       — module loads, template definitions (baked)
20-optional.conf   — runtime config deployed by configure role
50-routing.conf    — final routing/filtering (baked)
```

This pattern is needed when:
- The runtime config needs to intercept messages before the baked config
  processes them (e.g., forwarding before local storage)
- The baked config uses `stop` to prevent messages from falling through

Previous bug: rsyslog used a named ruleset for TCP-received messages.
Messages in a named ruleset never enter the default ruleset, so the
optional forwarding config (in the default ruleset) never saw remote
messages. Fix: remove the named ruleset, use a property filter with `stop`
at number 50, and deploy forwarding at number 20.

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

## Dynamic group reconstruction

Dynamic groups populated by `add_host` (e.g., `openwrt`, `pihole`) are
ephemeral — they exist only within a single `ansible-playbook` invocation.
Any entry point that runs as a separate invocation MUST reconstruct the
group before targeting it.

Entry points that need reconstruction:
- Per-feature `molecule/*/converge.yml`
- Per-feature `molecule/*/verify.yml`
- `playbooks/cleanup.yml` (rollback plays)

ALWAYS extract group reconstruction into a reusable task file (e.g.,
`tasks/reconstruct_<type>_group.yml`) to avoid duplication. The task file
must:
1. Verify the VM/container is running
2. Detect its IP from host-side state (bridge IPs, DHCP leases, etc.)
3. Detect the current auth method (may change after security hardening)
4. Register the host via `add_host` with the correct connection args

See the `ansible-testing` skill for the full pattern with auth detection.

Previous bug: per-feature verify assertions targeting the `openwrt` group
ran with zero hosts — all assertions passed silently because Ansible skips
plays when the host group is empty. The issue was only caught during plan
review, not at runtime, because "0 assertions passed" looks the same as
"all assertions passed" in Ansible output.

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

## Cross-cutting infrastructure ownership

Some infrastructure components are deployed by one service but consumed by
multiple services. The deploying project OWNS the resource; other projects
only ATTACH to it. This prevents duplicate deployment tasks and conflicting
configurations.

Current cross-cutting components:

| Component                  | Owning project     | Consumers                        |
|----------------------------|--------------------|---------------------------------|
| Display-exclusive hookscript | Custom UX Kiosk   | Kodi, Moonlight, Desktop VM, Gaming VM |

Rules:
- Only the owning project deploys the hookscript (via its provision role).
- Other projects add `hookscript: <path>` to their VM/container config.
- If the hookscript doesn't exist yet (owning project not deployed), the
  consumer's provision role MUST skip hookscript attachment with a warning,
  NOT deploy its own copy.
- Document the owning project explicitly in each consumer's project plan.

## Separate hardware topology

Services that run on separate physical hardware (e.g., Gaming Rig on a
dedicated machine) have unique characteristics:

- No OpenWrt router — the machine connects directly to the ISP router.
- Build profile may differ from the primary cluster.
- Testing requires the hardware to be physically available.
- Molecule scenarios should be conditional: skip when hardware unavailable
  rather than hard-fail.

Document the hardware topology separately in the project plan, including:
- Which machine, where it is, how it connects to the network.
- Which build profile it uses.
- How testing works when hardware is unavailable.

## VA-API driver portability

Image builds for services that use the iGPU for hardware acceleration
(Jellyfin, Kodi, Moonlight) SHOULD include BOTH Intel and AMD VA-API driver
packages. At runtime, only the matching driver loads.

Intel: `intel-media-va-driver` + `vainfo`
AMD: `mesa-va-drivers` + `vainfo`

This avoids rebuilding images when a container is moved to different
hardware. The configure role reads `igpu_vendor` (exported by
`proxmox_igpu`) to set `LIBVA_DRIVER_NAME` appropriately (`iHD` for Intel,
`radeonsi` for AMD).

## Hardware detection: hard-fail by default

NEVER add "graceful skip" for hardware expected on every host. Roles MUST
hard-fail when required hardware is missing. The reasoning: silent skips mask
fixable problems (BIOS settings, missing drivers) behind warnings that are
easy to miss, wasting entire test cycles.

| Hardware   | Expectation     | Detection role               |
|------------|----------------|------------------------------|
| iGPU       | REQUIRED       | `proxmox_igpu` (hard-fail)   |
| WiFi + VT-d| REQUIRED       | `proxmox_pci_passthrough` (hard-fail) |
| NIC count  | Dynamic OK     | `proxmox_bridges` (2+ only for `router_nodes`) |

Previous bug: `proxmox_pci_passthrough` silently skipped passthrough when
IOMMU groups were invalid. Root cause was VT-d disabled in BIOS — a
30-second fix masked for an entire test cycle.

## Test strategy

- `molecule/default/` is the full integration test (rebuilds everything from scratch).
- Per-feature scenarios (`molecule/<feature>/`) test incremental changes on top of the baseline.
- Per-feature scenarios assume the baseline exists (router VM running). They do NOT rebuild.
- Use `molecule converge` + `molecule verify` for day-to-day iteration (preserves baseline).
- Use `molecule test` only for clean-state validation (CI, pre-commit). It destroys the baseline.
- After `molecule test`, ALWAYS re-run `molecule converge` to restore the baseline before working on layered scenarios.
- Cleanup destroys ALL VMs via `qm list` iteration — not hardcoded VMIDs.
- Reproduce production bugs on the test machine first (`test.env`). Only involve production when the test machine cannot reproduce.
- Use TDD: write the verify assertion first, confirm it fails, implement the fix, confirm it passes.

## LXC package management

- ALWAYS use `install_recommends: false` when installing packages in LXC
  containers. Many packages Recommend kernel-related metapackages that pull
  in 70+ MB kernel images, filling the small LXC disk.
- Broken apt in an LXC container means the baseline is wrong. The fix
  belongs in `proxmox_lxc` (shared provisioning), NOT in individual
  configure roles. See the `clean-baselines` rule.
- The shared `proxmox_lxc` role removes broken kernel packages from the
  dpkg database after creating a container. This ensures every container
  starts with working apt regardless of template issues.
- Previous bug: `wireguard-tools` Recommends `wireguard` metapackage which
  depends on `linux-image-rt-amd64`. Without `install_recommends: false`,
  apt pulled in a 70MB kernel image that filled the 1GB container disk
  (No space left on device).

## Per-host IP indexing for multi-node LXC services

When a service deploys to multiple Proxmox hosts (e.g., rsyslog on all
monitoring_nodes), each container needs a unique IP. Use the host's index
in its flavor group: `offset + groups['<flavor>'].index(inventory_hostname)`.

- Ansible sorts group members ALPHABETICALLY. The index depends on the full
  group composition, which can differ between per-feature and E2E scenarios.
- Verify tasks MUST query the actual container IP from `pct config` instead
  of recomputing it. This avoids index drift between scenarios.
- WAN IPs add +200 to the base offset: `offset + 200 + index`.
- ALWAYS check that WAN IPs don't collide with any host's management IP.
  Previous bug: rsyslog_ct_ip_offset=11 produced WAN IP .211, colliding
  with mesh2's host IP (192.168.86.211).

Pattern for querying actual IP in verify tasks:

```yaml
- name: Get container IP from Proxmox config
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      pct config {{ ct_id }} | grep -oP 'ip=\K[^/,]+'
    executable: /bin/bash
  register: _ct_ip_query
  changed_when: false
```

## Per-feature scenario group membership

Per-feature Molecule scenarios MUST include all groups that affect the role's
branching logic. The role's `when:` conditions check group membership — if a
group is missing, the wrong branch executes.

- Previous bug: rsyslog-lxc scenario had `home` in `monitoring_nodes` only.
  The LAN/WAN detection checked `router_nodes` membership, found it missing,
  took the WAN path, and failed because `ansible_default_ipv4` was undefined
  (no `gather_facts`).
- Fix: add `router_nodes` to the per-feature scenario's platform groups for
  hosts that need the LAN path.
- This applies to ALL LXC roles with LAN/WAN branching, not just rsyslog.

## Logrotate in LXC containers

When writing logrotate configs baked into LXC images, use `root adm` as the
file ownership — NOT `syslog adm`. The `syslog` user may not exist in minimal
container templates. The Debian community standard for `/etc/logrotate.d/rsyslog`
uses `root adm`.

Previous bug: logrotate config with `create 0640 syslog adm` failed in the
rsyslog container because the `syslog` user didn't exist in the Proxmox
Debian 12 standard template. The chown fallback in build-images.sh (`chown
syslog:syslog || chown root:root`) masked this — a graceful fallback that
should have been a hard error.

## Handler conventions for LXC service roles

Configure roles that run inside LXC containers via `pct_remote` MUST use
`ansible.builtin.systemd` for service restarts in handlers, not
`ansible.builtin.command: cmd: systemctl restart ...`. Both work over
`pct_remote`, but the module follows Ansible conventions, reports state
accurately, and matches the pattern established by `pihole_configure`.

Use `ansible.builtin.command` only for operations that have no module
equivalent: config validation (`rsyslogd -N1`), status checks
(`systemctl is-active`), and binary execution (`pihole -g`).

Previous bug: `rsyslog_configure` handler used `ansible.builtin.command`
for restart while `pihole_configure` used `ansible.builtin.systemd`. Fixed
for consistency.

## Secret generation

- When a configure role generates secrets (keys, tokens), write them to
  `{{ env_generated_path }}` on the controller via `delegate_to: localhost`.
- The path auto-detects: `test.env.generated` under Molecule,
  `.env.generated` in production. NEVER hardcode the path.
- Use `ansible.builtin.blockinfile` with a service-specific marker so
  multiple services can accumulate in the same file.
- See the `secret-generation` rule for the full pattern.

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

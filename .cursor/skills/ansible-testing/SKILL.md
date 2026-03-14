---
name: ansible-testing
description: Run and validate Ansible project tests using Molecule, ansible-lint, and yamllint. Use when running tests, adding test scenarios, debugging test failures, extending verify.yml for new VMs, or working with Molecule configuration in the vm_builds project.
---

# Ansible Testing

## MANDATORY: Test after every code change

ALWAYS run `molecule test` after modifying roles, playbooks, or molecule
config. NEVER consider a task complete until the full test suite passes.
When adding or changing behavior, ALWAYS update `molecule/default/verify.yml`
with corresponding assertions before running the test.

## Test-first reproduction

When a bug is reported against production, ALWAYS reproduce it on the test
machine first. Replicate the production environment in `test.env` (same env
vars, same image) and run `molecule test`. Only involve the production host
when the test machine cannot reproduce the issue.

Previous bug: SSH timeout on production was reproduced by adding `WAN_MAC` to
`test.env`. Four fix-and-verify cycles completed in 15 minutes without touching
production.

## TDD iteration pattern

1. Write or update the verify assertion in `verify.yml` first.
2. `molecule test` — the assertion should fail (proves the test catches the issue).
3. Implement the fix in the role.
4. `molecule test` — the assertion should now pass.
5. Update skills/rules with lessons learned.

Use `molecule converge` for rapid mid-fix iteration. Use `molecule test` for
the final end-to-end proof from a clean state.

## Quick start

```bash
source .venv/bin/activate
set -a; source test.env; set +a

molecule test          # full clean-state pipeline (destroys baseline)
molecule converge      # run playbook only (preserves baseline)
molecule verify        # run assertions only
molecule cleanup       # reset test host
```

## Baseline workflow

The OpenWrt baseline on the primary host takes ~4 minutes to build. Layered
scenarios (`mesh1-infra`, `openwrt-security`, etc.) and leaf nodes (mesh1)
all depend on it. Prefer keeping the baseline running between test runs.

**Day-to-day iteration:**
```bash
molecule converge                  # build/update baseline (idempotent)
molecule verify                    # run assertions
molecule converge -s mesh1-infra   # run layered scenario (baseline must exist)
molecule verify -s mesh1-infra     # verify layered scenario
```

**Clean-state validation (CI, pre-commit, final proof):**
```bash
molecule test                      # full pipeline — destroys everything at the end
molecule converge                  # restore baseline for further work
```

**After a full `molecule test`:** ALWAYS re-run `molecule converge` to restore
the baseline before working on layered scenarios. Otherwise leaf nodes are
unreachable (no OpenWrt = no LAN).

## Molecule pipeline sequence

`molecule test` runs these phases in order:
1. `dependency` — install Galaxy requirements
2. `cleanup` — reset host from previous runs
3. `syntax` — ansible syntax check
4. `converge` — run `playbooks/site.yml`
5. `verify` — run `molecule/default/verify.yml`
6. `cleanup` — reset host after test (destroys baseline)

There is NO `lint` phase in the Molecule config. Run `ansible-lint` and `yamllint` separately.

## Architecture

- **Driver**: `default` with `managed: false` (real Proxmox hardware, not Docker)
- **Platforms**: 4 nodes — `home` (primary), `ai` and `mesh2` (directly reachable), `mesh1` (LAN satellite via ProxyJump)
- **Platform groups**: `home` gets `proxmox` + all primary flavor groups (including `wifi_nodes`); `ai` gets `proxmox`, `vpn_nodes`; `mesh2` gets `proxmox`, `vpn_nodes`, `wifi_nodes`; `mesh1` gets `proxmox`, `lan_hosts`, `vpn_nodes`, `wifi_nodes`
- **Provisioner**: `playbooks/site.yml` (phased: primary hosts → LAN bootstrap → services)
- **Cleanup**: two-play cleanup — `proxmox:!lan_hosts` for primary, `router_nodes` for LAN hosts via SSH
- **Config**: `molecule/default/molecule.yml`

### 4-node topology

```
ISP Router (192.168.86.x supernet)
  |
Switch
  |            |                  |
Home          AI Node          Mesh2
(primary)     192.168.86.220   192.168.86.211
  |
  |-- OpenWrt VM (10.10.10.1)
  |     |
  |     LAN bridge (10.10.10.x)
  |       |
  |     Mesh1 (10.10.10.210)
```

- **home**, **ai**, **mesh2**: directly reachable on the supernet (no ProxyJump)
- **mesh1**: behind home's OpenWrt, reachable via ProxyJump through home
- All 4 nodes are in `vpn_nodes` — WireGuard containers deploy on all 4 in parallel
- `mesh1` and `mesh2` are also in `wifi_nodes` — OpenWrt Mesh LXC deploys on both
- `home` is the only `router_nodes` member (runs OpenWrt)
- `mesh1` is the only `lan_hosts` member (requires OpenWrt to be running)

### Parallelism within plays

With 4 nodes in `vpn_nodes`, Ansible's linear strategy runs tasks on all
4 hosts concurrently within each play. No molecule-level parallelization
is needed — the parallelism is automatic. Each host gets its own WireGuard
container provisioned and configured simultaneously. Similarly, the
`wifi_nodes:!router_nodes` play runs mesh LXC provisioning on mesh1 and
mesh2 in parallel.

### Phased site.yml

`site.yml` runs in three phases to respect host reachability dependencies:

1. **Phase 1 (Primary hosts)**: `proxmox:!lan_hosts` — backup, infra, OpenWrt VM, OpenWrt configure
2. **Phase 2 (LAN satellites)**: After OpenWrt creates the LAN, bootstrap LAN hosts from `router_nodes`, then run backup + infra on `lan_hosts`
3. **Phase 3 (Services)**: Flavor groups that span both primary and LAN hosts (e.g., `vpn_nodes` includes home + mesh1) — runs in parallel across both hosts

This ordering is correct for both test and production. LAN hosts are only
reachable after OpenWrt provisions the LAN bridge.

## Baseline testing model

The **baseline** is the state after `molecule/default` converges successfully:
router VM running, WAN/LAN configured, DHCP serving, firewall active, all 4
nodes reachable. All per-feature molecule scenarios start from this baseline
and only converge/revert their own changes.

**CRITICAL: The OpenWrt baseline stays up.** Only tear down the specific
containers being tested. Full `molecule test` is reserved for final
validation only.

```
Scenario Hierarchy
├── molecule/default/              Full integration (home, mesh1, ai, mesh2 — 4-node)
│   ├── converge.yml               imports site.yml (phased: primary → LAN → services)
│   ├── verify.yml                 Multi-play: common infra, router, WireGuard, LAN host, mesh LXC
│   ├── cleanup.yml                Two-play: primary hosts + LAN hosts via SSH
│   └── cleanup_lan_host.yml       Per-LAN-host cleanup tasks (included by cleanup.yml)
│
├── molecule/openwrt-security/     Per-feature (assumes baseline exists)
│   ├── converge.yml               runs only security plays via tags
│   ├── verify.yml                 security-specific assertions only
│   └── cleanup.yml                runs security rollback only
│
├── molecule/wireguard-lxc/        Per-feature (WireGuard standalone)
│   ├── converge.yml               WireGuard provision + configure
│   ├── verify.yml                 WireGuard-specific assertions
│   └── cleanup.yml                destroy container + unload module
│
├── molecule/mesh1-infra/          Lightweight infra-only on mesh1
│   └── ...                        (partially redundant with default, kept for quick iteration)
│
└── ...
```

**Primary workflow:** Per-feature scenarios are the main test loop:
1. `molecule converge` (once) — build the full baseline with all 4 nodes
2. `molecule converge -s wireguard-lxc` + `molecule verify -s wireguard-lxc` — iterate
3. Each per-feature scenario tears down only its own containers, verifies, then cleans up
4. Baseline (OpenWrt, bridges, PCI, iGPU) is assumed to exist and left running

**Final validation only:** `molecule test` runs the full clean-state pipeline and
destroys everything at the end. Use only before committing or for CI.

Full `molecule test` takes 4-5 minutes. Per-feature scenarios take
30-60 seconds. During development, iterate with per-feature scenarios.
Run full integration before committing.

### Two kinds of per-feature scenarios

There are two distinct patterns for per-feature scenarios:

1. **Layered feature scenarios** (e.g., `openwrt-security`, `openwrt-vlans`):
   assume the baseline exists (router VM running), converge only their tagged
   plays, verify only their assertions, clean up only their changes.

2. **Standalone role scenarios** (e.g., `proxmox-lxc`, `proxmox-igpu`):
   test a single shared infrastructure role in isolation. They converge the
   role, verify its output, and clean up any artifacts. They do NOT depend
   on the baseline — they can run against a bare Proxmox host.

Standalone scenarios are the right pattern for shared roles that run on the
Proxmox host before any VMs are created. Layered scenarios are for features
that build on top of existing VM/container state.

Previous learning: the `proxmox-lxc` and `proxmox-igpu` scenarios were
developed independently of the default integration test. This allowed
rapid iteration on iGPU driver/vendor issues (Intel and AMD; 4+ fix cycles
in one session) without waiting for the full 4-minute default test each time.

### Per-feature scenario setup

Each per-feature scenario needs its own `molecule.yml` that shares the
platform config with `default` but uses a different test sequence:

```yaml
# molecule/openwrt-security/molecule.yml
dependency:
  name: galaxy
  options:
    requirements-file: ${MOLECULE_PROJECT_DIRECTORY}/requirements.yml

driver:
  name: default
  options:
    managed: false

platforms:
  - name: home
    groups:
      - proxmox
      - router_nodes

provisioner:
  name: ansible
  config_options:
    defaults:
      roles_path: ${MOLECULE_PROJECT_DIRECTORY}/roles
      collections_paths: ${MOLECULE_PROJECT_DIRECTORY}/collections
      host_key_checking: false
      retry_files_enabled: false
  env:
    HOME_API_TOKEN: ${HOME_API_TOKEN}
    PRIMARY_HOST: ${PRIMARY_HOST}
    MESH_KEY: ${MESH_KEY}
  inventory:
    links:
      host_vars: ../../inventory/host_vars/
      group_vars: ../../inventory/group_vars/

verifier:
  name: ansible

scenario:
  test_sequence:
    - dependency
    - syntax
    - converge
    - verify
    - cleanup
```

No initial cleanup phase — the baseline must already exist. If it doesn't,
converge will fail fast (can't SSH to OpenWrt) with a clear error.

### Per-feature converge pattern

Per-feature converge playbooks run only their tagged plays. They MUST
populate the `openwrt` dynamic group first since the baseline's `add_host`
state doesn't persist across molecule runs.

The reconstruction logic MUST detect the current SSH auth method. After
security hardening (M1), SSH uses key auth. Before M1 (or after rollback),
SSH uses password auth with empty password. Extract this into a reusable
task file to avoid duplication across converge, verify, and cleanup.

```yaml
# molecule/openwrt-security/converge.yml
---
- name: Reconstruct openwrt dynamic group from baseline
  hosts: router_nodes
  gather_facts: true
  tasks:
    - name: Verify VM 100 is running
      ansible.builtin.command:
        cmd: qm status 100
      register: _vm_status
      changed_when: false
      failed_when: "'running' not in _vm_status.stdout"

    - name: Include reusable group reconstruction
      ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

- name: Apply security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Include security hardening tasks
      ansible.builtin.include_role:
        name: openwrt_configure
        tasks_from: security.yml
```

### Reusable group reconstruction task file

Extract the group reconstruction logic into `tasks/reconstruct_openwrt_group.yml`
at the project root. This file is consumed by:
- Per-feature `converge.yml` (molecule scenarios)
- Per-feature `verify.yml` (molecule scenarios)
- `playbooks/cleanup.yml` (rollback plays)

The task file MUST:
1. Detect the OpenWrt LAN IP from Proxmox bridge state
2. Detect whether key auth or password auth is active
3. Build the correct `ansible_ssh_common_args` for the detected auth method
4. Register the host via `add_host` with appropriate args

Auth detection heuristic:
- If `OPENWRT_SSH_PRIVATE_KEY` env var is set AND `deploy_stamp` shows
  security hardening was applied → use key auth
- Otherwise → use password auth (empty password, `sshpass`)

```yaml
# tasks/reconstruct_openwrt_group.yml (simplified)
---
- name: Detect OpenWrt LAN IP from Proxmox LAN bridge
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      wan_br=$(ip -o route show default | awk '{print $5}' | head -1)
      for br in /sys/class/net/vmbr*/; do
        brname=$(basename "$br")
        [ "$brname" = "$wan_br" ] && continue
        ip -o -4 addr show dev "$brname" | awk '{print $4}' | cut -d/ -f1 | head -1
        break
      done
    executable: /bin/bash
  register: _proxmox_lan_ip
  changed_when: false

- name: Compute OpenWrt LAN IP
  ansible.builtin.set_fact:
    _openwrt_ip: >-
      {{ _proxmox_lan_ip.stdout.split('.')[0:3] | join('.') }}.1

- name: Detect SSH auth method
  ansible.builtin.set_fact:
    _use_key_auth: >-
      {{ (lookup('env', 'OPENWRT_SSH_PRIVATE_KEY') | length > 0) and
         (ansible_local.vm_builds.plays.openwrt_security is defined) }}

- name: Add OpenWrt to dynamic group
  ansible.builtin.add_host:
    name: openwrt-router
    groups: openwrt
    ansible_host: "{{ _openwrt_ip }}"
    ansible_user: root
    ansible_ssh_common_args: >-
      -o ProxyJump=root@{{ ansible_host }}
      -o StrictHostKeyChecking=no
      -o UserKnownHostsFile=/dev/null
      -o ConnectTimeout=10
      -o ServerAliveInterval=15
      -o ServerAliveCountMax=4
      {% if _use_key_auth %}
      -i {{ lookup('env', 'OPENWRT_SSH_PRIVATE_KEY') }}
      {% endif %}
```

### Per-feature verify pattern

Per-feature verify playbooks also run as a separate `ansible-playbook`
invocation, so the dynamic group is empty. The verify MUST reconstruct
the group before running assertions that target the VM.

```yaml
# molecule/openwrt-security/verify.yml
---
- name: Reconstruct openwrt dynamic group
  hosts: router_nodes
  gather_facts: true
  tasks:
    - name: Include reusable group reconstruction
      ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

- name: Verify security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Check banIP is installed
      ansible.builtin.raw: opkg list-installed | grep banip
      register: _banip
      changed_when: false
    - name: Assert banIP installed
      ansible.builtin.assert:
        that: _banip.rc == 0
```

Previous bug: per-feature verify assertions targeting the `openwrt` group
ran with zero hosts, silently passing all assertions. The group was empty
because `add_host` from converge doesn't persist into verify.

### Per-feature cleanup pattern

Per-feature cleanup runs only the feature's rollback tag. The
`cleanup.yml` playbook includes its own group reconstruction play
(tagged with all rollback tags), so no reconstruction is needed here:

```yaml
# molecule/openwrt-security/cleanup.yml
---
- name: Rollback security hardening
  ansible.builtin.import_playbook: ../../playbooks/cleanup.yml
  tags: [openwrt-security-rollback]
```

### Running per-feature tests

```bash
# Establish baseline (once, or when baseline is stale)
molecule converge

# Iterate on a feature
molecule test -s openwrt-security     # converge + verify + cleanup
molecule converge -s openwrt-security # apply only (for debugging)
molecule verify -s openwrt-security   # check only (after manual fixes)

# Full integration (before commit)
molecule test
```

### Standalone role scenario setup

Standalone scenarios test a single role without any baseline dependency.
Use these for shared infrastructure roles (`proxmox_lxc`, `proxmox_igpu`,
future shared roles). They converge the role directly, verify facts and
artifacts, then clean up host state changes.

```yaml
# molecule/proxmox-igpu/converge.yml
---
- name: Test proxmox_igpu role
  hosts: proxmox
  gather_facts: true
  roles:
    - proxmox_igpu
```

Key differences from layered scenarios:

1. **No baseline dependency** — no need to populate dynamic groups first.
2. **Cleanup restores host state** — enterprise repos, config files, etc.
   The cleanup is role-specific, not a full `cleanup.yml --tags` call.
3. **VMID 999 for throwaway resources** — standalone LXC tests use VMID 999
   to avoid collisions with real service VMIDs.
4. **Include cleanup in `test_sequence`** — standalone scenarios SHOULD
   include `cleanup` at the end (unlike layered scenarios which inherit it).

### Fact scoping across Molecule phases

Facts set during `converge` are NOT available in `verify`. Molecule runs
converge and verify as separate Ansible invocations with independent fact
caches.

**Impact:** If a role exports facts via `set_fact` (e.g., `proxmox_igpu`
exports `igpu_available`, `igpu_render_device`, etc.), those facts will be
undefined in verify.yml.

**Fix:** For read-only roles, re-include the role in verify:

```yaml
# molecule/proxmox-igpu/verify.yml
---
- name: Verify proxmox_igpu role
  hosts: proxmox
  gather_facts: true
  roles:
    - proxmox_igpu    # re-run to populate facts (idempotent)
  tasks:
    - name: Assert igpu_available fact is set
      ansible.builtin.assert:
        that: igpu_available is defined
```

For roles with side effects, use shell commands in verify to check state
directly instead of relying on Ansible facts.

Previous bug: `proxmox-igpu` verify failed with "igpu_available is not
defined" because the fact was only set during converge. Re-including the
role (which is idempotent) fixed it.

### Molecule file path resolution

`role_path` and relative paths behave differently depending on which
scenario is running. The working directory during a Molecule run is the
scenario directory (`molecule/<scenario>/`), not the project root.

**Impact:** A task referencing `../../images/template.tar.zst` works from
`molecule/default/` but breaks from `molecule/proxmox-lxc/` because the
relative path resolves differently.

**Fix:** ALWAYS use `role_path` or `MOLECULE_PROJECT_DIRECTORY` for paths
that need to resolve relative to the project root:

```yaml
# GOOD — resolves correctly regardless of which scenario runs
src: "{{ role_path }}/../../{{ lxc_ct_template_path }}"

# BAD — breaks when called from non-default scenarios
src: "../../images/{{ lxc_ct_template }}"
```

Previous bug: `proxmox_lxc` template upload failed with "Could not find
or access" when run from the `proxmox-lxc` scenario because the relative
path resolved from `molecule/proxmox-lxc/` instead of the project root.

## Before running tests

1. Source test env: `set -a; source test.env; set +a`
2. Verify SSH: `ssh root@$PRIMARY_HOST hostname`
3. Build custom images (required): `./build-images.sh`
4. Verify images exist: `ls images/openwrt-router-*.img.gz images/openwrt-mesh-lxc-*-rootfs.tar.gz images/debian-*.tar.zst`
5. If previous run left host in bad state, power-cycle the machine

## Molecule platform groups

The molecule platform config MUST include all flavor groups that any play in
`site.yml` targets. When a new flavor group is added to `inventory/hosts.yml`,
it MUST also be added to `molecule/default/molecule.yml`:

```yaml
platforms:
  - name: home
    groups:
      - proxmox
      - router_nodes
      - vpn_nodes
      - dns_nodes
      - wifi_nodes
      - monitoring_nodes
      - service_nodes
      - media_nodes
      - desktop_nodes
  - name: mesh1
    groups:
      - proxmox
      - lan_hosts
      - vpn_nodes
      - wifi_nodes
  - name: ai
    groups:
      - proxmox
      - vpn_nodes
  - name: mesh2
    groups:
      - proxmox
      - vpn_nodes
      - wifi_nodes
```

All 4 hosts are tested in the default scenario. `home` runs all services.
`ai` and `mesh2` are directly reachable on the supernet (no ProxyJump).
`mesh1` is behind OpenWrt (requires ProxyJump). All 4 are in `vpn_nodes` —
WireGuard deploys on all 4 in parallel within the same play.

Without this, provision plays targeting flavor groups will skip hosts.

## Cleanup requirements for repeatable runs

The cleanup playbook MUST restore the host to a clean state using
**service-specific cleanup** — destroy only known project VMs and containers
by explicit VMID from `group_vars/all.yml`.

1. Destroy project VMs by explicit VMID (check existence first with
   `qm status`, then stop + destroy). Current VMIDs: OpenWrt (100).
2. Destroy project containers by explicit VMID (check with `pct status`,
   then stop + destroy). Current VMIDs: WireGuard (101), Pi-hole (102),
   Mesh WiFi (103), Netdata (500), rsyslog (501).
3. Unbind all devices from `vfio-pci`. Without this, WiFi hardware is invisible.
4. Remove modprobe blacklist files (`/etc/modprobe.d/blacklist-wifi.conf`, `/etc/modprobe.d/vfio-pci.conf`).
5. Reload WiFi kernel modules: `modprobe -r iwlmvm iwlwifi; modprobe iwlwifi`.
6. Rescan PCI bus: `echo 1 > /sys/bus/pci/rescan`.
7. Tear down stale bridges (skip vmbr0 management bridge).
8. `ifup --all --force` to restore interfaces.

Steps 3-6 are critical. Without them, the next run cannot detect WiFi hardware.

**NEVER use blanket `qm list` / `pct list` iteration to destroy VMs and
containers.** This destroys non-project resources on shared hosts, is slower
than explicit VMIDs, and provides no benefit. Use explicit VMIDs defined in
`group_vars/all.yml`. Check existence with `qm status` / `pct status` before
attempting stop + destroy.

Previous bug: molecule cleanup used `qm list | awk` and `pct list | awk` to
discover and destroy ALL VMs/containers. This was unsafe on shared test hosts
and forced a full rebuild of every resource on every test run.

**NEVER delete LXC templates from `/var/lib/vz/template/cache/` in molecule
cleanup.** The `proxmox_lxc` role checks `pveam list` and skips upload when
the template is already cached. Deleting templates forces re-upload of ~820MB
across 4 hosts on every run. Template deletion is only appropriate in
`playbooks/cleanup.yml` behind `[full-restore, clean]` tags.

Previous bug: molecule cleanup deleted all cached templates. Each subsequent
`molecule test` re-uploaded pihole (205MB), rsyslog (143MB), netdata (315MB),
wireguard (143MB), and openwrt-mesh (14MB) to every host. Removing the
template deletion saved ~7 minutes on a 4-node test run.

**NEVER restore the host config from backup archive in molecule cleanup.**
The explicit file removal tasks already remove all ansible-managed files.
The backup restore is redundant and adds ~15s per host. Backup restore is
only appropriate in `playbooks/cleanup.yml` behind `[full-restore]` tag.

**Make `update-initramfs` conditional** on PCI passthrough config having been
present. Check `stat` on `/etc/modprobe.d/vfio-pci.conf` before removing it.
Only run `update-initramfs` when the file existed. This saves ~20s per host
when PCI passthrough wasn't configured.

## Verify completeness requirements

Every configured feature MUST have a corresponding assertion in `verify.yml`.
"Is it running?" is not enough. A router VM that is running but has no DHCP or
has a colliding subnet is a production outage.

Minimum assertion categories per VM type:

| Category | Example assertions |
|---|---|
| VM state | Running, correct VMID |
| VM auto-start | `onboot=1`, `startup order=N` |
| NIC topology | `net0` on correct bridge (WAN vs LAN) |
| Network config | WAN has IP, LAN subnet doesn't collide with WAN |
| Services | DHCP start/limit/leasetime configured, firewall running |
| Optional features | MAC cloning (when `WAN_MAC` set), mesh (when WiFi present) |
| Backup | Manifest exists, has required fields, archive file on disk |
| Deploy tracking | `vm_builds.fact` exists, contains expected plays |
| State files | `.state/addresses.json` exists, contains host + IPs |

Previous bug: DHCP was configured but never verified. A broken DHCP config
would pass all tests and only be caught when clients couldn't get addresses.

## Extending verify for new VM types

Add assertions to `molecule/default/verify.yml` per VM type. Verify from the
Proxmox host using `qm`, `sshpass`, or SSH ProxyJump. Avoid running Ansible
directly against VMs in verify — use raw commands instead.

```yaml
- name: Verify HomeAssistant VM
  hosts: proxmox
  gather_facts: false
  tasks:
    - name: Check VM is running
      ansible.builtin.command:
        cmd: qm status {{ homeassistant_vm_id }}
      register: ha_status
      changed_when: false

    - name: Assert VM is running
      ansible.builtin.assert:
        that: "'running' in ha_status.stdout"
        fail_msg: "HomeAssistant VM is not running"
```

## LXC container verify checklist

LXC services need deeper verification than "container is running." The
following categories should be tested for every LXC service:

| Category | Example assertions |
|---|---|
| Container state | Running, correct VMID |
| Auto-start | `onboot=1`, `startup order=N` |
| Baked config | All baked config files exist inside container |
| Config validation | Service config passes validation (e.g., `rsyslogd -N1`, `nginx -t`) |
| Service state | `systemctl is-active <service>` |
| Network listener | Port is listening (`ss -tlnp`) |
| Functional test | Send data, verify it's received/processed |
| Multi-source test | Multiple senders/tags produce correctly separated output |
| No-leak test | Remote data stays in remote logs, not local syslog |
| Restart resilience | Stop/start service, verify listener and reception recover |
| Resource usage | Memory within allocation (e.g., `free -m` < allocated) |
| Logrotate | Config exists and passes `logrotate --debug` validation |
| Deploy stamp | `stamp.plays` contains the service entry |

The no-leak test is critical for services that use rsyslog's `stop` directive
to prevent remote messages from polluting local logs. Without it, the `stop`
directive could silently break and double-log everything.

Previous bug: rsyslog used a named ruleset for TCP input. Messages in the
named ruleset never reached the default ruleset, so forwarding config
deployed at number 20 never saw remote messages. The no-leak test catches
this class of routing error.

## Hard-fail over graceful degradation

NEVER add "graceful skip" logic for hardware expected on every host. Silent
skips mask fixable problems (wrong BIOS settings, missing drivers).

- **iGPU**: REQUIRED. `proxmox_igpu` hard-fails if absent. Supports both Intel
  (i915) and AMD (amdgpu). Never assume Intel-only.
- **WiFi + VT-d/IOMMU**: REQUIRED for passthrough. `proxmox_pci_passthrough`
  hard-fails if IOMMU is not active or groups are invalid.
- **NIC count**: OK to handle dynamically — hardware legitimately varies.
- Previous bug: graceful skip of IOMMU masked a disabled VT-d BIOS setting on
  mesh1 for an entire test cycle. A 30-second BIOS fix was hidden behind
  "WARNING: skipping passthrough."

## Cleanup completeness

**CRITICAL — cleanup must NEVER destroy access credentials:**
- NEVER remove `/root/.ssh/authorized_keys` from any host. SSH keys are operator prerequisites. Removing them permanently locks out remote nodes — potentially thousands of miles away with no physical console access.
- NEVER remove Proxmox API tokens (`pveum user token remove`). API tokens are operator-created, stored in `.env`, and required for all Ansible runs.
- NEVER remove ANY credential the operator created manually. Apply this test to EVERY cleanup task: "Did the converge/playbook create this?" If no → do not touch it.
- Previous bug: mesh1-infra cleanup removed both authorized_keys and the API token from a LAN satellite node, permanently locking out all SSH and API access.

When a role writes a new file to the Proxmox host, ALWAYS add it to the removal list in:
- `molecule/default/cleanup.yml` (test cleanup — primary hosts play)
- `molecule/default/cleanup_lan_host.yml` (test cleanup — LAN hosts tasks)
- `tasks/cleanup_lan_host.yml` (production cleanup — LAN hosts tasks)
- `playbooks/cleanup.yml` (production cleanup — primary hosts play)

When a role writes a local state file (e.g., `.state/addresses.json`), ALWAYS add a `delegate_to: localhost` cleanup task to remove it.

**Parity rule:** All cleanup paths MUST remove the same set of files.
When adding a file to one, ALWAYS add it to all others. Current managed files:

- Host config: `ansible-bridges.conf`, `ansible-proxmox-lan.conf` (legacy),
  `ansible-temp-lan.conf` (test workaround)
- Module config: `blacklist-wifi.conf`, `vfio-pci.conf`, `wireguard.conf`
- Apt repos: `pve-no-subscription.sources` (added by igpu), enterprise
  repos (renamed to `.disabled`, restored on cleanup)
- VM images: `/tmp/openwrt-upload*` (temporary during import)
- Templates: `/var/lib/vz/template/cache/*` — ONLY in `playbooks/cleanup.yml`
  behind `[full-restore, clean]` tags. NEVER in molecule cleanup (see above).
- Hookscripts: `/var/lib/vz/snippets/mesh-wifi-phy-*.sh`
- Facts: `vm_builds.fact`
- Local: `.state/addresses.json`, `.env.generated`, `test.env.generated`

Previous bug: `ansible-proxmox-lan.conf` was deployed by `openwrt_configure` but not removed by cleanup, causing stale LAN management IPs on subsequent test runs.

## Test performance optimization

The full 4-node `molecule test` takes ~13-14 minutes. Most time goes to
template uploads, NTP sync, `pct_remote` overhead, and SSH round trips in
verify. Apply these rules to avoid wasting time:

### Template caching
- NEVER delete templates in molecule cleanup. Keep them cached on Proxmox
  hosts. The `proxmox_lxc` role's `pveam list` check skips upload when
  cached. Template deletion forces re-upload of ~820MB across 4 hosts.
- Template deletion is only valid in production cleanup behind
  `[full-restore, clean]` tags.

### NTP sync
- ALWAYS check clock skew BEFORE running the full NTP burst+sleep+makestep
  sequence. Use `chronyc -n tracking | awk '/System time/{print $4}'` to
  get skew in seconds. Only sync when skew > 30s.
- The NTP sync takes ~7s per host (sleep 6 + burst). With 4 hosts × 3
  sync points (site.yml primary, site.yml LAN, proxmox_igpu), that's 84s
  wasted when clocks are accurate.

### pct_remote task count
- Each `pct_remote` task opens a new paramiko SSH connection → `pct exec`
  pipeline. Each task takes 15-60 seconds depending on payload.
- MINIMIZE the number of tasks in configure roles. Base system config that
  is identical across all containers belongs in the image, NOT the
  configure role.
- Previous bug: `netdata_configure` deployed a systemd override (3 tasks:
  mkdir, copy, daemon_reload) via `pct_remote`. With 4 containers, this
  added ~12 minutes. Moving the override to the image saved 38% of the
  per-feature test time.

### apt cache
- Set `cache_valid_time: 86400` (24h) for apt tasks, not 3600 (1h).
  Test machines rarely have stale packages. The shorter interval triggers
  `apt-get update` on every run when the cache expires between test cycles.

### Selective image rebuilds
- Use `./build-images.sh --host <ip> --only <target>` to rebuild a single
  image (mesh, router, pihole, rsyslog, netdata, wireguard). Full rebuilds
  take ~15 min; selective rebuilds take ~2-3 min.
- Every service MUST have a custom image with ALL packages baked in.
  WireGuard was the last service converted (from stock Debian template to
  custom image). ZERO configure roles should install packages at runtime.

### Verify phase: consolidate pct config reads
- NEVER call `pct config <id>` multiple times for the same container. Each
  call is an SSH round trip (~1-2s). Read the full config once, register it,
  then assert against the registered output using Jinja filters.
- Pattern: `ansible.builtin.command: cmd: pct config {{ ct_id }}` →
  register as `_ct_cfg` → assert `"'onboot: 1' in _ct_cfg.stdout"`,
  `_ct_cfg.stdout is regex('startup:.*order=3')`, etc.
- For IP extraction from cached config: use
  `{{ _ct_cfg.stdout | regex_search('ip=([^/,]+)', '\\1') | first }}`.
- Previous bug: verify.yml had 20 individual `pct config` calls (3-6 per
  container). Consolidating to 6 (one per container type) eliminated 14
  SSH round trips per host × 4 hosts = 56 unnecessary SSH connections.

### Verify phase: batch pct exec calls per container
- NEVER run multiple individual `pct exec` calls against the same container
  when the checks are independent. Batch them into a single `pct exec`
  with `/bin/sh -c '...'` and key=value output for easy parsing.
- Pattern: combine health checks into one shell script that outputs
  `KEY=value` lines. Parse with `'KEY=value' in result.stdout` or
  `result.stdout | regex_search('KEY=(\d+)', '\\1') | first`.
- Example (WireGuard — 7 checks → 1 pct exec):
  ```yaml
  - name: Run WireGuard container health checks (single pct exec)
    ansible.builtin.shell:
      cmd: >-
        pct exec {{ ct_id }} -- /bin/sh -c '
        echo "IFACE=$(ip link show wg0 >/dev/null 2>&1 && echo ok || echo missing)";
        echo "SVC=$(systemctl is-enabled wg-quick@wg0 2>/dev/null || echo unknown)";
        echo "NAT=$(iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null && echo present || echo missing)"
        '
    register: wg_health
    changed_when: false

  - name: Assert wg0 exists
    ansible.builtin.assert:
      that: "'IFACE=ok' in wg_health.stdout"
  ```
- Keep tasks with retry logic (e.g., `curl` with `retries/until`) as
  separate `pct exec` calls since they need per-attempt control.
- Keep timing-dependent tests (log reception after `pause`) as separate
  calls since they depend on wall-clock ordering.
- Previous savings: batching eliminated ~43 individual SSH calls in the
  default verify, saving ~60-80 seconds across 4 hosts.

### Verify phase: merge plays with same hosts target
- When two verify plays target the same `hosts:` group with the same
  `gather_facts:` setting, merge them into one play. Each play has startup
  overhead (SSH connection setup, fact gathering if enabled, variable
  resolution).
- Example: rsyslog and Netdata both target `monitoring_nodes` with
  `gather_facts: false`. Merging them into one play eliminates one play
  startup per host.

### Wait/pause tuning
- OpenWrt detached restart scripts take ~18s (8s explicit sleep + ~10s
  service restarts). The post-script pause should be 20s, not 30s.
- OpenWrt VM first boot SSH: `delay: 10, timeout: 120` (not 15/180).
  Typical first boot is 30-60s.
- LXC container networking: `delay: 3` (not 4). DHCP usually completes on
  first probe.
- WiFi PHY detection: `delay: 3` (not 5). PHY appears within seconds or
  indicates a real problem.
- Apt retry delay: `delay: 10` (not 15). Transient apt failures recover
  quickly.
- Verify-phase SSH waits: reduce `delay` and `timeout` for services that
  are already confirmed running from converge. The verify wait is a safety
  check, not a first-boot wait.

## Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH broken or host down | Check `PRIMARY_HOST`, verify SSH |
| `community.proxmox` not found | Collections missing | `ansible-galaxy collection install -r requirements.yml` |
| Bridge numbers keep incrementing | Cleanup didn't remove bridges | `./cleanup.sh clean test.env` |
| WiFi radios=0 after converge | PCI passthrough not cleaned up | Ensure cleanup unbinds vfio-pci, reloads modules, rescans PCI |
| `Timeout waiting for SSH` | Network restart dropped connection | Verify SSH args include `ConnectTimeout=10`, `ServerAliveInterval=15` |
| `opkg update` fails with HTTPS error | HTTPS not supported | Ensure `sed -i 's\|https://\|http://\|g'` runs before `opkg update` |
| `opkg update` fails with "Operation not permitted" | Firewall zones stale after network restart | Restart firewall after network topology change, before outbound connections |
| Conflict detection script returns wrong result | BusyBox `ip neigh show` doesn't support IP filter args | Use `/proc/net/arp` with `awk` instead of `ip neigh show dev eth0 <ip>` |
| MAC stored without colons in UCI | BusyBox `tr -d '[:space:]'` deletes `:` | Use `tr -d ' \t\n\r'` instead of `tr -d '[:space:]'` |
| GUI reachability test fails on OpenWrt | BusyBox `nc` doesn't support `-w` timeout flag | Use `(echo QUIT \| nc HOST PORT) </dev/null` |
| VM reachable by IPv6 but not IPv4 on LAN bridge | Stale LAN-subnet IP on another bridge creates duplicate /24 route | Remove conflicting IPs from non-LAN bridges; kernel picks the first route |
| `ifreload -a` doesn't start DHCP client | Separate `inet dhcp` file conflicts with `inet manual` in bridges.conf | Modify bridge stanza in-place; never use a second config file for same iface |
| Route filter hides default route | `ip route show default dev eth0` misses routes using OpenWrt aliases | Use `ip route show default` without `dev` filter |
| `deprecated-local-action` lint error | Used `local_action` syntax | Replace with `delegate_to: localhost` (see below) |
| Stale LAN IP after cleanup | Missing config file in cleanup list | Add the file to both cleanup playbooks |
| `igpu_available is not defined` in verify | Facts from converge not available in verify | Re-include the role in verify.yml (see fact scoping section) |
| `Could not find or access` template | Relative path resolved from scenario dir | Use `role_path` for paths (see path resolution section) |
| `ModuleNotFoundError: paramiko` | Missing Python dep for `pct_remote` | `pip install paramiko` and add to `requirements.txt` |
| `apt-get update` hangs on Proxmox host | Enterprise repos unreachable without subscription | Rename to `.disabled`, add no-subscription repo (see proxmox-safety rule) |
| `lsmod \| grep -q` returns rc=141 | SIGPIPE from `grep -q` with `pipefail` | Use `grep -c` instead (see proxmox-safety rule) |
| `intel-media-va-driver-non-free` not found | Wrong package name for Debian version | Check packages with `apt-cache search`; use `intel-media-va-driver` |
| Per-feature verify passes with 0 assertions | Dynamic group empty in verify (separate invocation) | Add group reconstruction play at top of verify.yml |
| Rollback play targets 0 hosts | Dynamic group empty in cleanup (separate invocation) | Add reconstruction play in cleanup.yml, tagged with all rollback tags |
| SSH auth fails after security rollback | Rollback re-enabled password but didn't clear root password | Rollback MUST clear `/etc/shadow` root hash to restore empty-password baseline |
| `uci: Invalid argument` on mesh WiFi radio | WiFi PHY namespace-moved after boot; wireless config not auto-generated | Run `wifi config` to generate `/etc/config/wireless` before `uci set wireless.radio*` |
| All hosts unreachable during cleanup | PCI passthrough cleanup + initramfs update can trigger host reboot or network loss | Check host reachability before re-running; may need physical power cycle |

## Ansible syntax pitfalls with `raw:` heredocs

When using `ansible.builtin.raw: |` with shell heredocs (e.g., `cat << 'EOF'`),
the Ansible argument parser may fail on content that looks like Jinja2:

- `${var:-default}` — the `${...}` is misinterpreted. Use `$var` or avoid defaults.
- `|| true` inside heredocs — can confuse the parser in some contexts.
- POSIX character classes in `tr` (e.g., `[:space:]`) — the colons can interact
  with YAML/Jinja2 parsing.

ALWAYS run `ansible-playbook --syntax-check playbooks/site.yml` after modifying
`raw:` tasks with heredocs. The syntax check catches these before deployment.

## Shell task safety

ALWAYS use `set -o pipefail` in any shell task that contains a pipeline
(`|`). Without it, only the exit code of the LAST command in the pipeline
is checked — failures in earlier commands are silently swallowed.

ALWAYS set `executable: /bin/bash` on shell tasks that use bash-specific
features (`set -o pipefail`, `{print $3}`, process substitution). The
default shell may be `/bin/sh` which doesn't support `pipefail`.

**Exception:** `ansible.builtin.raw` tasks and `{{ openwrt_ssh }}` commands
that run on OpenWrt/BusyBox ash. BusyBox ash does NOT support `pipefail`.
Do not add it to those commands.

ALWAYS use the block scalar (`cmd: |`) format for pipefail commands, not
the folded scalar (`cmd: >-`). This keeps `set -o pipefail` on its own
line and avoids YAML joining it with the command.

```yaml
# BAD — if `ip route` fails, awk sees empty stdin, returns success
- name: Get gateway
  ansible.builtin.shell:
    cmd: ip route show default | awk '{print $3}' | head -1

# BAD — folded scalar joins pipefail with command on one line
- name: Get gateway
  ansible.builtin.shell:
    cmd: >-
      set -o pipefail
      ip route show default | awk '{print $3}' | head -1

# GOOD — pipeline failure propagates correctly
- name: Get gateway
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      ip route show default | awk '{print $3}' | head -1
    executable: /bin/bash
```

### Audit pattern

This class of bug is silent and recurring. Periodically scan the codebase:

```bash
# Find shell tasks with pipes but no pipefail
rg -l 'ansible.builtin.shell' roles/ molecule/ playbooks/ | \
  xargs rg -l '\|' | sort -u
# Then manually check each file for set -o pipefail
```

Previous bug: a single audit pass found missing `pipefail` in 6 roles and
both cleanup playbooks. All were silent — no test caught them because the
upstream commands happened to succeed during testing.

## Deprecated Ansible patterns

NEVER use `local_action`. It was deprecated in Ansible and trips `deprecated-local-action` lint errors.

```yaml
# BAD — deprecated
- name: Do something locally
  local_action:
    module: ansible.builtin.file
    path: /tmp/foo
    state: directory

# GOOD — modern equivalent
- name: Do something locally
  ansible.builtin.file:
    path: /tmp/foo
    state: directory
  delegate_to: localhost
```

NEVER use short module names (e.g., `command`). ALWAYS use FQCNs (e.g., `ansible.builtin.command`).

## Permanent diagnostics in playbooks

Every VM build playbook SHOULD include diagnostic tasks at key milestones
(post-bootstrap, post-restart, final state). These run on every build and
provide the debug output needed when things fail.

Rules for diagnostic tasks:

1. ALWAYS use `changed_when: false` and `failed_when: false` — diagnostics must
   never break the build.
2. Register the output and use `debug: var:` to display it. This ensures the
   output appears in the Ansible log and terminal files.
3. Include kernel-level checks (`dmesg` errors) — these are often the smoking
   gun when application-level symptoms are misleading.
4. Include the actual protocol test (not just ping). ICMP working does NOT
   mean TCP/HTTP works.
5. When you add ad-hoc debug tasks during troubleshooting, generalize them and
   make them permanent before closing the issue.

Previous bug: `ping 8.8.8.8` worked but `wget` segfaulted. The root cause
(IPv6 DAD failure from duplicate MAC) was only visible in `dmesg`. Had
permanent diagnostics been in place, the first run would have shown the issue.

## Diagnosing failures — priority order

1. **Terminal output**: grep for `FAILED`, `fatal:`, `UNREACHABLE`
2. **Kernel logs**: `dmesg | grep -iE 'error|segfault|duplicate'`
3. **Interface/bridge state**: `ip addr`, `ip route`, bridge membership
4. **Firewall state**: zone bindings, nftables chains
5. **Protocol-level test**: test with the actual protocol (TCP, HTTP) not just ICMP

## Molecule env var handling

Molecule's `provisioner.env` section uses `${VAR_NAME}` syntax for variable
substitution. NEVER use shell-style defaults like `${VAR:-default}` — the
parser treats `:-}` as part of the variable name and fails with "Invalid
placeholder in string."

For required env vars: use `${VAR_NAME}` and ensure the var is always set
in `test.env` (sourced before `molecule test`).

For optional env vars: do NOT add them to `provisioner.env` at all. The
role's `defaults/main.yml` already uses `lookup('env', 'VAR_NAME') | default('', true)`,
which reads directly from the shell environment. Ansible inherits the full
shell environment regardless of what `provisioner.env` lists.

Previous bug: `RSYSLOG_HOME_SERVER: ${RSYSLOG_HOME_SERVER:-}` in
`molecule.yml` caused "Invalid placeholder in string" and prevented all
molecule runs from starting.

## Multi-node E2E testing

When a service needs testing on all 4 nodes (home, mesh1, ai, mesh2), add the
flavor group to ALL platforms in the molecule default scenario — not just the
static inventory. This is a test-only change that doesn't affect production.

Verify tasks for multi-node scenarios MUST avoid recomputing IPs. Instead,
read the full container config once, then extract the IP from the cached
output:

```yaml
- name: Read container config (single call)
  ansible.builtin.command:
    cmd: pct config {{ ct_id }}
  register: _ct_cfg
  changed_when: false

- name: Extract container IP from cached config
  ansible.builtin.set_fact:
    _ct_ip: "{{ _ct_cfg.stdout | regex_search('ip=([^/,]+)', '\\1') | first }}"
```

This pattern:
- Works for both LAN and WAN hosts
- Eliminates `default('10.10.10.1', true)` fallbacks in verify tasks
- Avoids index computation drift between per-feature and E2E scenarios
- Serves as a hard-fail if the container doesn't exist (empty stdout)
- Reuses the same registered config for onboot, startup order, features,
  and IP assertions — eliminating redundant SSH round trips

Previous bug: rsyslog verify computed IP as `LAN_GATEWAY + offset`. This only
worked for LAN hosts and used a fallback default for the gateway. With 4-node
testing, WAN hosts had wrong computed IPs. Fixed by querying `pct config`.

## Verify task conventions

1. **Fail-fast IP validation.** After retrieving a container IP (via `pct config`),
   assert it is non-empty and doesn't collide with the host IP BEFORE any
   functional tests (log send, DNS query, etc.). Wrong-IP errors are confusing
   when they surface as "log not found" 10 tasks later.

2. **No `failed_when: false` on client sends.** When sending test traffic
   (`logger --tcp`, `dig`, `curl`), let the task fail immediately on connection
   error. The real failure message ("Connection refused") is far more
   informative than a downstream "expected output not found" assertion.
   Reserve `failed_when: false` for commands whose rc is checked by a later
   assertion (e.g., `grep -c` that returns rc=1 on no match).

3. **`set -o pipefail` only on pipelines.** Never add `pipefail` to
   single-command or `&&`-chained tasks. It adds noise and obscures the
   convention that "pipefail = there is a pipe here." Correct: `pct exec ...
   | grep -c ...` with pipefail. Wrong: `logger --tcp ...` with pipefail.

4. **Use standard modules for service management.** Prefer
   `ansible.builtin.systemd` over `ansible.builtin.command: cmd: systemctl
   restart ...` for restarts and state management. Both work over `pct_remote`,
   but the module reports state accurately and follows Ansible conventions.
   Use `command` only for status checks (`systemctl is-active`) or validation
   (`rsyslogd -N1`).

## Wake-on-LAN testing

WoL (`wol.sh`) is a recovery utility, NOT a testable service. NEVER include
WoL in the Molecule test suite — it requires a host to be powered off, which
is destructive and non-idempotent.

- WoL is verified manually after physical power events, not in CI/CD.
- The script's correctness is validated by code review, not automated testing.
- USB ethernet adapters do not support WoL. Hosts with USB-only networking
  need alternative recovery (smart plug, IPMI, manual power button).

## Lint configuration

- `ansible-lint`: `.ansible-lint` — production profile, skips `command-instead-of-module` for Proxmox shell tasks
- `yamllint`: `.yamllint.yml` — 160-char lines, relaxed comment spacing
- Run manually: `ansible-lint && yamllint .`

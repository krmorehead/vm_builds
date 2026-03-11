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

molecule test          # full pipeline
molecule converge      # run playbook only
molecule verify        # run assertions only
molecule cleanup       # reset test host
```

## Molecule pipeline sequence

`molecule test` runs these phases in order:
1. `dependency` — install Galaxy requirements
2. `cleanup` — reset host from previous runs
3. `syntax` — ansible syntax check
4. `converge` — run `playbooks/site.yml`
5. `verify` — run `molecule/default/verify.yml`
6. `cleanup` — reset host after test

There is NO `lint` phase in the Molecule config. Run `ansible-lint` and `yamllint` separately.

## Architecture

- **Driver**: `delegated` (real Proxmox hardware, not Docker)
- **Platform**: test machine IP from `PROXMOX_HOST` env var
- **Platform groups**: `proxmox` + all flavor groups (e.g., `router_nodes`)
- **Provisioner**: `playbooks/site.yml`
- **Cleanup**: `playbooks/cleanup.yml --tags clean`
- **Config**: `molecule/default/molecule.yml`

## Baseline testing model

The **baseline** is the state after `molecule/default` converges successfully:
router VM running, WAN/LAN configured, DHCP serving, firewall active. All
per-feature molecule scenarios start from this baseline and only converge/revert
their own changes.

```
Scenario Hierarchy
├── molecule/default/              Full integration (rebuild everything)
│   ├── converge.yml               imports site.yml
│   ├── verify.yml                 ALL baseline + feature assertions
│   └── cleanup.yml                full cleanup (destroy VMs, restore host)
│
├── molecule/openwrt-security/     Per-feature (assumes baseline exists)
│   ├── converge.yml               runs only security plays via tags
│   ├── verify.yml                 security-specific assertions only
│   └── cleanup.yml                runs security rollback only
│
├── molecule/openwrt-vlans/        Per-feature
│   ├── converge.yml               runs only VLAN plays via tags
│   ├── verify.yml                 VLAN-specific assertions only
│   └── cleanup.yml                runs VLAN rollback only
│
└── ...
```

**Why:** Full `molecule test` takes 4-5 minutes. Per-feature scenarios take
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
rapid iteration on iGPU driver issues (4+ fix cycles in one session)
without waiting for the full 4-minute default test each time.

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
    PROXMOX_API_TOKEN_SECRET: ${PROXMOX_API_TOKEN_SECRET}
    PROXMOX_HOST: ${PROXMOX_HOST}
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
state doesn't persist across molecule runs:

```yaml
# molecule/openwrt-security/converge.yml
---
- name: Populate OpenWrt dynamic group from baseline
  hosts: proxmox
  gather_facts: true
  tasks:
    - name: Detect OpenWrt LAN IP from Proxmox LAN bridge
      ansible.builtin.shell:
        cmd: |
          set -o pipefail
          lan_br=$(ip -o route show default | awk '{print $5}' | head -1)
          # Get first non-WAN bridge
          for br in /sys/class/net/vmbr*/; do
            brname=$(basename "$br")
            [ "$brname" = "$lan_br" ] && continue
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

- name: Apply security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Include security hardening tasks
      ansible.builtin.include_role:
        name: openwrt_configure
        tasks_from: security.yml
```

### Per-feature cleanup pattern

Per-feature cleanup runs only the feature's rollback tag:

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
2. Verify SSH: `ssh root@$PROXMOX_HOST hostname`
3. Verify images exist: `ls images/openwrt.img images/*.tar.zst`
4. If previous run left host in bad state, power-cycle the machine

## Molecule platform groups

The molecule platform config MUST include all flavor groups that any play in
`site.yml` targets. When a new flavor group is added to `inventory/hosts.yml`,
it MUST also be added to `molecule/default/molecule.yml`:

```yaml
platforms:
  - name: home
    groups:
      - proxmox
      - router_nodes       # targets OpenWrt provision plays
      # - service_nodes    # add when service VM types are created
```

Without this, provision plays targeting flavor groups will skip the test host.

## Cleanup requirements for repeatable runs

The cleanup playbook MUST restore the host to a clean state:

1. Stop and destroy ALL VMs (`qm list` iteration, not hardcoded IDs).
2. Unbind all devices from `vfio-pci`. Without this, WiFi hardware is invisible.
3. Remove modprobe blacklist files (`/etc/modprobe.d/blacklist-wifi.conf`, `/etc/modprobe.d/vfio-pci.conf`).
4. Reload WiFi kernel modules: `modprobe -r iwlmvm iwlwifi; modprobe iwlwifi`.
5. Rescan PCI bus: `echo 1 > /sys/bus/pci/rescan`.
6. Tear down stale bridges (skip vmbr0 management bridge).
7. Restore host config from backup, `ifup --all --force`.

Steps 2-5 are critical. Without them, the next run cannot detect WiFi hardware.

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

## Cleanup completeness

When a role writes a new file to the Proxmox host, ALWAYS add it to the removal list in BOTH:
- `molecule/default/cleanup.yml` (test cleanup)
- `playbooks/cleanup.yml` (production cleanup)

When a role writes a local state file (e.g., `.state/addresses.json`), ALWAYS add a `delegate_to: localhost` cleanup task to remove it.

**Parity rule:** The two cleanup playbooks MUST remove the same set of files.
When adding a file to one, ALWAYS add it to the other. Periodically diff
the removal lists to catch drift. Current managed files:

- Host config: `ansible-bridges.conf`, `ansible-proxmox-lan.conf` (legacy),
  `ansible-temp-lan.conf` (test workaround)
- Module config: `blacklist-wifi.conf`, `vfio-pci.conf`
- Apt repos: `pve-no-subscription.sources` (added by igpu), enterprise
  repos (renamed to `.disabled`, restored on cleanup)
- Templates/images: `/tmp/openwrt.img`, `/var/lib/vz/template/cache/debian-*.tar.zst`
- Facts: `vm_builds.fact`
- Local: `.state/addresses.json`

Previous bug: `ansible-proxmox-lan.conf` was deployed by `openwrt_configure` but not removed by cleanup, causing stale LAN management IPs on subsequent test runs.

## Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH broken or host down | Check `PROXMOX_HOST`, verify SSH |
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

## Lint configuration

- `ansible-lint`: `.ansible-lint` — production profile, skips `command-instead-of-module` for Proxmox shell tasks
- `yamllint`: `.yamllint.yml` — 160-char lines, relaxed comment spacing
- Run manually: `ansible-lint && yamllint .`

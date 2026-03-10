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

## Before running tests

1. Source test env: `set -a; source test.env; set +a`
2. Verify SSH: `ssh root@$PROXMOX_HOST hostname`
3. Verify OpenWrt image exists: `ls images/openwrt.img`
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
| `deprecated-local-action` lint error | Used `local_action` syntax | Replace with `delegate_to: localhost` (see below) |
| Stale LAN IP after cleanup | Missing config file in cleanup list | Add the file to both cleanup playbooks |

## Shell task safety

ALWAYS use `set -o pipefail` in any shell task that contains a pipeline
(`|`). Without it, only the exit code of the LAST command in the pipeline
is checked — failures in earlier commands are silently swallowed.

ALWAYS set `executable: /bin/bash` on shell tasks that use bash-specific
features (`set -o pipefail`, `{print $3}`, process substitution). The
default shell may be `/bin/sh` which doesn't support `pipefail`.

```yaml
# BAD — if `ip route` fails, awk sees empty stdin, returns success
- name: Get gateway
  ansible.builtin.shell:
    cmd: ip route show default | awk '{print $3}' | head -1

# GOOD — pipeline failure propagates correctly
- name: Get gateway
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      ip route show default | awk '{print $3}' | head -1
    executable: /bin/bash
```

Previous bug: a gateway detection pipeline in `openwrt_vm` was missing
`set -o pipefail`. If `ip route` failed, the variable silently got an
empty string instead of raising an error.

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

## Lint configuration

- `ansible-lint`: `.ansible-lint` — production profile, skips `command-instead-of-module` for Proxmox shell tasks
- `yamllint`: `.yamllint.yml` — 160-char lines, relaxed comment spacing
- Run manually: `ansible-lint && yamllint .`

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

## Extending verify for new VM types

Add a new play to `molecule/default/verify.yml` per VM type:

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

Pattern: verify from the Proxmox host using `qm`, `sshpass`, or SSH ProxyJump. Avoid running Ansible directly against VMs in verify — use raw commands instead.

## Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH broken or host down | Check `PROXMOX_HOST`, verify SSH |
| `community.proxmox` not found | Collections missing | `ansible-galaxy collection install -r requirements.yml` |
| Bridge numbers keep incrementing | Cleanup didn't remove bridges | `./cleanup.sh clean test.env` |
| WiFi radios=0 after converge | PCI passthrough not cleaned up | Ensure cleanup unbinds vfio-pci, reloads modules, rescans PCI |
| `Timeout waiting for SSH` | Network restart dropped connection | Verify SSH args include `ConnectTimeout=10`, `ServerAliveInterval=15` |
| `opkg update` fails | HTTPS not supported | Ensure `sed -i 's\|https://\|http://\|g'` runs before `opkg update` |

## Lint configuration

- `ansible-lint`: `.ansible-lint` — production profile, skips `command-instead-of-module` for Proxmox shell tasks
- `yamllint`: `.yamllint.yml` — 160-char lines, relaxed comment spacing
- Run manually: `ansible-lint && yamllint .`

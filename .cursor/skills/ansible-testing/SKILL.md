---
name: ansible-testing
description: Run and validate Ansible project tests using Molecule, ansible-lint, and yamllint. Use when running tests, adding test scenarios, debugging test failures, or working with Molecule configuration in the vm_builds project.
---

# Ansible Testing

## Quick start

From the project root:

```bash
source .venv/bin/activate
set -a; source test.env; set +a

# Full test cycle (lint + syntax + create + converge + verify + cleanup + destroy)
molecule test

# Run specific phases
molecule lint         # yamllint + ansible-lint
molecule syntax       # playbook syntax check
molecule converge     # run the playbook
molecule verify       # run assertions
molecule cleanup      # reset test host
```

## Molecule architecture

- **Driver**: `delegated` (connects to a real Proxmox host, not Docker).
- **Platform**: test machine IP comes from `PROXMOX_HOST` env var.
- **Provisioner**: uses the project's `playbooks/site.yml`.
- **Cleanup**: runs `playbooks/cleanup.yml --tags clean` to reset the host.

Key config: `molecule/default/molecule.yml`

## Before running tests

1. Source the test env: `set -a; source test.env; set +a`
2. Ensure the OpenWrt image exists at `images/openwrt.img`
3. Ensure SSH access to the test machine is working: `ssh root@$PROXMOX_HOST hostname`
4. Ensure the test machine has been power-cycled if previous runs left it in a bad state

## Common failures and fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH not configured or host down | Check `PROXMOX_HOST`, verify SSH |
| Role not found | Molecule can't see project roles | Check `roles_path` in `molecule.yml` |
| `community.proxmox` not found | Collections not installed | Run `ansible-galaxy collection install -r requirements.yml` |
| Cleanup kills SSH | Using `ifdown --all` in cleanup | Replace with targeted bridge teardown |
| Bridge numbers keep incrementing | Previous bridges not cleaned up | Run `cleanup.sh clean test.env` first |

## Lint configuration

- `ansible-lint`: `.ansible-lint` (production profile, skips `command-instead-of-module` for Proxmox shell tasks)
- `yamllint`: `.yamllint.yml` (160-char lines, relaxed comments)

## Test-production parity

The test machine should mirror production hardware layout. Differences to track:
- Number and type of NICs
- WiFi card presence (PCIe passthrough testing)
- Proxmox node name (both use `home` currently)

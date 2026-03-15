---
name: molecule-testing-patterns
description: Molecule testing patterns, TDD workflow, baseline management, and scenario architecture. Use when running tests, managing test workflows, or setting up molecule scenarios.
---

# Molecule Testing Patterns

## Test-First Development

1. ALWAYS run `molecule test` after modifying roles, playbooks, or molecule config. NEVER consider a task complete until the full test suite passes.

2. When adding or changing behavior, ALWAYS update `molecule/default/verify.yml` with corresponding assertions before running the test.

3. TDD iteration pattern:
   1. Write or update the verify assertion in `verify.yml` first
   2. `molecule test` — the assertion should fail (proves the test catches the issue)
   3. Implement the fix in the role
   4. `molecule test` — the assertion should now pass
   5. Update skills/rules with lessons learned

## Test-First Reproduction

4. When a bug is reported against production, ALWAYS reproduce it on the test machine first. Replicate the production environment in `test.env` (same env vars, same image) and run `molecule test`.

5. Only involve the production host when the test machine cannot reproduce the issue.

6. Previous bug: SSH timeout on production was reproduced by adding `WAN_MAC` to `test.env`. Four fix-and-verify cycles completed in 15 minutes without touching production.

## Quick Start Commands

```bash
source .venv/bin/activate
set -a; source test.env; set +a

molecule test          # full clean-state pipeline (destroys baseline)
molecule converge      # run playbook only (preserves baseline)
molecule verify        # run assertions only
molecule cleanup       # reset test host
```

## Baseline Workflow

The OpenWrt baseline takes ~4 minutes to build. Prefer keeping it running between test runs.

**Day-to-day iteration:**
```bash
molecule converge                  # build/update baseline (idempotent)
molecule verify                    # run assertions
molecule converge -s mesh1-infra   # run layered scenario (baseline must exist)
molecule verify -s mesh1-infra     # verify layered scenario
```

**Clean-state validation (CI, pre-commit, final proof):**
```bash
molecule test                      # full pipeline — destroys everything
molecule converge                  # restore baseline for further work
```

After `molecule test`: ALWAYS re-run `molecule converge` to restore the baseline before working on layered scenarios.

## Molecule Pipeline Sequence

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
- **Provisioner**: `playbooks/site.yml` (phased: primary hosts → LAN bootstrap → services)

## 4-Node Topology

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

## Phased site.yml

`site.yml` runs in three phases to respect host reachability dependencies:

1. **Phase 1 (Primary hosts)**: `proxmox:!lan_hosts` — backup, infra, OpenWrt VM, OpenWrt configure
2. **Phase 2 (LAN satellites)**: After OpenWrt creates the LAN, bootstrap LAN hosts from `router_nodes`, then run backup + infra on `lan_hosts`
3. **Phase 3 (Services)**: Flavor groups that span both primary and LAN hosts — runs in parallel across both hosts

## Pre-Test Checklist

1. Source test env: `set -a; source test.env; set +a`
2. Verify SSH: `ssh root@$PRIMARY_HOST hostname`
3. Build custom images (required): `./build-images.sh`
4. Verify images exist: `ls images/openwrt-router-*.img.gz images/openwrt-mesh-lxc-*-rootfs.tar.gz images/debian-*.tar.zst`
5. If previous run left host in bad state, power-cycle the machine

## Common Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH broken or host down | Check `PRIMARY_HOST`, verify SSH |
| `community.proxmox` not found | Collections missing | `ansible-galaxy collection install -r requirements.yml` |
| Bridge numbers keep incrementing | Cleanup didn't remove bridges | `./cleanup.sh clean test.env` |
| WiFi radios=0 after converge | PCI passthrough not cleaned up | Ensure cleanup unbinds vfio-pci, reloads modules, rescans PCI |
| `Timeout waiting for SSH` | Network restart dropped connection | Verify SSH args include `ConnectTimeout=10`, `ServerAliveInterval=15` |

## Multi-Node E2E Testing

When a service needs testing on all 4 nodes, add the flavor group to ALL platforms in the molecule default scenario — not just the static inventory.

This is a test-only change that doesn't affect production.
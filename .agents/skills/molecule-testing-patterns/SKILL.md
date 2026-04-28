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

molecule test          # full clean-state pipeline
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
molecule test                      # full pipeline: cleanup → converge → verify
```

After `molecule test`, the baseline is left running (verify is the last step).
To do a clean rebuild for the next iteration, run `molecule converge` manually.

## Molecule Pipeline Sequence

`molecule test` runs these phases in order:
1. `dependency` — install Galaxy requirements
2. `cleanup` — reset host from previous runs
3. `syntax` — ansible syntax check
4. `prepare` — start API server, verify images exist
5. `converge` — run `playbooks/site.yml`
6. `verify` — run `molecule/default/verify.yml`

There is NO trailing cleanup or reconverge. The baseline is left running
after verify so mesh1 (LAN host) remains accessible. NEVER add cleanup
or destroy to the end of the test_sequence.

There is NO `lint` phase in the Molecule config. Run `ansible-lint` and `yamllint` separately.

## Architecture

- **Driver**: `default` with `managed: false` (real Proxmox hardware, not Docker)
- **Platforms**: 6 nodes — `home` (primary), `ai`, `mesh2`, `bridge-1`, `bridge-2` (directly reachable), `mesh1` (LAN satellite via ProxyCommand)
- **Provisioner**: `playbooks/site.yml` (phased: primary hosts → LAN bootstrap → services)

## 6-Node Topology

```
ISP Router (192.168.86.x supernet)
  |
Switch
  |            |              |           |          |
Home          AI Node       Mesh2     Bridge-1   Bridge-2
(primary)     .220          .211        .230       .231
  |
  |-- OpenWrt VM (10.10.10.1)
  |     |
  |     LAN bridge (10.10.10.x)
  |       |
  |     Mesh1 (10.10.10.210)
```

- **home**, **ai**, **mesh2**, **bridge-1**, **bridge-2**: directly reachable on the supernet (no proxy)
- **mesh1**: behind home's OpenWrt, reachable via ProxyCommand through home
- `home` is the only `router_nodes` member (runs OpenWrt VM)
- `ai` is in `gaming_nodes` — Gaming LXC with Sunshine
- `mesh1` is in `streaming_nodes` — Moonlight streaming client
- `bridge-1` and `bridge-2` are in `bridge_nodes` — WiFi bridge AP and STA
- All 6 nodes are in `kiosk_nodes` (every host needs a Manager)

## Use the 6 nodes intelligently for different tests

Each molecule scenario can assign different groups to the same host. A host
is NOT locked into one role across all scenarios.

NEVER co-locate a streaming server and client on the same host. If ai runs
Sunshine (gaming_nodes), ai CANNOT also run Moonlight (streaming_nodes).

## Phased site.yml

`site.yml` runs in three phases to respect host reachability dependencies:

1. **Phase 1 (Primary hosts)**: `proxmox:!lan_hosts` — backup, infra, OpenWrt VM, OpenWrt configure
2. **Phase 2 (LAN satellites)**: After OpenWrt creates the LAN, bootstrap LAN hosts from `router_nodes`, then run backup + infra on `lan_hosts`
3. **Phase 3 (Services)**: Flavor groups that span both primary and LAN hosts — runs in parallel across all hosts

## Pre-Test Checklist

1. Source test env: `set -a; source test.env; set +a`
2. Verify access: `curl -sf http://localhost:$WEBUI_PORT/api/fleet/health` (if base state up) or `ansible home -m ping` (first run)
3. Build custom images (required): `scripts/build-images.sh`
4. Verify images exist: `ls images/openwrt-router-*.img.gz images/openwrt-mesh-lxc-*-rootfs.tar.gz images/debian-*.tar.zst`
5. If previous run left host in bad state, power-cycle the machine

## Common Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | Host down or network broken | Check `curl $CALLHOME_URL/api/fleet/health`, then Ansible access |
| `community.proxmox` not found | Collections missing | `ansible-galaxy collection install -r requirements.yml` |
| Bridge numbers keep incrementing | Cleanup didn't remove bridges | `scripts/cleanup.sh clean test.env` |
| WiFi radios=0 after converge | PCI passthrough not cleaned up | Ensure cleanup unbinds vfio-pci, reloads modules, rescans PCI |
| `Timeout waiting for SSH` | Network restart dropped connection | Verify SSH args include `ConnectTimeout=10`, `ServerAliveInterval=15` |

## Multi-Node E2E Testing

When a service needs testing on all 6 nodes, add the flavor group to ALL platforms in the molecule default scenario — not just the static inventory.

This is a test-only change that doesn't affect production.

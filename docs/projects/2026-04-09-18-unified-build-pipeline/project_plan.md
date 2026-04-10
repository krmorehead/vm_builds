# Unified Build Pipeline

## Status: COMPLETE

## Overview

The build/cleanup pipeline is currently split between molecule (testing) and
production (`build.py` / `playbooks/cleanup.yml`). This project unifies them
so test and production always use the same code path — eliminating the class
of bugs that occur when cleanup logic diverges between environments.

## Type

Cross-cutting infrastructure improvement (build and test pipeline)

## Motivation: Current Build Issues

### 1. Containers surviving cleanup (root cause: divergent cleanup logic)

During the 2026-04-09 test cycle, old containers with stale IPs survived
molecule cleanup on WAN hosts (mesh2, bridge-1, bridge-2). The molecule
cleanup (`molecule/default/cleanup.yml`, 380 lines) and production cleanup
(`playbooks/cleanup.yml`, 826 lines) overlap ~60% but differ in:

- **SSH timeout/keepalive parameters** for LAN host cleanup
- **File removal lists** (molecule has Desktop SSH keys, production doesn't)
- **`qm stop` timeout handling** (molecule uses `timeout 180`, production doesn't)
- **DHCP lease cleanup regex** (molecule uses `grep -oP`, production uses `sed`)

When molecule cleanup missed containers, the next converge reused stale
containers with old IP allocations (the +200 offset, now corrected to +150).
This created IP collisions — specifically, kiosk CT 401 on bridge-1 got
`192.168.86.220`, which is ai's management IP. The result: alternating SSH
host keys when connecting to ai (sometimes hitting the real host, sometimes
the rogue container), leading to hours of debugging.

### 2. Dual maintenance burden

Every new service added to the project requires updating cleanup logic in
TWO places:
- `molecule/default/cleanup.yml` — VMID lists, file removal, PCI cleanup
- `playbooks/cleanup.yml` — same VMID lists, same files, same PCI logic

This violates DRY. When one gets updated but not the other (which has
happened), the test and production environments diverge silently.

### 3. `cleanup_lan_host.yml` duplication

Two copies of the LAN host cleanup task file exist:
- `molecule/default/cleanup_lan_host.yml` — molecule's version (better SSH timeouts)
- `tasks/cleanup_lan_host.yml` — production's version (different regex, no timeouts)

The molecule version evolved independently with SSH keepalives and timeout
wrapping that never made it back to the production copy.

### 4. What's working well (and should NOT change)

The build side is already unified and follows community standards:

| Component | How it works | DRY? |
|---|---|---|
| **Build playbook** | `converge.yml` imports `site.yml` — one line | YES |
| **SuperManager deploys** | `deploy.py` → `data.build_deploy_command()` → `build.py` → `ansible-playbook site.yml` | YES |
| **CLI deploys** | `run.sh` → `build.py` → `ansible-playbook site.yml` | YES |
| **Heartbeat watchdog** | Lives in `site.yml` Phase 0b — runs in all contexts | YES |
| **API server startup** | `start_test_api.py` imports `build.start_api_server()` — shared function | YES |
| **Fleet readiness gate** | Lives in `site.yml` — runs in all contexts | YES |

The `converge.yml` one-line-import pattern proves this approach works. We
just need to apply the same pattern to cleanup.

## Goals

1. **Single source of truth for cleanup** — one playbook (`playbooks/cleanup.yml`)
   used by molecule, SuperManager, and CLI
2. **DRY** — eliminate duplicate VMID lists, file removal lists, PCI cleanup logic,
   and LAN host cleanup task files
3. **Community standard compliance** — molecule for testing, `build.py` for
   production, shared playbooks for both (same pattern as the Ansible community)
4. **Preserve the heartbeat watchdog** — must continue to kill the run on stale
   heartbeat in all environments (test, production, SuperManager)

## What this project does NOT change

- `build.py` remains the production/SuperManager entry point (community convention)
- Molecule remains the test framework (community convention: Molecule tests roles,
  not production deployment)
- `site.yml` remains the shared build playbook (already shared via `converge.yml` import)
- Per-feature molecule scenarios are unchanged
- Image building (`build-images.sh`) is unchanged
- The heartbeat watchdog behavior is unchanged (it lives in `site.yml`, not cleanup)

## Prerequisites

- All 20 existing project plans complete (all services deployed and tested)
- `playbooks/cleanup.yml` has per-feature rollback plays for all services
- `molecule/default/cleanup.yml` has VMID-based destroy for all services
- Heartbeat watchdog (`scripts/heartbeat_watchdog.sh`) operational in `site.yml` Phase 0b

## Skills

| Skill | When to use |
|---|---|
| `molecule-cleanup` | Cleanup safety patterns, VMID-based destroy, credential protection |
| `proxmox-cleanup-safety` | Host cleanup completeness, file removal lists |
| `testing-workflow` | TDD methodology, molecule test vs converge workflow |
| `molecule-testing` | Test execution, baseline preservation |
| `project-structure-rules` | Design principles (one path, no fallbacks) |

---

## Architecture and Design Principles

### Community standards compliance

The Ansible community is unambiguous on the separation of concerns:

- **Molecule** = test framework. It tests your roles and playbooks by
  running them against real or containerized infrastructure, then verifying
  the result. It owns the test lifecycle (prepare → converge → verify).
  It is NOT a deployment tool.
- **`ansible-playbook`** (or AWX/Tower/`build.py` wrapper) = production
  deployment. It runs the same playbooks that molecule tests, but without
  the test scaffolding (prepare, verify, lifecycle hooks).
- **Playbooks** are the shared unit. Both molecule and production invoke
  `ansible-playbook` on the same playbook files. Molecule's `converge.yml`
  and `cleanup.yml` should be thin wrappers (one-line `import_playbook`)
  that delegate to the shared playbooks, not independent implementations.

This project extends a pattern that already works: `converge.yml` is a
one-line import of `site.yml`. We apply the same pattern to `cleanup.yml`.

### DRY analysis (before and after)

| Component | Before | After |
|---|---|---|
| VMID destroy lists | 2 copies (molecule + production) | 1 copy (production) |
| File removal lists | 2 copies (12 files each, slightly different) | 1 copy |
| PCI/vfio cleanup | 2 copies (identical logic) | 1 copy |
| Bridge teardown | 2 copies (identical logic) | 1 copy |
| LAN host cleanup tasks | 2 files (different SSH params, different regex) | 1 file (merged best of both) |
| Controller state cleanup | 2 copies (different paths via playbook_dir) | 1 copy |
| Test API teardown | 1 copy (molecule-only) | 1 copy (in unified, conditioned on env var) |

### OOP-analogous structure

Ansible playbooks aren't objects, but the same principles apply:

- **Single Responsibility**: Each play has one job (destroy VMs, clean files,
  tear down test infra). The cleanup playbook composes them.
- **Open/Closed**: The `never` tag pattern makes plays extensible (opt-in via
  `--tags`) without modifying the default execution path. New rollback plays
  are added with `[never]` — they don't affect default runs.
- **Don't Repeat Yourself**: After this project, there is exactly ONE place
  to add cleanup for a new service. Previously, you had to add it to two
  playbooks and keep two task files in sync.
- **Interface Segregation**: The cleanup playbook serves multiple consumers
  (molecule, CLI, SuperManager) through tag-based selection, not separate
  implementations. Each consumer gets exactly the plays it needs:
  - Molecule (no tags): selective VMID destroy + test teardown
  - `--tags restore`: host config restore from backup
  - `--tags full-restore`: scorched-earth restore
  - `--tags <service>-rollback`: per-feature rollback

### Architectural decisions

```
Unified cleanup
├── Why not Molecule as production runner?
│   └── Community convention: Molecule = testing only
│       ├── Molecule adds overhead (temp dirs, lifecycle hooks)
│       ├── Molecule not designed for production cancellation/progress
│       └── Would confuse contributors who know the standard pattern
├── Why merge into playbooks/cleanup.yml (not molecule/default/cleanup.yml)?
│   └── Production cleanup is the superset
│       ├── Has per-feature rollback plays (25 plays)
│       ├── Has full-restore capability from backup
│       ├── Has LAN host cleanup
│       └── Molecule cleanup is a strict subset (3 plays)
├── How does test-control-plane teardown fit?
│   └── New play in playbooks/cleanup.yml, conditioned on MOLECULE_PROJECT_DIRECTORY
│       ├── Kills watchdog, API server, SSH tunnel, socat relay
│       ├── Removes .state/ PID files
│       └── Only runs when env var is set (test context)
└── What about the heartbeat watchdog?
    └── No change needed — already in site.yml Phase 0b
        ├── Reads .state/callhome_url (written by build.py or start_test_api.py)
        ├── Monitors $PPID (the ansible-playbook process)
        ├── Kills the run on stale heartbeat
        └── Works identically in test and production
```

### Unified pipeline diagram (after)

```
                    ┌──────────────────────────────┐
                    │     Entry Points              │
                    │                               │
                    │  SuperManager  CLI   Molecule  │
                    │  (deploy.py) (run.sh) (test)   │
                    └───────┬────────┬───────┬──────┘
                            │        │       │
                            ▼        ▼       │
                       ┌─────────────────┐   │
                       │    build.py      │   │
                       │  (env, probing,  │   │
                       │   API server)    │   │
                       └────────┬────────┘   │
                                │            │
                                ▼            ▼
                       ┌─────────────────────────────┐
                       │     ansible-playbook          │
                       ├───────────────────────────────┤
                       │                               │
  BUILD ──────────────►│  playbooks/site.yml           │
  (converge.yml        │  ├── Phase 0: API + watchdog  │
   imports this)       │  ├── Phase 1: Infrastructure  │
                       │  ├── Phase 2: LAN bootstrap   │
                       │  └── Phase 3: Services        │
                       │                               │
  CLEANUP ────────────►│  playbooks/cleanup.yml         │
  (cleanup.yml         │  ├── Selective VMID destroy    │◄── default (molecule)
   imports this)       │  ├── Test control plane        │◄── conditioned on env
                       │  ├── Per-feature rollback      │◄── --tags *-rollback
                       │  └── Full restore from backup  │◄── --tags restore
                       └───────────────────────────────┘
```

---

## Current State Analysis

### What's already shared (correct)

| Component | Test | Production | Shared? |
|---|---|---|---|
| Build playbook | `converge.yml` imports `site.yml` | `build.py` → `ansible-playbook site.yml` | YES |
| Heartbeat watchdog | `site.yml` Phase 0b | `site.yml` Phase 0b | YES |
| API server function | `start_test_api.py` imports `build.start_api_server()` | `build.py:start_api_server()` | YES |
| Fleet readiness gate | `site.yml` Phase 3b | `site.yml` Phase 3b | YES |
| Heartbeat circuit breaker | `site.yml` post-configure | `site.yml` post-configure | YES |

### What's duplicated (the problem)

| Component | Molecule (`molecule/default/cleanup.yml`) | Production (`playbooks/cleanup.yml`) |
|---|---|---|
| LAN host cleanup | Play 1: `cleanup_lan_host.yml` loop | Play: `cleanup_lan_host.yml` loop (tagged `restore,full-restore,clean`) |
| VMID-based destroy | Play 2: explicit VMID lists (12 CTs, 6 VMs) | Play: `Restore Proxmox host from backup` (tagged, uses `qm list`/`pct list` for full-restore) |
| PCI cleanup | Play 2: vfio unbind + VGA rebind | Play: same logic in restore play |
| File removal | Play 2: 12 ansible-managed files | Play: same files in restore play |
| Bridge teardown | Play 2: stale bridge removal | Play: same logic in restore play |
| Kernel cleanup | Play 2: initramfs, WiFi reload, PCI rescan | Play: same logic in restore play |
| Controller state | Play 2: `.state/addresses.json`, `*.generated` | Play: same files in restore play |
| Test API teardown | Play 3: kill watchdog, API, tunnel, socat + remove PID files | **NOT PRESENT** |
| Per-feature rollbacks | **NOT PRESENT** | 23 per-feature rollback plays (tagged `[never]`) |
| Full restore | **NOT PRESENT** | Restore from vzdump backup (tagged `[full-restore]`) |

### Key insight

Production `playbooks/cleanup.yml` has tasks tagged `[restore, full-restore,
clean]` WITHOUT the `never` tag. When Ansible runs with no `--tags` on the
CLI, ALL tasks run EXCEPT those with the `never` special tag. This means if
molecule imports `playbooks/cleanup.yml` and runs it without `--tags`, the
destructive restore tasks (including "Restore host configuration from backup")
would execute — that is NOT what molecule cleanup should do.

**Critical: The `never` tag interaction with other tags**

Adding `never` to existing play/task tag lists is safe:
- `tags: [restore, full-restore, clean, never]` with `--tags restore` → RUNS
  (the explicit `--tags` overrides `never`)
- `tags: [restore, full-restore, clean, never]` with no `--tags` → SKIPPED
  (`never` takes effect)

This means we can add `never` to the existing Restore and LAN cleanup plays
without breaking their current `--tags restore` / `--tags full-restore`
invocations from `build.py`.

The right merge strategy is:
1. Add `never` to the "Restore Proxmox host from backup" and production
   "Clean up LAN satellite hosts" plays (makes them opt-in only, preserving
   existing `--tags restore` behavior)
2. Add molecule's selective VMID destroy + host cleanup as new plays that
   run by default (no `never` tag)
3. Add molecule's test-control-plane teardown as a localhost play
   (conditioned on `MOLECULE_PROJECT_DIRECTORY`)
4. `molecule/default/cleanup.yml` becomes a one-line import

---

## Milestones

## Milestone dependency graph

```
M1 (merge cleanup) ← self-contained
└── M2 (validation + docs) ← depends on M1
```

### Milestone 1: Merge cleanup playbooks

_Self-contained. No external dependencies._

Merge molecule's 3 cleanup plays into `playbooks/cleanup.yml` so there is
one cleanup playbook for all contexts. Molecule's `cleanup.yml` becomes a
one-line import.

See: `molecule-cleanup`, `proxmox-cleanup-safety` skills.

**Implementation pattern:**
- Modified file: `playbooks/cleanup.yml` (add 2 new plays)
- Modified file: `molecule/default/cleanup.yml` (replace with one-line import)
- No new roles, no site.yml changes, no molecule scenario changes

**Tasks:**

- [ ] Add `never` to the existing "Restore Proxmox host from backup" play
  (currently tasks tagged `[restore, full-restore, clean]` without `never`
  — adding `never` makes them opt-in only, preserving existing
  `--tags restore` behavior from `build.py`)
- [ ] Add `never` to the existing "Clean up LAN satellite hosts" play
  (currently tagged `[restore, full-restore, clean]` at play level —
  add `never` so it only runs with explicit `--tags restore`)
- [ ] Add "Selective LAN satellite cleanup" play to `playbooks/cleanup.yml`
  - Hosts: `router_nodes`
  - NO `never` tag (runs by default when molecule imports without `--tags`)
  - `ignore_unreachable: true`
  - Logic: molecule's current Play 1 (`cleanup_lan_host.yml` loop)
  - Must run BEFORE the selective service cleanup play (OpenWrt must be alive)
  - Path: uses `tasks/cleanup_lan_host.yml` (relative to `playbooks/`)
- [ ] Add "Selective service cleanup" play to `playbooks/cleanup.yml`
  - Hosts: `proxmox:!lan_hosts`
  - NO `never` tag (runs by default)
  - `ignore_unreachable: true`
  - Logic: molecule's current Play 2 (VMID-based destroy, PCI cleanup,
    file removal, bridge teardown, kernel cleanup, controller state)
  - Must include connectivity probe, backup manifest check
  - Must use explicit VMID lists from `group_vars/all.yml` (NEVER `qm list`/`pct list`)
  - Credential safety: NEVER remove SSH keys, API tokens, or operator-created files
- [ ] Add "Test control plane teardown" play to `playbooks/cleanup.yml`
  - Hosts: `localhost`
  - NO `never` tag (runs by default, but conditioned on env var)
  - Condition: `when: lookup('env', 'MOLECULE_PROJECT_DIRECTORY') | length > 0`
  - Logic: molecule's current Play 3 (kill watchdog, API server, SSH tunnel,
    socat relay; remove `.state/` PID files)
  - Production builds use `build.py`'s `atexit` handler for API shutdown,
    so this play is a no-op in production
- [ ] Verify play ordering in `playbooks/cleanup.yml`:
  1. Selective LAN cleanup (runs by default)
  2. Selective service cleanup (runs by default)
  3. Test control plane teardown (runs by default, conditioned on env var)
  4. Per-feature rollback plays (tagged `[never]`, opt-in only)
  5. Restore from backup (tagged `[never]`, opt-in via `--tags restore`)
  6. Production LAN cleanup (tagged `[never]`, opt-in via `--tags restore`)
- [ ] Replace `molecule/default/cleanup.yml` with one-line import:
  ```yaml
  ---
  - name: Import unified cleanup
    ansible.builtin.import_playbook: ../../playbooks/cleanup.yml
  ```
- [ ] Verify `molecule/default/cleanup_lan_host.yml` path resolution:
  the molecule cleanup currently includes `cleanup_lan_host.yml` from the
  molecule directory. The unified cleanup includes `../tasks/cleanup_lan_host.yml`
  from the `playbooks/` directory. The molecule-local copy may need to be
  removed or the path adjusted so both point to the same file.
- [ ] Merge `molecule/default/cleanup_lan_host.yml` improvements into
  `tasks/cleanup_lan_host.yml` (molecule version has SSH timeouts and
  keepalives that the production version lacks; grep -oP vs sed difference
  for BusyBox compat). Then remove the molecule copy.
- [ ] Verify `playbook_dir` resolution: `import_playbook` sets `playbook_dir`
  to the imported file's directory (`playbooks/`), so:
  - `{{ playbook_dir }}/../.state/` → `.state/` ✓
  - `{{ playbook_dir }}/../tasks/cleanup_lan_host.yml` → `tasks/cleanup_lan_host.yml` ✓
  - Test control plane: `_project_root: "{{ playbook_dir }}/.."` (not `/../..`)

**Verify:**
- [ ] `molecule test` passes end-to-end (cleanup destroys all containers on all 6 hosts)
- [ ] `molecule cleanup` alone destroys all project VMs/CTs on all hosts
- [ ] Test control plane (API server, watchdog, tunnel, socat) is stopped after molecule cleanup
- [ ] No containers survive cleanup on any WAN host (ai, mesh2, bridge-1, bridge-2)
- [ ] No SSH keys, API tokens, or operator credentials are removed
- [ ] `build.py --playbook cleanup` (no tags) performs same selective cleanup as molecule
- [ ] Per-feature rollback plays still work: `build.py --playbook cleanup --tags pihole-rollback`
- [ ] Full restore still work: `build.py --playbook cleanup --tags restore` and `--tags full-restore`
- [ ] DRY validation: `grep -r 'project_ct_ids\|project_vm_ids' playbooks/ molecule/` shows
  VMID lists only in `playbooks/cleanup.yml` (not in any molecule file)
- [ ] DRY validation: only ONE copy of `cleanup_lan_host.yml` exists (in `tasks/`, not `molecule/`)

**Rollback:**
Restore `molecule/default/cleanup.yml` from git. Remove the 3 new plays from
`playbooks/cleanup.yml`. No host-side state changes to undo (cleanup only
destroys things that the converge creates).

---

### Milestone 2: Documentation and validation

_Depends on: M1._

Update project rules, skills, and architecture docs to reflect the unified
pipeline. Run the full test suite to validate.

See: `learn-from-mistakes`, `writing-skills` skills.

- [ ] Update `.cursor/rules/project-structure.mdc`:
  - Document that `playbooks/cleanup.yml` is the ONE cleanup for all contexts
  - Update the "Key files" table: remove separate molecule cleanup entry, note unified cleanup
  - Update cleanup philosophy section to reference unified approach
- [ ] Update `AGENTS.md`:
  - Update "Cleanup Completeness" section to reference unified cleanup
  - Remove references to separate molecule cleanup logic
- [ ] Update `.cursor/rules/proxmox-safety.mdc`:
  - Update cleanup completeness section
- [ ] Update `molecule/AGENTS.md`:
  - Update "Service-Specific Cleanup" section to reference unified cleanup
- [ ] Update `.agents/skills/molecule-cleanup/SKILL.md`:
  - Document the one-line import pattern
  - Update rules about where cleanup logic lives
- [ ] Update `project-plan-review.mdc`:
  - Remove "Cleanup parity" check (no longer two playbooks to keep in sync)
  - Replace with "Cleanup coverage" check (verify new files are in `playbooks/cleanup.yml`)
- [ ] Update `.cursor/rules/secret-generation.mdc`:
  - Change "Both cleanup playbooks" reference to unified cleanup
  - Update path from `{{ playbook_dir }}/../../` to `{{ playbook_dir }}/../`
- [ ] Run `molecule test` — full clean-state validation on all 6 hosts
- [ ] Run `pytest tests/ -v` — Python test suite
- [ ] Run `ansible-lint && yamllint .` — lint checks

**Verify:**
- [ ] All tests pass with exit code 0
- [ ] No references to separate molecule cleanup remain in rules/skills
- [ ] `playbooks/cleanup.yml` is referenced as the single cleanup source

**Rollback:**
Revert doc changes from git. No code impact.

---

## Testing Strategy

### Parallelism

This project modifies cleanup only — no service provisioning or configure
changes. The full E2E test (`molecule test`) exercises the cleanup at the
start of its test_sequence (`dependency → cleanup → syntax → prepare →
converge → verify`). The cleanup runs on all 6 hosts in parallel.

### Per-feature scenario impact

Per-feature scenarios (`molecule/pihole-lxc/`, etc.) have their own cleanup
playbooks that destroy only their service's container. These are NOT affected
by this change — they target specific VMIDs, not the unified cleanup.

### Day-to-day workflow

```bash
# Source environment
set -a && source test.env && set +a

# Test cleanup in isolation
molecule cleanup

# Full clean-state validation
molecule test

# Production cleanup (same playbook, no tags needed — selective plays run by default)
python build.py --playbook cleanup --env test.env

# Full restore from backup (opt-in via --tags)
python build.py --playbook cleanup --tags restore --env test.env

# Per-feature rollback (unchanged)
python build.py --playbook cleanup --tags pihole-rollback --env test.env
```

### Teardown table

| Scenario | Creates | Destroys | Baseline impact |
|---|---|---|---|
| `molecule cleanup` | Nothing | All project VMs/CTs on all hosts; test control plane | Clean slate |
| `molecule test` | All services | All services (cleanup at start + error cleanup) | Clean slate |
| `build.py --playbook cleanup` | Nothing | All project VMs/CTs on all hosts (selective, same as molecule) | Clean slate |
| `build.py --playbook cleanup --tags restore` | Nothing | Same as above + restore host config from backup | Restore |
| `build.py --playbook cleanup --tags full-restore` | Nothing | ALL VMs/CTs (not just project) + restore from backup | Full restore |
| `build.py --playbook cleanup --tags pihole-rollback` | Nothing | Pi-hole CT only | Partial cleanup |

## Related Projects

- **Container NAT Networking** (`docs/projects/2026-04-09-19-container-nat-networking/`):
  COMPLETED. WAN containers now use per-host NAT bridges (10.99.x.x via
  `vmbr_ct`) instead of household subnet IPs. The cleanup lists in both
  `molecule/default/cleanup.yml` and `playbooks/cleanup.yml` already include
  the new cleanup items (`ansible-container-bridge.conf`, iptables NAT flush,
  `vmbr_ct` teardown). When merging cleanup playbooks for this project,
  carry these items forward into the unified cleanup.

## Future Integration Considerations

- When adding a new service, add its VMID to the `project_ct_ids` or
  `project_vm_ids` list in the unified cleanup play in `playbooks/cleanup.yml`.
  There is only ONE place to add it now (previously had to add to both
  molecule and production cleanup).
- The test control plane teardown play is conditioned on
  `MOLECULE_PROJECT_DIRECTORY`. If a future test runner replaces molecule,
  set this env var or add a new condition.
- The heartbeat watchdog kills the ansible-playbook process on stale
  heartbeat. This works for both `build.py` (which uses `subprocess.run`)
  and molecule (which uses the ansible provisioner). No special handling
  needed — the watchdog monitors `$PPID` regardless of entry point.

## How this aligns with the 3-tier system

The 3-tier hierarchy (Container → Manager → SuperManager) is about
**runtime operations** — heartbeats, status queries, mode switching.
The build pipeline is about **provisioning** — creating and configuring
the containers/VMs that the 3-tier system then monitors.

After this project, the full picture is:

| Layer | Build/Deploy | Runtime Operations | Cleanup |
|---|---|---|---|
| **SuperManager** | `deploy.py` → `build.py` → `site.yml` | HTTP to Manager API | `build.py --playbook cleanup` |
| **Molecule** | `converge.yml` → `site.yml` | (same heartbeats via `site.yml` Phase 0b) | `cleanup.yml` → `playbooks/cleanup.yml` |
| **CLI** | `run.sh` → `build.py` → `site.yml` | N/A | `cleanup.sh` → `build.py --playbook cleanup` |

All three paths share the same playbooks. The entry point differs
(`build.py` vs molecule provisioner), but the Ansible logic is identical.
This is the Ansible community standard: playbooks are the shared contract,
tools are the interface.

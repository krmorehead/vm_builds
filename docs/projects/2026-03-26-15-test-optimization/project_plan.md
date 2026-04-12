> **Status: Completed** — Archived 2026-04-10

# Test Architecture Optimization

## Status: COMPLETE (2026-03-26)

All four milestones implemented:
- **M1**: Parallel image builds (`--parallel`, `--hosts`) in `build-images.sh`
- **M2**: Per-feature unit test `prepare.yml` in all 13 scenarios
- **M3**: E2E `verify.yml` slimmed 35% (2378→1539 lines), `prepare.yml` pre-flight added
- **M4**: CHANGELOG, molecule AGENTS.md, UNIT_TEST_PATTERN.md updated

## Overview

Restructure the testing pipeline into two tiers — **unit tests** (per-feature
molecule scenarios) and **E2E integration tests** (molecule/default) — with
parallel image building across all Proxmox hosts.

Unit tests own the full lifecycle: build the image, deploy, verify all
service-specific functionality, tear down. E2E tests consume pre-built images
and verify only that all services work together as an integrated system.

This separation means E2E never waits on image builds and never re-tests
functionality that unit tests already cover. Combined with parallel image
builds across 3–4 hosts, the total wall-clock time for a full rebuild + E2E
drops from hours to minutes.

## Type

Cross-cutting infrastructure improvement (testing and build pipeline)

## Current State Analysis

### Image builds (sequential, single host)

```
build-images.sh --host <ip>
  → pihole (5 min)
  → rsyslog (5 min)
  → jellyfin (5 min)
  → netdata (5 min)
  → wireguard (3 min)
  → homeassistant (5 min)
  → kodi (5 min)
  → moonlight (5 min)
  → gaming (10 min)
  → sunshine (45 min)
  → desktop (20 min)
  + mesh + router (local, 5 min each)
  ≈ 120+ min wall-clock (sequential)
```

### E2E test (everything in one pass)

```
molecule test (default scenario)
  → cleanup (destroy previous state)
  → syntax check
  → converge (full site.yml: infra + all services on 4 hosts)
  → verify (2874 lines: deep service checks + integration checks)
```

E2E verify duplicates checks that per-feature scenarios already cover.
Every `pct exec` call in verify adds 15–60s of pct_remote overhead.
Deep service checks (DNS resolution retry loops, ad-block tests, log
ingestion pipelines, VA-API probes) dominate verify time.

### Per-feature tests (no image build)

```
molecule test -s pihole-lxc
  → converge (provision + configure from pre-built image)
  → verify (service-specific checks)
  → cleanup
```

Per-feature scenarios assume images are pre-built. They don't validate
that the image itself builds correctly.

### Problems

1. Image builds are sequential on one host (~120+ min for all 13)
2. E2E verify duplicates per-feature service checks (wasted time)
3. Per-feature scenarios don't build their own images (gap in coverage)
4. No parallelized rebuild workflow
5. E2E is slow because it tests deep service functionality AND integration

## Proposed Architecture

```
                     ┌─────────────────────────────────────┐
                     │       build-images.sh --parallel    │
                     │  Distributes across home, ai, mesh2 │
                     └─────┬─────────┬─────────┬──────────┘
                           │         │         │
                     ┌─────▼──┐ ┌────▼───┐ ┌───▼────┐
                     │  home  │ │   ai   │ │ mesh2  │   ← parallel
                     │pihole  │ │netdata │ │gaming  │
                     │rsyslog │ │jellyfin│ │desktop │
                     │wireguard│ │kodi   │ │sunshine│
                     │homeasst│ │moonlght│ │        │
                     └────────┘ └────────┘ └────────┘
                           │         │         │
                     ┌─────▼─────────▼─────────▼──────────┐
                     │          images/ directory          │
                     │    (cached, shared by all tests)    │
                     └─────┬───────────────────┬──────────┘
                           │                   │
                 ┌─────────▼────────┐ ┌────────▼──────────┐
                 │   Unit Tests     │ │   E2E Integration  │
                 │ (per-feature)    │ │  (molecule/default) │
                 │                  │ │                     │
                 │ prepare: build   │ │ pre-flight: assert  │
                 │   image (idem-   │ │   all images exist  │
                 │   potent)        │ │                     │
                 │ converge: deploy │ │ converge: site.yml  │
                 │ verify: ALL      │ │   (from cached img) │
                 │   service-       │ │ verify: integration │
                 │   specific       │ │   checks ONLY       │
                 │   functionality  │ │   (cross-service,   │
                 │ cleanup: destroy │ │    health, topology) │
                 └──────────────────┘ └─────────────────────┘
```

### Three testing workflows

| Workflow | Command | What it does | When to use |
|----------|---------|-------------|-------------|
| Unit test | `molecule test -s pihole-lxc` | Build image → deploy → verify all functionality → cleanup | Developing a single service |
| E2E | `molecule test` | Deploy all from pre-built images → verify integration → cleanup | Final validation, CI |
| Full rebuild + E2E | `./build-images.sh --parallel && molecule test` | Parallel image rebuild → E2E integration | Paranoid / release validation |

## Prerequisites

- All existing per-feature molecule scenarios (`molecule/<service>-lxc/`)
- `build-images.sh` with `--only <target>` support (already exists)
- All 4 Proxmox hosts operational (home, mesh1, ai, mesh2)
- No new services or VMs — this project modifies test infrastructure only

## Skills

| Skill | When to use |
|-------|-------------|
| `molecule-testing` | Molecule commands, baseline workflow, test sequences |
| `molecule-verify` | Assertion patterns, batch operations, completeness |
| `molecule-performance` | Template caching, NTP sync, pct_remote overhead |
| `molecule-scenario-hierarchy` | Scenario architecture, baseline vs per-feature |
| `image-management-patterns` | Image build, local storage, template caching |
| `build-entry-point` | Build.py orchestration, host probing |
| `testing-workflow` | TDD methodology, converge vs test |

---

## Architectural Decisions

```
Decisions
├── Image build parallelism: distribute across available Proxmox hosts
│   ├── 3 directly reachable hosts (home, ai, mesh2) for remote builds
│   ├── mesh1 excluded by default (behind OpenWrt, ProxyCommand overhead)
│   ├── Controller handles local builds (mesh, router) in parallel
│   └── Each host builds sequentially (one VMID at a time), hosts in parallel
│
├── Unit test = per-feature scenario + prepare step
│   ├── molecule prepare.yml builds the image (idempotent, skips if cached)
│   ├── converge deploys and configures from the image
│   ├── verify tests ALL service-specific functionality
│   ├── cleanup destroys the container (image stays cached for E2E)
│   └── Force rebuild: delete images/<service>-*.tar.zst before running
│
├── E2E = integration-only verification
│   ├── Pre-flight: hard-fail if any image is missing from images/
│   ├── Converge: site.yml provisions all services from cached images
│   ├── Verify: cross-service integration, health checks, topology
│   ├── ZERO service-specific deep checks (those live in unit tests)
│   └── Deploy stamps, generated env file, infrastructure assertions
│
├── Image caching: images/ directory shared by all tests
│   ├── LXC templates persist on Proxmox hosts (pveam list cache hit)
│   ├── build-images.sh skips if output file exists
│   ├── prepare.yml in unit tests is a no-op when image is cached
│   └── E2E never builds images — just deploys from cache
│
├── mesh1 excluded from image builds by default
│   ├── Behind OpenWrt, reachable only via ProxyCommand
│   ├── Template uploads are slower through the proxy
│   └── 3 WAN hosts provide sufficient parallelism
│
└── No new services or VMs — this is infrastructure-only
    ├── No changes to roles, site.yml plays, or cleanup VMIDs
    ├── Only build-images.sh, molecule configs, and verify.yml change
    └── Backward compatible: --host still works for single-host builds
```

---

## Testing Strategy

### Parallelism

Image builds parallelize across all directly-reachable hosts. Unit tests
run independently per service. E2E runs on all 4 hosts simultaneously
(existing behavior via site.yml phased plays).

### Per-feature scenarios (unit tests)

Each per-feature scenario (`molecule/<service>-lxc/`) becomes a complete
unit test that covers the full lifecycle: image build, deployment,
configuration, functionality verification, and teardown.

### Day-to-day workflow

```bash
# Developing a service: run its unit test
molecule test -s pihole-lxc        # builds image if needed, full test

# Quick iteration: converge + verify only (skip image build)
molecule converge -s pihole-lxc
molecule verify -s pihole-lxc

# Final validation: E2E integration (images must be pre-built)
molecule test                       # deploys all, integration checks only

# Release validation: parallel rebuild + E2E
./build-images.sh --parallel && molecule test
```

### Teardown table

| Scenario | Images built? | Images destroyed? | Containers destroyed? |
|----------|--------------|------------------|----------------------|
| Unit test (`molecule test -s pihole-lxc`) | Yes (if missing) | No (cached) | Yes (cleanup) |
| E2E (`molecule test`) | No (pre-built required) | No (cached) | Yes (cleanup) |
| Full rebuild | Yes (all, parallel) | No (overwritten) | N/A |
| Force fresh unit test | Yes (after manual `rm`) | No | Yes |

---

## Milestone Dependency Graph

```
M1 (parallel image builds) ─── self-contained
│
M2 (per-feature unit test pattern) ─── self-contained
│   └── M3 (E2E verify slimming) ─── depends on M2
│
└── M4 (workflow tooling + docs) ─── depends on M1, M2, M3
```

M1 and M2 can be implemented in parallel.
M3 depends on M2 (per-feature verify must be comprehensive before
stripping checks from E2E verify). M4 ties everything together.

---

## Milestones

### Milestone 1: Parallel Image Build Infrastructure

_Self-contained. No external dependencies._

Add multi-host parallelism to `build-images.sh` so all 13 images build
across 3 hosts simultaneously instead of sequentially on one host.

See: `image-management-patterns` skill, `build-entry-point` skill.

**Implementation pattern:**
- Script: `scripts/build-images.sh` — add `--parallel` flag and
  `--hosts <ip1>,<ip2>,...` argument
- Distribution: round-robin or weighted assignment of build targets
  to available hosts
- Local builds (mesh, router) run on the controller in parallel with
  remote builds
- Each remote host builds its assigned images sequentially (one build
  VMID at a time), but multiple hosts build in parallel

**Build distribution strategy:**

Each host can only run one LXC build container at a time (unique VMID).
Assign images to hosts to balance total build time:

```
Controller (local):  mesh (~3 min), router (~3 min)     → ~6 min
Host 1 (home):       pihole, rsyslog, wireguard, homeassistant  → ~18 min
Host 2 (ai):         netdata, jellyfin, kodi, moonlight → ~20 min
Host 3 (mesh2):      gaming (~10 min), desktop (~20 min) → ~30 min
                     sunshine (~45 min) on whichever host finishes first
```

Wall-clock estimate: ~30–50 min (vs ~120+ min sequential)

The longest-running builds (sunshine at ~45 min, desktop at ~20 min)
are the bottleneck regardless of parallelism. Distributing the LXC
builds across hosts ensures they don't add to the critical path.

- [ ] Add `--hosts <ip1>,<ip2>,...` flag to `build-images.sh` alongside
  existing `--host <ip>` (backward compatible)
- [ ] Implement build target distribution: assign each `--only` target to
  a host via round-robin or static mapping
- [ ] Launch per-host build loops as background jobs (`&`)
- [ ] Local builds (mesh, router) run as a separate background job
- [ ] Collect exit codes from all background jobs via `wait -n` or
  `wait $pid`; fail with clear error if any build fails
- [ ] Error reporting: on failure, print which host and which build
  target failed
- [ ] Add `--parallel` convenience flag that reads host IPs from
  environment (`PRIMARY_HOST`, `AI_HOST`, `MESH_2_HOST`) instead of
  requiring explicit `--hosts`
- [ ] Ensure build VMIDs don't collide across hosts (each host uses the
  same VMIDs — 990, 991, 997, 998 — but on different hosts, so no
  collision)

**Verify:**

- [ ] `./build-images.sh --parallel` produces all 13 images in `images/`
- [ ] Wall-clock time with 3 hosts is < 50% of single-host sequential time
- [ ] `./build-images.sh --host <ip>` still works (backward compatible)
- [ ] A single build failure reports the failing host and target, doesn't
  silently continue
- [ ] Local builds (mesh, router) complete independently of remote builds
- [ ] `./build-images.sh --parallel --only pihole --only rsyslog`
  distributes only the specified targets

**Rollback:**

Revert `build-images.sh` changes. `--host <ip>` continues to work as
the single-host fallback. No persistent state changes.

---

### Milestone 2: Per-Feature Unit Test Pattern

_Self-contained. No external dependencies._

Each per-feature molecule scenario becomes a full unit test by adding an
image build step (`prepare.yml`) and ensuring its verify covers ALL
service-specific functionality.

See: `molecule-testing` skill, `molecule-verify` skill,
`molecule-scenario-hierarchy` skill.

**Implementation pattern:**
- Add `prepare.yml` to each per-feature scenario directory
- Add `prepare` to `test_sequence` in each per-feature `molecule.yml`
- Audit per-feature verify against E2E verify — migrate any missing
  service-specific checks

**Prepare pattern (reusable across all per-feature scenarios):**

```yaml
# molecule/<service>-lxc/prepare.yml
---
- name: Build service image
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Build <service> image if not cached
      ansible.builtin.command:
        cmd: >-
          {{ playbook_dir }}/../../scripts/build-images.sh
          --host {{ lookup('env', 'PRIMARY_HOST') }}
          --only <service>
      register: _build
      changed_when: "'already exists' not in _build.stdout"
```

The `build-images.sh --only <service>` is already idempotent — it skips
if the output file exists in `images/`. Day-to-day iteration is fast
(prepare is a no-op). Force a rebuild by deleting the cached image.

**Updated test_sequence for per-feature scenarios:**

```yaml
scenario:
  test_sequence:
    - dependency
    - syntax
    - cleanup
    - prepare       # ← NEW: build image if not cached
    - converge
    - verify
    - cleanup
```

**Per-feature scenarios to update (13 total):**

| Scenario | Image target | Prepare host |
|----------|-------------|-------------|
| `pihole-lxc` | pihole | PRIMARY_HOST |
| `rsyslog-lxc` | rsyslog | PRIMARY_HOST |
| `netdata-lxc` | netdata | PRIMARY_HOST |
| `wireguard-lxc` | wireguard | PRIMARY_HOST |
| `homeassistant-lxc` | homeassistant | PRIMARY_HOST |
| `jellyfin-lxc` | jellyfin | PRIMARY_HOST |
| `kodi-lxc` | kodi | PRIMARY_HOST |
| `moonlight-lxc` | moonlight | PRIMARY_HOST |
| `gaming-lxc` | gaming | AI_HOST |
| `desktop-vm` | desktop | PRIMARY_HOST |
| `sunshine-vm` | sunshine | PRIMARY_HOST |
| `openwrt-mesh` | mesh | (local build, no host needed) |
| `openwrt-security` | router | (local build, no host needed) |

- [ ] Create `prepare.yml` for each of the 13 per-feature scenarios
  (templatized — only the `--only <target>` and host var differ)
- [ ] Add `prepare` to `test_sequence` in each scenario's `molecule.yml`,
  after `cleanup` and before `converge`
- [ ] Audit E2E verify.yml against each per-feature verify.yml:
  identify service-specific checks in E2E that don't exist in the
  per-feature verify
- [ ] Migrate missing service-specific checks from E2E verify to their
  respective per-feature verify.yml files
- [ ] Verify each per-feature scenario covers:
  - Container/VM state (running, config, onboot, startup order)
  - Service health (process running, ports listening)
  - Service functionality (DNS resolution, log ingestion, streaming,
    VPN connectivity, etc.)
  - Error cases (what happens when upstream is down)
- [ ] Document the unit test pattern in a `molecule/UNIT_TEST_PATTERN.md`
  reference for future services

**Verify:**

- [ ] `molecule test -s pihole-lxc` builds image (if missing), deploys,
  verifies all Pi-hole functionality, and tears down
- [ ] Each per-feature verify covers ALL checks that were previously only
  in E2E verify for that service
- [ ] Running a per-feature test with a cached image skips the build
  (prepare is a no-op)
- [ ] Deleting the cached image and re-running the per-feature test
  rebuilds the image from scratch

**Rollback:**

Remove `prepare.yml` files and revert `test_sequence` changes in
per-feature `molecule.yml` files. Per-feature verify additions are
additive and safe to keep.

---

### Milestone 3: E2E Integration Test Refactoring

_Depends on M2 (per-feature verify must be comprehensive before stripping
checks from E2E verify)._

Slim down E2E verify to focus exclusively on integration and cross-service
checks. Service-specific deep checks now live in per-feature verify (M2).

See: `molecule-verify` skill, `molecule-performance` skill.

**Implementation pattern:**
- Add pre-flight image existence checks to E2E converge (or a
  `prepare.yml` that asserts all images exist without building)
- Refactor `molecule/default/verify.yml` to remove service-specific
  deep checks, keeping only integration and health checks

**What stays in E2E verify:**

```
Infrastructure (all hosts)
├── Bridges: at least one physical-NIC bridge per host
├── IOMMU: active (status report)
├── iGPU: detected, driver loaded, correct vendor
├── PCI passthrough: WiFi bound correctly on router_nodes
└── Deploy stamps: all expected plays recorded

Services (basic health)
├── All containers running (pct status)
├── All VMs running (qm status)
├── Correct config: onboot, startup order, features (nesting)
├── Container/VM IPs assigned and non-colliding
└── Services responding on expected ports (TCP connect)

Cross-service integration
├── DNS: query Pi-hole from another container → resolves
├── Syslog: send test log from OpenWrt → appears in rsyslog container
├── VPN: WireGuard peer handshake across nodes
├── Streaming: Moonlight can reach Sunshine API (HTTP 200)
├── Monitoring: Netdata agent reachable from other hosts
├── Mesh WiFi: radios detected, mesh link (when hardware present)
└── OpenWrt: WAN connectivity, DHCP serving, LAN routing

Generated state
├── test.env.generated exists with expected sections
├── .state/addresses.json written
└── LAN gateway / CIDR values present
```

**What moves to per-feature verify (removed from E2E):**

```
Pi-hole deep checks
├── FTL process status, web admin HTTP code
├── DNS resolution retry loop with diagnostics
├── Ad-block domain test (doubleclick.net)
├── DHCP disabled assertion
└── FTL listener readiness (TCP+UDP 53)

rsyslog deep checks
├── TCP receiver listening on port 514
├── Log ingestion pipeline (send → receive → file)
├── Config validation (rsyslogd -N1)
└── syslog forwarding from OpenWrt

Netdata deep checks
├── Agent running, streaming configured
├── Host metrics via bind mounts
├── VA-API availability
└── Per-host metric collection

WireGuard deep checks
├── Interface up, wg show output
├── Peer handshake timing
├── Tunnel connectivity (ping through VPN)
└── Key generation and persistence

Home Assistant deep checks
├── Docker container running
├── Web UI responding (port 8123)
└── HA API health check

Jellyfin / Kodi / Moonlight deep checks
├── Service-specific process checks
├── VA-API / DRI device access
├── Streaming endpoint health
└── Media library / player state

Gaming LXC deep checks
├── Sunshine service active
├── dsda-doom installed
├── VA-API H.264 encode
├── DRI device bind mounts
└── Sunshine API health (port 47990)

Desktop VM deep checks
├── Display manager running
├── Desktop environment loaded
├── SSH access
└── GPU acceleration
```

**Size estimate:** E2E verify drops from ~2874 lines to ~800–1200 lines
(infrastructure + basic health + cross-service integration).

- [ ] Add `prepare.yml` to `molecule/default/` that asserts ALL required
  images exist in `images/` — hard-fail with a clear message listing
  which images are missing and the `build-images.sh` command to build them
- [ ] Add `prepare` to the E2E test_sequence (before `converge`):
  ```yaml
  test_sequence:
    - dependency
    - cleanup
    - syntax
    - prepare       # ← assert images exist
    - converge
    - verify
  ```
- [ ] Refactor `molecule/default/verify.yml`:
  - Keep: infrastructure assertions (bridges, iGPU, IOMMU, PCI)
  - Keep: basic service health (running, config, IPs, ports)
  - Keep: cross-service integration tests
  - Keep: deploy stamps and generated state checks
  - Remove: service-specific deep checks (listed above)
  - Add: explicit cross-service integration tests if not already present
    (DNS query from container A to Pi-hole, syslog send from OpenWrt
    to rsyslog, VPN handshake check, Moonlight→Sunshine API check)
- [ ] Verify no regressions: the combination of per-feature verify (M2)
  and slimmed E2E verify covers everything the original E2E verify
  covered. Create a coverage matrix documenting which check lives where
- [ ] Ensure E2E verify still runs on all 4 hosts (home, mesh1, ai, mesh2)
  for multi-node integration checks

**Verify:**

- [ ] E2E verify.yml is < 1500 lines (down from ~2874)
- [ ] `molecule test` passes with pre-built images
- [ ] E2E catches integration failures (break a cross-service link →
  E2E fails, per-feature still passes)
- [ ] Per-feature catches service-specific failures (break a service
  config → per-feature fails, E2E health check fails too)
- [ ] E2E execution time is measurably shorter (fewer pct_exec calls,
  fewer retry loops)
- [ ] No assertion coverage gaps: every check from the original E2E
  verify lives in either the new E2E verify or a per-feature verify

**Rollback:**

Restore `molecule/default/verify.yml` from git. The per-feature verify
additions from M2 are safe to keep (additive).

---

### Milestone 4: Workflow Tooling & Documentation

_Depends on M1 (parallel builds), M2 (unit test pattern),
M3 (E2E slimming)._

Create convenience workflows, update documentation, and add a CHANGELOG
entry.

See: `build-entry-point` skill, `testing-workflow` skill.

- [ ] Add `--parallel` documentation to `build-images.sh --help` output
- [ ] Update `README.md` Testing section:
  - Three-tier workflow (unit test, E2E, full rebuild + E2E)
  - When to use each workflow
  - Example commands
- [ ] Update `molecule/AGENTS.md`:
  - Document the unit test pattern (prepare → converge → verify → cleanup)
  - Document E2E as integration-only
  - Update "Service-Specific Cleanup" section with image caching strategy
- [ ] Update `docs/architecture/overview.md`:
  - Add Testing Architecture section describing the two-tier model
  - Document image build parallelism and distribution
- [ ] Update `scripts/AGENTS.md`:
  - Document `--parallel` and `--hosts` flags
  - Update build-images.sh reference
- [ ] Add CHANGELOG entry under `[Unreleased]`:
  - Parallel image builds across multiple Proxmox hosts
  - Per-feature scenarios now include image build (prepare step)
  - E2E verify slimmed to integration-only checks
  - ~3x faster image builds, faster E2E verification

**Verify:**

- [ ] `ansible-lint && yamllint .` passes with no new warnings
- [ ] Documentation matches implemented behavior
- [ ] All three workflows documented with example commands
- [ ] CHANGELOG entry present

**Rollback:** N/A — documentation-only milestone.

---

## Performance Impact Estimates

### Image builds

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Wall-clock (all 13 images) | ~120+ min | ~30–50 min | ~60–75% faster |
| Hosts utilized | 1 | 3 (+ controller) | 3–4x parallelism |
| Bottleneck | Total sequential time | Longest single build (sunshine ~45 min) | Predictable ceiling |

### E2E test

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Verify assertions | ~2874 lines | ~800–1200 lines | ~60% fewer |
| pct_exec calls in verify | Many (deep checks) | Few (health + integration) | Fewer slow calls |
| Retry loops | Per-service (DNS, FTL, streaming) | Cross-service only | Fewer waits |
| Image build dependency | Implicit (must pre-build) | Explicit (prepare asserts) | Clear failure |

### Per-feature unit test

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Image build | External (manual) | Built-in (prepare step) | Self-contained |
| Coverage | Service-specific only | Service-specific + build validation | More complete |
| Cached iteration | Fast (no image build) | Fast (prepare is no-op) | Same speed |
| Fresh rebuild | Manual delete + rebuild | Same (delete image, re-run) | Same, documented |

### Full rebuild + E2E

| Workflow | Before | After |
|----------|--------|-------|
| Sequential rebuild + E2E | ~120 min build + E2E time | ~30–50 min build + faster E2E |
| Day-to-day E2E (cached) | Same converge + long verify | Same converge + short verify |
| Single service iteration | `molecule test -s pihole-lxc` | Same + optional image rebuild |

---

## Future Integration Considerations

- **New services:** Follow the unit test pattern — every new per-feature
  scenario includes `prepare.yml` and comprehensive verify. E2E only
  adds basic health and cross-service integration checks for the new
  service.
- **CI/CD pipeline:** The three-tier workflow maps naturally to CI stages:
  1. Lint (fast, seconds)
  2. Unit tests (parallelizable across services)
  3. E2E integration (single run, pre-built images)
- **Image versioning:** As the project grows, consider checksumming image
  configs to auto-detect when a rebuild is needed (e.g., hash of
  `build-images.sh` function body + package lists).
- **mesh1 as a build host:** If ProxyCommand overhead is acceptable,
  mesh1 could be added to the build pool for additional parallelism.
  Requires OpenWrt to be running (chicken-and-egg with router builds).
- **Distributed image cache:** Currently images live in `images/` on the
  controller and are uploaded to each Proxmox host during provisioning.
  A future optimization could pre-distribute images to hosts during the
  parallel build step, eliminating upload time during converge.

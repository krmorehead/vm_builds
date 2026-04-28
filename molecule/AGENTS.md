# Molecule Testing Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the molecule/ directory. These rules focus on testing patterns, TDD workflow, and diagnostic approaches.

## SHOW STOPPER: Unreachable Host = FULL STOP

When the heartbeat system detects a failure (SM API dead, service stale, or
Ansible shows `unreachable=1` in a PLAY RECAP):
- **STOP ALL WORK.** Do not continue development, do not run more tests.
- **Check the heartbeat system first:** `curl $CALLHOME_URL/api/fleet/health`
  and `curl $CALLHOME_URL/api/fleet/stale`. The heartbeat watchdog should
  have already killed the run — if it didn't, the watchdog itself is broken.
- **Investigate cause IMMEDIATELY.** Check API logs, not just SSH.
- **NEVER dismiss as "pre-existing."** Find the root cause.
- **For `wol_capable: false` hosts (ai): physical power-on required.** No remote recovery.
- **Do NOT validate features against a substitute host.** If ai runs Sunshine and ai is down, Moonlight verification is IMPOSSIBLE.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., .agents/skills/use-idle-time/SKILL.md), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Testing Patterns:**
- @.agents/skills/testing-workflow - TDD methodology and test patterns
- @.agents/skills/molecule-testing - Test execution and validation
- @.agents/skills/molecule-cleanup - Resource cleanup and safety
- @.agents/skills/molecule-verify - Assertion patterns and verification
- @.agents/skills/molecule-performance - Test optimization and performance
- @.agents/skills/molecule-scenario-hierarchy - Scenario architecture
- @.agents/skills/molecule-group-reconstruction - Dynamic group patterns

**Development Workflow:**
- @.agents/skills/use-idle-time - Productive wait time utilization
- @.agents/skills/learn-from-mistakes - Bug prevention patterns
- @.agents/skills/clean-baselines - Baseline establishment
- @.agents/skills/openwrt-diagnostics - OpenWrt troubleshooting patterns

## Development Guidelines

For productive use of wait time: .agents/skills/use-idle-time/SKILL.md

## Cross-Coverage Rules

### From Other Directories
- **Project planning**: Reference @.agents/skills/project-planning-structure for scenario planning
- **Role development**: Use @.agents/skills/ansible-conventions for task structure patterns
- **Testing workflow**: Apply @.agents/skills/testing-workflow for TDD methodology
- **Cleanup patterns**: Reference @.agents/skills/molecule-cleanup for resource management
- **Performance optimization**: Use @.agents/skills/molecule-performance for test optimization

## Test-First Reproduction (CRITICAL)

When a bug is reported against a production host, **ALWAYS** reproduce it on the test machine first using `molecule test` or `molecule converge`. **NEVER** iterate on production when a test machine is available.

**Process:**
1. Replicate the production environment in `test.env` (same env vars, same image)
2. Run `molecule test` to see if the bug reproduces
3. If it reproduces, fix and verify on the test machine
4. If it does NOT reproduce, add diagnostics and compare output between test and production
5. Only involve the production host when the test machine cannot reproduce the issue

**Previous bug:** SSH timeout was reported on production. Instead of debugging blind on the production host, we added `WAN_MAC` to `test.env` and immediately reproduced the issue, iterated through 4 fix cycles in 15 minutes.

## TDD Iteration Pattern (MANDATORY)

For any non-trivial code change:

1. **Write or update the verify assertion first** (`molecule/default/verify.yml`)
2. Run `molecule test` — the new assertion should fail (proves the test catches the issue)
3. Implement the fix in the role
4. Run `molecule test` — the assertion should now pass
5. Update skills/rules with lessons learned

When adding a new feature: write the verify assertion that checks the feature works, then implement the feature.

## Converge vs Full Test Workflow

### Day-to-Day Iteration
- Use `molecule converge` + `molecule verify` for day-to-day iteration
- This preserves the OpenWrt baseline so leaf nodes (mesh1) remain accessible
- Layered scenarios don't need a full rebuild (~4 min saved)

### Clean-State Validation
- Use `molecule test` only for clean-state validation (CI, pre-commit, final proof)
- The test_sequence ends at `verify` — the baseline is left running
- Layered scenarios can run immediately after a successful `molecule test`

**Default `test_sequence` (`molecule/default/molecule.yml`):** `dependency` → `cleanup` → `syntax` → `prepare` → `converge` → `verify`. The `prepare` step asserts all required images exist (hard-fails with build instructions if any are missing). There is **no** extra `converge` step at the end of the sequence — restoring a full baseline after a clean-state run is manual (`molecule converge`), not part of `molecule test`.

**NEVER** consider a fix complete until `molecule test` passes end-to-end.

## Service-Specific Cleanup (CRITICAL)

`molecule/default/cleanup.yml` is a one-line import of `playbooks/cleanup.yml` — the **unified cleanup playbook**. There is ONE cleanup to maintain. When adding a new service, add its VMID to `playbooks/cleanup.yml` only.

Cleanup destroys **only** known project VMs/containers by **explicit VMID**. **NEVER** iterate `qm list` / `pct list` to destroy ALL resources on a host.

**Rules:**
- Images are built once via `build-images.sh` and cached on each Proxmox host
- Templates persist across test runs (`pveam list` cache hit → skip upload)
- Each service owns its own lifecycle: provision, configure, verify, cleanup
- Per-feature scenarios create and destroy only their own container/VM
- The full integration test (`molecule test`) creates all services from cached images, verifies they work together, then cleans up each service by VMID
- Test control plane teardown (API server, watchdog, tunnel) is in the unified cleanup, conditioned on `MOLECULE_PROJECT_DIRECTORY` env var

**Adding a new feature:** run only the per-feature scenario. The full integration test is reserved for CI and final proof.

## Two-Tier Testing Architecture

### Unit Tests (per-feature scenarios)
Each `molecule/<service>-lxc/` scenario is self-contained:
- `prepare.yml` builds the service image if not cached (idempotent)
- `converge.yml` deploys the single service
- `verify.yml` runs ALL deep service-specific checks
- `cleanup.yml` tears down only its own container

**Test sequence:** `dependency` → `syntax` → `cleanup` → `prepare` → `converge` → `verify` → `cleanup`

See `molecule/UNIT_TEST_PATTERN.md` for the full pattern and examples.

### E2E Integration Tests (default scenario)
`molecule/default/` is an integration-only test:
- `prepare.yml` asserts all images exist (hard-fails if missing)
- `converge.yml` deploys all services
- `verify.yml` checks ONLY infrastructure health, basic service liveness, cross-service integration (e.g., log reception), and deploy stamps
- Deep service-specific diagnostics belong in per-feature scenarios

**Principle:** E2E verify should NOT duplicate checks already covered by unit tests. It validates that services work *together*, not that each service works *internally*.

**Previous bug:** blanket `qm list` / `pct list` cleanup destroyed everything on the host (including non-project resources), forced a full rebuild of ~820MB of templates on every test run, and was slower than explicit VMID lookup.

## Non-Recoverable Host Protection (CRITICAL — production-breaking if violated)

Cleanup and molecule files MUST NOT contain operations that could crash or shut down hosts that cannot be remotely recovered.

**Rules:**
- NEVER run `modprobe -r amdgpu` or `modprobe -r i915` in broad-scope plays (hosts: proxmox*). PCI bus rescan after vfio-pci unbind is sufficient for E2E cleanup
- ONLY run GPU driver unload in per-feature cleanup (e.g., sunshine-vm) gated on VGA controller count >= 2
- NEVER add `shutdown`, `poweroff`, `halt`, or `init 0` to any molecule or cleanup file
- Every host has `wol_capable` (true/false) in `inventory/host_vars/`. Hosts with `wol_capable: false` (e.g., `ai` — USB ethernet) CANNOT be recovered remotely
- `tests/test_host_safety.py` is a **static linter** that catches these patterns at pytest time. ALWAYS run `pytest tests/` before committing cleanup changes
- `tests/test_wol.py` enforces that non-WoL hosts are excluded from `scripts/wol.sh`

**Previous bug:** E2E cleanup ran `modprobe -r amdgpu` on ALL hosts including `ai` (single AMD GPU, USB ethernet). Kernel panicked, host crashed. Required physical power-on. The static linter now catches this exact pattern.

## Hard-Fail Over Graceful Degradation

**NEVER** add "graceful skip" for hardware expected on every host:
- iGPU is present on every modern Intel CPU
- WiFi and VT-d/IOMMU are required for passthrough

Silent skips mask fixable BIOS settings behind warnings that waste test cycles.
**NIC count** is the exception — hardware legitimately varies.

## Productive Wait Time (MANDATORY)

When a long-running command is backgrounded (`molecule test`, `converge`), **ALWAYS** use the wait time for productive work instead of just polling.

### Priority Order During Idle Time

1. **Review and update architecture docs** (`docs/architecture/`) — verify they match the current code
2. **Review and update skills** (`.agents/skills/`) — check for outdated patterns, missing lessons
3. **Review and update rules** (`AGENTS.md` files) — same as skills
4. **Code review against original intent** — if working from a project plan (`docs/projects/`), re-read the plan and diff against the current implementation
5. **General code cleanliness** — scan recently changed files for dead code, unclear naming, missing error handling, or inconsistent style

### Constraints

- **NEVER** block on polling alone. Start productive work immediately after backgrounding the command, then interleave status checks
- **ALWAYS** tell the user what you're reviewing while waiting, so they have context on the parallel work
- If the test run fails, prioritize fixing the failure over finishing the review work
- A `molecule test` run takes ~4-5 minutes. That is enough time to review and update 2-3 files. **Use it**

### What NOT to Do During Idle Time

- Do NOT make code changes to files that the running test depends on
- Do NOT start a second molecule run — only one can run at a time
- Do NOT forget to check on the test. Interleave checks every 60-120 seconds

## Anti-Fake-Test Doctrine (CRITICAL — read before writing ANY test)

The test suite is the early warning system for infrastructure problems.
A test that passes while the infrastructure is broken is WORSE than no test —
it provides false confidence.

**Rules:**
- NEVER mock `probe_host`, network connectivity, or hardware detection against
  hosts you control. We own 6 nodes at known IPs. Probe the REAL hosts.
- NEVER mock SSH commands (`_ssh_exec`, `subprocess.run` with SSH) that trigger
  real operations on real nodes you own. If the command fails, the test MUST fail.
- NEVER write pytest tests that only read YAML files and check string content.
  That's a linter pretending to be a test. Write an actual linter (like
  `test_host_safety.py`) or test real behavior via Molecule.
- NEVER name a test "verify X works" unless it actually exercises X.
- `pytest tests/` failing because hosts are unreachable is CORRECT behavior.
  NEVER add workarounds to make it pass when machines are down.
- Mocking is ONLY justified for side effects you can't control: `subprocess.run`
  (don't run ansible-playbook), `shutil.which` (binary detection), filesystem
  isolation (tmp_path for error-path testing).
- Every `patch()` or `monkeypatch` call MUST have an inline comment with TWO parts:
  (1) WHY this mock is necessary (what side effect it prevents), and
  (2) HOW the test still genuinely validates the feature despite the mock.
  If you cannot write both sentences, the mock is unjustified — remove it.

**How to detect a fake test:**
1. Does it mock the very thing it claims to verify? → FAKE
2. Would it pass identically if all infrastructure were offline? → FAKE
3. Does the test name say "verify X works" but never actually runs X? → FAKE

**Previous catastrophe:** `TestResolveProxmoxHost` (5 tests) monkeypatched
`probe_host` with fake IPs. All 5 passed while `ai` was crashed and all 3
WAN hosts were unreachable. Replaced with `TestInfrastructureHealth` that
probes real hosts from test.env — when hosts are down, those tests FAIL with
actionable messages telling you exactly what's wrong.

## Test Failure Diagnosis (ORDER)

When a test fails, follow this diagnostic order:

1. **Read the full error context** — grep for `FAILED`, `fatal:`, `UNREACHABLE` in the terminal output
2. **Check dmesg on the target** — kernel-level errors (IPv6 DAD, segfaults, interface errors) are often the root cause when application-level symptoms are misleading
3. **Check interface/bridge state** — `ip addr`, `ip route`, bridge memberships
4. **Check firewall state** — zone bindings, nftables chains
5. **Test actual protocols** — ICMP ping working does NOT mean TCP works. Always test with the protocol the application uses
6. **Add permanent diagnostics** — if you had to add ad-hoc debug tasks, generalize them and make them permanent so the next failure is easier to diagnose

**Previous bug:** `ping 8.8.8.8` worked but `wget` got EPERM. Root cause was IPv6 DAD failure from duplicate MAC corrupting uclient/libubox — only visible in `dmesg`.

**Diagnostic Patterns:**
- Use @.agents/skills/openwrt-diagnostics for OpenWrt-specific troubleshooting
- Apply @.agents/skills/molecule-verify patterns for comprehensive test validation
- Reference @.agents/skills/testing-workflow for diagnostic methodology

## Molecule Commands Reference

```bash
# Fast iteration (keeps baseline)
molecule converge
molecule verify

# Full clean-state test (destroys all)
molecule test

# Test specific scenarios
molecule test -s default              # Full integration (6 nodes)
molecule test -s openwrt-security     # Per-feature scenario
molecule test -s pihole-lxc           # Service-specific test

# Cleanup
molecule destroy
```

This directory contains all molecule testing scenarios. Use `molecule converge` + `molecule verify` for iteration (preserves baseline); the default scenario’s `test_sequence` does not end with a reconverge — see **Converge vs Full Test Workflow** above.
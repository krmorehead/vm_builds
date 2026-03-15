# Molecule Testing Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the molecule/ directory. These rules focus on testing patterns, TDD workflow, and diagnostic approaches.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., @.cursor/rules/use-idle-time.mdc), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

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

For productive use of wait time: @.cursor/rules/use-idle-time.mdc

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
- It destroys the baseline at the end
- After `molecule test`, **ALWAYS** re-run `molecule converge` to restore the baseline before working on layered scenarios

**NEVER** consider a fix complete until `molecule test` passes end-to-end.

## Service-Specific Cleanup (CRITICAL)

Molecule cleanup destroys **only** known project VMs/containers by **explicit VMID**. **NEVER** iterate `qm list` / `pct list` to destroy ALL resources on a host.

**Rules:**
- Images are built once via `build-images.sh` and cached on each Proxmox host
- Templates persist across test runs (`pveam list` cache hit → skip upload)
- Each service owns its own lifecycle: provision, configure, verify, cleanup
- Per-feature scenarios create and destroy only their own container/VM
- The full integration test (`molecule test`) creates all services from cached images, verifies they work together, then cleans up each service by VMID

**Adding a new feature:** run only the per-feature scenario. The full integration test is reserved for CI and final proof.

**Previous bug:** blanket `qm list` / `pct list` cleanup destroyed everything on the host (including non-project resources), forced a full rebuild of ~820MB of templates on every test run, and was slower than explicit VMID lookup.

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
2. **Review and update skills** (`.cursor/skills/`) — check for outdated patterns, missing lessons
3. **Review and update rules** (`.cursor/rules/`) — same as skills
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
molecule test -s default              # Full integration (4 nodes)
molecule test -s openwrt-security     # Per-feature scenario
molecule test -s pihole-lxc           # Service-specific test

# Cleanup
molecule destroy
```

This directory contains all molecule testing scenarios and follows the baseline preservation pattern for efficient development iteration.
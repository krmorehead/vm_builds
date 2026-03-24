---
name: learn-from-mistakes
description: Update skills and rules when encountering new issues to prevent recurrence. Includes hard-fail patterns, code custodianship, and mandatory testing requirements.
---

# Learn from Mistakes

Use when debugging failures, implementing workarounds, or encountering unexpected errors to prevent recurrence and maintain code quality standards.

## Rules

1. ALWAYS fix immediate problem first - don't stop to write skills mid-debug
2. ALWAYS check if existing skills should be updated after fixing issues
3. NEVER add graceful degradation for expected hardware (iGPU, WiFi, IOMMU)
4. ALWAYS do credentials safety audit after completing features
5. ALWAYS run full test suite after code changes
6. NEVER consider task complete until `molecule test` passes
7. ALWAYS generalize ad-hoc diagnostics and make them permanent
8. NEVER commit or present changes as complete without passing tests

## Patterns

Skills update process:

```bash
# When encountering new issue:
1. Fix immediate problem first
2. Search existing .agents/skills/ and AGENTS.md files
3. If lesson is new, add to relevant skill
4. Use NEVER/ALWAYS constraints, not suggestions
5. Include one-line "what went wrong" before rule
```

Code custodianship audit:

```bash
# After completing feature/fix:
1. Grep cleanup playbooks for authorized_keys, pveum, token, .ssh
2. Pipefail audit: grep shell tasks with | for set -o pipefail
3. Cleanup parity: diff molecule/*/cleanup.yml and playbooks/cleanup.yml
4. Doc accuracy: verify docs/architecture/ matches actual exports
5. Verify coverage: every role in site.yml needs verify.yml assertion
6. Host safety: run pytest tests/test_host_safety.py — catches modprobe -r
   amdgpu/i915 in broad-scope plays, shutdown commands in cleanup files
7. WoL safety: run pytest tests/test_wol.py — non-WoL hosts excluded from wol.sh
```

Mandatory testing sequence:

```bash
# After ANY code change:
1. ansible-lint && yamllint .          # Syntax/style
2. molecule test                       # Full integration test
3. Update verify.yml if needed         # Add assertions
4. No untested merges                  # Tests must pass first
```

## Documentation accuracy

When changing a role's exported facts, bridge names, device paths, or connection patterns, ALWAYS update `docs/architecture/` in the same commit.

- `overview.md` role-reference diagrams MUST list the same exports — update both if you update one
- NEVER document planned/future exports as if they already exist (mark with "(future)" or omit)
- NEVER hardcode bridge names (`vmbr0`, `vmbr1`) in docs — use "WAN bridge" / "LAN bridge"

Previous bug: `overview.md` listed `gpu_pci_devices` as an export of `proxmox_pci_passthrough`, but the role only exports `wifi_pci_devices`.

## Handler conventions

Prefer `ansible.builtin.systemd` over `ansible.builtin.command: cmd: systemctl restart` for service management in handlers. Use `command` only for status checks and config validation.

## Anti-patterns

NEVER explain what mistakes are in learning rules
NEVER add graceful skip for hardware that should be present
NEVER skip testing because "it works locally"
NEVER delete ad-hoc diagnostics without making them permanent
NEVER run `modprobe -r amdgpu` in broad-scope cleanup — kernel-panics single-GPU AMD hosts
NEVER shut down or crash hosts with `wol_capable: false` — they cannot be recovered remotely
NEVER add a host to wol.sh without verifying its NIC supports Wake-on-LAN
NEVER mock `probe_host` to test infrastructure health — probe REAL hosts from test.env
NEVER write pytest tests that only check YAML string content instead of running code
NEVER write a test that passes identically when infrastructure is offline (unless testing pure Python)
NEVER name a test "verify X works" without actually exercising X
NEVER dismiss an unreachable host as "pre-existing" or "not caused by our changes"
NEVER continue development when ANY host in the fleet is unreachable
NEVER deviate from the project plan without explicit user approval
NEVER co-locate a streaming server and client on the same host
NEVER assign all services to the same node when 4 nodes are available

## Use the 4 nodes intelligently

Different molecule scenarios assign different groups to the same host.
Each test scenario should use the topology that exercises the feature:

- **Cross-subnet streaming**: server on WAN host, client on LAN host
- **Mesh WiFi**: mesh nodes on satellite hosts, not router_nodes
- **Per-feature isolation**: only the groups needed for that feature

Previous catastrophe: Agent put Moonlight (streaming client) on home, which
also runs Sunshine (streaming server). This is physically nonsensical — you
can't stream to yourself. It also eliminated cross-subnet testing via
WireGuard VPN, which was the ENTIRE purpose of the Moonlight project.
The user explicitly said to use mesh1. The agent deviated from the plan.

## Unreachable host protocol (MANDATORY — SHOW STOPPER)

When ANY host shows `unreachable=1` in a PLAY RECAP or fails a connectivity
probe:

1. **FULL STOP.** Do not continue development. Do not run more tests. Do not
   say "pre-existing." An unreachable host is a 5-alarm emergency.
2. **Investigate the cause immediately.** Check terminal history for what ran
   on that host. Search for `modprobe -r`, `shutdown`, `poweroff`, GPU
   operations, or any destructive command that touched the host.
3. **Check if YOUR session caused it.** Cross-reference the host's last-seen
   timestamp with commands from this session and recent sessions.
4. **Report the severity to the user.** For `wol_capable: false` hosts, this
   means physical access is required. For hosts 3000 miles away, this could
   cost days of downtime. Say this explicitly.
5. **Do NOT validate features that depend on the unreachable host.** If `ai`
   is down and `ai` runs Sunshine/Doom, then Moonlight verification is
   IMPOSSIBLE. Saying "Moonlight tests pass on home" is meaningless when the
   streaming server is offline.
6. **Block the session on recovery.** The user must know that no further
   progress is possible until the host is restored.

Previous catastrophe (Moonlight session, 2026-03-23): `ai` went unreachable
from `modprobe -r amdgpu` in an earlier sunshine-vm cleanup. The agent saw
`unreachable=1` in THREE separate test runs over 4 hours and dismissed it
every time as "pre-existing, not our problem." It ran converge, verify, and
cleanup cycles that could never validate the actual feature (Moonlight
streaming from ai's Sunshine server). The entire session was wasted. `ai`
required physical power-on with no remote recovery, 3000 miles from the
operator. This dismissal pattern is the single most expensive failure mode
in this project.

Previous bug: E2E cleanup ran `modprobe -r amdgpu` on `ai` (single AMD GPU, USB ethernet). Kernel panicked, host crashed. Required physical power-on. Now caught by `tests/test_host_safety.py`.

Previous bug: `TestResolveProxmoxHost` (5 tests) monkeypatched `probe_host` with fake IPs. All 5 passed while `ai` was crashed and all 3 WAN hosts were unreachable. Nobody knew because the "host probing tests" never probed any hosts. Replaced with `TestInfrastructureHealth` that probes real hosts. Now `pytest tests/` is the infrastructure early warning system.
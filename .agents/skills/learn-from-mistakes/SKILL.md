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
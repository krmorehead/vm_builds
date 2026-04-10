---
name: early-validation-patterns
description: Proactive validation patterns to catch issues early, prevent debugging blind, and establish testing habits before problems escalate.
---

# Early Validation and Proactive Testing Patterns

Use when starting new projects, implementing new service types, or when you want to prevent issues before they become major problems.

## Critical Validation Sequence (MANDATORY)

**Before ANY development work:**
```bash
set -a && source test.env && set +a
echo "HOME_API_TOKEN: $HOME_API_TOKEN"
echo "PRIMARY_HOST: $PRIMARY_HOST"
echo "AI_HOST: $AI_HOST"
echo "MESH_2_HOST: $MESH_2_HOST"
# Probe ALL hosts — not just PRIMARY_HOST
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$PRIMARY_HOST "echo 'home OK'"
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$AI_HOST "echo 'ai OK'"
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$MESH_2_HOST "echo 'mesh2 OK'"
ansible all -m ping
```

**If ANY host is unreachable → FULL STOP. Investigate immediately.**
Do NOT continue development with a degraded fleet. An unreachable host is a
5-alarm emergency, especially for `ai` (USB ethernet, no WoL, no remote
recovery — requires physical power-on 3000 miles away).

**NEVER dismiss an unreachable host as "pre-existing."** Every unreachable
host was caused by something. Find the cause. If automation crashed it,
that is a code bug that MUST be fixed before any other work continues.

Previous catastrophe: `ai` went unreachable during a Moonlight implementation
session. The agent dismissed it as "pre-existing" THREE TIMES across 4 hours
of testing, running converge and verify cycles that could never validate the
actual streaming feature (ai IS the Sunshine/Doom server). The root cause was
`modprobe -r amdgpu` from an earlier sunshine-vm cleanup — the exact known
crash vector. The dismissal wasted the entire session and left ai requiring
physical power-on with no remote recovery path.

## Proactive Testing Triggers

**Test IMMEDIATELY when you:**
- Create a new service role (`<service>_lxc`, `<service>_configure`)
- Add Docker-in-LXC or container functionality
- Use `pct exec` commands
- Encounter "undefined variable" errors
- See "Could not resolve hostname none"
- Add any Ansible template modules for containers
- Modify variable scoping or host context

**Previous lesson**: We created `homeassistant_configure` but didn't test until much later. We should have run `molecule converge` immediately after role creation.

## Environment Variable Patterns

**Proper environment export:**
```bash
# CORRECT: Exports all variables
set -a && source test.env && set +a

# WRONG: Variables not available to child processes
source test.env
```

**Molecule environment handling:**
- Molecule uses `${VAR}` syntax in molecule.yml
- Variables MUST be exported for molecule to access them
- Test with `echo $HOME_API_TOKEN` before running molecule

## Code Quality Validation (Run After Every Change)

```bash
# After any Ansible code changes:
source .venv/bin/activate
ansible-lint roles/<service>_*/ 
yamllint roles/<service>_*/ molecule/<scenario>/
```

**Previous lesson**: We fixed linting issues at the end instead of catching them early. Run lint checks continuously.

## Skill Loading Patterns

**Load skills proactively when you recognize the domain:**
- LXC/Container work → `lxc-container-patterns`
- Docker-in-LXC → `lxc-container-patterns` + `docker-related` (when available)
- Proxmox operations → `proxmox-safety-rules`
- New Ansible development → `ansible-conventions`
- Testing work → `molecule-testing`

**Previous lesson**: We struggled with Docker-in-LXC patterns because we didn't load `lxc-container-patterns` until late. Load relevant skills immediately when you recognize the domain.

## Early Bug Detection

**Common early warning signs:**
- "Could not resolve hostname none" → Environment not exported
- "pct: command not found" → Wrong execution context
- "proxmox_vmid is undefined" → Variable scoping issues
- Template failures in containers → Use shell + pct exec instead

**When you see these → Test immediately instead of debugging blind.**

## Test-First Development Habits

1. **Write verify assertion FIRST** → Then implement the feature
2. **Test after EVERY significant change** → Don't batch multiple untested changes
3. **Reproduce on test machine FIRST** → Never debug production directly
4. **Run full test cycle BEFORE declaring success** → `molecule test` passes end-to-end

## Common Failure Prevention

| Early Symptom | Likely Cause | Prevention |
|---------------|--------------|------------|
| "hostname none" | Environment not exported | Always use `set -a && source test.env && set +a` |
| UNREACHABLE hosts | SSH or host connectivity | Validate with `ansible home -m ping` first |
| Container template failures | Missing nesting=1 or cgroup | Load `lxc-container-patterns` early |
| Docker access fails | Wrong execution context | Use `service_nodes` host, not container group |
| SSH auth intermittently fails + host key oscillates | IP address conflict (container on another host has same IP) | Check `pct config` on all hosts for IP conflicts before investigating hardware |
| SSH "Permission denied" on freshly installed host | Another device/container answering on the same IP | Run `arp -n` and verify WAN containers use NAT bridge (10.99.x.x), not the household subnet |

## Development Workflow Integration

**At the start of ANY new service implementation:**
1. Load relevant skills proactively
2. Run environment validation
3. Write first verify assertion
4. Test that assertion fails (TDD)
5. Implement minimal feature
6. Test that assertion passes
7. Continue with small iterations

**Result**: Catch issues in minutes instead of hours of debugging.

## Manual testing means REAL testing

"Manual testing" is NOT viewing a UI populated with fabricated data. It is
exercising every interactive feature against real infrastructure and verifying
the real operation completed on real hardware.

**NEVER fabricate data to make a UI look correct during manual testing:**
- NEVER `curl -X POST /api/checkin` to create fake nodes
- NEVER script fake heartbeats to populate a dashboard
- NEVER claim "testing passed" after only viewing pages without clicking anything

**ALWAYS test against real infrastructure:**
- Start real services on real hosts (deploy with `molecule converge` if needed)
- Let real heartbeats flow from real containers
- Click every button, toggle, and action on the page
- Verify the real outcome on the real hardware (SSH, pct exec, API query)

Fabricated test data is mocking with extra steps. It proves the UI can render
JSON — not that the system works.

Previous catastrophe (2026-04-09): Agent curled fake heartbeats into the
Cluster Manager, viewed the dashboard, never clicked batman toggle or any
other feature, and declared "manual testing complete." Every feature was
untested. The dashboard looked pretty because it was fed fabricated data.
---
name: project-planning
description: Template and conventions for vm_builds project plans. Use when creating, reviewing, or updating project plans in docs/projects/, when adding a new VM or service type, or when structuring milestones with verify/rollback sections.
---

# Project Planning Conventions

## Context

Every VM type or cross-cutting concern gets a project plan in
`docs/projects/<date>-<seq>-<name>/project_plan.md`. Plans are the contract
between planning and implementation. Without structure, milestones lack
verify criteria, rollback procedures, and dependency tracking — leading to
"it works on my machine" outcomes.

## Rules

1. Every milestone MUST include three sections: **tasks** (checkbox items), **verify** (inline assertions to add to molecule), and **rollback** (how to undo the milestone).
2. Every milestone MUST declare its dependency status: **self-contained** (no external blockers) or **blocked on** (lists the blocking project/milestone).
3. Self-contained milestones come before blocked milestones in the ordering. Work that can ship now ships first.
4. Milestone 0 of any project that introduces new testing patterns MUST establish the test infrastructure before feature work begins.
5. Feature milestones MUST reference the relevant skills by name so implementers know which skills to load.
6. The project plan MUST reference the architectural decisions that were made and WHY, using the tree diagram format from `docs/architecture/overview.md`.
7. NEVER defer all testing to a final milestone. Each milestone owns its own assertions.
8. Blocked milestones SHOULD still be fully specified — they're ready to implement the moment the blocker is resolved.
9. Every feature milestone MUST include an **implementation pattern** note specifying: which task file to create, which plays to add to `site.yml`, which tags to use, and which molecule scenario to create. NEVER leave "how it integrates" as an open question.
10. Every feature milestone that installs packages MUST note retries/delay requirements by referencing the relevant skill rule (e.g., "with retries per openwrt-build rule 4").
11. Rollback MUST fully restore the baseline state, including auth credentials and connection methods. If a milestone changes how Ansible connects to a target (e.g., password → key auth), the rollback MUST reverse the connection method too.
12. When a milestone changes the auth/connection method for a dynamic group, the plan MUST specify how subsequent plays and per-feature scenarios detect and adapt to the new auth method.
13. Every plan MUST include a **Milestone Dependency Graph** (ASCII tree) showing the ordering and blocking relationships at a glance.
14. Before execution, ALWAYS run the plan through the **Plan Review Checklist** (below) against all referenced skills. Previous bug: the OpenWrt router plan had two critical issues (SSH auth transition, dynamic group persistence) that would have broken implementation — both caught only by cross-referencing skills during review.

## Template

```markdown
# <Service Name>

## Overview
2-3 sentences: what this is, current state, what this project adds.

## Type
VM (KVM/QEMU) | LXC container | Cross-cutting infrastructure

## Resources (for VM/LXC projects)
- Cores, RAM, Disk, Network, PCI, VMID

## Startup (for VM/LXC projects)
- Auto-start, boot priority, dependencies

## Build Profiles
Which build profiles include this service.

## Prerequisites
What must exist before this project starts.

## Skills
Table of relevant skills with when-to-use descriptions.

---

## Architectural Decisions
Tree diagram of decisions with rationale (leaf nodes).

---

## Milestones

### Milestone 0: <Foundation Work>
_Self-contained._
Description of infrastructure/scaffolding this project needs.

- [ ] Task items as checkboxes

**Verify:**
- [ ] Assertions to add to molecule verify

**Rollback:**
How to undo this milestone.

### Milestone N: <Feature>
_Self-contained._ or _Blocked on: <project/milestone>._
Description.

See: `<skill-name>` skill.

**Implementation pattern:**
- Task file: `roles/<type>_configure/tasks/<feature>.yml`
- site.yml plays: (1) configure on `<dynamic_group>`, tag `<feature>` (2) deploy_stamp on `<flavor_group>`, tag `<feature>`
- Molecule scenario: `molecule/<type>-<feature>/`

- [ ] Task items

**Verify:**
- [ ] Assertions

**Rollback (`--tags <feature>-rollback`):**
Steps to reverse the feature (including auth/credential state if changed).
```

## Milestone dependency graph

Include an ASCII tree showing milestone ordering at the top of the
Milestones section:

```markdown
## Milestone dependency graph
M0 (test infra)
├── M1 (security) ← self-contained
├── M2 (VLANs) ← self-contained
├── M3 (encrypted DNS) ← self-contained
├── M4 (mesh) ← self-contained
├── M5 (pihole DNS) ← blocked on Pi-hole LXC project
├── M6 (syslog) ← blocked on Netdata project
├── M7 (monitoring) ← blocked on Netdata project
└── M8 (docs + integration)
```

## Dependency tracking

Mark each milestone clearly:

```markdown
_Self-contained. No external dependencies._
```

```markdown
_Blocked on: Pi-hole LXC project (2026-03-09-03). Cannot test DNS
forwarding chain without a running Pi-hole instance._
```

Blocked milestones appear after all self-contained milestones. Within
each group, order by logical dependency (security before VLANs before
DNS, since VLANs may affect DNS zone config).

## Milestone sizing

Each milestone should be completable in a single focused session
(2-4 hours). If a milestone has more than 8-10 checkbox items, split
it. If it has fewer than 3, merge it with an adjacent milestone.

## Plan review checklist

Before considering a plan ready for execution, verify each item:

1. **Dynamic group persistence**: Do any plays target dynamic groups
   (`openwrt`, `pihole`, etc.)? If so, every entry point that runs as a
   separate `ansible-playbook` invocation (per-feature converge, verify,
   cleanup/rollback) MUST reconstruct the group. `add_host` is ephemeral.
2. **Auth transitions**: Does any milestone change how Ansible connects
   to a target (password → key, add SSH key, disable password)? If so:
   - The milestone MUST specify the exact ordering (deploy → verify → lock)
   - The milestone MUST re-register the host via `add_host` with new args
   - Subsequent milestones MUST detect which auth method is active
   - Rollback MUST restore the original auth method
3. **Rollback completeness**: Does each rollback section undo EVERYTHING
   the milestone changed? Check: UCI config, packages, files, auth state,
   cron jobs, service enablement. A partial rollback leaves the system in
   an undefined state.
4. **Skill rule compliance**: For each referenced skill, scan its Rules
   section. Every applicable NEVER/ALWAYS constraint should be reflected
   in the task items (e.g., retry/delay for opkg, pipefail for shell tasks,
   detached scripts for firewall restarts).
5. **Implementation pattern**: Does every feature milestone specify the
   task file name, `site.yml` play structure (target group + tags), and
   `deploy_stamp` pairing? Ambiguity here causes inconsistent implementations.
6. **Molecule scenario**: Does every feature milestone that adds testable
   behavior also create a per-feature molecule scenario?
7. **Verify from the right host**: Do verify assertions run on the Proxmox
   host (via `qm`, shell commands) or inside the VM (via dynamic group)?
   If inside the VM, the verify needs group reconstruction too.

## Cross-references

- Reference skills inline: `See: rollback-patterns skill.`
- Reference architecture docs: `See: docs/architecture/overview.md, Network Topology.`
- Reference other project plans by their directory name:
  `Blocked on: 2026-03-09-00-shared-infrastructure, Milestone 1.`

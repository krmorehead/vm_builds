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

- [ ] Task items

**Verify:**
- [ ] Assertions

**Rollback (`--tags <feature>-rollback`):**
Steps to reverse the feature.
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

## Cross-references

- Reference skills inline: `See: rollback-patterns skill.`
- Reference architecture docs: `See: docs/architecture/overview.md, Network Topology.`
- Reference other project plans by their directory name:
  `Blocked on: 2026-03-09-00-shared-infrastructure, Milestone 1.`

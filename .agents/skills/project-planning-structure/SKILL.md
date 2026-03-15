---
name: project-planning-structure
description: Project planning structure and milestone template patterns. Use when creating project plans, structuring milestones, or organizing project documentation.
---

# Project Planning Structure Rules

## Plan Location & Naming

1. Every VM type or cross-cutting concern gets a project plan in `docs/projects/<date>-<seq>-<name>/project_plan.md`. Plans are the contract between planning and implementation.

2. Without structure, milestones lack verify criteria, rollback procedures, and dependency tracking — leading to "it works on my machine" outcomes.

## Milestone Structure Requirements

3. Every milestone MUST include three sections: **tasks** (checkbox items), **verify** (inline assertions to add to molecule), and **rollback** (how to undo the milestone).

4. Every milestone MUST declare its dependency status: **self-contained** (no external blockers) or **blocked on** (lists the blocking project/milestone).

5. Self-contained milestones come before blocked milestones in the ordering. Work that can ship now ships first.

6. Milestone 0 of any project that introduces new testing patterns MUST establish the test infrastructure before feature work begins.

## Milestone Template Structure

7. Each milestone follows this structure:

```markdown
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

## Milestone Dependency Graph

8. Every plan MUST include a **Milestone Dependency Graph** (ASCII tree) showing the ordering and blocking relationships at a glance.

```markdown
## Milestone dependency graph
M0 (test infra)
├── M1 (security) ← self-contained
├── M2 (VLANs) ← self-contained
├── M3 (encrypted DNS) ← self-contained
├── M4 (mesh) ← self-contained
├── M5 (pihole DNS) ← blocked on Pi-hole LXC project
└── M8 (docs + integration)
```

## Milestone Sizing

9. Each milestone should be completable in a single focused session (2-4 hours). If a milestone has more than 8-10 checkbox items, split it. If it has fewer than 3, merge it with an adjacent milestone.

## Blocked Milestone Handling

10. Blocked milestones SHOULD still be fully specified — they're ready to implement the moment the blocker is resolved.

11. When implementing a project, blocked milestones SHOULD be moved to their downstream projects rather than kept as stubs. Stubs in `site.yml`, `cleanup.yml`, and task files create dead code that confuses future maintainers.

12. Previous bug: M5-M7 stubs (pihole_dns, syslog, monitoring) were implemented as task files + site.yml plays + cleanup.yml rollback plays, then had to be removed entirely because they belonged in their respective downstream projects.
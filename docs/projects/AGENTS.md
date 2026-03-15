# Project Planning Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the docs/projects/ directory. These rules focus on project plan structure, review processes, and milestone planning.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., .agents/skills/learn-from-mistakes/SKILL.md), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Project Planning:**
- @.agents/skills/project-planning-structure - Milestone templates and organization
- @.agents/skills/project-planning-task-ordering - Task dependencies and sequencing
- @.agents/skills/project-planning-container-vm - Container/VM resource planning
- @.agents/skills/project-planning-verification - Milestone validation patterns
- @.agents/skills/project-plan-review - Plan review checklist and validation

**Rollback Architecture:**
- @.agents/skills/rollback-architecture - Rollback strategy patterns
- @.agents/skills/rollback-per-feature - Feature-specific rollback implementation
- @.agents/skills/rollback-group-reconstruction - Dynamic group rollback patterns

## Development Guidelines

For learning from planning mistakes: .agents/skills/learn-from-mistakes/SKILL.md

## Cross-Coverage Rules

### From Other Directories
- **Architecture consistency**: Reference @.agents/skills/project-structure-rules for structural validation
- **Task ordering**: Use @.agents/skills/project-planning-task-ordering for milestone dependencies
- **Container/VM planning**: Apply @.agents/skills/project-planning-container-vm for resource requirements
- **Verification patterns**: Reference @.agents/skills/project-planning-verification for milestone validation
- **Rollback architecture**: Use @.agents/skills/rollback-architecture for milestone reversibility

## Project Plan Structure

Every VM type or cross-cutting concern gets a project plan in `docs/projects/<date>-<seq>-<name>/project_plan.md`. Plans are the contract between planning and implementation.

## Milestone Structure Requirements

Every milestone **MUST** include three sections:
- **tasks** (checkbox items)
- **verify** (inline assertions to add to molecule)
- **rollback** (how to undo the milestone)

Every milestone **MUST** declare its dependency status:
- **self-contained** (no external blockers)
- **blocked on** (lists the blocking project/milestone)

Self-contained milestones come before blocked milestones in the ordering.

## Plan Review Checklist (MANDATORY)

When editing or reviewing a project plan, **ALWAYS** run through this checklist:

### Structural Validation

1. **Dynamic group persistence**: every separate `ansible-playbook` invocation that targets a dynamic group **MUST** reconstruct it first
2. **Auth transitions**: if a milestone changes connection method, specify ordering, re-registration, detection, and rollback
3. **Rollback completeness**: every rollback undoes everything the milestone changed (UCI, packages, files, auth, cron, services)
4. **One path, no fallbacks**: reject "try X, fall back to Y" logic

### Container/VM Requirements

5. **LXC features**: declare required features (`nesting=1`, etc.) or explicitly state "no special features required"
6. **Bake, don't configure at runtime**: packages AND base configuration belong in the image build. Configure roles only apply host-specific topology
7. **Image build milestone**: every service needs M0 with a `build-images.sh` section and custom template variables in `group_vars/all.yml`

### Cross-Reference Verification

8. **Prerequisite verification**: grep the codebase to confirm claimed prerequisites exist (VMIDs, groups, molecule platforms, .gitignore entries)
9. **site.yml play ordering**: clarify positioning relative to `never`-tagged plays. State whether the new tag runs during normal converge or is opt-in
10. **Shared tags**: if a tag is shared with another service (e.g., `[monitoring]`), document this is intentional and the implication
11. **Cleanup parity**: files deployed by roles must appear in BOTH cleanup playbooks. Rollback tags must appear in `playbooks/cleanup.yml`
12. **Architecture doc consistency**: verify plays, tags, and resources match `overview.md`, `roles.md`, `build-profiles.md`, and `roadmap.md`

### Completeness Requirements

13. **Network topology assumption**: document which host topologies the service supports. If the flavor group spans LAN and WAN hosts, specify the branching strategy
14. **Container IP offset**: verify the offset is defined in `group_vars/all.yml` and doesn't collide with existing allocations:
    - WireGuard: 3–6
    - Pi-hole: 10
    - rsyslog: 12
    - Netdata: 13
    - Home Assistant: 14
    - Jellyfin: 15
    - Mesh WiFi: 20
15. **Milestone consolidation**: provisioning + site.yml integration should be one milestone. Blocked stubs should be deferred to downstream projects, not kept as dead code
16. **Future integration notes**: document how downstream projects interact with the new service
17. **Testing Strategy section**: every plan needs parallelism, per-feature scenarios, day-to-day workflow, and teardown table
18. **Documented exceptions to bake principle**: Docker pull (pinned tag), desktop VM (cloud image + apt), Windows VM (ISO + autounattend) are documented exceptions
19. **Cross-cutting ownership**: shared infrastructure (hookscript, etc.) has ONE owning project. Other plans only attach/reference
20. **Separate hardware topology**: gaming rig and similar separate-hardware services need different network, testing, and build profile docs
21. **VA-API driver portability**: iGPU image builds include BOTH Intel and AMD driver packages for hardware portability

### Performance Considerations

22. **Configure role task budget**: count `pct_remote` tasks in the proposed configure role. Each adds 15-60s overhead. If the config is identical across all containers, bake it into the image instead
23. **LXC disk sizing**: verify planned rootfs can hold the EXTRACTED template (3-5x compressed size). Minimum 2GB for services with monitoring or databases

## Common Planning Mistakes (Learn from Previous Bugs)

### Container/VM Patterns
- **WireGuard plan omitted `nesting=1`** — iptables MASQUERADE would fail at runtime
- **rsyslog plan skipped image build** — configure role would create spool dirs and enable TCP modules at runtime, violating "bake, don't configure"
- **All 9 plans installed packages at runtime** (apt/pip/opkg) in configure roles instead of baking them into images
- **Netdata 1GB disk was too small** for 314MB compressed / 1013MB extracted template — `pct create` failed mid-extraction

**Prevention Patterns:**
- Reference @.agents/skills/project-planning-container-vm for resource planning
- Apply @.agents/skills/image-management-patterns for bake vs configure decisions
- Use @.agents/skills/lxc-container-patterns for container-specific considerations

### Structure Problems
- **rsyslog plan split provisioning and site.yml integration** into two milestones
- **All 9 plans had separate Integration milestone** that duplicated provisioning work
- **All 9 plans were missing Testing Strategy sections** (parallelism, scenarios, workflow)
- **All 9 plans were missing rollback plays** in `playbooks/cleanup.yml`

### Configuration Issues
- **Netdata plan used `${NETDATA_STREAM_API_KEY:-}`** in molecule.yml provisioner.env — Molecule's parser fails on `:-}` syntax
- **Netdata plan combined netdata_lxc and rsyslog_lxc** in one site.yml play — changed to separate plays for failure isolation
- **Netdata systemd override deployed via configure role** (3 pct_remote tasks × 4 nodes) added ~3 minutes

## Milestone Template

Each milestone should follow this structure:

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

Every plan **MUST** include a **Milestone Dependency Graph** (ASCII tree) showing the ordering and blocking relationships at a glance.

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

This directory contains project plans following the milestone-based architecture pattern with proper dependency tracking and review processes.
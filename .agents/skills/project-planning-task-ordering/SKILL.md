---
name: project-planning-task-ordering
description: Project milestone task ordering and implementation patterns. Use when structuring task dependencies, ordering milestone work, or planning implementation sequences.
---

# Task Ordering & Implementation Rules

## Configure Milestone Ordering

1. Every milestone's task list MUST follow dependency order. Walk through each task and ask: "What must already exist for this to succeed?"

2. Canonical ordering for a configure milestone:
   1. Fix system baseline state (broken packages, missing modules)
   2. Install packages
   3. Generate keys/credentials (requires package tools like `wg genkey`)
   4. Template configuration files (requires generated keys)
   5. Start/enable services (requires config files)
   6. Configure runtime state (firewall rules, sysctl, NAT — requires services)
   7. Persist runtime state (save iptables rules, write generated env file)

## Provisioning Milestone Ordering

3. For a provisioning milestone:
   1. Load host-side kernel modules (LXC shares host kernel)
   2. Upload images/templates
   3. Create VM/container
   4. Configure auto-start
   5. Start VM/container
   6. Clean template baseline (fix broken packages in LXC)
   7. Register in dynamic inventory

## Dependency Constraint Rules

4. NEVER put key generation before package installation. NEVER put service start before configuration. NEVER install packages before fixing broken system state.

## Implementation Pattern Specification

5. Every feature milestone MUST include an **implementation pattern** note specifying:
   - Which task file to create
   - Which plays to add to `site.yml`
   - Which tags to use
   - Which molecule scenario to create

6. Never leave "how it integrates" as an open question. Implementation ambiguity causes inconsistent implementations.

## Architectural Decision Documentation

7. The project plan MUST reference the architectural decisions that were made and WHY, using the tree diagram format from `docs/architecture/overview.md`.

## Plan Review Execution

8. Before execution, ALWAYS run the plan through the **Plan Review Checklist** against all referenced skills. This catches critical issues that would break implementation.

9. Previous bug: the OpenWrt router plan had two critical issues (SSH auth transition, dynamic group persistence) that would have broken implementation — both caught only by cross-referencing skills during review.
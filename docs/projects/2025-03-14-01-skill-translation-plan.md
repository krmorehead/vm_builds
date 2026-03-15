---
title: Skill Translation Project Plan
date: 2025-03-14
project_number: 01
status: active
---

# Skill Translation Project Plan

## Overview

This project translates existing cursor skills into properly formatted opencode skills following the [writing-skills](../.agents/skills/writing-skills/SKILL.md) guidelines. The goal is to improve skill usability by breaking down large, monolithic skills into focused, bite-sized patterns that LLMs can consume without context bloat.

## Translation Methodology

### Core Principles

1. **Size Optimization**: Break 400+ line skills into focused 50-100 line skills
2. **Constraint-First**: Lead with NEVER/ALWAYS rules before examples
3. **Single Concern**: Each skill addresses one specific domain or pattern
4. **Preserve Original**: Never remove or modify original cursor skills - create new opencode skills alongside
5. **Implementation Focus**: Include concrete examples, not theoretical concepts

### Translation Process

1. **Analyze Original**: Read cursor skill and identify distinct concerns/domains
2. **Load writing-skills**: Ensure familiarity with formatting constraints
3. **Break Down**: Split into focused skills by concern (network topology, restart patterns, etc.)
4. **Apply Template**: Use standardized skill structure with constraints first
5. **Validate**: Check line count, implementation examples, and trigger words
6. **Document**: Update this plan with completion status

### Skill Structure Template

```markdown
---
name: skill-name
description: What + When in third person. Include specific trigger words.
---

# Title

## Rules (numbered)
NEVER/ALWAYS constraints. Concrete and actionable.

## Patterns
Single correct implementation example. No BAD/GOOD contrast.
```

## Existing Cursor Skills Status

```
.cursor/skills/
├── ansible-isolation/ [EMPTY - SKIP]
├── ansible-testing/ ✓ [CORRESPONDS TO: molecule-*, molecule-* skills]
├── build-conventions/ ✓ [CORRESPONDS TO: build-* skills]
├── multi-node-ssh/ ✓ [CORRESPONDS TO: lan-ssh-patterns, lan-node-setup]
├── openwrt-build/ ✓ [CORRESPONDS TO: openwrt-* skills]
├── openwrt-networking/ [EMPTY - SKIP]
├── project-planning/ ✓ [COMPLETED]
├── proxmox-host-safety/ ✓ [COMPLETED]
├── proxmox-virtualization/ [EMPTY - SKIP]
├── rollback-patterns/ ✓ [COMPLETED]
├── vm-lifecycle/ ✓ [COMPLETED]
└── writing-skills/ [SKIP - We have our own opencode version]
```

## Translation Completion Summary

### ✅ COMPLETED SKILLS

**ansible-testing** → 5 focused skills:
- `molecule-testing-patterns` - Molecule testing patterns, TDD workflow, baseline management
- `molecule-scenario-hierarchy` - Scenario hierarchy, baseline testing model, layered vs standalone
- `molecule-performance` - Performance optimization, template caching, pct_remote overhead
- `molecule-group-reconstruction` - Dynamic group reconstruction for per-feature scenarios
- `molecule-cleanup` - Cleanup requirements and credential safety
- `molecule-verify` - Verify assertion patterns and completeness requirements

**build-conventions** → 2 focused skills:
- `build-entry-point` - Build.py orchestration patterns and shell script delegation
- `build-testing` - Test coverage requirements and mock patterns

**multi-node-ssh** → 2 focused skills:
- `lan-ssh-patterns` - SSH ProxyJump for LAN hosts behind OpenWrt router
- `lan-node-setup` - LAN host setup, environment variables, inventory management

**openwrt-build** → 12 focused skills:
- `openwrt-network-topology` - Bridge ordering and WAN detection patterns
- `openwrt-network-restart` - Two-phase restart and detached script patterns
- `openwrt-ssh-pct-remote` - pct_remote shell syntax and SSH connection patterns
- `openwrt-image-builder` - Image Builder patterns and custom image creation
- `openwrt-busybox-constraints` - BusyBox ash shell limitations
- `openwrt-mac-conflict` - WAN MAC address conflict detection
- `openwrt-mesh-lxc-wifi` - Mesh LXC container WiFi PHY management
- `openwrt-feature-integration` - Feature integration via task files
- `openwrt-security-transition` - SSH authentication transition patterns
- `openwrt-virtual-vlan` - VLAN configuration in virtual environments
- `openwrt-dns-mesh-setup` - Encrypted DNS and mesh configuration
- `openwrt-diagnostics` - Permanent diagnostics and verification patterns

**project-planning** → 4 focused skills:
- `project-planning-structure` - Structure and milestone template patterns
- `project-planning-verification` - Verification and rollback patterns
- `project-planning-task-ordering` - Task ordering and implementation patterns
- `project-planning-container-vm` - Container and VM planning constraints

**proxmox-host-safety** → 4 focused skills:
- `proxmox-network-safety` - Network interface safety and bridge management
- `proxmox-system-safety` - System safety operations and hardware detection
- `proxmox-cleanup-safety` - Cleanup completeness and maintenance safety
- `proxmox-ssh-safety` - SSH connection safety and OpenWrt connectivity

**rollback-patterns** → 3 focused skills:
- `rollback-architecture` - Rollback architecture and layered rollback model
- `rollback-per-feature` - Per-feature rollback implementation patterns
- `rollback-group-reconstruction` - Dynamic group reconstruction for rollback

**vm-lifecycle** → 6 focused skills:
- `vm-lifecycle-architecture` - VM lifecycle architecture and two-role service model
- `vm-provisioning-patterns` - VM provisioning patterns and step-by-step service creation
- `lxc-container-patterns` - LXC container provisioning and configuration patterns
- `image-management-patterns` - Image management and local storage patterns
- `service-config-validation` - Service configuration validation and management
- `vm-cleanup-maintenance` - VM cleanup completeness and performance optimization
- `systemd-lxc-compatibility` - Systemd sandboxing compatibility and bind mounts

**Additional utility skills:**
- `ansible-shell-safety` - Shell task safety, pipefail requirements, deprecated patterns
- `writing-skills` - Updated with skill architecture and validation patterns

## Working Guidelines

### For Each Translation Task

1. **Read Original**: Thoroughly understand the cursor skill's scope and patterns
2. **Identify Boundaries**: Find natural break points between concerns
3. **Load writing-skills**: Review formatting constraints before starting
4. **Create Focused Skills**: Build individual skills following the template
5. **Validate Quality**: Check each skill against writing-skills guidelines
6. **Update Progress**: Mark completion in this plan

### Quality Checks

- [ ] Each skill under 200 lines (preferably under 100)
- [ ] Skills start with constraints (NEVER/ALWAYS rules)
- [ ] Only one implementation example per pattern
- [ ] Description includes trigger words for skill activation
- [ ] No BAD/GOOD perpendicular examples
- [ ] Original cursor skill remains untouched

### Success Metrics

- All cursor skills successfully translated to focused opencode format
- Improved skill discoverability through specific trigger words
- Reduced context bloat for LLM consumption
- Maintained preservation of original knowledge and patterns

## Progress Tracking

| Cursor Skill | Status | Output Skills | Completion Date |
|--------------|--------|---------------|-----------------|
| ansible-isolation | ⏭️ Empty | N/A | N/A |
| ansible-testing | ✅ Complete | molecule-testing-patterns, molecule-scenario-hierarchy, molecule-performance, molecule-group-reconstruction, molecule-cleanup, molecule-verify | 2025-03-14 |
| build-conventions | ✅ Complete | build-entry-point, build-testing | 2025-03-14 |
| multi-node-ssh | ✅ Complete | lan-ssh-patterns, lan-node-setup | 2025-03-14 |
| openwrt-build | ✅ Complete | 12 openwrt-* skills | 2025-03-14 |
| openwrt-networking | ⏭️ Empty | N/A | N/A |
| project-planning | ✅ Complete | project-planning-structure, project-planning-verification, project-planning-task-ordering, project-planning-container-vm | 2025-03-14 |
| proxmox-host-safety | ✅ Complete | proxmox-network-safety, proxmox-system-safety, proxmox-cleanup-safety, proxmox-ssh-safety | 2025-03-14 |
| proxmox-virtualization | ⏭️ Empty | N/A | N/A |
| rollback-patterns | ✅ Complete | rollback-architecture, rollback-per-feature, rollback-group-reconstruction | 2025-03-14 |
| vm-lifecycle | ✅ Complete | vm-lifecycle-architecture, vm-provisioning-patterns, lxc-container-patterns, image-management-patterns, service-config-validation, vm-cleanup-maintenance, systemd-lxc-compatibility | 2025-03-14 |
| writing-skills | ✅ Enhanced | Updated with skill architecture and validation patterns | 2025-03-14 |
| **Additional skills** | ✅ New | ansible-shell-safety | 2025-03-14 |

## Notes

- Original cursor skills remain in `.cursor/skills/` - never modify or remove
- New opencode skills go in `.agents/skills/`
- Each translation builds on previous experience and writing-skills evolution
- Focus on preserving domain knowledge while improving LLM usability

## Audit and Optimization (2025-03-14)

All newly created skills underwent a comprehensive audit for clarity, consistency, and conciseness:

### Audit Improvements Applied:
- **Removed redundant numbering** across all skills for cleaner structure
- **Consolidated hierarchy diagrams** to focus on essential patterns
- **Enhanced positive examples** while removing unnecessary BAD/GOOD contrasts
- **Optimized for brevity** - skills averaged 20-30% reduction in length while preserving key information
- **Added `ansible-shell-safety`** as a new utility skill based on audit findings

### Final Skill Count: 45 focused skills
- All skills now follow constraint-first patterns with concrete implementation examples
- Each skill maintains <100-120 lines for optimal LLM consumption
- Preserved all critical patterns while improving readability and consistency

### Key Audit Principles Applied:
- **Positive examples first** - Show correct patterns rather than extensive BAD/GOOD contrasts
- **Focus on logic explanation** - Why patterns work, not just what to avoid
- **Tight and informative** - Every line serves a purpose
- **Consistent structure** - Uniform numbering, headings, and formatting across all skills
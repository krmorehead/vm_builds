---
name: project-plan-review
description: Review checklist for project plans before execution. Includes structural validation, container/VM requirements, cross-reference checks, and performance considerations.
---

# Project Plan Review

Use when editing or reviewing project plans in `docs/projects/` to ensure completeness, correctness, and prevent common planning mistakes.

## Rules

1. ALWAYS verify dynamic group persistence for separate ansible-playbook invocations
2. ALWAYS document auth transitions with ordering, re-registration, detection
3. NEVER split provisioning and site.yml integration into separate milestones
4. NEVER use `proxmox_lxc_default_template` - create service-specific template vars
5. NEVER add "graceful skip" for expected hardware - hard-fail instead
6. ALWAYS verify container IP offset doesn't collide with existing allocations
7. NEVER use bare relative paths - they break with molecule scenarios
8. ALWAYS include rollback plays in `playbooks/cleanup.yml` for all features

## Patterns

Structural validation:

```yaml
# Dynamic group reconstruction pattern
- name: Reconstruct dynamic group for rollback
  include_tasks: tasks/reconstruct_service_group.yml
  
# Auth transition specification
# Play ordering: SSH key → API token → configure with new auth method
# Detection: verify new auth method before removing old
# Rollback: reverse order of transition
```

Cross-reference verification:

```bash
# Check these exist before completing plan:
- grep for VMID in group_vars/all.yml
- grep for flavor group in inventory/hosts.yml  
- grep for platform in molecule/*/molecule.yml
- grep for cleanup in both cleanup playbooks
- grep for verify assertions in molecule/default/verify.yml
```

Container IP allocation check:

```yaml
# Verify offset doesn't collide:
# Current allocations:
WireGuard: 3-6    Pi-hole: 10    rsyslog: 12    Netdata: 13
HA: 14           Jellyfin: 15   MeshWiFi: 20

# WAN offset +200, verify against host IPs:
# home=.201, ai=.220, mesh2=.211
```

## Anti-patterns

NEVER explain what project plans are in review rules
NEVER keep blocked milestones as stubs in current project
NEVER omit LXC features declaration (nesting=1, privileged, etc.)
NEVER skip image build milestone - configure roles can't install packages
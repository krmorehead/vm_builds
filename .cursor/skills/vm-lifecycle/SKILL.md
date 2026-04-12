---
name: vm-lifecycle
description: Consolidated — see .agents/skills/vm-lifecycle-architecture/SKILL.md for two-role VM/LXC patterns, site.yml ordering, image management, inventory flavors, and cleanup/verify conventions.
---

# VM Lifecycle

> Canonical source: `.agents/skills/vm-lifecycle-architecture/SKILL.md`

**Summary:** Two-role provision/configure model, shared infra once per host, `deploy_stamp` on provision plays, LXC via `include_role: proxmox_lxc`, local images, LAN vs NAT container networking, and operational checklists (cleanup, diagnostics, pct_remote performance).

This skill has been consolidated. See the canonical source for the full content.

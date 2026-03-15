# Skills for vm_builds Project

This directory contains 56 skills for the vm_builds Ansible project. Each skill has a specific focus and is designed to be concise for LLM consumption.

## Guidelines

- **< 100 lines ideal, < 200 hard limit**
- Single GOOD implementation example per pattern (no BAD examples)
- Lead with NEVER/ALWAYS constraints before examples
- Focus on previous bugs and prevention
- Pack descriptions with trigger words
- Every SKILL.md MUST have YAML frontmatter with `name` and `description`

## Skills by Domain

### Development & Coding Standards
- `ansible-conventions` — Task structure, module usage, variable patterns
- `ansible-shell-safety` — Shell task patterns, pipefail, heredoc pitfalls
- `python-code-style` — Python conventions, error handling, type hints
- `writing-skills` — Skill writing patterns and documentation standards
- `opencode-rules-writing` — AGENTS.md and opencode.json conventions

### Build & Scripting
- `build-entry-point` — Build.py orchestration, shell delegation
- `build-testing` — Test coverage for build functions
- `openwrt-image-builder` — OpenWrt image building automation
- `image-management-patterns` — Image management and storage

### Testing & Validation
- `testing-workflow` — TDD methodology, test patterns
- `molecule-testing` — Test execution, validation, baseline
- `molecule-testing-patterns` — Testing patterns and scenario architecture
- `molecule-cleanup` — Resource cleanup and safety
- `molecule-verify` — Assertion patterns and verification
- `molecule-performance` — Test optimization
- `molecule-scenario-hierarchy` — Scenario architecture
- `molecule-group-reconstruction` — Dynamic group patterns
- `clean-baselines` — Baseline establishment
- `use-idle-time` — Productive wait time utilization

### Network & OpenWrt
- `openwrt-busybox-constraints` — BusyBox ash limitations
- `openwrt-diagnostics` — Diagnostics and verification
- `openwrt-dns-mesh-setup` — Encrypted DNS and mesh config
- `openwrt-feature-integration` — Feature integration patterns
- `openwrt-mac-conflict` — WAN MAC conflict detection
- `openwrt-mesh-lxc-wifi` — WiFi PHY management
- `openwrt-network-restart` — Network restart patterns
- `openwrt-network-topology` — Bridge ordering and WAN detection
- `openwrt-security-transition` — SSH authentication
- `openwrt-ssh-pct-remote` — pct_remote shell syntax
- `openwrt-virtual-vlan` — VLAN configuration

### Infrastructure Safety
- `proxmox-cleanup-safety` — Cleanup completeness
- `proxmox-network-safety` — Network interface safety
- `proxmox-safety-rules` — Host management and credentials
- `proxmox-ssh-safety` — SSH connection safety
- `proxmox-system-safety` — System operations and hardware detection

### Project Planning
- `project-planning-container-vm` — Container/VM constraints
- `project-planning-structure` — Milestone templates
- `project-planning-task-ordering` — Task dependencies
- `project-planning-verification` — Milestone validation
- `project-plan-review` — Plan review checklist
- `project-structure-rules` — Architecture and design principles

### Service Integration & Rollback
- `rollback-architecture` — Rollback strategies
- `rollback-group-reconstruction` — Dynamic group rollback
- `rollback-per-feature` — Per-feature rollback
- `secret-generation` — Secret and dynamic config generation
- `service-config-validation` — Config validation patterns
- `systemd-lxc-compatibility` — Systemd sandboxing in LXC
- `task-ordering` — Task dependency ordering
- `vm-cleanup-maintenance` — Cleanup and maintenance
- `vm-lifecycle-architecture` — Two-role service model
- `vm-provisioning-patterns` — VM provisioning patterns

### LAN Host Patterns
- `lan-node-setup` — LAN host bootstrap flow
- `lan-ssh-patterns` — SSH ProxyJump patterns

### Container & VM Patterns
- `lxc-container-patterns` — LXC provisioning and configuration

### Learning & Development
- `learn-from-mistakes` — Bug prevention and skills updates

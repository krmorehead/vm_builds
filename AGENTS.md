# vm_builds Project Rules

This is an Ansible project that automates VM and LXC container provisioning on Proxmox VE. The project deploys OpenWrt router VMs with shared LXC infrastructure and follows strict architectural patterns.

## SHOW STOPPER: Unreachable Host Protocol

**ANY unreachable host is a 5-alarm emergency. FULL STOP.**

- When `pytest tests/` reports a host unreachable, or `molecule` shows `unreachable=1`: STOP ALL WORK.
- Investigate cause IMMEDIATELY. Check terminal history for `modprobe -r`, GPU operations, `shutdown`.
- NEVER dismiss as "pre-existing." NEVER continue development. NEVER say "not caused by our changes."
- For `wol_capable: false` hosts (`ai`): physical power-on required. No remote recovery. Report this to the user immediately.
- Do NOT validate features against a substitute host when the actual target is down. If `ai` runs Sunshine and `ai` is unreachable, Moonlight verification is IMPOSSIBLE.
- This protocol exists because an agent dismissed `ai` unreachable THREE TIMES in one session, wasting 4 hours and leaving a host 3000 miles away permanently severed from the network.

## Critical Work Management Rules

**MANDATORY: Use Todo Lists and Task Tools**
- ALWAYS use `todowrite` tool to break down complex projects into manageable tasks
- Use `task` tool for multi-step autonomous operations when appropriate  
- Update todo status in real-time as work progresses
- Never proceed with complex work without a structured task breakdown

**MANDATORY: Built-in Learning Loop**
- After every project milestone, update relevant skills with lessons learned
- When encountering new bugs or patterns, update the appropriate `.agents/skills/` files
- Document new cross-coverage patterns as they emerge
- Use idle time during long operations to review and improve documentation

### Learning Loop Implementation

**When to Update Skills and Rules:**
- After completing project milestones (document new patterns learned)
- When encountering bugs that could have been prevented with better guidance
- When discovering cross-coverage patterns that span multiple domains
- During productive wait time in test runs (use idle time effectively)

**What to Update:**
- Add new bugs to "Previous bugs learned" sections in relevant skill files
- Update cross-coverage patterns when new inter-domain relationships emerge
- Add missing skill references to AGENTS.md files when new relationships are discovered
- Create new skills when patterns become complex enough to warrant their own domain

**Update Process:**
1. Identify the affected skill files based on the new knowledge
2. Add specific bug patterns or lessons learned to the appropriate sections
3. Update cross-coverage rules in AGENTS.md files if new relationships are discovered
4. Test the updates by referencing the updated guidance during actual work

## Skill Reference Tree

This tree organizes all skills by domain area to help agents quickly find relevant patterns:

### **Development & Coding Standards**
- **ansible-conventions** — Task structure, module usage, variable patterns, OpenWrt constraints
- **ansible-shell-safety** — Shell task patterns, pipefail requirements, heredoc pitfalls
- **python-code-style** — Python conventions, error handling, type hints
- **writing-skills** — Skill writing patterns and documentation standards

### **Build & Scripting**
- **build-entry-point** — Build.py orchestration, shell delegation, host probing
- **build-testing** — Test coverage for build functions, error path testing
- **openwrt-image-builder** — OpenWrt image building automation patterns
- **image-management-patterns** — Image management and storage patterns

### **Testing & Validation**
- **testing-workflow** — TDD methodology, test patterns, diagnostic approaches
- **molecule-testing** — Test execution, validation, baseline preservation
- **molecule-cleanup** — Resource cleanup and safety patterns
- **molecule-verify** — Assertion patterns and comprehensive verification
- **molecule-performance** — Test optimization and performance patterns
- **molecule-scenario-hierarchy** — Scenario architecture and organization
- **molecule-group-reconstruction** — Dynamic group patterns and reconstruction
- **clean-baselines** — Baseline establishment and maintenance patterns
- **use-idle-time** — Productive wait time utilization during test runs

### **Network & OpenWrt Patterns**
- **openwrt-busybox-constraints** — BusyBox ash shell limitations and constraints for OpenWrt
- **openwrt-diagnostics** — OpenWrt permanent diagnostics and verification patterns
- **openwrt-dns-mesh-setup** — OpenWrt encrypted DNS and mesh configuration patterns
- **openwrt-feature-integration** — OpenWrt feature integration via task files and play patterns
- **openwrt-image-builder** — OpenWrt Image Builder patterns and custom image creation
- **openwrt-mac-conflict** — OpenWrt WAN MAC address conflict detection and deferred application
- **openwrt-mesh-lxc-wifi** — OpenWrt Mesh LXC container WiFi PHY management and namespace handling
- **openwrt-network-restart** — OpenWrt network restart patterns and detached script execution
- **openwrt-network-topology** — OpenWrt bridge ordering and WAN detection patterns
- **openwrt-security-transition** — OpenWrt SSH authentication transition and security hardening patterns
- **openwrt-ssh-pct-remote** — OpenWrt pct_remote shell syntax and SSH connection patterns
- **openwrt-virtual-vlan** — OpenWrt VLAN configuration in virtual environments using Proxmox bridges

### **Infrastructure Safety**
- **proxmox-cleanup-safety** — Proxmox cleanup completeness and maintenance safety patterns
- **proxmox-network-safety** — Proxmox network interface safety and bridge management patterns
- **proxmox-safety-rules** — Safety rules for Proxmox host management, remote operations, and credential protection
- **proxmox-ssh-safety** — Proxmox SSH connection safety and OpenWrt connectivity patterns
- **proxmox-system-safety** — Proxmox system safety operations and hardware detection patterns

### **Project Planning**
- **project-planning-container-vm** — Container and VM planning constraints and capability requirements
- **project-planning-structure** — Project planning structure and milestone template patterns
- **project-planning-task-ordering** — Project milestone task ordering and implementation patterns
- **project-planning-verification** — Project milestone verification and rollback patterns
- **project-plan-review** — Review checklist for project plans before execution
- **project-structure-rules** — Project architecture and design principles for vm_builds Ansible project

### **Service Integration & Rollback**
- **rollback-architecture** — Rollback architecture and layered rollback model patterns
- **rollback-group-reconstruction** — Dynamic group reconstruction for rollback play patterns
- **rollback-per-feature** — Per-feature rollback implementation patterns and tag conventions
- **secret-generation** — Auto-generation and persistence patterns for secrets, keys, and dynamic configuration
- **service-config-validation** — Service configuration validation and config management patterns
- **systemd-lxc-compatibility** — Systemd sandboxing compatibility and LXC bind mount patterns
- **task-ordering** — Task dependency ordering for Ansible playbooks, ensuring prerequisites are met
- **vm-cleanup-maintenance** — VM cleanup completeness, performance optimization, and maintenance patterns
- **vm-lifecycle-architecture** — VM lifecycle architecture patterns and two-role service model
- **vm-provisioning-patterns** — VM provisioning patterns and step-by-step service creation

### **LAN Host Patterns**
- **lan-node-setup** — Add LAN hosts, env variables, inventory setup, bootstrap flow for Proxmox nodes behind OpenWrt
- **lan-ssh-patterns** — SSH ProxyJump for LAN hosts behind OpenWrt router

### **Container & VM Patterns**
- **lxc-container-patterns** — LXC container provisioning and configuration patterns
- **windows-vm-patterns** — Windows 11 VM provisioning, iGPU PCI passthrough, QEMU Guest Agent, PowerShell configuration

### **Fleet Management & Runtime Operations**
- **manager-api-pattern** — Manager API vs SSH decision framework, container-side script pattern (wifi_setup.sh, batman_trigger.sh), subscription model, fleet readiness gate

### **Learning & Development**
- **learn-from-mistakes** — Update skills and rules when encountering new issues to prevent recurrence
- **opencode-rules-writing** — Skill writing patterns and LLM-optimized skills
- **early-validation-patterns** — Proactive validation patterns to catch issues early and prevent debugging blind

## Project Structure

- `roles/` - Ansible roles with two-role pattern: `<type>_vm/lxc` + `<type>_configure`
- `playbooks/` - Main playbook execution flows and cleanup
- `molecule/` - Test scenarios: default (full integration), per-feature scenarios
- `inventory/` - Host groups and variables by deployment topology
- `images/` - Custom VM/container images (built via build-images.sh)
- `.state/` - Runtime state files (gitignored, environment-specific)
- `docs/projects/` - Project plans and implementation documentation
- `docs/architecture/` - System architecture and role dependency documentation

## Code Standards

### Ansible Conventions
- Use fully qualified collection names: `ansible.builtin.command`, not `command`
- NEVER use `local_action` - always use `delegate_to: localhost`
- Include `changed_when` or `failed_when` on command/shell tasks
- Use section-header comments (`# ── Section name ──`) to organize task files
- Capitalize first word in handler names and match `notify:` exactly

### OpenWrt/BusyBox Constraints
- Use `ansible.builtin.raw` ONLY for OpenWrt commands (no Python available)
- NEVER use `grep -P` - use `sed -n 's/pattern/\\1/p'` instead
- NEVER use heredocs in YAML | blocks for OpenWrt scripts (indentation breaks shebang)
- Switch OpenWrt opkg to HTTP: `sed -i 's|https://|http://|g' /etc/opkg/distfeeds.conf`
- Use `modprobe` explicitly after `opkg install kmod-*` (auto-load disabled)

### Variable and Secret Management
- Use `lookup('env', 'VAR_NAME')` for secrets - NEVER use vault files
- NEVER reference another role's `defaults/main.yml` directly
- ALWAYS use `env_generated_path` for auto-generated secrets and dynamic config
- Static constants in `group_vars/all.yml`, operator secrets in `.env/test.env`

## Build, Lint, and Test Commands

### Development Workflow
```bash
# Setup Python environment and dependencies
./setup.sh

# Lint checking before commits
ansible-lint && yamllint .

# Test individual roles with molecule
molecule converge     # Fast iteration (keeps baseline)
molecule verify       # Run assertions on converged state
molecule test         # Full clean-state test (destroys all)

# Test specific scenarios
molecule test -s default              # Full integration (6 nodes)
molecule test -s openwrt-security     # Per-feature scenario
molecule test -s pihole-lxc           # Service-specific test

# Build custom images
./scripts/build-images.sh --only router  # Build single image
./scripts/build-images.sh               # Build all images

# Python testing (for build.py changes)
pytest tests/ -v
```

### Critical Testing Rules

**MANDATORY: Test-First Development (TDD)**
- Write verify assertions BEFORE implementing features
- Test immediately when encountering failures - never debug blind
- ALWAYS reproduce production bugs on test machine first
- NEVER consider fix complete until `molecule test` passes end-to-end

**MANDATORY: Anti-Fake-Test Doctrine**
- NEVER mock `probe_host`, network connectivity, or hardware detection against hosts you control
- NEVER write tests that check YAML string content instead of running the actual code
- NEVER write a test that would pass identically if all infrastructure were offline
  (unless it tests pure Python logic like string parsing or command construction)
- Tests that probe real hosts MUST fail when those hosts are unreachable — that's the point
- `pytest tests/` is the infrastructure early warning system. If it passes while
  machines are down, the tests are lying.
- When a test mocks something, ask: "Would removing this mock make the test catch real problems?"
  If yes, remove the mock and test the real thing.
- Previous catastrophe: `TestResolveProxmoxHost` mocked `probe_host` with fake IPs.
  All 5 tests passed while `ai` was crashed and all WAN hosts were unreachable.
  Nobody knew until manual inspection. Those tests were deleted and replaced with
  `TestInfrastructureHealth` that probes real hosts from test.env.

**MANDATORY: Environment Validation**
- ALWAYS run `set -a && source test.env && set +a` before ANY molecule commands
- Test environment setup immediately: SSH connectivity, Ansible ping, variable export
- Validate with: `ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST "echo test"` and `ansible home -m ping`

**MANDATORY: Heartbeat API Server (callhome)**
- `.state/callhome_url` is the SOLE source of truth for `callhome_server`. Both `build.py` (production) and `prepare.yml` (test) write this file. Ansible hard-fails if it is missing.
- `prepare.yml` starts the headless API server automatically before converge. `cleanup.yml` stops it.
- NEVER hardcode `CALLHOME_SERVER` in env files. The URL is detected dynamically.
- During molecule runs, query `http://localhost:8088/api/fleet/health` for real-time fleet status.

**MANDATORY: Testing Workflow**
- Use `molecule converge + verify` for day-to-day iteration (preserves baseline)
- Use `molecule test` only for clean-state validation (destroys all)
- Run lint checks (`ansible-lint && yamllint .`) after ANY code changes
- Load relevant skills proactively when working with LXC, Docker, or new service types

**PROMPT YOURSELF:**
- "Should I test this right now?" YES - test after every significant change
- "Do I need to validate the environment?" YES - always before molecule commands
- "Should I load skills for this pattern?" YES - especially for LXC/Docker/Container work
- "Am I about to mock something I could test for real?" STOP — test the real thing
- "Am I debugging blind?" NO - reproduce on test machine first

## Safety and Architecture Rules

### NEVER Do These on Remote Hosts
- `ifdown --all`, `systemctl stop networking` - kills management network
- `ip link delete vmbr0` - destroys management bridge
- Hardcode specific bridges as WAN - WAN detected at runtime via default route
- Apply `WAN_MAC` at Proxmox VM NIC level - use MAC conflict detection flow
- Remove SSH keys or API tokens during cleanup - they're operator prerequisites
- `modprobe -r amdgpu` on single-GPU AMD hosts - kernel panic, host crash
- `shutdown`, `poweroff`, `halt` in cleanup or molecule files - bricks non-WoL hosts
- ANY destructive kernel operation on `wol_capable: false` hosts

### Host Recoverability (CRITICAL — production-breaking if violated)
- Every host MUST declare `wol_capable` (true/false) in `inventory/host_vars/`
- `ai` has `wol_capable: false` — USB ethernet only, no Wake-on-LAN
- NEVER run `modprobe -r amdgpu` or `modprobe -r i915` in broad-scope plays (hosts: proxmox) — use PCI bus rescan instead
- ONLY run GPU driver unload in per-feature cleanup (gaming_lxc) gated on VGA controller count >= 2
- NEVER shut down or crash hosts that cannot be remotely recovered
- `scripts/wol.sh` MUST NOT include non-WoL hosts — enforced by `tests/test_wol.py`
- `tests/test_host_safety.py` is a static linter that catches `modprobe -r amdgpu/i915` in broad-scope plays, `shutdown/poweroff` in cleanup/molecule files, and unrecognized `modprobe -r` modules
- ALWAYS run `pytest tests/` before committing — the safety linter catches this class of bug at test time, before it touches hardware
- Previous bug: E2E cleanup ran `modprobe -r amdgpu` on all hosts. `ai` (single AMD GPU, USB ethernet) kernel-panicked. Required physical power-on 3000 miles away

### Architecture Principles
- **Bake, don't configure**: NEVER install packages during configure roles
- **Two-role pattern**: Every service has `<type>_vm/lxc` + `<type>_configure`
- **One path, no fallbacks**: Never add fallback logic - fail with clear messages
- **Deploy_stamp pattern**: Include as last role in provision plays
- **Hard-fail over graceful degradation**: Expected hardware (iGPU, WiFi) must be present
- **Docker-in-LXC configure**: Target the HOST group (e.g., `service_nodes`), NOT the container dynamic group. `pct exec` only exists on the Proxmox host
- **Jinja2 vs Docker templates**: Docker `--format "{{.X}}"` conflicts with Jinja2. Use `docker image inspect` or escape with `{{ "{{" }}`

### Network and Bridge Management
- WAN bridge auto-detected via host default route device
- Proxmox LAN management IP: `.2` in LAN subnet, DYNAMIC but PERSISTENT
- Bootstrap IP migration: remove from WAN bridge after network restart
- Use detached scripts for network topology changes (firewall, interface assignment)
- NEVER assume PRIMARY_HOST is only reachability path

### Cleanup Completeness
- When ANY role deploys files, ALWAYS add to cleanup in BOTH `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`
- Cleanup removes ONLY files playbook deployed, NEVER operator-created credentials
- Remove generated env files: `test.env.generated`, `.env.generated`
- Use explicit VMID destruction, NEVER iterate `qm list`/`pct list`

## Task Ordering Patterns

Always resolve dependencies top-down:
1. Fix system state before package installation
2. Install packages before using package commands
3. Generate keys/credentials before configuring services
4. Configure before starting services
5. Start services before runtime verification
6. Network configuration before dependent services
7. Shared infrastructure before service provisioning

## Directory-Specific Rules

This project includes directory-specific AGENTS.md files that provide targeted instructions for different areas:

- **@roles/AGENTS.md** - Ansible role development, task conventions, and safety patterns
- **@molecule/AGENTS.md** - Testing workflows, TDD patterns, and diagnostic approaches  
- **@docs/projects/AGENTS.md** - Project planning structure and review processes
- **@docs/architecture/AGENTS.md** - System architecture and documentation standards
- **@playbooks/AGENTS.md** - Playbook execution patterns and async operations
- **@inventory/AGENTS.md** - Variable scoping and secret management
- **@scripts/AGENTS.md** - Script execution patterns and entry point conventions

## External File References

For development standards and patterns: @.agents/skills/writing-skills
For OpenWrt-specific patterns: @.agents/skills/openwrt-network-topology
For Proxmox safety rules: @.agents/skills/proxmox-safety-rules
For testing workflows: @.agents/skills/testing-workflow
For task ordering patterns: @.agents/skills/task-ordering
For secret generation: @.agents/skills/secret-generation
For clean baselines: @.agents/skills/clean-baselines
For project structure: @.agents/skills/project-structure-rules
For async patterns: @.agents/skills/async-job-patterns

## Cross-Coverage Patterns

**Network Changes:**
- Bridge management: @.agents/skills/openwrt-network-topology
- VLAN configuration: @.agents/skills/openwrt-virtual-vlan
- Network restarts: @.agents/skills/openwrt-network-restart

**Infrastructure Safety:**
- Host operations: @.agents/skills/proxmox-system-safety
- Network interfaces: @.agents/skills/proxmox-network-safety
- SSH connectivity: @.agents/skills/proxmox-ssh-safety
- Cleanup completeness: @.agents/skills/proxmox-cleanup-safety

**Service Integration:**
- Feature patterns: @.agents/skills/openwrt-feature-integration
- Configuration validation: @.agents/skills/service-config-validation
- DNS and mesh: @.agents/skills/openwrt-dns-mesh-setup

**Testing and Validation:**
- Testing workflow: @.agents/skills/testing-workflow
- Performance optimization: @.agents/skills/molecule-performance
- Diagnostics patterns: @.agents/skills/openwrt-diagnostics

**Runtime Operations & Fleet Management (3-tier hierarchy):**
- Manager API pattern: @.agents/skills/manager-api-pattern
- Tier 1 (Super Manager): UI pages → HTTP only → Manager API (NEVER SSH)
- Tier 2 (Manager): REST endpoints → SSH → container-side scripts
- Tier 3 (Container scripts): `wifi_setup.sh`, `batman_trigger.sh` (baked into image, self-contained)
- Fleet readiness: `/api/fleet/ready` gate in verify.yml, `_fleet_api_ready` dual-path pattern
- Ansible owns initial deploy; Manager owns runtime (status, mode switching, toggling)
- NEVER embed inline shell in manager endpoints — use container-side scripts

When working in specific directories or on particular tasks, load the relevant directory AGENTS.md or skill file for detailed guidance.

## Deployment and Testing Strategy

- **Molecule default**: Full integration test with 6 nodes (home, mesh1, ai, mesh2, bridge-1, bridge-2)
- **Per-feature scenarios**: Test individual features in isolation
- **Baseline workflow**: Use converge/verify for iteration, test for validation
- **Test machine**: Use for debugging before touching production
- **TDD approach**: Write assertions first, then implement features

This project prioritizes reliability, clear failure modes, and comprehensive testing over convenience or speed.
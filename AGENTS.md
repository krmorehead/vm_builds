# vm_builds Project Rules

This is an Ansible project that automates VM and LXC container provisioning on Proxmox VE. The project deploys OpenWrt router VMs with shared LXC infrastructure and follows strict architectural patterns.

## SHOW STOPPER: Unreachable Host Protocol

**ANY unreachable host is a 5-alarm emergency. FULL STOP.**

- When `pytest tests/` reports a host unreachable, `molecule` shows `unreachable=1`, OR SSH returns "Permission denied": STOP ALL WORK.
- Investigate cause IMMEDIATELY. Check terminal history for `modprobe -r`, GPU operations, `shutdown`.
- NEVER dismiss as "pre-existing." NEVER continue development. NEVER say "not caused by our changes."
- NEVER "skip this host and build the next one." ALL hosts must be healthy before ANY work proceeds.
- For `wol_capable: false` hosts (`ai`): physical power-on required. No remote recovery. Report this to the user immediately.
- Do NOT validate features against a substitute host when the actual target is down. If `ai` runs Sunshine and `ai` is unreachable, Moonlight verification is IMPOSSIBLE.

### Mandatory fleet health checks

Run SSH connectivity checks to ALL hosts at these mandatory checkpoints:
1. **Session start** — before beginning any work
2. **After every build/deploy/converge** — check ALL hosts, not just the target
3. **Before proceeding to next host** — in multi-host operations
4. **Every 30 minutes** — during long sessions

Any of these are SHOW STOPPERS (not just EHOSTUNREACH):
- SSH "Permission denied" (authentication failure = lost access)
- SSH connection refused or timed out
- Proxmox API (port 8006) connection refused
- SuperManager showing 0% disk AND 0% memory (Manager can't SSH to host)
- Ansible `unreachable=1` in any play recap

### Previous catastrophes

1. (2026-03-23): Agent dismissed ai unreachable THREE TIMES over 4 hours. Wasted entire session. ai required physical power-on 3000 miles away.
2. (2026-04-08): ai SSH auth failed mid-session. Agent continued building 4 other hosts individually instead of stopping. User had to explicitly call out the show stopper. The "skip and continue" instinct is ALWAYS wrong.

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
- **webui-design-system** — NiceGUI design system, color semantics, scalable test constants
- **webui-manual-testing** — Manual testing procedures for SuperManager, Manager, and Kiosk UIs
- **manual-testing-playbook-writing** — How to write comprehensive manual testing playbooks: exhaustive feature enumeration, per-app steps, host-awareness, section structure
- **webui-ux-principles** — UX design principles: color semantics, icon choices, layout, information hierarchy

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
- **code-review-checklist** — Code review covering MVC/OOP separation, Ansible safety, UI conventions, test quality

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

### **Fleet Management & Runtime Operations (4-Tier Architecture)**
- **manager-api-pattern** — 4-tier Manager hierarchy (SuperManager → ClusterManager → NodeManager → container scripts), event-driven batman/bridge propagation, fleet readiness gate, container-side script pattern (wifi_setup.sh, batman_trigger.sh), subscription model, cluster definition and inter-manager communication

### **Learning & Development**
- **learn-from-mistakes** — Update skills and rules when encountering new issues to prevent recurrence
- **opencode-rules-writing** — Skill writing patterns and LLM-optimized skills
- **early-validation-patterns** — Proactive validation patterns to catch issues early and prevent debugging blind
- **code-review-checklist** — Code review checklist covering MVC/OOP separation, Ansible safety, UI conventions, and test quality

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

# FAST iteration loop (90% of the time — preserves baseline)
molecule converge && molecule verify

# Fix ONE broken image (fastest loop)
./scripts/build-images.sh --host $PRIMARY_HOST --only pihole  # ~2-3 min
molecule test -s pihole-lxc                                    # per-feature

# Full E2E clean-state proof (LAST STEP — after all images pass)
molecule test                                # Full integration (6 nodes)

# Build custom images (IN PARALLEL across 6 hosts)
./scripts/build-images.sh --only router      # Build single image
./scripts/build-images.sh                    # Build all images

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
- NEVER mock SSH commands (`_ssh_exec`, subprocess SSH calls) that trigger real operations on real nodes you own
- NEVER write tests that check YAML string content instead of running the actual code
- NEVER write a test that would pass identically if all infrastructure were offline
  (unless it tests pure Python logic like string parsing or command construction)
- Tests that probe real hosts MUST fail when those hosts are unreachable — that's the point
- `pytest tests/` is the infrastructure early warning system. If it passes while
  machines are down, the tests are lying.
- When a test mocks something, ask: "Would removing this mock make the test catch real problems?"
  If yes, remove the mock and test the real thing.
- Every `patch()` or `monkeypatch` call MUST have an inline comment with TWO parts:
  (1) WHY this mock is necessary (what side effect it prevents), and
  (2) HOW the test still genuinely validates the feature despite the mock.
  If you cannot write both sentences, the mock is unjustified — remove it.
- Previous catastrophe: `TestResolveProxmoxHost` mocked `probe_host` with fake IPs.
  All 5 tests passed while `ai` was crashed and all WAN hosts were unreachable.
  Nobody knew until manual inspection. Those tests were deleted and replaced with
  `TestInfrastructureHealth` that probes real hosts from test.env.
- Previous catastrophe: Batman API tests mocked `heartbeat._ssh_exec` — the exact
  SSH call that triggers batman on real nodes. Tests passed while batman mode was
  completely broken. The mock hid the real failure for an entire test cycle.

**MANDATORY: Environment Validation**
- ALWAYS run `set -a && source test.env && set +a` before ANY molecule commands
- Test environment setup immediately: SSH connectivity, Ansible ping, variable export
- Validate with: `ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST "echo test"` and `ansible home -m ping`

**MANDATORY: Heartbeat API Server (callhome)**
- `.state/callhome_url` is the SOLE source of truth for `callhome_server`. Both `build.py` (production), `prepare.yml` (test), and the NiceGUI app write this file on startup. Ansible hard-fails if it is missing.
- `prepare.yml` starts the headless API server automatically before converge. `cleanup.yml` stops it.
- NEVER hardcode `CALLHOME_SERVER` in env files. The URL is detected dynamically.
- ALL ports are controlled by the `WEBUI_PORT` env var (default: 52500, set to 52525 in test.env/.env). This port MUST be open in the controller's firewall.
- During molecule runs, query `http://localhost:$WEBUI_PORT/api/fleet/health` for real-time fleet status.

**MANDATORY: Testing Workflow (Fail-Fast)**
- Use `molecule converge + verify` for day-to-day iteration (preserves baseline, FAST)
- Fix broken images INDIVIDUALLY: `build-images.sh --only <target>` + `molecule test -s <type>-lxc`
- Full E2E (`molecule test`) is the LAST STEP — only after all images pass individually
- NEVER run `molecule test` as the primary iteration loop — it destroys and rebuilds everything
- If E2E takes 30+ minutes, the bake pipeline is broken (images being re-uploaded or packages installed at runtime)
- You have 6 Proxmox hosts — build images IN PARALLEL across them
- Run lint checks (`ansible-lint && yamllint .`) after ANY code changes
- Load relevant skills proactively when working with LXC, Docker, or new service types

**MANDATORY: Proxmox Hosts Are Bakeable Targets**
- Proxmox hosts are machines you control, same as containers. Host-level infrastructure (socat, iptables rules, systemd units, kernel parameters) is part of the deploy pipeline
- NEVER hand-configure Proxmox hosts. Everything needed should be deployed via Ansible roles or baked into the base Proxmox image
- NEVER use `nohup ... &` for persistent host services. Deploy systemd units with `Restart=always`. Background processes die when SSH sessions close
- Host-level services (e.g., `manager-api-proxy.service`, `supermanager-relay.service`) are deployed by provisioning roles and cleaned up by `playbooks/cleanup.yml`
- Previous bug (2026-04-12): socat proxies for 4-tier heartbeat were started with `nohup &` and died when Ansible's SSH ControlMaster closed. Fix: systemd units with Restart=always

**MANDATORY: Manual Testing Requires Fully Converged System**
- NEVER start manual testing (browser UI, CLI playbooks, API queries) unless `molecule test` has completed and ALL 6 hosts are on the 10.10.10.x LAN with ALL containers deployed and heartbeating
- NEVER rationalize "No route to host" as "expected in pre-mesh state" — if you see that error during manual testing, you started too early
- The system ALWAYS ends `molecule test` in a pristine state. Manual testing starts from that pristine state. Build first, test second. Always.
- Previous catastrophe (2026-04-10): Agent started manual testing before converging. Wasted entire session verifying expected failures.

**PROMPT YOURSELF:**
- "Should I test this right now?" YES - test after every significant change
- "Do I need to validate the environment?" YES - always before molecule commands
- "Should I load skills for this pattern?" YES - especially for LXC/Docker/Container work
- "Am I about to mock something I could test for real?" STOP — test the real thing
- "Am I debugging blind?" NO - reproduce on test machine first
- "Is the system fully converged for manual testing?" If not — run molecule test FIRST

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
- **Bake, don't configure**: NEVER install packages during configure roles — this applies to containers AND Proxmox hosts. Host-level packages (socat), systemd units, and iptables rules are infrastructure that gets deployed, not hand-configured
- **Two-role pattern**: Every service has `<type>_vm/lxc` + `<type>_configure`
- **One path, no fallbacks**: Never add fallback logic - fail with clear messages
- **Deploy_stamp pattern**: Include as last role in provision plays
- **Hard-fail over graceful degradation**: Expected hardware (iGPU, WiFi) must be present
- **Docker-in-LXC configure**: Target the HOST group (e.g., `service_nodes`), NOT the container dynamic group. `pct exec` only exists on the Proxmox host
- **Jinja2 vs Docker templates**: Docker `--format "{{.X}}"` conflicts with Jinja2. Use `docker image inspect` or escape with `{{ "{{" }}`
- **Proxmox = bakeable target**: Host-level systemd units, iptables rules, kernel parameters are all deployable infrastructure. NEVER use `nohup &` for persistent services — systemd units with `Restart=always`

### Network and Bridge Management
- WAN bridge auto-detected via host default route device
- Proxmox LAN management IP: `.2` in LAN subnet, DYNAMIC but PERSISTENT
- Bootstrap IP migration: remove from WAN bridge after network restart
- Use detached scripts for network topology changes (firewall, interface assignment)
- NEVER assume PRIMARY_HOST is only reachability path

### Cleanup Completeness
- `playbooks/cleanup.yml` is the SINGLE unified cleanup for all contexts (molecule, CLI, SuperManager). `molecule/default/cleanup.yml` is a one-line import.
- When ANY role deploys files, add to `playbooks/cleanup.yml` only — there is ONE cleanup to maintain
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
- Code review (MVC, safety, conventions): @.agents/skills/code-review-checklist
- Manual UI testing: @.agents/skills/webui-manual-testing
- Writing manual test playbooks: @.agents/skills/manual-testing-playbook-writing

**Web UI Design and UX:**
- Design system (colors, CSS, constants): @.agents/skills/webui-design-system
- UX principles (human-intuitive design): @.agents/skills/webui-ux-principles
- Manual testing procedures: @.agents/skills/webui-manual-testing

**Runtime Operations & Fleet Management (4-tier hierarchy):**
- Manager API pattern: @.agents/skills/manager-api-pattern
- Tier 1 (SuperManager / `app.py`): Global fleet view, nodes.json, deploy orchestration
- Tier 2 (ClusterManager / `kiosk_server.py` on router node): Subnet-scoped fleet,
  event broadcast DOWN to child Managers, relay UP to SuperManager.
  A cluster = one household's network (all nodes on 10.10.10.x LAN).
- Tier 3 (NodeManager / `kiosk_server.py` per host): Local container ops only,
  relays heartbeats UP. NEVER iterates other hosts.
- Tier 4 (Container scripts): `wifi_setup.sh`, `batman_trigger.sh`, `callhome.py`
  (baked into image, self-contained, KEY=value output)
- Fleet readiness: `/api/fleet/ready` gate in verify.yml, `_fleet_api_ready` dual-path pattern
- Ansible owns initial deploy; Manager owns runtime (status, mode switching, toggling)
- NEVER embed inline shell in manager endpoints — use container-side scripts
- NEVER put fleet-level ops (batman_fleet, get_mesh_nodes) on NodeManager — ClusterManager only

When working in specific directories or on particular tasks, load the relevant directory AGENTS.md or skill file for detailed guidance.

## Standard Work Cycle (MANDATORY)

Every change that touches `build-images.sh` or a configure role follows this
exact 6-step cycle. No shortcuts. No skipping steps. No fallback code.

### Step 1: Update image build scripts
Modify `build-images.sh` to bake new content into the image. All packages,
static config, systemd enablement, user/group setup, and application code
go HERE. Configure roles handle ONLY host-specific runtime config.

### Step 2: Build images on test units (PARALLEL, REQUIRED)
Run `build-images.sh --host <ip> --only <target>` on the Proxmox hosts.
You have 6 healthy units (home, mesh1, ai, mesh2, bridge-1, bridge-2).
Build images IN PARALLEL across them. This step is NOT optional — if you
skip it, configure roles will fail because baked content doesn't exist.

**THERE ARE NO FALLBACKS. NEVER add "legacy image" detection or fallback
code in configure roles.** The image is the source of truth. Build it.

```bash
# Parallel image builds across hosts
./scripts/build-images.sh --host $PRIMARY_HOST --only pihole &
./scripts/build-images.sh --host $AI_HOST --only wireguard &
./scripts/build-images.sh --host $MESH_2_HOST --only kiosk &
wait
```

### Step 3: Write tests and playbook updates (while images build)
While images are building, write unit tests, integration tests, and
update playbook/role code. NEVER just poll the build.

### Step 4: Run E2E test suite (after images are ready)
Once images are rebuilt, run `molecule test` for full integration.
The `proxmox_lxc` version-mismatch system auto-rebuilds containers
from fresh images.

### Step 5: Code review (while E2E runs)
While `molecule test` runs (~30-45 min), review for DRY, architecture
adherence, code quality, KISS, OOP service patterns, and test quality.

### Step 6: Manual playbook verification (after E2E passes)
After `molecule test` passes, manually run ALL playbook plays to verify
the 4-tier manager system works end-to-end. Every container, every
heartbeat, every manager relay.

No rollback strategy needed — old image versions are saved and we can
just rebuild. Straightforward clean management.

## Deployment and Testing Strategy

- **Molecule default**: Full integration test with 6 nodes (home, mesh1, ai, mesh2, bridge-1, bridge-2)
- **Per-feature scenarios**: Test individual features in isolation
- **Baseline workflow**: Use converge/verify for iteration, test for validation
- **Test machine**: Use for debugging before touching production
- **TDD approach**: Write assertions first, then implement features

This project prioritizes reliability, clear failure modes, and comprehensive testing over convenience or speed.
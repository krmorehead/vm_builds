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
9. Every feature milestone MUST include an **implementation pattern** note specifying: which task file to create, which plays to add to `site.yml`, which tags to use, and which molecule scenario to create. NEVER leave "how it integrates" as an open question.
10. Per the project's "Bake, don't configure at runtime" principle (`project-structure.mdc`): every package belongs in the image build, NOT at runtime. If a plan proposes `opkg install` or `apt install` during converge, reject it. Configure roles only do host-specific topology changes.
11. Rollback MUST fully restore the baseline state, including auth credentials and connection methods. If a milestone changes how Ansible connects to a target (e.g., password → key auth), the rollback MUST reverse the connection method too.
12. When a milestone changes the auth/connection method for a dynamic group, the plan MUST specify how subsequent plays and per-feature scenarios detect and adapt to the new auth method.
13. Every plan MUST include a **Milestone Dependency Graph** (ASCII tree) showing the ordering and blocking relationships at a glance.
14. Before execution, ALWAYS run the plan through the **Plan Review Checklist** (below) against all referenced skills. Previous bug: the OpenWrt router plan had two critical issues (SSH auth transition, dynamic group persistence) that would have broken implementation — both caught only by cross-referencing skills during review.

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

## Milestone dependency graph

Include an ASCII tree showing milestone ordering at the top of the
Milestones section:

```markdown
## Milestone dependency graph
M0 (test infra)
├── M1 (security) ← self-contained
├── M2 (VLANs) ← self-contained
├── M3 (encrypted DNS) ← self-contained
├── M4 (mesh) ← self-contained
├── M5 (pihole DNS) ← blocked on Pi-hole LXC project
├── M6 (syslog) ← blocked on Netdata project
├── M7 (monitoring) ← blocked on Netdata project
└── M8 (docs + integration)
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

When implementing a project, blocked milestones SHOULD be moved to
their downstream projects rather than kept as stubs. Stubs in
`site.yml`, `cleanup.yml`, and task files create dead code that
confuses future maintainers. Instead, document the integration point
in the architecture docs (e.g., "Pi-hole DNS forwarding play will be
added by the Pi-hole LXC project"). The downstream project owns both
its own infrastructure AND the OpenWrt integration plays.

Previous bug: M5-M7 stubs (pihole_dns, syslog, monitoring) were
implemented as task files + site.yml plays + cleanup.yml rollback
plays, then had to be removed entirely because they belonged in their
respective downstream projects.

## Task ordering within milestones

Every milestone's task list MUST follow dependency order. Walk through each
task and ask: "What must already exist for this to succeed?"

Canonical ordering for a configure milestone:
1. Fix system baseline state (broken packages, missing modules)
2. Install packages
3. Generate keys/credentials (requires package tools like `wg genkey`)
4. Template configuration files (requires generated keys)
5. Start/enable services (requires config files)
6. Configure runtime state (firewall rules, sysctl, NAT — requires services)
7. Persist runtime state (save iptables rules, write generated env file)

For a provisioning milestone:
1. Load host-side kernel modules (LXC shares host kernel)
2. Upload images/templates
3. Create VM/container
4. Configure auto-start
5. Start VM/container
6. Clean template baseline (fix broken packages in LXC)
7. Register in dynamic inventory

NEVER put key generation before package installation. NEVER put service
start before configuration. NEVER install packages before fixing broken
system state.

See: `task-ordering` rule for the full ordering reference and common mistakes.

## Secret generation in milestones

When a milestone generates secrets (keys, tokens, PSKs):
1. Document which env vars are auto-generated and which require user input
2. Specify the generated file: `test.env.generated` (test) or
   `.env.generated` (production), auto-detected via `env_generated_path`
3. Include a verify assertion checking the generated file exists and
   contains the expected keys
4. Include cleanup of the generated file in rollback

See: `secret-generation` rule for the full pattern.

## Milestone sizing

Each milestone should be completable in a single focused session
(2-4 hours). If a milestone has more than 8-10 checkbox items, split
it. If it has fewer than 3, merge it with an adjacent milestone.

## Plan review checklist

Before considering a plan ready for execution, verify each item.

### Structural checks

1. **Dynamic group persistence**: Do any plays target dynamic groups
   (`openwrt`, `pihole`, etc.)? If so, every entry point that runs as a
   separate `ansible-playbook` invocation (per-feature converge, verify,
   cleanup/rollback) MUST reconstruct the group. `add_host` is ephemeral.
2. **Auth transitions**: Does any milestone change how Ansible connects
   to a target (password → key, add SSH key, disable password)? If so:
   - The milestone MUST specify the exact ordering (deploy → verify → lock)
   - The milestone MUST re-register the host via `add_host` with new args
   - Subsequent milestones MUST detect which auth method is active
   - Rollback MUST restore the original auth method
3. **Rollback completeness**: Does each rollback section undo EVERYTHING
   the milestone changed? Check: UCI config, packages, files, auth state,
   cron jobs, service enablement. A partial rollback leaves the system in
   an undefined state.
4. **Skill rule compliance**: For each referenced skill, scan its Rules
   section. Every applicable NEVER/ALWAYS constraint should be reflected
   in the task items (e.g., retry/delay for opkg, pipefail for shell tasks,
   detached scripts for firewall restarts).
5. **Implementation pattern**: Does every feature milestone specify the
   task file name, `site.yml` play structure (target group + tags), and
   `deploy_stamp` pairing? Ambiguity here causes inconsistent implementations.
6. **Molecule scenario**: Does every feature milestone that adds testable
   behavior also create a per-feature molecule scenario?
7. **No fallback paths**: Does the plan introduce any "try X, fall back to Y"
   logic? If so, reject it. One tested path per feature. Missing prerequisites
   should fail with an actionable error message, not silently degrade.
8. **Verify from the right host**: Do verify assertions run on the Proxmox
   host (via `qm`, shell commands) or inside the VM (via dynamic group)?
   If inside the VM, the verify needs group reconstruction too.

### Container/VM capability checks

9. **LXC features and capabilities**: If the plan provisions an LXC
   container, verify that required features are declared:
   - `nesting=1`: needed for iptables/nftables inside unprivileged containers
   - `mount=cgroup`: needed for cgroup mounts (systemd containers)
   - `keyctl=1`: needed for kernel key management
   - If no special features are needed, the plan MUST explicitly state
     "no special LXC features required" so reviewers don't wonder.
   Previous bug: WireGuard plan omitted `nesting=1`. The `iptables -t nat
   MASQUERADE` command in M2 would have failed with "Permission denied"
   at runtime.
10. **Bake, don't configure at runtime** (per `project-structure.mdc`): if
    the plan mentions runtime package installation (`opkg install`,
    `apt install`), reject it. Packages AND base configuration belong in
    the image build. Configure roles only apply host-specific topology
    (IPs, bridges, subnets, forwarding targets). If software is already
    in the base OS (e.g., rsyslog in Debian), the image build should
    pre-configure it — the configure role should not set up listeners,
    spool directories, or logrotate from scratch.
11. **Kernel module host-side loading**: If the service needs kernel modules
    (WireGuard, VFIO, GPU drivers), verify the plan loads them on the
    Proxmox HOST (not inside the container — LXC shares the host kernel).
    Include persistence via `/etc/modules-load.d/` and cleanup removal of
    both the config file and `modprobe -r`.
12. **WiFi PHY namespace move**: If the plan provisions an LXC container
    that needs WiFi access, verify:
    - Container is privileged (`unprivileged: false`) — namespace moves
      require CAP_NET_ADMIN
    - `--ostype unmanaged` for OpenWrt containers (Proxmox can't auto-detect)
    - `lxc_ct_skip_debian_cleanup: true` for non-Debian containers
    - WiFi driver loading on the host before PHY detection
    - Hard-fail if no WiFi PHY found (all wifi_nodes must have WiFi)
    - Hookscript for PHY persistence across container restarts
    - `proxmox_pci_passthrough` must clean stale vfio-pci bindings on
      non-router hosts before the mesh role runs
    Previous bug: mesh1 WiFi was invisible because stale vfio-pci.conf
    and blacklist-wifi.conf from a prior run kept iwlwifi blacklisted
    and the device bound to vfio-pci.

### Image build checks

13. **Image build milestone**: Does the plan include an image build
    milestone (M0) with a `build-images.sh` section? Every service needs
    a purpose-built image, even if the software is pre-installed in the
    base OS. The image build bakes in default configuration, spool
    directories, and service-specific setup. If the plan says "pre-installed
    on Debian, no build needed", reject it — the IMAGE may not need extra
    packages, but it DOES need pre-configuration.
    Previous bug: rsyslog plan skipped M0 because "rsyslog is pre-installed
    on Debian." But the configure role then had to create spool directories,
    enable TCP modules, and set up logrotate at runtime — all of which
    belong in the image.
14. **Image template variables**: Does the plan define custom template
    variables (`<type>_lxc_template`, `<type>_lxc_template_path`) in
    `group_vars/all.yml`? Using `proxmox_lxc_default_template` directly
    bypasses the image verification gate. Every service should reference
    its own template variable so the provision role can hard-fail if the
    custom image is missing.

### Cross-reference checks

15. **Prerequisite verification**: Grep the codebase to confirm claimed
    prerequisites actually exist: VMIDs in `group_vars/all.yml`, flavor
    groups in `inventory/hosts.yml`, dynamic groups in inventory, platform
    groups in `molecule/default/molecule.yml`.
16. **site.yml play ordering**: Verify the proposed play position doesn't
    conflict with existing plays. Count the actual play numbers. Clarify
    positioning relative to `never`-tagged per-feature plays. If the new
    plays are NOT tagged `never`, explicitly state they run during normal
    converge.
17. **Tag collision**: Verify proposed tags don't collide with existing
    tags in `site.yml` or `cleanup.yml`.
18. **Shared tags**: If the plan uses a tag shared with another service
    (e.g., `[monitoring]` for rsyslog + netdata), document this is
    intentional and explain the implication: you cannot deploy just one
    of the services via tag once both exist in `site.yml`.
19. **Cleanup parity**: Verify that files deployed by the new roles are
    added to BOTH `molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`.
    Also verify `playbooks/cleanup.yml` gets rollback tags if the service
    supports per-feature rollback.
20. **Architecture doc consistency**: Verify the plan's plays, tags, and
    resource allocations match `overview.md`'s target site.yml and VM
    table. Flag any discrepancies.

### Completeness checks

21. **Gitignore coverage**: If the plan creates new generated or state
    files (e.g., `.env.generated`, `.state/`), verify they are already in
    `.gitignore`. Don't add redundant task items for files already covered.
22. **Predictable IP for consumers**: If the service will be consumed by
    other services (routing, DNS forwarding, log collection), verify the
    plan addresses how consumers discover a stable IP (static lease, DNS,
    etc.). Document this even if the solution is deferred to a downstream
    project.
23. **Future integration notes**: If the service introduces a new pattern
    (e.g., `.env.generated` accumulation, NAT routing), add a "Future
    Integration Considerations" section documenting how downstream
    projects should interact with it.
24. **Network topology assumption**: Does the plan document which host
    topologies the service supports? If the flavor group could include
    hosts both behind OpenWrt (LAN) and directly on WAN, the plan MUST
    specify the topology branching strategy (bridge, subnet, gateway,
    DNS). A "Network topology assumption" section is required for all
    LXC container and VM plans.
    Previous bug: rsyslog plan only said "static IP on LAN bridge" but
    `monitoring_nodes` appears in ALL build profiles including Gaming
    Rig (no OpenWrt). WAN-connected hosts need different bridge, gateway,
    and DNS settings.
25. **Container IP offset allocation**: If the service uses static IPs
    computed from an offset, verify the offset is defined in
    `group_vars/all.yml` and doesn't collide with existing allocations.
    Current allocations: WireGuard 3–6, Pi-hole 10, rsyslog 11, Netdata
    12, MeshWiFi 13, HA 14, Jellyfin 15. WAN offsets add +200. Check WAN
    offset against physical host IPs on the supernet.
26. **Milestone consolidation**: Are any milestones redundant? Provisioning
    and site.yml integration should be in the SAME milestone (not split).
    Per-feature rollback plays in cleanup.yml should be in the testing
    milestone (where per-feature scenarios are defined), not a separate
    milestone.
27. **Deferred work ownership**: If the plan contains milestones that are
    "blocked on" downstream projects, verify they are explicitly deferred
    to those projects with a clear integration point. Stubs in site.yml,
    cleanup.yml, and task files create dead code.
    Previous bug: OpenWrt M5-M7 stubs (pihole_dns, syslog, monitoring)
    were implemented then removed because they belonged downstream.
28. **Testing Strategy section**: Every plan MUST include a "Testing
    Strategy" section with: (a) parallelism in `molecule/default`, (b)
    per-feature scenario hierarchy, (c) day-to-day workflow (bash
    commands), (d) teardown table showing what each scenario creates and
    destroys and its baseline impact.
29. **Documented exceptions to bake principle**: Three documented
    exceptions exist. Plans using these MUST explicitly state the
    exception and rationale:
    - **Docker pull of pinned image tag**: deterministic, versioned,
      idempotent (e.g., Home Assistant pre-pulls HA container image)
    - **Desktop VMs via cloud image + apt**: full desktop environments
      are too large and hardware-dependent for pre-built images; cloud
      image + cloud-init is the VM community standard
    - **Windows VMs via ISO + autounattend.xml**: install-from-ISO IS
      the bake approach for Windows — deterministic, unattended, with
      drivers pre-injected
    Any OTHER runtime package installation is still rejected.
30. **Cross-cutting milestone ownership**: If the plan deploys shared
    infrastructure (e.g., display-exclusive hookscript), identify which
    project OWNS the deployment and which projects only ATTACH. Only one
    project deploys; others reference it. Document the owning project
    explicitly.
    Previous bug: Kodi, Moonlight, and Desktop VM plans all had tasks to
    deploy the display-exclusive hookscript. Consolidated ownership to
    the Kiosk project.
31. **Separate hardware topology**: If the service runs on separate
    physical hardware (e.g., Gaming Rig), the plan MUST document the
    hardware topology separately. Separate hardware may lack OpenWrt,
    may use a different build profile, and may require hardware-dependent
    testing strategies (skip when hardware unavailable).
32. **VA-API driver portability**: Image builds for services that use
    iGPU (Jellyfin, Kodi, Moonlight) SHOULD include BOTH Intel and AMD
    VA-API driver packages. At runtime, only the matching driver loads.
    This avoids rebuilding images when hardware changes.

## Cross-references

- Reference skills inline: `See: rollback-patterns skill.`
- Reference architecture docs: `See: docs/architecture/overview.md, Network Topology.`
- Reference other project plans by their directory name:
  `Blocked on: 2026-03-09-00-shared-infrastructure, Milestone 1.`

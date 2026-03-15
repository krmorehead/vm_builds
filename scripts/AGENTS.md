# Script Execution Rules

This AGENTS.md provides specific instructions for agentic coding agents working in the scripts/ directory. These rules focus on script execution patterns, entry point conventions, and build process automation.

## External File Loading

CRITICAL: When you encounter a file reference (e.g., @.cursor/rules/project-structure.mdc), use your Read tool to load it on a need-to-know basis. They're relevant to the SPECIFIC task at hand.

Instructions:
- Do NOT preemptively load all references - use lazy loading based on actual need
- When loaded, treat content as mandatory instructions that override defaults
- Follow references recursively when needed

## Essential Skills Reference

**Script Development:**
- @.agents/skills/build-entry-point - Entry point orchestration patterns
- @.agents/skills/python-code-style - Python code conventions
- @.agents/skills/build-testing - Script testing patterns
- @.agents/skills/writing-skills - Skill writing patterns

**Image Building:**
- @.agents/skills/openwrt-image-builder - OpenWrt image building automation
- @.agents/skills/image-management-patterns - Image management patterns
- @.agents/skills/openwrt-mesh-lxc-wifi - Mesh LXC image patterns

**System Integration:**
- @.agents/skills/systemd-lxc-compatibility - Service compatibility patterns
- @.agents/skills/proxmox-system-safety - System safety operations

## Development Guidelines

For project structure and entry point conventions: @.cursor/rules/project-structure.mdc

## Cross-Coverage Rules

### From Other Directories
- **Build entry point**: Reference @.agents/skills/build-entry-point for orchestration patterns
- **Python code style**: Use @.agents/skills/python-code-style for script conventions
- **Build testing**: Apply @.agents/skills/build-testing for script validation
- **Image building**: Reference @.agents/skills/openwrt-image-builder for build automation
- **System compatibility**: Use @.agents/skills/systemd-lxc-compatibility for service patterns

## Entry Point Convention (CRITICAL)

**`build.py` is the SINGLE entry point for running Ansible.** ALL shell scripts MUST delegate to it.

**NEVER** call `ansible-playbook` directly from `run.sh` or `cleanup.sh`.

`build.py` handles:
- Env file parsing (with quote stripping)
- Required var validation
- Host probing with state file fallback
- Playbook resolution

Bypassing it loses all of these capabilities.

## Key Scripts Reference

| Script | Purpose |
|--------|---------|
| `setup.sh` | Bootstrap .venv + pip + ansible-galaxy |
| `run.sh` | Convenience wrapper — delegates to `build.py` |
| `cleanup.sh` | Restore / full-restore / clean / rollback — delegates to `build.py` |
| `build-images.sh` | Builds custom images (mesh LXC, router VM, Pi-hole, rsyslog, Netdata, WireGuard). Use `--only <target>` for selective rebuilds |
| `wol.sh` | Wake-on-LAN utility: wake hosts by alias or MAC. Proxied WoL for LAN hosts via PRIMARY_HOST |

## Script Patterns

### setup.sh
Bootstrap script for development environment:
- Creates Python virtual environment
- Installs dependencies (Ansible, Molecule, linters, Galaxy collections/roles)
- Should be run once after cloning, and can be re-run to update dependencies

### run.sh
Convenience wrapper that delegates to `build.py`:
```bash
#!/bin/bash
# Delegates all execution to build.py
python3 build.py "$@"
```

**NEVER** implement logic directly in `run.sh` — always delegate to `build.py`.

### cleanup.sh
Cleanup and rollback operations that delegate to `build.py`:
```bash
#!/bin/bash
# Delegates all cleanup to build.py  
python3 build.py cleanup "$@"
```

Supports tags for selective cleanup:
- `[cleanup]` — Full cleanup
- `[rollback-*]` — Specific feature rollback
- `[full-restore]` — Restore from backup
- `[clean]` — Clean state removal

### build-images.sh
Custom image building automation:

**Usage:**
```bash
# Build all images
./build-images.sh

# Build specific image
./build-images.sh --only openwrt
./build-images.sh --only pihole
./build-images.sh --only wireguard
```

**Targets:**
- `openwrt` — OpenWrt router VM image
- `mesh` — OpenWrt Mesh LXC template
- `pihole` — Pi-hole DNS container
- `rsyslog` — rsyslog log collector
- `netdata` — Netdata monitoring agent
- `wireguard` — WireGuard VPN container

**Image Build Process:**
1. Each service gets its own build section
2. Packages are baked into images (never installed at runtime)
3. Images are stored locally in `images/` directory
4. Built images are uploaded to Proxmox during provisioning

### wol.sh
Wake-on-LAN utility for remote host recovery:

**Usage:**
```bash
# Wake specific host by alias
./wol.sh home
./wol.sh mesh1
./wol.sh ai
./wol.sh mesh2

# Wake by MAC address
./wol.sh 00:23:24:54:23:fa

# Proxy WoL for LAN hosts via PRIMARY_HOST
./wol.sh mesh1  # Proxied through home
```

**Requirements:**
- NIC with WoL support
- WoL enabled in BIOS
- `ethtool Wake-on: g` on management NIC
- Machine in standby (S5) with power connected

**Limitations:**
- USB ethernet adapters do NOT support WoL
- Hosts with USB-only networking require manual power-on
- Test manually or in dedicated recovery scenario (not part of regular test suite)

## Script Safety Rules

### NEVER Do These in Scripts
- Call `ansible-playbook` directly (always use `build.py`)
- Hardcode IP addresses or hostnames
- Skip environment validation
- Assume script execution without proper delegation

### ALWAYS Do These in Scripts
- Delegate to `build.py` for Ansible operations
- Include proper error handling and exit codes
- Validate required environment variables
- Use descriptive help text and usage examples
- Follow the single entry point pattern

## Image Building Pattern

**NEVER** install packages during configure roles. All packages must be baked into images during build.

**Build vs Configure Separation:**
- `build-images.sh` — Packages + base configuration (runs once)
- Configure roles — Only host-specific topology (IPs, bridges, subnets)

**Image Storage:**
- Built images stored in `images/` directory (gitignored)
- Uploaded to Proxmox during provisioning
- Templates persist across test runs (`pveam list` cache hit)

## Development Workflow

1. **Initial Setup**: Run `./setup.sh` to bootstrap environment
2. **Development**: Use `./run.sh` for Ansible operations (delegates to `build.py`)
3. **Testing**: Use `molecule` commands for role testing
4. **Image Building**: Use `./build-images.sh` for custom image creation
5. **Cleanup**: Use `./cleanup.sh` for rollback operations (delegates to `build.py`)

## Error Handling

All scripts should:
- Exit with appropriate status codes
- Provide clear error messages
- Validate prerequisites before execution
- Delegate complex logic to `build.py`

**Error Handling Patterns:**
- Reference @.agents/skills/python-code-style for error handling conventions
- Use @.agents/skills/build-testing for comprehensive test coverage
- Apply @.agents/skills/proxmox-system-safety for system error patterns

This directory contains the shell script entry points following the single delegation pattern to `build.py` for all Ansible operations.
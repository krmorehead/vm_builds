---
name: testing-workflow
description: Test-first development, TDD workflow, molecule testing patterns, and diagnostic approaches for Ansible playbooks and infrastructure automation.
---

# Testing Workflow and TDD Patterns

Use when running molecule tests, implementing TDD workflow, diagnosing test failures, or establishing testing baselines for Ansible playbooks.

## Rules

**TDD and Test-First Development:**
1. ALWAYS reproduce production bugs on test machine first using `molecule test` or `molecule converge`
2. NEVER iterate on production when test machine is available  
3. ALWAYS write verify assertions before implementing features (TDD)
4. NEVER consider a fix complete until `molecule test` passes end-to-end

**Environment and Setup Validation:**
5. ALWAYS validate environment before ANY molecule commands: `set -a && source test.env && set +a`
6. ALWAYS test SSH and Ansible connectivity before running molecule: `ansible home -m ping`
7. ALWAYS run lint checks (`ansible-lint && yamllint .`) after ANY code changes

**Proactive Testing Triggers:**
8. Test IMMEDIATELY when creating new service roles or container types
9. Test IMMEDIATELY when adding Docker-in-LXC or container-specific patterns  
10. Test IMMEDIATELY when you see variable scoping issues or undefined variable errors
11. NEVER proceed with development when environment validation fails

**Cleanup and Safety:**
12. NEVER use blanket cleanup that destroys all resources - use explicit VMIDs
13. NEVER add graceful degradation for expected hardware (iGPU, WiFi, VT-d)

**Failure Diagnosis:**
14. ALWAYS check dmesg first when diagnosing test failures
15. NEVER assume ICMP working means TCP works - test with actual protocols

**Recent Testing Lessons:**
16. ALWAYS validate missing variables in role defaults - undefined variables in Jinja2 templates cause test failures
17. ALWAYS test environment setup BEFORE running molecule (dependency installation, template availability)
18. ALWAYS use default values for hardware-dependent variables (igpu_render_device) to prevent test failures
19. ALWAYS validate generated env file paths exist before relying on dynamic network configuration

## Patterns

TDD iteration pattern:

```yaml
# 1. Write/update verify assertion first
- name: Verify service is running
  ansible.builtin.assert:
    that: service_status.rc == 0

# 2. Run molecule test - assertion should fail
molecule test

# 3. Implement fix in role
# roles/service_configure/tasks/main.yml

# 4. Run molecule test - assertion should pass
molecule test
```

Converge vs test workflow:

```bash
# Day-to-day iteration (preserves baseline)
molecule converge && molecule verify

# Clean-state validation (CI, final proof)  
molecule test

# After molecule test, restore baseline
molecule converge
```

Diagnostic order:

```yaml
# When test fails, follow this order:
1. Read full error context (grep for FAILED, fatal:, UNREACHABLE)
2. Check dmesg on target (kernel-level errors)
3. Check interface/bridge state (ip addr, ip route)
4. Check firewall state (zone bindings, nftables)
5. Test actual protocols (not just ping)
6. Add permanent diagnostics
```

## Early Bug Detection Patterns

**Environment Issues (Catch Early):**
- "Could not resolve hostname none" → Environment variables not exported
- SSH connection failures → PRIMARY_HOST or HOME_API_TOKEN issues
- "ansible_date_time is undefined" → Facts not gathered, wrong host context

**Container/Docker Issues (Catch Early):**
- "pct: command not found" → Running on container instead of Proxmox host
- "proxmox_vmid is undefined" → Variable scoping issues, use `homeassistant_ct_id`
- Docker daemon access fails → Wrong execution context for `pct exec` commands

**File Deployment Issues (Catch Early):**
- Template deployment fails in containers → Use shell commands with `pct exec`
- "Recursive loop detected" → Self-referencing variables in defaults/main.yml
- YAML parsing errors → Avoid Jinja2 templates for container file writing

**Testing Strategy:**
- Run `molecule converge` after ANY container/Docker code changes
- Run `molecule verify` immediately when assertions fail
- Test with actual protocol (HTTP, Docker commands) not just connectivity

## Anti-patterns

NEVER explain what TDD is in testing workflow rules
NEVER use graceful skip for hardware expected on every host
NEVER just poll during long-running commands - use idle time productively
NEVER add failed_when: false on connection tests (let real errors fail immediately)
NEVER debug container issues without testing on the actual host first
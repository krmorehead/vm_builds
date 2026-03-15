---
name: testing-workflow
description: Test-first development, TDD workflow, molecule testing patterns, and diagnostic approaches for Ansible playbooks and infrastructure automation.
---

# Testing Workflow and TDD Patterns

Use when running molecule tests, implementing TDD workflow, diagnosing test failures, or establishing testing baselines for Ansible playbooks.

## Rules

1. ALWAYS reproduce production bugs on test machine first using `molecule test` or `molecule converge`
2. NEVER iterate on production when test machine is available
3. ALWAYS write verify assertions before implementing features (TDD)
4. NEVER consider a fix complete until `molecule test` passes end-to-end
5. NEVER use blanket cleanup that destroys all resources - use explicit VMIDs
6. NEVER add graceful degradation for expected hardware (iGPU, WiFi, VT-d)
7. ALWAYS check dmesg first when diagnosing test failures
8. NEVER assume ICMP working means TCP works - test with actual protocols

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

## Anti-patterns

NEVER explain what TDD is in testing workflow rules
NEVER use graceful skip for hardware expected on every host
NEVER just poll during long-running commands - use idle time productively
NEVER add failed_when: false on connection tests (let real errors fail immediately)
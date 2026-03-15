---
name: async-job-patterns
description: Detached script patterns, async operations, SSH connection handling, and verification approaches for long-running remote tasks.
---

# Async and Detached Job Patterns

Use when operations will sever SSH connections, handling network restarts, firewall changes, or long-running Ansible tasks that exceed SSH timeouts.

## Rules

1. ALWAYS use `ignore_unreachable: true` on detached script launch tasks
2. NEVER assume detached script succeeded just because launch task returned ok
3. ALWAYS follow detached scripts with pause that exceeds total runtime
4. NEVER restart services synchronously if restart changes firewall rules or SSH config
5. NEVER use `async` with `poll: 0` unless separate verification step exists
6. ALWAYS verify expected outcome after detached operations (don't trust script completion)
7. NEVER use synchronous firewall restart over SSH when WAN zone rules have changed
8. ALWAYS use detached scripts for operations that change network topology

## Patterns

Detached script pattern:

```yaml
- name: Schedule restart via detached script
  ansible.builtin.raw: >-
    printf '#!/bin/sh\nsleep 1\n<commands>\nrm -f /tmp/_script.sh\n'
    > /tmp/_restart_net.sh &&
    chmod +x /tmp/_restart_net.sh &&
    start-stop-daemon -S -b -x /tmp/_restart_net.sh
  ignore_unreachable: true
  changed_when: true

- name: Wait for services to stabilize
  ansible.builtin.pause:
    seconds: 30

- name: Verify expected outcome
  ansible.builtin.wait_for:
    host: "{{ target_ip }}"
    port: 22
    timeout: 120
  delegate_to: "{{ proxmox_host }}"
```

Long-running Ansible tasks:

```yaml
# Option 1: Use async with poll
- name: Long running command
  ansible.builtin.command: /path/to/long/command
  async: 300
  poll: 10

# Option 2: Increase SSH timeout
- name: Configure with longer timeout
  ansible.builtin.shell: |
    set -o pipefail
    long_running_operation
  environment:
    ANSIBLE_TIMEOUT: 60
  ansible_ssh_common_args: "-o ServerAliveInterval=15 -o ServerAliveCountMax=4"
```

## Anti-patterns

NEVER explain what async operations are in async patterns
NEVER use nohup for detachment (unreliable on BusyBox)
NEVER assume launch task success means script completed successfully
NEVER omit verification after detached operations
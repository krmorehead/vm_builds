---
name: molecule-verify
description: Molecule verify assertion patterns. completeness requirements, batch operations, multi-node patterns.
---

# Molecule Verify

## Rules

1. Every configured feature MUST have assertion in verify.yml.
2. Assert IP non-empty and no collision BEFORE functional tests.
3. SINGLE `pct config` call per container, then assert on cached output.
4. Batch independent `pct exec` calls with key=value output.
5. NEVER use `failed_when: false` on client sends — let errors surface.
6. Per-feature verify MUST reconstruct dynamic groups (separate invocation).

## Completeness requirements

**Per VM:** state, NIC topology, network config, services running, deploy tracking.

**Per LXC:** container state, auto-start, baked config, config validation, service active, listener bound, functional test, restart resilience, resource usage, deploy stamp.

## IP validation

```yaml
- name: Read container config
  ansible.builtin.command:
    cmd: pct config {{ ct_id }}
  register: _ct_cfg
  changed_when: false

- name: Extract IP
  ansible.builtin.set_fact:
    _ct_ip: >-
      {{ _ct_cfg.stdout | regex_search('ip=([^/,]+)', '\1') | first }}

- name: Assert IP valid
  ansible.builtin.assert:
    that:
      - _ct_ip is defined
      - _ct_ip | length > 0
```

## Batch pct config

```yaml
ansible.builtin.command:
  cmd: pct config {{ ct_id }}
register: _ct_cfg

- name: Assert onboot
  ansible.builtin.assert:
    that: "'onboot: 1' in _ct_cfg.stdout"
```

## Batch pct exec

```yaml
ansible.builtin.shell:
  cmd: >-
    pct exec {{ ct_id }} -- /bin/sh -c '
    echo "IFACE=$(ip link show wg0 >/dev/null 2>&1 && echo ok || echo missing)";
    echo "SVC=$(systemctl is-enabled wg-quick@wg0 2>/dev/null || echo unknown)"
    '
  register: health
  changed_when: false
```

## Client sends

```yaml
# Let errors surface immediately
ansible.builtin.shell:
  cmd: logger --tcp {{ ct_ip }} 514 --server.host test

# Reserve failed_when: false for commands whose rc is checked by assertion
```

## Service management

Prefer `ansible.builtin.systemd` over `command: systemctl`:

```yaml
ansible.builtin.systemd:
  name: rsyslog
  state: restarted
```

## Multi-node patterns

For 4-node tests, avoid IP recomputation. Read config once:

```yaml
ansible.builtin.command:
  cmd: pct config {{ ct_id }}
register: _ct_cfg

ansible.builtin.set_fact:
  _ct_ip: >-
    {{ _ct_cfg.stdout | regex_search('ip=([^/,]+)', '\1') | first }}
```

## Fact scoping

Facts from converge NOT available in verify. For roles with facts, re-include:

```yaml
- name: Verify proxmox_igpu role
  hosts: proxmox
  gather_facts: true
  roles:
    - proxmox_igpu  # re-run to populate facts
```

## No-leak testing

Verify remote messages don't pollute local logs:

```yaml
- name: Send from remote
  ansible.builtin.shell:
    cmd: logger --tcp {{ remote_ip }} 514 "TEST-LEAK-{{ ansible_date_time.epoch }}"

- name: Verify no leak
  ansible.builtin.shell:
    cmd: grep "TEST-LEAK-{{ ansible_date_time.epoch }}" /var/log/syslog
  register: _leak
  failed_when: _leak.rc == 0
```

## Common failures

- 0 assertions ran → dynamic group empty (add reconstruction)
- Rollback targets 0 hosts → empty group in cleanup
- `igpu_available not defined` → re-include role in verify
- Wrong IP for WAN hosts → use `pct config` extraction

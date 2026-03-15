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

**Per VM:**

| Category | Example assertions |
|---|---|
| VM state | Running, correct VMID |
| VM auto-start | `onboot=1`, `startup order=N` |
| NIC topology | `net0` on correct bridge (WAN vs LAN) |
| Network config | WAN has IP, LAN subnet doesn't collide with WAN |
| Services | DHCP configured, firewall running |
| Optional features | MAC cloning (when `WAN_MAC` set), mesh (when WiFi present) |
| Backup | Manifest exists, has required fields, archive on disk |
| Deploy tracking | `vm_builds.fact` exists, contains expected plays |
| State files | `.state/addresses.json` exists, contains host + IPs |

**Per LXC:**

| Category | Example assertions |
|---|---|
| Container state | Running, correct VMID |
| Auto-start | `onboot=1`, `startup order=N` |
| Baked config | All baked config files exist inside container |
| Config validation | Service config passes validation (`rsyslogd -N1`, `nginx -t`) |
| Service state | `systemctl is-active <service>` |
| Network listener | Port is listening (`ss -tlnp`) |
| Functional test | Send data, verify received/processed |
| Multi-source test | Multiple senders/tags produce correctly separated output |
| No-leak test | Remote data stays in remote logs, not local syslog |
| Restart resilience | Stop/start service, verify listener and reception recover |
| Resource usage | Memory within allocation (`free -m` < allocated) |
| Logrotate | Config exists and passes `logrotate --debug` validation |
| Deploy stamp | `stamp.plays` contains the service entry |

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

## Verify task conventions

1. **Fail-fast IP validation.** Assert IP non-empty and no collision with host IP BEFORE functional tests. "IP is empty" is clear; "log not found" 10 tasks later is not.

2. **No `failed_when: false` on client sends.** Let `logger --tcp`, `dig`, `curl` fail immediately on connection error. "Connection refused" is far more useful than downstream "expected output not found."

3. **`set -o pipefail` only on pipelines.** NEVER add pipefail to single-command tasks. It adds noise and obscures the convention that pipefail signals a pipeline.

4. **Use `ansible.builtin.systemd` over `command: systemctl`** for restarts. Use `command` only for status checks and validation.

## Jinja regex_search in assert

`regex_search` without a capture group returns a STRING match (not boolean). In `assert: that:` blocks, ALWAYS append `is not none`:

```yaml
# BAD — returns string, assert sees "Conditional was derived from type str"
- _output | regex_search('RADIOS=phy')

# GOOD — explicit boolean
- _output | regex_search('RADIOS=phy') is not none
```

## Common failures

- 0 assertions ran → dynamic group empty (add reconstruction)
- Rollback targets 0 hosts → empty group in cleanup
- `igpu_available not defined` → re-include role in verify
- Wrong IP for WAN hosts → use `pct config` extraction
- Per-feature scenario missing `router_nodes` group → LAN/WAN detection takes wrong path

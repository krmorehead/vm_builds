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
7. Use the fleet readiness API as the **primary** path for container liveness;
   `pct exec` is the **fallback** when the API is unavailable.
8. New service verify blocks MUST follow the `_fleet_api_ready` dual-path
   pattern. See the `manager-api-pattern` skill for details.

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

## grep -c with find -exec in containers

When using `find ... -exec grep -c 'pattern' {} +` inside a container (via `pct exec`), `grep -c` outputs `filename:count` when multiple files match, but just `count` for a single file. Jinja2's `| int` filter returns 0 for `filename:count` strings, silently breaking assertions.

ALWAYS use `grep -rch` with `awk` sum instead:

```yaml
# BAD — breaks when multiple files match
pct exec {{ ct_id }} -- find /var/log/remote/ -name '*.log' -exec grep -c 'pattern' {} + 2>/dev/null | head -1

# GOOD — numeric output regardless of file count
pct exec {{ ct_id }} -- grep -rch 'pattern' /var/log/remote/ 2>/dev/null | awk '{sum+=$1} END {print sum+0}'
```

Previous bug: rsyslog separation test used `find -exec grep -c`. On mesh1, multiple log files existed under different hostname directories. `grep -c` output `/var/log/remote/home/e2e_daemon_a.log:11`. Jinja2 `| int` converted this to 0, failing the assertion.

## Host-to-container TCP fallback pattern

When verifying TCP-based services in containers, host-to-container TCP may fail on specific hosts (e.g., mesh1) even when ICMP ping succeeds and br_netfilter is disabled. Add a fallback path:

```yaml
- name: Send log via host TCP
  ansible.builtin.shell:
    cmd: logger --tcp -n {{ container_ip }} -P 514 "test message"
  register: _send
  failed_when: false
  retries: 5
  delay: 5
  until: _send.rc == 0

- name: Diagnose TCP failure
  ansible.builtin.shell:
    cmd: |
      echo "=== nft ruleset ==="
      nft list ruleset 2>/dev/null | head -40
      echo "=== raw TCP ==="
      timeout 3 bash -c "echo test > /dev/tcp/{{ container_ip }}/514" 2>&1 && echo "ok" || echo "fail"
    executable: /bin/bash
  when: _send.rc != 0

- name: Fallback via pct exec
  ansible.builtin.shell:
    cmd: >-
      pct exec {{ ct_id }} --
      logger --tcp -n 127.0.0.1 -P 514 "test message"
  when: _send.rc != 0
```

Previous bug: mesh1 host consistently failed `logger --tcp` to its rsyslog container (10.10.10.14) despite ping succeeding and br_netfilter disabled. Root cause unresolved (likely nftables or conntrack). Fallback via `pct exec` with localhost TCP verifies rsyslog works without host-level network dependency.

## Windows VM verification via QEMU Guest Agent

For Windows VMs without reliable SSH (no sshpass, LAN-only IPs), use `qm guest exec` instead of SSH connectivity tests:

```yaml
- name: Test in-VM command via Guest Agent
  ansible.builtin.command:
    cmd: qm guest exec {{ vm_id }} --timeout 15 -- cmd /c "echo vm-ok"
  register: _exec_test
  changed_when: false
  failed_when: false

- name: Assert in-VM command works
  ansible.builtin.assert:
    that: "'vm-ok' in _exec_test.stdout"
```

For per-host VMID scenarios, compute the effective VMID at the start of verify:

```yaml
- name: Set per-host VM ID
  ansible.builtin.set_fact:
    gaming_vm_id: "{{ (gaming_vm_id | int) + groups['gaming_nodes'].index(inventory_hostname) }}"
```

Previous bug: verify used `sshpass` for SSH testing. `sshpass` wasn't installed on mesh1. Switched to Guest Agent which bypasses network entirely.

## DNS service verification (Pi-hole FTL)

ALWAYS check BOTH TCP and UDP listeners for DNS services. `dig` defaults to UDP. `ss -tlnp` only checks TCP — a service can bind TCP:53 but miss UDP:53 due to startup race or partial initialization.

```yaml
- name: Wait for DNS on TCP+UDP port 53
  ansible.builtin.shell:
    cmd: >-
      pct exec {{ ct_id }} -- /bin/sh -c '
      tcp=$(ss -tlnp 2>/dev/null | grep -c ":53 ");
      udp=$(ss -ulnp 2>/dev/null | grep -c ":53 ");
      [ "$tcp" -gt 0 ] && [ "$udp" -gt 0 ] && echo BOTH || echo MISSING
      '
  retries: 6
  delay: 5
  until: "'BOTH' in _ftl_listen.stdout"

- name: Restart FTL if listeners are incomplete
  ansible.builtin.command:
    cmd: >-
      pct exec {{ ct_id }} -- /bin/sh -c
      'systemctl restart pihole-FTL && sleep 3'
  when: "'BOTH' not in _ftl_listen.stdout"
```

ALWAYS wrap DNS resolution tests in `block/rescue`. The `retries/until` pattern makes a task `fatal` when all retries expire — any diagnostic tasks after it never execute. Use `block:` for the test and `rescue:` for diagnostics + recovery (FTL restart + retry).

ALWAYS add a network connectivity pre-check before DNS tests: verify the container can ping its gateway and an external IP. This distinguishes "FTL is broken" from "container has no network."

Previous bug: Pi-hole DNS verify timed out in E2E tests. `ss -tlnp | grep :53` passed (TCP listener present) but `dig @127.0.0.1` timed out (UDP listener missing). FTL had TCP bound but UDP wasn't ready. Adding UDP check + automatic FTL restart fixed the intermittent failure.

Previous bug: DNS test used `retries: 5` with `until:`. After all retries expired, the task failed fatally. Diagnostic tasks defined below it never executed. Switching to `block/rescue` ensures diagnostics always fire, and the rescue block restarts FTL + retries, recovering from transient upstream timeout.

## Avoid regex_search on pct exec output

NEVER use `regex_search` with capture groups to parse `pct exec` output in `set_fact` or `assert` blocks. `pct exec` output passes through multiple shell layers (SSH → Proxmox host bash → lxc-attach → container shell) and may contain unexpected formatting, ANSI escapes, or extra whitespace that breaks regex matching.

**Safe pattern:** Use string-contains checks with `in` operator:

```yaml
# BAD — crashes with 'NoneType' if regex returns None
- set_fact:
    val: "{{ output | regex_search('KEY=(\\S+)', '\\1') | first }}"

# GOOD — simple, robust, no crash risk
- assert:
    that: "'KEY=expected' in output"

# GOOD — for negation checks
- assert:
    that: "'KEY=0' not in output"
```

Previous bug: Batched rsyslog/gaming verify tasks used `regex_search('MATCH=(\\S+)', '\\1') | first` to extract values from `pct exec` output. The regex returned `None` on all 4 nodes, crashing `set_fact` with `'NoneType' object has no attribute 'group'`. Replaced with string-contains checks.

## Display-exclusive container assertions

When containers share a display device (iGPU render node), a hookscript may stop
one container when another starts (e.g., Kiosk stopped when Desktop VM starts).
Assert container config separately from running state:

```yaml
- name: Assert container config is correct
  ansible.builtin.assert:
    that:
      - "'onboot: 1' in _cfg.stdout"
      - _cfg.stdout is regex('startup:.*order=6')

- name: Check if competing VM is running
  ansible.builtin.command:
    cmd: qm status {{ desktop_vm_id }}
  register: _desktop_status
  changed_when: false
  failed_when: false

- name: Assert running OR stopped by hookscript
  ansible.builtin.assert:
    that: >-
      'running' in _cfg.stdout or
      ('stopped' in _cfg.stdout and 'running' in (_desktop_status.stdout | default('')))
```

Previous bug: Kodi and Kiosk assertions required 'running' state, but the
display-exclusive hookscript correctly stopped them when the Desktop VM started.
Fix: accept 'stopped' when the competing VM is running.

## Multi-instance container IP from API

When the same container type deploys to multiple hosts (e.g., rsyslog on 4 nodes),
the fleet API returns a single IP (last check-in) for all instances sharing the
same `container_id`. ALWAYS use `pct config` for per-host container IPs in verify:

```yaml
- name: Extract per-host container IP
  ansible.builtin.set_fact:
    _ct_ip: "{{ _ct_cfg.stdout | regex_search('ip=([^/,]+)', '\\1') | first }}"
```

Previous bug: rsyslog verify used API-returned IP for cross-service log tests.
All 4 hosts got the same IP (10.10.10.14 — Home Assistant's IP), causing logger
TCP to fail on 3 of 4 hosts. Fix: always use `pct config` for IP extraction.

## systemd service state from callhome

`callhome.py` uses `systemctl list-units` which reports state as `running` (not
`active` from `systemctl is-active`). Assertions against API `systemd_services`
data MUST accept both values:

```yaml
- _api.json.systemd_services.get('service', '') in ['active', 'running']
```

## Common failures

- 0 assertions ran → dynamic group empty (add reconstruction)
- Rollback targets 0 hosts → empty group in cleanup
- `igpu_available not defined` → re-include role in verify
- Wrong IP for WAN hosts → use `pct config` extraction
- Per-feature scenario missing `router_nodes` group → LAN/WAN detection takes wrong path
- Register result used as string → ALWAYS use `_var.stdout | trim`, not `_var` directly. Register results are dicts, not strings. Previous bug: `_jellyfin_verify_ip.split('.')` failed because `_jellyfin_verify_ip` was a register result (dict), not the stdout string.
- `set -o pipefail` on non-pipeline commands → use `ansible.builtin.command` for single commands without pipes. Pipefail on non-pipeline commands adds noise and obscures the convention that pipefail signals a pipeline.
- `config_files[path].keys()` returns a method, not a list → use `config_files[path]['keys']` to access the stored key list from callhome extensions.

---
name: molecule-testing
description: Run and validate Ansible tests. Molecule commands, TDD workflow, layered scenarios, performance optimization.
---

# Molecule Testing

## Rules

1. Always run `molecule test` after modifications. TDD: write verify, test fails, fix, test passes.
2. Keep baseline running between tests. 4-minute baseline costs on every run.
3. Molecule platform config MUST include all flavor groups from inventory/hosts.yml.
4. NEVER delete templates in cleanup. Template deletion forces ~820MB re-upload.
5. NEVER use graceful skips for required hardware (iGPU, IOMMU). Silent skips mask problems.

## Commands

```bash
source .venv/bin/activate
set -a; source test.env; set +a

molecule test          # full pipeline
molecule converge      # run playbook (preserves baseline)
molecule verify        # run assertions
molecule cleanup       # reset host
```

## Baseline workflow

```bash
molecule converge                  # build/update baseline
molecule verify                    # run assertions
molecule converge -s wireguard-lxc # run layered scenario
molecule verify -s wireguard-lxc
```

Pre-commit: `molecule test && molecule converge`

## Pipeline sequence

`molecule test` runs: dependency → cleanup → syntax → converge → verify → cleanup

No lint phase — run `ansible-lint && yamllint .` separately.

## Scenario types

**Layered scenarios** (e.g., `openwrt-security`):
- Assume baseline exists
- Converge only tagged plays
- Verify only feature assertions
- Cleanup only feature artifacts

**Standalone scenarios** (e.g., `proxmox-igpu`):
- Test single role
- No baseline dependency
- Cleanup restores host state

## Pre-test checklist

```bash
set -a; source test.env; set +a
ssh root@$PRIMARY_HOST hostname
./build-images.sh
```

## Platform groups

```yaml
platforms:
  - name: home
    groups: [proxmox, router_nodes, vpn_nodes, wifi_nodes]
  - name: mesh1
    groups: [proxmox, lan_hosts, vpn_nodes, wifi_nodes]
```

All 4 hosts in default scenario. Without this, plays skip hosts.

## Group reconstruction

Per-feature scenarios target dynamic groups. State does NOT persist across phases. MUST reconstruct at start of each phase:

```yaml
- name: Reconstruct openwrt group
  hosts: router_nodes
  tasks:
    - include_tasks: ../../tasks/reconstruct_openwrt_group.yml
```

Task file detects OpenWrt LAN IP, auth method, builds SSH args, registers host.

## Path resolution

Use `role_path` for project-relative paths:

```yaml
src: "{{ role_path }}/../../images/{{ lxc_ct_template }}"
```

## Performance optimization

- Set `cache_valid_time: 86400` for apt tasks
- MINIMIZE `pct_remote` tasks. Each opens new SSH connection
- Consolidate `pct config` reads — one call per container

## Hard-fail requirements

- **iGPU**: REQUIRED. Supports Intel (i915) and AMD (amdgpu)
- **WiFi + IOMMU**: REQUIRED for passthrough
- **NIC count**: OK to handle dynamically

## Molecule env vars

Molecule `provisioner.env` uses `${VAR}` syntax. NEVER use `${VAR:-default}`.

Required: add to `provisioner.env`. Optional: do NOT add (role defaults use `lookup('env', 'VAR')`).

## Raw heredoc pitfalls

`ansible.builtin.raw:` heredocs fail on Jinja2-like content:
- `${var:-default}` — misinterpreted
- `|| true` — can confuse parser
- `[:space:]` — colons conflict with YAML

Run `ansible-playbook --syntax-check playbooks/site.yml` after modifying `raw:`.

## Diagnosing failures

1. Terminal: `FAILED`, `fatal:`, `UNREACHABLE`
2. Kernel: `dmesg | grep -iE 'error|segfault|duplicate'`
3. Interfaces: `ip addr`, `ip route`
4. Firewall: zone bindings, nftables
5. Protocol: test actual protocol (TCP/HTTP), not ICMP

## Shell safety

```yaml
ansible.builtin.shell:
  cmd: |
    set -o pipefail
    command1 | command2
  executable: /bin/bash
```

Exception: `ansible.builtin.raw` and BusyBox do NOT support pipefail.

## Deprecated patterns

Use `delegate_to: localhost` instead of `local_action`. Use FQCNs: `ansible.builtin.command`.

## Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UNREACHABLE` during converge | SSH broken or host down | Check `PRIMARY_HOST`, verify SSH |
| `community.proxmox` not found | Collections missing | `ansible-galaxy collection install -r requirements.yml` |
| Bridge numbers keep incrementing | Cleanup didn't remove bridges | `./cleanup.sh clean test.env` |
| WiFi radios=0 after converge | PCI passthrough not cleaned up | Ensure cleanup unbinds vfio-pci, reloads modules, rescans PCI |
| `Timeout waiting for SSH` | Network restart dropped connection | Verify SSH args include `ConnectTimeout=10`, `ServerAliveInterval=15` |
| `opkg update` fails HTTPS | HTTPS not supported | Ensure `sed -i 's\|https://\|http://\|g'` runs before `opkg update` |
| `opkg update` EPERM | Firewall zones stale after network restart | Restart firewall before outbound connections |
| MAC stored without colons in UCI | BusyBox `tr -d '[:space:]'` deletes `:` | Use `tr -d ' \t\n\r'` |
| GUI reachability fails on OpenWrt | BusyBox `nc` no `-w` flag | Use `(echo QUIT \| nc HOST PORT) </dev/null` |
| VM reachable by IPv6 but not IPv4 | Stale LAN-subnet IP on another bridge | Remove conflicting IPs from non-LAN bridges |
| `ifreload -a` no DHCP client | Separate `inet dhcp` file conflicts | Modify bridge stanza in-place |
| Route filter hides default route | `ip route show default dev eth0` misses aliases | Use `ip route show default` without `dev` filter |
| `igpu_available not defined` in verify | Facts from converge not in verify | Re-include role in verify.yml |
| `Could not find or access` template | Relative path from scenario dir | Use `role_path` for paths |
| `ModuleNotFoundError: paramiko` | Missing Python dep for `pct_remote` | Add `paramiko` to `requirements.txt` |
| `apt-get update` hangs on Proxmox | Enterprise repos unreachable | Rename to `.disabled`, add no-subscription repo |
| `lsmod \| grep -q` returns rc=141 | SIGPIPE from `grep -q` with pipefail | Use `grep -c` instead |
| Per-feature verify passes with 0 assertions | Dynamic group empty | Add group reconstruction play |
| Rollback targets 0 hosts | Dynamic group empty in cleanup | Add reconstruction play in cleanup.yml |
| SSH auth fails after security rollback | Didn't clear root password | Rollback MUST clear `/etc/shadow` root hash |
| `uci: Invalid argument` on mesh radio | PHY namespace-moved after boot | Run `wifi config` before `uci set wireless.radio*` |

## Permanent diagnostics rules

1. ALWAYS use `changed_when: false` and `failed_when: false` on diagnostic tasks
2. Register output and use `debug: var:` to display in logs
3. Include kernel-level checks (`dmesg` errors) at key milestones
4. Include actual protocol tests (TCP/HTTP), not just ICMP ping
5. Generalize ad-hoc debug tasks into permanent diagnostics before closing issues

## Lint configuration

- `ansible-lint`: `.ansible-lint` — production profile, skips `command-instead-of-module` for Proxmox shell tasks
- `yamllint`: `.yamllint.yml` — 160-char lines, relaxed comment spacing
- Run manually: `ansible-lint && yamllint .`

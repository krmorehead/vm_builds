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

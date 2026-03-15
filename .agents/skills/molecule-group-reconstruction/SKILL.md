---
name: molecule-group-reconstruction
description: Molecule dynamic group reconstruction for per-feature scenarios. OpenWrt group detection, SSH auth method detection, persistent state patterns.
---

# Molecule Group Reconstruction

## Reconstruction Requirement

Per-feature scenarios target dynamic groups. State does NOT persist across Molecule phases (converge, verify, cleanup are separate Ansible invocations). Per-feature converge playbooks MUST populate the dynamic group first since the baseline's `add_host` state doesn't persist.

The reconstruction logic MUST detect the current SSH auth method. After security hardening (M1), SSH uses key auth. Before M1 (or after rollback), SSH uses password auth with empty password.

## Reusable Task File Pattern

Extract the group reconstruction logic into `tasks/reconstruct_openwrt_group.yml` at the project root. This file is consumed by per-feature converge, verify, and cleanup.

The task file MUST:
1. Detect the OpenWrt LAN IP from Proxmox bridge state
2. Detect whether key auth or password auth is active
3. Build the correct `ansible_ssh_common_args` for the detected auth method
4. Register the host via `add_host` with appropriate args

## Auth Detection Logic

```yaml
- name: Detect SSH auth method
  ansible.builtin.set_fact:
    _use_key_auth: >-
      {{ (lookup('env', 'OPENWRT_SSH_PRIVATE_KEY') | length > 0) and
         (ansible_local.vm_builds.plays.openwrt_security is defined) }}

- name: Add OpenWrt to dynamic group
  ansible.builtin.add_host:
    name: openwrt-router
    groups: openwrt
    ansible_host: "{{ _openwrt_ip }}"
    ansible_user: root
    ansible_ssh_common_args: >-
      -o ProxyJump=root@{{ ansible_host }}
      -o StrictHostKeyChecking=no
      -o UserKnownHostsFile=/dev/null
      -o ConnectTimeout=10
      -o ServerAliveInterval=15
      -o ServerAliveCountMax=4
      {% if _use_key_auth %}
      -i {{ lookup('env', 'OPENWRT_SSH_PRIVATE_KEY') }}
      {% endif %}
```

## Per-Feature Converge Pattern

```yaml
# molecule/openwrt-security/converge.yml
---
- name: Reconstruct openwrt dynamic group from baseline
  hosts: router_nodes
  gather_facts: true
  tasks:
    - name: Verify VM 100 is running
      ansible.builtin.command:
        cmd: qm status 100
      register: _vm_status
      changed_when: false
      failed_when: "'running' not in _vm_status.stdout"

    - name: Include reusable group reconstruction
      ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

- name: Apply security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Include security hardening tasks
      ansible.builtin.include_role:
        name: openwrt_configure
        tasks_from: security.yml
```

## Per-Feature Verify Pattern

Per-feature verify playbooks also run as a separate `ansible-playbook` invocation, so the dynamic group is empty. The verify MUST reconstruct the group before running assertions:

```yaml
# molecule/openwrt-security/verify.yml
---
- name: Reconstruct openwrt dynamic group
  hosts: router_nodes
  gather_facts: true
  tasks:
    - name: Include reusable group reconstruction
      ansible.builtin.include_tasks: ../../tasks/reconstruct_openwrt_group.yml

- name: Verify security hardening
  hosts: openwrt
  gather_facts: false
  tasks:
    - name: Check banIP is installed
      ansible.builtin.raw: opkg list-installed | grep banip
      register: _banip
      changed_when: false
    - name: Assert banIP installed
      ansible.builtin.assert:
        that: _banip.rc == 0
```

Previous bug: per-feature verify assertions targeting the `openwrt` group ran with zero hosts, silently passing all assertions. The group was empty because `add_host` from converge doesn't persist into verify.

## Fact Scoping

Facts set during `converge` are NOT available in `verify`. Molecule runs converge and verify as separate Ansible invocations with independent fact caches.

For read-only roles that export facts, re-include the role in verify:

```yaml
# molecule/proxmox-igpu/verify.yml
---
- name: Verify proxmox_igpu role
  hosts: proxmox
  gather_facts: true
  roles:
    - proxmox_igpu    # re-run to populate facts (idempotent)
  tasks:
    - name: Assert igpu_available fact is set
      ansible.builtin.assert:
        that: igpu_available is defined
```

Previous bug: `proxmox-igpu` verify failed with "igpu_available is not defined" because the fact was only set during converge. Re-including the role fixed it.
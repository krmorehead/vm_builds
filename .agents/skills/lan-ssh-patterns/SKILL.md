---
name: lan-ssh-patterns
description: SSH ProxyCommand for LAN hosts behind OpenWrt router. Connection hardening, credential safety, keepalives.
---

# LAN SSH Patterns

## Architecture

```
Controller → Primary host (ProxyCommand SSH) → LAN host
```

Dependency: OpenWrt baseline must be running before LAN hosts reachable.

## Rules

1. NEVER assume LAN hosts reachable without OpenWrt baseline running.
2. NEVER remove SSH authorized_keys or API tokens in cleanup — operator prerequisites.
3. NEVER manage SSH keys/passwords in playbooks — operator sets up manually.
4. ALWAYS use ProxyCommand (not ProxyJump) so keepalives apply to BOTH the jump and target connections.
5. ALWAYS add ServerAliveInterval=15 and ServerAliveCountMax=4 to BOTH the ProxyCommand inner SSH and the outer SSH args.
6. ALWAYS export env vars before Molecule: `set -a; source test.env; set +a`
7. ALWAYS add `meta: reset_connection` + `wait_for_connection` pre_tasks to the first Phase 2 play targeting lan_hosts.

## ProxyCommand configuration

```yaml
# inventory/group_vars/lan_hosts.yml
ansible_ssh_common_args: >-
  -o ProxyCommand="ssh
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -W %h:%p root@{{ lookup('env', 'PRIMARY_HOST') }}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=30
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
```

ProxyCommand (not ProxyJump) is required so we can pass `-o ServerAliveInterval`
to the jump connection. With ProxyJump, only the outer connection gets keepalives
from the command line — the jump connection inherits from ssh_config only.

## ansible.cfg hardening

```ini
[ssh_connection]
ssh_args = -o ControlMaster=auto -o ControlPersist=300s -o ServerAliveInterval=15 -o ServerAliveCountMax=4
```

- ControlPersist=300s (not 60s): keeps ControlMaster alive during long plays.
- ServerAliveInterval on ssh_args: protects ALL connections including ControlMasters.

## Connection priming for Phase 2 plays

```yaml
pre_tasks:
  - name: Clear stale SSH state to LAN hosts
    ansible.builtin.meta: reset_connection

  - name: Wait for SSH via ProxyCommand to stabilize
    ansible.builtin.wait_for_connection:
      timeout: 120
      delay: 5
```

Previous bug: mesh1 became UNREACHABLE mid-play during `proxmox_igpu` (54 tasks
completed, then "Data could not be sent"). Root cause: the controller→home
ControlMaster that carried the ProxyJump tunnel died silently because:
(a) ControlPersist=60s was too short, (b) no keepalives on the jump connection,
(c) no connection recovery at play boundaries.

## Testing connectivity

```bash
# Verify OpenWrt running
qm status 100

# Verify LAN bridge IP
ip -4 addr show | grep 10.10.10

# Ping LAN host from primary
ping -c1 10.10.10.210

# SSH via ProxyJump
ssh -o ProxyJump=root@192.168.86.201 root@10.10.10.210 hostname
```

## SSH tunnel for browser access

```bash
ssh -L 8007:<lan-host-ip>:8006 root@192.168.86.201
# Browse https://localhost:8007
```

## Order in site.yml

Plays targeting lan_hosts MUST come after OpenWrt configure:

1. Phase 1: backup + infra + OpenWrt on proxmox:!lan_hosts
2. Phase 2: bootstrap + backup + infra on lan_hosts
3. Phase 3: services on flavor groups spanning both

## Cleanup safety

NEVER remove credentials in cleanup:

```yaml
# Only remove playbook artifacts
- name: Remove ansible-managed files
  file: path={{ item }} state=absent
  loop:
    - /etc/network/interfaces.d/ansible-bridges.conf
    - /etc/ansible/facts.d/vm_builds.fact
```

## Troubleshooting

**LAN host unreachable:**
- Verify OpenWrt running: `qm status 100`
- Verify LAN bridge: `ip -4 addr show | grep 10.10.10`
- Check DHCP: `ssh root@10.10.10.1 'cat /tmp/dhcp.leases'`

**SSH permission denied:**
- Test from primary: `ssh -o BatchMode=yes root@10.10.10.210 hostname`
- Re-push keys via Proxmox GUI shell or SSH tunnel

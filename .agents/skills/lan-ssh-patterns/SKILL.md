---
name: lan-ssh-patterns
description: SSH ProxyJump for LAN hosts behind OpenWrt router. Baseline dependency, credential safety, keepalives.
---

# LAN SSH Patterns

## Architecture

```
Controller → Primary host → OpenWrt VM → LAN host
```

Dependency: OpenWrt baseline must be running before LAN hosts reachable.

## Rules

1. NEVER assume LAN hosts reachable without OpenWrt baseline running.
2. NEVER remove SSH authorized_keys or API tokens in cleanup — operator prerequisites.
3. NEVER manage SSH keys/passwords in playbooks — operator sets up manually.
4. ALWAYS add ServerAliveInterval=15 and ServerAliveCountMax=4 for ProxyJump.
5. ALWAYS export env vars before Molecule: `set -a; source test.env; set +a`

## ProxyJump configuration

```yaml
# inventory/group_vars/lan_hosts.yml
ansible_ssh_common_args: >-
  -o ProxyJump=root@{{ lookup('env', 'PRIMARY_HOST') }}
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=10
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4
```

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

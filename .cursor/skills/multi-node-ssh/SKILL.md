---
name: multi-node-ssh
description: SSH ProxyJump patterns for managing Proxmox nodes behind the OpenWrt router. Use when adding LAN hosts, debugging satellite connectivity, writing Molecule scenarios for LAN nodes, or working with tasks/bootstrap_lan_host.yml.
---

# Multi-Node SSH Patterns

LAN hosts sit behind the OpenWrt router on 10.10.10.0/24 and are NOT directly
reachable from the controller. All SSH goes through the primary Proxmox host
via ProxyJump. LAN hosts are ONLY reachable when the OpenWrt router VM is
running on the primary host.

## Rules

- NEVER assume LAN hosts are reachable without the OpenWrt baseline running.
  Plays targeting `lan_hosts` MUST come after the OpenWrt configure play in
  `site.yml`. Molecule scenarios for LAN hosts MUST verify the baseline first.
- NEVER remove SSH authorized_keys or API tokens in cleanup. These are
  operator prerequisites, not playbook artifacts.
- NEVER manage SSH keys or passwords in playbooks. The operator sets up key
  auth manually via the Proxmox GUI shell or `ssh-copy-id` through the tunnel.
- ALWAYS add `ServerAliveInterval=15` and `ServerAliveCountMax=4` to SSH args
  for ProxyJump connections. Without keepalives, the connection drops during
  long sequences of local Ansible tasks that don't send traffic.
- ALWAYS export env vars before running Molecule:
  `set -a; source test.env; set +a; molecule test -s mesh1-infra`

## Architecture

```
Controller (laptop, 192.168.86.0/24)
  └──SSH──► Primary host (home, 192.168.86.201)
               ├──SSH──► OpenWrt VM (10.10.10.1, manages LAN)
               └──ProxyJump──► LAN host (mesh1, 10.10.10.210)
```

Dependency chain: controller → home → OpenWrt VM → LAN exists → mesh1 reachable.

## Patterns

### Adding a new LAN node (full checklist)

1. **Physical setup**: connect the node to the LAN switch behind OpenWrt
2. **Discover the node** from the primary host:

```bash
# Check OpenWrt DHCP leases
ssh root@10.10.10.1 'cat /tmp/dhcp.leases'
# Or scan the subnet
for i in $(seq 200 220); do ping -c1 -W1 10.10.10.$i &>/dev/null && echo 10.10.10.$i; done
```

3. **Set up SSH key auth** (one-time, requires console or Proxmox GUI):

```bash
# Option A: SSH tunnel + GUI shell
ssh -L 8007:<node-ip>:8006 root@192.168.86.201
# Browse https://localhost:8007, open Shell, paste public keys

# Option B: ssh-copy-id through ProxyJump (requires password)
ssh-copy-id -o ProxyJump=root@192.168.86.201 root@<node-ip>
```

Both the controller's AND the primary host's keys must be authorized.

4. **Add env var**: `NODENAME_API_TOKEN=` to `.env`/`test.env`
5. **Add to inventory**: `inventory/hosts.yml` under `lan_hosts`
6. **Create host_vars**: `inventory/host_vars/nodename.yml` with `ansible_host`
7. **Run bootstrap**: the `tasks/bootstrap_lan_host.yml` task handles DHCP
   lease and API token creation automatically during converge
8. **Verify**: `ssh -o ProxyJump=root@$PRIMARY_HOST root@<node-ip> hostname`

### SSH tunnel for browser access

```bash
ssh -L 8007:<lan-host-ip>:8006 root@192.168.86.201
# Then browse: https://localhost:8007
```

### BAD: Cleanup removes credentials

```yaml
# BAD -- this locks out the remote node permanently
- name: Remove SSH keys
  file: path=/root/.ssh/authorized_keys state=absent

- name: Remove API token
  command: pveum user token remove root@pam ansible
```

### GOOD: Cleanup only removes playbook artifacts

```yaml
# GOOD -- only touch what the playbook created
- name: Remove ansible-managed files
  file: path={{ item }} state=absent
  loop:
    - /etc/network/interfaces.d/ansible-bridges.conf
    - /etc/ansible/facts.d/vm_builds.fact
```

## Environment variable convention

```bash
PRIMARY_HOST=192.168.86.201          # Primary Proxmox host IP
HOME_API_TOKEN=cab59c9a-...          # matches inventory_hostname "home"
MESH1_API_TOKEN=39d2976f-...         # matches inventory_hostname "mesh1"
```

Convention: `<INVENTORY_HOSTNAME>_API_TOKEN` (uppercased, hyphens → underscores).

`group_vars/proxmox.yml` resolves dynamically — no per-host override needed:

```yaml
proxmox_api_token_secret: >-
  {{ lookup('env', (inventory_hostname | upper | replace('-', '_')) + '_API_TOKEN') }}
```

## Inventory layout

```yaml
# inventory/hosts.yml
proxmox:
  children:
    lan_hosts:
      hosts:
        mesh1: {}
```

```yaml
# inventory/group_vars/lan_hosts.yml
ansible_ssh_common_args: >-
  -o ProxyJump=root@{{ lookup('env', 'PRIMARY_HOST') }}
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=10
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4
```

## Bootstrap flow (`tasks/bootstrap_lan_host.yml`)

Called from the primary host (`router_nodes`):

1. **Verifies SSH key auth** — fails with setup instructions if not working
2. **Creates DHCP static lease** on OpenWrt (if missing)
3. **Creates API token** on the LAN host (if missing)
4. **Saves token** to `test.env` on the controller

## Baseline workflow for LAN nodes

LAN nodes (mesh1) are ONLY reachable when the OpenWrt baseline is running.
Prefer keeping the baseline up between test runs:

```bash
molecule converge                  # build/update baseline (idempotent)
molecule verify                    # verify baseline
molecule converge -s mesh1-infra   # run layered scenario
molecule verify -s mesh1-infra     # verify mesh1

# Clean-state validation only when needed:
molecule test                      # destroys everything
molecule converge                  # restore baseline for further work
```

## Molecule scenario for LAN nodes

```yaml
# molecule/mesh1-infra/molecule.yml (key sections)
platforms:
  - name: home
    groups: [proxmox, router_nodes]
  - name: mesh1
    groups: [proxmox, lan_hosts]

provisioner:
  env:
    HOME_API_TOKEN: ${HOME_API_TOKEN}
    PRIMARY_HOST: ${PRIMARY_HOST}
    MESH1_API_TOKEN: ${MESH1_API_TOKEN}
```

Test sequence: `dependency → syntax → converge → verify → cleanup`
(no initial cleanup — baseline must exist).

## Troubleshooting

### LAN host unreachable
1. Verify OpenWrt is running: `qm status 100` on the primary host
2. Verify LAN bridge IP: `ip -4 addr show | grep 10.10.10` on the primary host
3. Ping from primary: `ping -c1 10.10.10.210`
4. Check DHCP lease: `ssh root@10.10.10.1 'cat /tmp/dhcp.leases'`

### SSH "Permission denied"
1. Verify key auth from primary: `ssh -o BatchMode=yes root@10.10.10.210 hostname`
2. Re-push keys via Proxmox GUI shell or SSH tunnel (see "Adding a new LAN node")

### DHCP lease drift
If the node gets a different IP after router reboot, the DHCP static lease
may not have been committed. Check on OpenWrt:
`uci show dhcp | grep mesh1` — if empty, re-run the bootstrap.

### API token issues
1. List tokens: `pveum user token list root@pam` on the LAN host
2. Create: `pveum user token add root@pam ansible --privsep=0`
3. Save to `test.env`: `MESH1_API_TOKEN=<value>`

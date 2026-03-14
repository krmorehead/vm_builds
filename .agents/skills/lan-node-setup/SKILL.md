---
name: lan-node-setup
description: Add LAN hosts, env variables, inventory setup, bootstrap flow for Proxmox nodes behind OpenWrt.
---

# LAN Node Setup

## Adding a new LAN node

1. **Physical setup**: connect node to LAN switch behind OpenWrt
2. **Discover node from primary**:

```bash
ssh root@10.10.10.1 'cat /tmp/dhcp.leases'
# or
for i in $(seq 200 220); do ping -c1 -W1 10.10.10.$i &>/dev/null && echo 10.10.10.$i; done
```

3. **Set up SSH key auth** (one time):

```bash
# Option A: SSH tunnel + GUI shell
ssh -L 8007:<node-ip>:8006 root@192.168.86.201
# Browse https://localhost:8007, open Shell, paste public keys

# Option B: ssh-copy-id through ProxyJump
ssh-copy-id -o ProxyJump=root@192.168.86.201 root@<node-ip>
```

4. **Add env var**: `<INVENTORY_HOSTNAME>_API_TOKEN=` to .env/test.env
5. **Add to inventory**: inventory/hosts.yml under lan_hosts
6. **Create host_vars**: inventory/host_vars/nodename.yml with ansible_host
7. **Run bootstrap**: tasks/bootstrap_lan_host.yml during converge
8. **Verify**: `ssh -o ProxyJump=root@$PRIMARY_HOST root@<node-ip> hostname`

## Environment variable convention

```bash
PRIMARY_HOST=192.168.86.201          # Primary Proxmox host IP
HOME_API_TOKEN=cab59c9a-...          # matches inventory_hostname "home"
MESH1_API_TOKEN=39d2976f-...         # matches inventory_hostname "mesh1"
```

Convention: `<INVENTORY_HOSTNAME>_API_TOKEN` (uppercased, hyphens → underscores).

Dynamic resolution in group_vars:

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

# inventory/host_vars/mesh1.yml
ansible_host: 10.10.10.210
ansible_user: root
```

## Bootstrap flow (tasks/bootstrap_lan_host.yml)

Called from primary host (router_nodes):

1. Verifies SSH key auth — fails with setup instructions if not working
2. Creates DHCP static lease on OpenWrt (if missing)
3. Creates API token on LAN host (if missing)
4. Saves token to test.env on controller

## Molecule scenario for LAN nodes

```yaml
# molecule/mesh1-infra/molecule.yml
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

Test sequence: dependency → syntax → converge → verify → cleanup
(no initial cleanup — baseline must exist)

## Baseline workflow

LAN nodes only reachable when OpenWrt baseline running:

```bash
molecule converge                  # build/update baseline
molecule verify                    # verify both nodes
molecule converge -s mesh1-infra   # infra-only on mesh1 (quick)
molecule verify -s mesh1-infra

# Clean-state validation only when needed:
molecule test                      # full pipeline
molecule converge                  # restore baseline
```

## DHCP lease issues

If node gets different IP after router reboot, static lease may not be committed:

```bash
ssh root@10.10.10.1 'uci show dhcp | grep mesh1'
# If empty, re-run bootstrap
```

## API token issues

List tokens: `pveum user token list root@pam`
Create: `pveum user token add root@pam ansible --privsep=0`
Save to test.env: `MESH1_API_TOKEN=<value>`

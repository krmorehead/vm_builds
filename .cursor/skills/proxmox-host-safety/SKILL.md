---
name: proxmox-host-safety
description: Validates Ansible tasks and shell commands for safety before executing them against remote Proxmox hosts. Use when writing or reviewing Ansible playbooks, roles, or shell commands that target Proxmox hosts, when running cleanup/restore operations, when modifying network interfaces or bridges, when working with PCI passthrough, or when SSH connectivity to a remote host might be affected.
---

# Proxmox Host Safety Validation

## Pre-flight checklist

Before running ANY command or playbook against a Proxmox host, validate:

### 1. Network-killing commands (BLOCK these)

These commands will sever SSH and make the host unreachable:

```yaml
# DANGEROUS - will kill your connection
- ifdown --all
- ifdown --all --force
- systemctl stop networking
- systemctl restart networking   # can drop and fail to restore
- ip link delete vmbr0           # destroys management bridge
- ip link set vmbr0 down         # kills management path
```

**Safe alternatives:**
```yaml
# Safe - additive, brings up new interfaces without tearing down existing
- ifup --all --force

# Safe - reload that preserves running interfaces
- ifreload -a

# Safe - tear down a SPECIFIC non-management bridge
- ip link set vmbr5 down && ip link delete vmbr5
```

### 2. Bridge teardown safety

When removing bridges during cleanup:
- Get the management bridge from the host's default route device (do NOT assume `vmbr0`).
- NEVER tear down the management bridge.
- Iterate over stale bridges and skip the management one:

```yaml
- name: Tear down stale bridges (skip management)
  ansible.builtin.shell:
    cmd: |
      mgmt_br=$(ip -o route show default | awk '{print $5}' | head -1)
      for br in $(ip -br link show type bridge | awk '{print $1}'); do
        [ "$br" = "$mgmt_br" ] && continue
        ip link set "$br" down
        ip link delete "$br"
      done
  changed_when: true
```

### 2b. WAN bridge ordering

NEVER hardcode bridge-to-role mappings (e.g., `vmbr0 = WAN`). The WAN bridge is detected at runtime via the host's default route. `openwrt_vm` orders bridges so the WAN bridge maps to `net0`/`eth0`; all others become LAN. Override with `openwrt_wan_bridge` in `host_vars` if needed.

Previous bug: hardcoded `vmbr0 = WAN` made Proxmox GUI unreachable when the modem was plugged into the NIC behind `vmbr0`, because leaf nodes on the LAN bridge had no route to the management IP on the WAN bridge.

### 3. LVM operations on root volumes

- Do NOT create LVM snapshots of the Proxmox root volume (`pve/root`). Merging snapshots on a live root volume is unreliable and can leave the system in a stuck merge state requiring reboot.
- Use file-based config backups (`tar`) and `vzdump` for VMs instead.

### 4. Reboot awareness

- If a playbook changes GRUB, initramfs, or kernel modules, a reboot may be needed.
- Set `pci_passthrough_allow_reboot: true` in host vars to allow automated reboots.
- After reboot, wait for SSH to come back with `wait_for_connection`.

### 5. Cleanup completeness

When ANY role deploys a file to the Proxmox host, ALWAYS add it to the removal list in BOTH cleanup playbooks (`molecule/default/cleanup.yml` AND `playbooks/cleanup.yml`).

Current ansible-managed files that must be cleaned:
- `/etc/network/interfaces.d/ansible-bridges.conf` (bridge config, may be modified to `inet dhcp`)
- `/etc/network/interfaces.d/ansible-proxmox-lan.conf` (legacy LAN management IP, superseded)
- `/etc/network/interfaces.d/ansible-temp-lan.conf` (test workaround, cleaned up)
- `/etc/modprobe.d/blacklist-wifi.conf` (WiFi driver blacklist)
- `/etc/modprobe.d/vfio-pci.conf` (PCI passthrough config)
- `/etc/ansible/facts.d/vm_builds.fact` (deploy stamp tracking)
- `/etc/apt/sources.list.d/pve-no-subscription.sources` (added by `proxmox_igpu`)
- `/tmp/openwrt.img` (left behind if build fails mid-upload)
- `/var/lib/vz/template/cache/debian-*.tar.zst` (LXC templates)
- Enterprise repos: restore `pve-enterprise.sources.disabled` → `.sources` and `ceph.sources.disabled` → `.sources`

Local state files that must be cleaned (via `delegate_to: localhost`):
- `.state/addresses.json` (cached host IPs)

Previous bug: `ansible-proxmox-lan.conf` was deployed but not cleaned up, leaving stale LAN management IPs across test runs.

### 6. Test machine protocol

Before running destructive operations (cleanup, VM destroy):
1. Confirm the target is the **test machine** (check `PROXMOX_HOST` env var).
2. Verify a backup exists (check for `manifest.json` in backup dir).
3. Use the `cleanup.sh` wrapper which enforces env file sourcing.

## SSH stability for OpenWrt connections

After OpenWrt network restart, the LAN IP changes (e.g., `192.168.1.1` → `10.10.10.1`). The bootstrap SSH connection will hang forever unless `ConnectTimeout` is set.

Required SSH args for baseline (password auth) OpenWrt connections:
```yaml
ansible_ssh_common_args: >-
  -o ProxyJump=root@{{ ansible_host }}
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o PubkeyAuthentication=no
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
```

After security hardening (M1), SSH switches to key auth. Replace
`PubkeyAuthentication=no` with `-i <key_path>` and remove `sshpass`:
```yaml
ansible_ssh_common_args: >-
  -o ProxyJump=root@{{ ansible_host }}
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -i {{ lookup('env', 'OPENWRT_SSH_PRIVATE_KEY') }}
```

The group reconstruction task file (`tasks/reconstruct_openwrt_group.yml`)
auto-detects which auth method is active by checking `deploy_stamp` state
and the `OPENWRT_SSH_PRIVATE_KEY` env var.

- `ConnectTimeout=10`: Prevents infinite hang when LAN IP changes.
- `ServerAliveInterval=15`: Prevents connection drop during local Ansible tasks (set_fact sequences) that don't generate SSH traffic.
- NEVER retry SSH to the old bootstrap address after LAN reconfiguration.

## PCI device cleanup after VM destruction

Devices bound to `vfio-pci` do NOT auto-revert when the VM is destroyed. Without cleanup, the next run can't detect WiFi hardware:

```bash
# 1. Unbind all vfio-pci devices
for dev in /sys/bus/pci/drivers/vfio-pci/0000:*/; do
  addr=$(basename "$dev")
  echo "$addr" > /sys/bus/pci/drivers/vfio-pci/unbind
done

# 2. Remove blacklist and vfio config files
rm -f /etc/modprobe.d/blacklist-wifi.conf /etc/modprobe.d/vfio-pci.conf

# 3. Reload original WiFi drivers
modprobe -r iwlmvm iwlwifi 2>/dev/null; modprobe iwlwifi

# 4. Rescan PCI bus
echo 1 > /sys/bus/pci/rescan
```

All four steps are required. Step 3 is critical -- `echo 1 > /sys/bus/pci/rescan` alone is insufficient because the kernel won't auto-bind drivers that were explicitly unbound.

## Decision tree

```
Is this command touching network interfaces?
├── YES → Does it tear down ALL interfaces?
│   ├── YES → BLOCK. Use targeted teardown instead.
│   └── NO → Is it tearing down vmbr0?
│       ├── YES → BLOCK.
│       └── NO → SAFE. Proceed.
└── NO → Is it modifying LVM on root?
    ├── YES → BLOCK. Use tar + vzdump instead.
    └── NO → SAFE. Proceed.
```

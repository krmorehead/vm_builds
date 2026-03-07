---
name: proxmox-host-safety
description: Validates Ansible tasks and shell commands for safety before executing them against remote Proxmox hosts. Use when writing or reviewing Ansible playbooks, roles, or shell commands that target Proxmox hosts, or when running cleanup/restore operations on remote machines.
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
- Get the management bridge (usually `vmbr0`, check `/etc/network/interfaces`).
- NEVER tear down the management bridge.
- Iterate over stale bridges and skip the management one:

```yaml
- name: Tear down stale bridges (skip management)
  ansible.builtin.shell:
    cmd: |
      for br in $(ip -br link show type bridge | awk '{print $1}'); do
        case "$br" in vmbr0) continue ;; esac
        ip link set "$br" down
        ip link delete "$br"
      done
  changed_when: true
```

### 3. LVM operations on root volumes

- Do NOT create LVM snapshots of the Proxmox root volume (`pve/root`). Merging snapshots on a live root volume is unreliable and can leave the system in a stuck merge state requiring reboot.
- Use file-based config backups (`tar`) and `vzdump` for VMs instead.

### 4. Reboot awareness

- If a playbook changes GRUB, initramfs, or kernel modules, a reboot may be needed.
- Set `pci_passthrough_allow_reboot: true` in host vars to allow automated reboots.
- After reboot, wait for SSH to come back with `wait_for_connection`.

### 5. Test machine protocol

Before running destructive operations (cleanup, VM destroy):
1. Confirm the target is the **test machine** (check `PROXMOX_HOST` env var).
2. Verify a backup exists (check for `manifest.json` in backup dir).
3. Use the `cleanup.sh` wrapper which enforces env file sourcing.

## SSH timeout on OpenWrt after reconfiguration

After OpenWrt network restart, the LAN IP changes (e.g., `192.168.1.1` → `10.10.10.1`). The bootstrap SSH connection will hang forever unless `ConnectTimeout` is set. ALWAYS include `-o ConnectTimeout=10` in `ansible_ssh_common_args` for OpenWrt connections. NEVER retry SSH to the old bootstrap address after LAN reconfiguration -- it will never come back.

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

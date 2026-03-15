---
name: proxmox-ssh-safety
description: Proxmox SSH connection safety and OpenWrt connectivity patterns. Use when managing SSH connections to OpenWrt, connection timeouts, or SSH authentication methods.
---

# Proxmox SSH & Connection Safety

## OpenWrt SSH Stability Requirements

1. After OpenWrt network restart, the LAN IP changes (e.g., `192.168.1.1` → `10.10.10.1`). The bootstrap SSH connection will hang forever unless `ConnectTimeout` is set.

## Baseline OpenWrt SSH Configuration

2. Required SSH args for baseline (password auth) OpenWrt connections:
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

## Security Hardened OpenWrt SSH Configuration

3. After security hardening (M1), SSH switches to key auth. Replace `PubkeyAuthentication=no` with `-i <key_path>` and remove `sshpass`:
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

## SSH Configuration Reasoning

4. The group reconstruction task file (`tasks/reconstruct_openwrt_group.yml`) auto-detects which auth method is active by checking `deploy_stamp` state and the `OPENWRT_SSH_PRIVATE_KEY` env var.

5. `ConnectTimeout=10`: Prevents infinite hang when LAN IP changes.

6. `ServerAliveInterval=15`: Prevents connection drop during local Ansible tasks (set_fact sequences) that don't generate SSH traffic.

## Connection Safety Rules

7. NEVER retry SSH to the old bootstrap address after LAN reconfiguration.

8. Always use ProxyJump through the Proxmox host for OpenWrt connections to maintain connectivity through network topology changes.
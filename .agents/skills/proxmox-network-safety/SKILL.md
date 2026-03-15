---
name: proxmox-network-safety
description: Proxmox network interface safety and bridge management patterns. Use when managing Proxmox bridges, network interfaces, or preventing network connectivity issues.
---

# Proxmox Network Safety Rules

## Network-Killing Commands (BLOCK These)

1. These commands will sever SSH and make the host unreachable:
   ```yaml
   # DANGEROUS - will kill your connection
   - ifdown --all
   - ifdown --all --force
   - systemctl stop networking
   - systemctl restart networking   # can drop and fail to restore
   - ip link delete vmbr0           # destroys management bridge
   - ip link set vmbr0 down         # kills management path
   ```

## Safe Network Alternatives

2. Use safe alternatives instead:
   ```yaml
   # Safe - additive, brings up new interfaces without tearing down existing
   - ifup --all --force

   # Safe - reload that preserves running interfaces
   - ifreload -a

   # Safe - tear down a SPECIFIC non-management bridge
   - ip link set vmbr5 down && ip link delete vmbr5
   ```

## Bridge Teardown Safety

3. When removing bridges during cleanup:
   - Get the management bridge from the host's default route device (do NOT assume `vmbr0`)
   - NEVER tear down the management bridge
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

## WAN Bridge Ordering Rules

4. NEVER hardcode bridge-to-role mappings (e.g., `vmbr0 = WAN`). The WAN bridge is detected at runtime via the host's default route. `openwrt_vm` orders bridges so the WAN bridge maps to `net0`/`eth0`; all others become LAN.

5. Override with `openwrt_wan_bridge` in `host_vars` if needed.

6. Previous bug: hardcoded `vmbr0 = WAN` made Proxmox GUI unreachable when the modem was plugged into the NIC behind `vmbr0`, because leaf nodes on the LAN bridge had no route to the management IP on the WAN bridge.

## Decision Tree for Network Safety

7. Use this decision tree:
   ```
   Is this command touching network interfaces?
   ├── YES → Does it tear down ALL interfaces?
   │   ├── YES → BLOCK. Use targeted teardown instead.
   │   └── NO → Is it tearing down vmbr0?
   │       ├── YES → BLOCK.
   │       └── NO → SAFE. Proceed.
   └── NO → SAFE. Proceed.
   ```
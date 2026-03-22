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

## br_netfilter and LXC Container Connectivity

8. The `br_netfilter` kernel module causes iptables/nftables to filter BRIDGED traffic. When loaded, host-level firewall rules can silently block traffic between the host and its own LXC containers on the same bridge, even though ICMP ping works (different netfilter path).

9. The `proxmox_lxc` role disables `bridge-nf-call-iptables` during provisioning, but `br_netfilter` can be re-loaded later by other kernel modules or services. ALWAYS check and disable before network-dependent verification tests:

   ```yaml
   - name: Check if br_netfilter is loaded
     ansible.builtin.stat:
       path: /proc/sys/net/bridge/bridge-nf-call-iptables
     register: _br_nf

   - name: Disable iptables on bridge traffic
     ansible.posix.sysctl:
       name: net.bridge.bridge-nf-call-iptables
       value: "0"
       sysctl_set: true
       reload: true
     when: _br_nf.stat.exists
   ```

10. Symptoms: `ping` to container IP succeeds but TCP connections (`logger --tcp`, `curl`, `nc`) fail with "connection refused" or timeout. This mismatch (ICMP works, TCP doesn't) is the hallmark of `br_netfilter` interference.

11. Previous bug: rsyslog verify on mesh1 consistently failed `logger --tcp -n 10.10.10.14 -P 514` while `ping 10.10.10.14` succeeded. The `br_netfilter` sysctl was already `0`, suggesting nftables rules or conntrack state was involved. A fallback via `pct exec` (localhost TCP inside the container) was added to maintain test coverage.

## Decision Tree for Network Safety

12. Use this decision tree:
   ```
   Is this command touching network interfaces?
   ├── YES → Does it tear down ALL interfaces?
   │   ├── YES → BLOCK. Use targeted teardown instead.
   │   └── NO → Is it tearing down vmbr0?
   │       ├── YES → BLOCK.
   │       └── NO → SAFE. Proceed.
   └── NO → SAFE. Proceed.
   ```
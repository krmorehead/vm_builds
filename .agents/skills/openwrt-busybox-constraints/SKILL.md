---
name: openwrt-busybox-constraints
description: BusyBox ash shell limitations and constraints for OpenWrt. Use when writing shell scripts, troubleshooting command failures, or working with network utilities on OpenWrt.
---

# OpenWrt BusyBox Shell Constraints

## Shell Feature Limitations

1. BusyBox ash does NOT support `set -o pipefail`. NEVER add pipefail to `ansible.builtin.raw` tasks or `{{ openwrt_ssh }}` commands that run on OpenWrt. Pipefail is required for all host-side `ansible.builtin.shell` tasks — see the `proxmox-safety` rule.

2. BusyBox `tr -d '[:space:]'` deletes colons (`:`) because BusyBox treats `[:space:]` as a character set containing `[`, `:`, `s`, `p`, `a`, `c`, `e`, `]` — NOT as a POSIX character class. ALWAYS use explicit chars: `tr -d ' \t\n\r'`.

3. BusyBox `nc` does NOT support `-w` (timeout) flag. Use `(echo QUIT | nc HOST PORT) </dev/null` for TCP port checks. NEVER use `echo | nc -w 3` on OpenWrt.

## Network Command Limitations

4. BusyBox `ip neigh show` does NOT support IP filter arguments like full iproute2. ALWAYS use `/proc/net/arp` with `awk` to look up gateway MACs on OpenWrt.

5. Similarly, avoid `ip -o`, `grep -oP`, and `grep -E` on OpenWrt.

## Default Route Detection

6. When checking for the default route in scripts on OpenWrt, NEVER filter by device name (`ip route show default dev eth0`). OpenWrt's netifd may use interface aliases (e.g., `wan`, `eth0.2`) that differ from the physical device name. Use `ip route show default` without a device filter.

## Process Detection

7. NEVER use `pgrep` on OpenWrt — it may not exist in BusyBox. Use `/etc/init.d/<service> status` or `ps | grep -c '[p]rocess'` instead.

8. Previous bug: `pgrep -x logd` failed on OpenWrt because pgrep wasn't available. Fixed by using `/etc/init.d/log status`.
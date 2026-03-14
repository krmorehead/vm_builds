---
name: openwrt-ssh-pct-remote
description: OpenWrt pct_remote shell syntax and SSH connection patterns. Use when running commands in OpenWrt containers via Proxmox, managing LXC containers, or debugging SSH connectivity issues.
---

# OpenWrt pct_remote Shell Syntax Rules

## Shell Execution Through pct_remote

1. The `community.proxmox.proxmox_pct_remote` connection plugin builds `/usr/sbin/pct exec <vmid> -- <cmd>` and sends it as a single string via SSH to the Proxmox host. The HOST's bash interprets the entire string before `pct exec` runs.

## Critical Shell Syntax Issues

2. **Semicolons split at host level.** `cmd1; cmd2` becomes two separate commands on the Proxmox host — only `cmd1` runs inside the container.

3. **Pipes split at host level.** `cmd1 | cmd2` — `cmd1` runs inside the container, `cmd2` runs on the HOST (filtering stdout from the container). This happens to work for text processing but is fragile.

4. **`export` is NOT a binary.** `lxc-attach` tries to exec the first word of the command as a binary. `export` is a shell builtin — it fails with `lxc-attach: Failed to exec "export"`.

5. **PATH is not set inside the container.** `lxc-attach`'s `execvp` uses the default path (`/bin:/usr/bin`), which misses `/sbin` and `/usr/sbin` where OpenWrt puts `uci`, `wifi`, `iw`.

## Solution: sh -c Wrapper Pattern

6. **ALWAYS wrap all commands in `/bin/sh -c '...'`.** The single quotes protect the payload from host bash. Inside the container, busybox ash provides its default `PATH=/sbin:/usr/sbin:/bin:/usr/bin`.

```bash
# BAD — semicolons split at host level, export is not a binary
- ansible.builtin.raw: >-
    export PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH";
    opkg update

# BAD — for loops break (host bash tries to exec "for")
- ansible.builtin.raw: >-
    for mod in iwlwifi ath9k; do modprobe "$mod" 2>/dev/null; done

# GOOD — sh -c wraps everything in a container-side shell
- ansible.builtin.raw: >-
    /bin/sh -c 'opkg update'

# GOOD — complex commands with semicolons inside sh -c
- ansible.builtin.raw: >-
    /bin/sh -c
    'opkg list-installed 2>/dev/null | grep -c wpad-mesh || true'

# GOOD — multi-command chains use && inside sh -c
- ansible.builtin.raw: >-
    /bin/sh -c
    'uci set wireless.mesh0=wifi-iface &&
    uci set wireless.mesh0.device="radio0" &&
    uci commit wireless'
```

## Quoting Rules for sh -c Through pct_remote

7. Outer single quotes protect the entire payload from host bash.

8. Inside, use double quotes for values: `uci set foo.bar="value"`

9. NEVER nest single quotes — use double quotes or drop quotes for simple alphanumeric values.

10. `&&` and `||` inside single quotes are interpreted by container ash.

11. `[ ... ] && echo x || echo y` WITHOUT sh -c is OK — `[` is exec'd by lxc-attach, `&&`/`||` chain at host level (works for simple checks).
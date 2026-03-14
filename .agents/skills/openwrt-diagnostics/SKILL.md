---
name: openwrt-diagnostics
description: OpenWrt permanent diagnostics and verification patterns. Use when debugging OpenWrt builds, troubleshooting network issues, or implementing diagnostic checks.
---

# OpenWrt Diagnostic and Verification Rules

## Permanent Diagnostics

1. OpenWrt build includes diagnostic tasks at two key milestones that run on EVERY build:

**Bootstrap diagnostics** (`openwrt_vm`, after SSH bootstrap):
   - VM status, bridge layout, bootstrap IP presence, dmesg errors

**Phase 1 diagnostics** (`openwrt_configure`, after WAN route + firewall restart):
   - WAN route, WAN IP, LAN IP, DNS resolvers, firewall status, dmesg errors

**Final diagnostics** (`openwrt_configure`, end of build):
   - VM status, onboot/startup config, LAN bridge IP, management config presence

2. When build fails, diagnostic output from last successful milestone narrows failure window.

## State File Pattern

3. `build.py` probes `PRIMARY_HOST` before running Ansible. If unreachable, it reads `.state/addresses.json` for cached alternative IPs. This handles cable-swap scenarios where original management IP is no longer routable.

4. The state file is written by `openwrt_configure` and cleaned by both cleanup playbooks. It is gitignored.

## Verification Requirements

5. ALWAYS verify expected outcome after network operations:
   - `wait_for` on SSH port (proves dropbear restarted)
   - `wait_for` on WAN default route (proves network restarted)
   - Firewall restart task (proves firewall can be restarted = it's running)

6. If detached script fails silently, verification steps catch it.
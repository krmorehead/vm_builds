---
name: openwrt-feature-integration
description: OpenWrt feature integration via task files and play patterns. Use when adding new features, creating modular configuration, or organizing OpenWrt configuration tasks.
---

# OpenWrt Feature Integration Patterns

## Task File Structure

1. Post-baseline features (security, VLANs, DNS, mesh) are implemented as separate task files within `roles/openwrt_configure/tasks/`:

```
roles/openwrt_configure/tasks/
├── main.yml          # Baseline configuration (WAN, LAN, DHCP, firewall)
├── security.yml      # M1: SSH hardening, banIP
├── vlans.yml         # M2: VLAN segmentation
├── dns.yml           # M3: Encrypted DNS (https-dns-proxy)
└── mesh.yml          # M4: WDS WiFi backhaul + Dawn steering
```

## Dual Play Pattern

2. Each feature gets TWO plays in `site.yml`:

**Configure play**: targets `openwrt` dynamic group with `include_role` using `tasks_from: <feature>.yml`

**Deploy stamp play**: targets `router_nodes` (Proxmox host) to record feature was applied

3. Both plays share a tag (e.g., `openwrt-security`) so they can be run individually via `--tags`. They also run in the default (no-tags) flow — every feature runs every time in the E2E pipeline.

## NEVER use Ansible's `never` special tag

NEVER tag plays with `[never]`. The `never` tag causes Ansible to silently skip plays unless `--tags never` is explicitly passed. This creates untested dead code — the play exists in site.yml but is silently skipped during `molecule test`, which runs with no `--tags`.

Previous bug: Gaming LXC was tagged `[gaming, never]` from its first commit (March 2026). It was NEVER deployed or tested in any E2E run for over a month. All per-feature OpenWrt plays (security, VLANs, DNS, mesh, syslog, pihole-dns) were also tagged `[never]` — none of them ran in the default pipeline.

The correct gating mechanism is idempotency, not tag exclusion. Every service is a core service. Every play runs every time. If a configure role is idempotent (and they all are), running it every time is safe and guarantees test coverage.

For rollback plays in `cleanup.yml`, the rollback-specific tag (e.g., `openwrt-security-rollback`) already gates them — you must explicitly pass `--tags openwrt-security-rollback` to invoke them. No `never` needed.

## Benefits

4. This pattern avoids re-running baseline tasks when iterating on a feature and enables per-feature molecule scenarios that converge only the relevant task file.
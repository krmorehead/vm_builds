---
name: molecule-performance
description: Molecule test performance optimization patterns. Template caching, NTP sync, pct_remote overhead, apt cache, selective rebuilds.
---

# Molecule Performance Optimization

The full 4-node `molecule test` takes ~13-14 minutes. Most time goes to template uploads, NTP sync, `pct_remote` overhead, and SSH round trips in verify. Apply these rules to avoid wasting time on every test run.

## Template Caching

NEVER delete templates in molecule cleanup. Keep them cached on Proxmox hosts. Template deletion forces re-upload of ~820MB across 4 hosts on every run.

Template deletion is only valid in production cleanup behind `[full-restore, clean]` tags.

## NTP Sync Optimization

Check clock skew BEFORE running the full NTP burst sequence. Use `chronyc -n tracking | awk '/System time/{print $4}'` to get skew in seconds.

Only sync when skew > 30s. The NTP sync takes ~7s per host. With 4 hosts × 3 sync points, that's 84s wasted when clocks are accurate.

## pct_remote Task Minimization

Each `pct_remote` task opens a new SSH connection and takes 15-60 seconds. MINIMIZE tasks in configure roles. Base system config that is identical across all containers belongs in the image, NOT the configure role.

Example: Moving a systemd override from configure role (3 tasks via pct_remote) to the image saved 38% of per-feature test time.

## Verify Phase Optimization

**Consolidate pct config reads:**
```yaml
# Read config once per container
ansible.builtin.command:
  cmd: pct config {{ ct_id }}
register: _ct_cfg

# Assert on cached output
ansible.builtin.assert:
  that: "'onboot: 1' in _ct_cfg.stdout"
```

**Batch pct exec calls:**
```yaml
# Combine independent checks into one call
ansible.builtin.shell:
  cmd: >-
    pct exec {{ ct_id }} -- /bin/sh -c '
    echo "IFACE=$(ip link show wg0 >/dev/null 2>&1 && echo ok || echo missing)";
    echo "SVC=$(systemctl is-enabled wg-quick@wg0 2>/dev/null || echo unknown)";
    echo "NAT=$(iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null && echo present || echo missing)"
    '
register: wg_health
```

## Wait/Pause Tuning

- OpenWrt detached restart scripts: 20s pause (not 30s)
- OpenWrt VM first boot: `delay: 10, timeout: 120` (not 15/180)
- LXC container networking: `delay: 3` (not 4)
- WiFi PHY detection: `delay: 3` (not 5)
- Verify-phase SSH waits: reduce delay/timeout for services already confirmed running from converge

## Apt Cache Configuration

Set `cache_valid_time: 86400` (24h) for apt tasks, not 3600 (1h). Test machines rarely have stale packages.

## Selective Image Rebuilds

Use `./build-images.sh --host <ip> --only <target>` to rebuild a single image. Full rebuilds take ~15 min; selective rebuilds take ~2-3 min.

Every service MUST have a custom image with ALL packages baked in. ZERO configure roles should install packages at runtime.

## Play Merging

When two verify plays target the same `hosts:` group with the same `gather_facts:` setting, merge them into one play to eliminate startup overhead.
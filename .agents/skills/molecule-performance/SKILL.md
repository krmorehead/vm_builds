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

## Fleet API Eliminates Most Verify SSH Overhead

The fleet readiness API (`/api/fleet/ready`, `/api/container/{id}/ready`)
replaces most `pct exec` liveness checks in verify with a single HTTP call.
When the fleet API is available (`_fleet_api_ready`), container health is
queried via `uri` instead of individual `pct exec` SSH connections.

SSH batching (below) remains relevant for:
- The **fallback path** when the fleet API is unavailable
- **Configure-phase** tasks (pct_remote is the only option during deploy)
- **Hypervisor checks** (pct config, qm config) that the fleet API doesn't cover

See the `manager-api-pattern` skill for the full dual-path pattern.

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

Build images IN PARALLEL across 6 hosts. Never serialize image builds when you have 6 available machines. The fast iteration loop is: fix build script → rebuild 1 image (~2 min) → per-feature test (~5 min) → loop until clean → full E2E once.

## Host-Level Services Are Deployable Infrastructure

Proxmox hosts are bakeable targets. Host-level systemd units (`manager-api-proxy.service`, `supermanager-relay.service`), iptables rules, and kernel parameters are all infrastructure that Ansible deploys during provisioning. They are tested the same way as container services — if a host service breaks, it shows up in molecule verify.

NEVER use `nohup ... &` for persistent host services. Background processes die when SSH sessions close. ALWAYS deploy systemd units with `Restart=always`.

Previous bug (2026-04-12): socat relays for 4-tier heartbeat chain were started with `nohup socat &`. Both died when Ansible's SSH ControlMaster session closed mid-E2E, causing 0 of 6 hosts on the SuperManager. Fix: systemd units persisted through the entire test and survived reboots.

## pct_remote gather_facts Avoidance

NEVER use `gather_facts: true` on plays targeting `pct_remote` dynamic groups unless the configure role actually references `ansible_*` facts. The `pct_remote` connection plugin pipes the setup module's JSON through `pct exec` and SSH. Hosts with many block devices (LVM thin pool, loop devices, device-mapper) generate `ansible_devices` output that exceeds the buffer, causing "Module result deserialization failed: No end of json char found".

ALWAYS default to `gather_facts: false` for `pct_remote` configure plays. If specific facts are needed, use `gather_subset: ['!hardware']` to skip the device-heavy hardware facts.

Previous bug: `wireguard-ai` via `pct_remote` — the `ai` host had 15+ block devices, producing ~12KB of `ansible_devices` JSON. The full setup module output (~20KB) was truncated by `pct exec`, causing JSON deserialization failure. Other hosts with fewer devices succeeded.

## serial Throttling for Multi-Container pct_remote

When a configure play targets 4+ `pct_remote` containers, each task opens a NEW paramiko SSH connection. With 4 containers × 8-10 tasks, that's 32-40 SSH connections opening/closing in rapid succession through the same SSH server. This overwhelms `sshd` and causes "Connection reset by peer", "Broken pipe", and "SSH session not active" errors.

ALWAYS add `serial: 2` to configure plays that target 4+ `pct_remote` containers:

```yaml
- name: Configure WireGuard
  hosts: wireguard
  gather_facts: false
  serial: 2
  tags: [wireguard]
  roles:
    - wireguard_configure
```

Previous bug: WireGuard configure ran 4 containers in parallel. Two containers (wireguard-home, wireguard-mesh1) both routed SSH through `home`'s sshd. The rapid connection churn caused "Connection reset by peer (104)" on both. Fix: `serial: 2` processes containers in batches, halving simultaneous SSH load.

## Bypass pct_remote for Health-Only Configure Plays

When a configure play ONLY checks service health (no container-internal
configuration changes), bypass `pct_remote` entirely by targeting the Proxmox
HOST group and using `ansible.builtin.command` with `pct exec`. This eliminates
paramiko SSH hangs that `serial` and timeout settings cannot prevent:

```yaml
- name: Configure Netdata
  hosts: monitoring_nodes
  gather_facts: false
  tasks:
    - name: Check service health
      ansible.builtin.command:
        cmd: pct exec {{ netdata_ct_id }} -- systemctl is-active netdata
      changed_when: false
      failed_when: false
      retries: 10
      delay: 3
      until: _check.stdout | trim == 'active'
```

Previous bug: `Configure Netdata` play with `pct_remote` hung indefinitely on
paramiko SSH handshake — even with `serial: 1`, `timeout = 60`, and
`host_key_auto_add = True`. 0% CPU indicated IO block. Fix: target host group
directly with `pct exec`, bypassing paramiko entirely.

## Play Merging

When two verify plays target the same `hosts:` group with the same `gather_facts:` setting, merge them into one play to eliminate startup overhead.

## Large VM Image Caching

Windows qcow2 images (19 GB) take 10-25 min to upload per host via SSH. Cache the image on the Proxmox host:

```yaml
- name: Check if image is already cached
  ansible.builtin.stat:
    path: "{{ upload_dest }}"
    get_checksum: false
  register: _remote_image

- name: Upload image (skip if cached)
  ansible.builtin.copy:
    src: "{{ local_image }}"
    dest: "{{ upload_dest }}"
  when: not (_remote_image.stat.exists | default(false))
```

Do NOT delete the cached image after import. Only production cleanup removes it.

Previous bug: 43-minute test run spent 25 minutes (60%) on image upload. Caching eliminates this on subsequent runs.

## Per-Feature Scenario Scope

Per-feature scenarios should test on a single host (e.g., `home` only). Multi-node coverage belongs in the full E2E test. This halves test time for services with expensive setup (large images, slow imports).
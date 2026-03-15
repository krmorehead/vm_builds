---
name: systemd-lxc-compatibility
description: Systemd sandboxing compatibility and LXC bind mount patterns. Use when configuring systemd services in LXC containers, managing container compatibility, or handling host metrics access.
---

# Systemd & LXC Compatibility Rules

## Systemd Sandboxing Requirements

1. Services installed from Debian packages (Netdata, rsyslog, etc.) often ship systemd unit files with sandboxing directives (`LogNamespace`, `ProtectSystem`, `ProtectHome`, `ProtectControlGroups`, `BindReadOnlyPaths`, `RuntimeDirectory`) that require mount namespace creation.

2. Inside LXC containers, these fail with exit code 226 (NAMESPACE).

## Two-Part Fix for Systemd Compatibility

3. **Container feature**: ALWAYS add `nesting=1` to LXC containers running services with systemd sandboxing. Without it, `unshare(CLONE_NEWNS)` is forbidden and ANY namespace-requiring directive fails.

4. **Systemd drop-in override**: Even with `nesting=1`, some directives like `LogNamespace` still fail. Deploy a drop-in override in the configure role AND bake it into the image:

   ```ini
   # /etc/systemd/system/<service>.service.d/lxc-override.conf
   [Service]
   LogNamespace=
   ProtectSystem=false
   ProtectHome=false
   ProtectControlGroups=false
   BindReadOnlyPaths=
   ```

## Systemd Override Rules

5. NEVER use empty strings for boolean-like settings (`ProtectSystem=` doesn't work). Use `ProtectSystem=false`.

6. For `LogNamespace`, empty string IS correct (it means "no namespace").

7. ALWAYS bake the override into the image via `build-images.sh`. The configure role MUST NOT deploy it — the override is base system config, not host-specific topology.

8. Previous bug: Netdata service exited 226/NAMESPACE in a privileged LXC container. Root cause: `LogNamespace=netdata` in the Netdata systemd unit created a journal namespace, which requires `CLONE_NEWNS` forbidden inside LXC even with `nesting=1`.

## Host Metrics via Bind Mounts

9. Monitoring agents (Netdata) that read host CPU, memory, disk, and temperature need access to the HOST's `/proc` and `/sys`, not the container's.

10. **Requirements:**
    1. **Privileged container** (`lxc_ct_unprivileged: false`). Unprivileged containers use UID mapping that prevents reading host procfs files.
    2. **Nesting feature** (`features: nesting=1`). Required for systemd sandboxing.
    3. **Bind mount entries**: Pass full Proxmox mount specs to `proxmox_lxc`:
       ```yaml
       lxc_ct_mount_entries:
         - "/proc,mp=/host/proc,ro=1"
         - "/sys,mp=/host/sys,ro=1"
       ```
    4. **Image config**: Bake `/host/proc` and `/host/sys` paths into the monitoring agent's config during image build.

## Per-Feature Scenario Requirements

11. Per-feature Molecule scenarios MUST include all groups that affect the role's branching logic. The role's `when:` conditions check group membership — if a group is missing, the wrong branch executes.

12. Previous bug: rsyslog-lxc scenario had `home` in `monitoring_nodes` only. The LAN/WAN detection checked `router_nodes` membership, found it missing, took the WAN path, and failed because `ansible_default_ipv4` was undefined.

13. Fix: add `router_nodes` to the per-feature scenario's platform groups for hosts that need the LAN path.

## LXC Package Management

14. ALWAYS use `install_recommends: false` when installing packages in LXC containers. Many packages Recommend kernel-related metapackages that pull in 70+ MB kernel images, filling the small LXC disk.

15. Broken apt in an LXC container means the baseline is wrong. The fix belongs in `proxmox_lxc` (shared provisioning), NOT in individual configure roles.

16. Previous bug: `wireguard-tools` Recommends `wireguard` metapackage which depends on `linux-image-rt-amd64`. Without `install_recommends: false`, apt pulled in a 70MB kernel image that filled the 1GB container disk (No space left on device).

## IP Query Pattern for Multi-Node Services

17. Pattern for querying actual IP in verify tasks:
   ```yaml
   - name: Get container IP from Proxmox config
     ansible.builtin.shell:
       cmd: |
         set -o pipefail
         pct config {{ ct_id }} | grep -oP 'ip=\K[^/,]+'
     executable: /bin/bash
     register: _ct_ip_query
     changed_when: false
   ```

18. Verify tasks MUST query the actual container IP from `pct config` instead of recomputing it. This avoids index drift between scenarios.
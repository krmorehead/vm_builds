---
name: lxc-container-patterns
description: LXC container provisioning and configuration patterns. Use when creating LXC services, managing container networking, or handling LXC template operations.
---

# LXC Container Patterns

## When to Test (Proactive Testing Triggers)

**Test IMMEDIATELY when:**
- Creating new LXC container services
- Adding Docker-in-LXC functionality  
- Modifying container networking or provisioning
- Using `pct exec` commands in configuration
- Encountering variable scoping issues with containers
- Any container-related Ansible code changes

**Previous lesson**: We should have tested after creating the `homeassistant_configure` role instead of debugging blind. Always test after container provisioning code changes.

**Environment validation for LXC work:**
```bash
set -a && source test.env && set +a
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST "pct list"
ansible home -m ping
```

## Load Relevant Skills Proactively

When working with LXC containers, load these skills IMMEDIATELY:
- `lxc-container-patterns` (this skill)
- `molecule-testing` for test patterns
- `ansible-conventions` for coding standards
- `proxmox-safety-rules` for Proxmox operations

## LXC Provisioning Pattern

1. LXC containers use the shared `proxmox_lxc` role via `include_role`. Each service's `<type>_lxc` role is a thin wrapper:

   ```yaml
   # roles/pihole_lxc/tasks/main.yml
   ---
   - name: Provision Pi-hole container
     ansible.builtin.include_role:
       name: proxmox_lxc
     vars:
       lxc_ct_id: "{{ pihole_ct_id }}"
       lxc_ct_hostname: pihole
       lxc_ct_dynamic_group: pihole
       lxc_ct_memory: 256
       lxc_ct_cores: 1
       lxc_ct_disk: "4"
       lxc_ct_onboot: true
       lxc_ct_startup_order: 3
   ```

2. The `proxmox_lxc` role handles: template upload, `pct create`, networking, device bind mounts, auto-start, container start, readiness wait, and `add_host` registration.

3. The "Start container" task MUST start containers that exist but are stopped, not only newly created containers. Use `'stopped' in (_lxc_status.stdout | default(''))` as an additional condition alongside `not lxc_exists`. Previous bug: `molecule converge` (idempotent re-run) found kiosk containers in "stopped" state on ai/mesh2. The start task was gated only on `not lxc_exists`, so it skipped the start. "Wait for container init" then failed with "container '401' not running!".

## LXC Readiness and Connection

3. For readiness: use `ls /` not `hostname` (BusyBox containers may lack it). For OpenWrt LXC: use `--ostype unmanaged`.

4. Configure plays target the dynamic group populated by `add_host`. The connection uses `community.proxmox.proxmox_pct_remote` which SSHes to the Proxmox host and runs `pct exec` inside the container.

5. For OpenWrt containers (no Python), use `ansible.builtin.raw` with commands wrapped in `/bin/sh -c '...'`.

## Container Networking Requirements

6. LXC container networking MUST match the host's actual topology. Hosts behind OpenWrt (`router_nodes`, `lan_hosts`) use the OpenWrt LAN subnet. Hosts directly on WAN use `ansible_default_ipv4.gateway/prefix`.

7. NEVER hardcode all containers to the OpenWrt LAN subnet.

## Bridge Allocation Patterns

8. Different VM/container types consume bridges differently:
   - **Router VMs** (OpenWrt): ALL bridges — WAN on `net0`, remaining as LAN ports
   - **Service VMs**: typically ONE LAN bridge — `proxmox_all_bridges[1]` (first non-WAN)
   - **LXC containers on LAN hosts**: `proxmox_all_bridges[1]` (LAN bridge)
   - **LXC containers on WAN hosts**: `proxmox_wan_bridge` — NEVER use `proxmox_all_bridges[1]`

## Template Management

9. Templates are stored locally in `images/` and uploaded to the Proxmox host during provisioning. NEVER use `pveam download` — the host may not have internet access.

10. Previous bug: `pveam download` failed with template name mismatch. Switching to local hosting eliminated the dependency.

## Upload Pattern for LXC Templates

11. ```yaml
    - name: Upload LXC template to Proxmox
      ansible.builtin.copy:
        src: "{{ role_path }}/../../{{ lxc_ct_template_path }}"
        dest: "/var/lib/vz/template/cache/{{ lxc_ct_template }}"
        mode: "0644"
    ```

12. ALWAYS use `role_path` for the source path. Relative paths like `../../images/...` break when Molecule runs from non-default scenarios.

## LXC Package Management

13. ALWAYS use `install_recommends: false` when installing packages in LXC containers. Many packages Recommend kernel-related metapackages that pull in 70+ MB kernel images.

14. Previous bug: `wireguard-tools` Recommends `wireguard` metapackage which depends on `linux-image-rt-amd64`. Without `install_recommends: false`, apt pulled in a 70MB kernel image that filled the 1GB container disk.

## LXC Disk Sizing Requirements

15. LXC templates compress well (~5:1 for Debian). ALWAYS verify that the rootfs disk is large enough for the EXTRACTED template, not just the compressed size.

16. Rule of thumb: Set disk to at least 2× the compressed template size, minimum 2 GB for any Debian-based container with monitoring or database services.

17. Previous bug: Netdata template was 314MB compressed but expanded to ~1013MB. 1GB rootfs caused `pct create` to fail mid-extraction.

## Per-Host IP Indexing

18. When a service deploys to multiple LAN hosts, each container needs a unique IP on the shared OpenWrt LAN. Use the host's index in its flavor group: `offset + groups['<flavor>'].index(inventory_hostname)`.

19. WAN hosts use private NAT subnets (10.99.{container_subnet_id}.0/24) via `vmbr_ct` bridge. Container IPs use just the service offset (no group indexing). Collisions are impossible — each host has its own /24.

20. Previous bug: WAN containers shared the household subnet (192.168.86.x). The offset+base+index formula produced IP collisions with host IPs and household devices. Replaced with per-host NAT bridges.

## DNAT for Inbound Connections on NAT Containers

22a. WAN host containers live on private NAT subnets (10.99.x.x) that are
unreachable from the LAN or other hosts. When a container needs to accept
inbound connections from outside the host, add DNAT rules in the
provisioning role (`<type>_lxc/tasks/main.yml`):

```yaml
- name: Add DNAT rule for service port (WAN hosts)
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      WAN_IF="{{ proxmox_wan_bridge }}"
      CT_IP="{{ _lxc_net_ip }}"
      if ! iptables -t nat -C PREROUTING -i "$WAN_IF" -p tcp --dport 9001 \
           -j DNAT --to-destination "$CT_IP:9001" 2>/dev/null; then
        iptables -t nat -A PREROUTING -i "$WAN_IF" -p tcp --dport 9001 \
                 -j DNAT --to-destination "$CT_IP:9001"
      fi
    executable: /bin/bash
  when: not (_lxc_net_on_lan | bool)
  changed_when: true

- name: Add FORWARD rule for DNAT traffic (WAN hosts)
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      CT_IP="{{ _lxc_net_ip }}"
      if ! iptables -C FORWARD -d "$CT_IP" -p tcp --dport 9001 \
           -j ACCEPT 2>/dev/null; then
        iptables -A FORWARD -d "$CT_IP" -p tcp --dport 9001 -j ACCEPT
      fi
    executable: /bin/bash
  when: not (_lxc_net_on_lan | bool)
  changed_when: true
```

Key points:
- Gate on `not (_lxc_net_on_lan | bool)` — LAN containers are directly
  reachable and don't need DNAT.
- Use `-C` (check) before `-A` (append) for idempotency.
- BOTH `PREROUTING DNAT` and `FORWARD ACCEPT` rules are needed.
- Services currently using DNAT: Manager API (port 9001, kiosk_lxc),
  WireGuard (UDP 51820, wireguard_lxc).

Previous bug (2026-04-09): Cluster Manager on home (LAN) couldn't reach
child Managers on WAN hosts (ai, mesh2, bridge-1, bridge-2). Their kiosk
containers were on 10.99.x.19 — unreachable from the LAN. DNAT rules
forwarding port 9001 from the host IP to the container IP fixed it.

## Dynamic Inventory and Failed Hosts

21. ALWAYS use `ansible_play_hosts` (not `ansible_play_hosts_all`) in `add_host` loops that register containers. `ansible_play_hosts_all` includes hosts that failed in earlier plays, leading to phantom container registrations for containers that were never created.

22. Previous bug: `ai` failed in infrastructure (clock skew). The `add_host` loop used `ansible_play_hosts_all`, registering `wireguard-ai` even though no container existed. The configure play then failed with UNREACHABLE.

## Container PID Retrieval

23. NEVER use `pct pid` — it does not exist in Proxmox VE. Use `lxc-info -n <vmid>` and parse the PID line with `awk '/^PID:/{print $2}'`.

24. Previous bug: `openwrt_mesh_lxc` used `pct pid 103` to get the container PID for WiFi PHY namespace move. It failed with "ERROR: unknown command 'pct pid'".

## pct_remote Connection Requirements

25. The `community.proxmox.proxmox_pct_remote` connection plugin requires the `paramiko` Python package on the controller. ALWAYS add `paramiko` to `requirements.txt` when using `pct_remote`.

26. Previous bug: `ModuleNotFoundError: No module named 'paramiko'` at runtime because the dependency wasn't in `requirements.txt`.

## pct_remote gather_facts Buffer Overflow

38. NEVER use `gather_facts: true` on plays targeting `pct_remote` dynamic groups unless the configure role actually references `ansible_*` facts. The `pct_remote` connection plugin has a limited JSON output buffer. Hosts with many block devices (LVM thin pools, loop devices, device-mapper entries) produce `ansible_devices` output that exceeds the buffer, causing "Module result deserialization failed: No end of json char found".

39. ALWAYS set `gather_facts: false` for `pct_remote` configure plays. None of the current configure roles (wireguard, pihole, rsyslog, netdata) use gathered facts — they only use role variables and facts from `set_fact`.

40. If a future role needs specific facts, use `gather_subset: ['!hardware']` to skip the device-heavy hardware facts instead of gathering all facts.

41. Previous bug: `wireguard-ai` failed during fact gathering via `pct_remote` because the `ai` host (AMD Ryzen, 128GB SSD with LVM thin pool) had 15+ block devices. The full setup module JSON exceeded the `pct_remote` buffer. Other hosts with fewer devices succeeded. Fix: set `gather_facts: false` for all pct_remote configure plays.

## SSH Connection Exhaustion with Parallel pct_remote

42. When multiple `pct_remote` containers run in parallel, each task opens a NEW SSH connection to the Proxmox host. With 4+ containers × N tasks, this causes rapid SSH churn that overwhelms the SSH server, resulting in "Connection reset by peer", "Broken pipe", and "SSH session not active" errors.

43. ALWAYS add `serial: 2` to configure plays that target 4+ `pct_remote` containers. This processes containers in batches of 2, reducing simultaneous SSH connections.

44. Previous bug: WireGuard configure play ran 4 containers in parallel (wireguard-home, wireguard-mesh1, wireguard-ai, wireguard-mesh2). Tasks like `wg genkey | wg pubkey` opened 4+ SSH connections through `home`'s SSH server simultaneously. Two connections dropped with "Connection reset by peer (104)" and "Broken pipe". Fix: added `serial: 2` to the WireGuard configure play.

## pct_remote Intermittent Hangs

55. The `community.proxmox.proxmox_pct_remote` connection plugin uses paramiko for SSH, which can hang indefinitely during connection setup — even with `serial: 1`. Adding `timeout = 60` to `ansible.cfg` and `host_key_auto_add = True` under `[paramiko_connection]` does NOT prevent the hang.

56. When a configure play only checks service health (no container-internal configuration), BYPASS `pct_remote` entirely. Target the Proxmox HOST group and use `ansible.builtin.command` with `pct exec` directly:

    ```yaml
    - name: Configure Netdata
      hosts: monitoring_nodes
      gather_facts: false
      tasks:
        - name: Check netdata service health
          ansible.builtin.command:
            cmd: pct exec {{ netdata_ct_id }} -- systemctl is-active netdata
          register: _check
          changed_when: false
          failed_when: false
          retries: 10
          delay: 3
          until: _check.stdout | trim == 'active'
    ```

57. Previous bug: `Configure Netdata` play targeted `netdata` dynamic group with `pct_remote`. Hung intermittently on `Detect netdata config directory` task (0% CPU, blocked on paramiko SSH handshake). Adding `serial: 1` and paramiko config changes did not fix it. Fix: rewrote to target `monitoring_nodes` directly using `pct exec`.

## Device Passthrough in LXC Containers

45. For device passthrough (`/dev/dri`, `/dev/snd`, `/dev/input`) to LXC containers, use `lxc.mount.entry` directives directly in the container config file (`/etc/pve/lxc/<CTID>.conf`), NOT Proxmox's `-mp` mount option. The `-mp` pre-start hook rejects device directories in unprivileged containers (exit code 32).

46. Use privileged containers (`unprivileged: false`) with `nesting=1` for reliable device access. Add both `lxc.mount.entry` bind mounts AND `lxc.cgroup2.devices.allow` rules.

47. Apply device config atomically: stop container → append config → start container. Gate on whether the entries already exist to make tasks idempotent.

48. NEVER use `echo "..." >> /etc/pve/lxc/<CTID>.conf` to append to LXC config files. pmxcfs (Proxmox cluster filesystem) rejects in-place append with "File exists" error. ALWAYS use read-modify-write: `cp` to `/tmp`, modify there, `cp` back.

49. Previous bug: `lxc_device_passthrough.yml` used `echo >> $lxc_conf` to append mount entries. This worked on some hosts but failed on ai and mesh2 with "File exists" because pmxcfs doesn't support POSIX append. Fix: copy to /tmp, append there, copy back.

50. Previous bug: Jellyfin and Kodi containers failed with `lxc.hook.pre-start` exit code 32 when using Proxmox `-mp` mounts for `/dev/dri`. Fix: switched to privileged containers with `lxc.mount.entry` directives appended directly to the LXC config file.

## VA-API in Headless LXC Containers

52. ALWAYS use `vainfo --display drm` when running vainfo inside headless LXC containers. Plain `vainfo` tries to connect to an X server (DISPLAY env var), which doesn't exist in headless containers. The `--display drm` flag uses the DRM render node directly.

53. Previous bug: `jellyfin_configure` ran `pct exec {{ ct_id }} -- vainfo` without `--display drm`. Failed with "error: can't connect to X server!" on every run. Fix: added `--display drm` and `failed_when: false`.

## File Deployment via copy + pct push

49. NEVER use heredocs inside `pct exec -- bash -c 'cat > file << EOF ... EOF'` in Ansible `command` or `shell` modules. YAML string folding destroys heredoc structure, causing bash syntax errors ("unexpected token `<'").

50. ALWAYS use the `copy` + `pct push` pattern for deploying config files into containers:

    ```yaml
    - name: Write config to host temp
      ansible.builtin.copy:
        content: |
          <?xml version="1.0" encoding="UTF-8"?>
          <Config>
            <Setting>{{ variable }}</Setting>
          </Config>
        dest: /tmp/service_config.xml
        mode: "0644"

    - name: Push config into container
      ansible.builtin.command:
        cmd: pct push {{ ct_id }} /tmp/service_config.xml /etc/service/config.xml
      changed_when: true

    - name: Clean up host temp
      ansible.builtin.file:
        path: /tmp/service_config.xml
        state: absent
    ```

51. Previous bug: `pct exec {{ ct_id }} -- bash -c 'cat > /etc/jellyfin/network.xml << "NETWORK_EOF" ...'` via `ansible.builtin.command` with `>-` folded scalar collapsed newlines. Bash received `cat > file << "EOF" content EOF` on a single line and failed with `syntax error near unexpected token '<'`. Fix: use `ansible.builtin.copy` to write to host temp, then `pct push` into container.

## Configure Play Target Rules

54. **Non-Docker LXC configure plays** MUST target the dynamic group (e.g., `netdata`, `pihole`, `rsyslog`) with `pct_remote` connection. The `pct_remote` plugin handles all commands inside the container transparently.

55. Previous bug: `netdata_configure` targeted `monitoring_nodes` (Proxmox hosts) instead of the `netdata` dynamic group. All commands (`command`, `template`, `systemd`) ran on the HOST, not inside the container. The role checked `/etc/netdata` on the Proxmox host (doesn't exist) and failed. Fix: changed `site.yml` to `hosts: netdata`.

## Docker-in-LXC Configuration Patterns

27. **Docker-in-LXC configure play target**: Configure plays for Docker-in-LXC services MUST target the Proxmox HOST group (e.g., `service_nodes`), NOT the container dynamic group (e.g., `homeassistant`). The role uses `pct exec` commands which only exist on the Proxmox host.

28. **Container ID handling**: Use `homeassistant_ct_id` from `group_vars/all.yml`. NEVER reference undefined `proxmox_vmid` variable in configure roles that run on the host.

29. **File deployment in containers**: Use `pct exec -- bash -c 'cat > file << "EOF" ... EOF'` (heredoc) instead of chained `echo` commands. This preserves YAML structure and avoids quoting nightmares:

    ```yaml
    - name: Create config file in container
      ansible.builtin.shell:
        cmd: |
          set -o pipefail
          pct exec {{ ct_id }} -- bash -c 'cat > /path/config.yml << "CONFIG_EOF"
          key: value
          nested:
            item: data
          CONFIG_EOF
          chmod 0644 /path/config.yml'
        executable: /bin/bash
      changed_when: true
    ```

30. **LXC nesting requirements**: Docker-in-LXC requires `nesting=1` feature. Pass via `lxc_ct_features: ["nesting=1"]` to `proxmox_lxc`. Configure Docker daemon with `cgroupfs` driver for cgroup v2 compatibility (bake into image).

31. **Jinja2 conflicts with Docker templates**: Docker Go template syntax (`{{.Repository}}`) uses the same delimiters as Jinja2. In Ansible tasks, escape with `{{ "{{" }}` and `{{ "}}" }}`, or avoid Docker `--format` flags entirely (use `docker image inspect` instead).

32. Previous bug: Configure play targeted `homeassistant` group (pct_remote connection), but the role used `pct exec` commands. `pct` only exists on the Proxmox host, not inside the container. Fix: change site.yml to target `service_nodes` (host).

33. Previous bug: Docker `--format "{{.Repository}}:{{.Tag}}"` in verify.yml was interpreted by Ansible's Jinja2 engine as undefined variables. Fix: use `docker image inspect` instead of `--format`.

## Dynamic Host Fact Detection in Per-Feature Scenarios

52. Configure roles that need host-level facts (e.g., render device GID for iGPU) MUST detect them dynamically from the host instead of relying on cached facts from infrastructure roles. Per-feature molecule scenarios don't run infrastructure plays, so facts like `igpu_render_gid` are undefined.

53. Pattern: use `stat -c '%g' /dev/dri/renderD*` to detect the render GID at runtime:

    ```yaml
    - name: Detect render device GID from host
      ansible.builtin.shell:
        cmd: |
          set -o pipefail
          stat -c '%g' /dev/dri/renderD* 2>/dev/null | head -1
        executable: /bin/bash
      register: _render_gid
      changed_when: false
      failed_when: _render_gid.stdout | trim | length == 0
    ```

54. Previous bug: `moonlight_configure` referenced `igpu_render_gid` (exported by `proxmox_igpu`). The per-feature scenario didn't run infrastructure plays, so the variable was undefined. Fix: detect GID dynamically with `stat`.

## Configure Role Connection Decision

34. When a configure role needs facts from the Proxmox host (e.g., `igpu_render_gid`, `igpu_render_device` from `proxmox_igpu`), it MUST target the Proxmox host group (`media_nodes`, `service_nodes`) and use `pct exec` commands. The `pct_remote` connection cannot access host-side facts because the dynamic group hosts are separate inventory entries.

35. When a configure role only needs container-internal state (e.g., pihole, netdata, rsyslog), it should target the dynamic group with `pct_remote` and use direct commands without `pct exec` wrapping.

36. Previous bug: Jellyfin configure play targeted `hosts: jellyfin` (dynamic group with `pct_remote`) but the role used `pct exec {{ jellyfin_ct_id }} --` wrapping on every command. The `pct_remote` plugin already wraps commands in `pct exec`, causing double wrapping: `pct exec 300 -- pct exec 300 -- systemctl ...`. Fix: change site.yml to target `media_nodes` (Proxmox hosts) since the role needs host-side igpu facts.

## Pi-hole v6 Configuration

33. Pi-hole v6 uses `/etc/pihole/pihole.toml` (NOT `setupVars.conf` or `pihole-FTL.conf`). Use `/usr/bin/pihole-FTL --config <key> <value>` for programmatic configuration.

34. Pi-hole v6 uses FTL's embedded web server (NOT lighttpd). Do not manage `lighttpd.service`.

35. ALWAYS run `pihole -g` (gravity update) BEFORE switching the container's `resolv.conf` to `127.0.0.1`. Gravity downloads blocklists from the internet, which needs working DNS.

36. For unattended install, pre-seed `/etc/pihole/pihole.toml` with at least `dns.upstreams` before running the installer. Use `PIHOLE_SKIP_OS_CHECK=true` in LXC containers.

37. Previous bug: Configure role set `resolv.conf` to `127.0.0.1`, then ran `pihole -g`. Gravity hung because FTL's DNS wasn't fully initialized.

## Display-Exclusive Hookscript and DRI-Sharing Containers

38. When the Desktop VM holds the iGPU via PCI passthrough (`--hostpci0`), DRI devices (`/dev/dri/renderD*`) are not available on the Proxmox host. Containers that bind-mount DRI devices (Kodi, Kiosk, Moonlight, Jellyfin) lose GPU access. Graphical services (Kodi) cannot start and report `inactive`.

39. Configure roles for DRI-sharing containers MUST include a defensive "ensure container is running" guard: check `pct status`, start if stopped, wait for readiness via `pct exec -- ls /`. This handles re-runs where hookscripts have already stopped the container.

40. Verify assertions for DRI-sharing services MUST skip `systemctl is-active` checks when the Desktop VM is running. The service legitimately can't start without GPU access.

41. When detecting render group GID from `/dev/dri/renderD*`, use `failed_when: false` and fall back to a cached `igpu_render_gid` fact from `proxmox_igpu`. The Desktop VM holding the iGPU makes the DRI device unavailable on the host.

42. Previous bug: `kiosk_configure` ran `stat -c '%g' /dev/dri/renderD*` but the Desktop VM held the iGPU via passthrough. No DRI devices existed on the host. Fix: made detection non-fatal with fallback to cached fact.
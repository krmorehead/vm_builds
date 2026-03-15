---
name: lxc-container-patterns
description: LXC container provisioning and configuration patterns. Use when creating LXC services, managing container networking, or handling LXC template operations.
---

# LXC Container Patterns

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

18. When a service deploys to multiple Proxmox hosts, each container needs a unique IP. Use the host's index in its flavor group: `offset + groups['<flavor>'].index(inventory_hostname)`.

19. WAN IPs add +200 to the base offset. ALWAYS check that WAN IPs don't collide with any host's management IP.

20. Previous bug: rsyslog_ct_ip_offset=11 produced WAN IP .211, colliding with mesh2's host IP.

## Dynamic Inventory and Failed Hosts

21. ALWAYS use `ansible_play_hosts` (not `ansible_play_hosts_all`) in `add_host` loops that register containers. `ansible_play_hosts_all` includes hosts that failed in earlier plays, leading to phantom container registrations for containers that were never created.

22. Previous bug: `ai` failed in infrastructure (clock skew). The `add_host` loop used `ansible_play_hosts_all`, registering `wireguard-ai` even though no container existed. The configure play then failed with UNREACHABLE.

## Container PID Retrieval

23. NEVER use `pct pid` — it does not exist in Proxmox VE. Use `lxc-info -n <vmid>` and parse the PID line with `awk '/^PID:/{print $2}'`.

24. Previous bug: `openwrt_mesh_lxc` used `pct pid 103` to get the container PID for WiFi PHY namespace move. It failed with "ERROR: unknown command 'pct pid'".

## pct_remote Connection Requirements

25. The `community.proxmox.proxmox_pct_remote` connection plugin requires the `paramiko` Python package on the controller. ALWAYS add `paramiko` to `requirements.txt` when using `pct_remote`.

26. Previous bug: `ModuleNotFoundError: No module named 'paramiko'` at runtime because the dependency wasn't in `requirements.txt`.

## Pi-hole v6 Configuration

27. Pi-hole v6 uses `/etc/pihole/pihole.toml` (NOT `setupVars.conf` or `pihole-FTL.conf`). Use `/usr/bin/pihole-FTL --config <key> <value>` for programmatic configuration.

28. Pi-hole v6 uses FTL's embedded web server (NOT lighttpd). Do not manage `lighttpd.service`.

29. ALWAYS run `pihole -g` (gravity update) BEFORE switching the container's `resolv.conf` to `127.0.0.1`. Gravity downloads blocklists from the internet, which needs working DNS.

30. For unattended install, pre-seed `/etc/pihole/pihole.toml` with at least `dns.upstreams` before running the installer. Use `PIHOLE_SKIP_OS_CHECK=true` in LXC containers.

31. Previous bug: Configure role set `resolv.conf` to `127.0.0.1`, then ran `pihole -g`. Gravity hung because FTL's DNS wasn't fully initialized.
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Parallel image building** -- `build-images.sh --parallel` distributes image
  builds across multiple Proxmox hosts concurrently. Uses round-robin assignment
  with automatic host discovery from env vars (`PRIMARY_HOST`, `AI_HOST`,
  `MESH_2_HOST`). Local-only targets (mesh, router) build on the controller.
  Override hosts with `--hosts <ip1,ip2,...>`. Reduces total build time from
  serial (~15 min) to parallel (~5 min with 3 hosts).
- **Per-feature unit test `prepare.yml`** -- Each per-feature molecule scenario
  (`molecule/<service>-lxc/`) now includes a `prepare.yml` that builds the
  service image if not cached. Unit tests are fully self-contained: prepare →
  converge → verify → cleanup. See `molecule/UNIT_TEST_PATTERN.md`.
- **E2E image pre-flight check** -- `molecule/default/prepare.yml` asserts all
  required images exist before E2E integration tests run, failing with clear
  build instructions if any are missing.

### Changed

- **E2E verify.yml slimmed by 35%** -- `molecule/default/verify.yml` now focuses
  on infrastructure health, basic service liveness, cross-service integration,
  and deploy stamps. Deep service-specific diagnostics (config validation,
  separation tests, leak tests, logrotate, VA-API profiles, KDE/GNOME shortcuts,
  etc.) moved to per-feature unit test verify files. Two-tier testing: unit tests
  for depth, E2E for integration breadth.
- **Molecule test sequences updated** -- All per-feature scenarios include
  `prepare` step after `cleanup`. Default scenario includes `prepare` before
  `converge`. Consistent pattern across all 13+ scenarios.

- **Debian Desktop VM** -- `desktop_vm` and `desktop_configure` roles deploy
  a Debian 12 VM (VMID 400) with both KDE Plasma (Windows-style UX) and GNOME
  (Mac-style UX) desktop sessions. Users select their preferred UX at the SDDM
  login screen; both sessions share the same home directory. Exclusive iGPU
  passthrough via hostpci0 (UEFI/OVMF, q35). Custom baked image via
  `build-images.sh --only desktop` (KDE + GNOME + apps pre-installed).
  Vendor-specific GPU drivers (Intel or AMD) at configure time. Shared
  cross-session shortcuts (Ctrl+Shift+4 for screenshot via Flameshot).
  On-demand service (no auto-start). Per-feature molecule scenario
  (`molecule/desktop-vm/`). Display-exclusive hookscript attachment.
- **Gaming LXC container (Fedora)** -- `gaming_lxc` and
  `gaming_lxc_configure` roles deploy a Fedora-based gaming container
  (VMID 601) with Sunshine game streaming server + dsda-doom. Uses GPU render
  device sharing (NOT PCI passthrough) -- safe on AMD APU single-GPU hosts.
  Fedora 41 base with Mesa VA-API freeworld drivers (H.264/HEVC encode),
  Vulkan, headless Xorg, PipeWire audio, GameMode. Replaces the Windows
  gaming VM as the active gaming path. Per-feature molecule scenario
  (`molecule/gaming-lxc/`) targeting `ai`. IP offset 18 on WAN subnet.
- **Moonlight streaming client LXC** -- `moonlight_lxc` and
  `moonlight_configure` roles deploy a Moonlight embedded game streaming
  client as an LXC container (VMID 302). Custom Debian 12 image with
  `moonlight-embedded` and VA-API drivers (Intel + AMD) baked in. Device
  passthrough for iGPU (`/dev/dri`) and USB input (`/dev/input`,
  `/dev/uinput`). On-demand container (`onboot: false`) for display-exclusive
  game streaming from a Sunshine server. Per-feature molecule scenario
  (`molecule/moonlight-lxc/`) for fast iteration. IP offset 17 on the
  OpenWrt LAN subnet.
- **GPU passthrough hookscript** -- Generalized Proxmox hookscript
  (`gpu-passthrough-hook.sh`) that manages GPU lifecycle at VM start/stop.
  `pre-start` discovers hostpci devices from VM config, stops GPU-consuming
  LXC containers, suspends conflicting VMs, unbinds GPU from native driver
  via sysfs, and binds to vfio-pci. `post-stop` reverses the operation and
  restarts stopped consumers. Works with any GPU vendor (Intel, AMD, NVIDIA).
  Enables single-GPU passthrough on hosts that previously hard-failed (e.g.,
  `ai` with sole AMD Raven Ridge iGPU).
- **AMD APU GPU passthrough limitation documented** -- AMD APU iGPUs
  (Raven Ridge, etc.) share the SoC die with the CPU. ANY GPU unbind path
  (sysfs OR modprobe -r) triggers a GPU reset that hangs the entire NBIO,
  crashing the system. `ai` is excluded from `gaming_nodes`. Only Intel
  iGPUs and discrete GPUs support the hookscript approach.
- **Host recoverability tracking** -- `wol_capable` host variable (true/false)
  declared in every host_vars file. Tracks whether a host can be remotely
  recovered via Wake-on-LAN after a crash or shutdown. USB-only ethernet hosts
  (e.g., `ai`) are marked `wol_capable: false`. Unit tests (`tests/test_wol.py`)
  enforce that non-WoL hosts are excluded from `scripts/wol.sh`. E2E verify
  asserts every host declares `wol_capable` and non-WoL hosts don't appear
  in the WoL script.

- **Sunshine API health check in verify** -- Gaming LXC per-feature and E2E
  verify playbooks now assert that Sunshine's web UI (HTTPS port 47990) is
  reachable from the Proxmox host, with retry logic for startup delays.
  Confirms the game streaming server is actually serving, not just that the
  systemd unit is active.
- **Dynamic GPU device detection** -- All verify and configure tasks now
  detect `/dev/dri/card*` and `/dev/dri/renderD*` dynamically via sysfs
  instead of hardcoding `card0` or `renderD128`. Fixes false failures on
  AMD APU hosts where the card device is `card1`.
- **Sunshine streaming server** -- Windows 11 VM (VMID 600, 4096 MB RAM,
  64 GB disk) with iGPU PCI passthrough running Sunshine as a Moonlight
  streaming host. Uses vfio-pci to bind the host iGPU exclusively to the VM
  for hardware encoding. Pre-built image via `build-images.sh --only sunshine`
  using Tiny11 + unattended install with Sunshine, GZDoom, and Freedoom baked
  in. `gaming_vm` role provisions the VM (q35/OVMF, QEMU Guest Agent, IP
  discovery via `add_host`); `gaming_configure` role sets Sunshine credentials
  via web API and verifies game installation. Per-feature molecule scenario
  (`sunshine-vm`), rollback tag (`gaming-rollback`), opt-in deployment via
  `--tags gaming`, and reusable group reconstruction
  (`tasks/reconstruct_gaming_group.yml`).
- **Kodi media player** -- LXC container (VMID 301, 1024 MB RAM, 4 GB disk)
  running Kodi as a local media player and home theater frontend. Renders
  directly to the physical display via GBM/DRM using the shared iGPU. HDMI
  audio output via ALSA bind mount. Custom Debian 12 template with
  kodi-standalone, kodi-gbm, Mesa VA-API drivers (Intel + AMD), and libcec
  pre-installed (built by `build-images.sh`). `kodi_lxc` role provisions the
  container with device mounts (`/dev/dri`, `/dev/snd`, `/dev/input`) and
  cgroup allowlists (DRI, sound, input); `kodi_configure` role configures
  iGPU render group, ALSA HDMI audio, and web interface. On-demand container
  (`onboot: false`). Per-feature molecule scenario (`kodi-lxc`), rollback tag
  (`kodi-rollback`), full integration testing, and reusable group
  reconstruction (`tasks/reconstruct_kodi_group.yml`).
- **Jellyfin media server** -- LXC container (VMID 300, 2048 MB RAM, 8 GB disk)
  running Jellyfin with Intel Quick Sync hardware transcoding via iGPU device
  passthrough. Custom Debian 12 template with Jellyfin server, web interface,
  FFmpeg, and VA-API drivers for both Intel (`intel-media-va-driver`) and AMD
  (`mesa-va-drivers`) hardware acceleration pre-installed (built by
  `build-images.sh`). `jellyfin_lxc` role provisions the container with iGPU
  device mounting (`/dev/dri/renderD128`) and media path mounting
  (`/mnt/media` → `/media`); `jellyfin_configure` role configures admin user,
  iGPU render group access, transcoding settings, and media libraries. Supports
  hardware-accelerated transcoding with automatic software fallback when iGPU
  unavailable. Per-feature molecule scenario (`jellyfin-lxc`), rollback tag
  (`jellyfin-rollback`), full integration testing, and reusable group
  reconstruction (`tasks/reconstruct_jellyfin_group.yml`).
- **Wake-on-LAN utility** (`wol.sh`) -- recovery script for remotely waking
  Proxmox hosts after power outages. Supports WAN hosts via local L2 broadcast
  and LAN hosts via proxied WoL through the primary host (Python 3 socket,
  no extra packages on remote). Includes `--wait` flag for SSH polling,
  `--list` for host inventory, and `all` to wake the entire fleet. Known host
  MACs and IPs are hardcoded; env-sourced `PRIMARY_HOST` for LAN proxy path.
- **Netdata monitoring agent** -- LXC container (VMID 500, 128 MB RAM, 2 GB
  disk) running Netdata for host-level monitoring with CPU, memory, disk, and
  temperature metrics via bind-mounted `/proc` and `/sys`. Custom Debian 12
  template with Netdata pre-installed and pre-configured for dbengine retention
  (1 hour), dashboard on port 19999, and `/host/proc`+`/host/sys` paths
  (built by `build-images.sh`). Container runs privileged with `nesting=1`
  feature and a systemd drop-in override for LXC compatibility.
  `netdata_lxc` role provisions the container with topology-aware networking
  and read-only bind mounts for host metrics; `netdata_configure` role
  deploys optional child-parent streaming config when
  `NETDATA_PARENT_IP` and `NETDATA_STREAM_API_KEY` are set (soft WireGuard
  dependency). Per-feature molecule scenario (`netdata-lxc`), rollback tag
  (`netdata-rollback`), and reusable group reconstruction
  (`tasks/reconstruct_netdata_group.yml`).
- **WireGuard custom image** -- `build-images.sh` now builds a custom WireGuard
  LXC template with `wireguard-tools`, `iptables`, and `iptables-persistent`
  pre-installed. Eliminates the last runtime package installation in the project,
  fully aligning with "bake, don't configure at runtime." The
  `wireguard_configure` role no longer installs any packages -- it only applies
  host-specific tunnel configuration (keys, endpoints, NAT rules).
- **Selective image builds** -- `build-images.sh` now supports `--only <target>`
  to rebuild a single image instead of all six. Targets: `mesh`, `router`,
  `pihole`, `rsyslog`, `netdata`, `wireguard`. Reduces rebuild time from
  ~15 min to ~2-3 min.
- **rsyslog centralized logging** -- LXC container (VMID 501, 64 MB RAM, 1 GB
  disk) running rsyslog as a centralized log collector. Custom Debian 12
  template with rsyslog pre-configured for TCP reception on port 514, disk-
  assisted spooling, RFC 1918 sender restriction, per-hostname log separation,
  and logrotate (built by `build-images.sh`). `rsyslog_lxc` role provisions the
  container with topology-aware networking (LAN or WAN bridge depending on host
  position); `rsyslog_configure` role deploys optional forwarding rules when
  `RSYSLOG_HOME_SERVER` is set (disk-assisted queue survives WireGuard tunnel
  outages). `openwrt_configure/tasks/syslog.yml` configures OpenWrt to forward
  system logs to the rsyslog container via UCI. Per-feature molecule scenarios
  (`rsyslog-lxc`, `openwrt-syslog`), rollback tags (`rsyslog-rollback`,
  `openwrt-syslog-rollback`), and reusable group reconstruction
  (`tasks/reconstruct_rsyslog_group.yml`).
- **Pi-hole DNS filtering** -- LXC container (VMID 102, 256 MB RAM, 2 GB disk)
  running Pi-hole for network-wide DNS-level ad and tracker blocking. Custom
  Debian 12 template with Pi-hole pre-installed (built by `build-images.sh`).
  `pihole_lxc` role provisions the container on the LAN subnet; `pihole_configure`
  role applies host-specific settings (web password, upstream DNS, gravity
  update). `openwrt_configure/tasks/pihole_dns.yml` configures OpenWrt dnsmasq
  to forward DNS to Pi-hole with https-dns-proxy as fallback. Per-feature
  molecule scenarios (`pihole-lxc`, `openwrt-pihole-dns`), rollback tags
  (`pihole-rollback`, `openwrt-pihole-dns-rollback`), and reusable group
  reconstruction (`tasks/reconstruct_pihole_group.yml`).
- **WireGuard VPN client** -- first LXC container in the project. Lightweight
  container (VMID 101, 128 MB RAM, 1 GB disk) running a WireGuard client
  with persistent tunnel, IP forwarding, and iptables NAT/MASQUERADE. All
  credentials optional with auto-generation fallback via `.env.generated`.
  `wireguard_lxc` role provisions the container and loads the host kernel
  module; `wireguard_configure` role templates wg0.conf and configures
  tunnel networking (keys, endpoints, NAT rules). Per-feature molecule scenario
  (`wireguard-lxc`), rollback tag (`wireguard-rollback`), and reusable
  group reconstruction (`tasks/reconstruct_wireguard_group.yml`).
- **OpenWrt security hardening** (M1) -- root password, SSH key-only auth,
  banIP intrusion prevention, SYN flood protection, invalid packet drop,
  LAN-only SSH access. All configurable via env vars (`OPENWRT_ROOT_PASSWORD`,
  `OPENWRT_SSH_PUBKEY`, `OPENWRT_SSH_PRIVATE_KEY`).
- **VLAN segmentation** (M2) -- IoT (VLAN 10, 10.10.20.0/24) and Guest
  (VLAN 20, 10.10.30.0/24) networks with per-VLAN firewall zones and DHCP
  pools. Uses 802.1Q on br-lan (virtual environment, not DSA/swconfig).
- **Encrypted DNS** (M3) -- `https-dns-proxy` for DNS-over-HTTPS upstream
  resolution. dnsmasq forwards to local DoH proxy. Rebinding protection
  enabled.
- **Mesh enhancements** (M4) -- Dawn 802.11k/v/r client steering with
  configurable RSSI threshold and steering mode. Mesh peer monitoring via
  cron. All conditional on WiFi hardware presence.
- **Per-feature rollback infrastructure** (M0) -- each feature has a
  dedicated rollback tag in `cleanup.yml` (`openwrt-<feature>-rollback`).
  `cleanup.sh rollback <feature>` subcommand for easy one-command revert.
- **Per-feature molecule scenarios** -- `openwrt-security`, `openwrt-vlans`,
  `openwrt-dns`, `openwrt-mesh` for fast (~30-60s) per-feature testing
  against the existing baseline.
- **Reusable group reconstruction** -- `tasks/reconstruct_openwrt_group.yml`
  discovers the OpenWrt VM's LAN IP, detects SSH auth method (key vs
  password), and registers the dynamic group. Used by all per-feature
  scenarios and rollback plays.
- **Baseline documentation** -- `docs/architecture/baseline.md` defines the
  reusable baseline state, invariants, and assertion coverage.
- **Backup manifest version** -- `project_version` field added to the
  backup manifest for version-aware restore decisions.
- **Pytest coverage** for rollback tag naming convention and pass-through.
- **Multi-node testing infrastructure** — `mesh1-infra` Molecule scenario for
  running shared infrastructure roles on a secondary Proxmox node behind the
  OpenWrt router. Includes `tasks/bootstrap_lan_host.yml` for DHCP lease and
  API token provisioning. `lan_hosts` inventory group with ProxyJump SSH config.
- **Scalable env var convention** — renamed `PROXMOX_HOST` → `PRIMARY_HOST`,
  `PROXMOX_API_TOKEN_SECRET` → `HOME_API_TOKEN`. API tokens follow
  `<HOSTNAME>_API_TOKEN` convention with dynamic lookup in `group_vars/proxmox.yml`.
- **Four-node test topology** — `home` (primary router), `mesh1` (LAN
  satellite via ProxyJump), `ai` and `mesh2` (WAN-connected). Topology-aware
  LXC container networking (WAN vs LAN subnet, bridge, DNS).

- **`proxmox_lxc` role** -- reusable LXC container provisioning with
  parameterized resources, networking, features, mount entries, auto-start,
  and dynamic group registration via `community.proxmox.proxmox_pct_remote`.
- **`proxmox_igpu` role** -- iGPU detection for Intel (i915) and AMD (amdgpu),
  vendor-specific VA-API driver installation, and fact export (`igpu_vendor`,
  `igpu_pci_address`, `igpu_render_device`, etc.) for containers/VMs needing
  GPU access. NTP clock sync before apt operations to prevent GPG failures.
- **OpenWrt Mesh LXC** -- `openwrt_mesh_lxc` role provisions a privileged
  OpenWrt LXC container on mesh satellite nodes (`wifi_nodes:!router_nodes`).
  WiFi PHY namespace move gives the container exclusive radio access without
  PCIe passthrough. Hookscript for persistence across reboots.
  `openwrt_mesh_configure` installs WiFi drivers, `wpad-mesh-openssl`, and
  configures 802.11s mesh interfaces. Gracefully skips hosts without WiFi.
- **Custom OpenWrt images** -- `build-images.sh` uses the OpenWrt Image
  Builder to create pre-configured images. Mesh LXC rootfs strips firewall/
  routing and pre-installs WiFi packages. Router VM image pre-installs
  mesh, security, DNS, and diagnostic packages. Custom images are required
  (roles hard-fail if missing). Eliminates EPERM/opkg failures in LXC
  containers and reduces converge time by ~2-3 minutes.
- **Self-hosted LXC templates** -- templates stored in `images/` and
  uploaded to Proxmox during provisioning (no external download needed).
- **VMID allocation scheme** -- 100-series network, 200-series services,
  300-series media, 400-series desktop, 500-series observability, 600-series
  gaming. Defined in `inventory/group_vars/all.yml`.
- **Flavor groups** -- `router_nodes`, `vpn_nodes`, `dns_nodes`,
  `wifi_nodes`, `monitoring_nodes`, `service_nodes`, `media_nodes`,
  `desktop_nodes`, `gaming_nodes` in inventory for build profile composition.
- **Build profiles documentation** (`docs/architecture/build-profiles.md`).
- **Auto-start configuration** -- `proxmox_startup_order` lookup table and
  `proxmox_ondemand_services` list in `inventory/group_vars/all.yml`.
- **Per-feature Molecule scenarios** -- `proxmox-lxc` and `proxmox-igpu`
  for fast, isolated testing of individual roles.
- **Proxmox repo management** -- enterprise repo disabling, no-subscription
  repo setup, DNS fallback for apt operations.
- **`build.py`** -- Python build script with playbook selection (`--playbook`),
  tag control (`--tags`, `--skip-tags`), host targeting (`--limit`), dry run
  (`--check`), and `.env` validation. Replaces `run.sh` for day-to-day use.
- **Deployment tracking** via `deploy_stamp` role -- records which plays ran
  on each Proxmox host with version and timestamp in `/etc/ansible/facts.d/`.
  Available as `ansible_local.vm_builds` on subsequent runs.
- **Device flavor groups** -- inventory uses child groups under `proxmox`
  (e.g., `router_nodes`) to control which VM types each host receives.
  Shared infrastructure runs on all `proxmox` hosts regardless of flavor.
- **`project_version`** variable in `inventory/group_vars/all.yml` as single source
  of truth for version tracking across deployments.
- **Unit tests** for `build.py` (`tests/test_build.py`, 32 tests covering
  env parsing, validation, playbook resolution, and command construction).
- **Deploy stamp assertions** in Molecule verify to validate tracking works.

### Changed

- **GPU cleanup uses sysfs-only** -- All GPU driver cleanup across E2E,
  per-feature, and production cleanup playbooks now uses sysfs unbind +
  driver_override clear + PCI rescan instead of `modprobe -r`. Safe on
  any host regardless of GPU count.
- **gaming_vm role uses hookscript** -- Removed inline vfio-pci binding
  from the role. The hookscript handles GPU bind/unbind at VM start/stop.
  Removed the single-GPU AMD hard-fail that blocked passthrough on `ai`.
- **Service-specific molecule cleanup** -- Replaced blanket `qm list` / `pct list`
  iteration with explicit VMIDs from `inventory/group_vars/all.yml`. Cleanup now only
  destroys known project VMs (100) and containers (101-103, 500-501) by checking
  existence first. Removed backup restore and made `update-initramfs` conditional
  on PCI passthrough config presence. Fixes template deletion on LAN hosts that
  was missed in the earlier caching optimization.
- **Verify phase performance** -- Batched `pct exec` calls per container into
  single SSH round trips (WireGuard 7→1, Netdata 4→1, rsyslog 6→1, Pi-hole
  3→1, Mesh WiFi 3→1). Eliminates ~43 SSH calls per run (~80+ with multi-host
  multiplier). Previously consolidated `pct config` calls (20→6) remain.
- **Converge timing** -- WireGuard configure role eliminated 4 `pct_remote`
  tasks (apt install × 2, mkdir, sysctl copy — now baked into image).
  Reduced OpenWrt restart pauses from 30s to 20s. Tightened OpenWrt VM SSH
  wait, container networking wait, WiFi detection, and apt retry delays.
- **Mesh WiFi wireless config generation** -- `openwrt_mesh_configure` now
  runs `wifi config` inside the container to generate `/etc/config/wireless`
  from detected hardware before modifying radio settings. Fixes `uci: Invalid
  argument` when the WiFi PHY was namespace-moved after boot (the wireless
  config was never auto-generated).
- Tagged all plays in `site.yml`: `backup`, `infra`, `openwrt`, `cleanup`.
  Use `--tags` to run specific plays or `--skip-tags` to exclude them.
- Split `site.yml` shared infrastructure into its own play, separate from
  VM-specific provisioning. Shared roles (`proxmox_bridges`,
  `proxmox_pci_passthrough`) target all `proxmox` hosts; OpenWrt provision
  targets `router_nodes` only.

### Fixed

- **Pi-hole DNS timeout behind OpenWrt** -- Changed Pi-hole upstream DNS from
  hardcoded external servers (1.1.1.1, 1.0.0.1) to auto-detected container
  gateway. Direct DNS queries from the LAN container to external servers were
  intermittently rejected ("connection refused"), while gateway DNS always
  worked. The gateway is auto-detected via `ip -4 route show default` inside
  the container. Explicit overrides via `pihole_upstream_dns_1/2` are still
  supported for the `openwrt-pihole-dns` feature (where gateway-based DNS
  would create a loop).
- **E2E cleanup kernel panic on single-GPU AMD hosts** -- Replaced all
  `modprobe -r amdgpu/i915` with sysfs unbind + PCI rescan + explicit
  native driver rebinding. PCI rescan alone does not auto-bind when the
  module is already loaded. Explicit `echo PCI_ADDR > driver/bind` is
  required.
- **GPU not restored after cleanup** -- PCI rescan after vfio-pci unbind
  was leaving the Intel iGPU unbound (no DRI devices). Added explicit
  driver rebinding based on PCI vendor ID in cleanup and hookscript
  post-stop.
- **WoL script included non-WoL host** -- Removed `ai` from `scripts/wol.sh`
  HOST_MAC/HOST_IP. `ai` was listed with its PCIe NIC MAC, but is connected
  via USB ethernet only. WoL magic packets would never reach it.

## [1.0.0] - 2026-03-09

First production release. Provisions and configures an OpenWrt router VM on
Proxmox VE with full NIC bridge passthrough, WiFi PCIe passthrough, 802.11s
mesh networking, and collision-free LAN subnet selection.

### Added

- **Shared infrastructure roles**
  - `proxmox_backup` -- host config and VM backup via tar and vzdump
  - `proxmox_bridges` -- physical NIC discovery and per-port virtual bridge creation
  - `proxmox_pci_passthrough` -- WiFi IOMMU/vfio-pci setup with isolation validation
- **OpenWrt VM roles**
  - `openwrt_vm` -- VM lifecycle (image upload, API create, disk import, NIC/PCIe attach, bootstrap SSH)
  - `openwrt_configure` -- two-phase UCI configuration (WAN/LAN, WiFi drivers, 802.11s mesh, DHCP, firewall)
- **Playbooks**
  - `site.yml` -- full orchestration (backup, provision, configure, cleanup)
  - `cleanup.yml` -- tag-driven restore (restore, full-restore, clean)
- **Testing**
  - Molecule integration tests against a dedicated Proxmox test node
  - Verification of bridges, VM state, SSH, WAN/LAN subnets, WiFi mesh
- **Tooling**
  - `setup.sh` -- venv bootstrap with all Python and Galaxy dependencies
  - `run.sh` -- single-command playbook execution with .env sourcing
  - `cleanup.sh` -- restore/reset wrapper with env-file support
- **Documentation**
  - Architecture overview, OpenWrt build design, role reference, roadmap
  - Cursor rules and skills for AI-assisted development continuity

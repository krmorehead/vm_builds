# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **WireGuard VPN client** -- first LXC container in the project. Lightweight
  container (VMID 101, 128 MB RAM, 1 GB disk) running a WireGuard client
  with persistent tunnel, IP forwarding, and iptables NAT/MASQUERADE. All
  credentials optional with auto-generation fallback via `.env.generated`.
  `wireguard_lxc` role provisions the container and loads the host kernel
  module; `wireguard_configure` role installs wireguard-tools, templates
  wg0.conf, and configures networking. Per-feature molecule scenario
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
  mesh, security, DNS, and diagnostic packages. All roles have fallback
  logic (custom → stock image). Eliminates EPERM/opkg failures in LXC
  containers and reduces converge time by ~2-3 minutes.
- **Self-hosted LXC templates** -- templates stored in `images/` and
  uploaded to Proxmox during provisioning (no external download needed).
- **VMID allocation scheme** -- 100-series network, 200-series services,
  300-series media, 400-series desktop, 500-series observability, 600-series
  gaming. Defined in `group_vars/all.yml`.
- **Flavor groups** -- `router_nodes`, `vpn_nodes`, `dns_nodes`,
  `wifi_nodes`, `monitoring_nodes`, `service_nodes`, `media_nodes`,
  `desktop_nodes`, `gaming_nodes` in inventory for build profile composition.
- **Build profiles documentation** (`docs/architecture/build-profiles.md`).
- **Auto-start configuration** -- `proxmox_startup_order` lookup table and
  `proxmox_ondemand_services` list in `group_vars/all.yml`.
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
- **`project_version`** variable in `group_vars/all.yml` as single source
  of truth for version tracking across deployments.
- **Unit tests** for `build.py` (`tests/test_build.py`, 32 tests covering
  env parsing, validation, playbook resolution, and command construction).
- **Deploy stamp assertions** in Molecule verify to validate tracking works.

### Changed

- Tagged all plays in `site.yml`: `backup`, `infra`, `openwrt`, `cleanup`.
  Use `--tags` to run specific plays or `--skip-tags` to exclude them.
- Split `site.yml` shared infrastructure into its own play, separate from
  VM-specific provisioning. Shared roles (`proxmox_bridges`,
  `proxmox_pci_passthrough`) target all `proxmox` hosts; OpenWrt provision
  targets `router_nodes` only.

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

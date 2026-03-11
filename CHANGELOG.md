# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`proxmox_lxc` role** -- reusable LXC container provisioning with
  parameterized resources, networking, features, mount entries, auto-start,
  and dynamic group registration via `community.proxmox.proxmox_pct_remote`.
- **`proxmox_igpu` role** -- Intel iGPU detection, i915 driver management,
  Quick Sync (VA-API) verification via `vainfo`, and fact export for
  containers/VMs needing GPU access.
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

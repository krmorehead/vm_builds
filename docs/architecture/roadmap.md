# Roadmap

## Current State (v0.1)

A single playbook that provisions and configures an OpenWrt router VM on Proxmox with:

- Dynamic NIC discovery and bridge-per-port passthrough.
- WiFi PCIe passthrough with IOMMU setup.
- 802.11s mesh networking on all detected radios.
- Automatic WAN detection and collision-free LAN subnet selection.
- Upstream MAC cloning for seamless router swaps.
- Baseline firewall, DHCP, and DNS (dnsmasq).
- Environment-driven secrets (`.env`).
- Integration test framework (Molecule) against a dedicated test node.

## Short-Term Goals

### OpenWrt Hardening
- Set a root password and deploy SSH keys (disable password auth).
- Configure syslog forwarding to a central log server.
- Enable automatic security updates via `opkg` scheduled task.
- Install and configure `banIP` or `crowdsec` for intrusion prevention.

### DNS and Ad Blocking
- Install and configure `adguardhome` or `banIP` for DNS-level filtering.
- Configure upstream DNS (DoH/DoT) for encrypted DNS resolution.

### VLAN Support
- Tag LAN ports with VLAN IDs for network segmentation (IoT, guest, management).
- Create separate firewall zones and DHCP pools per VLAN.

### Multi-Node Mesh
- Deploy the same playbook to multiple Proxmox nodes, each running an OpenWrt VM.
- Mesh nodes auto-discover each other via 802.11s and form a unified LAN.
- Centralized configuration of mesh parameters across all nodes.

### Monitoring
- Export Proxmox and OpenWrt metrics (CPU, memory, bandwidth, WiFi clients).
- Deploy a lightweight monitoring stack (Prometheus node_exporter + Grafana, or similar).

## Medium-Term Goals

### Additional VM Types
- The project name is `vm_builds` (plural) -- the architecture supports multiple VM roles beyond OpenWrt.
- Potential candidates: NAS/file server, Pi-hole, Home Assistant, media server, development environments.
- Each VM type gets its own role pair: `<type>_vm` (provisioning) and `<type>_configure` (setup).

### Backup and Recovery
- Automated VM snapshots before configuration changes.
- Export VM configs and disk images to NAS for disaster recovery.
- One-command restore from backup.

### CI/CD Pipeline
- Run Molecule tests automatically on push (GitHub Actions or similar).
- Lint and syntax checks on every commit.
- Integration tests on a schedule against the dedicated test node.

### Image Build Pipeline
- Build custom OpenWrt images with pre-installed packages using the OpenWrt Image Builder.
- Include `wpad-mesh-openssl`, monitoring agents, and custom UCI defaults in the image itself, reducing post-boot configuration time.

## Long-Term Vision

### Infrastructure as Code for the Home Network
- The entire home network -- routing, switching, WiFi, DNS, firewall, VPN, monitoring -- is defined in this repository.
- A new Proxmox node can be added to the inventory and fully provisioned in minutes.
- Configuration drift is detected and corrected by scheduled playbook runs.
- The repository serves as living documentation of the network topology.

### Hardware Abstraction
- Support heterogeneous hardware: x86 mini-PCs, ARM SBCs, rack servers.
- Automatic detection of hardware capabilities and appropriate role selection.
- Graceful degradation when hardware features (WiFi, multiple NICs) are absent.

### Multi-Site
- Extend to multiple physical locations with site-to-site VPN (WireGuard).
- Centralized management with per-site inventory files.
- Cross-site mesh networking for seamless roaming.

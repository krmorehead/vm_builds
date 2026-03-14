# WireGuard VPN Build

## Overview

A lightweight LXC container (VMID 101) running a WireGuard client that
maintains a persistent VPN tunnel back to a home server. Other services
on the node can route through this tunnel for remote access.

This is the first LXC container in the project and serves as the proving
ground for the shared `proxmox_lxc` role.

## Design Decisions

### Container, not VM

WireGuard is userspace tools plus a kernel module — no hardware access
needed. At 128 MB RAM and 1 GB disk, VM overhead is unjustified. The
`pct_remote` connection eliminates SSH server, bootstrap IP, and
ProxyJump complexity.

### Kernel module on host

LXC containers share the host kernel. The `wireguard` module must be
loaded on the Proxmox host before `wg-quick` can create interfaces inside
the container. The module is persisted via `/etc/modules-load.d/wireguard.conf`.

Unprivileged containers can create WireGuard interfaces with the host
module loaded. The `nesting=1` feature is required for iptables/nftables
inside the container (NAT MASQUERADE).

### Key auto-generation

All WireGuard env vars are optional. When `WIREGUARD_PRIVATE_KEY` is
empty, the role generates a client keypair inside the container and a
dummy server keypair for config validity. Generated values are written
to `.env.generated` on the controller (gitignored, append mode).

The user copies values from the generated file to `.env` (or `test.env`)
for persistence. Server-side setup (adding the client as a peer) uses the
public key from the generated file.

The generated file path is `{{ env_generated_path }}` — auto-detected as
`test.env.generated` under Molecule and `.env.generated` in production.

For testing, no env vars are needed — auto-generation produces a valid
but non-connecting config (dummy endpoint: RFC 5737 `198.51.100.1:51820`).

### Routing strategy

The WireGuard container enables IP forwarding and MASQUERADE on wg0.
Consuming services (rsyslog, Netdata) configure their own routes to send
specific traffic through the WireGuard container's LAN IP. OpenWrt does
NOT need policy routing — each service decides locally.

Full-tunnel mode (`AllowedIPs 0.0.0.0/0`) is supported but not default.

## Environment Variables

All optional. Auto-generated when empty.

| Variable | Default | Purpose |
|----------|---------|---------|
| `WIREGUARD_PRIVATE_KEY` | auto-gen via `wg genkey` | Client private key |
| `WIREGUARD_CLIENT_ADDRESS` | `10.0.0.2/24` | Client tunnel IP |
| `WIREGUARD_SERVER_PUBLIC_KEY` | auto-gen dummy | Server public key |
| `WIREGUARD_SERVER_ENDPOINT` | `198.51.100.1:51820` | Server host:port |
| `WIREGUARD_ALLOWED_IPS` | `10.0.0.0/24` | Routed subnets |
| `WIREGUARD_PRESHARED_KEY` | omitted | Optional PSK |
| `WIREGUARD_DNS` | omitted | Tunnel DNS servers |

## `.env.generated` Pattern

The `.env.generated` file accumulates entries from multiple services.
Each service appends its section with a header comment. The WireGuard
section includes the client PUBLIC key (needed for server-side config).

```
# BEGIN WireGuard (auto-generated)
WIREGUARD_PRIVATE_KEY=oK56DE...=
WIREGUARD_PUBLIC_KEY=Xk9B3m...=
WIREGUARD_CLIENT_ADDRESS=10.0.0.2/24
WIREGUARD_SERVER_PUBLIC_KEY=aBcDeF...=
WIREGUARD_SERVER_ENDPOINT=198.51.100.1:51820
WIREGUARD_ALLOWED_IPS=10.0.0.0/24
# END WireGuard (auto-generated)
```

## Test vs Production

| Aspect | Test | Production |
|--------|------|------------|
| Keys | Auto-generated each run | Set in `.env` |
| Endpoint | `198.51.100.1` (non-routable) | Real server |
| Tunnel connectivity | Not verified | Handshake + data |
| Generated env | `test.env.generated` (cleaned up) | `.env.generated` → copied to `.env` |

## Image Build

Custom Debian 12 template built by `build-images.sh --only wireguard`.
Builds remotely on a Proxmox host via `pct create`/`exec`/`vzdump`.

### What's Baked In

| Component | Path | Purpose |
|-----------|------|---------|
| wireguard-tools | `wg`, `wg-quick` | WireGuard CLI utilities |
| iptables | `iptables`, `ip6tables` | Packet filtering (NAT) |
| iptables-persistent | `netfilter-persistent` | Boot-persistent rules |
| /etc/wireguard/ (0700) | dir | Config directory (wg0.conf deployed at runtime) |
| /etc/sysctl.d/99-wireguard.conf | file | IP forwarding enabled |

### Container Resources

- **Template**: `wireguard-debian-12-amd64.tar.zst` (~143 MB)
- **Disk**: 1 GB (template extracts to ~483 MB)
- **Memory**: 128 MB
- **Features**: `nesting=1` (required for iptables in container)

## Roles

### `wireguard_lxc`

Thin wrapper around `proxmox_lxc`. Loads the `wireguard` kernel module
on the host, persists it, then delegates to `proxmox_lxc` for container
creation. Uses the custom WireGuard template (`wireguard_lxc_template`).

### `wireguard_configure`

Configures the container via `pct_remote`. Zero package installs — all
packages are baked into the custom image by `build-images.sh`. Templates
`wg0.conf`, enables the service, applies IP forwarding sysctl, and
configures NAT. Auto-generates keys when env vars are empty.

## Molecule Scenarios

- `molecule/default/` — full integration (WireGuard provisions after
  OpenWrt, auto-generated keys, full verify assertions)
- `molecule/wireguard-lxc/` — per-feature (provisions container, configures,
  verifies, destroys; ~30-60 seconds)

## Rollback

`playbooks/cleanup.yml` with `--tags wireguard-rollback`:
1. Stops and destroys container 101
2. Removes `/etc/modules-load.d/wireguard.conf`
3. Unloads `wireguard` kernel module
4. Removes `.env.generated` on controller

Full cleanup (`molecule test` or `--tags clean`) destroys containers by
explicit VMIDs — no blanket `pct list` iteration.

## Future Integration

- **Predictable IP**: When downstream services route through this
  container, add a DHCP static reservation on OpenWrt (same pattern as
  the Proxmox LAN management IP).
- **Full-tunnel mode**: `AllowedIPs 0.0.0.0/0` routes ALL container
  traffic through the tunnel. Document trade-offs when needed.
- **`.env.generated` accumulation**: Downstream services that auto-generate
  credentials follow the same append pattern.

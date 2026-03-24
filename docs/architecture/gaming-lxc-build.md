# Gaming LXC Build Process

## Overview

This document describes the build process for the Gaming LXC template used
in the vm_builds project. The Gaming LXC provides game streaming via Sunshine
using GPU render device sharing (NOT PCI passthrough) for hardware encoding.
This approach is safe on AMD APU single-GPU hosts where PCI passthrough
would crash the system.

## Image Build Requirements

### Build Command

```bash
scripts/build-images.sh --host <proxmox-ip> --only gaming
```

### Prerequisites

- Proxmox VE host with internet access
- Build VMID 990 must be available (temporary build container)
- Sufficient disk space for Fedora + gaming packages (~8 GB during build)

### Build Output

**Template:** `images/gaming-fedora-amd64.tar.zst`
**Size:** ~800 MB - 1.5 GB (vzdump zstd compressed)
**Build Time:** 5-10 minutes

## Design Decisions

### Why Fedora

- dsda-doom available in Fedora repos (no source compilation needed)
- Sunshine available via COPR (Fedora's community repo)
- Latest Mesa drivers with better AMD GPU support
- PipeWire is the default audio system
- GameMode is first-class

### Why GPU Render Device Sharing (Not PCI Passthrough)

AMD Raven Ridge APU iGPUs share the SoC die with the CPU. Unbinding the
GPU driver (sysfs or modprobe -r) for PCI passthrough triggers a GPU reset
that hangs the entire NBIO, crashing the system. This is a hardware
limitation that cannot be worked around.

Instead, the container accesses `/dev/dri/card*` and `/dev/dri/renderD128`
directly from the host via bind mounts. The host keeps the `amdgpu` driver
loaded. Sunshine uses VA-API through the shared render device for hardware
encoding.

### Why Pre-Built Template (Not Runtime Install)

Following the project's "bake, don't configure at runtime" principle:
- Package installation via dnf takes 5-10 minutes
- Runtime installs depend on network (RPM Fusion, COPR repos)
- The template arrives ready to run with all software installed
- The configure role only applies host-specific settings (credentials)

### Software Baked into the Template

**Core:**
- Fedora 41 (rootfs from linuxcontainers.org)
- systemd, bash, procps, iproute

**GPU/Display:**
- Mesa VA-API freeworld drivers (`mesa-va-drivers-freeworld` from RPM Fusion --
  required for H.264/HEVC encode; stock `mesa-va-drivers` strips patent-encumbered codecs)
- Mesa Vulkan drivers (`mesa-vulkan-drivers`)
- Xorg server with modesetting driver
- Headless Xorg virtual display service (`xorg-virtual.service`)

**Streaming:**
- Sunshine (from LizardByte COPR repo)
- Sunshine systemd service (`sunshine.service`)

**Audio:**
- PipeWire + PipeWire-PulseAudio
- WirePlumber (session manager)

**Gaming:**
- dsda-doom (modern Doom source port, from RPM Fusion)
- Freedoom WADs (open-source Doom content, at `/usr/share/doom/`)
- GameMode (performance optimization)

### Fedora Packaging Gotchas

- **dnf5 strict mode:** Fedora 41 uses `dnf5`, which aborts the entire
  transaction if any single package is unavailable. Use `--skip-unavailable`
  on `dnf install` to prevent one missing package from blocking all others.
- **`passwd` package:** Does not exist on Fedora 41 (absorbed into
  `shadow-utils`, which is pre-installed). Including it in `dnf install`
  silently aborts the entire install.
- **Mesa codec stripping:** Fedora's stock `mesa-va-drivers` excludes
  patent-encumbered codecs (H.264, HEVC). You MUST `dnf swap mesa-va-drivers
  mesa-va-drivers-freeworld` from RPM Fusion to get hardware encode.
- **GZDoom:** Not available in RPM Fusion for Fedora 41. Use `dsda-doom`
  (modern Doom source port, in RPM Fusion Free).
- **Freedoom WAD path:** WADs install to `/usr/share/doom/` (not
  `/usr/share/freedoom/`).
- **GPU device numbering:** NEVER hardcode `/dev/dri/card0` or
  `/dev/dri/renderD128`. On AMD APU hosts, the card device may be `card1`.
  Always detect dynamically via `ls /dev/dri/card*`.

## Container Architecture

### Device Passthrough

The LXC container is **privileged** (`unprivileged: false`) with the
following device bind mounts:

| Host Device | Container Mount | Purpose |
|---|---|---|
| `/dev/dri` | `/dev/dri` | GPU render + card devices |
| `/dev/input` | `/dev/input` | Game controller input |

cgroup2 allowlists:
- `c 226:* rwm` -- DRI devices
- `c 13:* rwm` -- Input devices
- `c 10:223 rwm` -- `/dev/uinput` for virtual input

### Network Topology

The gaming container's network depends on host placement:

- **WAN hosts** (e.g., `ai`): Container gets a WAN-subnet IP with +200
  offset. Bridge: `proxmox_wan_bridge`.
- **LAN hosts** (behind OpenWrt): Container gets a LAN-subnet IP.
  Bridge: second bridge (`proxmox_all_bridges[1]`).

Current allocation: `ai` is the only `gaming_nodes` member.
IP offset: 18. WAN IP: `192.168.86.218` (no collision).

### Service Architecture

```
xorg-virtual.service (headless X11 :0)
        |
sunshine.service (captures :0, streams via NVENC/VA-API)
        |
moonlight client (mesh1) <--- WireGuard VPN ---> sunshine (ai)
```

## Role Architecture

### Two-Role Pattern

- `gaming_lxc` -- Provision: template check, IP computation, proxmox_lxc
  include, DRI/input device passthrough, cgroup allowlists
- `gaming_lxc_configure` -- Configure: GPU verification, VA-API check,
  Sunshine credentials, service enablement, health checks

### Exported Facts

- `gaming_static_ip` -- Container IP (cacheable)

### Dynamic Group

The provision role registers each container in the `gaming` dynamic group
via `proxmox_lxc` (inherited from the shared helper role).

## VMID Allocation

| VMID | Purpose |
|---|---|
| 600 | Gaming VM (legacy Windows build, not active) |
| 601 | Gaming LXC (Fedora, active gaming path) |

## Molecule Testing

### Per-Feature Scenario

```bash
molecule test -s gaming-lxc
```

Targets `ai` only. Tests:
- Container running
- GPU device access (`/dev/dri/renderD128`, `/dev/dri/card*` -- dynamically detected)
- VA-API H.264 encoding profiles (requires freeworld drivers)
- Privileged container with nesting
- DRI mount entries in LXC config
- Sunshine service active
- dsda-doom and Freedoom WADs installed
- deploy_stamp present

### E2E Integration

The `molecule/default/` scenario includes `ai` in `gaming_nodes`. The
E2E verify play checks container state, GPU access, and service health.

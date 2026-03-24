# Moonlight Build Process

## Overview

This document describes the build process for the Moonlight LXC template used in the vm_builds project. The Moonlight container runs `moonlight-embedded` for game streaming from a Sunshine server (Gaming Rig, project 13). It uses the iGPU for hardware video decode (VA-API) and renders to the physical display via DRM/KMS. USB input devices are passed through for controller support.

## Image Build Requirements

### Build Command

```bash
./build-images.sh --host <proxmox-ip> --only moonlight
```

### Prerequisites

- Proxmox VE host with internet access
- Base Debian 12 standard template: `debian-12-standard_12.12-1_amd64.tar.zst`
- 4GB available disk space on Proxmox host for build container (compilation needs headroom)

### Build Output

**Template:** `images/moonlight-debian-12-amd64.tar.zst`
**Size:** ~150-250MB (compressed)
**Build Time:** 1-3 minutes

## Design Decisions

### Streaming Client: moonlight-embedded

`moonlight-embedded` is the headless Moonlight client designed for framebuffer/DRM output. No X11 or Wayland required. Minimal resource usage (1 core, 512MB RAM). Compiled from source (the Cloudsmith repo only provides armhf packages for Raspberry Pi).

### Display Output: DRM/KMS

Moonlight renders directly to the display via DRM/KMS using `/dev/dri/*` device passthrough. This avoids the complexity of a display server.

### Package Selection

The image includes the following pre-installed software:

**Streaming:**
- `moonlight-embedded` v2.7.1 - Compiled from source (the Cloudsmith repo only provides armhf packages for Raspberry Pi, not x86-64 Debian)

**VA-API Drivers (Hardware Video Decode):**
- `intel-media-va-driver` - Intel iGPU hardware decode
- `mesa-va-drivers` - AMD GPU hardware decode
- `vainfo` - VA-API capability detection

**VA-API driver note:** Package names depend on Debian release. The correct package for Debian 12 (Bookworm) is `intel-media-va-driver` (not `intel-media-va-driver-non-free`, which does not exist on newer releases).

### Why Compile From Source?

The official moonlight-embedded Cloudsmith repository only publishes armhf packages for Raspberry Pi OS/OSMC. There are no x86-64 Debian packages available. Compilation follows the upstream wiki instructions exactly and produces the same binary. Build dependencies are removed after compilation to minimize image size.

### Why Both Intel and AMD Drivers?

Hardware portability. Only the appropriate driver loads at runtime based on actual GPU hardware. The same image works on Intel and AMD hosts without rebuilding.

### Pre-configuration (Baked into Image)

All packages are installed and configured during the image build. The configure role only applies host-specific settings:
- Sunshine server IP address
- Resolution, codec, and bitrate preferences
- Server pairing credentials

## Build Process Details

### Container Creation

1. **Base Template:** Debian 12 standard template (VMID 991)
2. **Resources:** 1024MB RAM, 2 CPU cores, 4GB disk (headroom for compilation)
3. **Network:** DHCP on management bridge
4. **Privileges:** Unprivileged container

### Build Steps

1. Install runtime dependencies (`libopus0`, `libasound2`, `libsdl2-2.0-0`, `libavcodec59`, `libavutil57`, etc.) and VA-API drivers
2. Install build dependencies (`cmake`, `gcc`, `g++`, development headers including `libsdl2-dev`, `libavcodec-dev`, `libavutil-dev`)
3. Clone moonlight-embedded v2.7.1 from GitHub with submodules
4. Compile with `cmake -DENABLE_X11=OFF` and `make` — X11 is disabled since LXC containers use DRM/KMS via SDL2's KMSDRM backend, not X11
5. Remove build dependencies and source to minimize image size (runtime deps marked manual survive `apt-get autoremove`)
6. Clean package caches

### Why `-DENABLE_X11=OFF`?

The LXC container has no X11 server. Enabling X11 at build time links the binary against `libEGL.so.1`, `libGLESv2.so`, and `libX11.so` — all of which get removed during build cleanup since they're only needed for X11 rendering. With X11 disabled, the binary only links against SDL2 (which uses DRM/KMS directly via the KMSDRM backend) and FFmpeg.

### Export Process

1. **Container Snapshot:** `vzdump` with zstd compression
2. **Template Download:** SCP from Proxmox host to local `images/` directory
3. **Cleanup:** Remove temporary container and vzdump archive

## Runtime Configuration

### Device Passthrough

The Moonlight container requires access to display and input devices:

| Device | Mount | cgroup Rule | Purpose |
|--------|-------|-------------|---------|
| `/dev/dri` | bind mount | `c 226:* rwm` | Display output + hardware decode |
| `/dev/input` | bind mount | `c 13:* rwm` | USB controllers and input |
| `/dev/uinput` | (via cgroup) | `c 10:223 rwm` | Virtual input device creation |

### Display Exclusivity

Moonlight is a display-exclusive container:
- `onboot: false` -- started on demand for game streaming
- When started, the display-exclusive hookscript (deployed by the Kiosk project) stops competing display consumers
- When stopped, the hookscript restarts the default display state (Kiosk)

The hookscript is NOT deployed by this project. Moonlight only attaches it.

### Network Topology

Moonlight containers deploy on `streaming_nodes` (mesh1) which is a LAN host
behind OpenWrt. The container uses the OpenWrt LAN subnet. The Sunshine
server runs on `gaming_nodes` (home) on the WAN subnet. Cross-subnet
streaming discovery happens via WireGuard VPN.

NEVER co-locate Moonlight (client) and Sunshine (server) on the same host.

## Environment Variables

### Build-time Variables

Defined in `inventory/group_vars/all.yml`:

```yaml
moonlight_lxc_template: moonlight-debian-12-amd64.tar.zst
moonlight_lxc_template_path: images/moonlight-debian-12-amd64.tar.zst
moonlight_ct_ip_offset: 17
moonlight_ct_id: 302
```

### Runtime Variables (Optional)

| Variable | Purpose | Example |
|----------|---------|---------|
| `MOONLIGHT_SERVER_IP` | Sunshine server hostname/IP | `192.168.1.50` |
| `MOONLIGHT_PAIR_PIN` | Pre-shared PIN for `moonlight pair` | `1234` |

## Testing

### What Tests Verify

- Container 302 is running with correct config (onboot: 0)
- DRI and input device bind mounts present
- cgroup device allowlists configured
- `moonlight-embedded` binary installed
- `vainfo` binary available
- Moonlight config file deployed at `/etc/moonlight.conf`
- VA-API hardware decode capability (informational, not hard-fail)

### What Tests Do NOT Verify

- Actual game streaming (no Sunshine server in molecule)
- Pairing with Sunshine (skipped when `MOONLIGHT_PAIR_PIN` is empty)
- Display output to physical monitor

## Integration Notes

This template integrates with:
- **proxmox_igpu** role: Provides iGPU facts and render device detection
- **proxmox_lxc** role: Handles container provisioning and networking
- **moonlight_configure** role: Applies server IP, resolution, codec, pairing
- **Gaming Rig** (project 13): Sunshine server that Moonlight streams from
- **Custom UX Kiosk** (project 12): Deploys the display-exclusive hookscript that Moonlight attaches

## Rollback Procedure

If the build fails or produces a defective template:

1. **Remove Template:**
   ```bash
   rm -f images/moonlight-debian-12-amd64.tar.zst
   ```

2. **Remove Variables:**
   Remove Moonlight-related variables from `inventory/group_vars/all.yml`

3. **Rebuild:**
   ```bash
   ./build-images.sh --host <proxmox-ip> --only moonlight
   ```

The build process follows the project's "Bake, don't configure" principle -- all packages are included in the image, while the configure role only applies host-specific settings (server IP, resolution, pairing).

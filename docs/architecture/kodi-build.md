# Kodi Build Process

## Overview

This document describes the build process for the Kodi LXC template used in the vm_builds project. The Kodi container provides a local media player and home theater frontend using GBM/DRM direct rendering to the physical display via the shared iGPU. HDMI audio output via ALSA bind mount.

## Image Build Requirements

### Build Command

```bash
./build-images.sh --host <proxmox-ip> --only kodi
```

### Prerequisites

- Proxmox VE host with internet access
- Base Debian 12 standard template: `debian-12-standard_12.12-1_amd64.tar.zst`
- 4GB available disk space on Proxmox host for build container

### Build Output

**Template:** `images/kodi-debian-12-amd64.tar.zst`
**Size:** ~300-500MB (compressed)
**Build Time:** 2-4 minutes

## Design Decisions

### Display Output: GBM/DRM (No X11)

Kodi renders directly to the display via GBM/DRM (`kodi-gbm`). This avoids the overhead and complexity of X11 or Wayland. The `kodi-standalone` systemd service starts Kodi on a virtual terminal with direct DRM access.

### Package Selection

The image includes the following pre-installed packages:

**Kodi Components:**
- `kodi` - Core Kodi media player
- `kodi-gbm` - GBM/DRM rendering backend
- `kodi-peripheral-joystick` - Game controller support

**Display & Audio:**
- `libcec6` + `cec-utils` - HDMI-CEC support for TV remote control
- `alsa-utils` - ALSA audio utilities for HDMI audio

**VA-API Drivers (Hardware Acceleration):**
- `intel-media-va-driver` - Intel iGPU hardware decode
- `mesa-va-drivers` - AMD GPU hardware decode
- `vainfo` - VA-API capability detection

### Why Both Intel and AMD Drivers?

Same rationale as Jellyfin: hardware portability. Only the appropriate driver loads at runtime based on actual GPU hardware.

### Pre-configuration (Baked into Image)

**kodi-standalone systemd service:**
- Runs as `kodi` system user
- Direct TTY access for GBM/DRM rendering
- Auto-restart on failure

**advancedsettings.xml:**
- Buffer: 50MB memory cache
- Read factor: 4x (for network streams)
- Curl timeouts: 30s

**System user:**
- `kodi` user with `audio`, `video`, `input`, `render` group memberships

## Build Process Details

### Container Creation

1. **Base Template:** Debian 12 standard template (VMID 993)
2. **Resources:** 1024MB RAM, 2 CPU cores, 4GB disk
3. **Network:** DHCP on management bridge
4. **Privileges:** Unprivileged container

### Package Installation Steps

1. Install Kodi core, GBM backend, and peripheral support
2. Install HDMI-CEC support (libcec)
3. Install ALSA utilities for audio
4. Install VA-API drivers (Intel + AMD) for hardware decode
5. Create `kodi` system user with device group memberships
6. Deploy `kodi-standalone` systemd service unit
7. Pre-configure `advancedsettings.xml` for network streaming
8. Clean package caches

### Export Process

1. **Container Snapshot:** `vzdump` with zstd compression
2. **Template Download:** SCP from Proxmox host to local `images/` directory
3. **Cleanup:** Remove temporary container and vzdump archive

## Runtime Configuration

### Device Passthrough

The Kodi container requires access to display, audio, and input devices:

| Device | Mount | cgroup Rule | Purpose |
|--------|-------|-------------|---------|
| `/dev/dri` | bind mount | `c 226:* rwm` | Display output + hardware decode |
| `/dev/snd` | bind mount | `c 116:* rwm` | HDMI audio output |
| `/dev/input` | bind mount | `c 13:* rwm` | Input devices (CEC, keyboard) |

### Display Exclusivity

Kodi is a display-exclusive container:
- `onboot: false` -- started on demand for media playback
- When started, the display-exclusive hookscript (deployed by the Kiosk project) stops competing display consumers
- When stopped, the hookscript restarts the default display state (Kiosk)

The hookscript is NOT deployed by this project. Kodi only attaches it via `pct set`.

### Network Topology

Kodi containers always use the OpenWrt LAN subnet. `media_nodes` hosts are always behind OpenWrt. No WAN-connected case exists.

## Environment Variables

### Build-time Variables

Defined in `inventory/group_vars/all.yml`:

```yaml
kodi_lxc_template: kodi-debian-12-amd64.tar.zst
kodi_lxc_template_path: images/kodi-debian-12-amd64.tar.zst
kodi_ct_ip_offset: 16
kodi_ct_id: 301
```

### Runtime Variables

No required runtime environment variables. Kodi is self-contained once provisioned.

## Integration Notes

This template integrates with:
- **proxmox_igpu** role: Provides iGPU facts and render device detection
- **proxmox_lxc** role: Handles container provisioning and networking
- **kodi_configure** role: Applies web interface, ALSA audio, and iGPU render group config
- **Jellyfin** (project 08): Kodi connects to Jellyfin via JellyCon add-on for media library access
- **Custom UX Kiosk** (project 12): Deploys the display-exclusive hookscript that Kodi attaches

## Rollback Procedure

If the build fails or produces a defective template:

1. **Remove Template:**
   ```bash
   rm -f images/kodi-debian-12-amd64.tar.zst
   ```

2. **Remove Variables:**
   Remove Kodi-related variables from `inventory/group_vars/all.yml`

3. **Rebuild:**
   ```bash
   ./build-images.sh --host <proxmox-ip> --only kodi
   ```

The build process follows the project's "Bake, don't configure" principle -- all packages and base configuration are included in the image, while the configure role only applies host-specific settings (iGPU render group, ALSA output, web interface).

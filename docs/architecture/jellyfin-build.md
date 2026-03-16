# Jellyfin Build Process

## Overview

This document describes the build process for the Jellyfin LXC template used in the vm_builds project. The Jellyfin container provides hardware-accelerated media transcoding using Intel Quick Sync or AMD VA-API via iGPU device passthrough.

## Image Build Requirements

### Build Command

```bash
./build-images.sh --host <proxmox-ip> --only jellyfin
```

### Prerequisites

- Proxmox VE host with internet access
- Base Debian 12 standard template: `debian-12-standard_12.12-1_amd64.tar.zst`
- iGPU hardware on the Proxmox host (Intel i915 or AMD amdgpu)
- 2GB available disk space on Proxmox host for build container

### Build Output

**Template:** `images/jellyfin-debian-12-amd64.tar.zst`
**Size:** ~200-300MB (compressed)
**Build Time:** 2-3 minutes

## Design Decisions

### Package Selection

The image includes the following pre-installed packages:

**Jellyfin Components:**
- `jellyfin` - Core Jellyfin server
- `jellyfin-web` - Web interface
- `jellyfin-ffmpeg` - FFmpeg with hardware acceleration support

**VA-API Drivers (Hardware Transcoding):**
- `intel-media-va-driver` - Intel iGPU hardware acceleration
- `mesa-va-drivers` - AMD GPU hardware acceleration
- `vainfo` - VA-API information utility

### Why Both Intel and AMD Drivers?

The image includes BOTH Intel and AMD VA-API drivers for hardware portability. At runtime:
- Only the appropriate driver loads based on actual GPU hardware
- No need to rebuild images when moving between different hardware
- Jellyfin configure role sets `LIBVA_DRIVER_NAME` based on `igpu_vendor` fact

### Pre-configuration

The image includes these baked-in configurations:

**Network:**
- Web interface port: 8096
- Base URL: `/`

**Hardware Acceleration:**
- VA-API device: `/dev/dri/renderD128`
- Driver selection: `auto` (determined at runtime)
- Default transcoding: Hardware-accelerated when available

**Service:**
- Jellyfin service: `enabled` at boot
- User: Runs as `jellyfin` user

## Build Process Details

### Container Creation

1. **Base Template:** Debian 12 standard template (VMID 995)
2. **Resources:** 512MB RAM, 1 CPU core, 2GB disk
3. **Network:** DHCP on management bridge
4. **Privileges:** Unprivileged container

### Package Installation Steps

1. **Repository Setup:**
   - Download and install Jellyfin GPG key
   - Add Jellyfin official Debian repository
   - Update package lists

2. **Jellyfin Installation:**
   - Install from official Jellyfin repository
   - Avoid recommends to minimize image size

3. **VA-API Driver Installation:**
   - Install both Intel and AMD drivers
   - Include `vainfo` for capability detection

4. **Configuration:**
   - Pre-configure port and base URL
   - Enable VA-API device path
   - Set up jellyfin user and directories

5. **Cleanup:**
   - Remove package caches
   - Clear temporary files
   - Stop services before snapshot

### Export Process

1. **Container Snapshot:** `vzdump` with zstd compression
2. **Template Download:** SCP from Proxmox host to local `images/` directory
3. **Cleanup:** Remove temporary container and vzdump archive

## Runtime Configuration

### iGPU Device Passthrough

The Jellyfin container requires access to the iGPU render device:

**Device Path:** `/dev/dri/renderD128`
**Cgroup Allowlist:** `c 226:128 rwm`
**Mount Point:** Bind mount to same path in container

### Media Storage

Media libraries are bind-mounted from the host:
- **Host Path:** `jellyfin_media_path` (default: `/mnt/media`)
- **Container Path:** `/media`

### Hardware Fallback

If iGPU is unavailable:
- Jellyfin automatically falls back to software transcoding
- No configuration changes required
- Performance degrades but functionality maintained

## Environment Variables

### Build-time Variables

Defined in `inventory/group_vars/all.yml`:

```yaml
jellyfin_lxc_template: jellyfin-debian-12-amd64.tar.zst
jellyfin_lxc_template_path: images/jellyfin-debian-12-amd64.tar.zst
jellyfin_ct_ip_offset: 15
jellyfin_media_path: /mnt/media
jellyfin_ct_id: 300
```

### Runtime Variables

**Required:**
- `JELLYFIN_ADMIN_PASSWORD` - Admin user password (production)

**Optional:**
- Auto-generated password for testing when empty

## Testing vs Production

### Testing Environment

- **Password:** Auto-generated if `JELLYFIN_ADMIN_PASSWORD` not set
- **Template:** Uses pre-built template from `images/` directory
- **Media:** No external media mounting (uses test data)

### Production Environment

- **Password:** Must set `JELLYFIN_ADMIN_PASSWORD` environment variable
- **Template:** Same build process, different environment
- **Media:** External NFS/SMB mount or local disk

## Build Verification

After building, verify the template contains expected components:

```bash
# Check template exists
ls -lh images/jellyfin-debian-12-amd64.tar.zst

# Verify with pct create test
pct create 999 local:vztmpl/jellyfin-debian-12-amd64 \
  --hostname jellyfin-test --memory 512 --cores 1 --rootfs local-lvm:2

# Test container start and basic functionality
pct start 999
pct exec 999 -- which jellyfin
pct exec 999 -- vainfo
pct exec 999 -- systemctl is-active jellyfin

# Cleanup test container
pct destroy 999 --purge
```

## Rollback Procedure

If the build fails or produces a defective template:

1. **Remove Template:**
   ```bash
   rm -f images/jellyfin-debian-12-amd64.tar.zst
   ```

2. **Remove Variables:**
   Remove Jellyfin-related variables from `inventory/group_vars/all.yml`

3. **Remove Build Function:**
   Remove `build_jellyfin_lxc()` and `cleanup_jellyfin_build()` from `scripts/build-images.sh`

4. **Clean Build Cache:**
   ```bash
   ./build-images.sh --clean
   ```

## Common Build Issues

### Repository Access Failure

**Symptom:** `apt-get update` fails with repository errors
**Solution:** Verify Proxmox host has internet access and DNS resolution

### Disk Space Issues

**Symptom:** Build container creation fails with disk space error
**Solution:** Free disk space on Proxmox host or increase storage allocation

### Template Corruption

**Symptom:** Template downloads but `pct create` fails
**Solution:** Remove template and rebuild. Check Proxmox host filesystem integrity

### VA-API Driver Compatibility

**Symptom:** `vainfo` fails or shows no hardware acceleration
**Solution:** This is expected in the build container (no GPU). Verification happens in production with actual iGPU.

## Integration Notes

This template integrates with:
- **proxmox_igpu** role: Provides iGPU facts and device detection
- **proxmox_lxc** role: Handles container provisioning and networking
- **jellyfin_configure** role: Applies runtime configuration and user setup

The build process follows the project's "Bake, don't configure" principle - all packages and base configuration are included in the image, while the configure role only applies host-specific settings.
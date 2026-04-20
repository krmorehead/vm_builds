# Desktop LXC Build Process

## Overview

A Debian 12 LXC container running both KDE Plasma (Windows-style) and GNOME
(Mac-style) desktop sessions. Users switch between them via the Web UI viewer
bar — no login screen needed. Both sessions share the same home directory
(Downloads, Pictures, Documents) so switching is purely a UX change.

The Desktop LXC uses shared DRI render node access (`/dev/dri/renderD*`)
for hardware-accelerated rendering. No iGPU passthrough, no IOMMU required.
It conflicts with Kodi and Moonlight for DRI3 device access (managed by the
display-exclusive hookscript).

## Image Build

Desktop LXC templates follow the standard "bake, don't configure" principle.
A custom vzdump template is built by `build-images.sh --only desktop` with
KDE Plasma, GNOME, KasmVNC, and all shared applications pre-installed.
GPU VA-API drivers for both Intel and AMD are baked in.

## Design Decisions

### One Container, Two Sessions

Both KDE and GNOME are installed in the same LXC container with the same
`desktop` user account. They share `/home/desktop/Downloads`,
`/home/desktop/Pictures`, etc. Swapping between the two is a one-click
operation in the Web UI viewer bar that:

1. Swaps the `/home/desktop/.vnc/xstartup` symlink to point at `xstartup-kde`
   or `xstartup-gnome`
2. Restarts `desktop-display.service` (KasmVNC Xvnc)
3. The iframe auto-reloads with the new desktop environment

This is architecturally cleaner than two separate containers:

- No data duplication or synchronization needed
- Single user account, single set of preferences for shared apps
- Smaller disk footprint (one OS, two DE packages)
- Instant switching via API call (~3 seconds)

### Session Switching Architecture

```
Container filesystem:
  /home/desktop/.vnc/xstartup-kde    # exec startplasma-x11
  /home/desktop/.vnc/xstartup-gnome  # XDG_CURRENT_DESKTOP=GNOME exec dbus-launch ...
  /home/desktop/.vnc/xstartup        # symlink → xstartup-kde (default)
  /usr/sbin/switch-desktop-session   # accepts kde|gnome|status

Web UI flow:
  Viewer bar [Windows] [Mac] buttons
    → POST /api/desktop/session/{kde|gnome}
    → NodeManager: pct exec 400 -- /usr/sbin/switch-desktop-session <session>
    → Script: swaps symlink, restarts desktop-display.service
    → KasmVNC restarts with new DE, iframe reloads after 3s
```

### KDE Plasma = Windows-Style UX

- Bottom taskbar with system tray
- Start menu via Application Launcher
- Alt+Tab window switching
- Breeze Dark theme (Windows 11 Dark Mode twin)
- Konsole terminal, Dolphin file manager, Kate editor

### GNOME = Mac-Style UX

- Dash-to-Dock at bottom (auto-hide, dynamic transparency)
- Activities overview via hot corner
- Adwaita Dark theme
- Close/minimize/maximize on the left (Mac-style button layout)
- Nautilus file manager, GNOME Terminal

### Shared Applications

Both sessions share:

- Firefox ESR as the default browser
- VLC for media playback
- Flameshot for screenshots
- PipeWire + WirePlumber for audio
- VA-API GPU drivers (Intel + AMD)

## Image

| Property | Value |
|----------|-------|
| Build | `./build-images.sh --host <ip> --only desktop` |
| Base | `images/debian-12-standard_12.7-1_amd64.tar.zst` (Debian 12 LXC template) |
| Output | `images/desktop-<version>-debian-12-amd64.tar.zst` |
| Variable | `desktop_lxc_template_path` in `group_vars/all.yml` |
| Includes | KDE Plasma, GNOME, KasmVNC, Firefox, VLC, Flameshot, PipeWire, VA-API drivers |
| Excludes | Nothing — all packages baked in |

## Resources

| Resource | Value |
|----------|-------|
| VMID | 400 |
| Cores | 4 |
| RAM | 4096 MB |
| Disk | 16 GB |
| Network | LAN (router/lan_hosts) or NAT bridge (WAN hosts) |
| DRI | Shared render node via bind mount |
| Auto-start | Yes (baked into image) |

## Roles

### `desktop_lxc` (provisioning)

Creates the LXC container via `include_role: proxmox_lxc`, configures
DRI device passthrough, and registers in the `desktop` dynamic group.

Exports: none (container IP computed from host topology).

### `desktop_configure` (configuration)

Writes host-specific config (callhome server, display port). Desktop
environments and shared apps are baked into the image. ZERO package installs.

## Display Exclusivity

- Starting Desktop: Kodi and Moonlight stop (DRI3 conflict)
- Jellyfin falls back to software transcoding
- Stopping Desktop: Kodi and Moonlight can restart
- Hookscript manages the conflict resolution

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/desktop/session/{kde\|gnome}` | POST | Switch desktop session |
| `/api/desktop/session` | GET | Query current session |

## Testing

```bash
# Per-feature iteration
molecule converge -s desktop-lxc
molecule verify -s desktop-lxc
molecule cleanup -s desktop-lxc

# Full integration
molecule test
```

## Rollback

```bash
# Per-feature rollback
./cleanup.sh --tags desktop-rollback

# Full container destruction via molecule cleanup
molecule cleanup -s desktop-lxc
```

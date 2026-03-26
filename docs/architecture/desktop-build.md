# Desktop VM Build Process

## Overview

A Debian 12 VM running both KDE Plasma (Windows-style) and GNOME (Mac-style)
desktop sessions. Users choose their preferred UX at the SDDM login screen.
Both sessions share the same home directory (Downloads, Pictures, Documents,
etc.) so switching is purely a configuration change.

The Desktop VM takes **exclusive** iGPU access via `hostpci` passthrough,
making it the most disruptive display-exclusive service. Starting it stops
Kiosk, Kodi, and Moonlight AND unbinds the iGPU from the host.

## Image Build

Desktop VMs follow the standard "bake, don't configure" principle. A custom
qcow2 image is built by `build-images.sh --only desktop` with KDE Plasma,
GNOME, SDDM, and all shared applications pre-installed. GPU drivers are the
only packages installed at configure time (vendor-dependent on `igpu_vendor`).

## Design Decisions

### One VM, Two Sessions

Both KDE and GNOME run on the same Debian VM with the same user account.
They naturally share `/home/user/Downloads`, `/home/user/Pictures`, etc.
Swapping between the two is a log-out-and-pick-another-session operation
at the SDDM login screen. This is architecturally cleaner than two
separate VMs because:

- No data duplication or synchronization needed
- Single user account, single set of preferences for shared apps
- Smaller disk footprint (one OS, two DE packages)
- Instant switching via SDDM session selector

### KDE Plasma = Windows-Style UX

- Bottom taskbar with system tray
- Start menu via Application Launcher
- Alt+Tab window switching
- Window snapping (Meta+Left/Right/Up/Down)
- Dark Breeze theme
- Print key for screenshot
- Ctrl+Shift+4 for region screenshot (cross-session shared shortcut)

### GNOME = Mac-Style UX

- Dash to Dock at bottom (auto-hide, dynamic transparency)
- Activities overview via Super+Space
- Super+Q to close windows (Cmd+Q analog)
- Super+H to minimize (Cmd+H analog)
- Shift+Super+3 for full screenshot (Cmd+Shift+3 analog)
- Ctrl+Shift+4 for region screenshot (cross-session shared shortcut)
- Natural scroll enabled
- Caps Lock remapped to Super (Caps = Cmd analog)
- Dark Adwaita theme
- Hot corners enabled

### Non-Conflicting Shared Shortcuts

Both sessions support Ctrl+Shift+4 for region screenshot via Flameshot,
which runs as a background app in both KDE and GNOME. Other shared
features:

- Flameshot autostart in both sessions
- GTK bookmarks for shared directories
- Firefox ESR as the default browser
- LibreOffice for documents
- VLC for media playback
- PipeWire + WirePlumber for audio

## Image

| Property | Value |
|----------|-------|
| Build | `./build-images.sh --host <ip> --only desktop` |
| Base | `images/debian-12-generic-amd64.qcow2` (Debian 12 cloud image) |
| Output | `images/desktop-debian-12-amd64.qcow2` |
| Variable | `desktop_image_path` in `group_vars/all.yml` |
| Includes | KDE Plasma, GNOME, SDDM, Firefox, VLC, LibreOffice, Flameshot, PipeWire |
| Excludes | GPU drivers (installed at configure time based on `igpu_vendor`) |

## Resources

| Resource | Value |
|----------|-------|
| VMID | 400 |
| Cores | 2 |
| RAM | 4096 MB |
| Disk | 32 GB |
| Network | WAN bridge (DHCP from ISP router) |
| BIOS | UEFI (OVMF), q35 |
| iGPU | Exclusive passthrough via hostpci0 |
| Auto-start | No (on-demand) |

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DESKTOP_USER` | yes | Desktop user name |
| `DESKTOP_PASSWORD` | yes | User password |
| `DESKTOP_SSH_PUBLIC_KEY` | no | SSH key for login |
| `DESKTOP_AUTOLOGIN` | no | Auto-login at boot (default: false) |
| `DESKTOP_DEFAULT_SESSION` | no | Default SDDM session (default: plasma) |

## Roles

### `desktop_vm` (provisioning)

Creates the VM with UEFI/q35, imports the cloud image, configures cloud-init,
attaches iGPU via hostpci0, and registers in the `desktop` dynamic group.

Exports: none (VM IP discovered via DHCP lease lookup).

### `desktop_configure` (configuration)

Installs host-specific GPU drivers and applies per-session polish (KDE dark
theme, GNOME dock, shared shortcuts, SDDM config, Flameshot autostart).
Desktop environments and shared apps are baked into the image.

## Display Exclusivity

- Starting Desktop VM: Kiosk, Kodi, Moonlight stop; iGPU unbound from host
- Jellyfin falls back to software transcoding
- Stopping Desktop VM: iGPU returns to host; Kiosk restarts
- Hookscript deployed by Kiosk project (2026-03-09-12); Desktop VM attaches

## Testing

```bash
# Per-feature iteration
molecule converge -s desktop-vm
molecule verify -s desktop-vm
molecule cleanup -s desktop-vm

# Full integration
molecule test
```

## Rollback

```bash
# Per-feature rollback
./cleanup.sh --tags desktop-rollback

# Full VM destruction via molecule cleanup
molecule cleanup -s desktop-vm
```

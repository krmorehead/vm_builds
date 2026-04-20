# KasmVNC Migration — Context & Architecture Notes

These notes provide everything a fresh session needs to plan and execute
the migration from the current noVNC pipeline to KasmVNC.

---

## Current Problem

The existing VNC streaming pipeline has **significant performance penalties**
when viewing remote container/VM displays through the web UI:

1. **Software-only encoding.** wayvnc encodes frames in software (CPU) using
   the RFB protocol. No hardware acceleration (VA-API, NVENC) is used, even
   though every host has an iGPU with VA-API support.

2. **Multi-hop overhead.** Each frame traverses 3-4 processes:
   `sway` → `wayvnc` (RFB encode) → `websockify` (WS bridge) → `noVNC` (JS decode + canvas render).
   websockify adds a full Python process doing nothing but protocol bridging.

3. **Primitive codec.** The RFB protocol uses raw pixel tiles, hextile, zlib,
   and tight encodings — all 1990s-era. No modern video codecs (H.264, VP8/9,
   WebP). Every pixel change is sent as a rectangle, not as a compressed
   video frame.

4. **No adaptive bitrate.** The pipeline sends at a fixed quality/rate
   regardless of network conditions. On LAN this wastes bandwidth; through
   VPN it creates lag.

5. **Client-side JavaScript decode.** noVNC decodes and renders in JavaScript
   on a `<canvas>` element. CPU-intensive on the browser, poor frame rates
   compared to native decode.

### What this affects

- **Kiosk remote view** — SuperManager/ClusterManager viewing child kiosk UIs
- **Desktop VM console** — viewing the Debian Desktop VM display
- **Kodi console** — viewing the Kodi media player display
- **Moonlight console** — viewing the Moonlight client display (the streaming
  session itself uses Sunshine→Moonlight with hardware encode/decode,
  but the "look at what Moonlight is showing" console uses the VNC pipeline)

### What this does NOT affect

- **Sunshine → Moonlight** gaming/streaming — this stays as-is. Sunshine uses
  hardware H.265/AV1 encoding with the iGPU, UDP transport with FEC, and
  Moonlight's native client does hardware decode. This is the gold standard
  for low-latency gaming. KasmVNC does NOT replace this.
- **Web view apps** (Home Assistant, Pi-hole, Jellyfin, Router) — these use
  HTTP iframes, not VNC. Unaffected.

---

## Current Architecture

### The VNC pipeline (per container/VM)

```
┌─────────────────── Inside LXC Container ───────────────────┐
│                                                             │
│  Application (Kodi / Chromium / Moonlight)                  │
│       │                                                     │
│       ▼                                                     │
│  sway compositor (headless Wayland, HEADLESS-1 output)      │
│       │                                                     │
│       ▼                                                     │
│  wayvnc (captures Wayland display → RFB on :5900)           │
│       │                                                     │
│       ▼                                                     │
│  websockify (RFB :5900 → WebSocket :608X)                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼  (WebSocket over TCP)
┌─────────────────── Proxmox Host ───────────────────────────┐
│                                                             │
│  socat / iptables DNAT                                      │
│  (port forward from host :608X → container :608X)           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼  (WebSocket over TCP)
┌─────────────────── Browser ────────────────────────────────┐
│                                                             │
│  noVNC (JS library)                                         │
│  Connects to ws://<host_ip>:608X                            │
│  Decodes RFB tiles in JavaScript → renders on <canvas>      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Desktop VM (QEMU) is slightly different:**
```
QEMU VM → Proxmox creates /var/run/qemu-server/{vmid}.vnc (Unix socket)
         → websockify bridges Unix socket → WebSocket :6081 on host
         → noVNC in browser connects to ws://<host_ip>:6081
```

### Port assignments

| App        | VNC Port (inside CT) | WebSocket Port | Handler Type   |
|------------|---------------------|----------------|----------------|
| Kiosk      | 5900                | 6080           | wayland_vnc    |
| Desktop VM | (QEMU internal)     | 6081           | qemu_vnc       |
| Kodi       | 5900                | 6082           | wayland_vnc    |
| Moonlight  | 5900                | 6083           | wayland_vnc    |

### Packages baked into container images (build-images.sh)

Each VNC-capable container image bakes these packages:

- `sway` — Wayland compositor (headless output driver)
- `wayvnc` — Wayland-native VNC server (captures sway display)
- `python3-websockify` — WebSocket-to-TCP bridge (Python)
- `xwayland` — X11 compatibility layer (for apps that don't support Wayland)

### Systemd services per container (3-service chain)

Each container has a 3-service dependency chain:

1. **`<app>-display.service`** — starts sway compositor
   - `ExecStart=/usr/bin/sway`
   - Creates `HEADLESS-1` virtual output at 1920x1080
   - Runs as the app's service user (kodi, moonlight, kiosk)

2. **`<app>-vnc.service`** — starts wayvnc VNC server
   - `After=<app>-display.service`, `Requires=<app>-display.service`
   - `ExecStart=/usr/bin/wayvnc --render-cursor 0.0.0.0 5900`
   - `ExecStartPre=/bin/sleep 3` (wait for sway to initialize)

3. **`<app>-vnc-ws.service`** — starts websockify bridge
   - `After=<app>-vnc.service`, `Requires=<app>-vnc.service`
   - `ExecStart=/usr/bin/websockify 0.0.0.0:608X localhost:5900`
   - `ExecStartPre=/bin/sleep 2` (wait for wayvnc to bind)

Service names by app:
- Kiosk: `kiosk-display`, `kiosk-vnc`, `kiosk-vnc-ws`
- Kodi: `kodi-display`, `kodi-vnc`, `kodi-vnc-ws`
- Moonlight: `moonlight-display`, `moonlight-vnc`, `moonlight-vnc-ws`

Desktop VM uses only 1 host-side service:
- `desktop-vnc-ws.service` — websockify bridging QEMU Unix socket to :6081

### Host-side port forwarding

Each Proxmox host forwards WebSocket traffic from the host to the container:

- **WAN hosts**: iptables DNAT on the WAN bridge interface
- **LAN hosts (router_nodes)**: socat TCP proxy (avoids hairpin NAT)
- **LAN hosts (lan_hosts like mesh1)**: socat TCP proxy

Systemd units deployed by provisioning roles:
- `kiosk-vnc-proxy.service` — socat for kiosk VNC WS port (LAN hosts)
- `kodi-vnc-proxy.service` — socat for kodi VNC WS port
- `moonlight-vnc-proxy.service` — socat for moonlight VNC WS port
- `desktop-vnc-ws.service` — websockify for desktop QEMU VNC

VNC relay for LAN hosts (on router node):
- `kiosk-vnc-relay-<hostname>.service` — socat relay per LAN host

### Browser-side (noVNC)

Static JS assets: `scripts/webui/static/noVNC/` (728KB, 52 JS files)

Key files:
- `scripts/webui/pages/vnc_shared.py` — shared VNC viewer utilities
  - `mount_static()` — serves noVNC JS from `/static/noVNC/`
  - `render_vnc_canvas()` — emits CSS + canvas div + init script
  - `vnc_init_script()` — imports `RFB` from rfb.js, connects to ws:// URL
  - `pointer_passthrough_css()` — disables pointer-events on NiceGUI #app so
    noVNC canvas receives mouse input (critical hack for interactivity)
  - `_overlay_guard_script()` — repositions VNC div and manages Quasar overlay conflicts
- `scripts/webui/pages/console.py` — unified display console route
  - Routes: `/console/{node_id}/{app_id}`
  - VNC apps → `render_vnc_canvas()`, Web apps → iframe
- `scripts/webui/pages/remote_kiosk.py` — kiosk VNC viewer route
  - Routes: `/remote/{node_id}`
  - Direct VNC connection to kiosk's wayvnc/websockify stack

### Display Transfer Service

`scripts/webui/display_transfer.py` — handler-registry architecture:

- `DisplayHandler` protocol with `enter()`, `exit()`, `is_active()`, `get_viewstream_url()`
- Three concrete handlers:
  - `QemuVncHandler` — QEMU VMs (qm start/stop, ws:// URL)
  - `WaylandVncHandler` — LXC containers (pct start/stop, ws:// URL)
  - `WebViewHandler` — HTTP web UIs (no lifecycle, http:// URL)
- `DisplayTransferService` — registry + conflict resolution
- `build_handler()` factory builds handlers from `DisplayAppConfig`

Display app configs in `scripts/webui/data.py` (`DISPLAY_APP_CONFIGS` dict):
```python
"kiosk":     handler_type="wayland_vnc", ct_id="401", vnc_ws_port=6080
"desktop":   handler_type="qemu_vnc",    vmid="400",  vnc_ws_port=6081
"kodi":      handler_type="wayland_vnc", ct_id="301", vnc_ws_port=6082
"moonlight": handler_type="wayland_vnc", ct_id="302", vnc_ws_port=6083
```

Port constants in `data.py` (`Ports` class):
```python
KIOSK_VNC = 5900
KIOSK_VNC_WS = 6080
DESKTOP_VNC_WS = 6081
KODI_VNC_WS = 6082
MOONLIGHT_VNC_WS = 6083
```

### Test coverage

**Unit tests** (`tests/test_display_transfer.py`):
- 30+ tests covering all handler types, factory, service registry, conflict resolution
- Uses `SshStub` (not mocks) for SSH calls — records calls and returns configurable responses
- Tests: protocol compliance, viewstream URL construction, enter/exit success/failure,
  already-running detection, is_active true/false, conflict resolution, unknown handler errors
- Integration tests verify `DISPLAY_APP_CONFIGS` entries build valid handlers

**Molecule verify** (`molecule/default/verify.yml`):
- Kiosk: VNC service enabled checks, wayvnc/websockify package checks, DNAT/socat proxy checks, relay checks
- Kodi: baked content checks (sway, wayvnc, websockify, service enabled), kodi-vnc-proxy running
- Moonlight: baked content checks, moonlight-vnc-proxy running
- Desktop: desktop-vnc-ws (websockify) service active

---

## Suggested Solution: KasmVNC

### Why KasmVNC

KasmVNC is a heavily modified fork of TigerVNC optimized for browser-based
remote desktop. It replaces the entire `wayvnc + websockify + noVNC` stack
with a single binary that:

1. **Has a built-in WebSocket server** — no websockify bridge needed
2. **Uses modern codecs** — WebP, JPEG, auto-detecting video regions and
   switching to H.264-like encoding for moving content
3. **Has DRI3 GPU acceleration** — can use the iGPU for rendering (not encode,
   but still much faster than pure software)
4. **Supports adaptive quality** — automatically adjusts based on client bandwidth
5. **Has a built-in web client** — no noVNC JS library needed
6. **Supports audio streaming** — built-in PulseAudio/PipeWire capture
7. **Multi-user capable** — read-only viewers, clipboard sync, file transfer

### What changes

**KasmVNC runs on X11, not Wayland.** This is the biggest architectural change.
The current pipeline uses `sway` (Wayland compositor). KasmVNC captures an
X11 display via its built-in Xvnc server (virtual X11 display + VNC server
in one process). This means:

- `sway` → **replaced by** KasmVNC's built-in Xvnc (virtual X11 display)
- `wayvnc` → **replaced by** KasmVNC (VNC server is part of Xvnc)
- `websockify` → **eliminated** (KasmVNC has built-in WebSocket)
- `noVNC` → **eliminated** (KasmVNC has built-in web client)
- `xwayland` → **no longer needed** (apps run directly on X11)

The 3-service chain becomes 1:
```
Before: sway → wayvnc → websockify  (3 processes, 3 systemd units)
After:  KasmVNC Xvnc                (1 process, 1 systemd unit)
```

### Architecture after migration

```
┌─────────────────── Inside LXC Container ───────────────────┐
│                                                             │
│  Application (Kodi / Chromium / Moonlight)                  │
│       │                                                     │
│       ▼                                                     │
│  KasmVNC Xvnc (virtual X11 display + VNC + WebSocket)       │
│  Listens on :608X (WebSocket) directly                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼  (WebSocket over TCP, KasmVNC protocol)
┌─────────────────── Proxmox Host ───────────────────────────┐
│                                                             │
│  socat / iptables DNAT                                      │
│  (port forward — UNCHANGED from current architecture)       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼  (WebSocket over TCP)
┌─────────────────── Browser ────────────────────────────────┐
│                                                             │
│  KasmVNC web client (served by KasmVNC itself)              │
│  OR embedded via iframe at https://<host_ip>:608X           │
│  Decodes WebP/JPEG/video in browser (much lighter than RFB) │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### What stays the same

- **Port assignments** — same ports (6080-6083), just served by KasmVNC instead of websockify
- **Host-side forwarding** — socat/iptables DNAT rules unchanged
- **Display Transfer Service** — handler architecture stays the same, just URL format changes
- **NiceGUI management UI** — completely unchanged (it's the management layer, not the streaming layer)
- **Sunshine → Moonlight** — completely unchanged (gaming streaming is separate)
- **Web view apps** (HA, Pi-hole, etc.) — unchanged, they use iframes
- **VNC relay for LAN hosts** — unchanged socat relays
- **Conflict resolution** — unchanged (mutual exclusivity between display apps)

### Suggested new architecture — files and changes

#### 1. Image builds (`scripts/build-images.sh`)

**Remove** from all VNC-capable images:
- `sway`
- `wayvnc`
- `python3-websockify`
- `xwayland`
- All 3-service systemd unit files (`<app>-display`, `<app>-vnc`, `<app>-vnc-ws`)
- `sway` config files (`~/.config/sway/config`)

**Add** to all VNC-capable images:
- `kasmvncserver` package (from KasmVNC's Debian repo or pre-built `.deb`)
- A single systemd unit: `<app>-display.service`
  ```
  ExecStart=/usr/bin/kasmvncserver :1 \
    --websocketPort 608X \
    --SecurityTypes None \
    --geometry 1920x1080 \
    --depth 24 \
    --select-de none
  ```
- KasmVNC config (`~/.vnc/kasmvnc.yaml`) for quality/performance tuning
- X11 startup script (`.xinitrc` or `.xsession`) to launch the application

**Affected images** (4 total):
- Kiosk (CT 401) — currently has Chromium running under sway
- Kodi (CT 301) — currently has Kodi running under sway
- Moonlight (CT 302) — currently has Moonlight running under sway
- Desktop VM (VM 400) — different: uses QEMU VNC, may stay as-is or switch

**Desktop VM special case:**
The Desktop VM currently uses QEMU's built-in VNC (Unix socket on the Proxmox
host, bridged by websockify). Options:
- **Option A**: Keep QEMU VNC + websockify (simplest, no change inside VM)
- **Option B**: Install KasmVNC inside the Debian VM and bypass QEMU VNC
  (better quality, same stack as LXC containers, but requires more work)

Recommend Option A for now — QEMU VNC is adequate for a desktop VM that
primarily uses iGPU passthrough with a physical display.

#### 2. Web UI — browser-side changes

**Remove:**
- `scripts/webui/static/noVNC/` directory (728KB, 52 JS files)
- `vnc_shared.py` functions that reference noVNC: `mount_static()`, `vnc_init_script()`,
  `render_vnc_canvas()`, `pointer_passthrough_css()`, `_overlay_guard_script()`

**Replace with:**
KasmVNC serves its own web client. The console page becomes an iframe pointing
at the KasmVNC web client URL instead of a noVNC canvas:

```python
# Before (noVNC canvas)
render_vnc_canvas(f"ws://{host_ip}:6082")

# After (KasmVNC iframe)
ui.element("iframe").props(f'src="https://{host_ip}:6082"').classes("viewer-frame")
```

This eliminates the entire `pointer_passthrough_css()` hack — no more fighting
NiceGUI/Quasar for mouse events. The iframe is a standard HTML element that
NiceGUI doesn't interfere with. (Same pattern already used by `WebViewHandler`
for Home Assistant, Pi-hole, etc.)

**Files to modify:**
- `scripts/webui/pages/vnc_shared.py` — heavily simplified or deleted. KasmVNC
  pages just need the viewer bar CSS and an iframe
- `scripts/webui/pages/console.py` — VNC case becomes iframe (like web case)
- `scripts/webui/pages/remote_kiosk.py` — same change
- `scripts/webui/display_transfer.py` — `get_viewstream_url()` returns
  `https://<ip>:608X` instead of `ws://<ip>:608X`. Handler type may unify
  (all become "web" since they're all iframes now)
- `scripts/webui/data.py` — `DisplayType.VNC` may be removed or simplified.
  All display apps produce an HTTP(S) URL, not a WebSocket URL

**Potential handler simplification:**
```python
# Before: two VNC handler types + one web handler
QemuVncHandler    → ws:// URL, noVNC canvas
WaylandVncHandler → ws:// URL, noVNC canvas
WebViewHandler    → http:// URL, iframe

# After: possibly just two handler types
QemuVncHandler    → ws:// URL, noVNC canvas  (Desktop VM only, if keeping QEMU VNC)
IframeHandler     → https:// URL, iframe     (all KasmVNC apps + web apps)
```

#### 3. Ansible roles

**Affected roles:**
- `roles/kodi_lxc/tasks/main.yml` — port forwarding stays, just remove
  websockify references from comments/service names
- `roles/kodi_configure/tasks/main.yml` — minimal changes (already tiny)
- `roles/moonlight_lxc/tasks/main.yml` — same as kodi
- `roles/moonlight_configure/tasks/main.yml` — minimal
- `roles/kiosk_lxc/tasks/main.yml` — socat proxy stays, comments updated
- `roles/desktop_vm/tasks/main.yml` — if keeping QEMU VNC: websockify stays;
  if switching: replace with KasmVNC inside VM

**Host-side services that stay unchanged:**
- `kiosk-vnc-proxy.service` (socat) — just forwarding TCP, doesn't care about protocol
- `kodi-vnc-proxy.service` (socat) — same
- `moonlight-vnc-proxy.service` (socat) — same
- `kiosk-vnc-relay-<host>.service` (socat) — same
- Manager API proxy, DNAT rules — all unchanged

**Host-side service that changes (Desktop VM only):**
- `desktop-vnc-ws.service` — currently websockify bridging QEMU Unix socket.
  If keeping QEMU VNC: no change. If switching: remove this service entirely.

#### 4. Tests

**Unit tests** (`tests/test_display_transfer.py`):
- Update URL assertions: `ws://` → `https://` for KasmVNC apps
- Possibly simplify handler types (WaylandVncHandler → IframeHandler)
- Keep SshStub pattern (SSH calls to pct start/stop are unchanged)
- Remove noVNC-specific assertions

**Molecule verify** (`molecule/default/verify.yml`):
- Replace package checks: `wayvnc=yes, websockify=yes` → `kasmvncserver=yes`
- Replace service checks: `kodi-vnc=enabled, kodi-vnc-ws=enabled` →
  `kodi-display=enabled` (single service)
- Keep host-side proxy checks (socat/DNAT — unchanged)
- Add KasmVNC-specific checks: config file exists, port listening

#### 5. Cleanup

**`playbooks/cleanup.yml`** — update service names:
- Remove references to `<app>-vnc.service` and `<app>-vnc-ws.service`
- Keep `<app>-display.service` (now runs KasmVNC instead of sway)

---

## KasmVNC Installation Details

### Package source

KasmVNC provides `.deb` packages for Debian 12 (bookworm) on amd64:
- GitHub releases: https://github.com/kasmtech/KasmVNC/releases
- Or their apt repository

The package installs to `/usr/bin/kasmvncserver` and `/usr/bin/vncserver`.

### Container requirements

- **No nesting change** — KasmVNC runs in userspace, no kernel features needed
  beyond what's already configured
- **DRI3 acceleration** — uses `/dev/dri/renderD*` if available (already
  bind-mounted for Kodi/Moonlight/Kiosk containers via `lxc_device_passthrough.yml`)
- **Audio** — KasmVNC can capture PulseAudio. Current containers have ALSA
  passthrough; may need PipeWire/PulseAudio for audio streaming
- **X11 only** — KasmVNC does NOT support Wayland capture. Apps must run on
  the X11 display that KasmVNC's Xvnc creates

### Application compatibility

All current display apps support X11:
- **Kodi** — has X11 backend, widely tested
- **Chromium** — runs on X11 natively (currently uses `--ozone-platform=wayland`)
- **Moonlight** — has X11 support via SDL2
- **Desktop VM** — runs its own display server inside the VM (not affected)

### KasmVNC config example (`kasmvnc.yaml`)

```yaml
desktop:
  resolution:
    width: 1920
    height: 1080
  allow_resize: true

network:
  protocol: http
  websocket_port: 6082
  ssl:
    require_ssl: false

encoding:
  max_frame_rate: 30
  rect_encoding_mode:
    min_quality: 7
    max_quality: 9
  video_encoding_mode:
    jpeg_quality: -1
    webp_quality: -1
    max_frame_rate: 30
```

---

## Complexity Assessment

### Low risk / straightforward
- Removing noVNC static files and JS initialization code
- Updating URL format in display_transfer.py handlers
- Host-side socat/DNAT forwarding (unchanged)
- Unit test updates (mechanical URL changes)

### Medium risk / requires testing
- KasmVNC package installation and systemd service setup in build-images.sh
- Application startup under X11 instead of Wayland (Kodi, Chromium, Moonlight)
- KasmVNC iframe embedding in NiceGUI pages (X-Frame-Options, CORS)
- DRI3 GPU acceleration configuration
- Molecule verify assertion updates

### Higher risk / may need iteration
- Kodi under X11 — may need different launch flags than Wayland
- Moonlight under X11 — SDL2 X11 backend may have different behavior
- KasmVNC web client embedding — may need custom config to disable
  KasmVNC's own toolbar/chrome when embedded in our viewer bar
- Audio streaming — current ALSA passthrough may not work with KasmVNC's
  PulseAudio capture

---

## Scope Boundary

### IN scope
- Replace wayvnc + websockify + noVNC with KasmVNC in LXC containers (kiosk, kodi, moonlight)
- Update build-images.sh for all 3 LXC container images
- Update web UI console/viewer pages
- Update display_transfer.py handler architecture
- Update molecule verify assertions
- Update unit tests

### OUT of scope (separate projects)
- Desktop VM display (keep QEMU VNC + websockify for now)
- Sunshine → Moonlight gaming streaming (stays as-is, different use case)
- Web view apps (Home Assistant, Pi-hole, etc. — already iframes)
- Manager API, heartbeat, fleet management — unrelated to display streaming
- NiceGUI framework itself — stays as the management UI framework

---

## File Reference (complete list of files that will be touched)

### Build scripts
- `scripts/build-images.sh` — kiosk, kodi, moonlight image build functions

### Web UI (browser-side)
- `scripts/webui/static/noVNC/` — **DELETE entire directory** (728KB)
- `scripts/webui/pages/vnc_shared.py` — heavy rewrite or delete
- `scripts/webui/pages/console.py` — VNC rendering → iframe
- `scripts/webui/pages/remote_kiosk.py` — VNC rendering → iframe
- `scripts/webui/display_transfer.py` — handler URL format, possible type simplification
- `scripts/webui/data.py` — `DisplayType`, `Ports`, `DisplayAppConfig` updates

### Ansible roles
- `roles/kodi_lxc/tasks/main.yml` — comment/service name updates
- `roles/moonlight_lxc/tasks/main.yml` — comment/service name updates
- `roles/kiosk_lxc/tasks/main.yml` — comment/service name updates
- `roles/desktop_vm/tasks/main.yml` — no change (keeping QEMU VNC)

### Tests
- `tests/test_display_transfer.py` — URL format, handler type updates
- `molecule/default/verify.yml` — package/service assertion updates

### Cleanup/playbooks
- `playbooks/cleanup.yml` — service name updates
- `playbooks/site.yml` — no structural changes (same plays, same roles)

### Documentation
- `docs/manual-testing-playbooks.md` — update VNC service check steps
- `docs/architecture/overview.md` — update VNC pipeline description

---

## Implementation Outcomes

### Completed (2026-04-14)

**Scope change from plan:** The Desktop VM was included in the migration
(Option B — KasmVNC inside the VM) rather than kept on QEMU VNC as the
notes originally suggested. All 4 display apps now use KasmVNC.

**Key implementation details:**

1. **`install_kasmvnc()` helper function** added to `build-images.sh` —
   shared by kiosk, kodi, and moonlight LXC builds. Takes user, port,
   display number, service name, and xstartup content. Desktop VM installs
   KasmVNC inline (inside the VM via SSH heredoc).

2. **Handler hierarchy simplified:**
   - `_VncHandlerBase` → `_ManagedDisplayBase` (shared managed display logic)
   - `QemuVncHandler` → `VmDisplayHandler` (for VMs)
   - `WaylandVncHandler` → `ContainerDisplayHandler` (for LXC)
   - All generate `http://` URLs (KasmVNC serves its own web client)

3. **vnc_shared.py deleted**, replaced by `display_shared.py` with generic
   iframe utilities (no noVNC dependencies).

4. **Port assignments unchanged** — 6080 (kiosk), 6081 (desktop), 6082
   (kodi), 6083 (moonlight). Same host-side socat/DNAT forwarding.

5. **Image size changes:**
   - Kiosk: 508→509 MB (marginal increase)
   - Kodi: 369→353 MB (16 MB decrease — sway+wayvnc+websockify larger than KasmVNC)
   - Moonlight: 297→266 MB (31 MB decrease)

**Bug found during build:** The `install_kasmvnc()` helper function
initially failed on the kiosk build because `apt-get update` was not run
before `apt-get install /tmp/kasmvnc.deb`. The kiosk build cleans apt
lists (`rm -rf /var/lib/apt/lists/*`) before calling the helper. Fix:
added `apt-get update -qq` to the helper function.

### Manual testing outcomes (2026-04-14)

**Playbook 13 executed — ALL sections passed:**

| Section | Result |
|---------|--------|
| 13.1 KasmVNC display service health | ALL PASS — 6/6 kiosk, kodi, moonlight, desktop |
| 13.2 SM→CM drill-down via iframe | PASS — WebSocket 101 on all nodes |
| 13.3 App console switching | PASS — Kodi, Desktop, Moonlight consoles work |
| 13.4 Fleet dashboard display integration | PASS — Open Kiosk + app links on node details |
| 13.5 Direct SM access to all 6 kiosk displays | ALL PASS — 6/6 via DNAT/socat/relay |
| 13.6 CM display from own web UI | PASS — resolves via _child_managers path |
| 13.7 Error/edge cases | PASS — error page, stopped service, no noVNC artifacts |
| 13.8 Host × app verification matrix | ALL PASS — every cell verified |

**Bugs found during manual testing:**

1. **`-disableBasicAuth` flag is invalid.** KasmVNC 1.4.0's Xvnc accepts
   `-DisableBasicAuth` (capital D, Xvnc option), not `-disableBasicAuth`
   (lowercase, vncserver option). The lowercase flag was silently ignored,
   leaving basic auth enabled (401 on iframe requests).

2. **KasmVNC 1.4.0 requires a user even with DisableBasicAuth.** Without at
   least one user in the passwd file, vncserver exits with "No users
   configured and prompting is prohibited." The `vncpasswd -u <user> -ow`
   call in `install_kasmvnc()` creates the required user.

3. **`hw3d: true` crashes without `/dev/dri` access.** LXC containers
   without DRI device passthrough fail with "Failed to create gbm." The
   `kasmvnc.yaml` config must set `hw3d: false` as the safe default.

4. **Desktop VM had stale service file.** The deployed VM was from a
   pre-fix image, so it still had the `-disableBasicAuth` flag. Hot-patched
   via `qm guest exec` + `sed`; permanent fix in `build-images.sh`.

All fixes are baked into `build-images.sh` and will take effect on the
next image rebuild + molecule test cycle.

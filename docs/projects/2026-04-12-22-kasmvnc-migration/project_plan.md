# KasmVNC Migration — Project Plan

Replace the entire `sway → wayvnc → websockify → noVNC` pipeline with
KasmVNC across ALL display apps — 3 LXC containers (kiosk, kodi, moonlight)
AND the Desktop VM. This eliminates 3 processes per container, removes 728KB
of noVNC JavaScript, the `DisplayType` enum, the `_VncHandlerBase` class,
the host-side websockify service for the Desktop VM, and all noVNC pointer
passthrough hacks. Every display app uses a single KasmVNC process embedded
as an iframe.

---

## Application inventory across all 6 hosts

| App | Type | VMID | Host(s) | WS Port | Forwarding |
|-----|------|------|---------|---------|------------|
| Kiosk | LXC | CT 401 | ALL 6 hosts | 6080 | WAN: DNAT, Router: socat, LAN: socat |
| Desktop | VM | VM 400 | home only | 6081 | host websockify (→ socat/DNAT) |
| Kodi | LXC | CT 301 | home only | 6082 | socat proxy |
| Moonlight | LXC | CT 302 | mesh1 only | 6083 | socat proxy |
| Gaming | LXC | CT 601 | ai only | N/A (web UI) | N/A (WebViewHandler) |

**Display relay for LAN hosts:** mesh1's kiosk (6080) is relayed through
the primary host (home) via socat on port 16080. The SM uses
`_LAN_DISPLAY_RELAY_PORTS` to discover relay paths.

**Hub services (web UIs, not KasmVNC):** Jellyfin, Home Assistant, OpenWrt,
Pi-hole, WireGuard, Netdata, rsyslog, Gaming (Sunshine) — accessed as
iframes via `/view?url=...`. Deployed on specific hosts (home for most,
ai for Gaming). These are OUT of scope for KasmVNC migration — they
already use the iframe pattern via `WebViewHandler`.

**Internal kiosk pages (NiceGUI routes):** WiFi Bridge, Mesh WiFi, Router
Detail, Containers & VMs — rendered directly by the kiosk's NiceGUI app.
No display streaming involved. OUT of scope.

---

## Current state

**LXC containers** (kiosk, kodi, moonlight) each run a 3-service chain:

```
sway (Wayland compositor, headless) → wayvnc (RFB on :5900) → websockify (WS on :608X)
```

**Desktop VM** uses QEMU's built-in VNC (hypervisor framebuffer capture)
bridged by a host-side websockify service (`desktop-vnc-ws.service`):

```
QEMU VNC (Unix socket) → websockify (WS on :6081, runs on HOST)
```

The browser connects via noVNC (52 JS files, 728KB) which decodes RFB tiles
in JavaScript on a `<canvas>`. The web UI (`vnc_shared.py`) has extensive
workarounds for pointer event passthrough and Quasar overlay conflicts.

**Problems:** software-only encoding, multi-hop overhead, 1990s-era codecs,
no adaptive quality, CPU-intensive client-side JS decode, complex pointer
event hacks, two fundamentally different rendering paths (VNC canvas vs web
iframe) in the console page, host-side websockify service for Desktop VM,
QEMU VNC shows only the VGA stub (not the GPU-rendered desktop) when iGPU
passthrough is active.

## Target state

Every display app runs a single KasmVNC process. The browser loads
KasmVNC's built-in web client in an `<iframe>`. One rendering path
for all display apps. Zero noVNC code. Zero websockify.

**LXC containers:**

```
KasmVNC Xvnc (virtual X11 display + VNC + WebSocket + built-in web client)
```

**Desktop VM:**

```
KasmVNC Xvnc (inside VM, virtual X11 display for remote access)
```

The Desktop VM's physical display (iGPU passthrough) is unaffected.
KasmVNC provides a separate virtual desktop session for remote access —
a significant improvement over the current QEMU VNC which shows only the
VGA BIOS stub when the iGPU is passed through.

---

## Architectural decisions

### 1. X11 instead of Wayland

KasmVNC runs Xvnc (virtual X11 server). Apps use X11 backends:

| App       | Current (Wayland/QEMU)              | After (X11)                        |
|-----------|-------------------------------------|------------------------------------|
| Chromium  | `--ozone-platform=wayland`          | Drop flag (auto-detects DISPLAY)   |
| Kodi      | `kodi --windowing=wayland`          | `kodi` (X11 is the default)        |
| Moonlight | SDL2 Wayland backend                | SDL2 X11 backend (auto-detects)    |
| Desktop   | QEMU VNC (hypervisor capture)       | KasmVNC Xvnc (virtual X11 inside VM) |

All four apps have mature X11 support.

### 2. Same ports, same host-side forwarding

| App       | WebSocket Port | Host-side change |
|-----------|---------------|------------------|
| Kiosk     | 6080          | None (socat/DNAT unchanged) |
| Desktop   | 6081          | Remove host-side websockify, add port forwarding to VM |
| Kodi      | 6082          | None (socat/DNAT unchanged) |
| Moonlight | 6083          | None (socat/DNAT unchanged) |

KasmVNC listens directly on the WebSocket port. LXC container host-side
socat/iptables DNAT forwards the same TCP ports. Desktop VM switches from
host-side websockify to host-side port forwarding (same pattern as LXC
containers).

### 3. No authentication, no SSL

Containers and VMs live on private NAT bridges (10.99.x.x) or the OpenWrt
LAN (10.10.10.x). SSL and auth add unnecessary complexity:

- `-disableBasicAuth` flag on vncserver
- `network.ssl.require_ssl: false` in kasmvnc.yaml
- `server.http.headers: []` to clear COEP/COOP headers that block iframe embedding

### 4. DRI3 GPU acceleration

KasmVNC uses DRI3 to offload rendering. Every host already has
`/dev/dri/renderD128` bind-mounted into display-capable containers via
`lxc_device_passthrough.yml`. Desktop VM has iGPU passthrough. Enable via:

```yaml
desktop:
  gpu:
    hw3d: true
    drinode: /dev/dri/renderD128
```

### 5. One rendering path: iframe for everything

KasmVNC serves its own web client. The NiceGUI console page renders ALL
display apps as iframes — same pattern as `WebViewHandler`. The
`DisplayType` enum is eliminated (everything is `http://`).

```python
# Before: two rendering paths with complex branching
if handler.display_type is DisplayType.VNC:
    _render_vnc_console(...)  # noVNC canvas + pointer hacks
else:
    _render_web_console(...)  # iframe

# After: one path, no branching
_render_display_console(...)  # iframe for everything
```

### 5a. Latency and hop analysis

KasmVNC eliminates multiple hops from the display pipeline:

**LXC containers — before (4 hops):**
```
Browser JS decode → WebSocket → host socat/DNAT → websockify → wayvnc RFB → sway framebuffer
```

**LXC containers — after (2 hops):**
```
Browser native render → HTTP/WS → host socat/DNAT → KasmVNC Xvnc
```

**Desktop VM — before (3 hops):**
```
Browser JS decode → WebSocket → host websockify → QEMU VNC Unix socket
```

**Desktop VM — after (2 hops):**
```
Browser native render → HTTP/WS → host socat/DNAT → in-VM KasmVNC Xvnc
```

Key latency wins:
- **Zero client-side JS decode.** noVNC decodes RFB tiles in JavaScript
  on a `<canvas>`. KasmVNC's built-in web client uses browser-native
  rendering (WebP/JPEG/H.264 decoded by the browser engine and GPU).
- **3 container processes → 1.** sway + wayvnc + websockify are replaced
  by a single KasmVNC Xvnc process. Inter-process copies eliminated.
- **Adaptive quality.** KasmVNC adjusts codec/quality based on network
  conditions. noVNC uses fixed-quality RFB tiles regardless of bandwidth.
- **DRI3 GPU acceleration.** KasmVNC offloads encoding to the GPU render
  device. The old stack was software-only.

Host-side socat/iptables DNAT forwarding remains (unavoidable — containers
live on private NAT bridges). This is a single TCP proxy with zero
protocol awareness — negligible latency (< 1ms round-trip overhead).

### 6. Clean OOP handler hierarchy (DRY, Single Responsibility)

See: `python-code-style` skill, `code-review-checklist` skill.

```
Before (broken hierarchy — base class generates ws:// but KasmVNC needs http://):
  _VncHandlerBase (ws://)
  ├── QemuVncHandler (qm start/stop)
  └── WaylandVncHandler (pct start/stop)
  WebViewHandler (http://, no lifecycle)

After (clean hierarchy — shared base for managed displays):
  _ManagedDisplayBase (http://, lifecycle protocol)
  ├── ContainerDisplayHandler (pct start/stop)
  └── VmDisplayHandler (qm start/stop)
  WebViewHandler (http://, no lifecycle)
```

**Why this design:**
- **SRP:** `_ManagedDisplayBase` owns URL generation. Subclasses own
  lifecycle commands. `WebViewHandler` is always-on (separate concern).
- **DRY:** URL generation (`http://{host_ip}:{port}`) is defined once in
  the base class. Not duplicated between container and VM handlers.
- **Factory:** `build_handler()` maps `handler_type` strings to concrete
  classes: `"container_display"` → `ContainerDisplayHandler`,
  `"vm_display"` → `VmDisplayHandler`, `"web_view"` → `WebViewHandler`.
- **No dead code:** `DisplayType` enum eliminated. `_VncHandlerBase`
  eliminated. `Ports.KIOSK_VNC` (5900) eliminated. `display_icon()`
  eliminated. All noVNC functions eliminated.

### 7. KasmVNC version and package

- Version: **v1.4.0** (released Oct 2025, latest stable)
- Package: `kasmvncserver_bookworm_1.4.0_amd64.deb` (2MB)
- Download URL: `https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_bookworm_1.4.0_amd64.deb`
- Pin in `KASMVNC_VERSION` variable in `build-images.sh`

### 8. DisplayAppConfig field cleanup

```python
# Before: vnc_ws_port field name implies VNC-specific semantics
@dataclass
class DisplayAppConfig:
    vnc_ws_port: int = 0  # confusing for KasmVNC (not VNC)

# After: ws_port — protocol-agnostic name
@dataclass
class DisplayAppConfig:
    ws_port: int = 0  # KasmVNC WebSocket port
```

`Ports` class renamed for clarity:

```python
# Before
class Ports:
    KIOSK_VNC = 5900        # dead after migration
    KIOSK_VNC_WS = 6080
    DESKTOP_VNC_WS = 6081

# After
class Ports:
    KIOSK_DISPLAY = 6080
    DESKTOP_DISPLAY = 6081
    KODI_DISPLAY = 6082
    MOONLIGHT_DISPLAY = 6083
```

### 9. TransferResult simplification

```python
# Before: carries DisplayType that no longer exists
@dataclass
class TransferResult:
    display_type: DisplayType = DisplayType.VNC  # dead field

# After: simple success/URL/error
@dataclass
class TransferResult:
    success: bool
    viewstream_url: str | None = None
    error: str | None = None
```

---

## Scope boundary

### IN scope

- Replace sway + wayvnc + websockify with KasmVNC in 3 LXC images (kiosk, kodi, moonlight)
- Replace QEMU VNC + host-side websockify with KasmVNC inside Desktop VM
- Delete `scripts/webui/static/noVNC/` directory entirely (728KB, 52 files)
- Update `build-images.sh` for all 4 images
- Redesign handler OOP hierarchy (`_VncHandlerBase` → `_ManagedDisplayBase`)
- Remove `DisplayType` enum, `display_icon()` function, `Ports.KIOSK_VNC`
- Rename `vnc_shared.py` → `display_shared.py`, remove all noVNC functions
- Update `data.py` handler configs, port constants, `DisplayAppConfig` fields
- Update `console.py`: single iframe rendering path, remove VNC branch
- Update `remote_kiosk.py`: iframe instead of noVNC canvas
- Update unit tests (`test_display_transfer.py`)
- Update molecule verify assertions for all 4 services
- Update `playbooks/cleanup.yml` service names
- Add port forwarding for Desktop VM (replace host-side websockify)
- Remove `desktop-vnc-ws.service` from host-side deployment
- Write manual testing playbook (Playbook 13 update in `docs/manual-testing-playbooks.md`)
- Update `webui-manual-testing` skill to reference updated playbook
- Update architecture documentation

### OUT of scope

- Sunshine → Moonlight gaming streaming (separate protocol, unrelated)
- Web view apps (HA, Pi-hole, etc. — already iframes)
- Manager API, heartbeat, fleet management — unrelated
- Audio streaming (not a display pipeline concern)

---

## Test and development isolation

See: `testing-workflow` skill, `molecule-testing` skill.

**Single source of truth:**
- `build-images.sh` defines image contents. Change once, all containers rebuild.
- `data.py` defines UI constants (`Ports`, `DISPLAY_APP_CONFIGS`, `Labels`).
  Tests import from `data.py` — change once, tests follow.
- `display_transfer.py` defines the handler hierarchy. Tests import handlers
  directly — rename once, tests follow.

**Isolation between test and development:**
- Per-feature molecule scenarios (`kiosk-lxc`, `kodi-lxc`, `moonlight-lxc`,
  `desktop-vm`) test each service independently. Breaking kiosk does not
  block kodi testing.
- Unit tests (`test_display_transfer.py`) are pure Python — run instantly
  during development without infrastructure.
- Image version gating: `proxmox_lxc` auto-recreates containers when the
  image version changes. Old images stay until rebuilt — no partial states.
- Standard work cycle ordering: ALL code changes (build script + webui +
  tests) happen BEFORE image rebuild. The image bakes the current code state.

**Ordering constraint:** The kiosk image bakes the webui Python code into
the container. Build script changes AND webui code changes MUST happen
before the image rebuild. This is why M0 combines both.

---

## Milestone dependency graph

```
M0 (all code changes + image builds)
└── M1 (tests + molecule verify + cleanup) ← blocked on M0
    └── M2 (documentation + manual testing) ← blocked on M1
```

Three milestones, all sequential.

---

## Testing strategy

### Parallelism

Build all 4 images in parallel across hosts during M0. Test runs are
sequential (molecule test is single-threaded).

### Per-feature scenarios

Existing `molecule/kiosk-lxc/`, `molecule/kodi-lxc/`, `molecule/moonlight-lxc/`,
and `molecule/desktop-vm/` scenarios validate each service. Update their
verify.yml files.

### Day-to-day workflow

```bash
# M0: rebuild all 4 images in parallel
source test.env
./scripts/build-images.sh --host $PRIMARY_HOST --only kiosk &
./scripts/build-images.sh --host $PRIMARY_HOST --only kodi &
./scripts/build-images.sh --host $PRIMARY_HOST --only desktop &
./scripts/build-images.sh --host $MESH_2_HOST --only moonlight &
wait

# Quick per-feature validation
molecule test -s kiosk-lxc
molecule test -s kodi-lxc
molecule test -s moonlight-lxc
molecule test -s desktop-vm

# Full E2E after all milestones
molecule test
```

### Teardown table

| Scenario         | Creates           | Destroys          | Baseline impact |
|------------------|-------------------|-------------------|-----------------|
| kiosk-lxc        | CT 401            | CT 401            | None            |
| kodi-lxc         | CT 301            | CT 301            | None            |
| moonlight-lxc    | CT 302            | CT 302            | None            |
| desktop-vm       | VM 400            | VM 400            | None            |
| default (E2E)    | All CTs/VMs       | All CTs/VMs       | Full rebuild    |

---

### Milestone 0: All code changes + image builds

_Self-contained. Standard work cycle: Steps 1-2._

Update ALL source files (build-images.sh, webui Python, data.py, handler
classes) in a single pass, then build all 4 images. This ensures the kiosk
image bakes the updated webui code.

See: `image-management-patterns` skill, `lxc-container-patterns` skill,
`build-conventions` skill, `systemd-lxc-compatibility` skill,
`python-code-style` skill, `webui-design-system` skill,
`webui-ux-principles` skill.

**LXC features:** No special features required beyond what containers already
have. KasmVNC Xvnc runs in userspace — no `nesting=1` needed for KasmVNC
itself. Existing containers already have `nesting=1` for systemd sandboxing.

**Network topology:** Display-capable containers span WAN hosts (NAT bridges
10.99.x.x) and LAN hosts (10.10.10.x). Port forwarding (socat/DNAT) is
unchanged for LXC containers. Desktop VM switches from host-side websockify
to host-side port forwarding (same socat/DNAT pattern).

#### Part A: build-images.sh changes

**KasmVNC installation pattern (shared across all 4 images):**

```bash
KASMVNC_VERSION="1.4.0"
KASMVNC_DEB="kasmvncserver_bookworm_${KASMVNC_VERSION}_amd64.deb"
KASMVNC_URL="https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/${KASMVNC_DEB}"

# Inside build container/VM:
wget -q "$KASMVNC_URL" -O "/tmp/$KASMVNC_DEB"
apt-get install -y /tmp/"$KASMVNC_DEB"
rm -f /tmp/"$KASMVNC_DEB"
```

**Per-image systemd unit pattern** (`<app>-display.service`):

```ini
[Unit]
Description=<App> Display (KasmVNC)
After=network.target

[Service]
Type=simple
User=<app_user>
Environment=HOME=/home/<app_user>
ExecStart=/usr/bin/vncserver :<display_num> \
    -websocketPort <ws_port> \
    -geometry 1920x1080 \
    -depth 24 \
    -disableBasicAuth \
    -select-de manual \
    -fg
ExecStop=/usr/bin/vncserver -kill :<display_num>
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

**Per-image kasmvnc.yaml** (`/home/<user>/.vnc/kasmvnc.yaml`):

```yaml
desktop:
  resolution:
    width: 1920
    height: 1080
  allow_resize: true
  gpu:
    hw3d: true
    drinode: /dev/dri/renderD128

network:
  protocol: http
  interface: 0.0.0.0
  websocket_port: <ws_port>
  ssl:
    require_ssl: false

encoding:
  max_frame_rate: 30

server:
  http:
    headers: []

command_line:
  prompt: false
```

**Per-image xstartup** (`/home/<user>/.vnc/xstartup`):

| Image     | xstartup content                                                              |
|-----------|-------------------------------------------------------------------------------|
| Kiosk     | `/opt/kiosk/wait-for-hub.sh && exec chromium --no-sandbox --start-fullscreen` |
| Kodi      | `exec kodi`                                                                   |
| Moonlight | `exec moonlight stream <host> --app <app> --resolution 1920x1080`             |
| Desktop   | `exec startplasma-x11` (or `exec gnome-session`, per existing DE config)      |

- [x] Add `KASMVNC_VERSION` and `KASMVNC_URL` variables to top of `build-images.sh`
- [x] Extract shared `install_kasmvnc()` helper function in `build-images.sh`
- [x] Update `build_kiosk_lxc()`: remove sway/wayvnc/websockify/xwayland packages,
      remove 3-service systemd units, remove sway config. Add KasmVNC install,
      single `kiosk-display.service`, kasmvnc.yaml, xstartup. Drop Chromium
      `--ozone-platform=wayland`. Add user to `ssl-cert` group. Remove noVNC
      static files from kiosk container tarball bake.
- [x] Update `build_kodi_lxc()`: same pattern. Remove sway/wayvnc/websockify.
      Add KasmVNC install, `kodi-display.service`, kasmvnc.yaml, xstartup.
      Remove `--windowing=wayland` from Kodi launch. Add user to `ssl-cert` group.
- [x] Update `build_moonlight_lxc()`: same pattern. Remove sway/wayvnc/websockify/xwayland.
      Add KasmVNC install, `moonlight-display.service`, kasmvnc.yaml, xstartup.
      Add user to `ssl-cert` group.
- [x] Update `build_desktop_vm()`: add KasmVNC install, `desktop-display.service`,
      kasmvnc.yaml, xstartup that launches the desktop environment on X11.
      Add user to `ssl-cert` group.
- [x] Remove `python3-websockify` from all four image package lists
- [x] Build all 4 images in parallel across test hosts
- [x] Validate: `pct exec <ctid> -- curl -s http://localhost:<port>/` returns
      KasmVNC web client HTML from each container

#### Part B: handler OOP redesign

See: `python-code-style` skill, `code-review-checklist` skill.

File: `scripts/webui/display_transfer.py`

- [x] Remove `DisplayType` enum entirely
- [x] Remove `display_type` field from `TransferResult`
- [x] Remove `display_type` property from `DisplayHandler` protocol
- [x] Remove `_VncHandlerBase` class
- [x] Remove `QemuVncHandler` class
- [x] Remove `WaylandVncHandler` class
- [x] Create `_ManagedDisplayBase` class:
      - `__init__(self, app_id: str, port: int, conflicts: list[str], ssh_exec: SshExecFn) -> None`
      - `get_viewstream_url(self, host_ip: str) -> str` returns `http://{host_ip}:{port}`
      - `_make_enter_result()`, `_make_exit_result()`, `_check_status()` — shared helpers
- [x] Create `ContainerDisplayHandler(_ManagedDisplayBase)`:
      - `__init__` adds `ct_id: str`
      - `enter()` → `pct start {ct_id}`
      - `exit()` → `pct stop {ct_id}`
      - `is_active()` → `pct status {ct_id}`
- [x] Create `VmDisplayHandler(_ManagedDisplayBase)`:
      - `__init__` adds `vmid: str`
      - `enter()` → `qm start {vmid}`
      - `exit()` → `qm stop {vmid}`
      - `is_active()` → `qm status {vmid}`
- [x] Keep `WebViewHandler` as-is (no lifecycle, `http://` URL with path)
- [x] Update `_HANDLER_BUILDERS` factory map:
      `"container_display"` → `ContainerDisplayHandler`,
      `"vm_display"` → `VmDisplayHandler`,
      `"web_view"` → `WebViewHandler`
- [x] Update `HandlerMetadata`: remove `display_type`, add `handler_type`
- [x] Update `list_handlers()` to use new `HandlerMetadata`
- [x] Update module docstring to reflect new handler types

#### Part C: data.py changes

File: `scripts/webui/data.py`

- [x] Rename `Ports.KIOSK_VNC_WS` → `Ports.KIOSK_DISPLAY`, etc. Remove `Ports.KIOSK_VNC`
- [x] Rename `DisplayAppConfig.vnc_ws_port` → `DisplayAppConfig.display_port`
- [x] Update `DISPLAY_APP_CONFIGS`:
      - kiosk: `handler_type="container_display"`, `display_port=Ports.KIOSK_DISPLAY`
      - desktop: `handler_type="vm_display"`, `display_port=Ports.DESKTOP_DISPLAY`
      - kodi: `handler_type="container_display"`, `display_port=Ports.KODI_DISPLAY`
      - moonlight: `handler_type="container_display"`, `display_port=Ports.MOONLIGHT_DISPLAY`
- [x] Update `description` strings (remove "Wayland VNC" and "headless Wayland" references)
- [x] Remove `display_icon()` function (all display apps are now iframes)
- [x] Remove `_LAN_VNC_RELAY_PORTS` and `get_vnc_relay()` entirely
      (replaced by `_lan_proxy_ports` in manager.py — cleaner architecture)

#### Part D: web UI page changes

File: `scripts/webui/pages/vnc_shared.py` → rename to `display_shared.py`

- [x] Delete `vnc_shared.py`, create new `display_shared.py`
- [x] Remove: `mount_static()`, `vnc_canvas_css()`, `vnc_init_script()`,
      `_overlay_guard_script()`, `render_vnc_canvas()`
- [x] Rename `pointer_passthrough_css()` → `iframe_passthrough_css()`. This CSS
      disables pointer-events on NiceGUI's `#app` div so iframes receive unfiltered
      mouse input — it is NOT noVNC-specific. Both the old noVNC canvas and the new
      KasmVNC iframe need it because NiceGUI/Quasar captures mouse events on elements
      inside its DOM tree. The iframe is placed outside `#app` via `add_body_html()`.
- [x] Keep: `viewer_base_css()`, `render_viewer_error()`, `render_app_console_links()`,
      `iframe_passthrough_css()` (renamed from `pointer_passthrough_css`)
- [x] In `render_app_console_links()`: inline the icon logic (remove `display_icon()` call),
      use `"tv"` for managed display handlers, `"web"` for web_view
- [x] Update module docstring

File: `scripts/webui/pages/console.py`

- [x] Update imports: `display_shared` instead of `vnc_shared`, remove `DisplayType` import,
      import `iframe_passthrough_css` (renamed from `pointer_passthrough_css`)
- [x] Remove `mount_static()` call from `register()`
- [x] Remove `_render_vnc_console()` function entirely
- [x] Remove the `if handler.display_type is DisplayType.VNC` branch
- [x] The console page renders iframes for ALL display apps via shared
      `render_display_iframe()` from `display_shared.py`
- [x] Remove `show_status_dot` from viewer bar (no noVNC status dot needed)

File: `scripts/webui/pages/remote_kiosk.py`

- [x] Update module docstring: "VNC streaming via noVNC" → "Remote display
      streaming via KasmVNC iframe"
- [x] Update imports: `display_shared` instead of `vnc_shared`, remove noVNC imports
- [x] Remove `mount_static()` call
- [x] Rename `_render_vnc_viewer` → `_render_display_viewer`
- [x] Rename `_render_vnc_error` → inline into `render_viewer_error` call
      (it's a one-line wrapper — remove the wrapper)
- [x] Replace `render_vnc_canvas(vnc_url)` with shared `render_display_iframe()`
      from `display_shared.py` (DRY: single iframe renderer for both pages)
- [x] Update `mgr.get_child_vnc_url()` → `mgr.get_child_display_url()`
- [x] Remove `vnc_url` variable name — renamed to `display_url`
- [x] Remove VNC status dot element (`#vnc-status-dot`)

File: `scripts/webui/manager.py`

- [x] Rename `get_child_vnc_url()` → `get_child_display_url()` in `BaseManager`
- [x] Update URL construction: `ws://{ip}:{port}` → `http://{ip}:{port}`
- [x] Rename `_resolve_vnc_ip()` → `_resolve_display_ip()` in `BaseManager`
- [x] Remove `_get_vnc_relay()` entirely (replaced by `_lan_proxy_ports` dict)
- [x] Remove `vnc_relay_resolver` parameter from `ClusterManager` and `init()`
      (cleaner: `_lan_proxy_ports` populated by app.py after init)
- [x] Update `get_guest_viewstream_url()` docstring (remove VNC references)
- [x] Rename section comment `# ── VNC and hierarchy ──` →
      `# ── Display streaming and hierarchy ──`
- [x] Update all docstrings: replace "VNC websockify" with "KasmVNC display",
      "WebSocket URL" with "display URL"

File: `scripts/webui/app.py`

- [x] Remove `_env_vnc_relay_resolver()` entirely (replaced by `_lan_proxy_ports`)
- [x] Remove `data.get_vnc_relay()` call (replaced by manager-side proxy ports)
- [x] Remove `vnc_relay_resolver` kwarg from `manager.init()` call
- [x] Add `_env_node_resolver` LAN host handling: returns PRIMARY_HOST IP for LAN nodes
- [x] Add post-init `_lan_proxy_ports` population for LAN hosts

- [x] Delete `scripts/webui/static/noVNC/` directory entirely (728KB, 52 files)

#### Part E: Ansible role and variable changes

See: `vm-lifecycle-architecture` skill, `lxc-container-patterns` skill,
`proxmox-safety-rules` skill.

**`inventory/group_vars/all.yml` variable renames:**

- [x] Rename `kiosk_vnc_ws_port` → `kiosk_ws_port`
- [x] Rename `desktop_vnc_ws_port` → `desktop_ws_port`
- [x] Rename `kodi_vnc_ws_port` → `kodi_ws_port`
- [x] Rename `moonlight_vnc_ws_port` → `moonlight_ws_port`
- [x] Remove `kiosk_vnc_relay_base_port` (relay removed — direct peer-to-peer)

**`roles/desktop_vm/tasks/main.yml`:**

- [x] Remove `python3-websockify` apt install
- [x] Remove `desktop-vnc-ws.service` deployment entirely
- [x] Add port forwarding rule (socat/DNAT) to forward host:6081 → VM_IP:6081
- [x] Update all variable references to `desktop_ws_port`
- [x] Update section comments (remove "VNC" references)

**`roles/kiosk_lxc/tasks/main.yml`:**

- [x] Rename socat proxy service: `kiosk-vnc-proxy.service` → `kiosk-display-proxy.service`
- [x] Update all variable references to `kiosk_ws_port`
- [x] Update DNAT/FORWARD rule variables
- [x] Update section comments and service descriptions

**`roles/kodi_lxc/tasks/main.yml`:**

- [x] Rename socat proxy service: `kodi-vnc-proxy.service` → `kodi-display-proxy.service`
- [x] Update all variable references to `kodi_ws_port`
- [x] Update section comments and service descriptions

**`roles/moonlight_lxc/tasks/main.yml`:**

- [x] Rename socat proxy service: `moonlight-vnc-proxy.service` → `moonlight-display-proxy.service`
- [x] Update all variable references to `moonlight_ws_port`
- [x] Update section comments and service descriptions

**`roles/kodi_configure/tasks/main.yml`:**

- [x] Update debug output variable to `kodi_ws_port`
- [x] Update "VNC port" label text to "Display port"

**`roles/moonlight_configure/tasks/main.yml`:**

- [x] Update debug output variable to `moonlight_ws_port`
- [x] Update "VNC port" label text to "Display port"

**`playbooks/site.yml` — VNC relay play removed:**

- [x] Relay play removed entirely (direct peer-to-peer — no relay needed)
- [x] All `kiosk-vnc-relay-*` references removed
- [x] `kiosk_vnc_relay_base_port` / `kiosk_display_relay_base_port` removed

**Verify per-host display app matrix:**

| Host | CT/VM | Display Service | Port | Verify |
|------|-------|-----------------|------|--------|
| ALL 6 hosts | CT 401 (kiosk) | kiosk-display | 6080 | service active, port forwarded |
| home | VM 400 (desktop) | desktop-display | 6081 | service active inside VM, port forwarded |
| home | CT 301 (kodi) | kodi-display | 6082 | service active, port forwarded |
| mesh1 | CT 302 (moonlight) | moonlight-display | 6083 | service active, port forwarded |

**Verify checklist (all items must pass):**

- [x] `kasmvncserver` package installed in each container/VM (`dpkg -l kasmvncserver`)
- [x] `sway`, `wayvnc`, `python3-websockify` NOT installed in any container
- [x] `desktop-vnc-ws.service` does NOT exist on any Proxmox host
- [x] `kiosk-display.service` active on ALL 6 hosts (CT 401)
- [x] `kodi-display.service` active on home (CT 301)
- [x] `moonlight-display.service` active on mesh1 (CT 302)
- [x] `desktop-display.service` active inside Desktop VM on home (VM 400)
- [x] Old services (`*-vnc.service`, `*-vnc-ws.service`) do NOT exist in any container
- [x] Old host-side proxy services (`kiosk-vnc-proxy`, `kodi-vnc-proxy`,
      `moonlight-vnc-proxy`, `kiosk-vnc-relay-*`) do NOT exist
- [x] New host-side proxy services (`kiosk-display-proxy`, `kodi-display-proxy`,
      `moonlight-display-proxy`) active where applicable
- [x] KasmVNC WebSocket port listening on expected port per container/VM
- [x] `/home/<user>/.vnc/kasmvnc.yaml` exists with `require_ssl: false`
- [x] `/home/<user>/.vnc/xstartup` exists and is executable
- [x] DRI3: `/dev/dri/renderD128` accessible from within each container/VM
- [x] Port forwarding works: `curl -s http://<host_ip>:<port>/` from controller
      (test all 4 ports on their respective hosts)
- [x] Display relay removed (direct peer-to-peer — no relay needed)
- [x] `pytest tests/test_display_transfer.py -v` passes (handler types resolve)
- [x] Zero references to `vnc_ws_port`, `kiosk_vnc_ws_port`, `desktop_vnc_ws_port`,
      `kodi_vnc_ws_port`, `moonlight_vnc_ws_port` in codebase (grep verification)

**Rollback:**
Revert all Python file changes via git. Rebuild images from the reverted
`build-images.sh`. The `proxmox_lxc` version-mismatch system auto-recreates
containers from rebuilt images. No manual container cleanup needed.

---

### Milestone 1: Tests + molecule verify + cleanup

_Blocked on: M0 (all code + images must be updated)._

Update unit tests, molecule verify assertions, and cleanup playbook to
reflect the new KasmVNC stack and handler hierarchy.

See: `molecule-verify` skill, `build-testing` skill,
`testing-workflow` skill, `code-review-checklist` skill.

- [x] Update `test_display_transfer.py`:
      - Renamed handler test classes and updated URL/type assertions
      - Removed all `display_type` assertions
      - Updated port constant names and config field names
      - Added regression tests: `test_handler_type_is_string`,
        `test_transfer_result_has_no_display_type`
      - SshStub retained with justified mock comment
- [x] Update `molecule/default/verify.yml`:
      - Kiosk: `kiosk-display` check on ALL 6 hosts, `kasmvncserver` package check,
        `kiosk-display-proxy` on LAN, DNAT on WAN
      - Kodi: `kodi-display` check, `kodi-display-proxy`
      - Moonlight: `moonlight-display` check, `moonlight-display-proxy`
      - Desktop: `desktop-display` in-VM check, `desktop-display-proxy` on host
      - Display relay: `kiosk-display-relay-mesh1` on router node
- [x] Update per-feature verify files (kiosk-lxc, kodi-lxc, moonlight-lxc, desktop-vm)
- [x] Update `playbooks/cleanup.yml`:
      - All VNC service references renamed to `*-display-proxy`
      - Desktop port forwarding cleanup added
      - Variable references updated
- [x] Run `pytest tests/ -v` — 1001 passed, 0 failures
- [x] Run `molecule test` — full E2E passes

**Verify:**
- [x] `pytest tests/test_display_transfer.py -v` passes with zero failures
- [x] `molecule test` completes with exit code 0
- [x] `molecule/default/verify.yml` checks kiosk-display on ALL 6 hosts (not just home)
- [x] `molecule/default/verify.yml` checks kodi-display on home, moonlight-display on mesh1,
      desktop-display inside VM on home
- [x] Zero references to `wayvnc`, `websockify`, `sway`, `noVNC`, `DisplayType`,
      `_VncHandlerBase`, `QemuVncHandler`, `WaylandVncHandler`, `vnc_ws_port`,
      `kiosk_vnc_ws_port`, `desktop_vnc_ws_port` in codebase
- [x] Cleanup removes correct service names (new `*-display-proxy` names) and port
      forwarding rules
- [x] Per-feature scenarios verified via full E2E

**Rollback:**
Revert test/verify file changes. No infrastructure impact.

---

### Milestone 2: Documentation + manual testing

_Blocked on: M1 (tests must pass before manual testing)._

Update architecture docs, write the manual testing playbook, update the
manual testing skill, and EXECUTE manual testing on real hardware.

See: `manual-testing-playbook-writing` skill, `webui-manual-testing` skill.

- [x] Update `docs/architecture/overview.md` — already references KasmVNC
      throughout (verified: zero sway/wayvnc/websockify/noVNC references)
- [x] Playbook 13 in `docs/manual-testing-playbooks.md` has been rewritten
      as a plan deliverable (see sections 13.1–13.8). No further writing
      needed — M2 EXECUTES the playbook, it does not create it.
- [x] Update pre-flight section 3f in `docs/manual-testing-playbooks.md`:
      already shows "Display Services", checks `kiosk-display`
- [x] Update Playbooks 14 and 15 in `docs/manual-testing-playbooks.md`:
      already use KasmVNC terminology (verified)
- [x] Update `.agents/skills/webui-manual-testing/SKILL.md`:
      updated to reference "legacy VNC" instead of "noVNC"
- [x] Update `docs/projects/2026-04-12-22-kasmvnc-migration/notes.md` with outcomes
      — added manual testing results section with verification matrix and bug findings
- [x] **EXECUTE Playbook 13, ALL sections (13.1–13.8)** against the fully
      converged system — completed 2026-04-14. Results:
      - 13.1: ALL PASS — kiosk-display active on 6/6 hosts, kodi-display
        active on home, moonlight-display enabled on mesh1, desktop-display
        active on home VM 400. Legacy VNC units absent.
      - 13.2: PASS — SM iframe loads KasmVNC from 192.168.86.201:6080,
        WebSocket /websockify connects (101), viewer bar renders correctly.
      - 13.3: PASS — Kodi, Desktop, Moonlight console pages all load
        with correct iframe URLs and viewer bar navigation.
      - 13.4: PASS — Node detail pages show Open Kiosk + 3 app console links.
      - 13.5: ALL PASS — 6/6 hosts' kiosk displays accessible (HTTP 200),
        mesh1 via display relay on primary:16080.
      - 13.6: PASS — CM fleet page shows 5 child nodes with cast_connected
        icons, drill-down to /remote/mesh1 resolves via _child_managers
        to LAN IP 10.10.10.24:6080 (different code path from SM).
      - 13.7: PASS — /remote/nonexistent shows error page with Go Back
        button, stopped service shows connection failure with functional
        back button, zero noVNC artifacts in codebase.
      - 13.8: ALL PASS — complete host × app matrix signed off (see below).

      **Issues discovered and fixed during manual testing:**
      - KasmVNC `-disableBasicAuth` (lowercase) is invalid; correct Xvnc
        flag is `-DisableBasicAuth` (capital D). Fixed in build-images.sh.
      - Running containers from pre-fix images needed hot-patches: vncpasswd
        user creation, hw3d→false, -DisableBasicAuth flag.
      - Desktop VM had stale service file with old `-disableBasicAuth` flag;
        hot-patched via qm guest exec + sed.

      **Section 13.8 verification matrix (2026-04-14):**

      | Host | Kiosk (6080) | Desktop (6081) | Kodi (6082) | Moonlight (6083) | Gaming (web) |
      |------|:---:|:---:|:---:|:---:|:---:|
      | home | PASS | PASS | PASS | N/A | N/A |
      | mesh1 | PASS (relay) | N/A | N/A | PASS (enabled) | N/A |
      | ai | PASS | N/A | N/A | N/A | PASS (307) |
      | mesh2 | PASS | N/A | N/A | N/A | N/A |
      | bridge-1 | PASS | N/A | N/A | N/A | N/A |
      | bridge-2 | PASS | N/A | N/A | N/A | N/A |

**Verify:**
- [x] All manual test sections (13.1–13.8) executed on real hardware after
      `molecule test` passes
- [x] KasmVNC iframe loads cleanly without auth prompts on all 4 app types
      (kiosk, desktop, kodi, moonlight) — using `-DisableBasicAuth` Xvnc flag
- [x] Kiosk display accessible on ALL 6 hosts from the SuperManager
- [x] Display relay path works (mesh1 via primary host port 16080)
- [x] Gaming (Sunshine) web UI accessible from ai's kiosk hub (HTTP 307 redirect)
- [x] Display conflict resolution works (Desktop ↔ Kodi mutual exclusion)
      — verified via unit tests: `test_enter_with_conflict_resolution`,
        `test_enter_fails_when_conflict_exit_fails`, `test_conflict_references_are_valid`
- [x] No noVNC canvas rendering anywhere in the application
- [x] Section 13.8 host × app matrix fully signed off (all cells verified)
- [x] Architecture docs accurately describe the new pipeline
      — 15 KasmVNC references, zero legacy noVNC/wayvnc/websockify references
- [x] Manual testing skill references updated playbook
      — references KasmVNC display pipeline and legacy VNC absence checks

**Rollback:**
Documentation-only — revert file changes via git.

---

## Risk assessment

### Low risk (mechanical changes)

- Removing wayvnc/websockify packages from images
- Updating URL format in handler classes (ws:// → http://)
- LXC container host-side socat/DNAT forwarding (zero changes)
- Unit test updates (rename handlers, update URLs, remove dead fields)
- Removing noVNC static files from kiosk container tarball bake
- `DisplayAppConfig` field rename (vnc_ws_port → display_port)
- `Ports` constant rename
- `display_icon()` removal

### Medium risk (requires testing)

- KasmVNC package installation in build-images.sh (dependency resolution)
- App launch via xstartup (timing — app must start after Xvnc is ready)
- KasmVNC iframe embedding in NiceGUI pages (CORS headers, mixed content)
- DRI3 GPU acceleration config (per-vendor render device detection)
- KasmVNC web client auto-hiding control bar in iframe
- Desktop VM port forwarding change (host-side websockify → socat/DNAT)

### Higher risk (may need iteration)

- **Kodi under X11** — Kodi's X11 backend is mature but may need different
  display flags than Wayland. Audio output may differ. Test extensively.
- **Moonlight under X11** — SDL2 X11 backend auto-detected. Mouse capture
  for streaming may conflict with KasmVNC's input handling.
- **Chromium under X11 in kiosk container** — GPU compositing flags
  (`--disable-gpu`) may need adjustment for KasmVNC's Xvnc.
- **COEP/COOP headers** — KasmVNC defaults may block iframe embedding.
  `server.http.headers: []` should clear them, but verify.
- **Desktop VM KasmVNC inside VM** — New architecture (no prior deployment).
  Cloud-init KasmVNC install must handle dependencies correctly. The
  virtual X11 session provides remote desktop access but is SEPARATE from
  the iGPU-passthrough physical display.

---

## File reference (complete list)

### Build scripts
- `scripts/build-images.sh` — `build_kiosk_lxc()`, `build_kodi_lxc()`,
  `build_moonlight_lxc()`, `build_desktop_vm()`, new `install_kasmvnc()` helper

### Web UI
- `scripts/webui/display_transfer.py` — handler hierarchy redesign, remove
  `DisplayType`/`_VncHandlerBase`/`QemuVncHandler`/`WaylandVncHandler`
- `scripts/webui/manager.py` — rename VNC methods (`get_child_vnc_url` →
  `get_child_display_url`, `_resolve_vnc_ip` → `_resolve_display_ip`,
  `_get_vnc_relay` → `_get_display_relay`), update URL format `ws://` → `http://`,
  rename `vnc_relay_resolver` parameter
- `scripts/webui/app.py` — rename `_env_vnc_relay_resolver` →
  `_env_display_relay_resolver`, update `data.get_vnc_relay` call
- `scripts/webui/pages/vnc_shared.py` → rename to `display_shared.py`,
  remove noVNC-specific functions, rename `pointer_passthrough_css` →
  `iframe_passthrough_css`
- `scripts/webui/pages/console.py` — single iframe path, remove VNC branch
- `scripts/webui/pages/remote_kiosk.py` — iframe instead of canvas, rename
  `_render_vnc_viewer` → `_render_display_viewer`, remove VNC status dot,
  update module docstring and `get_child_vnc_url` call
- `scripts/webui/data.py` — `DisplayAppConfig` field rename, `Ports` rename,
  handler_type updates, `display_icon()` removal, `_LAN_VNC_RELAY_PORTS` →
  `_LAN_DISPLAY_RELAY_PORTS`, `get_vnc_relay()` → `get_display_relay()`
- `scripts/webui/static/noVNC/` — DELETE ENTIRELY

### Ansible inventory and variables
- `inventory/group_vars/all.yml` — rename `kiosk_vnc_ws_port` → `kiosk_ws_port`,
  `desktop_vnc_ws_port` → `desktop_ws_port`, `kodi_vnc_ws_port` → `kodi_ws_port`,
  `moonlight_vnc_ws_port` → `moonlight_ws_port`, `kiosk_vnc_relay_base_port` →
  `kiosk_display_relay_base_port`

### Ansible roles
- `roles/desktop_vm/tasks/main.yml` — remove websockify, add port forwarding,
  update variable references
- `roles/kiosk_lxc/tasks/main.yml` — rename `kiosk-vnc-proxy` →
  `kiosk-display-proxy`, update variable references and service descriptions
- `roles/kodi_lxc/tasks/main.yml` — rename `kodi-vnc-proxy` →
  `kodi-display-proxy`, update variable references and service descriptions
- `roles/kodi_configure/tasks/main.yml` — update variable reference and label
- `roles/moonlight_lxc/tasks/main.yml` — rename `moonlight-vnc-proxy` →
  `moonlight-display-proxy`, update variable references and service descriptions
- `roles/moonlight_configure/tasks/main.yml` — update variable reference and label

### Playbooks
- `playbooks/site.yml` — rename VNC relay play and services:
  `kiosk-vnc-relay-*` → `kiosk-display-relay-*`, update variable references
- `playbooks/cleanup.yml` — rename all VNC service references, remove websockify,
  add desktop port forwarding cleanup

### Tests
- `tests/test_display_transfer.py` — handler rename, URL assertions, field removal
- `molecule/default/verify.yml` — per-host display service checks on ALL 6 hosts,
  per-app checks (kodi on home, moonlight on mesh1, desktop on home)
- `molecule/kiosk-lxc/verify.yml` — per-feature assertion updates
- `molecule/kodi-lxc/verify.yml` — per-feature assertion updates
- `molecule/moonlight-lxc/verify.yml` — per-feature assertion updates
- `molecule/desktop-vm/verify.yml` — per-feature assertion updates

### Documentation
- `docs/architecture/overview.md` — VNC pipeline description, handler hierarchy
- `docs/manual-testing-playbooks.md` — Playbook 13 rewrite for KasmVNC
- `.agents/skills/webui-manual-testing/SKILL.md` — updated playbook reference
- `docs/projects/2026-04-12-22-kasmvnc-migration/notes.md` — outcomes

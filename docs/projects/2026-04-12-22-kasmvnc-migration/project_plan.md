# KasmVNC Migration — Project Plan

Replace the entire `sway → wayvnc → websockify → noVNC` pipeline with
KasmVNC across ALL display apps — 3 LXC containers (kiosk, kodi, moonlight)
AND the Desktop VM. This eliminates 3 processes per container, removes 728KB
of noVNC JavaScript, the `DisplayType` enum, the `_VncHandlerBase` class,
the host-side websockify service for the Desktop VM, and all noVNC pointer
passthrough hacks. Every display app uses a single KasmVNC process embedded
as an iframe.

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
    KIOSK_WS = 6080
    DESKTOP_WS = 6081
    KODI_WS = 6082
    MOONLIGHT_WS = 6083
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

- [ ] Add `KASMVNC_VERSION` and `KASMVNC_URL` variables to top of `build-images.sh`
- [ ] Extract shared `install_kasmvnc()` helper function in `build-images.sh`
- [ ] Update `build_kiosk_lxc()`: remove sway/wayvnc/websockify/xwayland packages,
      remove 3-service systemd units, remove sway config. Add KasmVNC install,
      single `kiosk-display.service`, kasmvnc.yaml, xstartup. Drop Chromium
      `--ozone-platform=wayland`. Add user to `ssl-cert` group. Remove noVNC
      static files from kiosk container tarball bake.
- [ ] Update `build_kodi_lxc()`: same pattern. Remove sway/wayvnc/websockify.
      Add KasmVNC install, `kodi-display.service`, kasmvnc.yaml, xstartup.
      Remove `--windowing=wayland` from Kodi launch. Add user to `ssl-cert` group.
- [ ] Update `build_moonlight_lxc()`: same pattern. Remove sway/wayvnc/websockify/xwayland.
      Add KasmVNC install, `moonlight-display.service`, kasmvnc.yaml, xstartup.
      Add user to `ssl-cert` group.
- [ ] Update `build_desktop_vm()`: add KasmVNC install, `desktop-display.service`,
      kasmvnc.yaml, xstartup that launches the desktop environment on X11.
      Add user to `ssl-cert` group.
- [ ] Remove `python3-websockify` from all four image package lists
- [ ] Build all 4 images in parallel across test hosts
- [ ] Validate: `pct exec <ctid> -- curl -s http://localhost:<port>/` returns
      KasmVNC web client HTML from each container

#### Part B: handler OOP redesign

See: `python-code-style` skill, `code-review-checklist` skill.

File: `scripts/webui/display_transfer.py`

- [ ] Remove `DisplayType` enum entirely
- [ ] Remove `display_type` field from `TransferResult`
- [ ] Remove `display_type` property from `DisplayHandler` protocol
- [ ] Remove `_VncHandlerBase` class
- [ ] Remove `QemuVncHandler` class
- [ ] Remove `WaylandVncHandler` class
- [ ] Create `_ManagedDisplayBase` class:
      - `__init__(self, app_id: str, port: int, conflicts: list[str], ssh_exec: SshExecFn) -> None`
      - `get_viewstream_url(self, host_ip: str) -> str` returns `http://{host_ip}:{port}`
      - `_make_enter_result()`, `_make_exit_result()`, `_check_status()` — shared helpers
- [ ] Create `ContainerDisplayHandler(_ManagedDisplayBase)`:
      - `__init__` adds `ct_id: str`
      - `enter()` → `pct start {ct_id}`
      - `exit()` → `pct stop {ct_id}`
      - `is_active()` → `pct status {ct_id}`
- [ ] Create `VmDisplayHandler(_ManagedDisplayBase)`:
      - `__init__` adds `vmid: str`
      - `enter()` → `qm start {vmid}`
      - `exit()` → `qm stop {vmid}`
      - `is_active()` → `qm status {vmid}`
- [ ] Keep `WebViewHandler` as-is (no lifecycle, `http://` URL with path)
- [ ] Update `_HANDLER_BUILDERS` factory map:
      `"container_display"` → `ContainerDisplayHandler`,
      `"vm_display"` → `VmDisplayHandler`,
      `"web_view"` → `WebViewHandler`
- [ ] Update `HandlerMetadata`: remove `display_type`, add `handler_type`
- [ ] Update `list_handlers()` to use new `HandlerMetadata`
- [ ] Update module docstring to reflect new handler types

#### Part C: data.py changes

File: `scripts/webui/data.py`

- [ ] Rename `Ports.KIOSK_VNC_WS` → `Ports.KIOSK_WS`, etc. Remove `Ports.KIOSK_VNC`
- [ ] Rename `DisplayAppConfig.vnc_ws_port` → `DisplayAppConfig.ws_port`
- [ ] Update `DISPLAY_APP_CONFIGS`:
      - kiosk: `handler_type="container_display"`, `ws_port=Ports.KIOSK_WS`
      - desktop: `handler_type="vm_display"`, `ws_port=Ports.DESKTOP_WS`
      - kodi: `handler_type="container_display"`, `ws_port=Ports.KODI_WS`
      - moonlight: `handler_type="container_display"`, `ws_port=Ports.MOONLIGHT_WS`
- [ ] Update `description` strings (remove "Wayland VNC" and "headless Wayland" references)
- [ ] Remove `display_icon()` function (all display apps are now iframes)

#### Part D: web UI page changes

File: `scripts/webui/pages/vnc_shared.py` → rename to `display_shared.py`

- [ ] Rename file `vnc_shared.py` → `display_shared.py`
- [ ] Remove: `mount_static()`, `vnc_canvas_css()`, `vnc_init_script()`,
      `_overlay_guard_script()`, `render_vnc_canvas()`
- [ ] Rename `pointer_passthrough_css()` → `iframe_passthrough_css()`. This CSS
      disables pointer-events on NiceGUI's `#app` div so iframes receive unfiltered
      mouse input — it is NOT noVNC-specific. Both the old noVNC canvas and the new
      KasmVNC iframe need it because NiceGUI/Quasar captures mouse events on elements
      inside its DOM tree. The iframe is placed outside `#app` via `add_body_html()`.
- [ ] Keep: `viewer_base_css()`, `render_viewer_error()`, `render_app_console_links()`,
      `iframe_passthrough_css()` (renamed from `pointer_passthrough_css`)
- [ ] In `render_app_console_links()`: inline the icon logic (remove `display_icon()` call),
      use `"tv"` for managed display handlers, `"web"` for web_view
- [ ] Update module docstring

File: `scripts/webui/pages/console.py`

- [ ] Update imports: `display_shared` instead of `vnc_shared`, remove `DisplayType` import,
      import `iframe_passthrough_css` (renamed from `pointer_passthrough_css`)
- [ ] Remove `mount_static()` call from `register()`
- [ ] Remove `_render_vnc_console()` function entirely
- [ ] Remove the `if handler.display_type is DisplayType.VNC` branch
- [ ] The console page calls `_render_web_console()` for ALL display apps
      (already exists, renders an iframe — DRY: reuse existing code).
      `_render_web_console()` already calls `iframe_passthrough_css()` (renamed)
- [ ] Remove `show_status_dot` from viewer bar (no noVNC status dot needed)

File: `scripts/webui/pages/remote_kiosk.py`

- [ ] Update imports: `display_shared` instead of `vnc_shared`, remove noVNC imports
- [ ] Remove `mount_static()` call
- [ ] Replace `render_vnc_canvas(vnc_url)` with the iframe rendering pattern
      from `_render_web_console()` in `console.py` (DRY: extract shared iframe
      renderer from console.py if not already shared)

File: `scripts/webui/manager.py`

- [ ] Rename `get_child_vnc_url()` → `get_child_display_url()` in `BaseManager`
- [ ] Update URL construction: `ws://{ip}:{port}` → `http://{ip}:{port}`
- [ ] Rename `_resolve_vnc_ip()` → `_resolve_display_ip()` in `BaseManager`
- [ ] Rename `_get_vnc_relay()` → `_get_display_relay()` in `BaseManager`
- [ ] Update `ClusterManager`: rename `vnc_relay_resolver` parameter →
      `display_relay_resolver`, update `_get_display_relay()` override
- [ ] Update `create_manager()` factory: rename `vnc_relay_resolver` kwarg
- [ ] Update `get_guest_viewstream_url()` docstring (remove VNC references)
- [ ] Rename section comment `# ── VNC and hierarchy ──` →
      `# ── Display streaming and hierarchy ──`
- [ ] Update all docstrings: replace "VNC websockify" with "KasmVNC display",
      "WebSocket URL" with "display URL"
- [ ] Update `remote_kiosk.py`: `mgr.get_child_vnc_url()` → `mgr.get_child_display_url()`

- [ ] Delete `scripts/webui/static/noVNC/` directory entirely (728KB, 52 files)

#### Part E: Ansible role changes

See: `vm-lifecycle-architecture` skill, `lxc-container-patterns` skill,
`proxmox-safety-rules` skill.

- [ ] `roles/desktop_vm/tasks/main.yml`: remove `desktop-vnc-ws.service`
      (host-side websockify) deployment. Add port forwarding rule
      (socat/DNAT) to forward host:6081 → VM_IP:6081, same pattern as
      LXC container port forwarding in `kiosk_lxc`/`kodi_lxc`/`moonlight_lxc`.
- [ ] `roles/kiosk_lxc/tasks/main.yml`: update service name references in comments
- [ ] `roles/kodi_lxc/tasks/main.yml`: update service name references in comments
- [ ] `roles/moonlight_lxc/tasks/main.yml`: update service name references in comments

**Verify:**
- [ ] `kasmvncserver` package installed in each container/VM (`dpkg -l kasmvncserver`)
- [ ] `sway`, `wayvnc`, `python3-websockify` NOT installed in any container
- [ ] `desktop-vnc-ws.service` does NOT exist on the Proxmox host
- [ ] `kiosk-display.service` / `kodi-display.service` / `moonlight-display.service` /
      `desktop-display.service` enabled and active
- [ ] Old services (`*-vnc.service`, `*-vnc-ws.service`) do NOT exist
- [ ] KasmVNC WebSocket port listening on expected port per container/VM
- [ ] `/home/<user>/.vnc/kasmvnc.yaml` exists with `require_ssl: false`
- [ ] `/home/<user>/.vnc/xstartup` exists and is executable
- [ ] DRI3: `/dev/dri/renderD128` accessible from within each container/VM
- [ ] Port forwarding works: `curl -s http://<host_ip>:<port>/` from controller
- [ ] `pytest tests/test_display_transfer.py -v` passes (handler types resolve)

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

- [ ] Update `test_display_transfer.py`:
      - Rename `TestQemuVncHandler` → `TestVmDisplayHandler`
      - Rename `TestWaylandVncHandler` → `TestContainerDisplayHandler`
      - Update all URL assertions: `ws://` → `http://`
      - Update handler type mapping: `"qemu_vnc"` → `"vm_display"`,
        `"wayland_vnc"` → `"container_display"`
      - Remove `display_type` assertions from `TestTransferResult`
      - Remove `display_type` assertions from handler property tests
      - Update `Ports.KIOSK_VNC_WS` → `Ports.KIOSK_WS` etc.
      - Update `cfg.vnc_ws_port` → `cfg.ws_port`
      - Update `_make_service()` to use new handler classes
      - Update `test_list_handlers_metadata` to check `handler_type` instead
        of `display_type`
      - Rename `test_vnc_ports_unique` → `test_ws_ports_unique`, update to
        read `cfg.ws_port` instead of `cfg.vnc_ws_port`
      - Keep `SshStub` pattern. Justification comment (per `testing-workflow` skill):
        WHY: `_ssh_exec` runs `pct start/stop` and `qm start/stop` on remote
        Proxmox hosts — irreversible infrastructure side effects that modify
        container/VM state. HOW: the stub records call arguments and returns
        configurable responses, letting tests verify handler logic (conflict
        resolution, already-running detection, error propagation) without
        touching hardware
- [ ] Update `molecule/default/verify.yml`:
      - Kiosk: replace `kiosk-vnc` + `kiosk-vnc-ws` checks with `kiosk-display`
        check. Replace `wayvnc`/`websockify` package checks with `kasmvncserver`.
        Keep DNAT/socat proxy checks.
      - Kodi: replace `kodi-vnc` + `kodi-vnc-ws` with `kodi-display`.
        Replace package checks. Keep `kodi-vnc-proxy` (socat) check.
      - Moonlight: replace `moonlight-vnc` + `moonlight-vnc-ws` with
        `moonlight-display`. Replace package checks. Keep proxy check.
      - Desktop: replace `desktop-vnc-ws` (host-side websockify) with port
        forwarding check and `desktop-display` (in-VM service) check.
      - Add KasmVNC checks: config file, WebSocket port, DRI3 render device
- [ ] Update per-feature verify files (`molecule/kiosk-lxc/verify.yml`, etc.)
- [ ] Update `playbooks/cleanup.yml`:
      - Remove `desktop-vnc-ws.service` cleanup from host-side cleanup
      - Add desktop port forwarding rule cleanup (socat/iptables DNAT)
      - Update comments referencing old service names
- [ ] Run `pytest tests/ -v` — all tests pass
- [ ] Run `molecule test` — full E2E passes

**Verify:**
- [ ] `pytest tests/test_display_transfer.py -v` passes with zero failures
- [ ] `molecule test` completes with exit code 0
- [ ] Zero references to `wayvnc`, `websockify`, `sway`, `noVNC`, `DisplayType`,
      `_VncHandlerBase`, `QemuVncHandler`, `WaylandVncHandler` in assertions
- [ ] Cleanup removes correct service names and port forwarding rules

**Rollback:**
Revert test/verify file changes. No infrastructure impact.

---

### Milestone 2: Documentation + manual testing

_Blocked on: M1 (tests must pass before manual testing)._

Update architecture docs, write the manual testing playbook, update the
manual testing skill, and EXECUTE manual testing on real hardware.

See: `manual-testing-playbook-writing` skill, `webui-manual-testing` skill.

- [ ] Update `docs/architecture/overview.md`:
      - Replace VNC pipeline description (3-service → 1-service for LXC,
        QEMU VNC + host websockify → in-VM KasmVNC for Desktop)
      - Update package list for display-capable containers/VMs
      - Update service chain diagram
      - Update handler hierarchy diagram
- [ ] Playbook 13 in `docs/manual-testing-playbooks.md` has been rewritten
      as a plan deliverable (see sections 13.1–13.7). No further writing
      needed — M2 EXECUTES the playbook, it does not create it.
- [ ] Update `.agents/skills/webui-manual-testing/SKILL.md`:
      - Update Playbook 13 description to reference KasmVNC
      - Remove "wayvnc + websockify + noVNC" references
- [ ] Update `docs/projects/2026-04-12-22-kasmvnc-migration/notes.md` with outcomes
- [ ] **EXECUTE Playbook 13, ALL sections (13.1–13.7)** against the fully
      converged system. Run every command. Click every button. Verify every
      expected outcome. No section may be skipped. Specifically:
      - Console page for kiosk/kodi/moonlight/desktop: iframe loads, interaction works
      - Remote kiosk page: iframe loads, child node selection works
      - Hub page: display app links navigate correctly
      - VNC relay: LAN kiosk accessible via relay on router node
      - Desktop VM: KasmVNC shows usable virtual desktop (not VGA BIOS stub)

**Verify:**
- [ ] All manual test sections executed on real hardware after `molecule test` passes
- [ ] KasmVNC iframe loads cleanly without auth prompts on all 4 app types
- [ ] No noVNC canvas rendering anywhere in the application
- [ ] Architecture docs accurately describe the new pipeline
- [ ] Manual testing skill references updated playbook

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
- `DisplayAppConfig` field rename (vnc_ws_port → ws_port)
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
- `scripts/webui/pages/vnc_shared.py` → rename to `display_shared.py`,
  remove noVNC-specific functions, rename `pointer_passthrough_css` →
  `iframe_passthrough_css`
- `scripts/webui/pages/console.py` — single iframe path, remove VNC branch
- `scripts/webui/pages/remote_kiosk.py` — iframe instead of canvas
- `scripts/webui/data.py` — `DisplayAppConfig` field rename, `Ports` rename,
  handler_type updates, `display_icon()` removal
- `scripts/webui/static/noVNC/` — DELETE ENTIRELY

### Ansible roles
- `roles/desktop_vm/tasks/main.yml` — remove websockify, add port forwarding
- `roles/kiosk_lxc/tasks/main.yml` — comment updates only
- `roles/kodi_lxc/tasks/main.yml` — comment updates only
- `roles/moonlight_lxc/tasks/main.yml` — comment updates only

### Tests
- `tests/test_display_transfer.py` — handler rename, URL assertions, field removal
- `molecule/default/verify.yml` — package/service assertion updates
- `molecule/kiosk-lxc/verify.yml` — per-feature assertion updates
- `molecule/kodi-lxc/verify.yml` — per-feature assertion updates
- `molecule/moonlight-lxc/verify.yml` — per-feature assertion updates
- `molecule/desktop-vm/verify.yml` — per-feature assertion updates

### Cleanup and playbooks
- `playbooks/cleanup.yml` — service name updates, remove websockify, add port forwarding cleanup
- `playbooks/site.yml` — no structural changes

### Documentation
- `docs/architecture/overview.md` — VNC pipeline description, handler hierarchy
- `docs/manual-testing-playbooks.md` — Playbook 13 rewrite for KasmVNC
- `.agents/skills/webui-manual-testing/SKILL.md` — updated playbook reference
- `docs/projects/2026-04-12-22-kasmvnc-migration/notes.md` — outcomes

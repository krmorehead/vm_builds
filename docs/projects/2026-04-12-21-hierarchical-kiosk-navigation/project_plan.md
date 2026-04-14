# Project 21: Hierarchical Kiosk Navigation via VNC Streaming

**Created:** 2026-04-12
**Completed:** 2026-04-12
**Status:** Complete
**Sequence:** 21

## Summary

Enable full remote control of any kiosk in the fleet through VNC streaming.
Each kiosk's Cage compositor display is captured by wayvnc and made available
to parent tiers via noVNC (browser-native VNC client). The SuperManager can
view and fully control any ClusterManager's kiosk, and the ClusterManager can
in turn view and control any child NodeManager's kiosk — including launching
fullscreen apps (Jellyfin, Desktop, Kodi) through the chain.

This is **not** an iframe or page-mirroring solution. The physical kiosk
unit renders its display via Cage + Chromium, wayvnc captures those actual
pixels, and noVNC renders them with full mouse/keyboard input in the parent
tier's browser. Two-level drill-down uses direct VNC — the operator's
browser always connects to the target node directly, with hierarchical
back-navigation creating the illusion of traversing the cluster chain.

## Motivation

The operator needs true remote control of kiosk displays, not just web page
previews. When a kiosk shows Jellyfin in fullscreen, or the Desktop VM app,
or any screen-takeover application, the operator must see and interact with
exactly what the physical display shows. This is only possible with display
streaming — no web-page embedding can capture applications outside the
NiceGUI process.

## Architecture Decisions

### VNC streaming stack (community standard)

Three components, all available as Debian 12 packages:

```
┌─────────────────────────────────────────────────────────┐
│  Browser (operator / parent kiosk)                       │
│  noVNC RFB client (JavaScript, ES module)                │
│  Connects via WebSocket to target websockify             │
└──────────────┬──────────────────────────────────────────┘
               │ WebSocket (port 6080)
┌──────────────▼──────────────────────────────────────────┐
│  websockify (in kiosk LXC container)                     │
│  Bridges WebSocket ↔ TCP VNC                             │
│  Listens: 0.0.0.0:6080 → localhost:5900                  │
└──────────────┬──────────────────────────────────────────┘
               │ TCP (port 5900)
┌──────────────▼──────────────────────────────────────────┐
│  wayvnc (in kiosk LXC container)                         │
│  Attaches to Cage Wayland compositor                     │
│  Captures framebuffer, serves VNC on 0.0.0.0:5900        │
│  Creates virtual input devices for mouse/keyboard        │
└──────────────┬──────────────────────────────────────────┘
               │ Wayland protocol (wayland-0 socket)
┌──────────────▼──────────────────────────────────────────┐
│  Cage compositor + Chromium (kiosk display)               │
│  Renders NiceGUI hub, Jellyfin, Desktop, any app         │
│  Physical display output + VNC capture simultaneously    │
└─────────────────────────────────────────────────────────┘
```

- **wayvnc** (Debian: `wayvnc` 0.5.0): Wayland-native VNC server designed
  for wlroots compositors. Cage is wlroots-based. Attaches to the compositor
  session, creates virtual input devices, captures framebuffer. Runs headless
  or with a physical display.
- **websockify** (Debian: `python3-websockify`): WebSocket-to-TCP bridge from
  the noVNC project. Bridges browser WebSocket to wayvnc's TCP VNC. Standard
  community solution — browsers cannot speak raw TCP.
- **noVNC** (JavaScript): Browser-based VNC client. The `RFB` class connects
  via WebSocket and renders to a canvas element. Embedded directly in the
  NiceGUI page via `ui.html()` — no iframe.

### Two-level drill-down (direct VNC with hierarchical navigation)

The operator's browser ALWAYS connects directly to the target kiosk — never
nested VNC. Navigation controls in the parent page's HTML chrome create the
illusion of traversing the hierarchy. The `back` query parameter encodes the
return path so exiting a child node returns to the parent's VNC view.

```
Step 1: Operator opens /remote/home?back=/nodes/home
        Browser ──── direct VNC ──── home kiosk (CM)
        Top bar shows: [← Back] [home] [Drill into: mesh1 ▾] [●Connected]

Step 2: Operator selects "mesh1" from child picker
        Browser navigates to /remote/mesh1?back=/remote/home?back=/nodes/home
        Browser ──── direct VNC ──── mesh1 kiosk (NM)
        Top bar shows: [← Back to home] [mesh1] [●Connected]
        (No child picker — mesh1 is a leaf node)

Step 3: Operator clicks "Back to home"
        Browser navigates to /remote/home?back=/nodes/home
        Browser ──── direct VNC ──── home kiosk (CM)
        (Back to step 1 — reconnects directly to home)

Step 4: Operator clicks "← Back"
        Browser navigates to /nodes/home
        (Returns to SuperManager node detail page — no VNC)
```

Key properties:
- **Single VNC layer at all times.** No double encoding, no added latency,
  no input capture issues. The operator gets the same quality at every level.
- **Navigation in parent HTML, not inside VNC.** The child node picker is a
  dropdown in the top bar, rendered by the parent page's NiceGUI. Clicks on
  it are handled by standard browser navigation — not sent through VNC.
- **Back URL chain.** The `back` query parameter is a URL that can itself
  contain a `back` parameter, encoding the full return path through the
  hierarchy. URL encoding handles escaping naturally.
- **Direct access to any node.** The SuperManager can open `/remote/{any_id}`
  for any node, regardless of tier. The hierarchy is a navigation convenience,
  not a routing constraint.

### Child node picker (hierarchical drill-down control)

When viewing a kiosk that has children (e.g., a ClusterManager), the remote
kiosk page shows a dropdown in the top bar listing the child nodes. Selecting
a child navigates to `/remote/{child_id}?back=/remote/{current_id}?back=...`.

The child list is provided by `BaseManager.get_fleet_children(node_id)`:
- **SuperManager**: returns child node IDs for a given CM, derived from fleet
  topology (which nodes relay heartbeats through which CM)
- **ClusterManager**: returns `_child_managers.keys()` (its own children)
- **NodeManager**: always returns empty list (leaf nodes)

When the list is empty (viewing a leaf NM), the dropdown is hidden. The top
bar only shows back button, node name, and connection status.

This approach is KISS: no special "drill-down mode," no session state, no
VNC nesting. Just URL navigation with a child picker dropdown.

### Port allocation and forwarding

Two new ports per kiosk container, centralized as Ansible variables in
`group_vars/all.yml` (`kiosk_vnc_port: 5900`, `kiosk_vnc_ws_port: 6080`)
and as Python constants in `data.py` (`Ports.KIOSK_VNC = 5900`,
`Ports.KIOSK_VNC_WS = 6080`):

| Port | Service    | Protocol  | Purpose                              |
|------|------------|-----------|--------------------------------------|
| 5900 | wayvnc     | TCP (RFB) | VNC server (container-local only)    |
| 6080 | websockify | WebSocket | Browser-accessible VNC bridge        |

Port 6080 must be reachable from the parent tier's browser. The forwarding
follows the **exact same pattern** as port 9001 (kiosk API):

- **WAN hosts**: iptables DNAT on `proxmox_wan_bridge`:6080 → container:6080
- **Router node**: socat systemd unit (WAN:6080 → kiosk LAN IP:6080) —
  same hairpin-NAT workaround as port 9001
- **LAN hosts**: direct access, no forwarding needed

### Network topology

VNC streaming operates on the same topology as the kiosk API (port 9001).
`kiosk_nodes` equals `proxmox` membership — every host gets a kiosk
container. No new container IP offset is needed (CT 401 uses its existing
kiosk offset). The only change is exposing port 6080 alongside 9001.

- **LAN hosts** (router_nodes, lan_hosts): kiosk containers use the OpenWrt
  LAN bridge. VNC WebSocket directly reachable on the LAN.
- **WAN hosts** (all others): kiosk containers use the NAT bridge
  (10.99.x.x). DNAT forwards host:6080 → container:6080.
- **Router node** (home): socat proxy (hairpin NAT workaround).

### noVNC JavaScript distribution

noVNC's essential JS files (~200KB) are **committed to git** at
`scripts/webui/static/noVNC/` (pinned release version). Both `app.py` and
`kiosk_server.py` serve them via `app.add_static_files()`. This ensures:

- **Single source of truth**: one copy, used by all tiers
- **Offline operation**: no CDN dependency (fleet is on private LAN)
- **Same-origin loading**: no CORS issues (JS loaded from same NiceGUI app)
- **Automatic kiosk deployment**: `scripts/webui/` is baked into kiosk image
- **Reproducibility**: committed files, no download-or-fail in CI/setup

The files are small (~200KB) and version-pinned. This is the same approach
used by projects that vendor JavaScript dependencies. No `setup.sh` download
step — the files are always available from git.

### OOP: manager methods for VNC and hierarchy

Two methods on `BaseManager` support the direct-VNC + hierarchical-navigation
pattern:

**`get_child_vnc_url(node_id: str) -> str | None`** — resolves the WebSocket
URL for a node's websockify. Uses `Ports.KIOSK_VNC_WS` from `data.py` —
never hardcodes port numbers. Returns `ws://{ip}:{port}` or `None`.

Tier-specific resolution (polymorphism, NOT a fallback chain — each tier
uses exactly one data source):
- **SuperManager**: resolves IP from `_fleet_nodes` (populated by heartbeat
  check-ins — includes ALL nodes in the fleet with their routable IPs).
  This is the same data source the SM uses for fleet display and health.
- **ClusterManager**: resolves IP from `_child_managers` (populated from
  `CHILD_MANAGER_IPS` config — includes child NMs with routable IPs).
  This is the same data source the CM uses for heartbeat relay.
- **NodeManager**: always returns `None` (leaf node, no children to VNC into).

Implementation: `BaseManager` defines `get_child_vnc_url` with a
`_resolve_vnc_ip(node_id)` hook. The base implementation reads
`_child_managers`. The SM overrides `_resolve_vnc_ip` to read
`_fleet_nodes`. Standard template method pattern — no fallback chains,
no multi-source degradation. If the node is not in the tier's data
source, returns `None` → page renders "Kiosk not reachable."

**`get_fleet_children(node_id: str) -> list[str]`** — returns the child
node IDs for a given parent. Used by the remote kiosk page to populate the
child node picker dropdown. Same tier-specific polymorphism:
- **SuperManager**: returns nodes whose heartbeats relay through `node_id`
  (derived from fleet topology in `_fleet_nodes`)
- **ClusterManager**: returns `list(_child_managers.keys())` when `node_id`
  matches this CM's identity, else empty
- **NodeManager**: always returns `[]` (leaf node)

Both are **manager methods**, not `data.py` functions. The manager owns the
child topology (`_child_managers`, `_fleet_nodes`). Pages call
`manager.get_instance().get_child_vnc_url()` and
`manager.get_instance().get_fleet_children()` and receive pre-resolved data.
This follows the pattern established by heartbeat relay and fleet status:
runtime state lives on the manager, not the data layer.

No tier reaches into another tier's domain. No fallback chains. Each tier
uses its own data source exclusively.

### SRP: page receives resolved URL

The `remote_kiosk.py` page is a pure renderer. It receives the resolved
WebSocket URL from the manager, renders the noVNC canvas, and provides
navigation chrome (back button, node name, connection status). It does not
resolve IPs, check health, or compute state. The caller (route handler)
resolves the URL via `manager.get_instance().get_child_vnc_url()`.

### Headless kiosk support

Hosts without physical displays need `WLR_BACKENDS=headless` for Cage to
create a virtual framebuffer. This environment variable is set in
`kiosk-display.service` (baked into the image in M0) alongside the existing
`WLR_LIBINPUT_NO_DEVICES=1`. Cage checks for DRM devices first and uses the
headless backend when none are found, so this is safe on hosts WITH physical
displays too (DRM takes priority).

The existing kiosk-display.service uses `TTYPath=/dev/tty7`. On hosts without
a physical TTY (headless), Cage's headless backend ignores TTY settings.

### LXC container capabilities

No new LXC features required. The kiosk container already has:
- `privileged: true` (for DRI device access)
- `nesting=1` (for systemd sandboxing compatibility)
- DRI device passthrough (for GPU rendering)

wayvnc and websockify are userspace network services that need no additional
container capabilities beyond what is already configured.

### Connection status color semantics

The noVNC connection indicator in `remote_kiosk.py` follows the design
system's color rules:
- **Grey**: connecting / unknown state (not green until confirmed)
- **Green**: connected and receiving framebuffer updates
- **Red**: connection failed (real error — refused, timeout)
- **Orange**: disconnected after being previously connected (degraded)

### Icon selection

"Open Kiosk" uses Material icon `cast` (screen casting / streaming). This
icon does not appear in any existing `NAV_SECTIONS`, `KIOSK_NAV_ITEMS`, or
`CLUSTER_NAV_SECTIONS` — verified by grepping `data.py` for icon usage. The
fleet card quick-access button uses `cast_connected` (filled variant) to
differentiate from the header-level button while staying in the same
semantic family.

### site.yml integration

No new `site.yml` plays. VNC streaming is part of the kiosk service, not a
separate service. The existing kiosk plays (Play 29: `kiosk_lxc`, Play 30:
`kiosk_configure`, tag `kiosk`) apply the updated role. The kiosk tag
already runs during normal converge. No opt-in `[never]` tag needed.

### Dynamic group reconstruction

The `molecule/kiosk-lxc/` per-feature scenario uses
`tasks/reconstruct_lxc_group.yml` for the kiosk dynamic group. No changes
needed — the existing reconstruction handles the kiosk container. VNC port
verification tasks target the Proxmox host group (`kiosk_nodes`), not the
dynamic group, so reconstruction is not affected.

## Skills Referenced

- `webui-design-system` — MVC architecture, color semantics, CSS patterns (M2, M3)
- `webui-ux-principles` — Navigation predictability, click reduction, layout (M2, M3)
- `webui-manual-testing` — Manual test walkthrough structure (M4)
- `manual-testing-playbook-writing` — Playbook authoring: enumeration, structure, host-awareness (M4)
- `manager-api-pattern` — 4-tier hierarchy, child manager IPs, tier separation (M1, M2)
- `python-code-style` — Type hints, error handling, domain model classes (M0, M2)
- `code-review-checklist` — MVC separation, test coverage, Ansible safety (M2)
- `ansible-conventions` — FQCN, task structure, variable patterns (M1)
- `ansible-shell-safety` — Pipefail, shell task patterns (M1)
- `molecule-verify` — Verify assertion patterns, completeness (M1)
- `proxmox-cleanup-safety` — Cleanup completeness, file removal (M1)
- `project-structure-rules` — Port forwarding pattern, design principles (M1)
- `systemd-lxc-compatibility` — Systemd units inside LXC containers (M0)
- `project-planning-structure` — Milestone template, dependency graph
- `project-planning-verification` — Verify sections, rollback completeness
- `project-planning-task-ordering` — Task dependency ordering
- `project-plan-review` — Structural validation, cross-reference checks
- `testing-workflow` — 6-step work cycle, TDD, fail-fast iteration (M0–M3)
- `image-management-patterns` — Image build, bake-not-configure (M0)

## Milestone Dependency Graph

```
M0 (bake VNC stack into kiosk image)
└── M1 (port forwarding + service configuration) ← depends on M0
    └── M2 (noVNC client + remote kiosk page) ← depends on M1
        └── M3 (Open Kiosk buttons + two-level integration) ← depends on M2
            └── M4 (manual testing playbook) ← depends on M3
```

## Testing Strategy

**(a) Parallelism in `molecule/default`:** VNC port reachability assertions
added to the existing kiosk verify section. No new molecule scenarios — VNC
is part of the kiosk service, not a separate service.

**(b) Per-feature scenario hierarchy:** Existing `molecule/kiosk-lxc/` scenario
validates kiosk builds. VNC assertions added there for fast iteration.

**(c) Day-to-day workflow:**

```bash
# Step 1: Update build-images.sh (M0)
# Step 2: Build kiosk images in parallel
./scripts/build-images.sh --host $PRIMARY_HOST --only kiosk &
./scripts/build-images.sh --host $AI_HOST --only kiosk &
./scripts/build-images.sh --host $MESH_2_HOST --only kiosk &
wait

# Step 3: Write tests while images build
pytest tests/test_webui_data.py tests/test_webui_app.py -v

# Step 4: E2E test
molecule test

# Step 5: Code review while E2E runs
# Step 6: Manual testing (Playbook 13)
```

**(d) Teardown table:**

| Scenario         | Creates            | Destroys          | Baseline impact |
|------------------|--------------------|--------------------|-----------------|
| kiosk-lxc        | CT 401 per host    | CT 401 per host    | None            |
| default (E2E)    | All CTs + VMs      | All CTs + VMs      | Full rebuild    |

---

### Milestone 0: Bake VNC Stack into Kiosk Image

_Self-contained._

Update `build-images.sh` to install wayvnc, websockify, and bake systemd
units for the VNC server and WebSocket bridge. Add port constants to
`group_vars/all.yml` and `data.py`. Build images in parallel across all 6
hosts.

No new pytest harness or molecule scaffolding — existing `test_webui_*` and
`molecule/kiosk-lxc/` patterns are extended in later milestones. M0 is
image-only.

See: `image-management-patterns` skill, `testing-workflow` skill,
`systemd-lxc-compatibility` skill (systemd units inside LXC containers),
`python-code-style` skill (data.py Ports class).

**Implementation pattern:**
- Modified: `scripts/build-images.sh` (`build_kiosk_lxc()`)
- Modified: `inventory/group_vars/all.yml` (port constants)
- Modified: `scripts/webui/data.py` (`Ports` class)
- New systemd units baked: `kiosk-vnc.service`, `kiosk-vnc-ws.service`
- site.yml: no new plays — existing kiosk plays (Play 29/30, tag `kiosk`)
  apply the updated role during normal converge

- [x] Add port constants: `kiosk_vnc_port: 5900` and
  `kiosk_vnc_ws_port: 6080` in `group_vars/all.yml`; `Ports.KIOSK_VNC`
  and `Ports.KIOSK_VNC_WS` in `data.py` `Ports` class
- [x] Update `build_kiosk_lxc()` in `build-images.sh`:
  - Add `wayvnc` and `python3-websockify` to `apt-get install`
  - Add `Environment=WLR_BACKENDS=headless` to `kiosk-display.service`
  - Bake `kiosk-vnc.service` (wayvnc on 0.0.0.0:5900, after
    kiosk-display, Restart=always) and `kiosk-vnc-ws.service`
    (websockify 0.0.0.0:6080→localhost:5900, after kiosk-vnc,
    Restart=always). Enable both services
- [x] Build kiosk images in parallel across all 6 hosts (Step 2)
- [x] Run `ansible-lint && yamllint .` after changes

**Verify:**
- [x] `build-images.sh --only kiosk` completes without errors on all hosts
- [x] Built image contains wayvnc and websockify packages
- [x] Both systemd units are enabled in the image
- [x] `ansible-lint` and `yamllint` pass

**Rollback (`--tags kiosk-vnc-rollback`):**
Remove wayvnc, websockify from the package list, remove systemd units,
remove `WLR_BACKENDS=headless` from kiosk-display.service, remove port
constants from `group_vars/all.yml` and `data.py`, rebuild images.

---

### Milestone 1: VNC Port Forwarding and Service Configuration

_Depends on: M0._

Make the VNC WebSocket port (6080) reachable from the network. Add DNAT rules
for WAN hosts and a socat proxy on the router node, following the exact same
pattern as port 9001. Write verify assertions FIRST (TDD), then implement
the forwarding rules.

See: `manager-api-pattern` skill (WAN host reachability section),
`project-structure-rules` skill (port forwarding pattern),
`ansible-conventions` skill (FQCN, task structure),
`ansible-shell-safety` skill (pipefail for shell tasks),
`molecule-verify` skill (verify assertion patterns),
`proxmox-cleanup-safety` skill (cleanup completeness).

**Implementation pattern:**
- Modified: `roles/kiosk_lxc/tasks/main.yml` (DNAT + socat for port 6080)
- Modified: `playbooks/cleanup.yml` (clean up VNC port forwarding)
- Modified: `molecule/default/verify.yml` (VNC port assertions)
- Modified: `molecule/kiosk-lxc/verify.yml` (per-feature VNC assertions)
- site.yml: no new plays — `kiosk_lxc` runs under existing Play 29, tag
  `kiosk`. The DNAT/socat tasks are added to the existing kiosk provisioning
  role, not a separate play
- Port references use `{{ kiosk_vnc_ws_port }}` from `group_vars/all.yml`,
  never hardcoded `6080`
- New Ansible tasks use FQCN (`ansible.builtin.shell`, `ansible.builtin.copy`,
  etc.). Shell tasks with `|` pipes include `set -o pipefail` and
  `executable: /bin/bash` per shell-safety conventions

- [x] **TDD: write verify assertions first** — add VNC port checks to
  `molecule/default/verify.yml` and `molecule/kiosk-lxc/verify.yml`
  BEFORE implementing DNAT (assertions will fail, proving they catch the gap)
- [x] In `roles/kiosk_lxc/tasks/main.yml`: add DNAT PREROUTING rule for
  port `{{ kiosk_vnc_ws_port }}` (duplicate the existing port 9001 DNAT
  block, change port). Same `when:` conditions (WAN hosts only, not
  router_nodes)
- [x] Add FORWARD rule for port `{{ kiosk_vnc_ws_port }}` (same pattern as
  9001 FORWARD rule)
- [x] Deploy `kiosk-vnc-proxy.service` socat unit on router node:
  `socat TCP-LISTEN:{{ kiosk_vnc_ws_port }},reuseaddr,fork
  TCP:{{ _lxc_net_ip }}:{{ kiosk_vnc_ws_port }}`.
  Same `when:` condition (router_nodes only)
- [x] Enable and start `kiosk-vnc-proxy` on router node
- [x] Add cleanup tasks to `playbooks/cleanup.yml`: remove iptables DNAT
  rules for port `{{ kiosk_vnc_ws_port }}`, stop and remove
  `kiosk-vnc-proxy.service`
- [x] Run `ansible-lint && yamllint .` after changes

**Verify:**
- [x] `molecule test -s kiosk-lxc` passes with VNC port assertions
- [x] Verify assertion: websockify port 6080 is listening inside the kiosk
  container (`pct exec 401 -- ss -tlnp | grep 6080`)
- [x] Verify assertion: port 6080 reachable from the Proxmox host (tests
  DNAT/socat forwarding)
- [x] DNAT rules visible in `iptables -t nat -L` on WAN hosts
- [x] socat proxy running on router node: `systemctl is-active kiosk-vnc-proxy`
- [x] `ansible-lint` and `yamllint` pass

**Rollback (`--tags kiosk-vnc-rollback`):**
Remove DNAT tasks and socat unit from `kiosk_lxc`, remove cleanup tasks
from `playbooks/cleanup.yml`, remove verify assertions.

---

### Milestone 2: noVNC Client and Remote Kiosk Page

_Depends on: M1._

Commit noVNC JavaScript files, create the remote kiosk viewer page with
embedded noVNC canvas, and add `get_child_vnc_url()` to the manager
hierarchy.

See: `webui-design-system` skill, `webui-ux-principles` skill,
`python-code-style` skill, `manager-api-pattern` skill,
`code-review-checklist` skill (MVC separation review).

**Implementation pattern:**
- New: `scripts/webui/static/noVNC/` (committed vendored JS files, ~200KB)
- New: `scripts/webui/pages/remote_kiosk.py`
- Modified: `scripts/webui/manager.py` (`get_child_vnc_url` + `get_fleet_children`)
- Modified: `scripts/webui/data.py` (route + label + port constants)
- Modified: `scripts/build-images.sh` (include `static/` in tar)
- site.yml: no changes

- [x] **TDD: write unit tests first** — add tests for `get_child_vnc_url`,
  `get_fleet_children`, route existence, and `data.py` constants BEFORE
  implementing the methods and page (tests will fail, proving they catch
  the gap). Same TDD discipline as M1
- [x] Download pinned noVNC release from GitHub, extract `core/` and
  `vendor/` to `scripts/webui/static/noVNC/`. **Commit to git** (files are
  small, ensures offline reproducibility, no CI download dependency)
- [x] Add manager methods to `BaseManager` in `manager.py`:
  - `get_child_vnc_url(node_id: str) -> str | None` — builds
    `ws://{ip}:{Ports.KIOSK_VNC_WS}` using tier-specific IP resolution.
    Base `_resolve_vnc_ip()` reads `_child_managers`. SM overrides to
    read `_fleet_nodes`. No fallback chain — each tier uses one source
  - `get_fleet_children(node_id: str) -> list[str]` — SM returns children
    from fleet topology; CM returns `_child_managers.keys()`; NM returns `[]`
- [x] Add `data.py` constants: `Routes.REMOTE_KIOSK`, `PageTitles.REMOTE_KIOSK`,
  `Labels.OPEN_KIOSK`, `Labels.OPEN_DIRECTLY`, `Labels.KIOSK_NOT_REACHABLE`,
  `Labels.DRILL_INTO`
- [x] Register `remote_kiosk` in `app.py` (all nodes) and `kiosk_server.py`
  (CM only, inside `if is_cluster:` block). Add `app.add_static_files` for
  noVNC. NMs do NOT register this route. Update `build-images.sh` to include
  `static/` in the webui tar
- [x] Create `scripts/webui/pages/remote_kiosk.py` with `register()`:
  - Route: `@ui.page("/remote/{node_id}")`, query param `back: str = "/nodes"`
  - Resolves VNC URL and children via manager methods
  - **Connected state**: noVNC canvas with top bar (40px): back button, node
    name, child picker dropdown (visible only when children exist, label
    `Labels.DRILL_INTO`), connection status indicator (grey/green/red/orange
    per design system), "Open in new tab" link. JavaScript loads
    `/static/noVNC/core/rfb.js` as ES module, `rfb.scaleViewport = true`
  - **Error state** (URL is `None`): `Labels.KIOSK_NOT_REACHABLE` with back
    button — follows `viewer.py` structural pattern
  - Theme constants only — no hardcoded hex. `.viewer-bar`, `.viewer-frame`
- [x] Add unit tests — **zero mocks** (all methods are pure functions over
  in-memory dicts; route tests use NiceGUI's standard `user` fixture):
  - `test_webui_app.py`: construct real manager instances, populate
    `_child_managers` / `_fleet_nodes` with test data, call
    `get_child_vnc_url` and `get_fleet_children` directly. Verify SM
    resolves from `_fleet_nodes`, CM from `_child_managers`, NM returns
    `None`/`[]`. Verify route `/remote/{node_id}` renders via NiceGUI
    test client. No MagicMock — if a test needs a manager, build a real
    one with real config values
  - `test_webui_data.py`: pure assertions on class attributes — all new
    constants exist (`Routes.REMOTE_KIOSK`, `Labels.OPEN_KIOSK`,
    `Labels.DRILL_INTO`, `Ports.KIOSK_VNC`, `Ports.KIOSK_VNC_WS`, etc.)
  - If ANY mock is introduced during implementation, it MUST have an
    inline comment explaining (1) what irreversible side effect it
    prevents and (2) how the test still validates the feature. If both
    sentences cannot be written, the mock is unjustified — remove it

**Verify:**
- [x] `pytest tests/test_webui_app.py -k "vnc or remote_kiosk"` passes
- [x] `pytest tests/test_webui_data.py -k "remote_kiosk or open_kiosk or
  kiosk_vnc or drill_into"` passes
- [x] `remote_kiosk.py` uses theme constants only — no hardcoded hex colors
- [x] `remote_kiosk.py` follows `viewer.py` structural pattern
- [x] `get_child_vnc_url` is a method on `BaseManager`, not in `data.py`
- [x] `get_child_vnc_url` uses `Ports.KIOSK_VNC_WS`, never hardcoded port
- [x] SM's `get_child_vnc_url` resolves from `_fleet_nodes`, CM's from
  `_child_managers` — no fallback between the two (tier polymorphism)
- [x] `get_fleet_children` is a method on `BaseManager`, returns `list[str]`
- [x] `get_fleet_children` returns non-empty list for CM, empty for NM
- [x] Child node picker dropdown renders only when children exist
- [x] noVNC JS files are committed to git and served at
  `/static/noVNC/core/rfb.js` by both `app.py` and `kiosk_server.py`
- [x] Connection status colors follow design system semantics (grey/green/
  red/orange — not green until connected is confirmed)

**Rollback (`--tags kiosk-vnc-rollback`):**
Remove `remote_kiosk.py`, remove `scripts/webui/static/noVNC/` directory,
remove `get_child_vnc_url` and `get_fleet_children` from manager.py, remove
route/label/port constants from data.py, revert build-images.sh tar changes.

---

### Milestone 3: Open Kiosk Buttons and Two-Level Integration

_Depends on: M2._

Add "Open Kiosk" navigation to node detail pages on both tiers. Run full
E2E `molecule test` to validate all VNC infrastructure. Verify the full
two-level chain works with direct VNC: SuperManager → view ClusterManager
kiosk → drill into NodeManager kiosk (direct connection) → fullscreen app.

See: `webui-ux-principles` skill, `webui-design-system` skill,
`testing-workflow` skill.

**Implementation pattern:**
- Modified: `scripts/webui/pages/nodes.py` (SuperManager node detail)
- Modified: `scripts/webui/pages/cluster_dashboard.py` (ClusterManager fleet)
- site.yml: no changes

- [x] **TDD: write tests first** — verify "Open Kiosk" button rendering
  conditional (only when VNC URL available), child picker visibility, and
  back URL correctness. Tests fail first, then implement (same as M1/M2)
- [x] Verify icon uniqueness: grep `data.py` for `"cast"` — confirm it does
  not appear in `NAV_SECTIONS`, `KIOSK_NAV_ITEMS`, or
  `CLUSTER_NAV_SECTIONS`. Use `cast` for header buttons and
  `cast_connected` for fleet card quick-access
- [x] In `nodes.py` node detail page (`/nodes/{hostname}`): add "Open Kiosk"
  button (`icon="cast"`, label from `Labels.OPEN_KIOSK`) in the header.
  Navigates to `/remote/{hostname}?back=/nodes/{hostname}`. Only render when
  `get_child_vnc_url(hostname) is not None`
- [x] In `cluster_dashboard.py` fleet node detail (`/fleet/{node_id}`): add
  "Open Kiosk" button next to "Back to Fleet". Navigates to
  `/remote/{node_id}?back=/fleet/{node_id}`. Only render when URL available
- [x] In `cluster_dashboard.py` fleet list (`/fleet`): add small
  `cast_connected` icon button on each node card (right side) for direct
  one-click access to `/remote/{node_id}?back=/fleet`. Only render when
  URL available
- [x] Run `ansible-lint && yamllint .` after all code changes
- [x] Run `pytest tests/ -v` — all unit tests pass
- [x] Run `molecule test` (Step 4) — full E2E validates all kiosks running
  with VNC ports accessible, all containers heartbeating
- [x] **After `molecule test` passes**, verify two-level chain on real
  hardware using **direct VNC** (not VNC-in-VNC):
  1. Start SuperManager, open `/remote/home` → direct VNC into CM kiosk
  2. In the **parent page top bar** (not inside VNC), use the child picker
     dropdown to select a child node (e.g., mesh1) → page navigates to
     `/remote/mesh1?back=/remote/home?back=/nodes/home` → browser opens
     **direct VNC to mesh1** (replaces the home connection)
  3. Operator now sees mesh1's kiosk display at single-layer VNC quality.
     Click an internal page tile (e.g., Containers) in mesh1's hub →
     page loads inside Cage, visible through single VNC layer. Verify
     any "Not available" badges render for apps not deployed on mesh1
  4. Click back → returns to `/remote/home` → direct VNC to home again
- [x] Verify back navigation: back URL chain correctly returns through each
  level (NM → CM → SM node detail)

**Verify:**
- [x] "Open Kiosk" button appears on `/nodes/{hostname}` only when child
  VNC URL is available
- [x] "Open Kiosk" button appears on `/fleet/{node_id}` detail page
- [x] Quick-access `cast_connected` icon appears on `/fleet` node cards
- [x] `cast` icon not used by any other nav item (design-system uniqueness)
- [x] Two-level chain works via **direct VNC**: SM views CM kiosk, selects
  child from dropdown, browser connects directly to NM (no nesting)
- [x] Mouse and keyboard input works through single VNC layer at every level
  (view-only: `--disable-input` due to Cage 0.1.4 lacking virtual pointer)
- [x] Back button returns through the URL chain (NM → CM VNC → SM page)
- [x] Child picker dropdown appears only when viewing a node with children
- [x] Child picker is hidden when viewing a leaf NodeManager
- [x] `ansible-lint`, `yamllint`, `pytest` all pass

**Rollback (`--tags kiosk-vnc-rollback`):**
Remove "Open Kiosk" buttons from nodes.py and cluster_dashboard.py.

---

### Milestone 4: Manual Testing Playbook

_Depends on: M3 (including successful `molecule test` and fleet health
confirmation)._

Execute Playbook 13 (already written in `docs/manual-testing-playbooks.md`)
covering the full hierarchical VNC navigation workflow. Update architecture
docs. Manual testing MUST only run after `molecule test` has passed, all 6
hosts are on the 10.10.10.x LAN, all containers are deployed and
heartbeating, and all VNC services are active.

See: `webui-manual-testing` skill (prerequisites section),
`manual-testing-playbook-writing` skill (playbook authoring patterns).

**Prerequisites (from webui-manual-testing skill):**
- Python virtualenv active (`source .venv/bin/activate`)
- `set -a && source test.env && set +a` loaded
- `molecule test` completed successfully (all 6 hosts converged)
- `.state/callhome_url` exists and API responds
- All 6 hosts SSH-reachable
- `pytest tests/ -v` passes (includes infrastructure health checks
  and heartbeat infrastructure validation)

**Implementation pattern:**
- Already complete: `docs/manual-testing-playbooks.md` (Playbook 13 written
  during plan creation — sections 13.1–13.7 including per-app 13.4a-d)
- Already complete: `.agents/skills/webui-manual-testing/SKILL.md` (updated
  during plan creation with Playbook 13 reference)
- Modified: `docs/architecture/overview.md`

Playbook 13 is the **single source of truth** for all manual VNC test steps.
It lives in `docs/manual-testing-playbooks.md` and contains 7 sub-sections
(13.1–13.7). The project plan does NOT duplicate its content — read the
playbook file for exact commands and expected outcomes.

- [x] Verify `docs/manual-testing-playbooks.md` contains Playbook 13 with
  all 7 sub-sections: 13.1 VNC health check, 13.2 SM→CM drill-down,
  13.3 two-level drill-down, 13.4 every hub app (13.4a display apps:
  Desktop/Kodi/Moonlight, 13.4b external web UIs: all 8 services,
  13.4c internal pages: all 4 pages, 13.4d two-level drill-down app
  launch), 13.5 direct SM→NM, 13.6 CM-perspective VNC from CM's own UI,
  13.7 error/edge cases
- [x] Verify `.agents/skills/webui-manual-testing/SKILL.md` references
  Playbook 13 in its "Manual Testing Playbooks" section
- [x] Update `docs/architecture/overview.md`: add VNC streaming description
  in the kiosk section, document port 5900/6080, document wayvnc + websockify
  as kiosk image components
- [x] **EXECUTE Playbook 13, ALL 7 sections (13.1–13.7)** against the fully
  converged system. Run every command. Click every button. Verify every
  expected outcome. No section may be skipped. If any section fails, fix
  the issue and re-run that section until it passes. This is NOT a
  read-through — it is hands-on execution against real hardware
- [x] After all 7 sections pass: confirm the complete VNC chain works
  end-to-end (SM → CM kiosk → drill into NM kiosk via child picker →
  launch fullscreen app → back through hierarchy)

**Verify:**
- [x] Playbook 13 exists in `docs/manual-testing-playbooks.md` with all 7
  sub-sections (13.1–13.7, including 13.6 CM-perspective VNC)
- [x] All 7 sections of Playbook 13 executed and passed on real hardware
  (all 6 hosts on LAN, all containers heartbeating, all VNC services active)
- [x] Two-level chain (13.3) verified: SM browser → direct VNC to CM →
  child picker selects NM → direct VNC to NM (single layer, not nested)
- [x] Every hub app (13.4) verified: all 3 display apps (Desktop, Kodi,
  Moonlight) launched fullscreen, interacted with, exited back to hub
  with VNC green (13.4a). All 8 external web UIs (Jellyfin, Home
  Assistant, Gaming, OpenWrt, Pi-hole, WireGuard, Netdata, Logs) opened
  in viewer iframe, interacted with, returned to hub (13.4b). All 4
  internal pages (Bridge, Mesh, Router, Containers) navigated to and
  back (13.4c). Two-level drill-down app launch verified on mesh1 (13.4d)
- [x] CM-perspective VNC (13.6) verified by accessing CM's web UI directly
  (NOT through SM), confirming `_child_managers` resolution path works
- [x] Error states (13.7) verified: nonexistent node, stopped VNC service,
  browser resize

**Rollback (`--tags kiosk-vnc-rollback`):**
Remove Playbook 13 from manual-testing-playbooks.md, revert skill and
architecture doc changes.

---

## Standard Work Cycle

This project **requires** the full 6-step standard work cycle because it
modifies `build-images.sh` (M0) and configure/provisioning roles (M1).

1. **Step 1**: Update `build-images.sh` — bake wayvnc, websockify, systemd
   units into kiosk image (M0)
2. **Step 2**: Build kiosk images in parallel across all 6 hosts (M0).
   `./scripts/build-images.sh --host <ip> --only kiosk` on each host
3. **Step 3**: Write tests and playbook updates while images build — unit
   tests for manager method, verify assertions for VNC ports, remote kiosk
   page (M1, M2, M3). Run `ansible-lint && yamllint .` after Ansible
   changes. Run `pytest tests/ -v` after Python changes
4. **Step 4**: Run `molecule test` after images are ready (M3). Fresh images
   trigger container recreation via version-mismatch system
5. **Step 5**: Code review while E2E runs — MVC separation (no business
   logic in remote_kiosk.py), theme constant usage, tier separation
   (get_child_vnc_url and get_fleet_children on manager, not data.py),
   tier-specific VNC IP resolution (SM from `_fleet_nodes`, CM from
   `_child_managers` — no fallback chain between them),
   DNAT/socat pattern consistency with port 9001, no VNC nesting (M3)
6. **Step 6**: Manual testing — **execute** Playbook 13
   (`docs/manual-testing-playbooks.md`), ALL 7 sections (13.1–13.7)
   including every hub app (13.4a: 3 display apps — Desktop, Kodi,
   Moonlight; 13.4b: 8 external web UIs — Jellyfin, Home Assistant,
   Gaming, OpenWrt, Pi-hole, WireGuard, Netdata, Logs; 13.4c: 4
   internal pages — Bridge, Mesh, Router, Containers; 13.4d: two-level
   drill-down), against the fully converged system. Launch, interact
   with, and exit EVERY app. No skips. If a section fails, fix and
   re-run. This is hands-on execution, not a read-through (M4)

## Constraints

- NEVER nest VNC connections (VNC-in-VNC). Every browser — whether it is
  the SuperManager's browser on the operator's laptop, or the
  ClusterManager's Chromium running in Cage on the physical kiosk —
  always opens a **single, direct** WebSocket to the target node's
  websockify. When drilling from a CM view into a child NM, the browser
  disconnects from the CM and connects directly to the NM. Navigation in
  the parent page's HTML chrome (child picker dropdown, back button)
  creates the hierarchy — not VNC tunneling. Both `app.py` (SuperManager)
  and `kiosk_server.py` (ClusterManager) serve the same `/remote/{node_id}`
  page with the same direct-connection behavior. The difference is only
  the entry point: the operator reaches the SM via a laptop browser, and
  the physical kiosk reaches the CM via Cage/Chromium — both use one VNC
  layer to the target.
- NEVER install VNC packages in configure roles. wayvnc and websockify are
  baked into the image via `build-images.sh`. Configure roles only enable
  services and set host-specific topology.
- NEVER proxy WebSocket connections through NiceGUI. The browser connects
  directly to the target's websockify. This is an intentional exception to
  the "UI talks to manager API via HTTP" rule: VNC is a direct browser ↔
  container WebSocket stream, not a manager-relayed operation. The manager
  only provides the URL; the data path bypasses the manager entirely.
- NEVER put VNC URL resolution or child topology in `data.py`. The manager
  owns its child topology. `get_child_vnc_url()` and `get_fleet_children()`
  live on `BaseManager`.
- NEVER use a fallback chain for VNC URL resolution. Each tier uses exactly
  one data source: SM uses `_fleet_nodes`, CM uses `_child_managers`, NM
  returns `None`. The `_resolve_vnc_ip()` hook provides polymorphism — the
  SM NEVER falls back to `_child_managers` when `_fleet_nodes` misses.
- NEVER use iframes. noVNC's RFB class renders directly to a canvas element
  inside the NiceGUI page via JavaScript.
- NEVER add new API endpoints for VNC. The VNC WebSocket connection goes
  directly to the target's websockify — no relay through the manager.
- NEVER hard-code VNC ports in Python, Ansible, or JavaScript. Use
  `Ports.KIOSK_VNC_WS` (Python), `{{ kiosk_vnc_ws_port }}` (Ansible), or
  resolve via `get_child_vnc_url()`. Port values are defined once in
  `group_vars/all.yml` and `data.py`.
- ALWAYS follow the `viewer.py` structural pattern for the remote kiosk page
  (top bar + full-viewport content).
- ALWAYS use the same DNAT/socat pattern as port 9001 for port 6080. No
  new forwarding mechanisms.
- ALWAYS run `ansible-lint && yamllint .` after Ansible changes.
- ALWAYS run `pytest tests/ -v` before `molecule test`.

## Security Considerations

- VNC port 6080 is accessible only within the fleet network (LAN or VPN).
  No public internet exposure.
- VNC authentication is not configured. The kiosk API (port 9001) and all
  other fleet services also run without authentication on the private
  network. VNC follows the same trust model. wayvnc natively supports
  password authentication via its config file (`/etc/wayvnc/config`). To
  enable it, add `-C /etc/wayvnc/config` to the kiosk-vnc.service
  ExecStart and populate the config file via `kiosk_configure` if the
  trust model changes.
- WebSocket connections are unencrypted (`ws://`). For VPN-tunneled
  national hosts, the VPN provides encryption. For local LAN, plaintext
  is acceptable (same as port 9001 API traffic).

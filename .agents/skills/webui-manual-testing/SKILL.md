---
name: webui-manual-testing
description: Manual testing procedures for the NiceGUI web UIs (SuperManager, Manager, Kiosk). Use when performing manual test walkthroughs, verifying UI behavior after changes, or when the user asks to "manually test" the web apps.
---

# Web UI Manual Testing

## Context

The web UI serves three distinct roles on different ports. Manual testing
verifies that the automation pipeline, heartbeat system, and UI rendering
work end-to-end. Automated tests (`pytest tests/test_webui_*.py`) cover
component logic and API contracts but cannot catch visual regressions,
layout overflows, or workflow usability issues.

## Prerequisites

ALWAYS verify these before starting a manual test session:

1. **System fully converged**: `molecule test` (or `molecule converge`) MUST
   have completed successfully. ALL 6 hosts on the 10.10.10.x LAN, ALL
   containers deployed and heartbeating, mesh established. NEVER start
   manual testing against a partially deployed or pre-mesh system. If the
   system is not converged, run `molecule test` first — do NOT proceed.
2. **Environment loaded**: `set -a && source test.env && set +a`
3. **Virtualenv active**: `source .venv/bin/activate`
4. **All hosts reachable**: `pytest tests/test_webui_heartbeat.py -k "infrastructure" -v`
   — if any host is unreachable, STOP. Fix reachability first.
5. **Callhome API running**: Check `.state/callhome_url` exists, or start
   via `python scripts/webui/app.py --headless`

Previous catastrophe (2026-04-10): Agent started manual testing in pre-mesh
state without running molecule test first. Every playbook that touched WAN
hosts returned "No route to host." The entire session was wasted verifying
expected failures instead of testing real functionality.

## Starting the apps

```bash
# SuperManager (port 9001 default)
python scripts/webui/app.py

# Production mode (with .env)
set -a && source .env && set +a && python scripts/webui/app.py

# Test mode (with test.env)
set -a && source test.env && set +a && python scripts/webui/app.py
```

All three roles (SuperManager, Manager, Kiosk) run from the same app.
The sidebar navigation switches between them. Kiosk pages are also
accessible from their own nav bar.

## Test walkthrough checklist

### Phase 1: Dashboard (SuperManager)

Route: `/` (Dashboard)

1. **Fleet card renders**: Health score, online/offline counts, bucket
   summary line ("N Production · N Lab · N Test Units").
2. **All configured hosts appear**: Count matches `test.env` hosts + `TEST_UNITS`.
   In test mode, expect 6 hosts (4 env + 2 test units from `TEST_UNITS`).
3. **Host status colors correct**: Green = heartbeating. Orange = reachable
   but no heartbeat. Grey = offline/unknown. Red = NEVER for "no data."
4. **View Fleet button navigates** to `/nodes`.
5. **No layout overflows**: Resize browser to 1024px, 768px, mobile width.
   No text truncation, no overlapping buttons, no elements escaping cards.

### Phase 2: Fleet Nodes

Route: `/nodes`

1. **Bucket sections visible**: Hosts grouped under "PRODUCTION", "LAB",
   "TEST UNITS" headers with counts.
2. **Each host card clickable** → navigates to `/nodes/{hostname}`.
3. **Add Host form works**: Expand "Add Host", fill name + IP, submit.
   Confirm success message and host appears in list after refresh.
4. **Auto-detect bucket**: Submit a host with IP ending in .201 → should
   classify as "test". IP ending in .50 → "lab". IP ending in .180 → "lab".

### Phase 3: Node Detail

Route: `/nodes/{hostname}` (click any host card)

1. **Summary header**: Status dot, hostname, IP, uptime, version.
2. **Resource gauges**: Disk and memory with color thresholds (green < 70%,
   orange < 90%, red >= 90%).
3. **Guests table**: Shows containers/VMs with VMID, name, type, status.
   If no heartbeat, shows "Waiting for heartbeat data."
4. **Deploy history**: Pass/fail badges with human-readable descriptions
   (NOT raw "rc=2"). Recent success after past failure → host shows healthy.
5. **Network info**: IPs, interfaces from extensions.
6. **Back navigation**: Back button returns to `/nodes`.

### Phase 4: Deploy workflow

Route: `/deploy`

1. **Service selection**: Tags load from `SERVICE_TAGS` in `data.py`.
2. **Start Deploy**: Confirm dialog, progress indication, completion status.
3. **Deploy history updates** on dashboard after deploy finishes.

### Phase 5: Kiosk (Home Hub)

Route: `/hub`

1. **All hub services render**: Jellyfin, Kodi, Moonlight, Desktop tiles.
2. **Section grouping**: Services grouped by section ("Entertainment",
   "Infrastructure", etc.).
3. **Launch tiles functional**: Click Jellyfin → `/launch?vmid=...` →
   shows launch page with description.
4. **Kiosk nav bar**: Shows "Home" icon + breadcrumb, NOT "Home Hub" text.

### Phase 6: Network pages (Manager)

Routes: `/bridge`, `/mesh`, `/router`

1. **Bridge**: Both bridge hosts show with WiFi status (AP/STA roles).
   Batman status renders. Signal quality colors use theme semantics.
2. **Mesh**: Mesh AP + STAs visible. WiFi metrics (signal, channel, PHY).
3. **Router**: OpenWrt status, WAN/LAN interface info.
4. **Containers**: Container list with start/stop controls.

### Phase 7: Cross-cutting checks

1. **Sidebar navigation**: Every nav item leads to the correct page.
   No duplicate icons. Active page highlighted.
2. **Color consistency**: Same status = same color across all pages.
   Red ONLY for errors/failures. Grey for no-data/offline.
3. **No raw internals visible**: No exit codes, no JSON, no stack traces,
   no internal identifiers shown to users anywhere.
4. **Responsive layout**: Cards, tables, and gauges adapt to window resize.
   No horizontal scrolling at 1024px width.

## When tests fail

When a manual test reveals an issue:

1. **Fix the automation, not the symptom.** If wrapper scripts are missing,
   run `molecule converge`, don't manually SSH and deploy. If heartbeats
   aren't starting, fix the callhome config, don't hardcode status.
2. **Add a pytest test** that catches the regression. If the issue was a
   layout overflow, add a test that verifies the element exists with the
   correct CSS class. If it was a missing host, add a test for host count.
3. **Update this skill** if the test walkthrough missed the failure case.

## Rules

1. NEVER manually deploy scripts, config files, or patches during testing.
   If infrastructure is missing, run the proper automation (`molecule converge`,
   `build-images.sh`, `build.py`).
2. ALWAYS verify the system is fully converged (molecule test completed,
   all hosts on LAN, all containers running) before starting manual testing.
   NEVER test in a pre-mesh or partially deployed state — it produces a wall
   of "No route to host" errors that prove nothing.
3. ALWAYS test with both `test.env` AND `.env` if production hosts are
   available. Bucket classification, host counts, and heartbeat behavior
   differ between environments.
4. ALWAYS resize the browser to multiple widths during testing. Layout
   bugs only appear at non-default sizes.
5. NEVER consider manual testing complete until every phase above has
   been checked. Skipping Phase 6 (network pages) because "they probably
   work" is how batman_trigger.sh went unnoticed for multiple sessions.
6. ALWAYS check that new hosts from `TEST_UNITS` appear in the correct
   bucket on first load (no manual registration needed).
7. After manual testing, run `pytest tests/ -v` to confirm automated tests
   still pass — manual fixes sometimes break automated expectations.
8. NEVER fabricate heartbeat data with curl/scripts to simulate real nodes.
   Use the REAL physical test machines with REAL heartbeats from REAL
   containers. Fabricated data is the same anti-pattern as mocking — it
   produces a pretty dashboard that proves nothing about whether the
   system actually works.
9. ALWAYS actually engage interactive features during testing. Viewing
   a page is NOT testing it. Click the batman toggle. Click Restart WiFi.
   Click bridge mode switch. Verify the REAL operation ran on the REAL
   hardware by checking the REAL output.
10. NEVER claim "manual testing complete" after only viewing pages.
    Every button, toggle, and action on the page must be exercised against
    real infrastructure. If an action can't be tested because infrastructure
    is missing, that is a test failure — not a skip.

### Cluster Manager testing (4-tier)

When testing the Cluster Manager (`kiosk_server.py --config` with
`IS_CLUSTER_MANAGER=true`):

- The Cluster Manager runs on the router node (home). Its child Managers
  are the 5 other nodes: mesh1, mesh2, ai, bridge-1, bridge-2.
- Child Managers must be REAL kiosk_server instances running on REAL
  hosts, heartbeating to the Cluster Manager. NEVER simulate heartbeats
  with curl.
- Batman mode testing: enable batman from the Cluster Manager GUI. Verify
  the event was broadcast to ALL child Managers. Verify each child Manager
  executed batman_trigger.sh on its local containers. Check batman status
  on every node.
- Bridge/WiFi testing: restart WiFi, switch AP/STA modes, verify the
  operations ran on the actual bridge containers.
- If kiosk containers are not deployed on the test hosts, deploy them
  first with `molecule converge` — do NOT fake the data.

## Manual Testing Playbooks

Step-by-step runbooks with exact commands for each test scenario are in
`docs/manual-testing-playbooks.md`. The playbooks cover:

- Playbook 1: Kiosk container health checks (all 6 hosts)
- Playbook 2: Cluster Manager fleet verification (API queries)
- Playbook 3: Batman mode enable/disable/status (with verification on real containers)
- Playbook 4: WiFi status on mesh/bridge containers
- Playbook 5: Guest management via Manager API
- Playbook 6: Child Manager direct communication
- Playbook 7: End-to-end heartbeat chain (Container → Manager → Cluster Manager)
- Playbook 8: Image version pipeline verification
- Playbook 9: SuperManager verification (API + fleet health)
- Playbook 10: Kiosk functionality on each unit (service, HTTP, callhome, config)
- Playbook 11: Manager kiosk fleet dashboard (home CM)
- Playbook 12: Browser-based UI verification (SM, CM, NM visual + interactive)
- Playbook 13: Hierarchical kiosk control via KasmVNC display pipeline
  (KasmVNC iframe streaming, single-process display service per container/VM,
  two-level drill-down with back-navigation, EVERY hub app tested via iframe —
  13.4a: 3 display apps (Desktop, Kodi, Moonlight) via `/console` iframe,
  13.4b: 8 external web UIs (Jellyfin, Home Assistant, Gaming, OpenWrt,
  Pi-hole, WireGuard, Netdata, Logs), 13.4c: 4 internal pages (Bridge, Mesh,
  Router, Containers), 13.4d: two-level drill-down app launch,
  CM-perspective display, error/edge cases, legacy VNC absence verification)
- Playbook 14: Cluster Manager sidebar and submenu navigation (full nav tree)
- Playbook 15: NodeManager kiosk navigation on individual unit
- Playbook 16: SuperManager full page navigation (all SM pages: Dashboard,
  Environment, Hosts, Services, Images, Deploy, Timeline, Nodes)
- Playbook 17: Display app launch flow end-to-end (hub tile → launch page →
  guest start → console page → KasmVNC iframe. Session switching KDE↔GNOME,
  app switcher round-trips, display conflict resolution)
- Playbook 18: External web UI testing via hub tiles (Jellyfin, Home Assistant,
  Router, Pi-hole, WireGuard, Netdata, Logs, Gaming — iframe viewer + X-Frame-Options)
- Playbook 19: Internal kiosk pages — CM interactive elements deep test
  (Bridge WiFi restart, Mesh batman cycle, Router data verification,
  Containers guest lifecycle)
- Playbook 20: SuperManager display pipeline — every host verification
  (6-host × 4-app matrix, child picker drill-down, non-available app errors)

ALWAYS use the playbooks for manual testing. They contain the exact commands
to run against real infrastructure — no improvisation needed. EXECUTE every
section — do not just read through them. Every button, toggle, and action
must be exercised against real hardware.

When WRITING new playbooks, load the `manual-testing-playbook-writing` skill
for exhaustive feature enumeration, section structure, host-awareness, and
per-app test step patterns.

## Previous bugs found by manual testing

- "Desktop" icon (`monitoring`) overlapped adjacent buttons at narrow
  widths. Fix: changed to `lan` icon with proper spacing.
- Fleet card showed "No nodes registered" when API server wasn't running.
  Fix: show configured hosts from env with "waiting for heartbeats" message.
- `rc=2` displayed raw in deploy badges. Fix: `exit_code_label()` mapping.
- Only 4 of 6 hosts appeared in production mode. Fix: `HostRegistry` +
  `TEST_UNITS` env var support.
- Bridge batman tests failed because wrapper scripts were never deployed
  via automation. Fix: `molecule converge -s bridge-lxc` instead of manual
  SSH deployment.
- Color semantics violated: red used for "no WoL", "stopped", "no data".
  Fix: systematic audit using grey/orange per semantic rules.
- Stale `vnc_shared.cpython-312.pyc` in `__pycache__` caused the running SM
  to serve legacy noVNC JavaScript (`rfb.js`, `vnc-container`) even after
  `vnc_shared.py` was deleted. The SM process started before the migration was
  using cached bytecode. Fix: delete stale `.pyc` files and restart the SM
  process after code changes. ALWAYS restart long-running Python processes
  after source code deletions — `__pycache__` keeps serving deleted modules.
- Orphan socat process (from previous session) held port 16080 on the primary
  host, causing `kiosk-display-relay-mesh1.service` to crash-loop with "Address
  already in use" (1720+ restarts). Fix: kill the orphan, restart the service.
  ALWAYS check for orphan socat processes before investigating service failures.
- Router VM (VMID 100) was stopped during manual testing session. Batman status
  returned "No route to host" for all child Managers because the router provides
  LAN↔WAN routing. Fix: always verify the router VM is running before starting
  batman/WiFi tests. The router may stop between molecule test runs.
- SSH tunnels for DNAT hosts (ai, mesh2) must target the container's NAT IP
  directly (`10.99.x.20:6080`) not `127.0.0.1:6080`. DNAT only matches on
  the external interface, not loopback.
- mesh1 KasmVNC display is blank from the SM when the controller has no VPN
  interface (wg0). The `_env_node_resolver` falls back to LAN IP (10.10.10.210)
  which the browser can't reach directly — it's behind the OpenWrt router.
  In production with VPN, this resolves to the VPN IP (10.0.0.2) and works.
  This is expected behavior in the non-VPN development environment.
- SM Hub "Launch" buttons are shared code designed for kiosk (NM) usage.
  On the SM, the launch page renders correctly but the "View Console" link
  is absent because the SM has no `host_name` set. The correct flow for
  remote app launching is SM → Open Kiosk (remote display) → use the kiosk's
  own Hub to launch apps within the KasmVNC iframe.
- SM Hub external service tiles (Jellyfin, HA, Pi-hole, Netdata) show
  "Not available" because those URLs come from kiosk `config.json` which the
  SM doesn't have. Test these tiles through the remote kiosk viewer by clicking
  "Open Kiosk" on a node detail page and interacting within the KasmVNC iframe.
- CM tunnel via socat + nsenter is required for browser testing. Direct SSH
  tunnels to the kiosk LAN IP (10.10.10.23) can fail due to IP collisions.
  Use: `lxc-info -n 401` to get PID, `nsenter -t $PID -n socat` to relay
  through the container's network namespace.
- KasmVNC iframe content cannot be interacted with via browser automation tools
  (Playwright/Puppeteer). The iframe renders a VNC stream, not DOM elements.
  Mouse/keyboard interaction must be verified manually or via VNC-specific APIs.
  Browser automation can verify the iframe loads (non-blank) and the viewer bar
  controls work, but NOT the content inside the stream.
- Quasar QSelect components with opacity:0 inputs cannot be clicked via browser
  automation refs. Use coordinate-based clicks on the visible label/arrow area,
  or test via direct URL navigation instead.

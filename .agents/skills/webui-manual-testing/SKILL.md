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

1. **Environment loaded**: `set -a && source test.env && set +a`
2. **Virtualenv active**: `source .venv/bin/activate`
3. **All hosts reachable**: `pytest tests/test_webui_heartbeat.py -k "infrastructure" -v`
   — if any host is unreachable, STOP. Fix reachability first.
4. **Bridge infrastructure deployed**: `molecule converge -s bridge-lxc` —
   batman/WiFi tests need live containers. NEVER manually deploy scripts.
5. **Callhome API running**: Check `.state/callhome_url` exists, or start
   via `python scripts/webui/app.py --headless`

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
2. ALWAYS verify all 6 hosts are reachable before starting manual testing.
   Incomplete infrastructure produces misleading results.
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

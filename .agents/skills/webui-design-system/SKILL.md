---
name: webui-design-system
description: NiceGUI web UI design system, color semantics, CSS patterns, and testing conventions. Use when modifying scripts/webui/ pages, theme.py, or writing test_webui_* tests.
---

# Web UI Design System

## Context

The NiceGUI web UI (scripts/webui/) serves three roles: SuperManager
(fleet deployment), Manager (host management), and Kiosk (app launcher).
A shared theme.py defines the dark design system used across all pages.

## Rules

1. NEVER use red (`COLOR_ERROR`, `t-error`) for non-error states. Red = critical
   error/failure ONLY. "No data," "stopped," "not configured," "no WoL" are NOT errors.
2. ALWAYS use grey (`TEXT_DISABLED`, `t-disabled`) for unknown/no-data/inactive states.
3. ALWAYS use orange (`COLOR_WARNING`, `t-warning`) for limitations, cautions, or
   unconfigured states that need attention but are not failures.
4. NEVER hardcode hex colors in page files. Use theme constants or theme helper
   functions (`signal_color()`, `health_score_color()`).
5. ALWAYS use design system CSS classes (`.action-btn`, `.outline-btn`, `.t-primary`,
   `.mono-val`, `.metric-label`, `.card-title`, `.section-label`, `.sep-accent`)
   instead of inline styles for recurring patterns.
6. NEVER use broad `except Exception` in UI code. Catch specific exceptions
   (`httpx.HTTPError`, `OSError`, `ValueError`).
7. ALWAYS ensure sidebar/nav icons are visually distinct — no two nav items
   should share the same icon.
8. All new data helpers MUST have type hints and tests in `test_webui_data.py`.
8b. NEVER show raw exit codes, return codes, or internal identifiers to users.
    Map them to human-readable descriptions (e.g., exit code 2 → "host
    unreachable or task failed").
9. NEVER hardcode UI strings in tests. Import `Routes`, `PageTitles`, `Labels`,
   `ApiRoutes` from `scripts.webui.data`. Change the constant once, tests follow.
10. ALWAYS use `data.DISPLAY_APPS`, `data.HUB_SERVICES`, `data.NAV_SECTIONS`,
    `data.KIOSK_NAV_ITEMS` in tests instead of repeating labels/routes/vmids.
11. When adding a new page: add its route to `Routes`, title to `PageTitles`,
    and any shared labels to `Labels`. Use these in both the page module and tests.
12. When adding a new button label used across pages, add it to `Labels` class.

## MVC Architecture: Domain Model → Data Layer → UI

The web UI follows a strict Model-View-Controller separation. Business logic
lives in domain objects (`data.py`), and page modules are thin UI renderers
that read properties — never compute state.

### Three-layer rule

1. **Domain model** (`Host`, `Fleet`, `DeployRecord` in `data.py`) —
   owns all state, health computation, and error descriptions. Properties
   like `.healthy`, `.errors`, `.last_deploy` encapsulate the rules.
2. **Factory functions** (`build_fleet()`, `get_known_hosts()` in `data.py`) —
   wire data sources (env vars, JSON files) into domain objects.
3. **UI pages** (`pages/dashboard.py`, etc.) — read domain object properties
   and render. ZERO if/else health logic. ZERO exit code mapping.

### NEVER put business logic in page modules

```python
# BAD: health logic in the UI layer
if last.exit_code == 0:
    ui.badge("success", color="green")
else:
    desc = EXIT_CODES.get(last.exit_code, "unknown")
    ui.badge(f"failed — {desc}", color="red")

# GOOD: UI reads domain object properties
if fleet.healthy:
    ui.badge("success", color="green")
else:
    ui.badge(data.exit_code_label(last.exit_code), color="red")
    for err in fleet.errors:
        ui.label(err)
```

### Domain object design

Domain classes MUST expose:
- `.healthy` → `bool` — single source of truth for health state
- `.errors` → `list[str]` — human-readable error descriptions
- Telemetry properties (`.online`, `.disk_pct`, `.memory_pct`, `.uptime`,
  `.guests`, `.guest_count`, `.version`, etc.) — graceful defaults when
  no heartbeat data is available
- Aggregate objects (e.g., `Fleet`) delegate to children and provide fleet-wide
  metrics (`.online_count`, `.total_guests`, `.avg_disk_pct`, `.health_score`)

#### Host class (identity + deploy + telemetry)

```python
class Host:
    telemetry: HostTelemetry | None  # None = no heartbeat yet

    @property
    def healthy(self) -> bool: ...      # deploy health
    @property
    def errors(self) -> list[str]: ...  # deploy + offline errors
    @property
    def online(self) -> bool: ...       # from telemetry.status
    @property
    def disk_pct(self) -> float: ...    # 0.0 if no telemetry
    @property
    def guests(self) -> list[GuestInfo]: ...  # parsed from services
    @property
    def guest_count(self) -> int: ...
    @property
    def vms(self) -> list[GuestInfo]: ...
    @property
    def containers(self) -> list[GuestInfo]: ...

    def attach_telemetry(self, t: HostTelemetry) -> None:
        """Wire heartbeat data; parses guests once."""
```

#### Fleet class (aggregate)

```python
class Fleet:
    @property
    def healthy(self) -> bool: ...
    @property
    def errors(self) -> list[str]: ...
    @property
    def online_count(self) -> int: ...
    @property
    def total_guests(self) -> int: ...
    @property
    def avg_disk_pct(self) -> float: ...
    @property
    def health_score(self) -> int: ...    # 0-100
    @property
    def worst_disk(self) -> Host | None: ...
    @property
    def has_telemetry(self) -> bool: ...

    def get_host(self, name: str) -> Host | None: ...
```

#### Key design decisions

- `HostTelemetry` is a SEPARATE dataclass — telemetry may be None (no heartbeat)
  and this cleanly separates "configured host" from "observed host"
- `GuestInfo` is parsed ONCE in `attach_telemetry()`, not on every property access
- `Fleet` replaces `FleetHealth` as the primary aggregate — one object, not two
- `build_fleet()` is the SINGLE entry point — reads env, deploy history, AND
  node registry, producing a fully wired Fleet

### Factory pattern: wiring data to domain objects

```python
def build_fleet(env: dict[str, str], state_dir: Path) -> Fleet:
    host_infos = get_known_hosts(env)
    history = load_deploy_history(state_dir)
    nodes = load_node_registry(state_dir)   # heartbeat data
    node_map = {n.hostname: n for n in nodes}

    hosts = []
    for info in host_infos:
        host = Host(name=info.name, ip=info.ip, ...)
        for record in history:
            if _deploy_targets_host(record, host.name):
                host.deploys.append(record)
        node = node_map.get(info.name)
        if node:
            host.attach_telemetry(HostTelemetry(...))  # from RegisteredNode
        hosts.append(host)
    return Fleet(hosts)
```

### Per-host detail page (`/nodes/{hostname}`)

The detail page is a Proxmox VE-inspired single-host view:
- Summary header with status dot, hostname, IP, uptime, version
- Resource gauges (disk, memory) with color thresholds
- Guests table (VMID, name, type, status) from `host.guests`
- Network info from `host.local_ips` and `host.extensions.network`
- Deploy history with pass/fail badges
- Extensions panel (WireGuard, Docker, etc.)
- Resource history sparklines

ALWAYS navigate to `/nodes/{hostname}` when a host card is clicked.

### When to add a new domain class

Add a domain class when:
- A UI page computes state from raw data (exit codes, timestamps, counts)
- The same computation appears in multiple pages
- Tests need to verify business logic without spinning up NiceGUI

Keep as plain functions/dataclasses when:
- The data is pass-through (display only, no derived state)
- There's no `.healthy`/`.errors` pattern to encapsulate

### Testing domain objects

Domain classes are pure Python — test without NiceGUI:

```python
class TestHost:
    def test_recovers_after_success_following_failure(self):
        host = data.Host("home", "192.168.86.201")
        host.deploys.append(make_record(exit_code=2))
        host.deploys.append(make_record(exit_code=0))
        assert host.healthy is True
        assert host.errors == []

class TestHostWithTelemetry:
    def test_online_with_telemetry(self):
        host = data.Host("home", "192.168.86.201")
        host.attach_telemetry(make_telemetry(status="online"))
        assert host.online is True
        assert host.guest_count == 3

class TestFleetAggregates:
    def test_health_score_all_online(self):
        fleet = data.Fleet([make_host_with_telemetry("a"), ...])
        assert fleet.health_score == 100
```

## Color Semantics

```
Green  (COLOR_SUCCESS / t-success) — running, healthy, reachable, ready
Orange (COLOR_WARNING / t-warning) — stale, limitation, caution, unconfigured
Red    (COLOR_ERROR / t-error)     — error, crashed, failed, critical alert
Grey   (TEXT_DISABLED / t-disabled) — offline, unknown, no data, inactive
Grey   (TEXT_SECONDARY / t-secondary) — stopped (intentional), muted info
Blue   (info)                       — informational, first-time setup prompts
```

## Patterns

### Status indicator colors

```python
# BAD: red for "no data"
return ui.icon("circle").classes("t-error")

# GOOD: grey for "no data"
return ui.icon("circle").classes("t-disabled")

# BAD: red for stopped container
if status == "stopped": return theme.COLOR_ERROR

# GOOD: neutral grey for stopped, red only for crashed
if status == "stopped": return theme.TEXT_SECONDARY
if status in ("error", "crashed"): return theme.COLOR_ERROR
```

### Signal strength colors

Use `theme.signal_color(quality)` instead of hardcoding:

```python
# BAD: hardcoded gradient in page
if signal_dbm > -50: sig_color = "#2dd4bf"
elif signal_dbm > -60: sig_color = "#4ade80"

# GOOD: centralized via theme
sig_color = theme.signal_color(signal_quality(signal_dbm))
```

### Scalable test patterns

Tests import constants from `scripts.webui.data` — never hardcode UI strings:

```python
from scripts.webui.data import Routes, PageTitles, Labels, ApiRoutes

# Routes
await user.open(Routes.DEPLOY)  # not "/deploy"
f"{Routes.LAUNCH}?vmid={_moon['vmid']}"  # parameterized routes

# Page titles
await user.should_see(PageTitles.NODES)  # not "Fleet Nodes"

# Button labels
user.find(Labels.START_DEPLOY).click()  # not "Start Deploy"

# API routes
resp = await client.post(ApiRoutes.CHECKIN, json=payload)

# Hub service data — derive from data model
_hub_svc = {s.key: s for s in data.HUB_SERVICES}
await user.should_see(_hub_svc["jellyfin"].tag)  # not "Media Server"

# Display apps — derive vmid/label from data
_moon = data.DISPLAY_APPS["MOONLIGHT_URL"]
f"{Routes.LAUNCH}?vmid={_moon['vmid']}&title={_moon['label']}"

# Hub sections — loop instead of hardcoded list
for section in {s.section for s in data.get_hub_services()}:
    await user.should_see(section)

# Bridge/mesh nodes — from data helpers
_BRIDGE_NODES = data.get_bridge_nodes()
_MESH_AP, _MESH_STAS = data.get_mesh_nodes()
```

### Kiosk nav bar

The kiosk nav bar shows "Home" (icon button) + breadcrumb, NOT "Home Hub".
Tests use `Labels.HOME` — `await user.should_see(Labels.HOME)`.

### Hub sections

Sections are derived from `data.HUB_SERVICES` — never hardcode the list.
Tests iterate `{s.section for s in data.get_hub_services()}`.

## Previous bugs

- Red overuse: status dots, stopped containers, "No WoL" badges, disconnected
  mesh nodes, "No env file" messages all used red. Users interpreted these as
  errors. Fix: systematic audit replacing with grey/orange per semantics above.
- Duplicate icons: Containers and Hosts both used `dns` icon. Fix: Containers
  changed to `view_in_ar`.
- Hardcoded batman color `#4ade80` in mesh.py instead of `theme.COLOR_SUCCESS`.
- Hardcoded signal colors in bridge.py instead of `theme.signal_color()`.
- `except Exception` in containers.py caught too broadly. Fix: catch
  `(httpx.HTTPError, OSError)`.
- `format_last_seen_relative` crashed on timezone-aware ISO timestamps due to
  naive/aware datetime subtraction. Fix: `.replace(tzinfo=None)`.
- Tests referencing old section names ("Desktop & Media") after hub sections
  were renamed to "Entertainment". 26 tests failed silently until full suite run.
  Fix: all tests now import constants from `data.py` (`Routes`, `PageTitles`,
  `Labels`, `ApiRoutes`) so label changes propagate automatically.
- Magic strings in 6 test files duplicated 200+ labels, routes, and page titles.
  Changing a button label required updating 5+ test files. Fix: centralized all
  UI strings into `data.py` constants consumed by both page modules and tests.
- "rc=2" in deploy badges was meaningless to users (mistaken for "release
  candidate"). Fix: map Ansible exit codes to human-readable descriptions
  (e.g., "failed — host unreachable or task failed") via `_exit_code_label()`.
- "monitoring" Material Icon in the fleet View button looked like a desktop
  monitor and overlapped adjacent buttons. Fix: changed to `lan` icon.
- Fleet card showed "No nodes registered" when no callhome server was running,
  giving the impression the hosts didn't exist. Fix: show configured host names
  from env as grey badges with "waiting for heartbeats" message.
- Business logic (exit code mapping, health computation, error aggregation)
  was embedded in `dashboard.py` UI functions. Changing health rules required
  editing UI code and couldn't be unit-tested without NiceGUI. Fix: extracted
  `Host`, `Fleet` domain classes and `build_fleet()` factory into `data.py`.
  Dashboard became a thin renderer reading `.healthy` and `.errors` properties.
  29 pure-Python tests cover all health scenarios without UI overhead.
- `RegisteredNode` (heartbeat data) and `Host` (env config) were separate
  objects representing the same entity. Dashboard read raw `nodes.json` in
  parallel with `Fleet`. Nodes list used `RegisteredNode` directly. Fix: merged
  telemetry into `Host` via `HostTelemetry` dataclass and `attach_telemetry()`.
  `build_fleet()` now loads all three data sources (env, deploy history,
  nodes.json) and produces a single fully-wired `Fleet`. Added `GuestInfo`
  for parsed service entries, `Host.guests`/`.vms`/`.containers` properties,
  and Fleet-level aggregates (`.online_count`, `.total_guests`, `.health_score`).
  50+ new domain model tests cover telemetry, guests, and aggregates.
- Per-host detail page was missing. Users had to scan fleet cards for host
  info. Fix: added `/nodes/{hostname}` route with Proxmox VE-inspired layout
  (resources, guests table, network, deploy history, extensions, sparklines).
  Node list cards are now clickable → detail page.

## Related skills

- **webui-ux-principles** — Higher-level UX design principles (color language,
  icon choices, layout rules, information hierarchy, click reduction).
- **webui-manual-testing** — Step-by-step manual testing checklist for all
  three UI roles (SuperManager, Manager, Kiosk).
- **code-review-checklist** — MVC/OOP review questions for web UI diffs.

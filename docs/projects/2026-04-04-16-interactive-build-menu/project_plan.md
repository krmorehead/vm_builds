# Interactive Build Menu (Web UI)

## Overview

Production builds currently require manually editing `.env`, running
`build.py` with the right flags, and remembering which tags control which
services. There is no guided workflow for first-time setup or day-to-day
operations. This project adds a web-based UI that wraps the existing
`build.py` and `build-images.sh` entry points with an interactive menu
system — environment setup, host connectivity checks, service selection,
image management, fleet node monitoring, kiosk Home Hub, and live
deployment output, all in one place.

**Implementation note:** The original plan targeted Textual (terminal UI).
During implementation the framework was changed to NiceGUI (Python web UI)
for richer widget support, browser-based kiosk compatibility, and the
ability to serve a Home Hub page on LXC kiosk containers.

## Type

Cross-cutting infrastructure (tooling / developer experience)

## Prerequisites

- `build.py` exists and handles env validation, host probing, playbook
  execution (already complete)
- `build-images.sh` with `--only`, `--parallel`, `--hosts` support
  (already complete — see 2026-03-26-15-test-optimization)
- All roles and services deployed and testable via molecule
- Python 3.10+ (already required by the project)

## Skills

| Skill | When to use |
|-------|-------------|
| `build-entry-point` | Integration with build.py functions |
| `python-code-style` | Python conventions, error handling, type hints |
| `build-testing` | Test coverage for new functions |
| `project-structure-rules` | Entry point conventions, env file patterns |
| `secret-generation` | Env file format, generated secrets |

---

## Architectural Decisions

```
Decisions
├── Framework: NiceGUI (Python web UI library)
│   ├── Modern, async-native, Quasar/Vue-based web UI
│   ├── Built on FastAPI + Starlette (REST endpoints for call-home)
│   ├── Supports live updates, log streaming, dark mode
│   ├── Runs in any browser — also serves kiosk Hub on LXC containers
│   └── Changed from original plan: Textual (TUI) was replaced with
│       NiceGUI for richer widgets and kiosk/browser compatibility
│
├── Integration: two models, clearly separated
│   ├── IMPORT model (data operations): data.py imports functions
│   │   from build.py directly (load_env, validate_env, probe_host).
│   │   Requires sys.path insert to resolve build.py at the project root
│   │   from scripts/webui/ package.
│   ├── SUBPROCESS model (execution): deploy and image builds run
│   │   build.py / build-images.sh as child processes. Necessary for:
│   │   output streaming, cancellation via SIGTERM, and isolation.
│   │   Env vars are passed via subprocess env dict (same pattern as
│   │   build.py main() — {**os.environ, **loaded_env}).
│   ├── Web UI is an OPTIONAL frontend — CLI scripts continue to work
│   └── No Ansible logic in the Web UI — it only orchestrates existing tools
│
├── Entry point: scripts/webui/ (package)
│   ├── Structured as scripts/webui/ package with per-page modules
│   ├── Convenience wrapper: scripts/webui.sh (same pattern as run.sh)
│   ├── Kiosk variant: scripts/webui/kiosk_server.py (Hub-only, localhost)
│   ├── build.py stays as the non-interactive CLI entry point
│   └── nicegui added to requirements.txt (setup.sh installs it automatically)
│
├── Pages (one per workflow)
│   ├── Dashboard (home): host/image/deploy/fleet summaries, quick actions
│   ├── Environment: view/edit .env values, validate, create from template
│   ├── Hosts: probe connectivity, SSH test, show host details
│   ├── Nodes: fleet call-home registry, node status, setup instructions
│   ├── Services: select services by tag, view per-host assignments
│   ├── Images: show built/missing images, trigger builds
│   ├── Deploy: run build.py with selected options, stream output
│   └── Home Hub: kiosk service launcher for TV displays
│
├── Call-home system: fleet node registration
│   ├── REST API: POST /api/checkin, GET /api/nodes (Starlette routes)
│   ├── Python client: scripts/callhome.py (stdlib only, cron-friendly)
│   ├── Shell client: scripts/callhome.sh (BusyBox-compatible, curl/wget)
│   ├── IP-change-gated: only contacts server when node IP changes
│   ├── HMAC-SHA256 auth: private key on server, public key on nodes
│   └── Node registry persisted to .state/nodes.json (gitignored)
│
├── Service tag map: hardcoded in data.py, not parsed from YAML
│   ├── Parsing inventory/hosts.yml and site.yml is fragile — structure
│   │   changes break the UI silently
│   ├── Hardcoded map is explicit, testable, and easily audited
│   ├── Trade-off: must update data.py when adding services to site.yml
│   └── Mitigated: "Adding a new service" checklist includes data.py update
│
└── Persistence: minimal state
    ├── Reads .env / test.env (existing pattern)
    ├── Reads .state/addresses.json (existing pattern)
    ├── Reads inventory/host_vars/*.yml for wol_capable (YAML parse)
    ├── NiceGUI storage: app.storage.general (env_path, selected_tags)
    ├── Deploy history written to .state/deploy_history.json (gitignored)
    └── Node registry written to .state/nodes.json (gitignored)
```

---

## Testing Strategy

### Two test tiers

**Tier 1 — Data layer tests** (`tests/test_webui_data.py`): Pure Python
tests for the data functions in `scripts/webui/data.py`. No NiceGUI
imports, no async, no UI. These test env parsing, service tag mapping,
image status, deploy command construction, deploy history, node
registry, call-home crypto, and theme constants. Fast, deterministic,
run on every change.

**Tier 2 — Functional UI tests** (`tests/test_webui_app.py`): Async
tests using NiceGUI's `user_simulation()` API. These render actual
pages, programmatically navigate, click buttons, and assert on real
widget state. They prove the user can actually operate the application.

Both tiers run via `pytest tests/test_webui_data.py tests/test_webui_app.py -v`.

### Dependencies

- `pytest-asyncio` added to `requirements.txt` (required for async
  NiceGUI tests)
- Configure `asyncio_mode = auto` in `pyproject.toml` so async test
  functions don't need per-test markers
- `nicegui` added to `requirements.txt`

### What the UI tests cover

Each milestone adds UI tests that exercise the actual user workflow
for that page. The pattern is:

```python
async def test_environment_shows_missing_vars(self, tmp_path):
    async with webui(tmp_path, env_file="incomplete.env") as user:
        await user.open("/environment")
        await user.should_see("Missing")
```

Tests use fixture env files (`tests/fixtures/`) to control input state,
never touch real `.env` or production hosts. Host probing in UI tests
is mocked at the `build.probe_host` level (this is UI behavior testing,
not infrastructure health testing — `tests/test_build.py` already
covers real host probes).

### Test fixtures

```
tests/
├── fixtures/
│   ├── complete.env       # All required + optional vars filled
│   ├── incomplete.env     # Missing HOME_API_TOKEN, MESH_KEY
│   └── empty.env          # Blank file
├── conftest.py            # NiceGUI storage workarounds
├── test_webui_data.py     # Tier 1: pure data tests
├── test_webui_app.py      # Tier 2: functional UI tests
└── test_webui_hub.py      # Hub page tests
```

### Day-to-day workflow

```bash
# Run all Web UI tests (data + functional)
pytest tests/test_webui_data.py tests/test_webui_app.py -v

# Run only data tests (fast, no async)
pytest tests/test_webui_data.py -v

# Run only UI functional tests
pytest tests/test_webui_app.py -v

# Launch the Web UI interactively (production env)
./scripts/webui.sh

# Launch with test environment
./scripts/webui.sh --env test.env

# CLI still works for automation / CI
python build.py --tags openwrt
./scripts/build-images.sh --parallel
```

### Teardown table

| Action | Files created | Files destroyed | Side effects |
|--------|--------------|----------------|-------------|
| Launch Web UI | .nicegui/ (storage) | None | Reads .env, serves HTTP |
| Deploy via Web UI | .state/deploy_history.json | None | Runs build.py (same as CLI) |
| Edit env via Web UI | .env (modified), .env.bak (backup) | None | User-confirmed writes only |
| Call-home check-in | .state/nodes.json, .state/fleet_ips.txt | None | Updates node registry |
| Run pytest (Tier 1) | None | None | Uses fixture files only |
| Run pytest (Tier 2) | tmp files via pytest tmp_path | Cleaned by pytest | Headless, no browser |

---

## Milestone Dependency Graph

```
M0 (foundation: app shell + data layer + env page) ✓ COMPLETE
├── M1 (host connectivity page) ✓ COMPLETE
├── M2 (service selection page) ✓ COMPLETE
├── M3 (deploy page) ✓ COMPLETE ← depends on M2
├── M4 (image management page) ✓ COMPLETE
├── M5 (dashboard + Home Hub + fleet nodes) ✓ COMPLETE
└── M6 (call-home system) ✓ COMPLETE
```

All milestones complete. Framework changed from Textual to NiceGUI.

---

## Milestones

### Milestone 0: Foundation — App Shell + Data Layer + Environment Page (COMPLETE)

_Self-contained. No external dependencies._

Bootstrapped the NiceGUI application with sidebar navigation, the
environment management page, the shared dark theme, and the core data
layer that loads `.env` / `test.env`.

**Delivered:**

```
scripts/
├── webui.sh               # Convenience wrapper
└── webui/
    ├── __init__.py
    ├── __main__.py         # Entry point (python -m scripts.webui)
    ├── app.py              # Page registration, REST API, CLI args
    ├── data.py             # Pure data functions (no NiceGUI imports)
    ├── theme.py            # Dark theme constants + CSS generation
    ├── components.py       # Shared interactive UI widgets
    ├── run_process.py      # Async subprocess runner
    ├── kiosk_server.py     # Minimal Hub server for LXC kiosks
    └── pages/
        ├── __init__.py
        ├── dashboard.py
        ├── environment.py
        ├── hosts.py
        ├── nodes.py
        ├── services.py
        ├── deploy.py
        ├── images.py
        └── hub.py
```

- [x] Add `nicegui` and `pytest-asyncio` to `requirements.txt`
- [x] Add `asyncio_mode = "auto"` to `pyproject.toml`
- [x] Create test fixtures (`tests/fixtures/complete.env`,
  `incomplete.env`, `empty.env`)
- [x] Create `scripts/webui/` package with `__init__.py`, `__main__.py`
- [x] Create `scripts/webui/data.py` — pure data layer with
  `load_environment`, `get_env_template`, `save_environment`,
  `EnvVar`/`EnvResult` dataclasses
- [x] Create `scripts/webui/app.py` — page registration, `configure()`,
  `register_pages()`, `register_api()`, CLI argument parsing
- [x] Create `scripts/webui/theme.py` — dark theme constants,
  generated CSS, reusable UI components (`page_header`, `nav_sidebar`,
  `section_label`, `card_title`, `status_text`)
- [x] Create `scripts/webui/pages/environment.py` — env table with
  inline editing, validate, save, create from template
- [x] Create `scripts/webui.sh` wrapper
- [x] Tier 1 data tests in `tests/test_webui_data.py`
- [x] Tier 2 UI tests in `tests/test_webui_app.py`

**Verify:** `pytest tests/test_webui_data.py tests/test_webui_app.py -v` — all pass

**Rollback:** Remove `scripts/webui/`, `scripts/webui.sh`,
`tests/test_webui_data.py`, `tests/test_webui_app.py`. Remove
`nicegui` from `requirements.txt`.

---

### Milestone 1: Host Connectivity Page (COMPLETE)

- [x] `get_known_hosts()`, `probe_all_hosts()`, `test_ssh_connection()`
  in `data.py`
- [x] `scripts/webui/pages/hosts.py` — table with probe/SSH buttons
- [x] WoL warnings, LAN host notes, latency display
- [x] Data + UI tests

---

### Milestone 2: Service Selection Page (COMPLETE)

- [x] `SERVICE_TAGS` and `DEPLOY_PROFILES` in `data.py`
- [x] `scripts/webui/pages/services.py` — checkboxes by category,
  profile dropdown, select/deselect all, deploy navigation
- [x] Data + UI tests

---

### Milestone 3: Deploy Page (COMPLETE)

- [x] `build_deploy_command()`, deploy history in `data.py`
- [x] `scripts/webui/run_process.py` — async subprocess with streaming
- [x] `scripts/webui/pages/deploy.py` — live output, cancel, history
- [x] Data + UI tests

---

### Milestone 4: Image Management Page (COMPLETE)

- [x] `get_image_status()`, `build_image_command()` in `data.py`
- [x] `scripts/webui/pages/images.py` — table, quick build, build all
- [x] Data + UI tests

---

### Milestone 5: Dashboard + Home Hub + Fleet Nodes (COMPLETE)

- [x] `scripts/webui/pages/dashboard.py` — env badge, host/image/deploy
  summaries, fleet card, recent history, quick actions
- [x] `scripts/webui/pages/hub.py` — kiosk service launcher with
  section-grouped cards, disabled state for unconfigured services
- [x] `scripts/webui/pages/nodes.py` — fleet node registry, status
  table, setup instructions, SSH test
- [x] `scripts/webui/kiosk_server.py` — minimal Hub-only server for
  LXC kiosks (localhost binding)
- [x] Dark theme with reverse-vignette gradient, teal accents, floating
  cards, hover effects
- [x] Data + UI tests

---

### Milestone 6: Call-Home Fleet System (COMPLETE)

- [x] `scripts/callhome.py` — Python call-home client (stdlib only,
  IP-change-gated, cron-friendly, HMAC auth)
- [x] `scripts/callhome.sh` — BusyBox/OpenWrt shell client (curl/wget)
- [x] REST API: `POST /api/checkin`, `GET /api/nodes` in `app.py`
- [x] Node registry with online/stale/offline status computation
- [x] Fleet IP text file for easy consumption
- [x] HMAC-SHA256 key generation and validation
- [x] Data + client tests

---

## Future Integration Considerations

- **New services:** When a new service is added to `site.yml`, add its
  tag to `SERVICE_TAGS` in `scripts/webui/data.py`. The Web UI picks it
  up immediately. This step is included in the "Adding a new LXC service"
  and "Adding a new VM type" checklists.
- **Molecule integration:** A future milestone could add a "Testing"
  page that runs molecule scenarios with live output — same subprocess
  streaming pattern as the deploy page.
- **Remote host management:** Could add WoL buttons for WoL-capable hosts
  (using `scripts/wol.sh`), power status indicators, and reboot controls.
- **Multi-unit management:** If the project expands to manage multiple
  independent Proxmox clusters, the Web UI could support switching between
  `.env` profiles via an env file selector.
- **Cleanup/Rollback page:** A dedicated page for `scripts/cleanup.sh`
  operations with tag selection (restore, full-restore, clean, rollback).
- **Generated env viewer:** The environment page could add a read-only
  tab showing values from `.env.generated` (auto-generated keys,
  LAN gateway) alongside the editable `.env` values.

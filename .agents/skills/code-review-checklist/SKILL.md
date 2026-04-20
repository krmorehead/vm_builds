---
name: code-review-checklist
description: Code review checklist for vm_builds web UI and Ansible code. Use when reviewing git diffs, auditing code quality, or doing final passes before committing.
---

# Code Review Checklist

Use when reviewing code changes via `git diff`, doing quality audits, or
performing final review passes before committing.

## Rules

1. ALWAYS check for MVC separation violations (business logic in UI pages).
2. ALWAYS verify new domain state is exposed via `.healthy`/`.errors` properties.
3. NEVER approve raw exit codes, return codes, or internal IDs shown to users.
4. NEVER approve broad `except Exception` — require specific exception types.
5. ALWAYS verify new UI strings are in `data.py` constants, not hardcoded.
6. ALWAYS check that new data helpers have tests in `test_webui_data.py`.
7. NEVER approve hardcoded hex colors in page files — use theme constants.
8. NEVER read raw `RegisteredNode` in page modules — use `Fleet`/`Host` from
   `build_fleet()`. The only exception is `compute_alerts()` which still takes
   `RegisteredNode` list (pending migration).
9. ALWAYS verify `Host` telemetry properties return graceful defaults when
   `telemetry is None` (e.g., `disk_pct → 0.0`, `guests → []`, `online → False`).

## MVC / OOP Architecture Review

The web UI follows a strict three-layer separation. Every review MUST check:

### Layer 1: Domain model (`data.py`)

- Does the change add state computation? It MUST be in a domain class, not
  a page module.
- Does a domain class expose `.healthy` and `.errors`? These are the standard
  interface for health/status.
- Are aggregate classes (e.g., `Fleet`) delegating to children?
  `fleet.healthy = all(h.healthy for h in self.hosts)` — not reimplementing.
- Are factory functions (`build_fleet()`) the only place raw data sources
  (JSON, env vars) are wired to domain objects?
- Does the change use `RegisteredNode` directly? It should use `Host` with
  attached `HostTelemetry` instead. `build_fleet()` merges both.
- Does a new `Host` property handle `telemetry is None` gracefully?

```python
# RED FLAG: health logic in a page module
def _deploy_card(state_dir):
    history = data.load_deploy_history(state_dir)
    if history[-1].exit_code == 0:  # ← business logic in UI
        ui.badge("success")

# CORRECT: page reads domain object
def _deploy_card(fleet):
    if fleet.healthy:  # ← domain object owns the logic
        ui.badge("success")
```

### Layer 2: Data helpers (`data.py`)

- Exit code mapping, status computation, error descriptions — all in `data.py`.
- Lookup tables (e.g., `ANSIBLE_EXIT_CODES`) live in `data.py`, not page modules.
- Functions that format data for display (e.g., `exit_code_label()`) are in
  `data.py` — the UI calls them, not reimplements them.

### Layer 3: UI pages (`pages/*.py`)

- Page functions receive domain objects, not raw data.
- ZERO `if exit_code == 0` or status-computing conditionals.
- Color decisions reference theme constants, never hardcoded values.
- Exception handling uses specific types (`httpx.HTTPError`, `OSError`).

### Review question checklist

Ask these for every diff that touches `scripts/webui/`:

1. **"Is there business logic in a page module?"** — if/else on exit codes,
   status strings, deploy history → move to domain class in `data.py`.
2. **"Could I test this logic without NiceGUI?"** — if no, the logic is in
   the wrong layer. Domain logic must be testable with plain pytest.
3. **"Does the UI show raw internal values?"** — exit codes, return codes,
   enum values, timestamps without formatting → map to human-readable text.
4. **"Is state derived from a `.healthy`/`.errors` property?"** — if the page
   manually iterates/filters/computes, it should be a domain object method.
5. **"Are new strings in `data.py` constants?"** — button labels, page titles,
   route paths → `Labels`, `PageTitles`, `Routes` classes.
6. **"Are colors semantic?"** — red=error only, orange=warning/limitation,
   grey=inactive/unknown, green=healthy/success.

## Ansible Code Review

### Safety checks

- `modprobe -r` NEVER in broad-scope plays (`hosts: proxmox`).
- No `shutdown`/`poweroff` in cleanup or molecule files.
- No `authorized_keys` or `pveum` removal in cleanup.
- `set -o pipefail` on all `ansible.builtin.shell` tasks with `|`.
- `executable: /bin/bash` on pipefail tasks.
- FQCN on all modules (`ansible.builtin.command`, not `command`).
- `changed_when` / `failed_when` on command/shell tasks.
- `delegate_to: localhost` instead of deprecated `local_action`.

### Architecture checks

- Configure roles have ZERO package installs.
- Configure roles use `ansible.builtin.uri` → NM API (not SSH/pct exec).
  Only `kiosk_configure` (bootstrap) and `openwrt_configure` (no HTTP API) are exceptions.
- Verify assertions use fleet API (`/api/fleet/ready`, `/api/container/{id}/ready`)
  over VPN. No SSH fallback paths.
- Hard-failure messages include INVESTIGATE prompts with diagnostic commands.
- Image paths use `role_path`, not bare relative paths.
- New VMIDs added to both cleanup files.
- Deploy stamp included in provision plays.
- Container IPs don't collide with existing allocations.

## Test Review

- New domain classes have pure-Python tests (no NiceGUI required).
- UI tests import constants from `data.py`, never hardcode strings.
- Tests verify actual behavior, not just string presence in YAML.
- No mocking of things that can be tested for real (infrastructure probes).
- Recovery scenarios tested: failure → success → `.healthy is True`.
- Telemetry properties tested with AND without `attach_telemetry()` to verify
  graceful defaults (0.0, [], False, "--", "unknown").
- Fleet aggregates tested: `.online_count`, `.total_guests`, `.avg_disk_pct`,
  `.health_score`, `.worst_disk`, `.worst_memory`.
- `build_fleet()` integration tested: nodes.json wiring, unmatched nodes,
  missing nodes.json.

## Previous bugs caught by review

- Exit code `2` displayed as "rc=2" — users thought it meant "release
  candidate 2". Fix: `exit_code_label()` in `data.py`.
- Health computation in `dashboard.py` — couldn't test without NiceGUI.
  Fix: `Host`/`Fleet` domain classes with `.healthy`/`.errors`.
- `except Exception` in 6 page modules — masked real errors.
  Fix: `except (httpx.HTTPError, OSError)`.
- Red badge for "No WoL" — users thought it was an error.
  Fix: orange (warning/limitation, not failure).
- 200+ magic strings across test files — label change broke 26 tests.
  Fix: `Routes`, `PageTitles`, `Labels` constants in `data.py`.
- `RegisteredNode` (heartbeat data) and `Host` (env config) were separate
  parallel objects. Dashboard read raw `nodes.json` alongside `Fleet`. Fix:
  merged via `HostTelemetry` dataclass wired by `build_fleet()`. Pages now
  use `Fleet` exclusively for all host data.
- Nodes page had no clickable detail view — users had to scan summary cards.
  Fix: added `/nodes/{hostname}` detail page with resources, guests table,
  network info, deploy history, extensions, and sparklines.

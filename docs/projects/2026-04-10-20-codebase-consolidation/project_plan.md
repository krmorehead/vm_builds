# Codebase Consolidation & OOP Refactoring

## Status: PLANNING

## Overview

After the 4-tier event-driven architecture, OOP domain model, and versioned
image build system were introduced, the codebase accumulated stale code, DRY
violations, inconsistent patterns, and documentation drift. This project
systematically consolidates the codebase following DRY, OOP, KISS, and Factory
pattern principles.

This is a pure refactoring project — no new features, no infrastructure changes,
no intentional functional or network behavior changes. Ansible reporting may
differ (handler renames, `changed_when` fixes) but deployed state and service
behavior are identical before and after each milestone.

## Type

Cross-cutting codebase consolidation (Python + Shell + Ansible + Docs)

## Prerequisites

- Codebase review completed: `docs/codebase-review-2026-04-10.md`
- All current tests passing (`pytest tests/ -v` and `molecule test`)
- No in-flight feature projects that would create merge conflicts

**Note:** This is a consolidation/refactoring project. M0 establishes test
infrastructure (not an image build milestone). No new services, containers,
or VMs are created — only existing code is reorganized.

## Skills

| Skill | When to use |
|---|---|
| `code-review-checklist` | MVC separation, OOP architecture, test quality review |
| `python-code-style` | Domain model classes, factory functions, type hints |
| `webui-design-system` | UI page patterns, theme constants, test conventions |
| `manager-api-pattern` | 4-tier hierarchy, manager class structure, endpoint conventions |
| `testing-workflow` | TDD, molecule patterns, integration vs unit test separation |
| `ansible-shell-safety` | pipefail, shell escaping, FQCN requirements |
| `webui-ux-principles` | Color semantics, icon consistency, information hierarchy |
| `build-conventions` | build.py patterns, entry point delegation |

---

## Architectural Decisions

```
Codebase consolidation
├── Why a phased approach (not big-bang)?
│   └── Risk management
│       ├── Each milestone is independently testable and revertible
│       ├── Test infrastructure comes first (M0) — everything after has a safety net
│       └── Documentation last (M7) — code truth established before docs updated
├── Why Python OOP before Ansible DRY?
│   └── Python changes have immediate test coverage
│       ├── pytest catches regressions within seconds
│       ├── Ansible changes require 4-5 min molecule runs
│       └── Python OOP patterns inform Ansible shared task design
├── Why test infrastructure first (M0)?
│   └── Every subsequent milestone needs reliable test feedback
│       ├── Integration markers prevent flaky CI
│       ├── Shared factories reduce per-milestone test boilerplate
│       └── WebuiTestHarness eliminates duplicated NiceGUI context setup
├── Why deduplicate shell scripts before build-images.sh refactor?
│   └── Single-source scripts are a prerequisite for build helper extraction
│       ├── callhome.sh and batman_trigger.sh are copied into 2 image trees
│       ├── Helpers reference these scripts — must be canonical first
│       └── Copy-at-build-time is simpler than symlinks in image builder
└── Why skills/docs last?
    └── Code is the source of truth
        ├── Updating docs before code creates double drift
        ├── Skills/rules reference code patterns — code must be stable first
        └── ProxyCommand vs ProxyJump can be batch-fixed in one pass
```

---

## Milestone Dependency Graph

```
M0 (test infrastructure)
├── M1a (Python OOP: ApiClient + MetricPageController) ← depends on M0
│   └── M1b (Python cleanup: exception hardening + small fixes) ← depends on M1a
│       └── M2 (Python DRY: data.py & heartbeat.py consolidation) ← depends on M1b
│           └── M3 (test suite cleanup & split) ← depends on M2
├── M4 (shell script deduplication) ← depends on M0
│   └── M5 (build-images.sh helper extraction) ← depends on M4
├── M6 (Ansible safety & DRY) ← depends on M0
└── M7 (skills, rules & docs alignment) ← depends on M1b, M2, M3, M4, M5, M6
```

**Serialization rationale:** M2 and M3 both touch `test_webui_data.py` and its
imports. Running them in parallel risks merge conflicts. M3 (test split) runs
after M2 (data consolidation) so the split files reflect the consolidated API.
M1 is split into M1a (OOP patterns) and M1b (exception fixes) to keep each
milestone under 10 tasks.

---

## Testing Strategy

### Parallelism

- **M0-M3** (Python): `pytest tests/ -v` after every change (~10s). M1a/M1b/M2/M3 are serialized (no parallel work)
- **M4-M5** (Shell): `./scripts/build-images.sh --only mesh --dry-run` for syntax; full build for integration
- **M6** (Ansible): `molecule test` after all changes (~4-5 min); `ansible-lint && yamllint .` for each file
- **M7** (Docs): No runtime tests; manual review of all updated files

### Per-milestone test workflow

```bash
# M0-M3: fast Python iteration
pytest tests/ -v                          # full suite
pytest tests/test_webui_data.py -v        # focused module
pytest tests/ -m "not integration" -v     # unit only (after M0)

# M4-M5: shell validation
bash -n scripts/build-images.sh           # syntax check
shellcheck scripts/build-images.sh        # lint (if installed)
./scripts/build-images.sh --only mesh     # single image smoke test (M5)

# M6: Ansible validation
ansible-lint roles/ playbooks/ tasks/     # lint
yamllint .                                # yaml format
molecule converge && molecule verify      # fast iteration (preserves baseline)
molecule test                             # full E2E (clean-state validation)

# M7: documentation consistency
grep -r "ProxyJump" docs/ .agents/ .cursor/  # find stale references
```

**Day-to-day workflow for M6:** Use `molecule converge` + `molecule verify` for
iterative changes (preserves OpenWrt baseline, ~2 min). Run full `molecule test`
(~4-5 min) only after all M6 tasks are complete for clean-state validation.

**Per-feature scenarios:** This project uses `molecule/default` (E2E) only — no
per-feature scenarios are created. The refactoring changes code organization, not
service behavior, so existing E2E coverage is sufficient.

### Estimated effort

| Milestone | Estimated time | Primary risk |
|-----------|---------------|--------------|
| M0 | 1-2 hours | NiceGUI test harness API design |
| M1a | 2-3 hours | MetricPageController abstraction — subscription model may differ per page |
| M1b | 1 hour | Low risk — targeted fixes |
| M2 | 2-3 hours | Protocol type for health score — must fit both Fleet and standalone callers |
| M3 | 2-3 hours | Ensuring 100% test coverage after split — no test regressions |
| M4 | 1-2 hours | build-images.sh copy timing — shared scripts must be copied before image tree is consumed |
| M5 | 2-3 hours | Helper parameterization — service-specific steps must remain inline |
| M6 | 3-4 hours | Highest blast radius — site.yml/cleanup.yml structural changes |
| M7 | 2-3 hours | Volume — ~25 files to update, accuracy critical |
| **Total** | **16-24 hours** | |

### Teardown table

| Milestone | Creates | Destroys | Baseline impact |
|-----------|---------|----------|-----------------|
| M0 | `tests/factories.py`, `tests/webui_helpers.py`, pytest markers | None | None — additive only |
| M1a | `scripts/webui/api_client.py` | Inline httpx in 4 page modules | None — same HTTP calls |
| M1b | Narrowed exception handlers | Broad `except Exception` | None — same error paths |
| M2 | `scripts/netutil.py`, unified functions in `data.py`, `heartbeat.py` | Duplicate functions, hardcoded IPs | None — same behavior |
| M3 | Split test files, shared fixtures | `test_webui_data.py` monolith | None — same test coverage |
| M4 | `scripts/image-builder/shared/` | Duplicate `.sh` files in image trees | None — same baked scripts |
| M5 | Helper functions in `build-images.sh` | Duplicate code blocks | None — same images built |
| M6 | `tasks/infra_pre_tasks.yml`, `tasks/cleanup_host_infra.yml`, `tasks/configure_openwrt_lxc_callhome.yml` | Duplicate blocks in site.yml, cleanup.yml; dead task files | None — same Ansible behavior |
| M7 | Updated `.md` files | Stale content | None — documentation only |

---

### Milestone 0: Test Infrastructure Foundation
_Self-contained._

Establish shared test infrastructure before any refactoring begins. This
gives every subsequent milestone a reliable safety net.

See: `testing-workflow` skill, `code-review-checklist` skill.

**Implementation pattern:**
- New files: `tests/factories.py`, `tests/webui_helpers.py`
- Modified: `tests/conftest.py`, `pyproject.toml`
- No Ansible or molecule changes

- [ ] Register `integration` and `unit` pytest markers in `pyproject.toml`
- [ ] Add `@pytest.mark.integration` to all environment-dependent tests:
  - `test_build.py::TestInfrastructureHealth` (probes real hosts)
  - `test_webui_data.py::test_ssh_success`, `test_ssh_failure` (real SSH)
  - `test_webui_app.py::test_probe_updates_status` (40s sleep, real probe)
  - `test_webui_app.py::TestBatmanApi`, `TestBridgeActionApi`, `TestWifiModeApi` (real SSH side effects)
  - `test_webui_heartbeat.py::TestRealSSH`, `TestRealWifiCollectors` (deployed containers)
- [ ] Create `tests/factories.py` with shared test data constructors:
  - `make_host(name, ip, **overrides) -> data.Host`
  - `make_telemetry(status, disk_pct, memory_pct, **overrides) -> data.HostTelemetry`
  - `make_deploy_record(exit_code, **overrides) -> data.DeployRecord`
  - `make_fleet(hosts) -> data.Fleet`
  - `make_checkin_payload(hostname, **overrides) -> dict`
  - `make_env_content(**vars) -> str`
- [ ] Create `tests/webui_helpers.py` with shared NiceGUI test harness:
  - `WebuiTestHarness` class with `async run(pages, storage)` context manager
  - Replaces duplicated `webui()`, `infra_ctx`, `viewer_ctx`, `launch_ctx`, `kiosk_ctx`
- [ ] Migrate `tests/conftest.py` from `tempfile.mkdtemp` to `tmp_path_factory` session fixture
- [ ] Replace token-like strings in `tests/fixtures/complete.env` with obvious placeholders

**Verify:**
- [ ] `pytest --co -m integration` lists only environment-dependent tests
- [ ] `pytest --co -m "not integration"` lists all unit tests
- [ ] `tests/factories.py` importable from any test module
- [ ] `tests/webui_helpers.py::WebuiTestHarness` usable as async context manager

**Rollback:**
Delete `tests/factories.py`, `tests/webui_helpers.py`. Revert `pyproject.toml`
marker registration. Revert `tests/conftest.py` fixture changes. Revert
`tests/fixtures/complete.env` placeholder changes. `git checkout` all test
files to remove `@pytest.mark.integration` decorators.

---

### Milestone 1a: Python OOP — ApiClient & MetricPageController
_Depends on M0._

Introduce the two highest-impact OOP patterns: a shared HTTP client service and
a base class for metric-polling pages. These eliminate boilerplate in 5+ page modules.

See: `python-code-style` skill, `webui-design-system` skill, `manager-api-pattern` skill.

**Implementation pattern:**
- New files: `scripts/webui/api_client.py`
- Modified: `scripts/webui/pages/bridge.py`, `mesh.py`, `router.py`, `containers.py`, `launch.py`
- Modified: `scripts/webui/manager.py`, `scripts/webui/heartbeat.py`
- Tests: add unit tests for new modules

- [ ] Create `scripts/webui/api_client.py` — `ApiClient` service class:
  - `__init__(self, base_url: str, timeout: float = 5.0)`
  - `async get(self, path: str, **kwargs) -> httpx.Response`
  - `async post(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response`
  - Module-level `get_client() -> ApiClient` factory using `get_api_base_url()`
  - Specific exception handling: `httpx.HTTPError`, `OSError` (not broad `except Exception`)
- [ ] Replace raw `httpx.AsyncClient` usage in page modules with `ApiClient`:
  - `pages/bridge.py` — WiFi restart, batman toggle API calls
  - `pages/mesh.py` — batman status, WiFi mode switch calls
  - `pages/containers.py` — guest list fetch
  - `pages/launch.py` — display launch fire-and-forget
  - `pages/router.py` — router status queries (if applicable)
- [ ] Extract `MetricPageController` base class in `scripts/webui/api_client.py` (co-located with `ApiClient`):
  - Shared logic: subscribe to metric types, set up `ui.timer`, refresh handler
  - Used by: `bridge.py`, `mesh.py`, `router.py`
  - Subclasses override `render(self, cache: dict) -> None`
- [ ] Extract `heartbeat.parse_guest_list(stdout: str) -> list[dict]`:
  - Single parser for `pct list` / `qm list` output
  - Used by: `manager.py::_collect_host_metrics` and `manager.py::_api_guests`
- [ ] Remove unused `page_name` parameter from `theme.kiosk_page_shell()`
- [ ] Add nav entry for Fleet in `data.py::NAV_SECTIONS` or align `cluster_dashboard.py` `active` slug

**Verify:**
- [ ] `pytest tests/ -v` passes — all existing behavior preserved
- [ ] `pytest tests/test_webui_app.py -v -k "api_client or ApiClient"` — new client tests pass
- [ ] No `async with httpx.AsyncClient` remains in page modules (grep verification)
- [ ] `from scripts.webui.api_client import ApiClient, get_client` works

**Rollback:**
Delete `scripts/webui/api_client.py`. `git checkout` all modified page modules
and `manager.py` / `heartbeat.py`. Revert `theme.py` and `data.py` changes.

---

### Milestone 1b: Python Cleanup — Exception Hardening & Small Fixes
_Depends on M1a._

Narrow broad exception handling across the Python codebase. Fix small dead code
and encapsulation issues. These are targeted fixes that don't require new classes.

See: `python-code-style` skill, `code-review-checklist` skill.

**Implementation pattern:**
- Modified: `scripts/webui/run_process.py`, `scripts/webui/pages/launch.py`, `scripts/webui/pages/containers.py`
- Modified: `build.py`, `scripts/callhome.py`
- No new files

- [ ] Fix `run_process.py` — catch `(OSError, asyncio.CancelledError)` instead of `except Exception`
- [ ] Fix `pages/launch.py` — distinguish "API unreachable" from "request sent" in return value
- [ ] Fix `pages/containers.py` — log at warning level instead of silent `except + pass`
- [ ] Fix `build.py` ~587 — catch `(URLError, HTTPError, TimeoutError)` instead of bare `except Exception: pass`
- [ ] Fix `build.py` ~578 — simplify `if api_proc and not args.no_api:` to `if api_proc:` (when `--no-api` is set, `api_proc` is never created, so the inner check is always true when `api_proc` exists)
- [ ] Fix `callhome.py` ~587 — narrow to `(OSError, ValueError)` in run_loop
- [ ] Expose `data.merge_cluster_child_containers()` as public API (replace `app.py` calling `data._save_node_registry`)

**Verify:**
- [ ] `pytest tests/ -v` passes — all existing behavior preserved
- [ ] `grep -rn "except Exception" scripts/webui/run_process.py scripts/webui/pages/launch.py scripts/webui/pages/containers.py` — zero matches
- [ ] `grep -c "except Exception" build.py` — zero matches (check ~587 AND ~609 timeline POST areas)
- [ ] `grep -c "except Exception" scripts/callhome.py` — zero matches (check ~577 AND ~587)
- [ ] `grep -rn "_save_node_registry" scripts/webui/app.py` — zero private calls

**Rollback:**
`git checkout` all modified files.

---

### Milestone 2: Python DRY — Data & Heartbeat Consolidation
_Depends on M1b._

Consolidate duplicated logic in `data.py` and `heartbeat.py`. These are the
data-layer deduplication that the OOP foundations from M1 enable.

See: `python-code-style` skill, `manager-api-pattern` skill.

**Implementation pattern:**
- New file: `scripts/netutil.py` (shared IP discovery helper)
- Modified: `scripts/webui/data.py`, `scripts/webui/heartbeat.py`, `scripts/webui/manager.py`
- Modified: `scripts/webui/pages/cluster_dashboard.py`, `build.py`, `scripts/callhome.py`

- [ ] Unify health score computation:
  - Extract `compute_health_score(nodes: Iterable[HasHealthMetrics]) -> int` using Protocol
  - Replace `Fleet.health_score` property to delegate to this function
  - Replace `_compute_health_score` standalone to use same function
  - Define `HasHealthMetrics` protocol: `status: str`, `disk_pct: float`, `memory_pct: float`
- [ ] Unify uptime formatting:
  - Consolidate `cluster_dashboard._fmt_uptime` and `data.format_uptime` into single function
  - `data.format_uptime(seconds: float, style: str = "long") -> str` with `"short"` for fleet
- [ ] Consolidate WiFi role inference:
  - Extract `heartbeat._infer_wifi_role(script_status: dict, interfaces: list) -> str`
  - Used by: `collect_bridge_metrics`, `collect_mesh_metrics`
- [ ] Consolidate VMID constants:
  - Move `_BRIDGE_CT_ID = 104` from `manager.py` to `data.py` next to other VMID documentation
  - Replace hardcoded `pct exec 104` in `heartbeat.py` with `data.BRIDGE_CT_ID`
- [ ] Consolidate host discovery:
  - Single source for mesh1 LAN IP — derive from `HostRegistry.seed_from_env` or `get_known_hosts`
  - Remove hardcoded `"10.10.10.210"` duplicate in `data.py::get_known_hosts`
- [ ] Consolidate IP discovery:
  - Create `scripts/netutil.py` with `get_primary_ip() -> str` (UDP connect pattern)
  - Replace duplicated pattern in `build.py::get_controller_ip` and `callhome.py::get_primary_ip`
- [ ] Document `load_kiosk_config` fallback chain as canonical pattern (add comment, not remove)

**Verify:**
- [ ] `pytest tests/ -v` passes — all existing behavior preserved
- [ ] `pytest tests/test_webui_data.py -v -k "health_score"` — unified scoring tests pass (pre-M3 path; M3 moves these to `test_webui_data_fleet.py`)
- [ ] `grep -r "_compute_health_score" scripts/` returns zero standalone implementations
- [ ] `grep -r "10.10.10.210" scripts/webui/data.py` returns zero hardcoded occurrences

**Rollback:**
`git checkout` all modified files. Delete `scripts/netutil.py`.

---

### Milestone 3: Test Suite Cleanup & Split
_Depends on M2._ (Serialized after M2 to avoid merge conflicts on shared test files.)

Split the monolithic `test_webui_data.py` (4167 lines) into focused modules.
Migrate all test files to use shared factories from M0 and harness from M0.

See: `testing-workflow` skill, `code-review-checklist` skill.

**Implementation pattern:**
- New files: `tests/test_webui_data_env.py`, `tests/test_webui_data_fleet.py`, `tests/test_callhome.py`
- Modified: all `test_webui_*.py` files
- Deleted: `test_webui_data.py` (replaced by split modules)

- [ ] Split `test_webui_data.py` into focused modules:
  - `test_webui_data_env.py` — env loading, `get_known_hosts`, `probe_all_hosts`, `SERVICE_TAGS`
  - `test_webui_data_fleet.py` — `Host`, `Fleet`, `HostTelemetry`, `GuestInfo`, `build_fleet`, `DeployRecord`
  - `test_callhome.py` — `register_checkin`, `HostRegistry`, `compute_alerts`, node registry
  - Delete `test_webui_data.py` after confirming split modules cover 100% of original tests
- [ ] Migrate all test files to use `tests/factories.py`:
  - Replace local `_make_host`, `_make_telemetry`, `_make_record` helpers
  - Replace inline `HostTelemetry(...)` / `DeployRecord(...)` construction
  - Replace repeated `env_content = f"PRIMARY_HOST=..."` with `factories.make_env_content()`
- [ ] Migrate all NiceGUI test files to use `tests/webui_helpers.py::WebuiTestHarness`:
  - `test_webui_app.py` — replace `webui()` context manager
  - `test_webui_bridge.py` — replace `infra_ctx`, `viewer_ctx`, `launch_ctx`
  - `test_webui_hub.py` — replace `hub_ctx`
  - `test_webui_kiosk_server.py` — replace `kiosk_ctx`
- [ ] Fix misleading test names:
  - `test_batman_hmac_token_matches_openssl` → `test_batman_hmac_token_format`
  - `test_service_tags_match_build_py_docstring` → derive from `SERVICE_TAGS` constant
- [ ] Fix stale magic numbers:
  - `test_webui_app.py` `"10/14 built"` → compute from `len(EXPECTED_IMAGES)`
  - `test_webui_hub.py` `len == 15` → derive from `data.get_hub_services()`
- [ ] Remove redundant test: `test_wol.py::test_ai_not_in_wol_script` (covered by loop)
- [ ] Add missing `collect_router_metrics` test or remove unused import in `test_webui_heartbeat.py`
- [ ] Remove `sys.path.insert` from all test files — rely on `pyproject.toml` `pythonpath`
- [ ] Strengthen weak tests:
  - `test_build.py::test_rollback_tag_naming_convention` — cross-check tag exists in `site.yml`
  - `test_build.py::test_returns_ip_string` — assert IPv4 format (regex or `ipaddress.ip_address`)
- [ ] Add mock-based unit path for `test_probe_updates_status` (keep integration version marked)

**Verify:**
- [ ] `pytest tests/ -v` passes — total test count equals or exceeds pre-split count
- [ ] `pytest tests/test_webui_data_fleet.py -v` runs fleet domain model tests
- [ ] `pytest tests/test_webui_data_env.py -v` runs env/host discovery tests
- [ ] `pytest tests/test_callhome.py -v` runs callhome/registry tests
- [ ] `grep -r "sys.path.insert" tests/` returns zero matches
- [ ] `grep -r "def _make_host\|def _make_telemetry\|def _make_record" tests/` — only in `factories.py`

**Rollback:**
Delete new test files. Restore `test_webui_data.py` from git. Revert all
test file modifications. Revert `pyproject.toml` pythonpath if changed.

---

### Milestone 4: Shell Script Deduplication
_Depends on M0._

Eliminate byte-identical shell script copies. Establish single-source pattern
for scripts baked into multiple image trees.

See: `build-conventions` skill, `openwrt-image-builder` skill.

**Implementation pattern:**
- New directory: `scripts/image-builder/shared/`
- Modified: `scripts/build-images.sh` (copy step during build)
- Deleted: duplicate copies in image tree directories

- [ ] Create `scripts/image-builder/shared/` directory for canonical scripts
- [ ] Move `scripts/callhome.sh` → `scripts/image-builder/shared/callhome.sh`:
  - Delete `scripts/image-builder/files-mesh-lxc/usr/sbin/callhome.sh`
  - Update `build-images.sh` to copy from `shared/` into both image trees before build
  - Keep `scripts/callhome.sh` as a copy of `shared/callhome.sh` (for direct testing on the controller; symlinks break when the image-builder tree is not present)
- [ ] Canonicalize `batman_trigger.sh`:
  - Move to `scripts/image-builder/shared/batman_trigger.sh`
  - Delete `scripts/image-builder/files-router-vm/usr/sbin/batman_trigger.sh`
  - Delete `scripts/image-builder/files-mesh-lxc/usr/sbin/batman_trigger.sh`
  - Update `build-images.sh` to copy from `shared/` into both image trees before build
- [ ] Fix stale comments in image builder files:
  - `files-mesh-lxc/etc/default/callhome` — update port example from `8088` to `WEBUI_PORT`
  - `callhome.sh` header — align path comment with `/usr/sbin/callhome.sh`
- [ ] Fix script usage strings:
  - `build.py` ~529 — `./setup.sh` → `./scripts/setup.sh`
  - `scripts/cleanup.sh` ~7 — `./cleanup.sh` → `./scripts/cleanup.sh`
  - `scripts/wol.sh` ~69 — `./wol.sh` → `./scripts/wol.sh`
  - `scripts/setup.sh` ~34 — add reference to `./scripts/run.sh`
- [ ] Fix `scripts/wol.sh` ~27 — load env from project root, not `$SCRIPT_DIR`
- [ ] Fix `scripts/cleanup.sh` ~48 — add `.venv` existence check before `source`

**Verify:**
- [ ] `diff scripts/image-builder/shared/callhome.sh scripts/callhome.sh` — identical or symlink
- [ ] `bash -n scripts/build-images.sh` — syntax check passes
- [ ] `grep -r "files-mesh-lxc/usr/sbin/callhome.sh" scripts/` — no direct references remain
- [ ] `grep -r "files-router-vm/usr/sbin/batman_trigger.sh" scripts/` — no direct references remain
- [ ] After mesh build: `sha256sum` of baked `callhome.sh` matches `scripts/image-builder/shared/callhome.sh`
- [ ] After mesh build: `sha256sum` of baked `batman_trigger.sh` matches `scripts/image-builder/shared/batman_trigger.sh`

**Rollback:**
Restore duplicate files from git. Delete `scripts/image-builder/shared/`.
Revert `build-images.sh` copy steps. Revert usage string fixes.

---

### Milestone 5: Build-Images Helper Extraction
_Depends on M4._

Extract repeated sequences in `build-images.sh` into reusable helper functions.
The 10+ `build_*_lxc` functions share identical phases (provision, wait, install,
dump, fetch) that should be parameterized helpers.

See: `build-conventions` skill, `image-management-patterns` skill.

**Implementation pattern:**
- Modified: `scripts/build-images.sh`
- No new files — helpers are functions within the same script

- [ ] Extract `debian_lxc_provision()` helper:
  - Params: VMID, hostname, template, disk size, memory, bridge
  - Handles: destroy existing, `pct create`, start, wait for network readiness
- [ ] Extract `wait_ct_ready()` helper:
  - Params: VMID, timeout (default 30s)
  - Handles: poll `pct status` until running, verify `getent hosts` works inside
- [ ] Extract `setup_ct_dns()` helper:
  - Params: VMID
  - Handles: write `resolv.conf` with working DNS, verify resolution
- [ ] Extract `run_apt_install_block()` helper:
  - Params: VMID, package list string
  - Handles: `apt-get update`, `apt-get install -y --no-install-recommends`, `apt-get clean`
- [ ] Extract `dump_and_fetch_template()` helper:
  - Params: VMID, output filename, local destination dir
  - Handles: `vzdump`, `scp` to controller, cleanup vzdump artifacts, `pct destroy`
- [ ] Refactor each `build_*_lxc` function to use helpers:
  - Preserve any service-specific steps (e.g., pihole pre-seed, rsyslog config) as inline blocks
  - Keep per-service function as the orchestrator: provision → dns → install → configure → dump
- [ ] Update manifest computation to work with refactored functions

**Verify:**
- [ ] `bash -n scripts/build-images.sh` — syntax check passes
- [ ] `./scripts/build-images.sh --only pihole` — builds successfully (one image smoke test)
- [ ] Built template is byte-identical to previous build (or validated via `sha256sum`)
- [ ] All `build_*_lxc` functions still listed in `--help` / usage output

**Rollback:**
`git checkout scripts/build-images.sh`.

---

### Milestone 6: Ansible Safety & DRY
_Depends on M0._

Fix shell pipeline safety gaps, extract duplicated Ansible task blocks, and
clean up dead task files. No functional/network behavior changes — only code
organization and Ansible reporting improvements. No new files are deployed to
Proxmox hosts. This milestone has the highest blast radius (site.yml,
cleanup.yml, roles) — always run full `molecule test` after completion.

See: `ansible-shell-safety` skill, `ansible-conventions` skill, `proxmox-cleanup-safety` skill.

**Implementation pattern:**
- New files: `tasks/infra_pre_tasks.yml`, `tasks/cleanup_host_infra.yml`, `tasks/configure_openwrt_lxc_callhome.yml`
- Modified: `playbooks/site.yml`, `playbooks/cleanup.yml`
- Modified: roles with missing pipefail
- Deleted: `tasks/wait_for_api_ready.yml`

- [ ] Add `set -o pipefail` + `executable: /bin/bash` to all shell tasks with pipelines:
  - `roles/proxmox_pci_passthrough/tasks/main.yml` ~47 — `ls | wc -l`
  - `tasks/bootstrap_lan_host.yml` ~62 — `ip link | grep | awk | head`
  - `tasks/bootstrap_lan_host.yml` ~73 — `uci show | grep -c`
  - `tasks/cleanup_lan_host.yml` ~84 — `iptables -S | grep | while read`
  - `playbooks/cleanup.yml` ~216 — `iptables -t nat -S | grep -q`
  - `playbooks/cleanup.yml` ~1129 — same mirrored pattern
  - Note: `grep -q` in pipelines → replace with `grep -c` per project rules
  - Exception: hookscript in `roles/kiosk_lxc/tasks/main.yml` runs on Proxmox host bash but pipes through `pct status`/`qm status` where the receiving end is BusyBox; use `grep -c` instead of `grep -q` but the pipeline is host-side bash (pipefail is safe to add)
- [ ] Extract shared `pre_tasks` block:
  - Create `tasks/infra_pre_tasks.yml` (disable enterprise repos, add no-subscription, clock skew check, conditional NTP)
  - Include from `site.yml` Play 1 (`proxmox:!lan_hosts`) and Play 6 (`lan_hosts`)
- [ ] Extract shared cleanup block:
  - Create `tasks/cleanup_host_infra.yml` (vfio unbind, GPU state, remove ansible-managed files, iptables cleanup, hookscript removal, enterprise repo restore, bridge teardown, WiFi reload)
  - Include from `playbooks/cleanup.yml` both primary and LAN host cleanup sections
- [ ] Extract shared OpenWrt LXC callhome configuration:
  - Create `tasks/configure_openwrt_lxc_callhome.yml` with vars `callhome_ct_id`, `callhome_container_name`
  - Replace duplicated tasks in `roles/openwrt_mesh_lxc/tasks/main.yml` and `roles/openwrt_bridge_lxc/tasks/main.yml`
- [ ] Remove dead task files:
  - Delete `tasks/wait_for_api_ready.yml` (zero references in repo)
  - Delete `tasks/reconstruct_gaming_group.yml` (gaming LXC uses host-side `pct exec`, not pct_remote dynamic groups)
- [ ] Add banner comment to `roles/gaming_vm/tasks/main.yml`:
  - "Legacy role for molecule/sunshine-vm/ only. Active gaming uses gaming_lxc."
- [ ] Standardize handler naming to human-readable capitalized style (e.g., `Restart Netdata`):
  - Update `roles/netdata_configure/handlers/main.yml`: `_restart_netdata` → `Restart Netdata`
  - Update corresponding `notify:` directives in the same role's tasks
  - Audit all roles for underscore-prefixed handler names and rename
- [ ] Fix `changed_when: true` on groupadd tasks in `jellyfin_configure`, `moonlight_configure`, `kodi_configure`:
  - Gate on stdout content instead of always reporting changed
- [ ] Move `bridge_ct_ip_offset` in `group_vars/all.yml` under correct section header
- [ ] Replace `grep -oP` in `molecule/default/verify.yml` ~96 with portable `awk`/`sed` pattern
- [ ] Document `desktop_configure` apt install as exception in `roles/AGENTS.md`

**Verify:**
- [ ] `ansible-lint roles/ playbooks/ tasks/` — passes
- [ ] `yamllint .` — passes
- [ ] `molecule test` — full E2E passes with identical behavior
- [ ] `grep -rn "set -o pipefail" tasks/ playbooks/ roles/ | wc -l` — count increased by 7+
- [ ] `tasks/wait_for_api_ready.yml` does not exist
- [ ] `grep -r "include_tasks.*infra_pre_tasks" playbooks/site.yml` — used in both infra plays

**Rollback:**
Revert playbooks first (`site.yml`, `cleanup.yml`) to remove `include_tasks`
references, THEN delete new task files (`infra_pre_tasks.yml`, `cleanup_host_infra.yml`,
`configure_openwrt_lxc_callhome.yml`). Reverting in the wrong order leaves
broken `include_tasks` references. `git checkout` all modified roles.
Restore `tasks/wait_for_api_ready.yml` from git if needed.

---

### Milestone 7: Skills, Rules & Documentation Alignment
_Depends on M1b, M2, M3, M4, M5, M6._

Align all documentation, skills, and rules with the refactored codebase.
Fix contradictions, remove redundancy, update stale references.

See: `writing-skills` skill, `code-review-checklist` skill.

**Implementation pattern:**
- Modified: ~25 documentation and skill files
- No code changes

- [ ] Fix critically incorrect skills:
  - `.agents/skills/molecule-testing-patterns/SKILL.md` — rewrite pipeline section to match actual `test_sequence`, fix node count (6, not 4), fix SSH method (ProxyCommand, not ProxyJump)
  - `.agents/skills/testing-workflow/SKILL.md` — remove "reconverge — baseline restored" claim
  - `molecule/AGENTS.md` — remove "destroys the baseline at the end" claim
- [ ] Standardize ProxyCommand terminology (batch find-and-replace):
  - `docs/architecture/overview.md` — ProxyJump → ProxyCommand
  - `docs/architecture/openwrt-build.md` — ProxyJump → ProxyCommand
  - `docs/architecture/AGENTS.md` — ProxyJump → ProxyCommand
  - `.cursor/skills/multi-node-ssh/SKILL.md` — ProxyJump → ProxyCommand
  - `inventory/AGENTS.md` — ProxyJump → ProxyCommand
- [ ] Consolidate redundant skill pairs (designate canonical, make other a pointer):
  - `.cursor/skills/ansible-testing/` = canonical; `.agents/skills/molecule-testing/` = pointer
  - `.agents/skills/rollback-architecture/` = canonical; `.cursor/skills/rollback-patterns/` = pointer
  - `.agents/skills/vm-lifecycle-architecture/` = canonical; `.cursor/skills/vm-lifecycle/` = pointer
  - `.agents/skills/task-ordering/` = canonical; `.cursor/rules/task-ordering.mdc` = pointer
  - `.agents/skills/clean-baselines/` = canonical; `.cursor/rules/clean-baselines.mdc` = pointer
  - `.agents/skills/ansible-conventions/` = canonical; `.cursor/rules/ansible-conventions.mdc` = pointer
  - `.agents/skills/writing-skills/` = canonical; `.cursor/skills/writing-skills/` = pointer
  - `.agents/skills/proxmox-safety-rules/` = canonical; `.cursor/skills/proxmox-host-safety/` = pointer
- [ ] Update architecture docs:
  - `docs/architecture/AGENTS.md` — fix page count (16, not 13), add `bridge_nodes` and `kiosk_nodes` to device flavors
  - `docs/architecture/overview.md` — fix default port, fix mesh1 kiosk IP, fix `gpu_pci_devices` reference
  - `docs/architecture/openwrt-build.md` — remove stale `opkg install` narrative, use "WAN bridge" language
  - `docs/architecture/build-profiles.md` — add `kiosk_nodes` row
- [ ] Update inventory docs:
  - `inventory/AGENTS.md` — add `bridge-1` and `bridge-2` to host list, fix container IP offset list (current values: kiosk=20, rsyslog=30, netdata=40)
  - `docs/projects/AGENTS.md` — update container IP offset list to match `group_vars/all.yml`
- [ ] Archive stale project plans:
  - `docs/projects/2026-04-09-19-container-nat-networking/project_plan.md` — update offset references or mark `## Status: COMPLETED`
  - `docs/projects/2026-03-09-05-netdata-monitoring/project_plan.md` — mark completed
  - `docs/projects/2026-03-09-12-custom-ux-kiosk/project_plan.md` — mark checklist items done
- [ ] Fix `pyproject.toml` version to match `project_version` in `group_vars/all.yml` (or document divergence)
- [ ] Update `images/manifest.json` — remove or fill `sunshine` entry with empty sha256
- [ ] Reconcile or document inventory/molecule drift:
  - `monitoring_nodes` static vs Molecule default membership difference
  - `sunshine-vm` scenario `gaming_nodes` membership (home vs ai)
  - `mesh-ax210` scenario `wifi_nodes` vs `bridge_nodes` group names
- [ ] Document OpenWrt LXC vs Debian container IP model divergence:
  - OpenWrt mesh/bridge: `offset + 200 + index` on WAN bridge (L2 access required)
  - Debian containers: `10.99.{subnet_id}.{offset}` on NAT bridge
  - Add to `docs/architecture/overview.md` networking section
- [ ] Document VMID constants in `data.py` as UI mirror of `group_vars/all.yml` (add comment)

**Verify:**
- [ ] `grep -r "ProxyJump" docs/ .agents/ .cursor/ inventory/ | grep -v "Previous bug\|Previous catastrophe\|Previous debugging"` — zero matches
- [ ] `grep -r "13 pages" docs/` — zero matches
- [ ] `grep -r "offset 21\|offset 11\|offset 12\|offset 13" docs/projects/AGENTS.md` — zero stale offsets
- [ ] Every `.cursor/skills/` file with a canonical `.agents/skills/` counterpart contains only a pointer
- [ ] `docs/architecture/AGENTS.md` mentions `bridge_nodes` and `kiosk_nodes`

**Rollback:**
`git checkout` all modified documentation files. Content-only changes — no
runtime impact.

---

## Future Considerations

### Not in scope (deferred)

- **`build.py` → `BuildContext` class**: Valuable OOP refactor but touches the entry point. Requires careful `test_build.py` updates and risk of breaking production `./scripts/run.sh`. Defer to a dedicated project.
- **`callhome.py` → `CollectorRegistry`**: Nice pattern but callhome.py runs inside containers with minimal deps. Adding class infrastructure to a stdlib-only script adds complexity for minimal gain. Defer unless collector count exceeds 8.
- **`test_host_safety.py` → ansible-lint custom rule**: Correct long-term fix but requires ansible-lint plugin authoring expertise. Keep as pytest for now.
- **Kiosk Fleet rendering**: `kiosk_server.py` passes `Fleet([])` to containers page. Fixing this requires a design decision about local-only fleet data. Defer to a UX project.
- **Auth gating for callhome**: `_validate_callhome_token` returns `True` when no key is set. Changing this requires coordinating with all deployed containers. Defer to a security hardening project.
- **Nine molecule scenarios missing `prepare.yml`**: These are layered scenarios that depend on `molecule converge -s default`. Adding `prepare.yml` to each requires understanding their dependency chain. Defer to a molecule infrastructure project.

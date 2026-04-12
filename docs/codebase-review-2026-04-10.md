# Codebase Review — 2026-04-10

Two-pass review (forward + reverse) of the entire vm_builds codebase after the
4-tier event-driven architecture, OOP domain model, and versioned image build
system were introduced. Reviewed against DRY, OOP, KISS, Factory pattern, and
service-class principles.

---

## Table of Contents

1. [Dead & Stale Code](#1-dead--stale-code)
2. [DRY Violations](#2-dry-violations)
3. [OOP & Factory Pattern Opportunities](#3-oop--factory-pattern-opportunities)
4. [Bad Patterns & Anti-Patterns](#4-bad-patterns--anti-patterns)
5. [Test Suite Issues](#5-test-suite-issues)
6. [Ansible Safety Gaps](#6-ansible-safety-gaps)
7. [Skills & Rules Staleness](#7-skills--rules-staleness)
8. [Documentation Drift](#8-documentation-drift)
9. [Cross-File Consistency Issues](#9-cross-file-consistency-issues)
10. [Recommended Refactoring Priority](#10-recommended-refactoring-priority)

---

## 1. Dead & Stale Code

### Python

| File | Issue | Fix |
|------|-------|-----|
| `scripts/webui/theme.py` ~559 | `kiosk_page_shell(page_name)` — parameter defined but never used | Remove param or wire to kiosk sub-nav highlight |
| `scripts/webui/kiosk_server.py` ~105 | `_render_containers(Fleet([]))` — Fleet summary paths (`.has_telemetry`, per-host guest counts) are always empty/meaningless on kiosk | Pass `None` or a dedicated flag to skip fleet-only sections; or build minimal fleet from local host |
| `scripts/webui/pages/containers.py` ~17 | `_fetch_guests()` swallows errors with `except + pass`, returns `[]` | Log at warning level; surface "API unreachable" vs "empty list" |
| `build.py` ~578 | `if api_proc and not args.no_api:` — when `api_proc` is set, `--no-api` was already not used; inner check is redundant | Simplify to `if api_proc:` |
| `tests/test_webui_heartbeat.py` ~25 | `collect_router_metrics` imported but never used in tests | Add parser-only tests or remove import |

### Ansible / Shell

| File | Issue | Fix |
|------|-------|-----|
| `tasks/wait_for_api_ready.yml` | Zero `include_tasks` references anywhere in the repo | Remove or wire into a role/playbook that needs it |
| `tasks/reconstruct_gaming_group.yml` | Documented in overview/rules but never included from any playbook, converge, or verify | Add `include_tasks` where gaming pct_remote group is needed, or remove |
| `roles/gaming_vm/` (entire role) | Legacy — `site.yml` uses `gaming_lxc` only; role exists for `molecule/sunshine-vm/` only | Add banner comment that this is legacy-only; retire when ready |
| `scripts/wol.sh` ~27 | Loads `.env`/`test.env` from `$SCRIPT_DIR` (the `scripts/` dir), but env files live at project root | Load from `$(cd "$SCRIPT_DIR/.." && pwd)/.env` |
| `scripts/cleanup.sh` ~48 | `source .venv/bin/activate` with no existence check (unlike `run.sh`) | Mirror `run.sh`: check `.venv` exists, print `setup.sh` hint |

### Image Builder

| File | Issue | Fix |
|------|-------|-----|
| `scripts/image-builder/files-mesh-lxc/etc/default/callhome` | Comment shows port `8088`; project uses `WEBUI_PORT` (52500/52525) | Update example comment |
| `scripts/image-builder/files-mesh-lxc/usr/sbin/callhome.sh` ~6 | Header says cron runs `/usr/local/bin/callhome.sh`; actual crontab uses `/usr/sbin/callhome.sh` | Align comment with baked path |
| `images/manifest.json` | `sunshine` entry has empty `sha256` and `built_at` | Fill after build or omit until image exists |

---

## 2. DRY Violations

### Python — High Impact

| Location | Duplication | Fix |
|----------|-------------|-----|
| `scripts/webui/pages/bridge.py`, `mesh.py`, `router.py` | Same structural pattern: lazy-import subscription manager, subscribe to metric types, `ui.timer(5.0)`, refresh handlers | Extract `MetricPageController` or shared `register_heartbeat_page(metric_types, render_fn)` mixin |
| `scripts/webui/pages/bridge.py`, `containers.py`, `mesh.py`, `launch.py` | Repeated `async with httpx.AsyncClient() as client:` + `get_api_base_url()` + method/timeout | Create `scripts/webui/api_client.py` with `async def api_get(path)` / `api_post(path, json)` |
| `scripts/webui/heartbeat.py` ~279 vs ~372 | `collect_bridge_metrics` and `collect_mesh_metrics` both infer WiFi `role` from script status / interface type | Extract `_infer_wifi_role(script_status, interfaces) -> str` |
| `scripts/webui/heartbeat.py` ~641 vs `manager.py` ~293 | `pct exec 104` hardcoded in collector; `_BRIDGE_CT_ID = 104` in manager | Single constant in `data.py` next to VMID docs |
| `scripts/webui/data.py` ~1402 vs ~2459 | `Fleet.health_score` and `_compute_health_score` both implement 40/30/30 availability/disk/memory scoring | One function taking protocol/dataclass with status + disk + mem |
| `scripts/webui/manager.py` ~128 vs ~425 | `_collect_host_metrics` and `_api_guests` both parse `pct list`/`qm list` output identically | Extract `heartbeat.parse_guest_list(stdout) -> list[dict]` |
| `scripts/webui/data.py` ~765 vs ~698 | `get_known_hosts` hardcodes mesh1 IP `"10.10.10.210"` while `HostRegistry.seed_from_env` has same default — two code paths for host list | Single helper `mesh1_lan_ip(env) -> str` or always derive from `HostRegistry` |
| `scripts/webui/cluster_dashboard.py` ~174 vs `data.py` `format_uptime` | `_fmt_uptime` and `format_uptime` format seconds→human strings differently | Unify into one uptime formatter |
| `build.py` ~232 vs `scripts/callhome.py` ~65 | Same "UDP connect to 8.8.8.8:80 to discover primary IPv4" pattern | Shared `scripts/netutil.py` or one-line helper |

### Shell — High Impact

| Location | Duplication | Fix |
|----------|-------------|-----|
| `scripts/callhome.sh` vs `files-mesh-lxc/usr/sbin/callhome.sh` | **Byte-identical** copies | Single canonical file; `build-images.sh` copies into image tree |
| `files-router-vm/.../batman_trigger.sh` vs `files-mesh-lxc/.../batman_trigger.sh` | **Byte-identical** copies | Same: one source, copy into both image trees during build |
| `scripts/build-images.sh` multiple `build_*_lxc` blocks | Repeated sequences: destroy old CT, template check, `pct create`, wait loops, `resolv.conf`, `apt-get`, `inject_callhome_agent`, `vzdump`, `scp`, cleanup | Extract phased helpers: `debian_lxc_provision()`, `wait_ct_ready()`, `run_apt_install_block()`, `dump_and_fetch_template()` |

### Ansible — High Impact

| Location | Duplication | Fix |
|----------|-------------|-----|
| `playbooks/site.yml` ~92 vs ~204 | Same "disable enterprise repos + add no-subscription + clock skew + NTP burst" `pre_tasks` block for primary and LAN hosts | Move to `tasks/disable_pve_enterprise_and_sync_time.yml`, include from both plays |
| `playbooks/cleanup.yml` ~153 vs ~1090 | Same vfio unbind + GPU state + remove ansible-managed files + iptables NAT/FORWARD cleanup + hookscript removal + enterprise repo restore + bridge teardown + WiFi reload sequences | Extract `tasks/cleanup_host_network_stack.yml` |
| `roles/openwrt_mesh_lxc/tasks/main.yml` ~189 vs `roles/openwrt_bridge_lxc/tasks/main.yml` ~304 | Nearly identical callhome configuration tasks, differ only by VMID and hostname variable | Create `tasks/configure_openwrt_lxc_callhome.yml` with vars `callhome_ct_id`, `callhome_container_name` |

### Tests — High Impact

| Location | Duplication | Fix |
|----------|-------------|-----|
| `test_webui_app.py`, `test_webui_bridge.py`, `test_webui_kiosk_server.py` | Duplicated `webui()` / `infra_ctx` / `viewer_ctx` / `launch_ctx` / `kiosk_ctx` NiceGUI context managers | One `tests/webui_helpers.py` with parameterized `register_pages=(...)` |
| `test_webui_app.py` ~830 | Repeated `env_content = f"PRIMARY_HOST=...\n"` blocks and `SAMPLE_CHECKIN` payloads | Factory `def minimal_env(**extra) -> str` + shared fixture for API base env |
| All test files | Repeated `PROJECT_ROOT` + `sys.path.insert` | Rely on `conftest.py` or `pyproject.toml` `pythonpath` for single path setup |
| `test_webui_data.py` (~4000+ lines) | Single enormous module mixing env, fleet, callhome, UI helpers | Split into `test_webui_data_env.py`, `test_webui_data_fleet.py`, `test_callhome.py`, shared `fixtures/data_factories.py` |
| Multiple test files | `HostTelemetry` / `DeployRecord` construction — local `_make_*` helpers exist in some classes but not shared | Create shared factory module for test dataclasses |

---

## 3. OOP & Factory Pattern Opportunities

### API Client Service

**Problem:** Every page module creates its own `httpx.AsyncClient`, constructs URLs
from `get_api_base_url()`, handles timeouts, and catches exceptions independently.

**Solution:** Create `scripts/webui/api_client.py`:

```python
class ApiClient:
    """Thin HTTP client for Manager API calls."""
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url
        self.timeout = timeout

    async def get(self, path: str, **kwargs) -> httpx.Response: ...
    async def post(self, path: str, json: dict | None = None, **kwargs) -> httpx.Response: ...
```

All page modules import and use this instead of raw `httpx`.

### MetricPage Base Class

**Problem:** `bridge.py`, `mesh.py`, `router.py` all follow the same pattern:
subscribe to metrics, set up timer, refresh on interval, render data.

**Solution:** Extract a `MetricPageController` base:

```python
class MetricPageController:
    """Base for pages that subscribe to heartbeat metric polling."""
    def __init__(self, metric_types: list[str], refresh_interval: float = 5.0): ...
    async def subscribe(self, node_id: str) -> None: ...
    async def refresh(self) -> None: ...  # calls self.render()
    def render(self, cache: dict) -> None: ...  # override in subclass
```

### Guest List Parser

**Problem:** `_collect_host_metrics` and `_api_guests` both parse `pct list`/`qm list`
output with the same structure, in different methods.

**Solution:** Single `heartbeat.parse_guest_list(stdout: str) -> list[dict]` used by both.

### Health Score Calculator

**Problem:** `Fleet.health_score` and `_compute_health_score` implement the same
40/30/30 scoring algorithm independently.

**Solution:** One function with a protocol interface:

```python
def compute_health_score(nodes: Iterable[HasHealthMetrics]) -> int:
    """40% availability + 30% disk + 30% memory."""
    ...
```

### Build Context (build.py)

**Problem:** `main()` orchestrates env loading, host probing, API lifecycle, and
fleet monitoring procedurally with many local variables.

**Solution:** `BuildContext` or `AnsibleRunner` class holding env path, resolved
host, playbook path, optional `ApiServer`/`FleetMonitor`. Constructors inject deps,
making testing cleaner.

### Collector Registry (callhome.py)

**Problem:** Many pure `collect_*` functions called in sequence with manual
if-guards for prerequisites.

**Solution:** `CollectorRegistry` that auto-discovers collectors and skips those
whose prerequisites are missing:

```python
class CollectorRegistry:
    def register(self, name: str, fn: Callable, requires: list[str]): ...
    def collect_all(self) -> dict[str, Any]: ...
```

### Image Builder Helpers (build-images.sh)

**Problem:** Large procedural script with repeated sequences across 10+ `build_*_lxc`
functions (destroy CT, create, wait, apt-get, vzdump, scp).

**Solution:** Extract phased helpers:

```bash
debian_lxc_provision() { ... }   # pct create + wait + resolv.conf
run_apt_install_block() { ... }  # apt-get update + install + cleanup
dump_and_fetch_template() { ... } # vzdump + scp + cleanup
```

### Test Harness Base Class

**Problem:** Every test file reinvents NiceGUI context management, storage setup,
and page registration.

**Solution:**

```python
class WebuiTestHarness:
    """Shared NiceGUI test context with storage and page registration."""
    async def run(self, pages: list[Callable], storage: dict | None = None): ...
```

---

## 4. Bad Patterns & Anti-Patterns

### Broad Exception Handling

| File | Line (approx.) | Issue |
|------|----------------|-------|
| `scripts/webui/run_process.py` ~91 | `except Exception` around subprocess I/O | Catch `OSError`, `asyncio.CancelledError` specifically |
| `scripts/webui/pages/launch.py` ~24 | Returns `{"success": True}` on any `httpx`/`OSError` — masks real failures | Distinguish "API unreachable" from "request sent" |
| `build.py` ~587 | Bare `except Exception: pass` around timeline POST | Log at warning; catch `URLError`, `HTTPError`, `TimeoutError` |
| `scripts/callhome.py` ~587 | `except Exception` in `run_loop` — catches `KeyboardInterrupt` path | Narrow to `(OSError, ValueError)` |

### Fallback Chains

| File | Issue |
|------|-------|
| `scripts/webui/data.py` ~1779 | `load_kiosk_config` tries file → NiceGUI `app.storage` → `{}` — multi-step resolution | Document as canonical pattern or split into explicit call sites with no silent `{}` |

### Security Concerns

| File | Issue |
|------|-------|
| `scripts/webui/app.py` ~119 | `_validate_callhome_token` returns `True` when `CALLHOME_PRIVATE_KEY` is empty | Gate "no key" mode on explicit env flag (`CALLHOME_ALLOW_INSECURE=true`) |
| `scripts/webui/app.py` ~162 | `_merge_cluster_containers` calls private `data._save_node_registry` | Expose public `data.merge_cluster_child_containers()` |

### Encapsulation Violations

| File | Issue |
|------|-------|
| `scripts/webui/app.py` ~162 | Calls `data._save_node_registry` (private helper) directly | Expose as public API |
| `scripts/webui/data.py` VMID constants | VMIDs (302, 301, 400) duplicate `group_vars/all.yml` knowledge | Document as UI mirror; consider generating from single JSON artifact |

### Stale Route/Nav Wiring

| File | Issue |
|------|-------|
| `scripts/webui/pages/cluster_dashboard.py` ~17 | `page_shell(active="fleet")` but `NAV_SECTIONS` has no "Fleet" entry — no sidebar highlight | Add Fleet nav entry or align `active` slug |

---

## 5. Test Suite Issues

### Bad Mocks / Misleading Tests

| File | Issue | Fix |
|------|-------|-----|
| `test_webui_app.py` ~1244 | `test_batman_hmac_token_matches_openssl` never invokes `openssl` — only checks Python `hmac` format | Rename to `test_batman_hmac_token_format` or actually compare with `openssl dgst` |
| `test_webui_data.py` ~277 | `test_service_tags_match_build_py_docstring` parses `build.__doc__` — fragile, duplicate source of truth | Single source: `SERVICE_TAGS` constant or generate docs from code |
| `test_build.py` ~535 | `test_rollback_tag_naming_convention` only checks `build_command` contains a string; doesn't verify tags exist in `site.yml` | Cross-check against `site.yml` tags |
| `test_build.py` ~574 | `test_returns_ip_string` accepts any non-empty string — doesn't verify correct IP | Assert IPv4 format or compare to known interface |

### Environment-Dependent Tests (Unlabeled)

| File | Issue | Fix |
|------|-------|-----|
| `test_build.py` ~286 | `TestInfrastructureHealth` probes real hosts — fails when laptop offline | Add `pytest.mark.integration`; gate on env var for CI |
| `test_webui_data.py` ~1627 | `test_ssh_success` / `test_ssh_failure` use real SSH | Same: mark `integration` or skip without reachability |
| `test_webui_app.py` ~222 | `test_probe_updates_status` sleeps up to 40s | Mock `probe_host` for UI state transition; keep one integration test |
| `test_webui_app.py` ~1128 | Batman/Bridge/WiFi API tests SSH to real hosts — destructive side effects | Run in dedicated integration job; document risk |
| `test_webui_heartbeat.py` ~198 | `TestRealSSH` / `TestRealWifiCollectors` require deployed containers | Mark `integration` |

### Missing Test Infrastructure

| Issue | Fix |
|-------|-----|
| No `pytest` markers for `integration` vs `unit` | Register markers; default CI runs only unit tests |
| No shared test harness for NiceGUI context | Create `WebuiTestHarness` class |
| No shared factory module for test dataclasses | Create `tests/factories.py` with `make_host()`, `make_telemetry()`, etc. |

### Stale / Dead Tests

| File | Issue |
|------|-------|
| `test_webui_app.py` ~69 | Asserts `"10/14 built"` with magic numbers | Compute from `len(EXPECTED_IMAGES)` dynamically |
| `test_webui_hub.py` ~166 | Hardcodes expected keys, section count, `len == 15` | Derive from `data.get_hub_services()` |
| `test_host_safety.py` (entire file) | Regex + YAML parse scanning — it's a linter, not a behavioral test | Rename to `lint_host_safety.py` or register custom ansible-lint rule |
| `test_wol.py` ~64 | `test_ai_not_in_wol_script` duplicates the loop over `wol_capable` | Drop redundant test |

### Test File Organization

| Issue | Fix |
|-------|-----|
| `test_webui_data.py` is 4000+ lines mixing 6 domains | Split into focused modules |
| `conftest.py` monkey-patches NiceGUI at import time | Prefer pytest plugin/autouse fixture with explicit teardown |
| `conftest.py` uses `tempfile.mkdtemp` without fixture | Use `tmp_path_factory` session fixture |
| `tests/fixtures/complete.env` | Committed token-like strings — use obvious placeholders |

---

## 6. Ansible Safety Gaps

### Missing `set -o pipefail`

| File | Task (approx.) |
|------|-----------------|
| `roles/proxmox_pci_passthrough/tasks/main.yml` ~47 | `ls ... \| wc -l` |
| `tasks/bootstrap_lan_host.yml` ~62 | `ip link ... \| grep ... \| awk ... \| head` |
| `tasks/bootstrap_lan_host.yml` ~73 | `uci show ... \| grep -c` |
| `tasks/cleanup_lan_host.yml` ~84 | `iptables -S \| grep ... \| while read` |
| `playbooks/cleanup.yml` ~216 | `iptables -t nat -S \| grep -q` (also uses `grep -q` in pipeline) |
| `playbooks/cleanup.yml` ~1129 | Same pattern mirrored |
| `roles/kiosk_lxc/tasks/main.yml` ~59 | Hookscript uses `pct status \| grep -q` / `qm status \| grep -q` |

### Inconsistencies

| File | Issue |
|------|-------|
| `roles/netdata_configure/handlers/` vs `roles/proxmox_bridges/handlers/` | Mixed handler naming: `_restart_netdata` (private-style) vs `Reload networking` (human-readable) |
| `molecule/default/verify.yml` ~96 | Uses `grep -oP` (Perl regex) — inconsistent with portability constraints elsewhere |
| `roles/jellyfin_configure/`, `moonlight_configure/`, `kodi_configure/` | `changed_when: true` on groupadd tasks — always reports change; could gate on stdout |
| `roles/desktop_configure/tasks/main.yml` ~22 | `apt` installs at configure time — documented exception to "bake" rule but not referenced in AGENTS.md |

---

## 7. Skills & Rules Staleness

### Critical — Incorrect Content

| File | Issue | Fix |
|------|-------|-----|
| `.agents/skills/molecule-testing-patterns/SKILL.md` | Describes test pipeline with trailing `cleanup` + `converge` that does NOT exist. Claims 4 nodes and ProxyJump — actual is 6 nodes and ProxyCommand | Rewrite to match `molecule/default/molecule.yml` or delete and point to canonical source |
| `.agents/skills/testing-workflow/SKILL.md` | Says `molecule test` ends with "reconverge — baseline restored" — contradicts rules and actual sequence | Remove reconverge claim; align with canonical |
| `molecule/AGENTS.md` | Says `molecule test` "destroys the baseline at the end" — conflicts with actual test_sequence | Update to describe actual sequence (ends at verify) |

### Redundant Skills (Overlapping Content)

| Pair | Overlap |
|------|---------|
| `.cursor/skills/ansible-testing/` vs `.agents/skills/molecule-testing/` vs `.agents/skills/molecule-testing-patterns/` vs `.agents/skills/testing-workflow/` | TDD, molecule test vs converge, pipeline phases, baseline workflow — same topics, different accuracy |
| `.cursor/skills/rollback-patterns/` vs `.agents/skills/rollback-architecture/` | Layered rollback model duplicated almost verbatim |
| `.cursor/skills/vm-lifecycle/` vs `.agents/skills/vm-lifecycle-architecture/` | Two-role pattern, deploy_stamp, play order |
| `.cursor/rules/task-ordering.mdc` vs `.agents/skills/task-ordering/` | Same dependency-ordering principles, different formatting |
| `.cursor/rules/clean-baselines.mdc` vs `.agents/skills/clean-baselines/` | Same baseline/apt/template principles |
| `.cursor/rules/ansible-conventions.mdc` vs `.agents/skills/ansible-conventions/` | Overlapping FQCN/module conventions |
| `.cursor/skills/writing-skills/` vs `.agents/skills/writing-skills/` | Nearly duplicate "Writing Skills for LLMs" |
| `.cursor/skills/proxmox-host-safety/` vs `.agents/skills/proxmox-network-safety/` + `.agents/skills/proxmox-safety-rules/` | Overlapping "never destroy management bridge / don't assume vmbr0 = WAN" |

**Recommendation:** Designate ONE canonical source per topic. Make the other a
one-line pointer. Never maintain the same content in two places.

### ProxyJump vs ProxyCommand Contradiction

These files say **ProxyJump** when the implementation uses **ProxyCommand**:

- `docs/architecture/overview.md`
- `docs/architecture/openwrt-build.md`
- `docs/architecture/AGENTS.md`
- `.cursor/skills/multi-node-ssh/SKILL.md`
- `inventory/AGENTS.md`

Correct files (`ProxyCommand`):
- `inventory/group_vars/lan_hosts.yml`
- `.agents/skills/lan-ssh-patterns/SKILL.md`
- `.cursor/rules/proxmox-safety.mdc`

**Fix:** Standardize on "ProxyCommand" everywhere.

---

## 8. Documentation Drift

### Architecture Docs

| File | Issue | Fix |
|------|-------|-----|
| `docs/architecture/openwrt-build.md` | Claims `openwrt_configure` installs WiFi driver packages via `opkg` — no such tasks exist | Document WiFi/mesh packages as baked in the router image build |
| `docs/architecture/openwrt-build.md` | Hardcodes bridge names (`vmbr0`) | Use "WAN bridge (auto-detected)" language |
| `docs/architecture/AGENTS.md` | Says "13 pages" — actual count is 16 non-`__init__` modules | Update count or say "see `Routes` in `data.py`" |
| `docs/architecture/AGENTS.md` | Device Flavors omits `bridge_nodes` and `kiosk_nodes` | Add both |
| `docs/architecture/overview.md` | SuperManager port shows `52525` as default; elsewhere `52500` is default | Pick one canonical default |
| `docs/architecture/overview.md` | Diagram shows mesh1 kiosk at `10.10.10.22` — actual IP depends on sorted group index | Use placeholders or recompute |
| `docs/architecture/overview.md` | `wifi_pci_devices (future: gpu_pci_devices)` — rules warn against documenting unrealized exports | Match wording to actual role exports |
| `docs/architecture/build-profiles.md` | No `kiosk_nodes` row | Add or reference invariant |

### Project Plans (Historical — Mark as Archived)

| File | Issue |
|------|-------|
| `docs/projects/2026-04-09-19-container-nat-networking/project_plan.md` | References Netdata offset 21; actual is 40 |
| `docs/projects/2026-03-09-05-netdata-monitoring/project_plan.md` | References rsyslog offset 11; actual is 30 |
| `docs/projects/2026-03-09-12-custom-ux-kiosk/project_plan.md` | Checklist shows `todo` for files that already exist |

### Script Usage Strings

| File | Issue |
|------|-------|
| `build.py` ~529 | Error says "Run `./setup.sh`" — actual path is `scripts/setup.sh` |
| `scripts/cleanup.sh` ~7 | Usage shows `./cleanup.sh` — actual path is `scripts/cleanup.sh` |
| `scripts/wol.sh` ~69 | Same: `./wol.sh` vs `scripts/wol.sh` |
| `scripts/setup.sh` ~34 | Says "Activate with `source .venv/bin/activate`" — no mention of `scripts/run.sh` |

---

## 9. Cross-File Consistency Issues

### Inventory / Molecule Drift

| Issue | Files |
|-------|-------|
| `monitoring_nodes` in static inventory has only `home`; Molecule default adds `mesh1`, `ai`, `mesh2` | `inventory/hosts.yml` vs `molecule/default/molecule.yml` |
| `sunshine-vm` scenario puts `home` in `gaming_nodes`; static inventory puts `ai` only | `molecule/sunshine-vm/molecule.yml` vs `inventory/hosts.yml` |
| `mesh-ax210` scenario puts `bridge-1`/`bridge-2` in `wifi_nodes`, not `bridge_nodes` | `molecule/mesh-ax210/molecule.yml` |
| `inventory/AGENTS.md` lists only 4 hosts — omits `bridge-1` and `bridge-2` | Should list all 6 |

### Version Drift

| Issue | Files |
|-------|-------|
| `pyproject.toml` version `1.0.0` vs `group_vars/all.yml` `project_version: 1.2.0` | Bump or document as unrelated |

### Variable Organization

| Issue | Files |
|-------|-------|
| `bridge_ct_ip_offset: 27` sits under the "Netdata LXC" comment block in `all.yml` | Move under a dedicated "WiFi Bridge LXC" section |

### OpenWrt LXC IP Models

| Issue | Files |
|-------|-------|
| OpenWrt mesh/bridge LXCs use `offset + 200 + index` on host supernet (L2 on WAN bridge), while Debian containers use `10.99.x.x` NAT — two IP models coexist without unified documentation | `roles/openwrt_mesh_lxc/`, `roles/openwrt_bridge_lxc/`, `docs/projects/nat-networking/` |

### Missing `prepare.yml`

Nine per-feature molecule scenarios lack `prepare.yml` (API server bootstrap):
`bridge-lxc`, `mesh-ax210`, `mesh1-infra`, `openwrt-dns`, `openwrt-pihole-dns`,
`openwrt-syslog`, `openwrt-vlans`, `proxmox-igpu`, `proxmox-lxc`.

If these scenarios require `.state/callhome_url`, they silently depend on a prior
`molecule converge -s default`. Document this or add `prepare.yml`.

---

## 10. Recommended Refactoring Priority

### P0 — Fix Incorrect Content (Same Day)

1. Fix `.agents/skills/molecule-testing-patterns/SKILL.md` — incorrect pipeline, node count, SSH method
2. Fix `molecule/AGENTS.md` — "destroys baseline" claim
3. Fix `.agents/skills/testing-workflow/SKILL.md` — reconverge claim
4. Standardize ProxyCommand terminology across all docs/skills

### P1 — High-Impact DRY (This Sprint)

5. Create `scripts/webui/api_client.py` — eliminates httpx boilerplate in 4+ page modules
6. Extract `MetricPageController` base — deduplicates bridge/mesh/router page pattern
7. Extract `heartbeat.parse_guest_list()` — used by 2 callers
8. Unify health score computation — one function, one algorithm
9. Deduplicate `callhome.sh` and `batman_trigger.sh` — single source, copy at build time
10. Extract `build-images.sh` phased helpers — reduce 10+ function duplication

### P2 — OOP Improvements (Next Sprint)

11. `ApiClient` service class for page modules
12. `CollectorRegistry` for callhome.py
13. `BuildContext` class for build.py
14. Test factory module (`tests/factories.py`)
15. `WebuiTestHarness` base class for NiceGUI tests
16. Split `test_webui_data.py` into focused modules

### P3 — Safety & Cleanup (Next Sprint)

17. Add `set -o pipefail` to 7 identified shell tasks
18. Add `pytest.mark.integration` to environment-dependent tests
19. Fix broad `except Exception` in 4 identified locations
20. Remove dead files: `wait_for_api_ready.yml`, unused `gaming_vm` banner
21. Fix script usage strings to use correct `scripts/` paths

### P4 — Documentation (Ongoing)

22. Update architecture docs (page count, device flavors, port defaults, diagrams)
23. Archive or update stale project plans
24. Consolidate redundant skill pairs (designate canonical, make others pointers)
25. Add missing `prepare.yml` or document dependency for 9 molecule scenarios
26. Document OpenWrt LXC vs Debian container IP model divergence

---

*Review performed: 2026-04-10. Two passes: forward (Python → Ansible → Scripts)
and reverse (leaf files → inventory → docs → cross-file). Skills used:
code-review-checklist, manager-api-pattern, webui-design-system.*

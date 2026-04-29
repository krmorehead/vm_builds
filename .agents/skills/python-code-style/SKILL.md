---
name: python-code-style
description: Python code conventions. Functions return errors, .env parsing strips quotes, type hints required.
---

# Python Code Style

## Rules

1. Functions NEVER call sys.exit(). Return None, raise exception, or return error code.
2. .env parsing MUST strip surrounding quotes from values.
3. Every function MUST have type hints.
4. Use Path from pathlib for file operations.
5. Logging: % formatting for logs, f-strings for non-logging.

## Patterns

### Function error handling

```python
def find_ansible_playbook(playbook: str) -> str:
    if not found:
        raise FileNotFoundError(f"Playbook not found: {playbook}")
    return path

def get_cached_address() -> str | None:
    path = Path('.state/addresses.json')
    if not path.exists():
        return None
    return json.loads(path.read_text()).get('PRIMARY_HOST')

def probe_host(host: str, port: int, timeout: int = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False
```

### .env quote stripping

```python
value = value.strip()
if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
    value = value[1:-1]
env[key.strip()] = value
```

### Path handling

```python
def ensure_file_exists(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    return path

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
```

### Dictionary merging

```python
def merge_dicts(*dicts: dict) -> dict:
    result = {}
    for d in dicts:
        result.update(d)
    return result

config = merge_dicts(default_config, env_config, cli_config)
```

### Logging

```python
import logging
log = logging.getLogger(__name__)

log.debug("Probing host %s", host)  # Use % formatting
msg = f"Built command for {playbook}"  # OK for non-logging
```

### Domain model classes (MVC pattern)

ALWAYS separate business logic from UI rendering using domain classes in
the data layer. Domain objects own derived state — the UI only reads properties.

```python
# Domain class in data.py — owns identity, deploy history, AND live telemetry
class Host:
    def __init__(self, name: str, ip: str, *, is_lan: bool = False) -> None:
        self.name = name
        self.ip = ip
        self.is_lan = is_lan
        self.deploys: list[DeployRecord] = []
        self.telemetry: HostTelemetry | None = None

    def attach_telemetry(self, t: HostTelemetry) -> None:
        self.telemetry = t
        self._guests = _parse_guests(t.services)

    @property
    def healthy(self) -> bool: ...   # from deploy history
    @property
    def errors(self) -> list[str]: ...  # deploy + offline errors
    @property
    def online(self) -> bool: ...    # from telemetry (False if None)
    @property
    def disk_pct(self) -> float: ... # 0.0 if no telemetry
    @property
    def guests(self) -> list[GuestInfo]: ...  # [] if no telemetry

# Aggregate class — delegates to children, adds fleet-wide metrics
class Fleet:
    def __init__(self, hosts: list[Host]) -> None:
        self.hosts = hosts

    @property
    def healthy(self) -> bool: ...
    @property
    def online_count(self) -> int: ...
    @property
    def total_guests(self) -> int: ...
    @property
    def health_score(self) -> int: ...  # 0-100
    @property
    def worst_disk(self) -> Host | None: ...

    def get_host(self, name: str) -> Host | None: ...

# Factory function — wires ALL data sources into domain objects
def build_fleet(env: dict[str, str], state_dir: Path) -> Fleet:
    hosts = [Host(name=i.name, ip=i.ip, ...) for i in get_known_hosts(env)]
    for record in load_deploy_history(state_dir):
        for host in hosts:
            if _deploy_targets_host(record, host.name):
                host.deploys.append(record)
    for node in load_node_registry(state_dir):
        host = ...  # match by hostname
        if host:
            host.attach_telemetry(HostTelemetry(...))
    return Fleet(hosts)

# UI page — thin renderer, ZERO business logic
def _deploy_card(fleet: Fleet) -> None:
    if fleet.healthy:
        ui.badge("success", color="green")
    else:
        for err in fleet.errors:
            ui.label(err)
```

Rules:
- NEVER compute health/status in page modules. Use `.healthy` / `.errors` properties.
- ALWAYS put exit code mapping, status computation, and error descriptions in `data.py`.
- ALWAYS expose `.healthy -> bool` and `.errors -> list[str]` on domain objects.
- ALWAYS use factory functions to wire raw data sources into domain objects.
- ALWAYS return graceful defaults when `telemetry is None` (0.0, [], False, "--").
- NEVER read `RegisteredNode` directly in page modules — use `Host`/`Fleet`.
- Test domain logic with plain pytest — no NiceGUI required.

### UI string constants (NiceGUI web UI)

```python
# ALWAYS centralize UI strings as class constants in data.py
class Labels:
    START_DEPLOY = "Start Deploy"  # used by both page module AND tests

# Page module:
ui.button(Labels.START_DEPLOY, ...)

# Test:
user.find(Labels.START_DEPLOY).click()

# NEVER duplicate strings across page + test:
# BAD: ui.button("Start Deploy") + user.find("Start Deploy")
# GOOD: both reference Labels.START_DEPLOY
```

## Previous bugs

- sys.exit() in functions → untestable, breaks composition
- Missing quote stripping → SSH fails with literal "192.168.1.100"
- Missing type hints → MyPy errors
- Broad `except Exception` in UI code caught too broadly. Use specific exceptions.
- `format_last_seen_relative` crashed on timezone-aware timestamps. Fix: `.replace(tzinfo=None)`.
- 200+ magic strings duplicated between page modules and 6 test files. Changing a
  label required updating tests in multiple files. Fix: centralized into `data.py`
  constants (`Routes`, `PageTitles`, `Labels`, `ApiRoutes`).
- Health computation (exit code mapping, deploy history analysis) was embedded in
  dashboard.py UI functions. Couldn't test health logic without NiceGUI. Fix:
  extracted `Host`/`Fleet` domain classes into `data.py` with `.healthy`/`.errors`
  properties. Dashboard became a thin renderer. 29 pure-Python tests cover all
  health scenarios.
- Redundant `"Host"` forward reference in `kickstart_callhome(host: "Host")` when
  `from __future__ import annotations` is already at the top. With postponed
  annotations, all annotations are strings by default — quoting is unnecessary.
- `kickstart_callhome` returned `success=True` even with partial container restart
  failures (errors collected in `errors` list). This is intentional — `success`
  means "SSH connected and discovered containers"; `errors` captures per-container
  issues. Document this semantic in the docstring when `success` has a nuanced
  meaning.
- `HostRegistry.register()` is the single write path to `registry.json`. NEVER
  duplicate upsert logic. Every caller (env seeding, manual form, TEST_UNITS)
  delegates to `register()`. Immutable-on-create fields (bucket, source,
  first_seen) are preserved on upsert. MAC match takes precedence over name
  match for identity resolution.
- NEVER register container/service heartbeats into HostRegistry. The registry
  is for PHYSICAL HOSTS only (Proxmox nodes). Container heartbeats go into
  `nodes.json` (telemetry) via `register_checkin()`. `build_fleet()` groups
  container telemetry under parent hosts — containers never appear as
  independent fleet members. Previous bug: `register_checkin()` called
  `HostRegistry.register()` for every heartbeat. Containers (pihole,
  wireguard, netdata, etc.) appeared as independent hosts on the dashboard
  with LAN IPs (10.10.10.x) instead of being grouped under their physical
  host. Fixed by removing the `HostRegistry.register()` call from
  `register_checkin()`.
- 4-tier architecture: Container → NodeManager → ClusterManager → SuperManager.
  Containers heartbeat to their local NodeManager. NodeManagers aggregate and
  relay to their ClusterManager. ClusterManagers relay to the SuperManager.
  NEVER let containers POST directly to the SuperManager's `/api/checkin`
  in the final architecture.
- NEVER hardcode ports. Use `WEBUI_PORT` env var (default 40500). Ports in the
  ephemeral range (32768-60999) can collide with outbound connections. Choose a
  port in the firewall's allowed range and set it in `.env`/`test.env`.
- NEVER duplicate `build.py:get_controller_ip()`. Import from `build` module.
- Deploy output MUST persist to a log file (`.state/deploy_output.log`), not just
  stream to a NiceGUI `ui.log` element. Browser disconnects kill the coroutine
  and lose all output. The log file is the source of truth.
- NiceGUI UI callbacks (`log.push`, `label.text = ...`) throw `RuntimeError` when
  the browser session is gone. Use a `_safe_ui(fn, *args)` helper to wrap these
  in a single `try/except RuntimeError`. NEVER scatter bare try/except blocks.
- When importing `Labels` in a page module, ALWAYS use the constants for button text
  instead of hardcoding strings. If you import `Labels` but use hardcoded strings,
  the import is dead AND the string is untracked. Previous bug: `bridge.py` and
  `images.py` imported `Labels` but used hardcoded `"Refresh Now"`, `"Build Selected"`,
  etc. — found by dead-import audit, fixed by replacing strings with constants.
- NEVER import a name and leave it unused. If the page needs `Labels.REFRESH_NOW`,
  import `Labels` AND use it. If it doesn't need Labels, don't import it.
- Dead code audit checklist: (1) unused imports, (2) functions defined but never
  called, (3) local variables assigned but never read, (4) stale docstrings
  mentioning old port numbers or URLs, (5) backward-compat fallbacks for removed
  features.

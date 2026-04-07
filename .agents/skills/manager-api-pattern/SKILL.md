# Manager API Pattern

## Purpose

Standard pattern for runtime container operations in the vm_builds fleet.
Three strict layers with no shortcuts between them.

## Three-Tier Hierarchy (MANDATORY)

```
┌─────────────────────────────────────────────────────────────┐
│  Super Manager (NiceGUI Web UI)                             │
│  External users, dashboards, kiosk pages                    │
│  ONLY talks to: Manager API (HTTP on localhost:9001)        │
│  NEVER: SSH, pct exec, or direct container access           │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (localhost)
┌────────────────────────▼────────────────────────────────────┐
│  Manager (manager.py REST endpoints)                        │
│  Receives HTTP requests, executes operations                │
│  ONLY talks to: Container-side scripts via SSH              │
│  Hypervisor ops (pct/qm) go to the Proxmox host            │
│  Metric collectors use container scripts when available     │
└────────────────────────┬────────────────────────────────────┘
                         │ SSH
┌────────────────────────▼────────────────────────────────────┐
│  Container-side scripts (baked into image)                  │
│  wifi_setup.sh, batman_trigger.sh                           │
│  Self-contained: detect hardware, apply config, report      │
│  KEY=value output for programmatic parsing                  │
└─────────────────────────────────────────────────────────────┘
```

### Rules for each tier

**Super Manager (UI pages in `scripts/webui/pages/`):**
- NEVER import or call `heartbeat._ssh_exec`. NEVER run shell commands.
- ALL mutations go through `httpx.AsyncClient` to `{get_api_base_url()}/api/...`
- Status reads come from the manager's metric cache (subscriptions) or API queries.

**Manager (`scripts/webui/manager.py`):**
- ALL container operations go through container-side scripts when available.
- NEVER embed inline shell logic (UCI commands, `iw` calls) that should be in a script.
- `heartbeat._ssh_exec()` for SSH. `resolve_node_ip()` for IP resolution.
- Hypervisor operations (`pct`, `qm`) target the Proxmox host IP, not containers.

**Container-side scripts (baked into image at `/usr/sbin/`):**
- Self-contained — detect PHYs, validate modes, apply config, restart services.
- Called identically by Ansible (initial deploy) and manager (runtime).
- `status` and `metrics` subcommands: no auth, `KEY=value` output.
- Mutation subcommands: may require HMAC auth (batman) or not (wifi mode).

## Initial Deploy vs Runtime

```
Initial deploy (Ansible):
  host_vars → configure role → pct_remote → container-side script

Runtime (Manager API):
  UI page → HTTP → Manager endpoint → SSH → container-side script
  Heartbeat/callhome → /api/checkin → fleet readiness gate
```

Both paths call the SAME container-side script. The script is baked into the
image and handles all mode-specific logic.

## Container-side script pattern

Every runtime-configurable feature gets a shell script baked into the image:

| Script | Purpose | Subcommands |
|--------|---------|-------------|
| `wifi_setup.sh` | WiFi WDS mode (AP/STA) | `configure`, `switch-mode`, `restart`, `status`, `metrics` |
| `batman_trigger.sh` | batman-adv mesh overlay | `enable`, `disable`, `status` |

### Script conventions

- Location: `/usr/sbin/` inside the container
- MUST be executable and work with BusyBox ash
- `status` subcommand: no auth required, outputs `KEY=value` lines
- Mutation subcommands: require HMAC auth token (via `/etc/batman_key`)
- MUST be idempotent — safe to call repeatedly
- MUST handle missing prerequisites with clear error messages

### Adding a new container-side script

1. Create the script in `scripts/image-builder/files-mesh-lxc/usr/sbin/`
2. The OpenWrt Image Builder includes all files from the `FILES` directory
3. Add manager API endpoints (mutation + status) in `manager.py`
4. Add unit tests in `tests/test_webui_app.py`
5. Add `wifi_setup.sh status` / equivalent to molecule verify assertions

## Manager API endpoints

### Mutation endpoints (require auth)

- `POST /api/wifi/mode/{node}/{mode}` — switch WiFi AP/STA mode
- `POST /api/batman/enable` — enable batman-adv on all mesh/bridge nodes
- `POST /api/batman/disable` — disable batman-adv
- `POST /api/bridge/restart-wifi` — restart WiFi on bridge nodes
- `POST /api/guests/{vmid}/{action}` — start/stop/restart container or VM

All mutation endpoints:
- Require `x-callhome-token` header when `CALLHOME_PRIVATE_KEY` is set
- Validate input before performing any SSH operations
- Return `{"success": bool, "output": str}` format
- Use `heartbeat._ssh_exec()` for SSH operations

### Status endpoints (no auth)

- `GET /api/wifi/status/{node}` — query WiFi mode/radio/interface state
- `GET /api/batman/status` — batman-adv status across all nodes
- `GET /api/container/{id}/ready` — container health from callhome data
- `GET /api/fleet/ready` — fleet-wide readiness gate
- `GET /api/nodes` — all registered nodes

### Adding a new endpoint

1. Define the handler function inside `register_api()` in `manager.py`
2. Mutation: add `_check_mutation_auth()` call at the top
3. Use `resolve_node_ip()` to find the target IP
4. Use `heartbeat._ssh_exec()` for SSH commands
5. Register the route with `starlette_app.routes.insert(0, Route(...))`
6. Add tests in `tests/test_webui_app.py`

## Fleet readiness gate (verify.yml)

The E2E verify playbook uses the manager API as the primary path for
container liveness checks, with SSH fallback:

```yaml
# Fleet readiness gate
- name: Check fleet API readiness
  ansible.builtin.uri:
    url: "{{ _api_base }}/api/fleet/ready?services=pihole,rsyslog,..."
  register: _fleet_check
  failed_when: false

- name: Set fleet API ready flag
  ansible.builtin.set_fact:
    _fleet_api_ready: "{{ _fleet_check.status | default(0) == 200 }}"

# Per-service: API when fleet ready, SSH fallback
- name: Check service health via API
  ansible.builtin.uri:
    url: "{{ _api_base }}/api/container/pihole/ready"
  when: _fleet_api_ready | bool

- name: Check service health via SSH (fallback)
  ansible.builtin.shell:
    cmd: pct exec {{ ct_id }} -- ...
  when: not (_fleet_api_ready | bool)
```

### When to use the fleet API (primary path)

- Container liveness checks (is the service running?)
- Service health queries (extensions, systemd_services)
- Readiness gates before functional tests

### When SSH stays (not replaced by API)

- Hypervisor operations: `pct config`, `pct status`, `qm config`, `qm agent`
- Host infrastructure: bridges, IOMMU, iGPU, backup manifests
- L3 integration tests: cross-container connectivity, DNS resolution
- OpenWrt-specific deep checks: UCI config, `iw` radio state
- QEMU Guest Agent operations

## Subscription model (heartbeat.py)

The manager polls nodes via SSH on-demand when a UI page subscribes:

```python
# _COLLECTOR_MAP defines available metric types
_COLLECTOR_MAP = {
    "wifi": collect_wifi_metrics,
    "bridge": collect_bridge_metrics,
    "router": collect_router_metrics,
    "mesh": collect_mesh_metrics,
    "batman": collect_batman_metrics,
}
```

### Adding a new collector

1. Write `collect_<type>_metrics(ip)` in `heartbeat.py`
2. Return a `HeartbeatCache` with structured data
3. Add to `_COLLECTOR_MAP` in `manager.py`
4. The poller automatically picks up new types when subscribed

### Subscription lifecycle

1. UI page calls `POST /api/heartbeat/subscribe` with `node_id` and `metric_type`
2. Poller runs the collector every 5s while subscription is active
3. Results cached in `MetricCache`, queryable via `GET /api/heartbeat/{node}/{type}`
4. Subscription expires after TTL (default 30s); UI renews on each page visit
5. `cleanup_expired()` removes stale subscriptions

## Rules

### Three-tier enforcement
- NEVER let UI pages (super manager) SSH to containers. ALL operations go
  through the manager API via HTTP. No exceptions.
- NEVER embed inline shell logic in manager endpoints when a container-side
  script exists. If you need `uci set`, `iw`, `wifi down/up` — put it in a
  script, bake it into the image, and call the script from the manager.
- NEVER add a container-side operation without a corresponding manager API
  endpoint. The UI must be able to trigger it via HTTP.

### Image and scripts
- ALWAYS bake scripts into the image at `/usr/sbin/`. Runtime operations use
  baked-in scripts, not ad-hoc SSH commands with inline shell logic.
- Container-side scripts MUST use `KEY=value` output format for status/metrics
  so the manager can parse results programmatically.
- ALWAYS add `status` and/or `metrics` subcommands to container scripts so
  heartbeat collectors can use them instead of raw tool output.
- When adding a new runtime feature: create the script first, then the manager
  endpoint, then the UI integration.

### Manager conventions
- ALWAYS use `heartbeat._ssh_exec()` for SSH from the manager.
- NEVER add mutation endpoints without `_check_mutation_auth()`.
- Heartbeat collectors (`collect_*_metrics()`) SHOULD use container-side
  scripts (`wifi_setup.sh metrics`) as their primary data source, falling
  back to raw `iw`/`uci` for containers without the script (e.g., router VM).

### Verify conventions
- NEVER poll containers directly from verify.yml without the
  `_fleet_api_ready` gate. New services should follow the dual-path pattern.
- NEVER add a new "SSH to container for status" pattern without first checking
  if the fleet API or a container-side script already provides the data.

### Previous bugs
- Manager `bridge/restart-wifi` used raw `wifi down && wifi up` instead of
  `wifi_setup.sh restart`. When the script got a bug fix, the raw command
  in the manager didn't benefit. Fixed by calling the script.
- Heartbeat `collect_wifi_metrics` used 3 separate SSH calls (iw dev, station
  dump, uci show) instead of one `wifi_setup.sh metrics` call. Added script
  as primary data source with raw fallback for nodes without the script.

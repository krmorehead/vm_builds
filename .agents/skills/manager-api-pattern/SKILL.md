# Manager API Pattern

## Purpose

Standard pattern for runtime container operations in the vm_builds fleet.
Four strict tiers with no shortcuts between them.

## What is a Cluster?

A **cluster** is a single household's network. One router node creates a
LAN subnet (10.10.10.x) via OpenWrt. Multiple Proxmox nodes join this
subnet via wired connections and WiFi mesh. All nodes converge onto the
same flat L2/L3 network once the mesh is fully established.

- The **router node** (e.g., home) is always the Cluster Manager
- Child nodes (mesh1, ai, mesh2, bridge-1, bridge-2) each run a Node Manager
- The cluster is tightly managed by a single end user
- A national/remote host is a single-node cluster (its own Cluster Manager)

The **SuperManager** sits above all clusters on the operator's workstation,
providing global visibility across local and remote clusters.

## Four-Tier Hierarchy (MANDATORY)

```
┌─────────────────────────────────────────────────────────────┐
│  SuperManager (app.py)                                      │
│  Global fleet view. Aggregates heartbeats from all Cluster  │
│  Managers. Logs cluster-level events. Shows ALL clusters.   │
│  Extends ClusterManager with global visibility.             │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP relay
┌────────────────────────▼────────────────────────────────────┐
│  ClusterManager (kiosk_server.py, IS_CLUSTER_MANAGER=true)  │
│  Subnet-scoped fleet view. Same UI as SuperManager but      │
│  scoped to its cluster. Accepts heartbeats from child       │
│  Managers. Broadcasts events DOWN. Relays UP to Super.      │
│  Fleet-level ops (batman, bridge/wifi across nodes) HERE.   │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP relay / event broadcast
┌────────────────────────▼────────────────────────────────────┐
│  NodeManager (kiosk_server.py, default)                     │
│  Per-host container management. Relays heartbeats UP to     │
│  ClusterManager. Receives broadcast events. LOCAL ops only. │
│  NEVER calls get_mesh_nodes() or get_bridge_nodes().        │
└────────────────────────┬────────────────────────────────────┘
                         │ SSH / pct exec
┌────────────────────────▼────────────────────────────────────┐
│  Container-side scripts (baked into image at /usr/sbin/)    │
│  wifi_setup.sh, batman_trigger.sh                           │
│  Self-contained: detect hardware, apply config, report      │
│  KEY=value output for programmatic parsing                  │
└─────────────────────────────────────────────────────────────┘
```

## Class Hierarchy (manager.py)

```
BaseManager — heartbeat polling, metric cache, relay heartbeat, SSH helper
  ├─ NodeManager — single-host scope, local batman, guest mgmt, event receiver
  └─ ClusterManager(NodeManager) — fleet view, event broadcast, fleet storage
```

- SuperManager = app.py using ClusterManager with `include_fleet_storage=False`
  (app.py has its own nodes.json-backed fleet storage but feeds `_fleet_nodes`
  from check-ins for batman broadcasting).
- kiosk_server.py uses NodeManager (default) or ClusterManager
  (`IS_CLUSTER_MANAGER=true`, `include_fleet_storage=True`).

### Child Manager discovery
ClusterManager receives its child Manager IPs via `CHILD_MANAGER_IPS` in
config.json — a dict mapping host names to routable IPs.

**Two topology phases determine the correct IP:**

1. **Pre-mesh / bootstrap**: Not all hosts are on the LAN yet. WAN hosts
   (ai, mesh2, bridge-1, bridge-2) have kiosk containers on private NAT
   subnets (10.99.x.x) that are unreachable from the LAN. For these
   hosts, `CHILD_MANAGER_IPS` uses the Proxmox **host IP** (192.168.86.x)
   with iptables DNAT rules forwarding port 9001 to the container.
   LAN hosts (mesh1) use the container IP directly (10.10.10.x).

2. **Post-mesh**: Once the WiFi mesh is fully established, ALL hosts
   converge onto 10.10.10.x. The Cluster Manager can reach all child
   Managers on their container IPs — no DNAT needed.

The `kiosk_configure` role builds `CHILD_MANAGER_IPS` dynamically:
- LAN hosts (`router_nodes` or `lan_hosts`) → `kiosk_static_ip` (container IP)
- WAN hosts (all others) → `ansible_host` (Proxmox host IP, with DNAT)

The `kiosk_lxc` role deploys DNAT rules on WAN hosts:
- `iptables -t nat -A PREROUTING -i $WAN_IF -p tcp --dport 9001 -j DNAT --to $CT_IP:9001`
- `iptables -A FORWARD -d $CT_IP -p tcp --dport 9001 -j ACCEPT`

Previous bug (2026-04-09): `CHILD_MANAGER_IPS` used container NAT IPs
(10.99.x.19) for WAN hosts. The Cluster Manager on the LAN couldn't reach
them — connection refused/timed out. Fix: use host IPs with DNAT.

### Event broadcasting pattern (batman)
ClusterManager.batman_fleet() uses a two-phase approach:
1. Phase 1 (local): execute on router VM + this node's containers directly
2. Phase 2 (broadcast): POST event to each child Manager's
   `/api/manager/events` endpoint. NodeManagers dispatch locally.

Batman status keys are host-qualified (e.g., `home/router-100`,
`mesh1/mesh-103`) to prevent key collisions when multiple hosts have
containers with the same VMID. Container discovery uses `pct status`
to only probe containers that actually exist and are running.

This replaces the old pattern where the ClusterManager SSHed directly to
every host in the fleet. Now each Manager executes only on its own host.

### Relay topology
- NodeManager → ClusterManager: via MANAGEMENT_SERVER config in config.json
- ClusterManager → SuperManager: via MANAGEMENT_SERVER config in config.json
- ClusterManager.build_relay_payload() includes `cluster_nodes` summary
  from `_fleet_nodes` so the SuperManager sees the full cluster picture.

### Relay debugging tips
- The relay loop in `BaseManager._relay_heartbeat()` runs every 30 seconds.
  It logs at DEBUG level on success, WARNING on failure.
- If the relay is WORKING, **no logs appear** at default journald level.
  Check the SuperManager's `/api/nodes` timestamps instead of looking for
  relay log entries.
- SuperManager timestamps use the **controller's local timezone** (e.g.,
  PDT), NOT UTC. A `last_seen` of `13:25:00` when local time is `1:25 PM`
  is CURRENT, not 7 hours stale. Always compare against `date` output.
- The relay collects host metrics via SSH (`_collect_host_metrics`) before
  POSTing to the SuperManager. If SSH to the Proxmox host fails (e.g.,
  kiosk user has no keys), metrics default to zero but the relay still
  posts. The `kiosk` user's SSH keys are deployed by kiosk_configure.
- The `CALLHOME_SERVER` in `/etc/default/callhome` is the **local Manager
  URL** (e.g., `http://10.10.10.22:9001`), NOT the SuperManager. Containers
  heartbeat to their local Manager. The Manager relays UP via
  `MANAGEMENT_SERVER` in `config.json`.
- After `molecule converge` updates config.json, the `kiosk-web` service is
  restarted and reads the new config. Verify by checking `journalctl -u
  kiosk-web` for a fresh "Started" entry.
- Previous debugging session (2026-04-09): relay appeared non-functional
  because timestamps looked stale. They were actually current — the
  SuperManager was in PDT (UTC-7). ~30 minutes of debugging wasted.

## Strict Configuration (MANDATORY)

NEVER silently fall back through multiple config sources. Every manager
instance receives its required config at construction time. If a required
value is missing, fail immediately with a clear error.

- BaseManager.__init__() takes a config dict with required keys.
- NodeManager requires: HOST_IP, HOST_NAME.
- ClusterManager requires: HOST_IP, HOST_NAME, MESH_KEY.
- NEVER read os.environ as a fallback inside methods. Config comes from __init__().
- NEVER silently return empty strings for missing config. Raise ValueError.

### Rules for each tier

**SuperManager (app.py):**
- Extends ClusterManager with global fleet visibility.
- Fleet endpoints (/api/nodes, /api/fleet/*) scoped to ALL clusters.
- Logs cluster-level events but does NOT act on them.

**ClusterManager (kiosk_server.py, IS_CLUSTER_MANAGER=true):**
- Fleet-level operations (batman across all nodes, bridge/wifi management) live HERE.
- Calls get_mesh_nodes(), get_bridge_nodes() — ONLY this tier and above.
- Broadcasts events DOWN to child Managers. Relays UP to SuperManager.
- Same UI pages as SuperManager but scoped to its subnet.

**NodeManager (kiosk_server.py, default):**
- Per-host container ops ONLY. Knows its own HOST_IP and containers.
- NEVER calls get_mesh_nodes() or get_bridge_nodes(). NEVER iterates other hosts.
- Receives broadcast events from ClusterManager, executes locally.
- Relays heartbeats UP to ClusterManager (not SuperManager directly).

**Super Manager UI pages (scripts/webui/pages/):**
- NEVER import or call `heartbeat._ssh_exec`. NEVER run shell commands.
- ALL mutations go through `httpx.AsyncClient` to `{get_api_base_url()}/api/...`
- Status reads come from the manager's metric cache (subscriptions) or API queries.

**Container-side scripts (baked into image at `/usr/sbin/`):**
- Self-contained — detect PHYs, validate modes, apply config, report.
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

### NodeManager endpoints (per-host)

Registered by NodeManager.register_api():

- `POST /api/batman/local/{action}` — enable/disable batman on THIS node's containers
- `GET /api/batman/local/status` — batman status on THIS node's containers
- `POST /api/manager/events` — receive broadcast events from ClusterManager
- `GET /api/guests` — list local containers/VMs
- `POST /api/guests/{vmid}/{action}` — start/stop/restart local container/VM
- `POST /api/heartbeat/subscribe` — subscribe to metric polling for a node
- `DELETE /api/heartbeat/subscribe/{id}` — unsubscribe
- `GET /api/heartbeat/{node}/{type}` — get cached metrics
- `POST /api/checkin` — receive container heartbeats (when MANAGEMENT_SERVER set)

### ClusterManager endpoints (fleet-level)

Registered by ClusterManager.register_api() (in addition to NodeManager):

- `POST /api/batman/enable` — enable batman across ALL nodes (broadcast)
- `POST /api/batman/disable` — disable batman across ALL nodes (broadcast)
- `GET /api/batman/status` — batman status from all nodes in cluster
- `POST /api/bridge/restart-wifi` — restart WiFi on bridge nodes
- `POST /api/wifi/mode/{node}/{mode}` — switch WiFi AP/STA mode
- `GET /api/wifi/status/{node}` — query WiFi mode/radio/interface state
- `POST /api/cluster/events` — receive events from SuperManager/external

When `include_fleet_storage=True` (kiosk_server.py ClusterManager):
- `POST /api/checkin` — accept heartbeats from child Managers
- `GET /api/nodes` — all nodes in this cluster
- `GET /api/fleet/ready` — cluster-scoped readiness gate

### SuperManager endpoints (app.py)

app.py registers its own persistent fleet storage routes before
ClusterManager routes (with `include_fleet_storage=False`):
- `POST /api/checkin` — persists to nodes.json + feeds `_fleet_nodes`
- `GET /api/nodes` — from nodes.json
- `GET /api/fleet/ready` — from nodes.json
- `GET /api/fleet/stale` — circuit breaker
- `GET /api/fleet/health` — summary

All mutation endpoints:
- Require `x-callhome-token` header when `CALLHOME_PRIVATE_KEY` is set
- Validate input before performing any SSH operations
- Return `{"success": bool, "output": str}` format
- Use `heartbeat._ssh_exec()` for SSH operations

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

### Four-tier enforcement
- NEVER let UI pages (super manager) SSH to containers. ALL operations go
  through the manager API via HTTP. No exceptions.
- NEVER embed inline shell logic in manager endpoints when a container-side
  script exists. If you need `uci set`, `iw`, `wifi down/up` — put it in a
  script, bake it into the image, and call the script from the manager.
- NEVER add a container-side operation without a corresponding manager API
  endpoint. The UI must be able to trigger it via HTTP.
- NEVER put fleet-level operations on NodeManager. batman_fleet(),
  get_mesh_nodes(), get_bridge_nodes() belong on ClusterManager only.
- NEVER let NodeManager communicate with other hosts' Managers. Only
  ClusterManager broadcasts events to child Managers.

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

### Strict configuration (CRITICAL)
- NEVER use try/except fallback chains to resolve config values. Every
  manager instance receives ALL required config at construction time.
- NEVER silently return empty strings for missing required config. If
  HOST_IP is needed and missing, raise immediately — do not return "" and
  let a downstream SSH call fail with a confusing error.
- NEVER read os.environ as a fallback inside runtime methods. Config comes
  from the constructor. The caller (app.py, kiosk_server.py) is responsible
  for building the config dict from its own sources (env file, config.json).
- Previous bug: get_host_ip() had a 3-layer fallback chain (config dict →
  app.storage → os.environ) that returned "" when all three missed. The
  empty string propagated through _ssh_exec as an invalid host, producing
  "ssh: Could not resolve hostname : Name or service not known" — a
  confusing error 5 layers removed from the actual problem (missing config).

### Tier separation (CRITICAL)
- NodeManager NEVER calls get_mesh_nodes() or get_bridge_nodes(). Those
  are fleet-level queries that only ClusterManager and SuperManager use.
- A NodeManager only operates on containers identified by VMID on its own
  host. It does not know about other hosts in the cluster.
- When adding a new fleet-level operation, put it on ClusterManager. When
  adding a per-host operation, put it on NodeManager.
- Previous bug: batman_toggle() in the flat manager.py iterated
  get_mesh_nodes() + get_bridge_nodes() and SSHed to every host. This ran
  on every kiosk_server.py instance (per-host managers), not just the
  cluster/super manager. Every Manager was trying to orchestrate the
  entire fleet.

### Verify conventions
- NEVER poll containers directly from verify.yml without the
  `_fleet_api_ready` gate. New services should follow the dual-path pattern.
- NEVER add a new "SSH to container for status" pattern without first checking
  if the fleet API or a container-side script already provides the data.

### Testing the Manager hierarchy

- NEVER fabricate heartbeats (curl /api/checkin) to test the Cluster Manager
  dashboard. Start REAL kiosk_server instances on REAL hosts and let REAL
  containers heartbeat. Fabricated heartbeats test JSON rendering, not the
  actual heartbeat relay chain.
- NEVER claim batman mode works without engaging the batman toggle on the
  GUI and verifying the REAL batman_trigger.sh executed on REAL containers.
- NEVER claim "5 child Managers visible" when those entries were curl'd into
  existence. Real child Managers are kiosk_server instances on physical hosts.
- When manual testing the Cluster Manager, deploy kiosk containers first
  (molecule converge), then test every interactive feature against real hardware.

### Callhome URL vs Management Server (CRITICAL distinction)
- `CALLHOME_SERVER` (in `/etc/default/callhome`) = URL of the LOCAL Manager
  on the same host. Containers heartbeat here. Written by the converge via
  the callhome play targeting all running containers.
- `MANAGEMENT_SERVER` (in `/opt/kiosk/config.json`) = URL of the UPSTREAM
  tier. NodeManagers relay to the ClusterManager; ClusterManagers relay to
  the SuperManager. Written by `kiosk_configure`.
- `.state/callhome_url` = controller-side file that `prepare.yml` and
  `build.py` write with the SuperManager URL. This is read by the
  `kiosk_configure` role and becomes `MANAGEMENT_SERVER` in config.json.
- NEVER hardcode any of these URLs. They are all dynamically detected.
- NEVER patch `/etc/default/callhome` or `config.json` on running
  containers. Update the build scripts or role defaults, rebuild images
  if needed, and run `molecule converge` to push correct config.

### Previous bugs
- Manager `bridge/restart-wifi` used raw `wifi down && wifi up` instead of
  `wifi_setup.sh restart`. When the script got a bug fix, the raw command
  in the manager didn't benefit. Fixed by calling the script.
- Heartbeat `collect_wifi_metrics` used 3 separate SSH calls (iw dev, station
  dump, uci show) instead of one `wifi_setup.sh metrics` call. Added script
  as primary data source with raw fallback for nodes without the script.
- (2026-04-09) Agent fabricated 5 child Manager heartbeats via curl during
  "manual testing" of the Cluster Manager. Dashboard rendered correctly
  because it was fed valid JSON. Batman mode was never actually triggered.
  Bridge WiFi restart was never tested. The entire manual test was theater
  that proved nothing about real functionality.

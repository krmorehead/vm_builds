# Manual Testing Playbooks

Step-by-step runbooks for verifying the 4-tier system on real hardware.
Every command targets real infrastructure — no mocks, no fabricated data.

## CRITICAL: Fully Converged State Required

Manual testing MUST run against a fully converged system. This means:

1. `molecule test` (or `molecule converge`) has completed successfully
2. ALL 6 hosts are on the 10.10.10.x LAN (mesh established)
3. ALL containers and VMs are deployed, configured, and heartbeating
4. The SuperManager API is running and receiving heartbeats from all nodes
5. ALL child Managers are visible in the Cluster Manager fleet

If the system is NOT in this state, run `molecule test` first. NEVER
start manual testing against a partially deployed or pre-mesh system.
Testing in a pre-mesh state produces a wall of "No route to host" errors
that prove nothing and waste time.

Previous catastrophe (2026-04-10): Agent started manual testing in pre-mesh
state. Every playbook that touched WAN hosts returned "No route to host."
The entire session was wasted verifying expected failures instead of testing
real functionality. The fix: ALWAYS converge first, test second.

## Prerequisites

```bash
set -a && source test.env && set +a
source .venv/bin/activate
```

### Step 0: Ensure fully converged state

If the system is not already converged from a recent `molecule test`:

```bash
molecule test
```

This runs cleanup → converge → verify and leaves all infrastructure deployed.

### Step 1: Verify all 6 hosts reachable

```bash
for h in $PRIMARY_HOST $AI_HOST $MESH_2_HOST $BRIDGE_1_HOST $BRIDGE_2_HOST; do
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@$h "hostname" 2>&1
done
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" root@10.10.10.210 "hostname"
```

If ANY host is unreachable: FULL STOP. Do not proceed.

### Step 2: Verify all containers are running

```bash
for pair in "$PRIMARY_HOST:home" "$AI_HOST:ai" "$MESH_2_HOST:mesh2" \
            "$BRIDGE_1_HOST:bridge-1" "$BRIDGE_2_HOST:bridge-2"; do
  host="${pair%%:*}"; label="${pair##*:}"
  echo "=== $label ==="
  ssh -o StrictHostKeyChecking=no root@$host "pct list" 2>&1
done
```

If ANY expected container is missing or stopped: run `molecule converge` first.

### Step 3: Pre-flight app functionality check (MANDATORY)

This catches broken app launches, unreachable services, and iframe
embedding issues BEFORE wasting time in the browser. Run ALL checks.

```bash
echo "=== Hub App Pre-flight Check ==="

# 3a. Kiosk API responds
for pair in "$PRIMARY_HOST:home:401"; do
  host="${pair%%:*}"; rest="${pair#*:}"; label="${rest%%:*}"; ctid="${rest##*:}"
  echo -n "$label kiosk API: "
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec $ctid -- python3 -c \"
import urllib.request, json
r = urllib.request.urlopen('http://localhost:9001/api/guests', timeout=5)
data = json.loads(r.read())
print(f'{len(data.get(\\\"guests\\\", []))} guests found')
\"" 2>/dev/null || echo "FAILED"
done

# 3b. Display apps can be started (should return success even if already running)
echo ""
echo "=== Display App Launch Tests ==="
# home: Kodi (301) and Desktop (400)
for pair in "301:Kodi" "400:Desktop"; do
  vmid="${pair%%:*}"; name="${pair##*:}"
  echo -n "home $name (VMID $vmid): "
  ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
    "pct exec 401 -- python3 -c \"
import urllib.request, json
req = urllib.request.Request('http://localhost:9001/api/guests/$vmid/start', method='POST')
r = urllib.request.urlopen(req, timeout=10)
d = json.loads(r.read())
print('OK' if d.get('success') else f'FAIL: {d.get(\\\"output\\\", d.get(\\\"error\\\"))}')
\"" 2>/dev/null || echo "FAILED"
done
# mesh1: Moonlight (302)
echo -n "mesh1 Moonlight (VMID 302): "
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 \
  "pct exec 401 -- python3 -c \"
import urllib.request, json
req = urllib.request.Request('http://localhost:9001/api/guests/302/start', method='POST')
r = urllib.request.urlopen(req, timeout=10)
d = json.loads(r.read())
print('OK' if d.get('success') else f'FAIL: {d.get(\\\"output\\\", d.get(\\\"error\\\"))}')
\"" 2>/dev/null || echo "FAILED"

# 3c. External service URLs are reachable from kiosk
echo ""
echo "=== Service URL Reachability (from kiosk) ==="
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- python3 -c \"
import urllib.request, json
config = json.load(open('/opt/kiosk/config.json'))
services = [
    ('Home Assistant', config.get('HOMEASSISTANT_URL', '')),
    ('Jellyfin',       config.get('JELLYFIN_URL', '')),
    ('Pi-hole',        config.get('PIHOLE_URL', '')),
    ('Netdata',        config.get('NETDATA_URL', '')),
    ('Kodi',           config.get('KODI_URL', '')),
]
for name, url in services:
    if not url:
        print(f'  {name:20s} NOT CONFIGURED (empty URL)')
        continue
    try:
        r = urllib.request.urlopen(url, timeout=5)
        print(f'  {name:20s} HTTP {r.status} OK')
    except Exception as e:
        print(f'  {name:20s} FAIL: {e}')
\"" 2>/dev/null

# 3d. X-Frame-Options check (services that block iframes break the viewer page)
echo ""
echo "=== X-Frame-Options Check (iframe blocking) ==="
for pair in "10.10.10.14:8123:HomeAssistant" "10.10.10.15:8096:Jellyfin" \
            "10.10.10.10:80:Pi-hole" "10.10.10.41:19999:Netdata"; do
  ip_port="${pair%:*}"; name="${pair##*:}"
  echo -n "  $name: "
  xfo=$(ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
    "curl -sI http://$ip_port 2>/dev/null | grep -i 'x-frame-options'" 2>/dev/null)
  if [ -n "$xfo" ]; then
    echo "BLOCKED — $xfo (will fail in kiosk iframe)"
  else
    echo "OK (no X-Frame-Options)"
  fi
done

# 3e. Router SSH reachability from kiosk (metric collection path)
echo ""
echo "=== Router Metric Collection Path ==="
echo -n "  Kiosk -> Router SSH: "
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- ssh -o StrictHostKeyChecking=no -o ConnectTimeout=3 root@10.10.10.1 \
   'echo REACHABLE; uci get system.@system[0].hostname 2>/dev/null'" 2>/dev/null || echo "FAILED"

# 3f. Display service status on ALL hosts (kiosk-display on CT 401)
echo ""
echo "=== Kiosk Display Services (all hosts) ==="
for pair in "$PRIMARY_HOST:home" "$AI_HOST:ai" "$MESH_2_HOST:mesh2" \
            "$BRIDGE_1_HOST:bridge-1" "$BRIDGE_2_HOST:bridge-2"; do
  host="${pair%%:*}"; label="${pair##*:}"
  echo -n "  $label kiosk-display: "
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- systemctl is-active kiosk-display 2>/dev/null" 2>/dev/null
done
echo -n "  mesh1 kiosk-display: "
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 401 -- systemctl is-active kiosk-display 2>/dev/null" 2>/dev/null

# 3g. App-specific display services (Desktop, Kodi, Moonlight)
echo ""
echo "=== App Display Services ==="
echo -n "  home desktop-display (CT 400): "
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 400 -- systemctl is-active desktop-display 2>/dev/null" 2>/dev/null || echo "inactive/missing"
echo -n "  home kodi display (CT 301): "
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct status 301 2>/dev/null" 2>/dev/null || echo "not found"
echo -n "  mesh1 moonlight display (CT 302): "
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct status 302 2>/dev/null" 2>/dev/null || echo "not found"
```

**Expected**: ALL checks pass. Any FAIL or BLOCKED result MUST be fixed before
proceeding to browser-based testing. Common failures:

| Failure | Root Cause | Fix |
|---------|-----------|-----|
| Display app "FAIL: already running" | Manager API doesn't treat running as success | Update `manager.py` `_api_guest_action` |
| Service URL FAIL: Connection refused | Service not listening or wrong IP in config | Check container networking, restart service |
| X-Frame-Options BLOCKED | Service sends SAMEORIGIN header | Add `use_x_frame_options: false` to service config |
| Router SSH FAILED | Kiosk can't reach router at 10.10.10.1 | Check LAN routing, SSH keys |
| Display service not active | KasmVNC crashed | `journalctl -u kiosk-display` inside container |

## Playbook 1: Verify Kiosk Containers Running

Check CT 401 (kiosk) is running and kiosk-web is active on every host.

```bash
for pair in "$PRIMARY_HOST:direct" "$AI_HOST:direct" "$MESH_2_HOST:direct" "$BRIDGE_1_HOST:direct" "$BRIDGE_2_HOST:direct"; do
  host="${pair%%:*}"
  echo -n "$host: "
  ssh -o StrictHostKeyChecking=no root@$host "pct status 401 && pct exec 401 -- systemctl is-active kiosk-web" 2>&1
done
echo -n "mesh1: "
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct status 401 && pct exec 401 -- systemctl is-active kiosk-web" 2>&1
```

**Expected**: All 6 show `status: running` and `active`.

## Playbook 2: Verify Cluster Manager Fleet

The Cluster Manager runs on home's kiosk (10.10.10.23:9001). Access via SSH tunnel:

```bash
ssh -o StrictHostKeyChecking=no -L 9099:10.10.10.23:9001 root@$PRIMARY_HOST -N -f
```

Query the fleet:

```bash
curl -s http://localhost:9099/api/nodes | python3 -c "
import json, sys
data = json.load(sys.stdin)
nodes = data.get('nodes', [])
print(f'Total nodes: {len(nodes)}')
for n in nodes:
    nid = n['node_id']
    ips = n.get('local_ips', [])
    ready = n.get('container_health', {}).get('ready', '?')
    print(f'  {nid:20s}  {str(ips):30s}  ready={ready}')
"
```

**Expected**: ALL containers across ALL 6 hosts appear with `ready=True`.
If any host or container is missing, the system is not fully converged —
run `molecule test` before proceeding.

## Playbook 3: Batman Mode — Enable and Verify

### Step 1: Check batman status across the cluster

```bash
curl -s http://localhost:9099/api/batman/status | python3 -m json.tool
```

**Expected**: Each entry shows `host/container` keys (e.g., `home/router-100`,
`mesh1/mesh-103`). Only containers that actually exist on each host appear.

### Step 2: Enable batman from the Cluster Manager

```bash
curl -s -X POST http://localhost:9099/api/batman/enable | python3 -m json.tool
```

**Expected**:
- `home/router-100`: May fail if `batman_trigger.sh` not in router image
- Child managers (mesh1): Receive the event via HTTP broadcast, execute locally
- Each child returns `success` or an error explaining what's missing

### Step 3: Verify on the actual container

SSH to mesh1 and check batman-adv:

```bash
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 103 -- /usr/sbin/batman_trigger.sh status"
```

### Step 4: Disable batman

```bash
curl -s -X POST http://localhost:9099/api/batman/disable | python3 -m json.tool
```

## Playbook 4: WiFi Status on Mesh/Bridge Containers

Check WiFi status on each mesh and bridge container:

```bash
# mesh1 (CT 103)
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 103 -- /usr/sbin/wifi_setup.sh status"

# mesh2 (CT 103) — via WAN IP (not on LAN yet)
ssh -o StrictHostKeyChecking=no root@$MESH_2_HOST "pct exec 103 -- /usr/sbin/wifi_setup.sh status"

# bridge-1 (CT 104) — via WAN IP
ssh -o StrictHostKeyChecking=no root@$BRIDGE_1_HOST "pct exec 104 -- /usr/sbin/wifi_setup.sh status"

# bridge-2 (CT 104) — via WAN IP
ssh -o StrictHostKeyChecking=no root@$BRIDGE_2_HOST "pct exec 104 -- /usr/sbin/wifi_setup.sh status"
```

**Expected**: Each shows `WIFI=up` with the correct SSID, MODE (ap/sta), and BAND.

## Playbook 5: Guest Management via Manager API

Query and manage guests (VMs/LXCs) on the local Proxmox host:

```bash
# List guests on home
curl -s http://localhost:9099/api/guests | python3 -m json.tool

# Restart a container (e.g., VMID 103)
curl -s -X POST http://localhost:9099/api/guests/103/restart | python3 -m json.tool
```

## Playbook 6: Verify Child Manager Communication

Test direct communication between Cluster Manager and child Managers:

```bash
# Query mesh1's batman local status directly
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- python3 -c \"
import urllib.request, json
r = urllib.request.urlopen('http://10.10.10.23:9001/api/batman/local/status', timeout=10)
print(json.dumps(json.loads(r.read()), indent=2))
\""
```

**Expected**: Returns host-qualified keys like `mesh1/mesh-103`.

## Playbook 7: End-to-End Heartbeat Chain

Verify the full 4-tier heartbeat chain:

1. **Container → Manager**: Check a container's callhome service:
   ```bash
   ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
     root@10.10.10.210 "pct exec 103 -- systemctl status callhome"
   ```

2. **Manager → Cluster Manager**: Check mesh1's kiosk relay:
   ```bash
   ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
     root@10.10.10.210 "pct exec 401 -- journalctl -u kiosk-web --no-pager -n 5 --since '1 min ago'"
   ```

3. **Cluster Manager fleet view**: Verify mesh1 appears:
   ```bash
   curl -s http://localhost:9099/api/nodes | python3 -c "
   import json, sys
   nodes = json.load(sys.stdin).get('nodes', [])
   mesh1 = [n for n in nodes if n['node_id'] == 'mesh1']
   print('mesh1 in fleet:', bool(mesh1))
   if mesh1:
       print('  ready:', mesh1[0].get('container_health', {}).get('ready'))
   "
   ```

## Playbook 8: Image Version Pipeline Verification

Verify that image versioning is working end-to-end: sidecar files on the
controller, `/etc/image_version` baked into containers, heartbeat delivery
to Node Managers, and fleet-wide visibility via the SuperManager API.

### Step 1: Verify sidecar version files exist

```bash
echo "=== Sidecar version files ==="
for svc in mesh router pihole rsyslog jellyfin netdata wireguard \
           homeassistant kodi moonlight kiosk gaming sunshine desktop; do
  f="images/${svc}.version"
  if [ -f "$f" ]; then
    printf "  OK   %-20s v%s\n" "$svc" "$(cat $f)"
  else
    printf "  MISS %-20s\n" "$svc"
  fi
done
```

**Expected**: All 14 services show `OK` with a valid semver (e.g., `v1.0.0`).

### Step 2: Verify image files match sidecar versions

```bash
echo "=== Image files ==="
for svc in mesh router pihole rsyslog jellyfin netdata wireguard \
           homeassistant kodi moonlight kiosk gaming sunshine desktop; do
  ver=$(cat "images/${svc}.version" 2>/dev/null || echo "?")
  count=$(ls images/${svc}-${ver}-* 2>/dev/null | wc -l)
  if [ "$count" -gt 0 ]; then
    file=$(ls images/${svc}-${ver}-* 2>/dev/null | head -1)
    size_mb=$(du -m "$file" | cut -f1)
    printf "  OK   %-20s v%-8s %4s MB  %s\n" "$svc" "$ver" "$size_mb" "$(basename $file)"
  else
    printf "  MISS %-20s v%-8s (no file matching ${svc}-${ver}-*)\n" "$svc" "$ver"
  fi
done
```

**Expected**: All images present (sunshine is optional — Windows build).

### Step 3: Verify version stamps baked into deployed containers

```bash
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST" "mesh2:$MESH_2_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  echo "=== $label ==="
  for vmid in 101 102 300 301 401 500 501; do
    ver=$(ssh -o StrictHostKeyChecking=no root@$host \
      "pct exec $vmid -- cat /etc/image_version 2>/dev/null" 2>/dev/null)
    if [ -n "$ver" ]; then
      echo "  CT $vmid: v$ver"
    fi
  done
done
```

**Expected**: Each container shows a semver matching its sidecar version.

### Step 4: Verify Node Manager reports versions via API

```bash
echo "=== Node Manager APIs ==="
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST" "mesh2:$MESH_2_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  echo "--- $label ($host:9001) ---"
  curl -s "http://${host}:9001/api/images/versions" 2>/dev/null | python3 -m json.tool || echo "  UNREACHABLE"
done
```

**Expected**: Each Node Manager returns `{"versions": {"pihole": "1.0.0", ...}}`
with entries for every container on that host. Versions match the baked
`/etc/image_version` values (delivered via heartbeat).

### Step 5: Verify SuperManager aggregates fleet versions

```bash
echo "=== Fleet versions (SuperManager) ==="
curl -s "http://localhost:${WEBUI_PORT:-52500}/api/fleet/versions" | python3 -m json.tool
```

**Expected**: Returns `{"fleet_versions": {"home": {"pihole": "1.0.0", ...}, ...}}`
with per-host version maps. Hosts with unreachable Node Managers show
`{"error": "unreachable"}`.

### Step 6: Verify build always creates a new version

```bash
./scripts/build-images.sh --host $PRIMARY_HOST --only pihole 2>&1 | tail -5
cat images/pihole.version
```

**Expected**: The version is bumped (patch increment) from the previous value.
The sidecar file contains the new version.

## Playbook 9: SuperManager Verification

The SuperManager runs on the controller (this machine). Start it:

```bash
python scripts/webui/app.py &
SUPERMANAGER_PID=$!
sleep 5
```

### Step 1: Dashboard loads

```bash
curl -s http://localhost:${WEBUI_PORT:-52525}/ | head -5
```

**Expected**: Returns HTML content (NiceGUI app).

### Step 2: Fleet health API

```bash
curl -s http://localhost:${WEBUI_PORT:-52525}/api/fleet/health | python3 -m json.tool
```

**Expected**: Shows total nodes, healthy count, stale count.

### Step 3: All nodes reporting

```bash
curl -s http://localhost:${WEBUI_PORT:-52525}/api/nodes | python3 -c "
import json, sys
data = json.load(sys.stdin)
nodes = data.get('nodes', [])
print(f'Total nodes reporting: {len(nodes)}')
for n in sorted(nodes, key=lambda x: x['node_id']):
    nid = n['node_id']
    ready = n.get('container_health', {}).get('ready', '?')
    services = ', '.join(n.get('container_health', {}).get('systemd_services', {}).keys()) if n.get('container_health') else 'none'
    print(f'  {nid:25s}  ready={ready:5s}  services: {services}')
"
```

**Expected**: All deployed containers appear with `ready=True`.

### Step 4: Container readiness detail

```bash
# Check a specific container
for svc in pihole rsyslog wireguard kiosk; do
  echo -n "$svc: "
  curl -s http://localhost:${WEBUI_PORT:-52525}/api/container/$svc/ready | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'ready={d.get(\"ready\")}, ports={d.get(\"listening_ports\", [])}')" 2>/dev/null || echo "not found"
done
```

**Expected**: Each service shows `ready=True` with expected listening ports.

### Step 5: Fleet readiness gate

```bash
curl -s "http://localhost:${WEBUI_PORT:-52525}/api/fleet/ready?services=pihole,rsyslog,wireguard,kiosk" | python3 -m json.tool
```

**Expected**: `{"ready": true}` or lists which services are not ready.

### Step 6: Stale heartbeat detection

```bash
curl -s "http://localhost:${WEBUI_PORT:-52525}/api/fleet/stale?services=pihole,rsyslog,wireguard,kiosk&max_age_seconds=120" -w "\nHTTP %{http_code}\n"
```

**Expected**: HTTP 200 (no stale services). HTTP 409 means a service stopped heartbeating.

### Cleanup

```bash
kill $SUPERMANAGER_PID 2>/dev/null
```

## Playbook 10: Kiosk Functionality on Each Unit

Verify the Kiosk UI (NiceGUI web app) works on each host's kiosk container (CT 401).

### Step 1: Verify kiosk-web service is active

```bash
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST" "mesh2:$MESH_2_HOST" "bridge-1:$BRIDGE_1_HOST" "bridge-2:$BRIDGE_2_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  echo -n "$label ($host): "
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- systemctl is-active kiosk-web" 2>&1
done
echo -n "mesh1 (proxy): "
ssh -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 401 -- systemctl is-active kiosk-web" 2>&1
```

**Expected**: All 6 show `active`.

### Step 2: Verify kiosk HTTP endpoint responds

```bash
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST" "mesh2:$MESH_2_HOST" "bridge-1:$BRIDGE_1_HOST" "bridge-2:$BRIDGE_2_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  # Get kiosk container IP from pct config
  kiosk_ip=$(ssh -o StrictHostKeyChecking=no root@$host \
    "pct config 401 | grep 'net0' | grep -oP 'ip=\K[^/]+'" 2>/dev/null)
  echo -n "$label (kiosk IP: $kiosk_ip): "
  ssh -o StrictHostKeyChecking=no root@$host \
    "curl -s -o /dev/null -w '%{http_code}' http://${kiosk_ip}:9001/ 2>/dev/null" 2>&1
  echo ""
done
```

**Expected**: All show HTTP 200.

### Step 3: Verify callhome reporting from each kiosk

```bash
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST" "mesh2:$MESH_2_HOST" "bridge-1:$BRIDGE_1_HOST" "bridge-2:$BRIDGE_2_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  echo "=== $label ==="
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- journalctl -u callhome --no-pager -n 3 --since '2 min ago'" 2>&1
  echo ""
done
```

**Expected**: Recent heartbeat log entries showing successful POST to the management server.

### Step 4: Verify config.json on each kiosk

```bash
for pair in "home:$PRIMARY_HOST" "ai:$AI_HOST"; do
  label="${pair%%:*}"
  host="${pair##*:}"
  echo "=== $label config.json ==="
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- cat /opt/kiosk/config.json" 2>&1 | python3 -m json.tool
  echo ""
done
```

**Expected**: Valid JSON with `IS_CLUSTER_MANAGER`, `MANAGEMENT_SERVER`, and `CHILD_MANAGER_IPS`.

## Playbook 11: Manager Kiosk on Home — Fleet Dashboard

Test the Cluster Manager's fleet dashboard from home's kiosk container.

### Step 1: Set up SSH tunnel

```bash
# Get home's kiosk IP
KIOSK_IP=$(ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct config 401 | grep 'net0' | grep -oP 'ip=\K[^/]+'" 2>/dev/null)
echo "Home kiosk IP: $KIOSK_IP"
ssh -o StrictHostKeyChecking=no -L 9098:${KIOSK_IP}:9001 root@$PRIMARY_HOST -N -f
echo "Tunnel: localhost:9098 -> ${KIOSK_IP}:9001"
```

### Step 2: Fleet dashboard API

```bash
curl -s http://localhost:9098/api/nodes | python3 -c "
import json, sys
data = json.load(sys.stdin)
nodes = data.get('nodes', [])
print(f'Fleet nodes: {len(nodes)}')
for n in sorted(nodes, key=lambda x: x['node_id']):
    nid = n['node_id']
    ch = n.get('container_health', {})
    ready = ch.get('ready', '?')
    ports = ch.get('listening_ports', [])
    print(f'  {nid:25s}  ready={ready}  ports={ports}')
"
```

**Expected**: ALL containers across ALL 6 hosts appear. If any are missing,
the system is not fully converged — run `molecule test` first.

### Step 3: Fleet page renders

```bash
curl -s -o /dev/null -w "Fleet page: HTTP %{http_code}\n" http://localhost:9098/fleet
curl -s -o /dev/null -w "Fleet node detail: HTTP %{http_code}\n" http://localhost:9098/fleet/mesh1
```

**Expected**: Both return HTTP 200.

### Step 4: Batman status via Manager API

```bash
curl -s http://localhost:9098/api/batman/status | python3 -m json.tool
```

**Expected**: Returns status for all OpenWrt containers in the cluster.

### Step 5: WiFi status via Manager API

```bash
curl -s http://localhost:9098/api/wifi/status | python3 -m json.tool
```

**Expected**: Returns WiFi status for mesh/bridge containers with mode, SSID, band info.

### Cleanup

```bash
# Kill the SSH tunnel
kill $(lsof -ti :9098) 2>/dev/null
```

## Playbook 12: Browser-Based UI Verification

Full UI testing requires browser access. This verifies visual rendering,
navigation, and interactive elements that CLI-only API tests cannot cover.

### Prerequisites — tunnels

**Tunnel port assignments** (used across all playbooks):

| Local Port | Target | Used by |
|-----------|--------|---------|
| 9099 | CM (10.10.10.23:9001) via direct tunnel | PB2–7 (CLI API) |
| 9098 | CM via socat relay | PB11–14, 19 (browser UI) |
| 9097 | ai NM (10.99.3.20:9001) | PB12, 15 |
| 6080–6083 | KasmVNC display streams | PB13 |
| ${WEBUI_PORT} | SuperManager (local) | PB9, 12, 16–18, 20 |

If PB11's direct tunnel on 9098 is still active, kill it before
setting up the socat relay below.

```bash
# Kill any stale PB11 tunnel on 9098
kill $(lsof -ti :9098) 2>/dev/null

# SuperManager runs on the controller (no tunnel needed):
# http://localhost:${WEBUI_PORT:-52525}/

# Cluster Manager (home kiosk at 10.10.10.23:9001)
# NOTE: Direct tunnel to 10.10.10.23 may fail due to LAN IP collisions.
# Use nsenter relay via kiosk container PID:
KIOSK_PID=$(ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "lxc-info -n 401 | awk '/^PID:/{print \$2}'")
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "nohup socat TCP-LISTEN:9098,fork,bind=0.0.0.0 \
   EXEC:'nsenter -t $KIOSK_PID -n socat STDIO TCP\:127.0.0.1\:9001' \
   > /dev/null 2>&1 &"
ssh -o StrictHostKeyChecking=no -L 9098:127.0.0.1:9098 root@$PRIMARY_HOST -N -f
# Access at: http://localhost:9098/

# NodeManager kiosk (ai — NAT bridge, no collision):
ssh -o StrictHostKeyChecking=no -L 9097:10.99.3.20:9001 root@$AI_HOST -N -f
# Access at: http://localhost:9097/
```

### Step 1: SuperManager dashboard

Open `http://localhost:${WEBUI_PORT:-52525}/` in a browser.

**Verify:**
- Hosts section shows all 6 hosts with IPs, disk/memory metrics
- Images section shows "All Images Built" or build status
- Fleet section at bottom shows online/total counts, health score
- Quick Actions section: Full Deploy, Build Images, Check Hosts, Deploy Timeline
- Navigation sidebar: Dashboard, Nodes, Deploy, Hub, etc.

### Step 2: SuperManager fleet nodes

Navigate to `/nodes`.

**Verify:**
- Fleet health score prominently displayed (e.g., 46/100 or higher)
- Online/total counts (e.g., 6 ONLINE, 6 TOTAL)
- Worst-case disk/memory indicators with host names
- All 6 hosts displayed as cards with status dot, IP, metrics
- Cards show badges (VPN, LAN, etc.)
- Service matrix table showing which services are on which hosts

### Step 3: Deploy page

Navigate to `/deploy`.

**Verify:**
- Tags displayed (backup, infra, openwrt, pihole, etc.)
- Host limit input, Dry Run toggle, Verbose control
- Start Deploy and Cancel buttons present

### Step 4: Home Hub

Navigate to `/hub`.

**Verify:**
- Services grouped: Infrastructure, Desktop & Media, Settings & Network, Monitoring, System
- Available services show blue action buttons
- Unavailable services show grey "Not available" badge
- Footer: "Kiosk Hub - Powered by Proxmox VE"

### Step 5: Cluster Manager fleet

Open `http://localhost:9098/fleet` in a browser.

**Verify:**
- Fleet tab shows child containers (7 on home: rsyslog, pihole, kiosk,
  homeassistant, kodi, wireguard, jellyfin)
- Each node card shows IP, disk %, memory %
- Auto-refresh toggle at bottom (5s interval)
- Sidebar navigation: Dashboard, Home Hub, Bridge, Mesh, Router, etc.

### Step 6: Cluster Manager network pages

Navigate to `/bridge`, `/mesh`, `/router` at `http://localhost:9098/`.

**Bridge:** Status cards for bridge-1 and bridge-2. WiFi status shows AP/STA
roles and signal quality. Traffic graph and stats table present. SSH errors
here indicate a failed converge — fix infrastructure before continuing.

**Mesh:** Topology diagram (Node → mesh1, mesh2). Status cards for each
node. Batman Mode section with enable/disable buttons.

**Router:** Router interface info, WAN/LAN status.

### Step 7: NodeManager kiosk (ai)

Open `http://localhost:9097/` in a browser.

**Verify:**
- Shows Home Hub only (no sidebar navigation, no fleet, no deploy)
- Same service tiles as Cluster Manager hub
- `/fleet` returns 404 (correct — NodeManagers don't manage other nodes)
- Color scheme matches Cluster Manager (dark theme, cyan accents)

### Expected differences: Cluster Manager vs NodeManager

| Feature | Cluster Manager | NodeManager |
|---------|----------------|-------------|
| Navigation | Sidebar with Fleet, Deploy, Hub | No sidebar — Hub only |
| Fleet dashboard | `/fleet` with child nodes | 404 |
| Deploy page | Available | Not present |
| Bridge/Mesh/Router | Available (SSH to containers) | Not present |
| Home Hub | Via navigation | Default and only page |

## Playbook 13: Hierarchical Kiosk Control via KasmVNC Display Pipeline

Test remote display streaming across the 4-tier hierarchy using KasmVNC
iframes. Each kiosk container runs a single `kiosk-display.service`
(KasmVNC Xvnc) that serves its web client on port 6080. Display apps
(Desktop, Kodi, Moonlight) run their own KasmVNC instances on dedicated
ports (6081-6083). The browser renders ALL display streams as iframes —
zero noVNC, zero websockify.

### Prerequisites

In addition to the standard prerequisites (Step 0–2 above):

- KasmVNC display services must be active on all kiosk containers
  (`kiosk-display.service` on CT 401 across all 6 hosts)
- SuperManager running in UI mode (not headless — see "SuperManager headless
  vs UI mode" in Known Issues)
- SSH tunnels from Playbook 12 still active (localhost:9098 → CM, etc.)
- `kasmvncserver` package installed in every display-capable container/VM

**KasmVNC WebSocket tunnels** — the browser loads KasmVNC's web client
via iframe at `http://<host_ip>:608X`. If the operator's machine is NOT
on the fleet LAN, set up tunnels:

```bash
# home (router node — socat proxy on WAN IP forwards to kiosk container)
ssh -o StrictHostKeyChecking=no -L 6080:127.0.0.1:6080 root@$PRIMARY_HOST -N -f

# ai (DNAT on WAN IP forwards to kiosk container)
ssh -o StrictHostKeyChecking=no -L 6081:127.0.0.1:6080 root@$AI_HOST -N -f

# mesh1 (LAN host — tunnel through primary)
ssh -o StrictHostKeyChecking=no -L 6082:10.10.10.210:6080 root@$PRIMARY_HOST -N -f

# mesh2
ssh -o StrictHostKeyChecking=no -L 6083:127.0.0.1:6080 root@$MESH_2_HOST -N -f
```

In production (operator on the LAN or VPN), fleet IPs are directly
reachable — no tunnels needed.

### 13.1 KasmVNC display service health check on all hosts

```bash
for pair in "$PRIMARY_HOST:home" "$AI_HOST:ai" "$MESH_2_HOST:mesh2" \
            "$BRIDGE_1_HOST:bridge-1" "$BRIDGE_2_HOST:bridge-2"; do
  host="${pair%%:*}"; label="${pair##*:}"
  echo -n "$label: "
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- systemctl is-active kiosk-display && \
     pct exec 401 -- dpkg -l kasmvncserver | grep -q ii && echo 'kasmvnc=ok' || echo 'kasmvnc=MISSING'" 2>&1
done
# mesh1 via proxy
ssh -o StrictHostKeyChecking=no \
  -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 \
  "pct exec 401 -- systemctl is-active kiosk-display && \
   pct exec 401 -- dpkg -l kasmvncserver | grep -q ii && echo 'kasmvnc=ok' || echo 'kasmvnc=MISSING'" 2>&1
```

**Expected**: All 6 hosts show `active` and `kasmvnc=ok`. If any service
is failed, check `journalctl -u kiosk-display` inside the container.
Verify only `kiosk-display.service` is used (legacy `kiosk-vnc` /
`kiosk-vnc-ws` units must not exist):

```bash
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- systemctl is-active kiosk-display"
```

**Expected**: Command output is `active`. Legacy `kiosk-vnc` and
`kiosk-vnc-ws` must not be installed as systemd units (`LoadState=not-found`).

```bash
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- bash -c 'for u in kiosk-vnc kiosk-vnc-ws; do [ \"\$(systemctl show -p LoadState --value \"\${u}.service\")\" = not-found ] || exit 1; done; echo legacy_vnc_units=absent'"
```

**Expected**: `legacy_vnc_units=absent`.

**Desktop LXC check** (on home only — CT 400):

```bash
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 400 -- systemctl is-active desktop-display" 2>&1
# Verify session switch script is baked in
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 400 -- test -x /usr/sbin/switch-desktop-session && echo 'switch-script=ok' || echo 'switch-script=MISSING'"
# Verify both xstartup session files exist
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 400 -- bash -c 'test -f /home/desktop/.vnc/xstartup-kde && test -f /home/desktop/.vnc/xstartup-gnome && echo sessions=ok || echo sessions=MISSING'"
# Verify current session
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 400 -- /usr/sbin/switch-desktop-session status"
```

**Expected**: `desktop-display` active inside the container (KasmVNC);
`switch-script=ok`; `sessions=ok`; `SESSION=kde` (default). The
container has both KDE Plasma and GNOME installed with switchable
xstartup symlink.

### 13.2 SuperManager → ClusterManager drill-down via iframe

1. Open SuperManager at `http://localhost:${WEBUI_PORT:-52525}/nodes`
2. Click home host card → `/nodes/home`
3. Verify "Open Kiosk" button visible with `cast` icon
4. Click "Open Kiosk" → `/remote/home?back=/nodes/home`
5. Verify KasmVNC iframe loads showing the CM kiosk hub page — NOT a
   noVNC `<canvas>`. The KasmVNC
   web client auto-hides its control bar when embedded in an iframe
6. Verify the **thin viewer bar** at the top shows: **back arrow**,
   "home" label, **"Drill into" dropdown** (child node picker), and
   **visibility_off toggle** button. The SM acts as a thin client —
   NO session switching, NO app switcher icons in the bar
7. Click the **visibility_off** toggle → verify the viewer bar hides,
   giving full-screen immersive view of the KasmVNC iframe
8. Click bridge/mesh/router links inside the iframe — verify Chromium
   navigates within the kiosk (page changes visible through iframe)
9. Move mouse, type on keyboard — verify input reaches the KasmVNC
   display through the iframe
10. Click back button in top bar → verify return to `/nodes/home`

### 13.3 Two-level drill-down (SM → CM kiosk → NM kiosk)

1. Open `/remote/home?back=/nodes/home` — iframe to CM kiosk
2. Verify child picker dropdown in the top bar shows child nodes
   (mesh1, ai, mesh2, bridge-1, bridge-2)
3. Select "mesh1" from the child picker dropdown (click in the
   **parent page top bar**, NOT inside the iframe display)
4. Verify browser navigates to `/remote/mesh1?back=/remote/home...`
5. Verify iframe disconnects from home and reconnects **directly** to
   mesh1's kiosk (single iframe layer — NOT iframe-in-iframe)
6. Verify mesh1's hub page is visible through single-layer iframe
7. Navigate mesh1's pages (containers, bridge, etc.) inside the iframe
8. Click back button in the **top bar** → verify return to
   `/remote/home` (iframe reconnects to home's kiosk)
9. Click back button again → verify return to `/nodes/home`
   (SuperManager node detail page — no iframe)

### 13.4 Every hub app — launch, interact, exit via display iframe

Test EVERY app on the kiosk hub through a single KasmVNC iframe layer.
The hub has 15 services in three interaction categories. **Test all of
them** — not a subset. View **home's** kiosk for these tests (home has
the most services configured).

Open `/remote/home?back=/nodes/home` to start viewing home's kiosk.
Navigate to the hub page inside the iframe if not already there.

---

**13.4a Display Apps (console view via `/console`)**

These apps run their own KasmVNC instance on dedicated ports. The console
page renders the app's KasmVNC web client in an iframe. Each one must be
tested individually.

**Desktop (VMID 400) — KDE session (default, Windows-style):**

1. Navigate to `/console/home/desktop` from the SM
2. Verify the console page loads with a **minimal thin viewer bar**:
   **back arrow**, "Desktop on home" label, and **visibility_off toggle**
3. Verify the viewer bar does NOT contain session switching buttons or
   app switcher icons — the SM is a thin client, session management
   happens within the desktop environment itself via the KasmVNC iframe
4. Verify the KasmVNC iframe shows a **KDE Plasma desktop** — Breeze Dark
   theme, bottom taskbar with system tray, Application Launcher (start
   menu style), dark window decorations. This should look like Windows 11
   Dark Mode, NOT generic XFCE
5. Interact: open Konsole (terminal), open Dolphin (file manager), move
   windows, click the Application Launcher menu, type text. Verify mouse
   and keyboard input work through the iframe
6. Click the **visibility_off** toggle → verify the viewer bar hides,
   giving full-screen immersive desktop view
7. Click back button in viewer bar → verify return to previous page
8. Verify no noVNC status dot, no `<canvas>` element — pure iframe

**Desktop (VMID 400) — session switching:**

Session switching between KDE (Windows-style) and GNOME (Mac-style) is
done WITHIN the desktop environment, through the KasmVNC iframe. The
switch-desktop-session script runs inside the Desktop LXC container.
The SM does not control sessions — it only provides the viewport.

9. To test session switching, use the desktop environment's own
   mechanisms (e.g., log out and select a different session, or use
   the baked-in `/usr/sbin/switch-desktop-session` command via a
   terminal inside the KasmVNC iframe)

**Kodi (VMID 301):**

1. Navigate to `/console/home/kodi` (app switcher or direct URL)
2. Verify Kodi's home screen renders in the KasmVNC iframe — browse
   menus, verify the X11 windowing mode works (Kodi detects `DISPLAY`
   automatically)
3. Interact: browse media library, open settings, scroll menus. Verify
   mouse and keyboard work through the iframe
4. Click back → verify return to previous page

**Moonlight (VMID 302):**

Moonlight is on `streaming_nodes` (mesh1), NOT home. Navigate to
`/console/mesh1/moonlight` from the SM.

1. Navigate to `/console/mesh1/moonlight`
2. Verify the Moonlight client UI renders in the KasmVNC iframe — the
   SDL2 X11 backend displays the connection/streaming UI
3. Interact: navigate Moonlight's menus, attempt to connect to the
   Sunshine server. Verify input works through the iframe
4. Click back → verify return to previous page

---

**13.4b External Web UIs (iframe via `/view`)**

These apps open inside the viewer page (`/view?url=...`) which wraps the
service URL in an iframe with a "Back to Hub" top bar. Test each one on
home's kiosk (iframe to `/remote/home`).

For each app below, perform these steps:

1. In the hub (inside the kiosk iframe), click the service tile
2. Verify the viewer page loads — top bar with home icon + service title
   visible at the top, service web UI visible in the iframe below
3. Interact with the service UI — click a link, navigate a page, scroll.
   Verify mouse and keyboard input work through the kiosk iframe
4. Click the home icon (top-left of viewer bar) → verify return to hub
5. Verify iframe connection remains active

Test these services on **home's kiosk** (services deployed on home):

| # | Tile | Section | What to verify in the iframe |
|---|------|---------|------------------------------|
| 1 | **Jellyfin** | Desktop & Media | Media library loads, browse a show/movie, start playback |
| 2 | **Home Assistant** | Desktop & Media | Dashboard loads with entity cards, click a card to see entity detail |
| 3 | **Router (OpenWrt)** | Settings & Network | LuCI status overview loads, navigate to Network → Interfaces |
| 4 | **Pi-hole** | Settings & Network | Dashboard loads with query stats, navigate to Query Log |
| 5 | **WireGuard** | Settings & Network | VPN status page loads if URL configured, OR tile shows "Not available" (WireGuard lacks a native web UI — this is valid) |
| 6 | **Netdata** | Monitoring | Real-time metrics dashboard loads, CPU/memory charts updating |
| 7 | **Logs (rsyslog)** | Monitoring | Log viewer loads if URL configured, OR tile shows "Not available" (rsyslog lacks a native web UI — this is valid) |

If any service URL is not configured on home's kiosk (tile shows "Not
available" badge), note it as a gap but do NOT skip — verify the disabled
tile renders correctly (greyed out, non-clickable).

**Gaming (Sunshine) — test on ai's kiosk, NOT home's:**

Gaming (Sunshine) is deployed only on `ai` (`gaming_nodes`). On home's hub
the Gaming tile shows "Not available." To test Gaming:

1. Navigate to SM `/nodes/ai` → "Open Kiosk" → `/remote/ai`
2. Inside ai's kiosk hub, verify the Gaming tile shows the Sunshine URL
3. Click Gaming tile → verify viewer page loads with Sunshine web UI
4. Interact: view apps list or login page, navigate menus
5. Click back → return to ai's hub

---

**13.4c Internal Kiosk Pages (navigate within NiceGUI)**

These tiles navigate to pages within the kiosk's own NiceGUI app — no
iframe, no screen takeover. The page renders directly in Chromium
(visible through the KasmVNC iframe).

For each page below:

1. In the hub (inside the kiosk iframe), click the tile
2. Verify the correct NiceGUI page loads inside Chromium (visible
   through the KasmVNC iframe) — NOT a blank page, NOT the hub
3. Interact with the page content — click a card, expand a section,
   scroll. Verify input works through the iframe
4. Navigate back to the hub — use the sidebar nav or browser back
5. Verify iframe remains connected

| # | Tile | Route | What to verify |
|---|------|-------|----------------|
| 1 | **WiFi Bridge** | `/bridge` | Bridge host cards with AP/STA roles, WiFi status, signal metrics |
| 2 | **Mesh WiFi** | `/mesh` | Mesh topology, peer status cards, signal quality indicators |
| 3 | **Router Detail** | `/router` | OpenWrt metrics (WAN/LAN/WiFi/Firewall/System) populated via heartbeat subscription to 10.10.10.1. Page must NOT be blank — verify data cards show real values |
| 4 | **Containers & VMs** | `/containers` | Guest list with VMIDs, names, status badges, start/stop controls |

---

**13.4d Two-level drill-down app launch (test multiple child hosts)**

Test that apps work after navigating SM → CM → NM via child picker.
Test at least 3 different child hosts to cover all forwarding topologies.

**mesh1 (LAN host — relay path):**

1. Open `/remote/home?back=/nodes/home` — iframe to home's CM kiosk
2. Select "mesh1" from the child picker dropdown → iframe to mesh1
3. In mesh1's hub, verify which tiles are enabled and which show "Not
   available." Click each enabled tile — internal pages (Bridge, Mesh,
   Router, Containers) and any configured external services. Verify
   each loads and is interactive through the iframe
4. If Moonlight is configured, navigate to its console via the app
   switcher or hub "Launch" tile. Verify the KasmVNC iframe shows
   Moonlight's UI on port 6083
5. Click back in the **parent page top bar** → return to `/remote/home`

**ai (WAN host — DNAT path):**

6. Select "ai" from the child picker → iframe to ai
7. Verify ai's hub loads. Click the Gaming (Sunshine) tile — verify
   Sunshine web UI loads inside the viewer iframe
8. Click back → return to `/remote/home`

**bridge-1 (WAN host — minimal services):**

9. Select "bridge-1" from the child picker → iframe to bridge-1
10. Verify bridge-1's hub shows mostly "Not available" tiles (bridge
    nodes have fewer services). Verify the Containers & VMs internal
    page works
11. Click back → return to `/remote/home`
12. Click back again → return to `/nodes/home` (SM page, no iframe)

### 13.5 Direct SuperManager → every host's kiosk display

Test that ALL 6 hosts' kiosk displays are accessible directly from the SM.
This verifies the display URL resolution, port forwarding (DNAT, socat,
relay), and KasmVNC service health on every unit.

For each host below:

1. Navigate to `/nodes/{hostname}` on the SuperManager
2. Click "Open Kiosk" → `/remote/{hostname}`
3. Verify KasmVNC iframe loads and shows the kiosk hub page
4. Verify mouse/keyboard interaction works through the iframe
5. Click one tile inside the iframe (any tile) to verify navigation
6. Click back → return to `/nodes/{hostname}`

| # | Host | IP resolution path | Port forwarding type |
|---|------|--------------------|---------------------|
| 1 | **home** | `_fleet_nodes` → WAN IP | socat proxy (router node cross-bridge) |
| 2 | **mesh1** | `_fleet_nodes` → LAN IP (direct) | socat proxy on mesh1:6080 → container |
| 3 | **ai** | `_fleet_nodes` → WAN IP | iptables DNAT |
| 4 | **mesh2** | `_fleet_nodes` → WAN IP | iptables DNAT |
| 5 | **bridge-1** | `_fleet_nodes` → WAN IP | iptables DNAT |
| 6 | **bridge-2** | `_fleet_nodes` → WAN IP | iptables DNAT |

**All 6 must load.** If any fails, note the specific error (iframe blank,
ERR_CONNECTION_REFUSED, timeout) and which forwarding path is broken.

### 13.6 ClusterManager display from CM's own web UI (different code path)

This tests the CM's tier-specific display URL resolution (`_child_managers`)
separately from the SM path (`_fleet_nodes`). Access the CM's
`kiosk_server` directly — NOT through the SM.

1. Open `http://localhost:9098/fleet` in the operator's browser
   (the CM's web UI, served by `kiosk_server.py`)
2. Verify "Open Kiosk" `cast_connected` icon buttons appear on each
   child node card (mesh1, ai, mesh2, bridge-1, bridge-2)
3. Click a node card → `/fleet/mesh1`. Verify "Open Kiosk" button
   with `cast` icon is present in the header
4. Click "Open Kiosk" → CM serves `/remote/mesh1?back=/fleet/mesh1`
5. Verify KasmVNC iframe loads and shows mesh1's kiosk display (single
   iframe — CM's `get_child_display_url` resolved from `_child_managers`)
6. Verify mouse/keyboard input works through the iframe
7. Click back → verify return to `/fleet/mesh1` on the CM's web UI
8. Verify this path uses the SAME `remote_kiosk.py` page as the SM
   (visual parity — same top bar, same child picker behavior)

### 13.7 Error and edge cases

1. Navigate to `/remote/nonexistent` → verify "Kiosk not reachable"
   error page with back button (same error state as before — unchanged)
2. Stop KasmVNC on a host, try to connect → verify iframe shows
   connection failure (browser's default iframe error, not blank screen):
   ```bash
   ssh -o StrictHostKeyChecking=no root@$AI_HOST \
     "pct exec 401 -- systemctl stop kiosk-display"
   ```
   Open `/remote/ai` — verify the iframe cannot load (ERR_CONNECTION_REFUSED
   or blank). The viewer bar back button should still work.
   Restart the service after testing:
   ```bash
   ssh -o StrictHostKeyChecking=no root@$AI_HOST \
     "pct exec 401 -- systemctl start kiosk-display"
   ```
3. Resize browser to 1024px and 768px → verify KasmVNC iframe scales
   correctly, top bar remains fixed, no overflow
4. Verify ZERO noVNC artifacts: no `<canvas id="vnc-container">`, no
   `#vnc-status-dot`, no `/static/noVNC/` requests in browser DevTools
   Network tab. ALL display streaming uses `<iframe>` elements
5. Verify display app conflict resolution: navigate to
   `/console/home/desktop` (start Desktop LXC), then switch to
   `/console/home/kodi` via the app switcher. Verify the Desktop LXC is
   stopped before Kodi starts (DRI3 device conflict). Then switch back
   to Desktop — verify Kodi stops and Desktop starts

### 13.8 Complete host × app verification matrix

Final sign-off checklist. Every cell must be verified during this playbook.
Mark each as PASS/FAIL.

| Host | Kiosk (6080) | Desktop (6081) | Kodi (6082) | Moonlight (6083) | Gaming (web) |
|------|:---:|:---:|:---:|:---:|:---:|
| **home** | SM direct | SM console | SM console | N/A | N/A |
| **mesh1** | SM direct + relay | SM console | N/A | SM console | N/A |
| **ai** | SM direct | SM console | N/A | N/A | hub tile |
| **mesh2** | SM direct | SM console | N/A | N/A | N/A |
| **bridge-1** | SM direct | SM console | N/A | N/A | N/A |
| **bridge-2** | SM direct | SM console | N/A | N/A | N/A |

- "SM direct" = `/remote/{hostname}` from the SuperManager
- "SM console" = `/console/{hostname}/{app_id}` from the SuperManager
- "hub tile" = service web UI accessed via the hub tile on that host's kiosk
- "N/A" = service not deployed on this host (verify tile shows "Not available")
- Session switching (KDE ↔ GNOME) is done within the desktop environment
  through the KasmVNC iframe, not via SM-side buttons. The SM is a thin
  client — it provides the viewport, not the control logic

## Playbook 14: Cluster Manager Sidebar & Submenu Navigation

Test the full navigation tree of the Cluster Manager (home's kiosk) by
visiting every sidebar item, expanding submenus, and verifying page
content renders correctly. This tests the kiosk_nav_bar on hub pages and
the cluster_nav_sidebar on internal pages.

### Prerequisites

- SSH tunnel to CM active (localhost:9098 → home kiosk 10.10.10.23:9001)
  from Playbook 12 prerequisites
- Alternatively, use VNC view: `/remote/home?back=/nodes` on the SM

### 14.1 Hub page navigation bar

Open `http://localhost:9098/hub` in the operator's browser (or VNC into
home's kiosk via the SM).

**Verify:**

1. Top navigation bar is visible with: Home icon, "Home Hub" label, and
   buttons for Fleet, Bridge, Mesh, Router, Containers
2. Click **Fleet** → navigates to `/fleet`
3. Verify Fleet page loads with child node cards (mesh1, ai, mesh2,
   bridge-1, bridge-2)
4. Click **Home** icon in nav bar → returns to `/hub`
5. Click **Bridge** → navigates to `/bridge`
6. Verify Bridge page loads with bridge host status cards
7. Click **Home** icon → returns to `/hub`
8. Click **Mesh** → navigates to `/mesh`
9. Verify Mesh page loads with mesh topology and node status
10. Click **Home** icon → returns to `/hub`
11. Click **Router** → navigates to `/router`
12. Verify Router page loads with WAN/LAN interface info
13. Click **Home** icon → returns to `/hub`
14. Click **Containers** → navigates to `/containers`
15. Verify Containers page loads with guest list (VMIDs, names, status)
16. Click **Home** icon → returns to `/hub`

### 14.2 Fleet page — sidebar navigation and node drill-down

Open `http://localhost:9098/fleet` in the operator's browser.

**Verify sidebar navigation (left panel):**

1. Sidebar shows sections: Fleet, Home Hub, Bridge, Mesh, Router, Containers
2. "Fleet" item is highlighted/active
3. Click **Home Hub** in sidebar → navigates to `/hub`
4. Verify hub page renders with service tiles
5. Navigate back to `/fleet`
6. Click **Bridge** in sidebar → navigates to `/bridge`
7. Verify bridge page renders
8. Navigate back to `/fleet`
9. Click **Mesh** in sidebar → navigates to `/mesh`
10. Verify mesh page renders
11. Navigate back to `/fleet`
12. Click **Router** in sidebar → navigates to `/router`
13. Verify router page renders
14. Navigate back to `/fleet`
15. Click **Containers** in sidebar → navigates to `/containers`
16. Verify containers page renders

**Verify node drill-down:**

17. On `/fleet`, click a child node card (e.g., "mesh1")
18. Verify navigation to `/fleet/mesh1` — node detail page
19. Verify node detail shows: hostname, IP, disk %, memory %, container
    health info, and display app buttons (Desktop Console, Kodi Console,
    Moonlight Console)
20. Click **Open Kiosk** → verify display viewer opens at `/remote/mesh1`
21. Click back → return to `/fleet/mesh1`
22. Click back again or use sidebar → return to `/fleet`

### 14.3 Bridge page — interactive elements

Open `http://localhost:9098/bridge`.

**Verify:**

1. Page shows bridge host cards for bridge-1 (AP) and bridge-2 (STA)
2. Each card shows WiFi status, signal quality, connection mode
3. WiFi Restart button present for each bridge host
4. Click WiFi Restart on one bridge → verify status update (API call
   triggers `wifi_setup.sh status` on the container)
5. Scroll down — verify traffic stats, signal history, or status
   timeline if present
6. Navigate to another page via sidebar or nav bar

### 14.4 Mesh page — interactive elements

Open `http://localhost:9098/mesh`.

**Verify:**

1. Topology diagram shows mesh nodes (mesh1, mesh2) with connections
2. Node status cards present for each mesh container
3. Batman Mode section visible with Enable/Disable buttons
4. Click **Batman Status** or status check → verify status query returns
   results for all mesh nodes
5. Click **Enable Batman** → verify API call succeeds (or returns clear
   error about missing prerequisites)
6. Click **Disable Batman** → verify API call succeeds
7. WiFi Status section shows mode, SSID, band, signal for each node
8. Navigate to another page via sidebar or nav bar

### 14.5 Router page — interactive elements

Open `http://localhost:9098/router`.

**Verify:**

1. Router interface summary: WAN status, LAN status, uptime
2. DHCP lease table or active clients list present
3. System info section with hostname, firmware version, memory usage
4. Navigate to another page via sidebar or nav bar

### 14.6 Containers page — interactive elements

Open `http://localhost:9098/containers`.

**Verify:**

1. Guest list shows all containers and VMs on home (VMIDs 100-601)
2. Each entry shows: VMID, name, status badge (running/stopped)
3. Start/Stop/Restart buttons present for each guest
4. Click **Restart** on a non-critical container (e.g., rsyslog CT 501)
   → verify status briefly shows "stopped" then "running"
5. Navigate to another page via sidebar or nav bar

## Playbook 15: NodeManager Kiosk Navigation on Individual Unit

Test navigation and functionality on a NodeManager (non-Cluster-Manager)
kiosk. NodeManagers have a reduced feature set: Hub page with nav bar,
but NO sidebar and NO fleet dashboard.

### Prerequisites

- SSH tunnel to a NodeManager kiosk active (e.g., ai's kiosk):
  ```bash
  ssh -o StrictHostKeyChecking=no -L 9097:10.99.3.20:9001 root@$AI_HOST -N -f
  ```

### 15.1 Hub page on NodeManager

Open `http://localhost:9097/hub` (or VNC into ai's kiosk).

**Verify:**

1. Top navigation bar is visible with: Home icon, "Home Hub" label,
   and buttons for Fleet, Bridge, Mesh, Router, Containers
2. Service tiles render correctly — same categories as CM hub
3. Infrastructure tiles (WiFi Bridge, Mesh WiFi, Router Detail,
   Containers) show action badges
4. Services not deployed on ai show "Not available" badge

### 15.2 Navigation bar page traversal on NodeManager

From the hub, click each nav bar button and verify page behavior:

1. Click **Fleet** → navigates to `/fleet`
   - **Expected**: Page renders but shows empty fleet (no child Managers)
     OR returns 404 (NodeManagers don't register fleet pages)
   - Note which behavior occurs — both are valid depending on page
     registration in `kiosk_server.py`
2. Click **Home** icon → returns to `/hub`
3. Click **Bridge** → navigates to `/bridge`
   - Verify page renders (may show only local bridge info or none)
4. Click **Home** icon → returns to `/hub`
5. Click **Mesh** → navigates to `/mesh`
   - Verify page renders (may show only local mesh info)
6. Click **Home** icon → returns to `/hub`
7. Click **Router** → navigates to `/router`
   - Verify page renders
8. Click **Home** icon → returns to `/hub`
9. Click **Containers** → navigates to `/containers`
   - Verify containers page shows local guests (ai's VMs/CTs)
   - Verify VMID, name, status for each guest
10. Click **Home** icon → returns to `/hub`

### 15.3 Internal pages navigation on NodeManager

From the hub, click each internal page tile:

1. Click **WiFi Bridge** tile → navigates to `/bridge`
   - Same as nav bar Bridge (15.2 step 3)
2. Click **Mesh WiFi** tile → navigates to `/mesh`
3. Click **Router Detail** tile → navigates to `/router`
4. Click **Containers & VMs** tile → navigates to `/containers`
   - Verify containers list shows ai's local guests

### 15.4 NodeManager vs ClusterManager differences

Verify these behavioral differences between NodeManager (ai) and
ClusterManager (home):

| Feature | ClusterManager (home) | NodeManager (ai) |
|---------|----------------------|-------------------|
| Hub nav bar | Fleet, Bridge, Mesh, Router, Containers | Same items |
| Fleet dashboard | Shows child Managers | Empty or 404 |
| Bridge page | Shows bridge-1, bridge-2 status | Local only or empty |
| Mesh page | Shows all mesh nodes | Local only or empty |
| Router page | Shows router details | May show limited info |
| Containers page | Shows home's guests | Shows ai's guests |
| Display remote control | Available for child nodes | Not available |
| Batman enable/disable | Broadcasts to all nodes | Local only |
| `/api/batman/status` | Returns cluster-wide status | Returns local only |

## Playbook 16: SuperManager Full Page Navigation

Test every page in the SuperManager UI by visiting each sidebar item and
verifying content renders correctly. This covers SM-specific pages NOT
tested by PB12 (which only spot-checks dashboard, nodes, deploy, hub).

### Prerequisites

- SuperManager running in UI mode (see Known Issues — headless vs UI mode):
  ```bash
  kill $(cat .state/test_api.pid) 2>/dev/null
  python scripts/webui/app.py --port ${WEBUI_PORT:-52525} --env test.env &
  echo $! > .state/test_api.pid
  sleep 5
  ```
- Browser open at `http://localhost:${WEBUI_PORT:-52525}/`

### 16.1 Dashboard (`/`)

1. Open `http://localhost:${WEBUI_PORT:-52525}/`
2. Verify **Environment badge** at top — shows active env file
3. Verify **Hosts section** — 6 host cards with names, IPs, and status
   dot (green = reachable, red = unreachable)
4. Verify **Images section** — "All Images Built" or per-target build
   status with version numbers
5. Verify **Fleet summary** at bottom — online/total counts, health
   score percentage
6. Verify **Quick Actions row** — 4 buttons: Full Deploy, Build Images,
   Check Hosts, Deploy Timeline
7. Click **Full Deploy** → verify navigation to `/deploy`
8. Click browser Back → return to dashboard
9. Click **Deploy Timeline** → verify navigation to `/timeline`
10. Click browser Back → return to dashboard
11. Click a **host card** (e.g., "home") → verify navigation to `/nodes/home`
12. Click browser Back → return to dashboard

### 16.2 Environment page (`/environment`)

1. Click **Environment** in the sidebar → navigates to `/environment`
2. Verify the page title renders
3. Verify the env variable table loads with key/value rows from the
   active env file (test.env or .env)
4. Verify **Validate** button is present — click it, verify it reports
   validation results (missing required vars, etc.)
5. Verify **Save** button is present
6. Verify **Create .env** button is present (for creating from template)
7. Do NOT actually save or create — just verify the controls exist and
   the table is populated

### 16.3 Hosts page (`/hosts`)

1. Click **Hosts** in the sidebar → navigates to `/hosts`
2. Verify table shows all hosts with hostname, IP, and connectivity status
3. Click **Probe All** → verify probing starts, status indicators update
   (green checkmark for reachable, red X for unreachable)
4. Click **Test SSH** → verify SSH test runs against all hosts, results
   display inline (success/failure per host)
5. Verify any unreachable host shows clearly with error details

### 16.4 Services page (`/services`)

1. Click **Services** in the sidebar → navigates to `/services`
2. Verify **Deploy Profile** select dropdown is present with options:
   Full Deploy, Home Unit, Mesh Unit, Gamer Unit, Bridge Units,
   Network Only, Core Services, Media Stack, Custom
3. Select **"Home Unit"** profile → verify tag checkboxes auto-update
   to match the profile
4. Select **"Custom"** → verify all checkboxes become manually toggleable
5. Verify tag checkboxes present: backup, infra, openwrt, lan-satellite,
   cleanup, pihole, wireguard, monitoring, homeassistant, media,
   moonlight, desktop, kiosk, mesh-wifi, bridge, gaming
6. Click **Select All** → verify all checkboxes checked
7. Click **Deselect All** → verify all checkboxes unchecked
8. Manually check "pihole" and "wireguard" → verify checkboxes toggle
9. Click **Deploy Selected** → verify navigation to `/deploy` with the
   selected tags pre-populated
10. Do NOT actually start the deploy — verify the tags are shown on the
    deploy page, then navigate away

### 16.5 Images page (`/images`)

1. Click **Images** in the sidebar → navigates to `/images`
2. Verify **Quick build** row shows "Mesh" and "Router" buttons
3. Verify the image table lists all buildable targets: mesh, router,
   pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi,
   moonlight, kiosk, gaming, sunshine, desktop
4. Verify each row shows: target name, version (from sidecar), build
   status, last build timestamp
5. Click **Refresh** → verify table refreshes
6. Do NOT click "Build Selected" or "Build All" — just verify controls
   are present and the table is populated correctly

### 16.6 Deploy page (`/deploy`)

1. Click **Deploy** in the sidebar → navigates to `/deploy`
2. Verify controls: **Host limit** input, **Dry Run** toggle, **Verbose**
   level selector
3. Verify **Start Deploy** button is present (enabled)
4. Verify **Cancel** button is present (disabled until deploy starts)
5. Verify the log area is empty (no previous run output)
6. Verify selected tags are shown (from Services page selection or empty)
7. Do NOT start a deploy — just verify the page renders with all controls

### 16.7 Timeline page (`/timeline`)

1. Click the **Deploy Timeline** quick action on the dashboard, or
   navigate to `/timeline` directly
2. Verify the page renders a deploy timeline (Gantt chart style) or
   a "No deploys recorded" empty state
3. If deploys exist: verify bars show deploy duration, tags, and status
4. Navigate back to the dashboard

### 16.8 Nodes page — Add Host and Kickstart (`/nodes`)

1. Navigate to `/nodes`
2. Verify **Auto-refresh** toggle switch at top — click it on, verify
   node cards update periodically (5s interval)
3. Click auto-refresh off
4. Verify the **Add Host** expansion panel at the bottom
5. Click to expand it — verify form fields: hostname, IP, MAC, VPN IP,
   bucket (select), and **Register** button
6. Do NOT register a host — just verify the form renders
7. Click a host card that is "reachable" but not heartbeating (if any)
   → navigate to `/nodes/{hostname}`
8. Verify the **Kickstart Heartbeat** button is present on reachable-but-
   not-online hosts — do NOT click it in this test
9. Navigate back to `/nodes`

### 16.9 Node detail — app console links (`/nodes/{hostname}`)

1. Navigate to `/nodes/home`
2. Verify **Back** button returns to `/nodes`
3. Verify **Open Kiosk** button with `cast` icon (if display URL resolves)
4. Verify **app console icon links** (tv/web icons) for display apps:
   Desktop, Kodi, Moonlight. Disabled icons for apps not on this host
5. Verify resource cards: disk %, memory %, CPU (if available)
6. Verify **Guests** table — list of VMs/CTs on this host with VMIDs
7. Verify **Network** section — interfaces, IPs, MACs
8. Verify **Deploy History** expansion — recent deploy stamps
9. Verify **Extensions** expansion — heartbeat extension data
10. Navigate to `/nodes/mesh1` — verify mesh1's detail loads
11. Navigate to `/nodes/ai` — verify ai's detail loads (may show offline
    status if ai is unreachable — verify the "unreachable" state renders)

## Playbook 17: Display App Launch Flow — End-to-End

Test the complete launch flow for display apps: hub tile → launch page →
guest start → console page → KasmVNC iframe. This is the critical user
path for local kiosk users (through KasmVNC) and remote operators
(through SM).

### Prerequisites

- All display app containers exist and are configured (CT 400 Desktop,
  CT 301 Kodi on home; CT 302 Moonlight on mesh1)
- SSH tunnel to CM active (localhost:9098)
- SuperManager running in UI mode

### 17.1 Desktop launch from SuperManager `/hub`

1. Open `http://localhost:${WEBUI_PORT:-52525}/hub`
2. Locate the **Desktop** tile in the "Desktop & Media" section
3. Verify the tile shows a **"Launch"** badge (not "Not available")
4. Click the Desktop tile → verify navigation to `/launch?vmid=400&title=Desktop&url_key=DESKTOP_URL`
5. Verify the **launch page** renders:
   - Desktop icon or similar
   - "Desktop" title
   - Description text
   - **"Launch Desktop"** button with play_arrow icon
   - **"View Desktop Console"** button with cast icon
   - **"Back to Hub"** button
6. Click **"Launch Desktop"** → verify:
   - Status text updates: "Starting VMID 400..."
   - On success: "Desktop is running." in accent color
   - Auto-navigates to `/console/home/desktop?back=/hub`
7. Verify the **console page** renders with a **minimal thin viewer bar**:
   - **Back arrow** button
   - "Desktop on home" label
   - **visibility_off** toggle button
   - KasmVNC iframe below the bar showing the desktop
   - NO session switching buttons (SM is a thin client)
   - NO app switcher icons (navigation happens via SM pages)

### 17.2 Console page interaction

1. On `/console/home/desktop`:
2. Verify KasmVNC iframe shows the desktop environment
3. Interact inside the iframe: move mouse, click, type on keyboard
4. Click the **visibility_off** toggle → verify viewer bar hides
5. Click back button → verify return to previous page

Note: Session switching and app switching are done via the SM's own
navigation (e.g., navigate to `/console/home/kodi` directly) or within
the node's desktop environment through the KasmVNC iframe. The SM does
not embed control logic in the viewer bar.

### 17.3 App navigation — Desktop ↔ Kodi via SM routes

1. Navigate to `/console/home/desktop` — verify Desktop iframe loads
2. Navigate to `/console/home/kodi` — verify Kodi iframe loads
3. Verify each console page shows the correct app label in the
   viewer bar ("Desktop on home" vs "Kodi on home")
4. Verify each KasmVNC iframe connects to the correct display port

### 17.4 Remote kiosk from SM Nodes page

1. Navigate to `/nodes/home`
2. Click **Open Kiosk** → verify `/remote/home?back=/nodes/home`
3. Verify the kiosk display iframe loads (showing the hub page on home)
4. Click **Back** in the viewer bar → verify return to `/nodes/home`

### 17.5 Launch flow error cases

**Missing VMID:**

1. Navigate to `/launch?title=Test` (no vmid parameter)
2. Verify error message "No VMID configured" in red
3. Verify **Back to Hub** button present and works

**App not on this host:**

1. Navigate to `/console/home/moonlight`
2. Verify error page: "Moonlight is not available on home"
3. Verify **Go Back** button present and works

**Nonexistent node:**

1. Navigate to `/console/nonexistent/desktop`
2. Verify error page with "Host unreachable" message
3. Verify **Go Back** button present and works

### 17.6 Kodi launch from SuperManager `/hub`

1. Navigate to `/hub`
2. Click the **Kodi** tile → verify `/launch?vmid=301&title=Kodi&url_key=KODI_URL`
3. Click **"Launch Kodi"** → verify guest starts
4. Auto-navigates to `/console/home/kodi`
5. Verify Kodi home screen visible in the KasmVNC iframe
6. Interact: browse menus with mouse, use keyboard for search
7. Click **Back** → verify return to hub

### 17.7 Moonlight launch from mesh1's hub

Moonlight is on `streaming_nodes` (mesh1), NOT home.

1. Navigate to `/remote/mesh1?back=/nodes` (mesh1's kiosk via SM)
2. Inside mesh1's kiosk hub, click the **Moonlight** tile
3. Verify the launch page renders inside the kiosk iframe (Chromium)
4. Verify launch button shows "Launch Moonlight"
5. Click launch → verify guest starts and console opens
6. Verify Moonlight SDL2 UI visible in the nested iframe
7. Click Back → return to mesh1's hub

### 17.8 Display app conflict resolution

This tests the DRI3 device exclusive access — only one display app runs
at a time per host.

1. Navigate to `/console/home/desktop` — verify Desktop is running
2. Navigate to `/console/home/kodi` (via SM URL bar or sidebar)
3. Verify the Desktop LXC (CT 400) is stopped (the display transfer service stops
   conflicting apps before starting the new one)
4. Verify Kodi starts and its home screen appears in the iframe
5. Navigate back to `/console/home/desktop`
6. Verify Kodi stops, Desktop starts
7. Verify via CLI:
   ```bash
   ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
     "pct status 400; pct status 301"
   ```
   **Expected**: The most recently switched-to app is running, the other
   is stopped

## Playbook 18: External Web UI Testing via Hub Tiles

Test every external web service accessible through the hub's iframe
viewer (`/view?url=...`). Each service has a dedicated URL configured in
the kiosk's `config.json`. This tests iframe embedding, X-Frame-Options
compatibility, and service functionality through the viewer.

### Prerequisites

- SuperManager in UI mode
- All service containers running and healthy

### 18.1 Jellyfin via SM hub tile

1. Navigate to `http://localhost:${WEBUI_PORT:-52525}/hub`
2. Click the **Jellyfin** tile in "Desktop & Media" section
3. Verify navigation to `/view?url=http://10.10.10.15:8096&title=Jellyfin`
4. Verify the viewer page renders:
   - Top bar with **home** icon (left), "Jellyfin" title, **open_in_new**
     icon (right)
   - Service web UI iframe below the bar
5. Verify the Jellyfin web UI loads inside the iframe:
   - Login page or media library (depending on auth state)
   - Browse a show or movie listing
   - Verify mouse clicks work inside the iframe (click a media item)
6. Click the **home** icon → verify return to `/hub`

### 18.2 Home Assistant via SM hub tile

1. Click the **Home Assistant** tile
2. Verify navigation to `/view?url=http://10.10.10.14:8123&title=Home+Assistant`
3. Verify the HA dashboard loads in the iframe:
   - Entity cards visible
   - Click a card to see entity detail
4. Click home → return to hub

### 18.3 Router (OpenWrt LuCI) via SM hub tile

1. Click the **Router** tile in "Settings & Network"
2. Verify navigation to `/view?url=http://10.10.10.1/...&title=Router`
3. Verify LuCI status overview loads:
   - System info, memory, network statistics
   - Navigate to Network → Interfaces inside the iframe
4. Click home → return to hub

### 18.4 Pi-hole via SM hub tile

1. Click the **Pi-hole** tile
2. Verify `/view?url=http://10.10.10.10/admin/&title=Pi-hole`
3. Verify Pi-hole dashboard loads:
   - Query stats (total queries, blocked percentage)
   - Navigate to Query Log inside the iframe
4. Click home → return to hub

### 18.5 WireGuard via SM hub tile

1. Click the **WireGuard** tile
2. If configured: verify viewer loads WireGuard status page
   - Peer list visible, tunnel status
3. If NOT configured (no web UI URL): verify tile shows "Not available"
   badge — this is a valid outcome as WireGuard doesn't have a native
   web UI. Note this as expected behavior.

### 18.6 Netdata via SM hub tile

1. Click the **Netdata** tile in "Monitoring"
2. Verify navigation to `/view?url=http://10.10.10.41:19999&title=Netdata`
3. Verify real-time metrics dashboard loads:
   - CPU chart updating
   - Memory chart updating
   - Disk I/O chart
4. Click home → return to hub

### 18.7 Logs (rsyslog) via SM hub tile

1. Click the **Logs** tile in "Monitoring"
2. If configured: verify log viewer loads with recent entries
3. If NOT configured: verify "Not available" badge — rsyslog doesn't
   have a native web UI. Note this as expected behavior.

### 18.8 Gaming (Sunshine) via ai's hub

Gaming is only on ai. Test via SM remote kiosk:

1. Navigate to `/remote/ai?back=/nodes`
2. Inside ai's kiosk hub, click the **Gaming** tile
3. Verify viewer loads with Sunshine web UI (port 47990):
   - Login page or apps list
4. Click home → return to ai's hub
5. Click Back in viewer bar → return to `/nodes`

### 18.9 X-Frame-Options cross-check

After testing each service above, verify none were blocked by
X-Frame-Options headers:

```bash
echo "=== X-Frame-Options Audit ==="
for pair in "10.10.10.14:8123:HomeAssistant" "10.10.10.15:8096:Jellyfin" \
            "10.10.10.10:80:Pi-hole" "10.10.10.41:19999:Netdata" \
            "10.10.10.1:80:OpenWrt"; do
  ip_port="${pair%:*}"; name="${pair##*:}"
  echo -n "  $name: "
  ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
    "curl -sI http://$ip_port 2>/dev/null | grep -i 'x-frame-options'" 2>/dev/null
  echo "(empty = OK, no blocking)"
done
```

## Playbook 19: Internal Kiosk Pages — CM Interactive Elements Deep Test

Deep test every interactive element on the Cluster Manager's internal
pages (Bridge, Mesh, Router, Containers). PB14 verifies pages RENDER;
this playbook verifies every INTERACTIVE ELEMENT functions correctly.

### Prerequisites

- SSH tunnel to CM: `ssh -o StrictHostKeyChecking=no -L 9098:10.10.10.23:9001 root@$PRIMARY_HOST -N -f`
- All containers running and heartbeating

### 19.1 Bridge page — WiFi restart and status refresh

1. Open `http://localhost:9098/bridge`
2. Verify bridge-1 card shows: WiFi status (AP mode), SSID, band (2.4GHz),
   channel (11), signal quality
3. Verify bridge-2 card shows: WiFi status (STA mode), same SSID/band
4. Click **WiFi Restart** on bridge-1:
   - Verify API call triggers (`/api/bridge/wifi/restart`)
   - Verify status updates after a few seconds
   - Verify bridge-1 returns to AP mode with same SSID
5. Verify via CLI that WiFi actually restarted:
   ```bash
   ssh -o StrictHostKeyChecking=no root@$BRIDGE_1_HOST \
     "pct exec 104 -- /usr/sbin/wifi_setup.sh status"
   ```
6. Verify bridge-2 shows STA associated to bridge-1's SSID

### 19.2 Mesh page — batman enable/disable cycle

1. Open `http://localhost:9098/mesh`
2. Verify mesh topology diagram shows mesh nodes (mesh1, mesh2)
3. Verify **Batman Status** section — click status check:
   - Returns status for all mesh containers
4. Click **Enable Batman**:
   - Verify API call to `/api/batman/enable`
   - Verify response shows per-node results
5. Verify via CLI on mesh1:
   ```bash
   ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
     root@10.10.10.210 "pct exec 103 -- /usr/sbin/batman_trigger.sh status"
   ```
   **Expected**: `BATMAN=enabled`
6. Click **Disable Batman**:
   - Verify API call to `/api/batman/disable`
   - Verify response shows per-node disable results
7. Verify via CLI: `BATMAN=disabled`

### 19.3 Router page — data population verification

1. Open `http://localhost:9098/router`
2. Verify page is NOT blank — data cards must show real values
3. Verify WAN status section: IP, interface, link state
4. Verify LAN status section: IP (10.10.10.1), interface, DHCP range
5. Verify system info: hostname (openwrt-router), firmware version
6. Verify the page updates via heartbeat subscription (data from
   container heartbeat at 10.10.10.1, not direct SSH)

### 19.4 Containers page — guest lifecycle management

1. Open `http://localhost:9098/containers`
2. Verify guest list shows ALL VMs and CTs on home:
   - VM 100 (openwrt-router) — running
   - CT 101 (wireguard) — running
   - CT 102 (pihole) — running
   - CT 200 (homeassistant) — running
   - CT 300 (jellyfin) — running
   - CT 301 (kodi) — running/stopped
   - CT 400 (desktop) — running/stopped
   - CT 401 (kiosk) — running
   - CT 500 (netdata) — running
   - CT 501 (rsyslog) — running
3. Verify each entry shows: VMID, name, status badge (green running /
   grey stopped)
4. Verify **Start/Stop/Restart** buttons present for each guest
5. Click **Restart** on rsyslog (CT 501) — safe, non-critical:
   - Verify status briefly changes
   - Verify container returns to "running" within 10 seconds
6. Verify via CLI:
   ```bash
   ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST "pct status 501"
   ```
   **Expected**: `status: running`

## Playbook 20: SuperManager Display Pipeline — Every Host Verification

Exhaustive verification that every host's display pipeline works from
the SuperManager. This is the capstone test — it proves the full chain:
SM → URL resolution → port forwarding → KasmVNC iframe → interaction.

### Prerequisites

- All tunnels from PB12 active
- SuperManager in UI mode
- All kiosk containers running with kiosk-display active

### 20.1 Systematic node-by-node verification

For EACH of the 6 hosts below, perform ALL 7 steps. Record PASS/FAIL.

**Steps (repeat for each host):**

1. Navigate to `/nodes/{hostname}` on the SM
2. Verify the node detail page renders with host info
3. Click **Open Kiosk** → verify `/remote/{hostname}` loads
4. Verify KasmVNC iframe connects and shows the hub page
5. Move mouse inside the iframe — verify cursor moves
6. Type on keyboard — verify input reaches the display
7. Click a hub tile inside the iframe — verify navigation works
8. Click **Back** in the viewer bar → verify return to `/nodes/{hostname}`

| # | Host | WAN/LAN IP | Forwarding | Steps 1-8 |
|---|------|-----------|------------|-----------|
| 1 | home | 192.168.86.201 | socat (cross-bridge) | ______ |
| 2 | mesh1 | 10.10.10.210 | socat (LAN host) | ______ |
| 3 | ai | 192.168.86.220 | iptables DNAT | ______ |
| 4 | mesh2 | 192.168.86.211 | iptables DNAT | ______ |
| 5 | bridge-1 | 192.168.86.230 | iptables DNAT | ______ |
| 6 | bridge-2 | 192.168.86.231 | iptables DNAT | ______ |

### 20.2 Child picker drill-down — all children

From `/remote/home`:

1. Verify child picker dropdown shows: mesh1, ai, mesh2, bridge-1, bridge-2
2. For EACH child:
   a. Select child from dropdown
   b. Verify iframe disconnects from home, reconnects to child's kiosk
   c. Verify child's hub page visible through single-layer iframe
   d. Click one tile inside the iframe — verify it works
   e. Click **Back** → verify return to `/remote/home`
3. After testing all children, click **Back** → verify return to
   `/nodes/home`

### 20.3 Console page — every display app on every host

Test every display app console on its target host:

| App | Node | Route | What to verify |
|-----|------|-------|----------------|
| Desktop | home | `/console/home/desktop` | KDE desktop, session buttons |
| Kodi | home | `/console/home/kodi` | Kodi home screen |
| Moonlight | mesh1 | `/console/mesh1/moonlight` | Moonlight SDL2 UI |
| Kiosk | home | `/remote/home` | Hub page (via remote_kiosk) |
| Kiosk | mesh1 | `/remote/mesh1` | Hub page |
| Kiosk | ai | `/remote/ai` | Hub page |
| Kiosk | mesh2 | `/remote/mesh2` | Hub page |
| Kiosk | bridge-1 | `/remote/bridge-1` | Hub page |
| Kiosk | bridge-2 | `/remote/bridge-2` | Hub page |

For each: verify iframe loads, interaction works, back button works.

### 20.4 Non-available app verification

Verify that apps NOT deployed on a host show correct error state.
Note: `target_hosts` restrictions in `DISPLAY_APP_CONFIGS` control the
error message. Kodi (home only) and Moonlight (mesh1 only) have explicit
restrictions → "not available on {host}". Desktop has NO restriction →
falls through to URL resolution → "Host unreachable" when the display
port isn't forwarded.

1. `/console/home/moonlight` → "Moonlight is not available on home"
2. `/console/mesh1/kodi` → "Kodi is not available on mesh1"
3. `/console/ai/kodi` → "Kodi is not available on ai"
4. `/console/ai/moonlight` → "Moonlight is not available on ai"
5. `/console/bridge-1/kodi` → "Kodi is not available on bridge-1"
6. `/console/mesh1/desktop` → "Desktop on mesh1" viewer (Desktop is
   deployed on all `desktop_nodes` hosts — mesh1, ai, mesh2, bridge-1, bridge-2)
7. `/console/ai/desktop` → "Desktop on ai" viewer (same — Desktop on all hosts)

Each should show the error page with "Go Back" button.

### 20.5 Final sign-off matrix

Complete verification matrix. EVERY cell must be tested. Mark PASS/FAIL.

| Host | Kiosk (remote) | Desktop (console) | Kodi (console) | Moonlight (console) | Hub Tiles |
|------|:-:|:-:|:-:|:-:|:-:|
| **home** | __ | __ | __ | N/A __ | __ |
| **mesh1** | __ | __ | N/A __ | __ | __ |
| **ai** | __ | __ | N/A __ | N/A __ | __ |
| **mesh2** | __ | __ | N/A __ | N/A __ | __ |
| **bridge-1** | __ | __ | N/A __ | N/A __ | __ |
| **bridge-2** | __ | __ | N/A __ | N/A __ | __ |

- Blank `__` = needs testing. Fill with PASS or FAIL.
- "N/A __" = verify error page renders ("not available on {host}")
- Session switching is done within the desktop environment via the
  KasmVNC iframe, not via SM viewer bar buttons

## Playbook 21: WiFi Bridge Negotiation & Link Config Verification

Verify the cross-endpoint WiFi negotiation produces the correct link
configuration and that it is visible in both CLI and the kiosk UI.

### Prerequisites

- System fully converged (`molecule test` passed)
- Bridge containers (CT 104) running on bridge-1 and bridge-2
- WiFi link established (Playbook 4 shows `WIFI=up` on both)

### 21.1 Verify negotiated link parameters via CLI

```bash
# Check bridge-1 (AP) link config
ssh -o StrictHostKeyChecking=no root@$BRIDGE_1_HOST \
  "pct exec 104 -- /usr/sbin/wifi_setup.sh status"
```

**Expected output includes:**
- `BAND=6g` (or `5g` if 6 GHz is unavailable)
- `HTMODE=HE160` (or the best negotiated mode)
- `CHANNEL=<non-auto value>` (specific channel, not "auto")
- `WIDTH_MHZ=160` (or negotiated width)
- `NOSCAN=1` (coexistence scanning disabled for dedicated link)
- `POWER_SAVE=Power save: off` (performance mode)
- `DRIVER=iwlwifi` (or actual driver)
- `WIFI=up`

```bash
# Check bridge-2 (STA) — should show identical band/htmode/channel
ssh -o StrictHostKeyChecking=no root@$BRIDGE_2_HOST \
  "pct exec 104 -- /usr/sbin/wifi_setup.sh status"
```

**Expected:** Both sides show the SAME band, htmode, and channel.

### 21.2 Verify capabilities reporting

```bash
# Dump full capabilities from AP side
ssh -o StrictHostKeyChecking=no root@$BRIDGE_1_HOST \
  "pct exec 104 -- /usr/sbin/wifi_setup.sh capabilities"
```

**Expected output includes:**
- `PHY=phy0` (or detected PHY)
- `BANDS=2g,5g,6g` (all bands the hardware supports)
- `BAND_6G_AP_CHANNELS=...` (non-empty if 6 GHz AP mode is available)
- `BAND_5G_HE=yes` and `BAND_5G_VHT=yes` for AX210
- `SUPPORTS_WDS=yes`

### 21.3 Verify negotiation logic offline

```bash
# Run negotiation with live capabilities from both endpoints
AP_CAPS=$(ssh -o StrictHostKeyChecking=no root@$BRIDGE_1_HOST \
  "pct exec 104 -- /usr/sbin/wifi_setup.sh capabilities")
STA_CAPS=$(ssh -o StrictHostKeyChecking=no root@$BRIDGE_2_HOST \
  "pct exec 104 -- /usr/sbin/wifi_setup.sh capabilities")

python3 scripts/wifi_negotiate.py \
  --ap "$AP_CAPS" --sta "$STA_CAPS"
```

**Expected:** JSON output with `band`, `channel`, `htmode`, `width_mhz`,
and `reason` explaining the selection. The band should match what's
configured on the containers.

### 21.4 Verify heartbeat extensions include link config

```bash
# Check callhome extensions for bridge containers
curl -s http://localhost:${WEBUI_PORT:-52525}/api/container/openwrt-bridge/ready \
  | python3 -m json.tool
```

**Expected:** `extensions.bridge_status.link_config` contains `band`,
`htmode`, `channel`, `noscan` matching the configured values.

```bash
# Also check the UCI wireless extension
curl -s http://localhost:${WEBUI_PORT:-52525}/api/container/openwrt-bridge/ready \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('extensions',{}).get('uci_wireless',{}), indent=2))"
```

**Expected:** `radios[0]` shows the negotiated band, htmode, channel.

### 21.5 Verify Bridge page UI and UX in Cluster Manager kiosk

Access the CM kiosk at `http://localhost:9098/bridge` (or via
SM remote kiosk at `/remote/home`).

#### 21.5a Page structure and layout

**Expected page elements (top to bottom):**
1. **Page header**: "WiFi Bridge" with subtitle "Dedicated wireless backhaul link"
2. **"How It Works" expandable guide** (collapsed by default):
   - Click to expand — should show role diagram and setup steps
3. **Link banner**: AP/STA connection visualization with signal
4. **Node cards**: One per bridge endpoint
5. **Traffic card**: Sparkline chart
6. **Detail table**: Both nodes with all metrics
7. **Action buttons row**: Refresh, Restart WiFi, Force Re-pair,
   Deploy Bridge, Swap Roles

#### 21.5b "How It Works" setup guide

1. Click the **"How It Works"** expansion panel
2. **Role diagram** shows two cards:
   - **Bridge 1 — Broadcaster** (AP): "Sends the WiFi signal" with
     cell_tower icon and explanation about plugging into main switch
   - **Bridge 2 — Receiver** (STA): "Receives the WiFi signal" with
     router icon and explanation about remote location
3. **Setup Process** shows 3 numbered steps:
   - Step 1: **Deploy** — explains Ansible deployment and auto-negotiation
   - Step 2: **Negotiate** — explains hardware capability detection
   - Step 3: **Pair** — explains AP startup and STA auto-connection

#### 21.5c Link banner states

1. **When linked**: Shows Broadcaster icon ◄━━━ signal ━━━► Receiver
   with signal strength, link summary (e.g., "6 GHz · HE160 · ch1"),
   and uptime. Labels say "Broadcaster" and "Receiver" (not AP/STA)
2. **When not linked**: Shows diagnostic hints:
   - Both offline: "Both bridge hosts are offline. Deploy the bridge..."
   - One offline: "[name] is offline. Check that the host is powered on..."
   - Both online but unpaired: "Both hosts are online but not paired.
     Try Force Re-pair to reconnect the receiver."

#### 21.5d Node cards

1. Each card shows the node name with a role badge:
   - **"Broadcaster"** (teal badge) for the AP
   - **"Receiver"** (blue badge) for the STA
2. Paired status: green "Paired" or orange "Unpaired" badge
3. **Negotiated Link section** (when data available):
   - Band, HT Mode, Width, Channel, Driver, Co-ex Scan, Power Save
   - Tooltip: "The system automatically tested both endpoints'..."
4. **WiFi Interface section**: interface name, mode (with human label:
   "Broadcaster (AP)" or "Receiver (managed)"), SSID, channel
5. **Link Quality section**: signal with quality label, TX/RX bitrate,
   packets, bytes, failed/retries

#### 21.5e Action buttons

1. **Refresh Now**: Triggers immediate data refresh
2. **Restart WiFi**: Tooltip says "Restarts WiFi on both..." — click
   and verify notification shows success count
3. **Force Re-pair**: Tooltip says "Restarts WiFi on the receiver
   only..." — click and verify notification
4. **Deploy Bridge**: Tooltip says "Runs the full bridge deployment..."
   — click and verify navigation to Services page with "bridge" tag
   pre-selected
5. **Swap Roles**: Tooltip says "Swap which host is the broadcaster..."
   — click and verify confirmation dialog appears:
   - Dialog shows which node switches from which role to which
   - "The link will briefly disconnect" warning in orange
   - Cancel button closes dialog
   - "Swap Roles" button triggers the swap

#### 21.5f Detail table

1. Shows both nodes with columns: Node, Role (Broadcaster/Receiver),
   Paired, Band, HT Mode, Signal, TX Rate, RX Rate, Updated
2. Roles display as "Broadcaster"/"Receiver" (not raw AP/STA)
3. When linked, both nodes show matching Band and HT Mode values

### 21.6 Verify asymmetric hardware handling (if available)

If mesh containers use different WiFi hardware than bridge containers:

```bash
# Compare mesh1 capabilities
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 103 -- /usr/sbin/wifi_setup.sh capabilities"

# Compare mesh2 capabilities
ssh -o StrictHostKeyChecking=no root@$MESH_2_HOST \
  "pct exec 103 -- /usr/sbin/wifi_setup.sh capabilities"
```

**Expected:** Different hardware reports different capabilities
(different bands, widths, HE/VHT support). The negotiation module
correctly handles asymmetric inputs (unit tested in
`tests/test_wifi_negotiate.py::TestAsymmetricHardware`).

### 21.7 Sign-off checklist

| Check | bridge-1 (AP) | bridge-2 (STA) |
|-------|:---:|:---:|
| `wifi_setup.sh status` shows band/htmode/channel | __ | __ |
| `wifi_setup.sh capabilities` reports all bands | __ | __ |
| Both sides show matching band/htmode/channel | __ | __ |
| `NOSCAN=1` (co-ex scan disabled) | __ | __ |
| `POWER_SAVE=off` | __ | __ |
| Heartbeat `bridge_status.link_config` populated | __ | __ |
| `wifi_negotiate.py` CLI produces correct JSON | __ | __ |

**UI sign-off:**

| Check | Pass? |
|-------|:---:|
| "How It Works" guide expands and shows role diagram | __ |
| Role diagram shows Broadcaster/Receiver with descriptions | __ |
| Setup steps show Deploy → Negotiate → Pair | __ |
| Link banner uses "Broadcaster" / "Receiver" labels | __ |
| Link banner shows one-line config summary when linked | __ |
| Disconnected banner shows diagnostic hint | __ |
| Node cards show "Broadcaster"/"Receiver" role badges | __ |
| Node cards show "Negotiated Link" section with metrics | __ |
| Detail table uses "Broadcaster"/"Receiver" role names | __ |
| "Deploy Bridge" tooltip explains full deployment | __ |
| "Swap Roles" button shows confirmation dialog | __ |
| Swap confirmation dialog has Cancel and Swap buttons | __ |
| "Force Re-pair" tooltip explains STA-only restart | __ |

## Known Issues and Workarounds

### LAN IP collisions (FIXED)

**Root cause:** Ansible sorts group members alphabetically, not by platform
list order. The `lxc_wan_or_lan_network.yml` formula
`offset + groups[flavor_group].index(hostname)` computed different indices
than the documented allocation table (which assumed platform-list ordering).

**Fix (2026-04-10):** Moved all indexed-service offsets into non-overlapping
blocks that account for alphabetical group sorting:

| Service | Old Offset | New Offset | Group Size | Range |
|---------|-----------|-----------|------------|-------|
| Kiosk | 19 | 20 | 6 | .20-.25 |
| rsyslog | 12 | 30 | 4 | .30-.33 |
| Netdata | 21 | 40 | 4 | .40-.43 |

Single-host services (Pi-hole .10, HA .14, Jellyfin .15, Kodi .16,
Moonlight .17, Gaming .18) are unchanged — single-host groups always
have index 0, so the offset IS the IP.

### Bridge and mesh SSH errors

**Symptom:** Bridge and mesh pages on the Cluster Manager show SSH errors
(connection refused, permission denied).

**Cause:** The system is not fully converged. Manual testing MUST NOT
proceed until all nodes are on the LAN and the mesh is established.

**Fix:** Run `molecule test` to converge the full system. If SSH errors
persist after a successful converge, investigate the specific connectivity
failure — do NOT dismiss it as "pre-mesh."

### SM Bridge/Mesh/Router pages show "Not reachable"

**Symptom:** The SuperManager's Bridge, Mesh, and Router pages show "Not
reachable" errors, while the same pages on the Cluster Manager (via
localhost:9098) show full data.

**Cause:** These SM pages try to directly query container APIs (bridge
containers at 10.99.5.x, mesh containers at 10.10.10.x, router VM at
10.10.10.1). The controller machine is NOT on the fleet LAN or VPN, so
these IPs are unreachable from the browser.

**Expected behavior:** In a production deployment with VPN, the
controller would reach these IPs via WireGuard. In local test setups
without VPN, the SM Bridge/Mesh/Router pages show error states. The CM
versions work because the CM is inside the cluster network.

**Workaround:** Use the CM pages (localhost:9098/bridge, /mesh, /router)
for infrastructure monitoring during local testing. The SM pages will
work in production with VPN connectivity.

### SuperManager headless vs UI mode

The `molecule prepare` phase starts the SuperManager in headless mode
(`--headless`), which serves only API endpoints — no UI pages. To test
the full UI:

1. Kill the headless server: `kill $(cat .state/test_api.pid)`
2. Start UI mode: `python scripts/webui/app.py --port ${WEBUI_PORT:-52525} --env test.env &`
3. Update `.state/test_api.pid` with the new PID

The socat tunnel on the primary host continues forwarding heartbeats to
the new server without interruption.

## When tests fail

1. Check kiosk-web logs: `pct exec 401 -- journalctl -u kiosk-web -n 30`
2. Check callhome logs: `pct exec <vmid> -- journalctl -u callhome -n 10`
3. Check network connectivity between containers: `pct exec 401 -- ping -c1 <target_ip>`
4. Verify config.json: `pct exec 401 -- cat /opt/kiosk/config.json | python3 -m json.tool`
5. Check for IP collisions: `for ct in $(pct list | awk 'NR>1{print $1}'); do ip=$(pct config $ct | grep net0 | grep -oP 'ip=\K[^/]+'); echo "CT $ct: $ip"; done | sort -t: -k2`
6. Check ARP consistency: `ip neigh show dev vmbr1` — MAC should match `pct config` hwaddr

## Network topology (fully converged)

After a successful `molecule test` or `molecule converge`, all 6 nodes are
on the 10.10.10.x subnet via the WiFi mesh. The Cluster Manager (home kiosk
at 10.10.10.23) can reach all child Managers directly — no DNAT, no WAN IPs
needed.

Manual testing assumes this topology. If any host is still on its WAN IP
(192.168.86.x), the system is NOT fully converged and manual testing MUST
NOT proceed.

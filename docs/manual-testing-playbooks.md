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
for pair in "301:Kodi" "400:Desktop"; do
  vmid="${pair%%:*}"; name="${pair##*:}"
  echo -n "$name (VMID $vmid): "
  ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
    "pct exec 401 -- python3 -c \"
import urllib.request, json
req = urllib.request.Request('http://localhost:9001/api/guests/$vmid/start', method='POST')
r = urllib.request.urlopen(req, timeout=10)
d = json.loads(r.read())
print('OK' if d.get('success') else f'FAIL: {d.get(\\\"output\\\", d.get(\\\"error\\\"))}')
\"" 2>/dev/null || echo "FAILED"
done

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

# 3f. VNC service status on ALL hosts
echo ""
echo "=== VNC Services (all hosts) ==="
for pair in "$PRIMARY_HOST:home" "$AI_HOST:ai" "$MESH_2_HOST:mesh2" \
            "$BRIDGE_1_HOST:bridge-1" "$BRIDGE_2_HOST:bridge-2"; do
  host="${pair%%:*}"; label="${pair##*:}"
  echo -n "  $label: "
  ssh -o StrictHostKeyChecking=no root@$host \
    "pct exec 401 -- systemctl is-active kiosk-vnc kiosk-vnc-ws 2>/dev/null" 2>/dev/null
done
echo -n "  mesh1: "
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" \
  root@10.10.10.210 "pct exec 401 -- systemctl is-active kiosk-vnc kiosk-vnc-ws 2>/dev/null" 2>/dev/null
```

**Expected**: ALL checks pass. Any FAIL or BLOCKED result MUST be fixed before
proceeding to browser-based testing. Common failures:

| Failure | Root Cause | Fix |
|---------|-----------|-----|
| Display app "FAIL: already running" | Manager API doesn't treat running as success | Update `manager.py` `_api_guest_action` |
| Service URL FAIL: Connection refused | Service not listening or wrong IP in config | Check container networking, restart service |
| X-Frame-Options BLOCKED | Service sends SAMEORIGIN header | Add `use_x_frame_options: false` to service config |
| Router SSH FAILED | Kiosk can't reach router at 10.10.10.1 | Check LAN routing, SSH keys |
| VNC not active | sway/wayvnc crashed | `journalctl -u kiosk-display` inside container |

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

```bash
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
ssh -o StrictHostKeyChecking=no -L 9097:10.99.3.19:9001 root@$AI_HOST -N -f
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
Verify NO old services exist:

```bash
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "pct exec 401 -- systemctl is-active kiosk-vnc kiosk-vnc-ws 2>&1 || true"
```

**Expected**: `inactive` or `Unit kiosk-vnc.service not found` for both.

**Desktop VM check** (on home only):

```bash
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "qm guest exec 400 -- systemctl is-active desktop-display" 2>&1
# Verify NO host-side websockify
ssh -o StrictHostKeyChecking=no root@$PRIMARY_HOST \
  "systemctl is-active desktop-vnc-ws 2>&1 || echo 'correctly absent'"
```

**Expected**: `desktop-display` active inside VM, `desktop-vnc-ws` absent
on host.

### 13.2 SuperManager → ClusterManager drill-down via iframe

1. Open SuperManager at `http://localhost:${WEBUI_PORT:-52525}/nodes`
2. Click home host card → `/nodes/home`
3. Verify "Open Kiosk" button visible with `cast` icon
4. Click "Open Kiosk" → `/remote/home?back=/nodes/home`
5. Verify KasmVNC iframe loads showing the CM kiosk hub page — NOT a
   noVNC `<canvas>`, NOT an RFB connection status dot. The KasmVNC
   web client auto-hides its control bar when embedded in an iframe
6. Verify the viewer bar at the top shows "home" label with back button
7. Click bridge/mesh/router links inside the iframe — verify Chromium
   navigates within the kiosk (page changes visible through iframe)
8. Move mouse, type on keyboard — verify input reaches the KasmVNC
   display through the iframe
9. Click back button in top bar → verify return to `/nodes/home`

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

**Desktop (VMID 400):**

1. In the app switcher buttons on the viewer bar, click the Desktop
   icon (or navigate via SM: `/console/home/desktop`)
2. Verify the console page loads with Desktop label in the viewer bar
3. Verify the KasmVNC iframe shows the Desktop VM's virtual X11 desktop
   (desktop environment wallpaper, taskbar), NOT the kiosk hub, NOT
   a VGA BIOS stub. This is a SEPARATE session from the iGPU passthrough
   physical display
4. Interact: open a terminal, move windows, click the application menu,
   type text. Verify mouse and keyboard input work through the iframe
5. Click back button in viewer bar → verify return to previous page
6. Verify no noVNC status dot, no `<canvas>` element — pure iframe

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

Test these services in order:

| # | Tile | Section | What to verify in the iframe |
|---|------|---------|------------------------------|
| 1 | **Jellyfin** | Desktop & Media | Media library loads, browse a show/movie, start playback |
| 2 | **Home Assistant** | Desktop & Media | Dashboard loads with entity cards, click a card to see entity detail |
| 3 | **Gaming (Sunshine)** | Desktop & Media | Sunshine web UI loads, apps list or login page visible |
| 4 | **Router (OpenWrt)** | Settings & Network | LuCI status overview loads, navigate to Network → Interfaces |
| 5 | **Pi-hole** | Settings & Network | Dashboard loads with query stats, navigate to Query Log |
| 6 | **WireGuard** | Settings & Network | VPN status page loads, peer list visible |
| 7 | **Netdata** | Monitoring | Real-time metrics dashboard loads, CPU/memory charts updating |
| 8 | **Logs (rsyslog)** | Monitoring | Log viewer page loads, recent log entries visible |

If any service URL is not configured on home's kiosk (tile shows "Not
available" badge), note it as a gap but do NOT skip — verify the disabled
tile renders correctly (greyed out, non-clickable).

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
| 3 | **Router Detail** | `/router` | OpenWrt status, WAN/LAN interfaces, DHCP lease table |
| 4 | **Containers & VMs** | `/containers` | Guest list with VMIDs, names, status badges, start/stop controls |

---

**13.4d Two-level drill-down app launch**

Test that apps work after navigating SM → CM → NM via child picker:

1. Open `/remote/home?back=/nodes/home` — iframe to home's CM kiosk
2. Select "mesh1" from the child picker dropdown → iframe to mesh1
3. In mesh1's hub, verify which tiles are enabled and which show "Not
   available." Click each enabled tile — internal pages (Bridge, Mesh,
   Router, Containers) and any configured external services. Verify
   each loads and is interactive through the iframe
4. If Moonlight is configured, navigate to its console. Otherwise verify
   it shows "Not available" or "Launch" badge
5. Click back in the **parent page top bar** → return to `/remote/home`
6. Verify home's kiosk hub is visible again, iframe reconnected
7. Click back again → return to `/nodes/home` (SM page, no iframe)

### 13.5 Direct SuperManager → NodeManager display

1. From `/nodes/mesh1`, click "Open Kiosk" → KasmVNC iframe to mesh1
2. Verify single-layer iframe (lower latency than two-level)
3. Navigate mesh1's kiosk pages, launch an app, verify full control
4. Click back → return to `/nodes/mesh1`

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
20. Click **Open Kiosk** → verify VNC viewer opens at `/remote/mesh1`
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
  ssh -o StrictHostKeyChecking=no -L 9097:10.99.3.19:9001 root@$AI_HOST -N -f
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
| VNC remote control | Available for child nodes | Not available |
| Batman enable/disable | Broadcasts to all nodes | Local only |
| `/api/batman/status` | Returns cluster-wide status | Returns local only |

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

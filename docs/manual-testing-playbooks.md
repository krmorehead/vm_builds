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

Verify that image versioning is working end-to-end across the pipeline.

### Step 1: Verify manifest.json is complete

```bash
python3 -c "
import json
with open('images/manifest.json') as f:
    m = json.load(f)
print(f'Schema: v{m[\"schema_version\"]}')
print(f'Images: {len(m[\"images\"])} targets')
for name, info in sorted(m['images'].items()):
    sha = info['sha256'][:12] + '...' if info['sha256'] else '(not built)'
    print(f'  {name:20s} v{info[\"version\"]:8s}  {sha}  {info[\"filename\"]}')
"
```

**Expected**: All 14 targets listed with version `1.0.0`, non-empty SHA-256, and
versioned filenames (e.g., `pihole-1.0.0-debian-12-amd64.tar.zst`).

### Step 2: Verify all manifest images exist on disk

```bash
python3 -c "
import json, os
with open('images/manifest.json') as f:
    m = json.load(f)
missing = []
for name, info in sorted(m['images'].items()):
    path = f'images/{info[\"filename\"]}'
    if os.path.exists(path):
        size_mb = os.path.getsize(path) / (1024*1024)
        print(f'  OK  {name:20s} {size_mb:8.1f} MB  {info[\"filename\"]}')
    else:
        missing.append(name)
        print(f'  MISS {name:20s}            {info[\"filename\"]}')
if missing:
    print(f'\nMissing: {missing}')
else:
    print(f'\nAll images present.')
"
```

**Expected**: All images present (sunshine is optional — Windows build).

### Step 3: Verify Ansible resolves manifest lookups

```bash
ansible localhost -m debug -a "msg={{ pihole_lxc_template }} v{{ pihole_image_version }}" \
  -e "project_root=$(pwd)"
```

**Expected**: Shows `pihole-1.0.0-debian-12-amd64.tar.zst v1.0.0`.

### Step 4: Verify version stamps inside deployed containers

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

**Expected**: Each container shows `v1.0.0` (or the current manifest version).

### Step 5: Verify build-images.sh skip logic

```bash
./scripts/build-images.sh --only pihole 2>&1
```

**Expected**: Shows `Pihole image v1.0.0 exists: pihole-1.0.0-debian-12-amd64.tar.zst`.

### Step 6: Verify bump validation

```bash
./scripts/build-images.sh --bump invalidtarget patch 2>&1 || true
./scripts/build-images.sh --bump pihole foo 2>&1 || true
```

**Expected**: Both produce clear error messages.

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

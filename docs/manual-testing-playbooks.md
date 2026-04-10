# Manual Testing Playbooks

Step-by-step runbooks for verifying the 4-tier system on real hardware.
Every command targets real infrastructure — no mocks, no fabricated data.

## Prerequisites

```bash
set -a && source test.env && set +a
source .venv/bin/activate
```

Verify all 6 hosts reachable:

```bash
for h in $PRIMARY_HOST $AI_HOST $MESH_2_HOST $BRIDGE_1_HOST $BRIDGE_2_HOST; do
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@$h "hostname" 2>&1
done
ssh -o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -W %h:%p root@$PRIMARY_HOST" root@10.10.10.210 "hostname"
```

If ANY host is unreachable: FULL STOP. Do not proceed.

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

The Cluster Manager runs on home's kiosk (10.10.10.22:9001). Access via SSH tunnel:

```bash
ssh -o StrictHostKeyChecking=no -L 9099:10.10.10.22:9001 root@$PRIMARY_HOST -N -f
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

**Expected**: All containers on the LAN (home's + mesh1's) appear with `ready=True`.
Other hosts join when WiFi mesh brings them onto the 10.10.10.x subnet.

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

## When tests fail

1. Check kiosk-web logs: `pct exec 401 -- journalctl -u kiosk-web -n 30`
2. Check callhome logs: `pct exec <vmid> -- journalctl -u callhome -n 10`
3. Check network connectivity between containers: `pct exec 401 -- ping -c1 <target_ip>`
4. Verify config.json: `pct exec 401 -- cat /opt/kiosk/config.json | python3 -m json.tool`

## Network topology when fully configured

When the WiFi mesh is fully established, all 6 nodes are on the 10.10.10.x
subnet. The Cluster Manager (home kiosk at 10.10.10.22) can reach all child
Managers directly — no DNAT, no WAN IPs needed.

Until the mesh is established, only home and mesh1 (physically wired) are
on the LAN. Other hosts (ai, mesh2, bridge-1, bridge-2) remain on their
WAN IPs (192.168.86.x) and are not reachable from the Cluster Manager.

#!/bin/sh
# Call-home client for vm_builds fleet nodes.
#
# Compares the current IP against the last IP successfully sent home.
# Only contacts the server when the IP has changed (or on first run).
# Designed for a cron job: * * * * * /usr/local/bin/callhome.sh
#
# Works on any Linux: uses curl if available, falls back to wget (BusyBox).
#
# Required env vars (baked into /etc/default/callhome at deploy time):
#   CALLHOME_SERVER     Management server URL
#   CALLHOME_PUBLIC_KEY Auth token (derived from the server's private key)

CONF="/etc/default/callhome"
[ -f "$CONF" ] && . "$CONF"

[ -z "$CALLHOME_SERVER" ] && exit 1
[ -z "$CALLHOME_PUBLIC_KEY" ] && exit 1

STATE_DIR="/var/lib/callhome"
LAST_IP_FILE="$STATE_DIR/last_ip"

# Detect current primary IP
CURRENT_IP=""
if command -v hostname >/dev/null 2>&1; then
    CURRENT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
if [ -z "$CURRENT_IP" ]; then
    CURRENT_IP=$(ip -4 route get 8.8.8.8 2>/dev/null | awk '/src/ {print $7; exit}')
fi
[ -z "$CURRENT_IP" ] && exit 1

# Compare against last sent IP — skip if unchanged
if [ -f "$LAST_IP_FILE" ]; then
    LAST_IP=$(cat "$LAST_IP_FILE" 2>/dev/null)
    [ "$CURRENT_IP" = "$LAST_IP" ] && exit 0
fi

HOSTNAME=$(hostname 2>/dev/null || echo "unknown")
NODE_ID=$(hostname -f 2>/dev/null || echo "$HOSTNAME")
UPTIME=$(cut -d. -f1 /proc/uptime 2>/dev/null || echo "0")

DISK_PCT=$(df / 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
DISK_PCT=${DISK_PCT:-0}

MEM_TOTAL=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)
MEM_AVAIL=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)
MEM_TOTAL=${MEM_TOTAL:-0}
MEM_AVAIL=${MEM_AVAIL:-0}
if [ "$MEM_TOTAL" -gt 0 ] 2>/dev/null; then
    MEM_PCT=$(( (MEM_TOTAL - MEM_AVAIL) * 100 / MEM_TOTAL ))
else
    MEM_PCT=0
fi

PAYLOAD="{\"node_id\":\"$NODE_ID\",\"hostname\":\"$HOSTNAME\",\"local_ips\":[\"$CURRENT_IP\"],\"uptime_seconds\":$UPTIME,\"services\":[],\"disk_usage_pct\":$DISK_PCT,\"memory_usage_pct\":$MEM_PCT,\"version\":\"\"}"

URL="$CALLHOME_SERVER/api/checkin"
OK=0

if command -v curl >/dev/null 2>&1; then
    curl -sf -X POST "$URL" \
        -H "Content-Type: application/json" \
        -H "X-Callhome-Token: $CALLHOME_PUBLIC_KEY" \
        -d "$PAYLOAD" >/dev/null 2>&1 && OK=1
elif command -v wget >/dev/null 2>&1; then
    wget -qO- --post-data="$PAYLOAD" \
        --header="Content-Type: application/json" \
        --header="X-Callhome-Token: $CALLHOME_PUBLIC_KEY" \
        "$URL" >/dev/null 2>&1 && OK=1
fi

if [ "$OK" = "1" ]; then
    mkdir -p "$STATE_DIR" 2>/dev/null
    printf '%s' "$CURRENT_IP" > "$LAST_IP_FILE"
fi

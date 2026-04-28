#!/usr/bin/env bash
# Heartbeat watchdog — polls fleet stale API every second.
# Kills the ansible-playbook process group the instant:
#   1. Any previously-healthy service goes stale, OR
#   2. The SuperManager API itself is unreachable for 5 consecutive seconds, OR
#   3. No services appear within WARMUP_DEADLINE seconds (heartbeat chain broken)
#
# The SM API IS the heartbeat. If it's dead, the heart stopped.
# If services never appear, the heartbeat chain never connected. FAIL.
#
# Usage: heartbeat_watchdog.sh <API_URL> <PGID_FILE> <SERVICES> [MAX_AGE_SECONDS] [WARMUP_DEADLINE]
#
#   PGID_FILE: file containing the process group ID to monitor/kill
#   SERVICES: comma-separated list (e.g. "pihole,rsyslog,netdata")
#   WARMUP_DEADLINE: max seconds to wait for first heartbeat (default: 300)
#
# Writes its own PID to .state/watchdog.pid for cleanup.
# Exits cleanly when the entire process group is gone (run finished).

set -euo pipefail

API_URL="${1:?Usage: heartbeat_watchdog.sh <API_URL> <PGID_FILE> <SERVICES> [MAX_AGE_SECONDS] [WARMUP_DEADLINE]}"
PGID_FILE="${2:?Usage: heartbeat_watchdog.sh <API_URL> <PGID_FILE> <SERVICES> [MAX_AGE_SECONDS] [WARMUP_DEADLINE]}"
SERVICES="${3:?Usage: heartbeat_watchdog.sh <API_URL> <PGID_FILE> <SERVICES> [MAX_AGE_SECONDS] [WARMUP_DEADLINE]}"
MAX_AGE="${4:-60}"
WARMUP_DEADLINE="${5:-300}"

SCRIPT_DIR="${WATCHDOG_STATE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
PID_FILE="${SCRIPT_DIR}/.state/watchdog.pid"
SM_FAIL_THRESHOLD=5

mkdir -p "${SCRIPT_DIR}/.state"
echo $$ > "$PID_FILE"

if [ ! -f "$PGID_FILE" ]; then
    echo "[watchdog] PGID file not found: $PGID_FILE — exiting"
    rm -f "$PID_FILE"
    exit 1
fi
TARGET_PGID=$(cat "$PGID_FILE")
if [ -z "$TARGET_PGID" ]; then
    echo "[watchdog] PGID file is empty — exiting"
    rm -f "$PID_FILE"
    exit 1
fi

STALE_URL="${API_URL}/api/fleet/stale?services=${SERVICES}&max_age_seconds=${MAX_AGE}"
HEALTH_URL="${API_URL}/api/fleet/health"
WARM_UP=true
SM_FAIL_COUNT=0
WARMUP_ELAPSED=0

echo "[watchdog] Started (PID $$) monitoring PGID $TARGET_PGID"
echo "[watchdog] SM API: $HEALTH_URL"
echo "[watchdog] Services: $SERVICES"
echo "[watchdog] Max age: ${MAX_AGE}s"
echo "[watchdog] Warmup deadline: ${WARMUP_DEADLINE}s"

kill_target() {
    local reason="$1"
    echo ""
    echo "========================================================"
    echo "  HEARTBEAT WATCHDOG: $reason"
    echo "========================================================"
    echo "  Killing ansible-playbook process group (PGID $TARGET_PGID) NOW"
    echo "========================================================"
    echo ""

    kill -TERM -- -"$TARGET_PGID" 2>/dev/null || true
    sleep 2
    kill -KILL -- -"$TARGET_PGID" 2>/dev/null || true

    rm -f "$PID_FILE"
    exit 1
}

is_group_alive() {
    kill -0 -- -"$TARGET_PGID" 2>/dev/null
}

while true; do
    if ! is_group_alive; then
        echo "[watchdog] Process group $TARGET_PGID gone — run finished, exiting cleanly"
        rm -f "$PID_FILE"
        exit 0
    fi

    # Probe the SM API — if it's dead, count consecutive failures
    if ! curl -sf --max-time 3 "$HEALTH_URL" > /dev/null 2>&1; then
        SM_FAIL_COUNT=$((SM_FAIL_COUNT + 1))
        if [ "$SM_FAIL_COUNT" -ge "$SM_FAIL_THRESHOLD" ]; then
            kill_target "SUPERMANAGER API DEAD (${SM_FAIL_COUNT} consecutive failures at ${HEALTH_URL})"
        fi
        sleep 1
        continue
    fi
    SM_FAIL_COUNT=0

    RESPONSE=$(curl -sf --max-time 5 "$STALE_URL" 2>/dev/null) || {
        sleep 1
        continue
    }

    HAS_STALE=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('has_stale', False))" 2>/dev/null) || {
        sleep 1
        continue
    }

    if $WARM_UP; then
        TOTAL=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('healthy',[])) + len(d.get('stale',[])))" 2>/dev/null) || TOTAL=0
        if [ "$TOTAL" -gt 0 ] 2>/dev/null; then
            WARM_UP=false
            echo "[watchdog] Warm-up complete — $TOTAL services detected, monitoring active"
        else
            WARMUP_ELAPSED=$((WARMUP_ELAPSED + 1))
            if [ "$WARMUP_ELAPSED" -ge "$WARMUP_DEADLINE" ]; then
                kill_target "WARMUP DEADLINE EXCEEDED — no heartbeats received in ${WARMUP_DEADLINE}s. The heartbeat chain is BROKEN. Not a single service registered."
            fi
            if [ $((WARMUP_ELAPSED % 30)) -eq 0 ]; then
                echo "[watchdog] Still warming up... ${WARMUP_ELAPSED}/${WARMUP_DEADLINE}s — no services detected yet"
            fi
            sleep 1
            continue
        fi
    fi

    if [ "$HAS_STALE" = "True" ]; then
        STALE_SERVICES=$(echo "$RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d.get('stale', []):
    print(f\"  {s['service']} — last seen {s['last_seen']}\")
" 2>/dev/null)

        kill_target "STALE SERVICE DETECTED
$STALE_SERVICES"
    fi

    sleep 1
done

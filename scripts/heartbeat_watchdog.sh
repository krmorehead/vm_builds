#!/usr/bin/env bash
# Heartbeat watchdog — polls fleet stale API every second.
# Kills the target PID the instant any previously-healthy service goes dark.
#
# Usage: heartbeat_watchdog.sh <API_URL> <TARGET_PID> <SERVICES> [MAX_AGE_SECONDS]
#
#   SERVICES: comma-separated list (e.g. "pihole,rsyslog,netdata")
#
# Writes its own PID to .state/watchdog.pid for cleanup.
# Exits cleanly when TARGET_PID is gone (run finished on its own).

set -euo pipefail

API_URL="${1:?Usage: heartbeat_watchdog.sh <API_URL> <TARGET_PID> <SERVICES> [MAX_AGE_SECONDS]}"
TARGET_PID="${2:?Usage: heartbeat_watchdog.sh <API_URL> <TARGET_PID> <SERVICES> [MAX_AGE_SECONDS]}"
SERVICES="${3:?Usage: heartbeat_watchdog.sh <API_URL> <TARGET_PID> <SERVICES> [MAX_AGE_SECONDS]}"
MAX_AGE="${4:-60}"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="${SCRIPT_DIR}/.state/watchdog.pid"

mkdir -p "${SCRIPT_DIR}/.state"
echo $$ > "$PID_FILE"

STALE_URL="${API_URL}/api/fleet/stale?services=${SERVICES}&max_age_seconds=${MAX_AGE}"
WARM_UP=true

while true; do
    if ! kill -0 "$TARGET_PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        exit 0
    fi

    RESPONSE=$(curl -sf "$STALE_URL" 2>/dev/null) || {
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
        else
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

        echo ""
        echo "========================================================"
        echo "  HEARTBEAT WATCHDOG: STALE SERVICE DETECTED"
        echo "========================================================"
        echo "$STALE_SERVICES"
        echo "========================================================"
        echo "  Killing ansible-playbook (PID $TARGET_PID) NOW"
        echo "========================================================"
        echo ""

        kill -TERM -- -"$TARGET_PID" 2>/dev/null || kill -TERM "$TARGET_PID" 2>/dev/null || true
        sleep 2
        kill -KILL -- -"$TARGET_PID" 2>/dev/null || kill -KILL "$TARGET_PID" 2>/dev/null || true

        rm -f "$PID_FILE"
        exit 1
    fi

    sleep 1
done

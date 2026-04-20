#!/bin/sh
# Call-home client for vm_builds fleet nodes.
#
# Compares the current IP against the last IP successfully sent home.
# Only contacts the server when the IP has changed (or on first run).
# Designed for a cron job: * * * * * /usr/sbin/callhome.sh
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

# In container mode, always heartbeat (service state may have changed).
# In host mode, skip if IP hasn't changed since last check-in.
if [ -z "$CALLHOME_CONTAINER" ] && [ -f "$LAST_IP_FILE" ]; then
    read LAST_IP < "$LAST_IP_FILE" 2>/dev/null
    [ "$CURRENT_IP" = "$LAST_IP" ] && exit 0
fi

HOSTNAME=${CALLHOME_HOSTNAME:-$(cat /proc/sys/kernel/hostname 2>/dev/null || hostname 2>/dev/null || echo "unidentified")}
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

# Container mode: include init.d service health when CALLHOME_CONTAINER is set
CONTAINER_HEALTH=""
if [ -n "$CALLHOME_CONTAINER" ]; then
    CONTAINER_ID="${CALLHOME_CONTAINER:-$HOSTNAME}"
    SVC_JSON=""
    if [ -d /etc/init.d ]; then
        for svc in /etc/init.d/*; do
            sname="${svc##*/}"
            case "$sname" in boot|rcS|rc.common) continue;; esac
            if "$svc" enabled 2>/dev/null; then
                if "$svc" running 2>/dev/null; then
                    state="running"
                else
                    state="stopped"
                fi
                if [ -n "$SVC_JSON" ]; then SVC_JSON="$SVC_JSON,"; fi
                SVC_JSON="$SVC_JSON\"$sname\":\"$state\""
            fi
        done
    fi

    # Collect listening TCP (state 0A) and UDP (state 07) ports
    # Uses printf %d for hex conversion — portable across BusyBox/gawk
    PORTS_JSON=""
    _collect_ports() {
        local file="$1" state="$2"
        [ -f "$file" ] || return
        awk -v st="$state" '$4==st{split($2,a,":");print a[2]}' "$file" 2>/dev/null
    }
    PORTS_RAW=$( { _collect_ports /proc/net/tcp 0A; _collect_ports /proc/net/udp 07; } \
        | while read -r hex; do printf '%d\n' "0x$hex" 2>/dev/null; done \
        | sort -un )
    PORTS_JSON=$(echo "$PORTS_RAW" | awk 'NF{if(NR>1)printf ",";printf "%s",$0}')
    unset -f _collect_ports

    # Composable extensions: wifi radios, network interfaces
    EXT_JSON=""
    if [ -d /sys/class/ieee80211 ]; then
        PHY_COUNT=0
        for _p in /sys/class/ieee80211/phy*; do
            [ -d "$_p" ] && PHY_COUNT=$((PHY_COUNT + 1))
        done
        if [ "$PHY_COUNT" -gt 0 ]; then
            EXT_JSON="\"wifi\":{\"phy_count\":$PHY_COUNT}"
        fi
    fi

    # Network interfaces (non-lo)
    NET_JSON=""
    for iface_path in /sys/class/net/*; do
        iface="${iface_path##*/}"
        [ "$iface" = "lo" ] && continue
        operstate="unknown"
        [ -f "$iface_path/operstate" ] && read operstate < "$iface_path/operstate" 2>/dev/null
        mac=""
        [ -f "$iface_path/address" ] && read mac < "$iface_path/address" 2>/dev/null
        addrs=$(ip -4 -o addr show "$iface" 2>/dev/null | awk '{print $4}' | head -1)
        entry="{\"name\":\"$iface\",\"operstate\":\"$operstate\",\"mac\":\"$mac\""
        [ -n "$addrs" ] && entry="$entry,\"addresses\":[\"$addrs\"]" || entry="$entry,\"addresses\":[]"
        entry="$entry}"
        [ -n "$NET_JSON" ] && NET_JSON="$NET_JSON,"
        NET_JSON="$NET_JSON$entry"
    done
    if [ -n "$NET_JSON" ]; then
        GW=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')
        NET_EXT="\"network\":{\"interfaces\":[$NET_JSON],\"default_gateway\":\"${GW:-}\"}"
        [ -n "$EXT_JSON" ] && EXT_JSON="$EXT_JSON,$NET_EXT" || EXT_JSON="$NET_EXT"
    fi

    # UCI wireless config (OpenWrt only)
    if command -v uci >/dev/null 2>&1; then
        RADIOS_JSON=""
        for rdev in $(uci -q show wireless 2>/dev/null | sed -n 's/^wireless\.\(radio[0-9]*\)=wifi-device$/\1/p'); do
            disabled=$(uci -q get "wireless.$rdev.disabled" 2>/dev/null)
            channel=$(uci -q get "wireless.$rdev.channel" 2>/dev/null)
            htmode=$(uci -q get "wireless.$rdev.htmode" 2>/dev/null)
            band=$(uci -q get "wireless.$rdev.band" 2>/dev/null)
            entry="{\"radio\":\"$rdev\",\"disabled\":\"${disabled:-0}\",\"channel\":\"${channel:-auto}\""
            [ -n "$htmode" ] && entry="$entry,\"htmode\":\"$htmode\""
            [ -n "$band" ] && entry="$entry,\"band\":\"$band\""
            entry="$entry}"
            [ -n "$RADIOS_JSON" ] && RADIOS_JSON="$RADIOS_JSON,"
            RADIOS_JSON="$RADIOS_JSON$entry"
        done
        if [ -n "$RADIOS_JSON" ]; then
            UCI_EXT="\"uci_wireless\":{\"radios\":[$RADIOS_JSON]}"
            [ -n "$EXT_JSON" ] && EXT_JSON="$EXT_JSON,$UCI_EXT" || EXT_JSON="$UCI_EXT"
        fi
    fi

    # batman-adv mesh status
    if [ -d /sys/class/net/bat0 ]; then
        BAT_OPERSTATE="down"
        [ -f /sys/class/net/bat0/operstate ] && read BAT_OPERSTATE < /sys/class/net/bat0/operstate
        BAT_ORIGINATORS=0
        BAT_GW=""
        if command -v batctl >/dev/null 2>&1; then
            BAT_ORIGINATORS=$(batctl o 2>/dev/null | tail -n +3 | wc -l)
            BAT_GW=$(batctl gwl 2>/dev/null | awk 'NR==2{print $2}')
        fi
        BAT_EXT="\"batman\":{\"interface\":\"bat0\",\"operstate\":\"$BAT_OPERSTATE\",\"originators\":${BAT_ORIGINATORS:-0}"
        [ -n "$BAT_GW" ] && BAT_EXT="$BAT_EXT,\"gateway\":\"$BAT_GW\""
        BAT_EXT="$BAT_EXT}"
        [ -n "$EXT_JSON" ] && EXT_JSON="$EXT_JSON,$BAT_EXT" || EXT_JSON="$BAT_EXT"
    fi

    # WDS bridge link status (dedicated WiFi bridge containers)
    HAS_IWINFO=false; command -v iwinfo >/dev/null 2>&1 && HAS_IWINFO=true
    HAS_IW=false; command -v iw >/dev/null 2>&1 && HAS_IW=true
    if $HAS_IWINFO || $HAS_IW; then
        WDS_JSON=""
        for wif in /sys/class/net/wlan*; do
            [ -d "$wif" ] || continue
            wname="${wif##*/}"
            wstate="unknown"
            [ -f "$wif/operstate" ] && read wstate < "$wif/operstate"
            wmode="" bssid=""
            if $HAS_IWINFO; then
                _info=$(iwinfo "$wname" info 2>/dev/null)
                wmode=$(echo "$_info" | awk '/Mode:/{print $NF}')
                bssid=$(echo "$_info" | awk '/Access Point:/{print $NF}')
            elif $HAS_IW; then
                wmode=$(iw dev "$wname" info 2>/dev/null | awk '/type/{print $2}')
            fi
            entry="{\"iface\":\"$wname\",\"operstate\":\"$wstate\""
            [ -n "$wmode" ] && entry="$entry,\"mode\":\"$wmode\""
            [ -n "$bssid" ] && entry="$entry,\"bssid\":\"$bssid\""
            entry="$entry}"
            [ -n "$WDS_JSON" ] && WDS_JSON="$WDS_JSON,"
            WDS_JSON="$WDS_JSON$entry"
        done
        if [ -n "$WDS_JSON" ]; then
            # Enrich bridge_status with negotiated link config from UCI
            LINK_CFG=""
            if command -v uci >/dev/null 2>&1; then
                _br_radio=$(uci show wireless 2>/dev/null | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' | head -1)
                if [ -n "$_br_radio" ]; then
                    _br_band=$(uci -q get "wireless.$_br_radio.band" 2>/dev/null)
                    _br_htmode=$(uci -q get "wireless.$_br_radio.htmode" 2>/dev/null)
                    _br_channel=$(uci -q get "wireless.$_br_radio.channel" 2>/dev/null)
                    _br_noscan=$(uci -q get "wireless.$_br_radio.noscan" 2>/dev/null)
                    LINK_CFG=",\"link_config\":{\"band\":\"${_br_band:-unknown}\",\"htmode\":\"${_br_htmode:-unknown}\",\"channel\":\"${_br_channel:-auto}\",\"noscan\":\"${_br_noscan:-0}\"}"
                fi
            fi
            BRIDGE_EXT="\"bridge_status\":{\"links\":[$WDS_JSON]${LINK_CFG}}"
            [ -n "$EXT_JSON" ] && EXT_JSON="$EXT_JSON,$BRIDGE_EXT" || EXT_JSON="$BRIDGE_EXT"
        fi
    fi

    EXT_BLOCK=""
    [ -n "$EXT_JSON" ] && EXT_BLOCK=",\"extensions\":{$EXT_JSON}" || EXT_BLOCK=",\"extensions\":{}"

    IMAGE_VERSION=""
    [ -f /etc/image_version ] && read IMAGE_VERSION < /etc/image_version 2>/dev/null

    CONTAINER_HEALTH=",\"container_health\":{\"container_id\":\"$CONTAINER_ID\",\"systemd_services\":{$SVC_JSON},\"listening_ports\":[$PORTS_JSON],\"ready\":true,\"image_version\":\"$IMAGE_VERSION\"$EXT_BLOCK}"
fi

PAYLOAD="{\"node_id\":\"$NODE_ID\",\"hostname\":\"$HOSTNAME\",\"local_ips\":[\"$CURRENT_IP\"],\"uptime_seconds\":$UPTIME,\"services\":[],\"disk_usage_pct\":$DISK_PCT,\"memory_usage_pct\":$MEM_PCT,\"version\":\"\"$CONTAINER_HEALTH}"

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

#!/bin/sh
# batman_trigger.sh — Enable/disable batman-adv with HMAC-verified trigger.
#
# Usage:
#   batman_trigger.sh enable  <hmac_token>
#   batman_trigger.sh disable <hmac_token>
#   batman_trigger.sh status
#
# The enable/disable commands verify the HMAC token against /etc/batman_key.
# The status command requires no authentication.

KEY_FILE="/etc/batman_key"
BAT_IFACE="bat0"

# Detect the WDS interface dynamically from UCI config.
# Falls back to wlan0 if UCI has no wds0 section.
detect_wds_iface() {
    local dev
    dev=$(uci get wireless.wds0.device 2>/dev/null)
    if [ -n "$dev" ]; then
        # The UCI device name (e.g., radio0) maps to a phy, find its netdev
        local phy idx iface
        idx=$(echo "$dev" | sed 's/radio//')
        phy="phy${idx}"
        iface=$(ls "/sys/class/ieee80211/${phy}/device/net/" 2>/dev/null | head -1)
        [ -n "$iface" ] && echo "$iface" && return
    fi
    echo "wlan0"
}
WDS_IFACE=$(detect_wds_iface)

die() { echo "ERROR: $1" >&2; exit 1; }

verify_hmac() {
    local action="$1" token="$2"
    [ -f "$KEY_FILE" ] || die "No key file at $KEY_FILE"
    local key
    key=$(cat "$KEY_FILE")
    [ -n "$key" ] || die "Empty key file"

    # BusyBox-compatible HMAC: use openssl if available
    local expected
    if command -v openssl >/dev/null 2>&1; then
        expected=$(printf '%s' "${action}_batman" | openssl dgst -sha256 -hmac "$key" | awk '{print $NF}')
    else
        die "openssl not available for HMAC verification"
    fi

    [ "$token" = "$expected" ] || die "HMAC verification failed"
}

do_enable() {
    # Ensure kernel module is loaded (OpenWrt kmod-* does not auto-load)
    if ! lsmod | grep -q '^batman_adv'; then
        modprobe batman-adv 2>/dev/null || insmod /lib/modules/*/batman-adv.ko 2>/dev/null || true
    fi

    # Configure batman-adv via UCI, using detected WDS interface
    uci batch <<UCI
set network.bat0=interface
set network.bat0.proto=batadv
set network.bat0.routing_algo=BATMAN_IV
set network.bat0.orig_interval=1000
set network.bat0.gw_mode=off
set network.bat0.bridge_loop_avoidance=1
set network.bat0.distributed_arp_table=1

set network.bat0_hardif=batadv_hardif
set network.bat0_hardif.master=bat0
set network.bat0_hardif.device=$WDS_IFACE
UCI
    uci commit network

    # Add bat0 to br-lan if not already a member
    local br_members
    br_members=$(uci get network.@device[0].ports 2>/dev/null || echo "")
    case "$br_members" in
        *bat0*) ;;
        *) uci add_list network.@device[0].ports=bat0; uci commit network ;;
    esac

    /etc/init.d/network restart
    echo "OK: batman-adv enabled on $BAT_IFACE via $WDS_IFACE"
}

do_disable() {
    # Remove bat0 from br-lan
    uci del_list network.@device[0].ports=bat0 2>/dev/null
    # Remove batman config
    uci delete network.bat0_hardif 2>/dev/null
    uci delete network.bat0 2>/dev/null
    uci commit network

    /etc/init.d/network restart
    echo "OK: batman-adv disabled"
}

do_status() {
    if [ -d "/sys/class/net/$BAT_IFACE" ]; then
        echo "BATMAN=active"
        echo "INTERFACE=$BAT_IFACE"
        batctl meshif "$BAT_IFACE" o 2>/dev/null || echo "NO_ORIGINATORS"
        echo "---INTERFACES---"
        batctl meshif "$BAT_IFACE" if 2>/dev/null || echo "NO_INTERFACES"
    else
        echo "BATMAN=inactive"
    fi
}

case "$1" in
    enable)
        [ -n "$2" ] || die "Usage: $0 enable <hmac_token>"
        verify_hmac "enable" "$2"
        do_enable
        ;;
    disable)
        [ -n "$2" ] || die "Usage: $0 disable <hmac_token>"
        verify_hmac "disable" "$2"
        do_disable
        ;;
    status)
        do_status
        ;;
    *)
        die "Usage: $0 {enable|disable|status} [hmac_token]"
        ;;
esac

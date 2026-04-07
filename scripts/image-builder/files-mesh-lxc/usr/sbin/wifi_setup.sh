#!/bin/sh
# wifi_setup.sh — Configure WiFi WDS (AP/STA with 4-address mode).
#
# Container-side script baked into the OpenWrt mesh LXC image.
# Callable by Ansible (initial deploy) and the manager API (runtime).
#
# Usage:
#   wifi_setup.sh configure --mode ap|sta --ssid SSID --key KEY \
#       [--encryption sae] [--channel auto] [--country US]
#   wifi_setup.sh switch-mode ap|sta
#   wifi_setup.sh restart
#   wifi_setup.sh status
#   wifi_setup.sh metrics
#
# configure:   Full WiFi setup — detect radios, validate mode support,
#              generate UCI config, create WDS interface, restart.
# switch-mode: Change AP/STA mode preserving existing SSID/key/encryption.
# restart:     Restart WiFi (wifi down/up) without changing config.
# status:      Query current mode, radio state, interfaces (KEY=value).
# metrics:     Status + station dump + bridge info (for heartbeat collectors).

set -e

die() { echo "ERROR: $1" >&2; exit 1; }

# ── Radio detection ──────────────────────────────────────────────

detect_phy() {
    iw phy 2>/dev/null | sed -n 's/^Wiphy //p' | head -1
}

detect_all_phys() {
    iw phy 2>/dev/null | sed -n 's/^Wiphy //p'
}

check_mode_support() {
    local phy="$1" mode="$2"
    local modes
    modes=$(iw phy "$phy" info 2>/dev/null \
        | sed -n '/Supported interface modes/,/^[^[:space:]]/p')
    case "$mode" in
        ap)  echo "$modes" | grep -q 'AP' || die "WiFi adapter ($phy) does not support AP mode" ;;
        sta) echo "$modes" | grep -q 'managed' || die "WiFi adapter ($phy) does not support managed/station mode" ;;
        *)   die "Invalid mode: $mode (expected ap or sta)" ;;
    esac
}

detect_band() {
    local bands
    bands=$(iw phy 2>/dev/null | grep -oE 'Band [0-9]+' | sort -u)
    if echo "$bands" | grep -q 'Band 2'; then
        echo "5g"
    elif echo "$bands" | grep -q 'Band 1'; then
        echo "2g"
    else
        die "No WiFi bands detected. Check that the PHY is visible (iw phy)."
    fi
}

htmode_for_band() {
    case "$1" in
        5g) echo "HE80" ;;
        2g) echo "HE40" ;;
        *)  echo "HT20" ;;
    esac
}

# ── UCI radio setup ──────────────────────────────────────────────

ensure_radio_sections() {
    wifi config > /etc/config/wireless 2>/dev/null || true

    local radios
    radios=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio')

    if [ -z "$radios" ]; then
        local idx=0
        for phy in $(detect_all_phys); do
            uci set "wireless.radio${idx}=wifi-device"
            uci set "wireless.radio${idx}.type=mac80211"
            uci set "wireless.radio${idx}.phy=${phy}"
            uci set "wireless.radio${idx}.channel=auto"
            uci set "wireless.radio${idx}.disabled=1"
            uci commit wireless
            idx=$((idx + 1))
        done
        radios=$(uci show wireless 2>/dev/null \
            | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
            | grep '^radio')
    fi

    [ -n "$radios" ] || die "No radio sections in UCI after setup. Check wpad-openssl and mac80211."
    echo "$radios"
}

configure_radios() {
    local band="$1" country="$2" channel="$3"
    local htmode
    htmode=$(htmode_for_band "$band")
    local radios
    radios=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio')

    for radio in $radios; do
        uci set "wireless.${radio}.disabled=0"
        uci set "wireless.${radio}.country=${country}"
        uci set "wireless.${radio}.band=${band}"
        uci set "wireless.${radio}.htmode=${htmode}"
        if [ "$channel" != "auto" ]; then
            uci set "wireless.${radio}.channel=${channel}"
        fi
    done
}

remove_default_ifaces() {
    for iface in $(uci show wireless 2>/dev/null \
        | grep '=wifi-iface' | grep 'default_' \
        | cut -d= -f1 | cut -d. -f2); do
        uci delete "wireless.$iface" 2>/dev/null || true
    done
}

create_wds_iface() {
    local mode="$1" ssid="$2" key="$3" encryption="$4"
    local first_radio
    first_radio=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio' | head -1)

    [ -n "$first_radio" ] || die "No radio found for WDS interface"

    uci set wireless.wds0=wifi-iface
    uci set "wireless.wds0.device=${first_radio}"
    uci set wireless.wds0.network=lan
    uci set "wireless.wds0.mode=${mode}"
    uci set "wireless.wds0.ssid=${ssid}"
    uci set "wireless.wds0.encryption=${encryption}"
    uci set "wireless.wds0.key=${key}"
    uci set wireless.wds0.wds=1
    if [ "$mode" = "ap" ]; then
        uci set wireless.wds0.hidden=1
    fi
}

# ── Subcommands ──────────────────────────────────────────────────

do_configure() {
    local mode="" ssid="" key="" encryption="sae" channel="auto" country="US"

    while [ $# -gt 0 ]; do
        case "$1" in
            --mode)       mode="$2"; shift 2 ;;
            --ssid)       ssid="$2"; shift 2 ;;
            --key)        key="$2"; shift 2 ;;
            --encryption) encryption="$2"; shift 2 ;;
            --channel)    channel="$2"; shift 2 ;;
            --country)    country="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    [ -n "$mode" ] || die "Missing --mode (ap or sta)"
    [ -n "$ssid" ] || die "Missing --ssid"
    [ -n "$key" ]  || die "Missing --key"

    local phy
    phy=$(detect_phy)
    [ -n "$phy" ] || die "No WiFi PHY detected (iw phy returned nothing)"

    check_mode_support "$phy" "$mode"

    local band
    band=$(detect_band)

    ensure_radio_sections >/dev/null
    configure_radios "$band" "$country" "$channel"
    remove_default_ifaces
    create_wds_iface "$mode" "$ssid" "$key" "$encryption"

    uci set network.lan.stp=1
    uci commit wireless
    uci commit network

    wifi down 2>/dev/null || true
    sleep 2
    wifi up 2>/dev/null || true

    echo "OK: WiFi configured as ${mode} on ${phy}"
    echo "SSID=${ssid}"
    echo "BAND=${band}"
    echo "PHY=${phy}"
    echo "MODE=${mode}"
}

do_switch_mode() {
    local new_mode="$1"
    [ -n "$new_mode" ] || die "Usage: wifi_setup.sh switch-mode ap|sta"
    case "$new_mode" in
        ap|sta) ;;
        *) die "Invalid mode: $new_mode (expected ap or sta)" ;;
    esac

    local phy
    phy=$(detect_phy)
    [ -n "$phy" ] || die "No WiFi PHY detected"
    check_mode_support "$phy" "$new_mode"

    local ssid key encryption
    ssid=$(uci get wireless.wds0.ssid 2>/dev/null)
    key=$(uci get wireless.wds0.key 2>/dev/null)
    encryption=$(uci get wireless.wds0.encryption 2>/dev/null || echo sae)

    [ -n "$ssid" ] || die "No existing WDS config (wireless.wds0.ssid). Run 'configure' first."
    [ -n "$key" ]  || die "No existing WDS key (wireless.wds0.key). Run 'configure' first."

    uci set "wireless.wds0.mode=${new_mode}"
    if [ "$new_mode" = "ap" ]; then
        uci set wireless.wds0.hidden=1
    else
        uci delete wireless.wds0.hidden 2>/dev/null || true
    fi
    uci commit wireless

    wifi down 2>/dev/null || true
    sleep 2
    wifi up 2>/dev/null || true

    echo "OK: WiFi mode switched to ${new_mode}"
    echo "SSID=${ssid}"
    echo "MODE=${new_mode}"
}

do_restart() {
    wifi down 2>/dev/null || true
    sleep 2
    wifi up 2>/dev/null || true

    local iface_count
    iface_count=$(iw dev 2>/dev/null | grep -cE 'type (AP|managed)' || echo 0)
    local mode
    mode=$(uci get wireless.wds0.mode 2>/dev/null || echo "unconfigured")
    echo "OK: WiFi restarted"
    echo "MODE=${mode}"
    echo "INTERFACES=${iface_count}"
}

do_status() {
    local phy mode ssid band
    phy=$(detect_phy)

    mode=$(uci get wireless.wds0.mode 2>/dev/null || echo "unconfigured")
    ssid=$(uci get wireless.wds0.ssid 2>/dev/null || echo "")
    band=$(uci get "wireless.$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | head -1).band" 2>/dev/null || echo "unknown")

    echo "PHY=${phy:-none}"
    echo "MODE=${mode}"
    echo "SSID=${ssid}"
    echo "BAND=${band}"

    local iface_count
    iface_count=$(iw dev 2>/dev/null | grep -cE 'type (AP|managed)' || echo 0)
    echo "INTERFACES=${iface_count}"

    if [ "$iface_count" -gt 0 ]; then
        echo "WIFI=up"
    else
        echo "WIFI=down"
    fi

    iw dev 2>/dev/null | grep -E '(Interface|type|channel|ssid)' || true
}

do_metrics() {
    do_status
    echo "---STATION_DUMP---"
    iw dev 2>/dev/null | sed -n 's/.*Interface //p' | while read -r iface; do
        echo "IFACE=${iface}"
        iw dev "$iface" station dump 2>/dev/null || true
    done
    echo "---BRIDGE---"
    brctl show br-lan 2>/dev/null || true
    brctl showstp br-lan 2>/dev/null | head -30 || true
}

# ── Main ─────────────────────────────────────────────────────────

case "$1" in
    configure)
        shift
        do_configure "$@"
        ;;
    switch-mode)
        shift
        do_switch_mode "$@"
        ;;
    restart)
        do_restart
        ;;
    status)
        do_status
        ;;
    metrics)
        do_metrics
        ;;
    *)
        die "Usage: $0 {configure|switch-mode|restart|status|metrics} [options]"
        ;;
esac

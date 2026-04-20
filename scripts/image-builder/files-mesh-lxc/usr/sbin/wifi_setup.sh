#!/bin/sh
# wifi_setup.sh — Configure WiFi WDS (AP/STA with 4-address mode).
#
# Container-side script baked into the OpenWrt mesh LXC image.
# Callable by Ansible (initial deploy) and the manager API (runtime).
#
# Usage:
#   wifi_setup.sh configure --mode ap|sta --ssid SSID --key KEY \
#       [--encryption sae] [--channel auto] [--country US] \
#       [--band auto] [--htmode auto]
#   wifi_setup.sh capabilities
#   wifi_setup.sh switch-mode ap|sta
#   wifi_setup.sh restart
#   wifi_setup.sh status
#   wifi_setup.sh metrics
#
# configure:     Full WiFi setup — detect radios, validate mode support,
#                generate UCI config, create WDS interface, restart.
#                With --band/--channel/--htmode auto, dynamically probes
#                for the best available parameters.
# capabilities:  Report structured KEY=value hardware capabilities.
#                Designed for cross-endpoint negotiation by wifi_negotiate.py.
# switch-mode:   Change AP/STA mode preserving existing SSID/key/encryption.
# restart:       Restart WiFi (wifi down/up) without changing config.
# status:        Query current mode, radio state, interfaces (KEY=value).
# metrics:       Status + station dump + bridge info (for heartbeat collectors).

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

detect_driver() {
    local phy="$1"
    local dev_path
    dev_path=$(readlink -f "/sys/class/ieee80211/${phy}/device/driver" 2>/dev/null)
    if [ -n "$dev_path" ]; then
        basename "$dev_path"
    else
        echo "unknown"
    fi
}

# ── LAR warmup ──────────────────────────────────────────────────

_get_lar_country() {
    iw reg get 2>/dev/null \
        | grep -A1 "phy#" | grep "country" \
        | sed -n 's/.*country \([A-Z0-9]*\).*/\1/p' | head -1
}

# Perform LAR warmup scan for iwlwifi. Creates a temporary managed
# interface, triggers a scan to receive beacons with Country IE, and
# waits for the firmware to update its regulatory domain from country 00.
# If keep_iface=1, the _lar_scan interface is left alive (required for
# the AP startup workaround where it must coexist with the AP interface).
_warmup_lar() {
    local phy="$1"
    local keep_iface="${2:-0}"
    local driver
    driver=$(detect_driver "$phy")
    [ "$driver" = "iwlwifi" ] || return 0

    local _phy_country
    _phy_country=$(_get_lar_country)

    if [ "$_phy_country" = "00" ] || [ -z "$_phy_country" ]; then
        iw phy "$phy" interface add _lar_scan type managed 2>/dev/null || true
        # Use locally administered MAC to avoid collision with AP BSSID
        local _base_mac
        _base_mac=$(ip link show _lar_scan 2>/dev/null \
            | sed -n 's/.*ether \([^ ]*\).*/\1/p')
        if [ -n "$_base_mac" ]; then
            local _first_octet
            _first_octet=$(echo "$_base_mac" | cut -d: -f1)
            local _rest
            _rest=$(echo "$_base_mac" | cut -d: -f2-6)
            # Set locally administered bit (OR with 0x02)
            _first_octet=$(printf '%02x' $(( 0x$_first_octet | 0x02 )))
            ip link set _lar_scan address "${_first_octet}:${_rest}" 2>/dev/null || true
        fi
        ip link set _lar_scan up 2>/dev/null || true
        iw dev _lar_scan scan trigger 2>/dev/null || true
        sleep 3

        local _lar_wait=0
        while [ $_lar_wait -lt 15 ]; do
            _phy_country=$(_get_lar_country)
            if [ "$_phy_country" != "00" ] && [ -n "$_phy_country" ]; then
                echo "LAR_DETECTED=${_phy_country}"
                break
            fi
            sleep 1
            # Re-trigger scan every 5 seconds in case first scan missed beacons
            if [ $(( (_lar_wait + 1) % 5 )) -eq 0 ]; then
                iw dev _lar_scan scan trigger 2>/dev/null || true
            fi
            _lar_wait=$((_lar_wait + 1))
        done

        if [ "$keep_iface" != "1" ]; then
            iw dev _lar_scan del 2>/dev/null || true
        fi
    fi
}

# Start an iwlwifi AP using the LAR workaround.
#
# iwlwifi's self-managed regulatory (LAR) resets to country 00 whenever
# hostapd creates an AP interface or sends NL80211_CMD_REQ_SET_REG.
# This blocks 5GHz/6GHz AP mode because channels are marked (no IR).
#
# Workaround: keep a managed _lar_scan interface alive (which preserves
# LAR state), pre-create the AP interface alongside it via iw (which does
# NOT reset LAR when a managed interface is present), then start hostapd
# on the pre-created interface.
#
# Requires: UCI wireless config already applied, country NOT set in UCI.
_start_iwlwifi_ap() {
    local phy="$1"
    local hostapd_conf="/var/run/hostapd-${phy}.conf"

    # Step 1: Generate hostapd config via wifi up (expected to fail on
    # 5GHz because LAR is at country 00 — we only need the config file)
    wifi up 2>/dev/null || true
    sleep 3

    [ -f "$hostapd_conf" ] || {
        echo "LAR_AP_MODE=config_missing"
        return 1
    }

    # Step 2: Kill hostapd and clean all interfaces
    killall hostapd 2>/dev/null || true
    sleep 1
    local dev
    for dev in $(iw dev 2>/dev/null | sed -n 's/.*Interface //p'); do
        iw dev "$dev" del 2>/dev/null || true
    done
    sleep 2

    # Step 3: LAR warmup — keep _lar_scan alive (keep_iface=1)
    _warmup_lar "$phy" 1

    # Step 4: Pre-create AP interface alongside _lar_scan (preserves LAR)
    local ap_iface
    ap_iface=$(grep '^interface=' "$hostapd_conf" | head -1 | cut -d= -f2)
    [ -n "$ap_iface" ] || ap_iface="${phy}-ap0"
    iw phy "$phy" interface add "$ap_iface" type __ap 2>/dev/null || true

    # Step 5: Start hostapd on the pre-created interface
    hostapd -s -B "$hostapd_conf" 2>/dev/null || true
    sleep 5

    # Verify AP reached ENABLED state
    if logread 2>/dev/null | grep -q "${ap_iface}: AP-ENABLED"; then
        echo "LAR_AP_MODE=success"
    else
        echo "LAR_AP_MODE=fallback_needed"
    fi
}

# ── Capability probing ──────────────────────────────────────────

_iw_phy_info=""

get_phy_info() {
    local phy="$1"
    if [ -z "$_iw_phy_info" ]; then
        _iw_phy_info=$(iw phy "$phy" info 2>/dev/null)
    fi
    echo "$_iw_phy_info"
}

_band_number_to_name() {
    case "$1" in
        1) echo "2g" ;;
        2) echo "5g" ;;
        4) echo "6g" ;;
        *) echo "unknown_band_$1" ;;
    esac
}

_parse_channels_for_band() {
    local phy_info="$1" band_num="$2"
    local in_band=0
    local channels="" ap_channels="" dfs_channels=""
    local freq ch flags

    local _tmpfile="/tmp/_parse_band_$$"
    echo "$phy_info" > "$_tmpfile"
    while IFS= read -r line; do
        if echo "$line" | grep -qE "Band ${band_num}:"; then
            in_band=1
            continue
        fi
        if [ "$in_band" = "1" ] && echo "$line" | grep -qE "Band [0-9]+:"; then
            break
        fi
        if [ "$in_band" = "1" ] && echo "$line" | grep -qE '^[[:space:]]+\* [0-9]+(\.[0-9]+)? MHz \['; then
            freq=$(echo "$line" | sed -n 's/.*\* \([0-9]*\)[.0-9]* MHz.*/\1/p')
            ch=$(echo "$line" | sed -n 's/.*\[\([0-9]*\)\].*/\1/p')
            flags=$(echo "$line" | sed 's/.*\]//')

            [ -z "$ch" ] && continue

            if echo "$flags" | grep -q 'disabled'; then
                continue
            fi

            channels="${channels:+${channels},}${ch}"

            if echo "$flags" | grep -qi 'radar'; then
                dfs_channels="${dfs_channels:+${dfs_channels},}${ch}"
            fi

            if ! echo "$flags" | grep -qi 'passive-scan\|no-IR\|no IR'; then
                ap_channels="${ap_channels:+${ap_channels},}${ch}"
            fi
        fi
    done < "$_tmpfile"
    rm -f "$_tmpfile"

    echo "CHANNELS=${channels}"
    echo "AP_CHANNELS=${ap_channels}"
    echo "DFS_CHANNELS=${dfs_channels}"
}

_detect_max_width() {
    local phy_info="$1" band_name="$2"
    local max_width=20

    case "$band_name" in
        2g)
            if echo "$phy_info" | grep -qE 'HE Phy Capabilities|HE MAC Capabilities'; then
                max_width=40
            elif echo "$phy_info" | grep -q 'Capabilities:.*HT'; then
                max_width=40
            fi
            ;;
        5g|6g)
            if echo "$phy_info" | grep -qE 'channel-width-[24].*160.*MHz|Supported Channel Width.*160'; then
                max_width=160
            elif echo "$phy_info" | grep -qE 'channel-width.*80.*MHz|Supported Channel Width.*80'; then
                max_width=80
            elif echo "$phy_info" | grep -qE 'VHT Capabilities'; then
                max_width=80
            elif echo "$phy_info" | grep -q 'Capabilities:.*HT'; then
                max_width=40
            fi
            ;;
    esac

    echo "$max_width"
}

_detect_he_support() {
    local phy_info="$1"
    echo "$phy_info" | grep -qE 'HE Phy Capabilities|HE MAC Capabilities' && echo "yes" || echo "no"
}

_detect_vht_support() {
    local phy_info="$1"
    echo "$phy_info" | grep -q 'VHT Capabilities' && echo "yes" || echo "no"
}

_detect_wds_support() {
    local phy_info="$1"
    if echo "$phy_info" | grep -q '4addr'; then
        echo "yes"
    elif echo "$phy_info" | grep -qE 'AP.*VLAN|AP/VLAN'; then
        echo "yes"
    else
        echo "no"
    fi
}

_detect_wpa3_support() {
    if [ -x /usr/sbin/wpad ] || [ -x /usr/sbin/hostapd ]; then
        if /usr/sbin/hostapd -v 2>&1 | grep -qi 'sae\|wpa3'; then
            echo "yes"
            return
        fi
        if wpad -v 2>&1 | grep -qi 'sae\|wpa3'; then
            echo "yes"
            return
        fi
    fi
    if [ -e /usr/lib/wpad-openssl ] || [ -e /lib/wpad-openssl ]; then
        echo "yes"
        return
    fi
    echo "no"
}

# ── Dynamic band/channel/width probing ──────────────────────────

probe_best_band() {
    local phy="$1" mode="$2"
    local phy_info best_band=""
    phy_info=$(get_phy_info "$phy")

    local band_num band_name
    for band_num in 4 2 1; do
        if ! echo "$phy_info" | grep -qE "Band ${band_num}:"; then
            continue
        fi
        band_name=$(_band_number_to_name "$band_num")

        if [ "$mode" = "ap" ]; then
            local parsed ap_ch
            parsed=$(_parse_channels_for_band "$phy_info" "$band_num")
            ap_ch=$(echo "$parsed" | sed -n 's/^AP_CHANNELS=//p')
            if [ -z "$ap_ch" ]; then
                continue
            fi
        fi

        best_band="$band_name"
        break
    done

    if [ -z "$best_band" ]; then
        echo "2g"
    else
        echo "$best_band"
    fi
}

probe_max_width() {
    local phy="$1" band="$2"
    local phy_info
    phy_info=$(get_phy_info "$phy")
    _detect_max_width "$phy_info" "$band"
}

select_best_channel() {
    local phy="$1" band="$2" width="$3"
    local phy_info band_num
    phy_info=$(get_phy_info "$phy")

    case "$band" in
        2g) band_num=1 ;;
        5g) band_num=2 ;;
        6g) band_num=4 ;;
        *)  echo "auto"; return ;;
    esac

    local parsed channels ap_channels dfs_channels
    parsed=$(_parse_channels_for_band "$phy_info" "$band_num")
    ap_channels=$(echo "$parsed" | sed -n 's/^AP_CHANNELS=//p')
    dfs_channels=$(echo "$parsed" | sed -n 's/^DFS_CHANNELS=//p')

    if [ -z "$ap_channels" ]; then
        echo "auto"
        return
    fi

    local non_dfs_ch="" ch
    local old_ifs="$IFS"
    IFS=","
    for ch in $ap_channels; do
        local is_dfs=0
        local dch
        for dch in $dfs_channels; do
            if [ "$ch" = "$dch" ]; then
                is_dfs=1
                break
            fi
        done
        if [ "$is_dfs" = "0" ]; then
            non_dfs_ch="${non_dfs_ch:+${non_dfs_ch},}${ch}"
        fi
    done
    IFS="$old_ifs"

    if [ -n "$non_dfs_ch" ]; then
        echo "$non_dfs_ch" | cut -d, -f1
    else
        echo "$ap_channels" | cut -d, -f1
    fi
}

htmode_for_params() {
    local band="$1" width="$2" phy="$3"
    local phy_info he vht
    phy_info=$(get_phy_info "$phy")
    he=$(_detect_he_support "$phy_info")
    vht=$(_detect_vht_support "$phy_info")

    if [ "$he" = "yes" ]; then
        echo "HE${width}"
    elif [ "$band" = "5g" ] && [ "$vht" = "yes" ]; then
        echo "VHT${width}"
    else
        echo "HT${width}"
    fi
}

# ── Performance tuning for dedicated links ──────────────────────

apply_performance_tuning() {
    local iface
    iface=$(iw dev 2>/dev/null | sed -n 's/.*Interface //p' | head -1)
    if [ -n "$iface" ]; then
        iw dev "$iface" set power_save off 2>/dev/null || true
    fi

    local radios radio
    radios=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio')
    for radio in $radios; do
        uci set "wireless.${radio}.noscan=1" 2>/dev/null || true
    done

    uci set wireless.wds0.dtim_period=3 2>/dev/null || true
    uci commit wireless 2>/dev/null || true
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
    local band="$1" country="$2" channel="$3" htmode="$4"
    local radios
    radios=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio')

    for radio in $radios; do
        uci set "wireless.${radio}.disabled=0"
        uci set "wireless.${radio}.country=${country}"
        uci set "wireless.${radio}.band=${band}"
        uci set "wireless.${radio}.htmode=${htmode}"
        uci set "wireless.${radio}.channel=${channel}"
    done
}

remove_default_ifaces() {
    for iface in $(uci show wireless 2>/dev/null \
        | grep '=wifi-iface' | grep 'default_' \
        | cut -d= -f1 | cut -d. -f2); do
        uci delete "wireless.$iface" 2>/dev/null || true
    done
}

ensure_bridge_lan() {
    local lan_dev
    lan_dev=$(uci -q get network.lan.device 2>/dev/null || echo "")

    if [ "$lan_dev" = "br-lan" ]; then
        return 0
    fi

    uci set network.br_lan=device
    uci set network.br_lan.type='bridge'
    uci set network.br_lan.name='br-lan'
    uci add_list network.br_lan.ports='eth0'
    uci set network.lan.device='br-lan'
    uci commit network
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

do_capabilities() {
    local phy
    phy=$(detect_phy)
    [ -n "$phy" ] || die "No WiFi PHY detected (iw phy returned nothing)"

    local driver
    driver=$(detect_driver "$phy")

    _warmup_lar "$phy"

    _iw_phy_info=""
    local phy_info
    phy_info=$(get_phy_info "$phy")

    echo "PHY=${phy}"
    echo "DRIVER=${driver}"

    local bands="" band_num band_name
    for band_num in 1 2 4; do
        if echo "$phy_info" | grep -qE "Band ${band_num}:"; then
            band_name=$(_band_number_to_name "$band_num")
            bands="${bands:+${bands},}${band_name}"
        fi
    done
    echo "BANDS=${bands}"

    for band_num in 1 2 4; do
        if ! echo "$phy_info" | grep -qE "Band ${band_num}:"; then
            continue
        fi
        band_name=$(_band_number_to_name "$band_num")
        local prefix="BAND_$(echo "$band_name" | tr 'a-z' 'A-Z')_"

        local parsed
        parsed=$(_parse_channels_for_band "$phy_info" "$band_num")
        local ch_line ap_line dfs_line
        ch_line=$(echo "$parsed" | sed -n 's/^CHANNELS=//p')
        ap_line=$(echo "$parsed" | sed -n 's/^AP_CHANNELS=//p')
        dfs_line=$(echo "$parsed" | sed -n 's/^DFS_CHANNELS=//p')

        echo "${prefix}CHANNELS=${ch_line}"
        echo "${prefix}AP_CHANNELS=${ap_line}"

        local max_w
        max_w=$(_detect_max_width "$phy_info" "$band_name")
        echo "${prefix}MAX_WIDTH=${max_w}"

        echo "${prefix}HE=$(_detect_he_support "$phy_info")"
        echo "${prefix}VHT=$(_detect_vht_support "$phy_info")"
        echo "${prefix}DFS_CHANNELS=${dfs_line}"
    done

    echo "SUPPORTS_WDS=$(_detect_wds_support "$phy_info")"
    echo "SUPPORTS_WPA3=$(_detect_wpa3_support)"
}

do_configure() {
    local mode="" ssid="" key="" encryption="sae" channel="auto" country="US" band="" htmode=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --mode)       mode="$2"; shift 2 ;;
            --ssid)       ssid="$2"; shift 2 ;;
            --key)        key="$2"; shift 2 ;;
            --encryption) encryption="$2"; shift 2 ;;
            --channel)    channel="$2"; shift 2 ;;
            --country)    country="$2"; shift 2 ;;
            --band)       band="$2"; shift 2 ;;
            --htmode)     htmode="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    [ -n "$mode" ] || die "Missing --mode (ap or sta)"
    [ -n "$ssid" ] || die "Missing --ssid"
    [ -n "$key" ]  || die "Missing --key"

    local phy
    phy=$(detect_phy)
    [ -n "$phy" ] || die "No WiFi PHY detected (iw phy returned nothing)"

    _warmup_lar "$phy"

    check_mode_support "$phy" "$mode"

    if [ -z "$band" ] || [ "$band" = "auto" ]; then
        band=$(probe_best_band "$phy" "$mode")
        echo "AUTO_BAND=${band}"
    fi

    if [ -z "$htmode" ] || [ "$htmode" = "auto" ]; then
        local width
        width=$(probe_max_width "$phy" "$band")
        if [ "$band" = "2g" ] && [ "$width" -gt 40 ]; then
            width=40
        fi
        htmode=$(htmode_for_params "$band" "$width" "$phy")
        echo "AUTO_HTMODE=${htmode}"
    fi

    if [ "$channel" = "auto" ]; then
        local width_num
        width_num=$(echo "$htmode" | sed 's/[^0-9]//g')
        channel=$(select_best_channel "$phy" "$band" "$width_num")
        echo "AUTO_CHANNEL=${channel}"
    fi

    ensure_radio_sections >/dev/null
    configure_radios "$band" "$country" "$channel" "$htmode"
    remove_default_ifaces
    ensure_bridge_lan
    create_wds_iface "$mode" "$ssid" "$key" "$encryption"

    uci set network.lan.stp=1
    uci commit wireless
    uci commit network

    local driver
    driver=$(detect_driver "$phy")

    if [ "$driver" = "iwlwifi" ] && [ "$mode" = "ap" ] && [ "$band" != "2g" ]; then
        # iwlwifi AP on 5GHz/6GHz needs the LAR workaround: hostapd's
        # regulatory update resets LAR to country 00 when country_code
        # is in the config. Remove country from UCI so the generated
        # hostapd config won't include it.
        local first_radio
        first_radio=$(uci show wireless 2>/dev/null \
            | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
            | grep '^radio' | head -1)
        uci delete "wireless.${first_radio}.country" 2>/dev/null || true
        uci commit wireless 2>/dev/null || true

        _start_iwlwifi_ap "$phy"
    else
        wifi down 2>/dev/null || true
        sleep 2
        wifi up 2>/dev/null || true
    fi

    apply_performance_tuning

    echo "OK: WiFi configured as ${mode} on ${phy}"
    echo "SSID=${ssid}"
    echo "BAND=${band}"
    echo "HTMODE=${htmode}"
    echo "CHANNEL=${channel}"
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

    local driver band
    driver=$(detect_driver "$phy")
    band=$(uci get "wireless.$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio' | head -1).band" 2>/dev/null || echo "2g")

    if [ "$driver" = "iwlwifi" ] && [ "$new_mode" = "ap" ] && [ "$band" != "2g" ]; then
        local first_radio
        first_radio=$(uci show wireless 2>/dev/null \
            | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
            | grep '^radio' | head -1)
        uci delete "wireless.${first_radio}.country" 2>/dev/null || true
        uci commit wireless 2>/dev/null || true
        _start_iwlwifi_ap "$phy"
    else
        wifi down 2>/dev/null || true
        sleep 2
        wifi up 2>/dev/null || true
    fi

    echo "OK: WiFi mode switched to ${new_mode}"
    echo "SSID=${ssid}"
    echo "MODE=${new_mode}"
}

do_restart() {
    local phy mode band driver
    phy=$(detect_phy)
    mode=$(uci get wireless.wds0.mode 2>/dev/null || echo "unconfigured")
    driver=$(detect_driver "${phy:-unknown}")

    local first_radio
    first_radio=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | grep '^radio' | head -1)
    band=$(uci get "wireless.${first_radio}.band" 2>/dev/null || echo "2g")

    if [ "$driver" = "iwlwifi" ] && [ "$mode" = "ap" ] && [ "$band" != "2g" ] && [ -n "$phy" ]; then
        _start_iwlwifi_ap "$phy"
    else
        wifi down 2>/dev/null || true
        sleep 2
        wifi up 2>/dev/null || true
    fi

    local iface_count
    iface_count=$(iw dev 2>/dev/null | grep -cE 'type (AP|managed)' || echo 0)
    echo "OK: WiFi restarted"
    echo "MODE=${mode}"
    echo "INTERFACES=${iface_count}"
}

do_status() {
    local phy mode ssid band htmode
    phy=$(detect_phy)

    local first_radio
    first_radio=$(uci show wireless 2>/dev/null \
        | sed -n 's/^wireless\.\([^.]*\)=wifi-device$/\1/p' \
        | head -1)

    mode=$(uci get wireless.wds0.mode 2>/dev/null || echo "unconfigured")
    ssid=$(uci get wireless.wds0.ssid 2>/dev/null || echo "")
    band=$(uci get "wireless.${first_radio}.band" 2>/dev/null || echo "unknown")
    htmode=$(uci get "wireless.${first_radio}.htmode" 2>/dev/null || echo "unknown")

    local channel noscan
    channel=$(uci get "wireless.${first_radio}.channel" 2>/dev/null || echo "unknown")
    noscan=$(uci get "wireless.${first_radio}.noscan" 2>/dev/null || echo "0")

    echo "PHY=${phy:-none}"
    echo "MODE=${mode}"
    echo "SSID=${ssid}"
    echo "BAND=${band}"
    echo "HTMODE=${htmode}"
    echo "CHANNEL=${channel}"
    echo "NOSCAN=${noscan}"

    local iface_count
    iface_count=$(iw dev 2>/dev/null | grep -cE 'type (AP|managed)' || echo 0)
    echo "INTERFACES=${iface_count}"

    if [ "$iface_count" -gt 0 ]; then
        echo "WIFI=up"
    else
        echo "WIFI=down"
    fi

    local iface
    iface=$(iw dev 2>/dev/null | sed -n 's/.*Interface //p' | head -1)
    if [ -n "$iface" ]; then
        local ps
        ps=$(iw dev "$iface" get power_save 2>/dev/null || echo "unknown")
        echo "POWER_SAVE=${ps}"
    fi

    local driver
    driver=$(detect_driver "${phy:-unknown}")
    echo "DRIVER=${driver}"

    local width_mhz=""
    case "$htmode" in
        HE160|VHT160) width_mhz="160" ;;
        HE80|VHT80)   width_mhz="80" ;;
        HE40|VHT40|HT40) width_mhz="40" ;;
        HE20|VHT20|HT20) width_mhz="20" ;;
    esac
    echo "WIDTH_MHZ=${width_mhz}"

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
    capabilities)
        do_capabilities
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
        die "Usage: $0 {configure|capabilities|switch-mode|restart|status|metrics} [options]"
        ;;
esac

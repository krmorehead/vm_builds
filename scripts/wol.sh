#!/usr/bin/env bash
set -euo pipefail

# Wake-on-LAN utility for vm_builds Proxmox hosts.
# Sends a magic packet to wake a host by MAC address or alias.
# Supports proxied WoL for LAN hosts behind the OpenWrt router.
#
# Usage:
#   ./wol.sh <mac-address>        Wake a single host by MAC
#   ./wol.sh <alias>              Wake a known host by name
#   ./wol.sh all                  Wake all known hosts
#   ./wol.sh --list               Show known hosts and MACs
#   ./wol.sh --wait <alias>       Wake and wait for SSH
#   ./wol.sh --wait all           Wake all and wait for SSH
#
# Prerequisites:
#   - wakeonlan on the controller (apt install wakeonlan)
#   - WoL enabled in BIOS on target hosts
#   - ethtool Wake-on: g on the management NIC (verified at setup time)
#
# LAN hosts (behind OpenWrt): WoL packet is sent from the primary host
# via SSH, since the controller has no L2 path to the LAN subnet.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load env for PRIMARY_HOST if available
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "${SCRIPT_DIR}/.env"; set +a
elif [[ -f "${SCRIPT_DIR}/test.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "${SCRIPT_DIR}/test.env"; set +a
fi

# ── Known hosts ────────────────────────────────────────────────────
# WAN hosts: directly reachable on the supernet (L2 from controller)
# LAN hosts: behind OpenWrt, WoL via proxy through PRIMARY_HOST
declare -A HOST_MAC=(
    [home]="8c:16:45:d1:87:a6"
    [ai]="6c:4b:90:c2:23:bf"    # PCIe NIC (nic0/r8169). USB adapter (nic1) won't wake from S5.
    [mesh2]="00:23:24:5b:83:76"
)

declare -A HOST_IP=(
    [home]="192.168.86.201"
    [ai]="192.168.86.220"
    [mesh2]="192.168.86.211"
)

# LAN hosts need WoL sent from the primary host, not the controller.
declare -A LAN_HOST_MAC=(
    [mesh1]="00:23:24:54:23:fa"
)
declare -A LAN_HOST_IP=(
    [mesh1]="10.10.10.210"
)

SSH_OPTS="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes"

usage() {
    cat <<'EOF'
Usage: ./wol.sh [--wait] <target>

Targets:
  <mac-address>   Send magic packet to a specific MAC (e.g., 8c:16:45:d1:87:a6)
  <alias>         Wake a known host by name (home, ai, mesh2, mesh1)
  all             Wake all known hosts
  --list          Show known hosts and their MAC addresses
  --wait          After sending WoL, poll until SSH is reachable (120s timeout)

Examples:
  ./wol.sh home                  # wake the home Proxmox host
  ./wol.sh --wait all            # wake all hosts, wait for SSH
  ./wol.sh 8c:16:45:d1:87:a6    # wake by MAC directly
  ./wol.sh mesh1                 # wake mesh1 via proxy through home
EOF
    exit 1
}

is_mac() {
    [[ "$1" =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]]
}

send_wol_local() {
    local name="$1" mac="$2"
    echo "Waking ${name} (${mac}) via local broadcast..."
    wakeonlan "$mac"
}

send_wol_via_proxy() {
    local name="$1" mac="$2" proxy_host="$3"
    echo "Waking ${name} (${mac}) via proxy ${proxy_host}..."
    # Python one-liner to send a magic packet. No extra packages needed
    # on the proxy host (Python 3 is always available on Proxmox).
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "root@${proxy_host}" \
        "python3 -c \"
import socket, struct
mac = '${mac}'.replace(':', '')
data = b'\\xff' * 6 + bytes.fromhex(mac) * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.sendto(data, ('255.255.255.255', 9))
s.close()
print('Magic packet sent')
\""
}

wait_for_host() {
    local name="$1" ip="$2" timeout="${3:-120}" ssh_extra="${4:-}"
    echo -n "  Waiting for ${name} (${ip})"
    local elapsed=0
    while (( elapsed < timeout )); do
        # shellcheck disable=SC2086
        if ssh $SSH_OPTS ${ssh_extra} "root@${ip}" "true" 2>/dev/null; then
            echo " up! (${elapsed}s)"
            return 0
        fi
        echo -n "."
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo " timeout after ${timeout}s"
    return 1
}

wake_host() {
    local name="$1"

    if [[ -n "${HOST_MAC[$name]+x}" ]]; then
        send_wol_local "$name" "${HOST_MAC[$name]}"
    elif [[ -n "${LAN_HOST_MAC[$name]+x}" ]]; then
        if [[ -z "${PRIMARY_HOST:-}" ]]; then
            echo "Error: PRIMARY_HOST not set — needed to proxy WoL to LAN host '${name}'" >&2
            echo "Set it in .env or test.env" >&2
            return 1
        fi
        send_wol_via_proxy "$name" "${LAN_HOST_MAC[$name]}" "${PRIMARY_HOST}"
    else
        echo "Error: unknown host '${name}'" >&2
        echo "Known WAN hosts: ${!HOST_MAC[*]}" >&2
        echo "Known LAN hosts: ${!LAN_HOST_MAC[*]:-none}" >&2
        return 1
    fi
}

wait_for_known_host() {
    local name="$1"
    if [[ -n "${HOST_IP[$name]+x}" ]]; then
        wait_for_host "$name" "${HOST_IP[$name]}"
    elif [[ -n "${LAN_HOST_IP[$name]+x}" ]]; then
        local proxy_args="-o ProxyJump=root@${PRIMARY_HOST}"
        wait_for_host "$name" "${LAN_HOST_IP[$name]}" 120 "$proxy_args"
    fi
}

# ── Main ───────────────────────────────────────────────────────────

if ! command -v wakeonlan &>/dev/null; then
    echo "Error: wakeonlan not found. Install it: sudo apt install wakeonlan" >&2
    exit 1
fi

[[ $# -lt 1 ]] && usage

WAIT=false
if [[ "$1" == "--wait" ]]; then
    WAIT=true
    shift
    [[ $# -lt 1 ]] && usage
fi

target="$1"

case "$target" in
    --list|-l)
        echo "WAN hosts (direct L2):"
        for alias in $(echo "${!HOST_MAC[@]}" | tr ' ' '\n' | sort); do
            printf "  %-10s %-20s %s\n" "$alias" "${HOST_MAC[$alias]}" "${HOST_IP[$alias]:-}"
        done
        if [[ ${#LAN_HOST_MAC[@]} -gt 0 ]]; then
            echo "LAN hosts (proxied via ${PRIMARY_HOST:-PRIMARY_HOST}):"
            for alias in $(echo "${!LAN_HOST_MAC[@]}" | tr ' ' '\n' | sort); do
                printf "  %-10s %-20s %s\n" "$alias" "${LAN_HOST_MAC[$alias]}" "${LAN_HOST_IP[$alias]:-}"
            done
        fi
        ;;
    all)
        for alias in $(echo "${!HOST_MAC[@]}" | tr ' ' '\n' | sort); do
            wake_host "$alias"
        done
        for alias in $(echo "${!LAN_HOST_MAC[@]}" | tr ' ' '\n' | sort); do
            wake_host "$alias"
        done
        echo ""
        total=$(( ${#HOST_MAC[@]} + ${#LAN_HOST_MAC[@]} ))
        echo "Magic packets sent to ${total} hosts."
        echo "Hosts typically take 1-3 minutes to boot after WoL."
        if $WAIT; then
            echo ""
            for alias in $(echo "${!HOST_MAC[@]}" | tr ' ' '\n' | sort); do
                wait_for_known_host "$alias"
            done
            for alias in $(echo "${!LAN_HOST_MAC[@]}" | tr ' ' '\n' | sort); do
                wait_for_known_host "$alias"
            done
        fi
        ;;
    *)
        if is_mac "$target"; then
            send_wol_local "unknown" "$target"
        else
            wake_host "$target"
            if $WAIT; then
                echo ""
                wait_for_known_host "$target"
            fi
        fi
        ;;
esac

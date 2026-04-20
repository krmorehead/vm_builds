#!/usr/bin/env bash
# Configure WireGuard VPN on the controller machine.
#
# NOTE: This script is now DEPRECATED. The controller VPN is baked into
# site.yml (the "Configure controller VPN tunnel" play) and runs
# automatically during molecule converge. This script is only needed
# if you want to manually set up the VPN outside the Ansible pipeline.
#
# Usage: sudo ./scripts/setup-controller-vpn.sh [--env test.env]

set -euo pipefail

ENV_FILE="${1:-}"
if [[ "$ENV_FILE" == "--env" ]]; then
    ENV_FILE="${2:-test.env}"
elif [[ -z "$ENV_FILE" ]]; then
    ENV_FILE="test.env"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

load_env() {
    local file="$1"
    if [[ -f "$file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$file"
        set +a
    fi
}

load_env "$PROJECT_ROOT/$ENV_FILE"

GEN_FILE="${ENV_FILE%.env}.env.generated"
if [[ "$ENV_FILE" == "test.env" ]]; then
    GEN_FILE="test.env.generated"
elif [[ "$ENV_FILE" == ".env" ]]; then
    GEN_FILE=".env.generated"
fi
load_env "$PROJECT_ROOT/$GEN_FILE"

PRIVKEY="${WIREGUARD_PRIVATE_KEY_CONTROLLER:-}"
HUB_PUBKEY="${WIREGUARD_HUB_PUBLIC_KEY:-}"
ENDPOINT="${WIREGUARD_SERVER_ENDPOINT:-}"
CTRL_IP="${CONTROLLER_VPN_IP:-10.0.0.7}"

if [[ -z "$PRIVKEY" ]]; then
    echo "ERROR: WIREGUARD_PRIVATE_KEY_CONTROLLER not found in $GEN_FILE"
    echo "Run 'molecule converge' first to generate keys."
    exit 1
fi

if [[ -z "$HUB_PUBKEY" ]]; then
    echo "ERROR: WIREGUARD_HUB_PUBLIC_KEY not found in $GEN_FILE"
    echo "Run 'molecule converge' first to generate the hub key."
    exit 1
fi

if [[ -z "$ENDPOINT" ]]; then
    echo "ERROR: WIREGUARD_SERVER_ENDPOINT not set in $ENV_FILE"
    exit 1
fi

if ! command -v wg &>/dev/null; then
    echo "Installing wireguard-tools..."
    apt-get update -qq && apt-get install -y -qq wireguard-tools
fi

echo "Configuring WireGuard on controller ($CTRL_IP/24)..."
echo "  Hub endpoint: $ENDPOINT"
echo "  Hub public key: ${HUB_PUBKEY:0:20}..."

cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
PrivateKey = $PRIVKEY
Address = $CTRL_IP/24

[Peer]
PublicKey = $HUB_PUBKEY
Endpoint = $ENDPOINT
AllowedIPs = 10.0.0.0/24
PersistentKeepalive = 25
EOF

chmod 600 /etc/wireguard/wg0.conf

if ip link show wg0 &>/dev/null; then
    echo "Restarting wg0..."
    wg-quick down wg0 2>/dev/null || true
fi

wg-quick up wg0
echo ""
echo "WireGuard VPN active:"
wg show wg0
echo ""
echo "Controller VPN IP: $CTRL_IP"
echo "Test with: ping -c1 10.0.0.1"

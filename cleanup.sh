#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
    echo "Usage: ./cleanup.sh <command> [args...] [env-file]"
    echo ""
    echo "Commands:"
    echo "  restore              Restore host config only (leave VMs as-is)"
    echo "  full-restore         Destroy current VMs, restore backed-up VMs + host config"
    echo "  clean                Destroy all VMs, restore host config (no VM restore)"
    echo "  rollback <feature>   Rollback a per-feature change (e.g., security, vlans, dns, mesh)"
    echo ""
    echo "Options:"
    echo "  env-file      Environment file to use (default: test.env)"
    echo ""
    echo "Examples:"
    echo "  ./cleanup.sh clean                      # test machine reset"
    echo "  ./cleanup.sh full-restore .env          # production rollback"
    echo "  ./cleanup.sh restore                    # config-only restore"
    echo "  ./cleanup.sh rollback security          # revert security hardening"
    echo "  ./cleanup.sh rollback vlans test.env    # revert VLANs on test machine"
    exit 1
}

[[ $# -lt 1 ]] && usage

COMMAND="$1"

case "$COMMAND" in
    rollback)
        [[ $# -lt 2 ]] && { echo "ERROR: rollback requires a feature name."; usage; }
        FEATURE="$2"
        ENV_FILE="${3:-test.env}"
        ;;
    *)
        ENV_FILE="${2:-test.env}"
        ;;
esac

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found."
    exit 1
fi

source .venv/bin/activate
set -a; source "$ENV_FILE"; set +a

case "$COMMAND" in
    restore)
        echo "Restoring host config on ${PRIMARY_HOST}..."
        python3 build.py --playbook cleanup --tags restore --env "$ENV_FILE"
        ;;
    full-restore)
        echo "Full restore (VMs + config) on ${PRIMARY_HOST}..."
        python3 build.py --playbook cleanup --tags full-restore --env "$ENV_FILE"
        ;;
    clean)
        echo "Clean reset (destroy VMs + restore config) on ${PRIMARY_HOST}..."
        python3 build.py --playbook cleanup --tags clean --env "$ENV_FILE"
        ;;
    rollback)
        ROLLBACK_TAG="openwrt-${FEATURE}-rollback"
        echo "Rolling back ${FEATURE} on ${PRIMARY_HOST} (tag: ${ROLLBACK_TAG})..."
        python3 build.py --playbook cleanup --tags "$ROLLBACK_TAG" --env "$ENV_FILE"
        ;;
    *)
        usage
        ;;
esac

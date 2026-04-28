#!/usr/bin/env bash
# Prune old LXC templates and vzdump backups on a Proxmox host.
#
# Keeps the N most recent versions of each service template (default: 2)
# and the most recent vzdump backup per VMID.
#
# Usage:
#   prune-templates.sh [--keep N] [--dry-run]
#
# Designed to run on the Proxmox host itself (called via SSH from
# build-images.sh or molecule cleanup).

set -euo pipefail

KEEP=2
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep)   KEEP="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

TEMPLATE_DIR="/var/lib/vz/template/cache"
BACKUP_DIR="/var/lib/ansible-backup"

# ── Template pruning ────────────────────────────────────────────────

prune_templates() {
    [ -d "$TEMPLATE_DIR" ] || return 0
    cd "$TEMPLATE_DIR"

    local before
    before=$(du -sh . 2>/dev/null | awk '{print $1}')
    local removed=0

    declare -A svc_files

    for f in *.tar.zst *.tar.xz; do
        [ -f "$f" ] || continue
        local svc
        svc=$(echo "$f" | sed -E 's/-[0-9]+\..*//')
        case "$svc" in
            debian-12-standard*|fedora-41*|build-base*) continue ;;
        esac
        svc_files[$svc]+="$f"$'\n'
    done

    for svc in "${!svc_files[@]}"; do
        local sorted
        sorted=$(echo -n "${svc_files[$svc]}" | while read -r f; do
            [ -n "$f" ] && echo "$(stat -c %Y "$f") $f"
        done | sort -rn)

        local i=0
        while IFS=' ' read -r _mtime fname; do
            [ -z "$fname" ] && continue
            i=$((i + 1))
            if [ "$i" -le "$KEEP" ]; then
                echo "KEEP:   $fname"
            else
                echo "REMOVE: $fname"
                if ! $DRY_RUN; then
                    rm -f "$fname"
                    rm -f "${fname%.tar.zst}.log" "${fname%.tar.xz}.log"
                fi
                removed=$((removed + 1))
            fi
        done <<< "$sorted"
    done

    rm -f vzdump-*.log 2>/dev/null || true

    local after
    after=$(du -sh . 2>/dev/null | awk '{print $1}')
    echo "Templates: removed $removed old versions ($before -> $after), keeping $KEEP per service"
}

# ── Vzdump backup pruning ──────────────────────────────────────────

prune_backups() {
    [ -d "$BACKUP_DIR" ] || return 0
    cd "$BACKUP_DIR"

    local before
    before=$(du -sh . 2>/dev/null | awk '{print $1}')
    local removed=0

    declare -A vmid_files

    for f in vzdump-*.vma.zst vzdump-*.vma.gz; do
        [ -f "$f" ] || continue
        local vmid
        vmid=$(echo "$f" | sed -E 's/vzdump-(lxc|qemu)-([0-9]+)-.*/\2/')
        vmid_files[$vmid]+="$f"$'\n'
    done

    for vmid in "${!vmid_files[@]}"; do
        local sorted
        sorted=$(echo -n "${vmid_files[$vmid]}" | while read -r f; do
            [ -n "$f" ] && echo "$(stat -c %Y "$f") $f"
        done | sort -rn)

        local i=0
        while IFS=' ' read -r _mtime fname; do
            [ -z "$fname" ] && continue
            i=$((i + 1))
            if [ "$i" -le 1 ]; then
                echo "KEEP:   $fname"
            else
                echo "REMOVE: $fname"
                if ! $DRY_RUN; then
                    rm -f "$fname"
                    rm -f "${fname%.vma.zst}.log" "${fname%.vma.gz}.log"
                fi
                removed=$((removed + 1))
            fi
        done <<< "$sorted"
    done

    local after
    after=$(du -sh . 2>/dev/null | awk '{print $1}')
    echo "Backups: removed $removed old archives ($before -> $after), keeping 1 per VMID"
}

# ── Main ───────────────────────────────────────────────────────────

echo "=== Pruning $(hostname) ==="
prune_templates
prune_backups
echo "Disk: $(df -h / | tail -1)"

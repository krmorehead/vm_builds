#!/bin/bash
# Generalized GPU passthrough hookscript for Proxmox VE.
#
# Manages GPU lifecycle for VMs with PCI passthrough:
#   pre-start  — stops GPU consumers, unbinds GPU from native driver,
#                binds to vfio-pci so QEMU can claim it.
#   post-stop  — unbinds from vfio-pci, rebinds native driver,
#                restarts GPU consumers that were stopped.
#
# Install:
#   cp gpu-passthrough-hook.sh /var/lib/vz/snippets/
#   chmod +x /var/lib/vz/snippets/gpu-passthrough-hook.sh
#   qm set <VMID> --hookscript local:snippets/gpu-passthrough-hook.sh
#
# Works with any GPU vendor (Intel, AMD, NVIDIA). Discovers PCI devices
# from the VM config — nothing is hardcoded.

set -euo pipefail

VMID="$1"
PHASE="$2"
STATE_DIR="/run/gpu-passthrough"
STATE_FILE="${STATE_DIR}/vm-${VMID}.state"
LOG_TAG="gpu-hook[${VMID}]"

log() { logger -t "$LOG_TAG" "$*"; echo "$*"; }

# ── Discover hostpci devices from VM config ─────────────────────────

get_hostpci_addresses() {
    qm config "$VMID" 2>/dev/null \
        | grep '^hostpci' \
        | sed 's/^hostpci[0-9]*: //' \
        | cut -d',' -f1
}

# ── Map PCI vendor ID to kernel driver name ─────────────────────────

get_native_driver() {
    local pci_addr="$1"
    local vendor
    vendor=$(cat "/sys/bus/pci/devices/${pci_addr}/vendor" 2>/dev/null || echo "")
    case "$vendor" in
        0x8086) echo "i915" ;;
        0x1002) echo "amdgpu" ;;
        0x10de) echo "nvidia" ;;
        *)      echo "" ;;
    esac
}

get_current_driver() {
    local pci_addr="$1"
    basename "$(readlink "/sys/bus/pci/devices/${pci_addr}/driver" 2>/dev/null)" 2>/dev/null || echo ""
}

# ── Find GPU consumers (LXC containers with /dev/dri bind mounts) ───

find_gpu_consumer_cts() {
    local running_cts
    running_cts=$(pct list 2>/dev/null | awk 'NR>1 && $2=="running" {print $1}')
    for ctid in $running_cts; do
        if grep -q '/dev/dri' "/etc/pve/lxc/${ctid}.conf" 2>/dev/null; then
            echo "$ctid"
        fi
    done
}

# ── Find VMs (other than ours) that reference the same PCI device ───

find_gpu_consumer_vms() {
    local target_pci="$1"
    local running_vms
    running_vms=$(qm list 2>/dev/null | awk 'NR>1 && $3=="running" {print $1}')
    for vmid in $running_vms; do
        [ "$vmid" = "$VMID" ] && continue
        if qm config "$vmid" 2>/dev/null | grep -q "hostpci.*${target_pci}"; then
            echo "$vmid"
        fi
    done
}

# ── Lifecycle phases ────────────────────────────────────────────────

do_pre_start() {
    mkdir -p "$STATE_DIR"
    local stopped_cts="" stopped_vms="" devices=""

    for pci_short in $(get_hostpci_addresses); do
        local pci_addr="0000:${pci_short}"
        local native_driver current_driver
        native_driver=$(get_native_driver "$pci_addr")
        current_driver=$(get_current_driver "$pci_addr")

        log "pre-start: GPU ${pci_addr} native=${native_driver} current=${current_driver}"

        # Stop LXC containers that use the GPU
        for ctid in $(find_gpu_consumer_cts); do
            log "pre-start: stopping LXC ${ctid} (GPU consumer)"
            pct shutdown "$ctid" --timeout 30 2>/dev/null || pct stop "$ctid" 2>/dev/null || true
            stopped_cts="${stopped_cts:+${stopped_cts} }${ctid}"
        done

        # Suspend VMs that reference the same PCI device
        for vmid in $(find_gpu_consumer_vms "$pci_short"); do
            log "pre-start: suspending VM ${vmid} (GPU consumer)"
            qm suspend "$vmid" 2>/dev/null || qm stop "$vmid" 2>/dev/null || true
            stopped_vms="${stopped_vms:+${stopped_vms} }${vmid}"
        done

        # Already on vfio-pci — nothing to do
        if [ "$current_driver" = "vfio-pci" ]; then
            log "pre-start: ${pci_addr} already on vfio-pci"
            devices="${devices:+${devices},}${pci_addr}:${native_driver}"
            continue
        fi

        # Unbind from native driver
        if [ -e "/sys/bus/pci/devices/${pci_addr}/driver" ]; then
            log "pre-start: unbinding ${pci_addr} from ${current_driver}"
            echo "$pci_addr" > "/sys/bus/pci/devices/${pci_addr}/driver/unbind" 2>/dev/null || true
            sleep 1
        fi

        # Bind to vfio-pci
        modprobe vfio-pci 2>/dev/null || true
        echo "vfio-pci" > "/sys/bus/pci/devices/${pci_addr}/driver_override"
        echo "$pci_addr" > /sys/bus/pci/drivers/vfio-pci/bind 2>/dev/null || true

        # Verify binding succeeded
        local post_driver
        post_driver=$(get_current_driver "$pci_addr")
        if [ "$post_driver" != "vfio-pci" ]; then
            log "pre-start: FAILED to bind ${pci_addr} to vfio-pci (got: ${post_driver})"
            exit 1
        fi
        log "pre-start: ${pci_addr} now on vfio-pci"
        devices="${devices:+${devices},}${pci_addr}:${native_driver}"
    done

    # Persist state for post-stop recovery
    cat > "$STATE_FILE" <<EOF
DEVICES="${devices}"
STOPPED_CTS="${stopped_cts}"
STOPPED_VMS="${stopped_vms}"
EOF
    log "pre-start: state saved to ${STATE_FILE}"
}

do_post_stop() {
    if [ ! -f "$STATE_FILE" ]; then
        log "post-stop: no state file — nothing to restore"
        exit 0
    fi

    # shellcheck source=/dev/null
    source "$STATE_FILE"

    # Restore each GPU device to its native driver
    IFS=',' read -ra dev_list <<< "${DEVICES:-}"
    for entry in "${dev_list[@]}"; do
        [ -z "$entry" ] && continue
        local pci_addr="${entry%%:*}"
        local native_driver="${entry##*:}"

        log "post-stop: restoring ${pci_addr} to ${native_driver}"

        # Unbind from vfio-pci
        if [ -e "/sys/bus/pci/drivers/vfio-pci/${pci_addr}" ]; then
            echo "$pci_addr" > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || true
            sleep 1
        fi

        # Clear driver override and rescan
        echo "" > "/sys/bus/pci/devices/${pci_addr}/driver_override" 2>/dev/null || true
        echo 1 > /sys/bus/pci/rescan 2>/dev/null || true
        sleep 2

        # PCI rescan does not auto-bind when the module is already loaded.
        # Explicitly bind to the native driver.
        if [ -n "$native_driver" ] && [ ! -e "/sys/bus/pci/devices/${pci_addr}/driver" ]; then
            modprobe "$native_driver" 2>/dev/null || true
            echo "$pci_addr" > "/sys/bus/pci/drivers/${native_driver}/bind" 2>/dev/null || true
            sleep 2
        fi

        # Wait for any render device to appear (GPU needs time to re-probe)
        for i in $(seq 1 10); do
            ls /dev/dri/renderD* >/dev/null 2>&1 && break
            sleep 1
        done

        local post_driver
        post_driver=$(get_current_driver "$pci_addr")
        log "post-stop: ${pci_addr} now on ${post_driver:-unbound}"
    done

    # Restart LXC containers that were stopped
    for ctid in ${STOPPED_CTS:-}; do
        log "post-stop: restarting LXC ${ctid}"
        pct start "$ctid" 2>/dev/null || true
    done

    # Resume VMs that were suspended
    for vmid in ${STOPPED_VMS:-}; do
        log "post-stop: resuming VM ${vmid}"
        qm resume "$vmid" 2>/dev/null || qm start "$vmid" 2>/dev/null || true
    done

    rm -f "$STATE_FILE"
    log "post-stop: GPU restore complete"
}

# ── Main dispatch ───────────────────────────────────────────────────

case "$PHASE" in
    pre-start)  do_pre_start ;;
    post-stop)  do_post_stop ;;
    # pre-stop and post-start are valid phases but we don't need them
    *)          log "phase ${PHASE}: no action" ;;
esac

exit 0

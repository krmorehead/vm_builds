#!/usr/bin/env bash
set -euo pipefail

# Builds custom images for the vm_builds project.
# Produces three outputs:
#   1. Mesh LXC rootfs     — minimal OpenWrt, no firewall, WiFi packages  (local build)
#   2. Router VM combined   — full OpenWrt with mesh/security/DNS packages (local build)
#   3. Pi-hole LXC template — Debian 12 with Pi-hole pre-installed         (remote build on Proxmox)
#
# Usage: ./build-images.sh [--clean] [--host <proxmox-ip>]
#   --clean          Remove cached Image Builder before downloading fresh copy
#   --host <ip>      Proxmox host for remote image builds (Pi-hole). Required for Pi-hole.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="${SCRIPT_DIR}/images"
BUILD_DIR="${SCRIPT_DIR}/.image-builder-cache"

OPENWRT_VERSION="24.10.0"
TARGET="x86"
SUBTARGET="64"
IB_NAME="openwrt-imagebuilder-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}.Linux-x86_64"
IB_ARCHIVE="${IB_NAME}.tar.zst"
IB_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}/targets/${TARGET}/${SUBTARGET}/${IB_ARCHIVE}"

MESH_FILES_DIR="${SCRIPT_DIR}/image-builder/files-mesh-lxc"

MESH_OUTPUT_NAME="openwrt-mesh-lxc-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}-rootfs.tar.gz"
ROUTER_OUTPUT_NAME="openwrt-router-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}-combined.img.gz"

# Pi-hole LXC template (built remotely on Proxmox via pct create/exec/vzdump)
PIHOLE_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
PIHOLE_OUTPUT_NAME="pihole-debian-12-amd64.tar.zst"
PIHOLE_BUILD_VMID=998

# Remote Proxmox host (set via --host flag)
PROXMOX_HOST=""
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# ── Package lists ────────────────────────────────────────────────────

# Mesh LXC: WiFi mesh node, no routing/firewall
MESH_PACKAGES=(
    # WiFi mesh
    wpad-mesh-openssl
    # WiFi CLI tool (namespace-aware detection via netlink)
    iw
    # Intel WiFi
    kmod-iwlwifi
    iwlwifi-firmware-iwl8265
    # MediaTek WiFi
    kmod-mt76
    # Atheros WiFi
    kmod-ath9k
    kmod-ath10k-ct
    ath10k-firmware-qca988x-ct
    # Remove packages that conflict or are unnecessary in LXC
    -wpad-basic-openssl
    -wpad-basic-wolfssl
    -wpad-basic-mbedtls
    -wpad-basic
    -wpad-mini
    -firewall4
    -nftables
    -odhcpd-ipv6only
    -dnsmasq
    -ppp
    -ppp-mod-pppoe
)

# Router VM: full router with mesh + security + DNS packages pre-installed
ROUTER_PACKAGES=(
    # WiFi mesh
    wpad-mesh-openssl
    # Intel WiFi
    kmod-iwlwifi
    iwlwifi-firmware-iwl8265
    # Diagnostics
    curl
    ip-full
    tcpdump
    # Encrypted DNS
    https-dns-proxy
    # Intrusion prevention
    banip
    # Mesh steering
    dawn
    # Remove conflicting default wpad
    -wpad-basic-openssl
    -wpad-basic-wolfssl
    -wpad-basic-mbedtls
    -wpad-basic
    -wpad-mini
)

# ── Functions ────────────────────────────────────────────────────────

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

remote_cmd() {
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "root@${PROXMOX_HOST}" "$@"
}

check_deps() {
    local missing=()
    for cmd in wget tar make zstd; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if (( ${#missing[@]} > 0 )); then
        die "Missing required tools: ${missing[*]}. Install them first."
    fi
}

download_imagebuilder() {
    mkdir -p "$BUILD_DIR"
    if [[ -d "${BUILD_DIR}/${IB_NAME}" ]]; then
        log "Image Builder already cached at ${BUILD_DIR}/${IB_NAME}"
        return
    fi

    log "Downloading OpenWrt Image Builder ${OPENWRT_VERSION}..."
    wget -q --show-progress -O "${BUILD_DIR}/${IB_ARCHIVE}" "$IB_URL"

    log "Extracting..."
    tar -I zstd -xf "${BUILD_DIR}/${IB_ARCHIVE}" -C "$BUILD_DIR"
    rm -f "${BUILD_DIR}/${IB_ARCHIVE}"
    log "Image Builder ready."
}

build_mesh_lxc() {
    log "Building mesh LXC rootfs..."
    local ib_dir="${BUILD_DIR}/${IB_NAME}"
    local pkg_list
    pkg_list=$(IFS=' '; echo "${MESH_PACKAGES[*]}")

    make -C "$ib_dir" image \
        PROFILE="generic" \
        PACKAGES="$pkg_list" \
        FILES="$MESH_FILES_DIR" \
        EXTRA_IMAGE_NAME="mesh-lxc" \
        2>&1 | tail -5

    local rootfs
    rootfs=$(find "${ib_dir}/bin" -name '*rootfs.tar.gz' -print -quit 2>/dev/null)
    if [[ -z "$rootfs" ]]; then
        die "Mesh LXC rootfs not found in Image Builder output"
    fi

    mkdir -p "$IMAGES_DIR"
    cp "$rootfs" "${IMAGES_DIR}/${MESH_OUTPUT_NAME}"
    log "Mesh LXC rootfs: ${IMAGES_DIR}/${MESH_OUTPUT_NAME}"
    log "  Size: $(du -h "${IMAGES_DIR}/${MESH_OUTPUT_NAME}" | cut -f1)"
}

build_router_vm() {
    log "Building router VM image..."
    local ib_dir="${BUILD_DIR}/${IB_NAME}"
    local pkg_list
    pkg_list=$(IFS=' '; echo "${ROUTER_PACKAGES[*]}")

    # Clean previous build artifacts to avoid profile collision
    make -C "$ib_dir" clean 2>/dev/null || true

    make -C "$ib_dir" image \
        PROFILE="generic" \
        PACKAGES="$pkg_list" \
        EXTRA_IMAGE_NAME="router" \
        2>&1 | tail -5

    local combined
    combined=$(find "${ib_dir}/bin" -name '*combined-ext4.img.gz' -print -quit 2>/dev/null)
    if [[ -z "$combined" ]]; then
        combined=$(find "${ib_dir}/bin" -name '*combined*.img.gz' -print -quit 2>/dev/null)
    fi
    if [[ -z "$combined" ]]; then
        die "Router VM image not found in Image Builder output"
    fi

    mkdir -p "$IMAGES_DIR"
    cp "$combined" "${IMAGES_DIR}/${ROUTER_OUTPUT_NAME}"
    log "Router VM image: ${IMAGES_DIR}/${ROUTER_OUTPUT_NAME}"
    log "  Size: $(du -h "${IMAGES_DIR}/${ROUTER_OUTPUT_NAME}" | cut -f1)"
}

cleanup_pihole_build() {
    local vmid="${PIHOLE_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_pihole_lxc() {
    log "Building Pi-hole LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${PIHOLE_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${PIHOLE_OUTPUT_NAME}"
    local vmid="${PIHOLE_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Pi-hole template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Pi-hole build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${PIHOLE_BASE_TEMPLATE}"
    fi

    trap cleanup_pihole_build EXIT

    # Ensure no stale build container exists
    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    # Upload base template if not already cached on host
    local remote_template="/var/lib/vz/template/cache/${PIHOLE_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    # Detect the management bridge (carries the default route)
    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${PIHOLE_BASE_TEMPLATE} \
        --hostname pihole-build \
        --memory 512 \
        --cores 1 \
        --rootfs local-lvm:2 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --features nesting=1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container networking..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network connectivity after 60s"
        fi
        sleep 2
    done
    log "Container has network access."

    # Force reliable DNS — DHCP may inject an ISP nameserver that doesn't resolve
    remote_cmd "pct exec ${vmid} -- bash -c 'echo nameserver 8.8.8.8 > /etc/resolv.conf'"

    log "Pre-seeding pihole.toml for v6 unattended install..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        groupadd -r pihole 2>/dev/null || true
        useradd -r -g pihole -s /usr/sbin/nologin -d /home/pihole pihole 2>/dev/null || true
        mkdir -p /etc/pihole
        chown pihole:pihole /etc/pihole
        chmod 775 /etc/pihole
        cat > /etc/pihole/pihole.toml << TOML_EOF
[dns]
upstreams = [\"1.1.1.1\", \"1.0.0.1\"]
TOML_EOF
        chown pihole:pihole /etc/pihole/pihole.toml
    '"

    log "Installing Pi-hole v6 (this takes 1-3 minutes)..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        export PIHOLE_SKIP_OS_CHECK=true
        apt-get update -qq
        apt-get install -y --no-install-recommends curl procps ca-certificates
        curl -sSL https://install.pi-hole.net -o /tmp/pihole-install.sh
        bash /tmp/pihole-install.sh --unattended
        rm -f /tmp/pihole-install.sh
        apt-get clean
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

    # Find the vzdump archive
    local vzdump_file
    vzdump_file=$(remote_cmd "ls -t /tmp/vzdump-lxc-${vmid}-*.tar.zst 2>/dev/null | head -1")
    if [[ -z "$vzdump_file" ]]; then
        remote_cmd "pct destroy ${vmid} --purge 2>/dev/null; true"
        die "vzdump archive not found on Proxmox host"
    fi
    log "vzdump archive: ${vzdump_file}"

    log "Downloading template to ${output}..."
    mkdir -p "$IMAGES_DIR"
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:${vzdump_file}" "$output"

    log "Cleaning up build container and vzdump archive..."
    remote_cmd "pct destroy ${vmid} --purge 2>/dev/null; rm -f '${vzdump_file}'; true"

    trap - EXIT

    log "Pi-hole LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# ── Main ─────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            log "Cleaning cached Image Builder..."
            rm -rf "$BUILD_DIR"
            shift
            ;;
        --host)
            [[ -n "${2:-}" ]] || die "--host requires an IP argument"
            PROXMOX_HOST="$2"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

check_deps
download_imagebuilder
build_mesh_lxc
build_router_vm
build_pihole_lxc

log ""
log "Done. Custom images in ${IMAGES_DIR}/:"
ls -lh "${IMAGES_DIR}/${MESH_OUTPUT_NAME}" "${IMAGES_DIR}/${ROUTER_OUTPUT_NAME}" "${IMAGES_DIR}/${PIHOLE_OUTPUT_NAME}" 2>/dev/null || true

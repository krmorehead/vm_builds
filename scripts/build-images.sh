#!/usr/bin/env bash
set -euo pipefail

# Builds custom images for the vm_builds project.
# Produces thirteen outputs:
#   1. Mesh LXC rootfs         — minimal OpenWrt, no firewall, WiFi packages      (local build)
#   2. Router VM combined      — full OpenWrt with mesh/security/DNS packages      (local build)
#   3. Pi-hole LXC template    — Debian 12 with Pi-hole pre-installed              (remote build on Proxmox)
#   4. rsyslog LXC template    — Debian 12 with rsyslog TCP receiver pre-configured (remote build on Proxmox)
#   5. Jellyfin LXC template   — Debian 12 with Jellyfin + VA-API drivers baked in (remote build on Proxmox)
#   6. Netdata LXC template    — Debian 12 with Netdata monitoring agent pre-installed (remote build on Proxmox)
#   7. WireGuard LXC template  — Debian 12 with wireguard-tools + iptables baked in (remote build on Proxmox)
#   8. Home Assistant template  — Debian 12 with Docker CE and HA container pre-pulled (remote build on Proxmox)
#   9. Kodi LXC template       — Debian 12 with kodi-standalone + GBM/DRM + Mesa + libcec (remote build on Proxmox)
#  10. Moonlight LXC template  — Debian 12 with moonlight-embedded + VA-API drivers   (remote build on Proxmox)
#  11. Gaming LXC template     — Fedora with Sunshine + GZDoom + Mesa VA-API + PipeWire (remote build on Proxmox)
#  12. Sunshine VM image        — Windows 11 with Sunshine + GZDoom + virtio drivers    (remote build on Proxmox)
#  13. Desktop VM image         — Debian 12 with KDE + GNOME + SDDM + apps baked in    (remote build on Proxmox)
#
# Usage: ./build-images.sh [--clean] [--host <proxmox-ip>] [--only <target>]
#   --clean          Remove cached Image Builder before downloading fresh copy
#   --host <ip>      Proxmox host for remote image builds. Required for remote-built templates.
#   --only <target>  Build only the specified target (mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, moonlight, gaming, sunshine, desktop).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$(cd "${SCRIPT_DIR}/../images" && pwd)"
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

# rsyslog LXC template (built remotely on Proxmox via pct create/exec/vzdump)
RSYSLOG_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
RSYSLOG_OUTPUT_NAME="rsyslog-debian-12-amd64.tar.zst"
RSYSLOG_BUILD_VMID=997

# Gaming LXC template (built remotely on Proxmox — Fedora base with Sunshine + GZDoom)
GAMING_FEDORA_VERSION="41"
GAMING_BASE_ROOTFS="fedora-${GAMING_FEDORA_VERSION}-default-amd64.tar.xz"
GAMING_LXC_IMAGE_URL="https://images.linuxcontainers.org/images/fedora/${GAMING_FEDORA_VERSION}/amd64/default"
GAMING_OUTPUT_NAME="gaming-fedora-amd64.tar.zst"
GAMING_BUILD_VMID=990

# Desktop VM image (built remotely on Proxmox: cloud-init boot → apt install desktops → export disk)
DESKTOP_BASE_IMAGE="debian-12-generic-amd64.qcow2"
DESKTOP_OUTPUT_NAME="desktop-debian-12-amd64.qcow2"
DESKTOP_BUILD_VMID=991

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

cleanup_rsyslog_build() {
    local vmid="${RSYSLOG_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up rsyslog build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_rsyslog_lxc() {
    log "Building rsyslog LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${RSYSLOG_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${RSYSLOG_OUTPUT_NAME}"
    local vmid="${RSYSLOG_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "rsyslog template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "rsyslog build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${RSYSLOG_BASE_TEMPLATE}"
    fi

    trap cleanup_rsyslog_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${RSYSLOG_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${RSYSLOG_BASE_TEMPLATE} \
        --hostname rsyslog-build \
        --memory 256 \
        --cores 1 \
        --rootfs local-lvm:1 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing rsyslog and configuring TCP log reception..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        apt-get update -qq
        apt-get install -y --no-install-recommends rsyslog

        mkdir -p /var/spool/rsyslog

        cat > /etc/rsyslog.d/10-receive.conf << \"RSYSLOG_EOF\"
# TCP syslog receiver — enable imtcp on port 514.
# Actions for received messages are in 50-remote-route.conf (processed
# later so optional forwarding configs at 20-* can act first).
module(load=\"imtcp\")
input(type=\"imtcp\" port=\"514\")

template(name=\"RemoteLogFile\" type=\"string\"
    string=\"/var/log/remote/%HOSTNAME%/%PROGRAMNAME%.log\")
RSYSLOG_EOF

        cat > /etc/rsyslog.d/50-remote-route.conf << \"ROUTE_EOF\"
# Route TCP-received messages to per-hostname files, then stop so they
# do not duplicate into the local syslog.  Numbered 50 so that any
# forwarding config (20-forward.conf) is evaluated first.
if \$inputname == \"imtcp\" then {
    action(type=\"omfile\" dynaFile=\"RemoteLogFile\")
    stop
}
ROUTE_EOF

        mkdir -p /var/log/remote

        cat > /etc/logrotate.d/rsyslog-remote << \"ROTATE_EOF\"
/var/log/remote/*/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 root adm
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
}
ROTATE_EOF

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying rsyslog starts correctly inside build container..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        systemctl restart rsyslog
        sleep 1
        systemctl is-active rsyslog
    '"
    remote_cmd "pct exec ${vmid} -- /usr/sbin/rsyslogd -N1"
    log "rsyslog smoke test passed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "rsyslog LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Jellyfin LXC template (built remotely on Proxmox via pct create/exec/vzdump)
JELLYFIN_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
JELLYFIN_OUTPUT_NAME="jellyfin-debian-12-amd64.tar.zst"
JELLYFIN_BUILD_VMID=995

cleanup_jellyfin_build() {
    local vmid="${JELLYFIN_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Jellyfin build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_jellyfin_lxc() {
    log "Building Jellyfin LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${JELLYFIN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${JELLYFIN_OUTPUT_NAME}"
    local vmid="${JELLYFIN_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Jellyfin template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Jellyfin build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${JELLYFIN_BASE_TEMPLATE}"
    fi

    trap cleanup_jellyfin_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${JELLYFIN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${JELLYFIN_BASE_TEMPLATE} \
        --hostname jellyfin-build \
        --memory 512 \
        --cores 1 \
        --rootfs local-lvm:2 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if [[ $retries -gt 30 ]]; then
            remote_cmd "pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container failed to start after 60 seconds"
        fi
        log "  waiting... ($retries/30)"
        sleep 2
    done
    log "Container has network access."

    # Force reliable DNS — DHCP may inject an ISP nameserver that doesn't resolve
    remote_cmd "pct exec ${vmid} -- bash -c 'echo nameserver 8.8.8.8 > /etc/resolv.conf'"

    log "Installing Jellyfin and VA-API drivers..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive

        # Update package lists
        apt-get update -qq

        # Install Jellyfin from official repositories
        # Add Jellyfin official GPG key and repository
        apt-get install -y --no-install-recommends gnupg2 wget curl

        # Download and add Jellyfin GPG key
        wget -O - https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /usr/share/keyrings/jellyfin.gpg

        # Add Jellyfin repository
        echo \"deb [signed-by=/usr/share/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main\" | tee /etc/apt/sources.list.d/jellyfin.list

        # Update package lists to include Jellyfin
        apt-get update -qq

        # Install Jellyfin server and web components
        apt-get install -y --no-install-recommends \
            jellyfin \
            jellyfin-web \
            jellyfin-ffmpeg7

        # Install VA-API drivers for hardware transcoding (Intel + AMD)
        # Intel iGPU support
        apt-get install -y --no-install-recommends \
            intel-media-va-driver \
            vainfo

        # AMD GPU support (mesa drivers for broader compatibility)
        apt-get install -y --no-install-recommends \
            mesa-va-drivers \
            mesa-vdpau-drivers

        # Pre-configure Jellyfin for port 8096
        mkdir -p /etc/jellyfin
        cat > /etc/jellyfin/jellyfin.conf << \"JELLYFIN_EOF\"
[Networking]
_port = 8096
_base_url = /

[MediaEncoder]
_vaapi_device = /dev/dri/renderD128
_vaapi_driver = auto
JELLYFIN_EOF

        # Enable hardware acceleration by default
        systemctl enable jellyfin || true

        # Clean up package caches
        apt-get clean
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying Jellyfin installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        which jellyfin
        vainfo
        ls -la /usr/lib/jellyfin/
        ls -la /usr/share/jellyfin/web/
    '"
    log "Jellyfin smoke test passed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

    local vzdump_file
    vzdump_file=$(remote_cmd "ls -t /tmp/vzdump-lxc-${vmid}-*.tar.zst 2>/dev/null | head -1")
    if [[ -z "$vzdump_file" ]]; then
        remote_cmd "pct destroy ${vmid} --purge 2>/dev/null; true"
        die "vzdump archive not found on Proxmox host"
    fi
    log "vzdump archive: ${vzdump_file}"

    log "Downloading template from Proxmox host..."
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:${vzdump_file}" "$output"

    log "Cleaning up build container and vzdump archive..."
    remote_cmd "pct destroy ${vmid} --purge 2>/dev/null; rm -f '${vzdump_file}'; true"

    trap - EXIT

    log "Jellyfin LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Netdata LXC template (built remotely on Proxmox via pct create/exec/vzdump)
NETDATA_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
NETDATA_OUTPUT_NAME="netdata-debian-12-amd64.tar.zst"
NETDATA_BUILD_VMID=996

cleanup_netdata_build() {
    local vmid="${NETDATA_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Netdata build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_netdata_lxc() {
    log "Building Netdata LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${NETDATA_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${NETDATA_OUTPUT_NAME}"
    local vmid="${NETDATA_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Netdata template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Netdata build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${NETDATA_BASE_TEMPLATE}"
    fi

    trap cleanup_netdata_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${NETDATA_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${NETDATA_BASE_TEMPLATE} \
        --hostname netdata-build \
        --memory 512 \
        --cores 1 \
        --rootfs local-lvm:2 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing Netdata via official kickstart script..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y --no-install-recommends curl ca-certificates

        curl -sSL https://get.netdata.cloud/kickstart.sh -o /tmp/netdata-kickstart.sh
        bash /tmp/netdata-kickstart.sh --stable-channel --dont-wait --no-updates --disable-telemetry
        rm -f /tmp/netdata-kickstart.sh
    '"

    log "Pre-configuring netdata.conf for LXC container with host metrics..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        CONF_DIR=\"\"
        if [ -f /etc/netdata/netdata.conf ]; then
            CONF_DIR=/etc/netdata
        elif [ -f /opt/netdata/etc/netdata/netdata.conf ]; then
            CONF_DIR=/opt/netdata/etc/netdata
        fi

        if [ -n \"\$CONF_DIR\" ]; then
            cat > \"\$CONF_DIR/netdata.conf\" << \"NETDATA_EOF\"
[global]
    hostname = netdata-child
    update every = 2

[db]
    mode = dbengine
    dbengine tier 0 retention = 3600

[directories]
    proc = /host/proc
    sys = /host/sys

[web]
    bind to = *
    default port = 19999

[plugins]
    proc = yes
    cgroups = yes
    apps = no
    charts.d = no
    node.d = no
    python.d = no
NETDATA_EOF
        fi

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Deploying systemd override for LXC compatibility..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        mkdir -p /etc/systemd/system/netdata.service.d
        cat > /etc/systemd/system/netdata.service.d/lxc-override.conf << \"OVERRIDE_EOF\"
[Service]
LogNamespace=
ProtectSystem=false
ProtectHome=false
ProtectControlGroups=false
BindReadOnlyPaths=
OVERRIDE_EOF
    '"

    log "Verifying Netdata starts correctly inside build container..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        systemctl restart netdata 2>/dev/null || service netdata restart 2>/dev/null || true
        sleep 3
        curl -s -o /dev/null -w \"%{http_code}\" http://127.0.0.1:19999 || echo \"no-dashboard\"
    '"
    log "Netdata smoke test completed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "Netdata LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# WireGuard LXC template (built remotely on Proxmox via pct create/exec/vzdump)
WIREGUARD_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
WIREGUARD_OUTPUT_NAME="wireguard-debian-12-amd64.tar.zst"
WIREGUARD_BUILD_VMID=995

# Kodi LXC template (built remotely on Proxmox via pct create/exec/vzdump)
KODI_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
KODI_OUTPUT_NAME="kodi-debian-12-amd64.tar.zst"
KODI_BUILD_VMID=993

# Moonlight LXC template (built remotely on Proxmox via pct create/exec/vzdump)
MOONLIGHT_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
MOONLIGHT_OUTPUT_NAME="moonlight-debian-12-amd64.tar.zst"
MOONLIGHT_BUILD_VMID=991

# Home Assistant LXC template (built remotely on Proxmox via pct create/exec/vzdump)
HOMEASSISTANT_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
HOMEASSISTANT_OUTPUT_NAME="homeassistant-debian-12-amd64.tar.zst"
HOMEASSISTANT_BUILD_VMID=994

cleanup_kodi_build() {
    local vmid="${KODI_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Kodi build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_kodi_lxc() {
    log "Building Kodi LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${KODI_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${KODI_OUTPUT_NAME}"
    local vmid="${KODI_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Kodi template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Kodi build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${KODI_BASE_TEMPLATE}"
    fi

    trap cleanup_kodi_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${KODI_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${KODI_BASE_TEMPLATE} \
        --hostname kodi-build \
        --memory 1024 \
        --cores 2 \
        --rootfs local-lvm:4 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing Kodi GBM/DRM stack, Mesa drivers, and libcec..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        apt-get install -y --no-install-recommends \
            kodi \
            kodi-peripheral-joystick \
            libcec6 \
            cec-utils \
            alsa-utils

        # VA-API drivers for both Intel and AMD iGPU
        apt-get install -y --no-install-recommends \
            intel-media-va-driver \
            mesa-va-drivers \
            vainfo

        # Create kodi system user for headless operation
        useradd -r -m -G audio,video,input,render -s /bin/bash kodi 2>/dev/null || true

        # Pre-configure kodi-standalone systemd service
        cat > /etc/systemd/system/kodi-standalone.service << \"SERVICE_EOF\"
[Unit]
Description=Kodi Standalone (GBM/DRM)
After=systemd-user-sessions.service network-online.target sound.target
Wants=network-online.target

[Service]
User=kodi
Group=kodi
PAMName=login
Type=simple
ExecStart=/usr/bin/kodi-standalone
Restart=on-failure
RestartSec=5
StandardInput=tty
TTYPath=/dev/tty7
TTYReset=yes
TTYVHangup=yes

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        systemctl daemon-reload

        # Pre-configure advancedsettings.xml template for buffer/cache tuning
        mkdir -p /home/kodi/.kodi/userdata
        cat > /home/kodi/.kodi/userdata/advancedsettings.xml << \"SETTINGS_EOF\"
<advancedsettings version=\"1.0\">
  <cache>
    <memorysize>52428800</memorysize>
    <readfactor>4</readfactor>
  </cache>
  <network>
    <curlclienttimeout>30</curlclienttimeout>
    <curllowspeedtime>30</curllowspeedtime>
  </network>
</advancedsettings>
SETTINGS_EOF
        chown -R kodi:kodi /home/kodi/.kodi

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying Kodi installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dpkg -l kodi | grep -c ^ii || { echo FAIL: kodi not installed; exit 1; }
        test -f /etc/systemd/system/kodi-standalone.service || { echo FAIL: service missing; exit 1; }
        test -f /home/kodi/.kodi/userdata/advancedsettings.xml || { echo FAIL: settings missing; exit 1; }
        id kodi || { echo FAIL: kodi user missing; exit 1; }
        echo ALL CHECKS PASSED
    '"
    log "Kodi smoke test passed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "Kodi LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_moonlight_build() {
    local vmid="${MOONLIGHT_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Moonlight build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_moonlight_lxc() {
    log "Building Moonlight LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${MOONLIGHT_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${MOONLIGHT_OUTPUT_NAME}"
    local vmid="${MOONLIGHT_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Moonlight template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Moonlight build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${MOONLIGHT_BASE_TEMPLATE}"
    fi

    trap cleanup_moonlight_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${MOONLIGHT_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${MOONLIGHT_BASE_TEMPLATE} \
        --hostname moonlight-build \
        --memory 1024 \
        --cores 2 \
        --rootfs local-lvm:4 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing runtime and build dependencies..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        # Runtime deps (marked manual — survives autoremove after build)
        # SDL2 for video output (uses KMSDRM backend in LXC without X11)
        # FFmpeg for video decode (libavcodec59 + libavutil57 MUST be here
        # so apt marks them manual; otherwise autoremove deletes them)
        # VA-API drivers for Intel + AMD hardware decode
        apt-get install -y --no-install-recommends \
            libopus0 libexpat1 libasound2 libudev1 libavahi-client3 \
            libcurl4 libevdev2 libpulse0 libsdl2-2.0-0 \
            libavcodec59 libavutil57 \
            intel-media-va-driver mesa-va-drivers vainfo \
            ca-certificates

        # Build deps (purged after compilation)
        apt-get install -y --no-install-recommends \
            git cmake gcc g++ make pkg-config \
            libssl-dev libopus-dev libasound2-dev libudev-dev \
            libavahi-client-dev libcurl4-openssl-dev libevdev-dev \
            libexpat1-dev libpulse-dev uuid-dev \
            libsdl2-dev libavcodec-dev libavutil-dev
    '"

    log "Compiling moonlight-embedded v2.7.1 from source..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        cd /tmp
        git clone --branch v2.7.1 --depth 1 https://github.com/moonlight-stream/moonlight-embedded.git
        cd moonlight-embedded
        git submodule update --init --recursive
        mkdir build && cd build
        cmake -DENABLE_X11=OFF ..
        make -j\$(nproc)
        make install
        ldconfig
    '"

    log "Cleaning up build dependencies and source..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get purge -y \
            git cmake gcc g++ make pkg-config \
            libssl-dev libopus-dev libasound2-dev libudev-dev \
            libavahi-client-dev libcurl4-openssl-dev libevdev-dev \
            libexpat1-dev libpulse-dev uuid-dev \
            libsdl2-dev libavcodec-dev libavutil-dev
        apt-get autoremove -y
        apt-get clean 2>/dev/null || true
        rm -rf /tmp/moonlight-embedded /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying Moonlight installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        test -x /usr/local/bin/moonlight || { echo FAIL: moonlight-embedded not installed; exit 1; }
        /usr/local/bin/moonlight --version 2>&1 || true
        vainfo --display drm 2>&1 | head -5 || true
        echo ALL CHECKS PASSED
    '"
    log "Moonlight smoke test passed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "Moonlight LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_wireguard_build() {
    local vmid="${WIREGUARD_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up WireGuard build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

cleanup_homeassistant_build() {
    local vmid="${HOMEASSISTANT_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Home Assistant build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_wireguard_lxc() {
    log "Building WireGuard LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${WIREGUARD_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${WIREGUARD_OUTPUT_NAME}"
    local vmid="${WIREGUARD_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "WireGuard template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "WireGuard build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${WIREGUARD_BASE_TEMPLATE}"
    fi

    trap cleanup_wireguard_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${WIREGUARD_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${WIREGUARD_BASE_TEMPLATE} \
        --hostname wireguard-build \
        --memory 256 \
        --cores 1 \
        --rootfs local-lvm:1 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --features nesting=1 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing WireGuard tools and iptables..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y --no-install-recommends wireguard-tools iptables iptables-persistent

        mkdir -p /etc/wireguard
        chmod 0700 /etc/wireguard

        cat > /etc/sysctl.d/99-wireguard.conf << \"SYSCTL_EOF\"
net.ipv4.ip_forward=1
SYSCTL_EOF

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying WireGuard tools work inside build container..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        wg genkey | wg pubkey >/dev/null
        test -d /etc/wireguard && echo wireguard-dir-ok
        test -f /etc/sysctl.d/99-wireguard.conf && echo sysctl-ok
        dpkg -l iptables-persistent | grep -q ^ii && echo iptables-persistent-ok
    '"
    log "WireGuard smoke test passed."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "WireGuard LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

build_homeassistant_lxc() {
    log "Building Home Assistant LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${HOMEASSISTANT_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/${HOMEASSISTANT_OUTPUT_NAME}"
    local vmid="${HOMEASSISTANT_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Home Assistant template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Home Assistant build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${HOMEASSISTANT_BASE_TEMPLATE}"
    fi

    trap cleanup_homeassistant_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${HOMEASSISTANT_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${HOMEASSISTANT_BASE_TEMPLATE} \
        --hostname homeassistant-build \
        --memory 1024 \
        --cores 2 \
        --rootfs local-lvm:8 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --features nesting=1 \
        --unprivileged 1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 20 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never became ready after 40s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'getent hosts deb.debian.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 15 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 30s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing Docker CE and docker-compose plugin..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive

        # Add Docker official repository
        apt-get update -qq
        apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release

        # Add Docker GPG key
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg

        # Add Docker repository
        echo \\
          \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \\
          \$(lsb_release -cs) stable\" | tee /etc/apt/sources.list.d/docker.list > /dev/null

        apt-get update -qq

        # Install Docker CE and docker-compose plugin
        apt-get install -y --no-install-recommends \\
            docker-ce \\
            docker-ce-cli \\
            containerd.io \\
            docker-buildx-plugin \\
            docker-compose-plugin

        # Configure cgroup delegation for Docker in unprivileged LXC
        mkdir -p /etc/docker
        cat > /etc/docker/daemon.json << \"DOCKER_EOF\"
{
  \"log-driver\": \"json-file\",
  \"log-opts\": {
    \"max-size\": \"10m\",
    \"max-file\": \"3\"
  },
  \"exec-opts\": [\"native.cgroupdriver=cgroupfs\"]
}
DOCKER_EOF

        # Pre-pull Home Assistant container image (documented exception to bake principle)
        systemctl start docker
        docker pull homeassistant/home-assistant:stable

        # Clean up apt cache
        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

        # Stop Docker daemon for template export
        systemctl stop docker
    '"

    log "Verifying Docker installation inside build container..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        test -f /usr/bin/docker && echo docker-installed
        docker --version
        docker compose version
    '"
    log "Docker installation verified."

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 2

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

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

    log "Home Assistant LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_gaming_build() {
    local vmid="${GAMING_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up gaming build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
}

build_gaming_lxc() {
    log "Building Gaming LXC template (remote on Proxmox)..."
    local base_rootfs="${IMAGES_DIR}/${GAMING_BASE_ROOTFS}"
    local output="${IMAGES_DIR}/${GAMING_OUTPUT_NAME}"
    local vmid="${GAMING_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Gaming template already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Gaming build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.220 --only gaming"
    fi

    # Download Fedora rootfs from linuxcontainers.org if not cached
    if [[ ! -f "$base_rootfs" ]]; then
        log "Downloading Fedora ${GAMING_FEDORA_VERSION} rootfs from linuxcontainers.org..."
        mkdir -p "$IMAGES_DIR"
        local latest_dir
        latest_dir=$(curl -sL "${GAMING_LXC_IMAGE_URL}/" \
            | grep -oP 'href="\K[0-9_%A-Fa-f]+(?=/")' \
            | sort -r | head -1)
        if [[ -z "$latest_dir" ]]; then
            die "Could not find Fedora ${GAMING_FEDORA_VERSION} rootfs on linuxcontainers.org.
  Check ${GAMING_LXC_IMAGE_URL}/ manually."
        fi
        local rootfs_url="${GAMING_LXC_IMAGE_URL}/${latest_dir}/rootfs.tar.xz"
        log "Rootfs URL: ${rootfs_url}"
        wget -q --show-progress -O "$base_rootfs" "$rootfs_url"
        log "Fedora rootfs cached: $(du -h "$base_rootfs" | cut -f1)"
    else
        log "Using cached Fedora rootfs: ${base_rootfs}"
    fi

    trap cleanup_gaming_build EXIT

    # Ensure no stale build container exists
    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    # Upload Fedora rootfs to Proxmox template cache
    local remote_template="/var/lib/vz/template/cache/${GAMING_BASE_ROOTFS}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading Fedora rootfs to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_rootfs" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${GAMING_BASE_ROOTFS} \
        --hostname gaming-build \
        --ostype unmanaged \
        --memory 2048 \
        --cores 2 \
        --rootfs local-lvm:8 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 0 \
        --features nesting=1 \
        --start false"

    log "Starting build container..."
    remote_cmd "pct start ${vmid}"

    log "Waiting for container to start..."
    local retries=0
    while ! remote_cmd "pct exec ${vmid} -- ls / >/dev/null 2>&1"; do
        retries=$((retries + 1))
        if (( retries > 30 )); then
            die "Build container never became ready after 60s"
        fi
        sleep 2
    done
    log "Container is ready."

    log "Waiting for network inside build container..."
    local net_retries=0
    while ! remote_cmd "pct exec ${vmid} -- bash -c 'curl -sI https://fedoraproject.org >/dev/null 2>&1'"; do
        net_retries=$((net_retries + 1))
        if (( net_retries > 30 )); then
            die "Build container never got network after 60s"
        fi
        sleep 2
    done
    log "Network ready."

    # Force reliable DNS
    remote_cmd "pct exec ${vmid} -- bash -c 'echo nameserver 8.8.8.8 > /etc/resolv.conf'"

    log "Installing RPM Fusion for freeworld codecs and gaming packages..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf install -y \
            https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-${GAMING_FEDORA_VERSION}.noarch.rpm \
            https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${GAMING_FEDORA_VERSION}.noarch.rpm \
            2>&1 | tail -3
    '"

    log "Installing base system and display packages..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf install -y --skip-unavailable \
            mesa-dri-drivers \
            mesa-vulkan-drivers \
            mesa-vdpau-drivers \
            libva-utils \
            vulkan-tools \
            xorg-x11-server-Xorg \
            xorg-x11-drv-modesetting \
            xorg-x11-xinit \
            xrandr \
            pipewire \
            pipewire-pulseaudio \
            wireplumber \
            gamemode \
            bash \
            systemd \
            dbus \
            procps-ng \
            iproute \
            net-tools \
            curl \
            wget \
            ca-certificates \
            2>&1 | tail -10
    '"

    log "Swapping mesa-va-drivers for freeworld variant (H.264/HEVC encode)..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf swap -y mesa-va-drivers mesa-va-drivers-freeworld 2>&1 | tail -5
    '"

    log "Installing gaming packages (dsda-doom + freedoom)..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf install -y dsda-doom freedoom 2>&1 | tail -5
    '"

    log "Installing Sunshine game streaming server..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf copr enable -y lizardbyte/stable 2>&1 | tail -3
        dnf install -y sunshine 2>&1 | tail -3
    '"

    log "Creating Xorg virtual display configuration..."
    remote_cmd "pct exec ${vmid} -- bash -c 'mkdir -p /etc/X11'"
    remote_cmd "pct exec ${vmid} -- bash -c 'cat > /etc/X11/xorg-virtual.conf << \"XEOF\"
Section \"Device\"
    Identifier  \"GPU\"
    Driver      \"modesetting\"
EndSection

Section \"Screen\"
    Identifier  \"Screen0\"
    Device      \"GPU\"
    DefaultDepth 24
    SubSection \"Display\"
        Depth 24
        Modes \"1920x1080\" \"1280x720\"
    EndSubSection
EndSection

Section \"ServerLayout\"
    Identifier  \"Layout0\"
    Screen 0    \"Screen0\"
EndSection
XEOF'"

    log "Creating systemd service for headless Xorg..."
    remote_cmd "pct exec ${vmid} -- bash -c 'cat > /etc/systemd/system/xorg-virtual.service << \"SEOF\"
[Unit]
Description=Headless Xorg display server
After=systemd-logind.service

[Service]
Type=simple
ExecStart=/usr/bin/Xorg :0 -config /etc/X11/xorg-virtual.conf -noreset
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SEOF'"

    log "Creating systemd service for Sunshine..."
    remote_cmd "pct exec ${vmid} -- bash -c 'cat > /etc/systemd/system/sunshine.service << \"SEOF\"
[Unit]
Description=Sunshine game streaming server
After=xorg-virtual.service
Requires=xorg-virtual.service

[Service]
Type=simple
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/0
ExecStart=/usr/bin/sunshine
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SEOF'"

    log "Creating PipeWire user service override for root..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        mkdir -p /root/.config/systemd/user
        mkdir -p /etc/systemd/system/pipewire.service.d
        cat > /etc/systemd/system/pipewire.service.d/override.conf << \"PEOF\"
[Unit]
Description=PipeWire Multimedia Service (system)

[Service]
Type=simple
ExecStart=
ExecStart=/usr/bin/pipewire
Environment=XDG_RUNTIME_DIR=/run/user/0

[Install]
WantedBy=multi-user.target
PEOF
    '"

    log "Enabling services..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        systemctl enable xorg-virtual.service 2>/dev/null || true
        systemctl enable sunshine.service 2>/dev/null || true
    '"

    log "Cleaning up package caches..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf clean all
        rm -rf /var/cache/dnf /tmp/* /var/tmp/*
    '"

    log "Stopping build container..."
    remote_cmd "pct stop ${vmid}"
    sleep 3

    log "Exporting container as template via vzdump..."
    remote_cmd "vzdump ${vmid} --dumpdir /tmp --compress zstd --mode stop"

    local vzdump_file
    vzdump_file=$(remote_cmd "ls -t /tmp/vzdump-lxc-${vmid}-*.tar.zst 2>/dev/null | head -1")
    if [[ -z "$vzdump_file" ]]; then
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

    log "Gaming LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# ── Desktop VM image ─────────────────────────────────────────────────
# Debian 12 cloud image with KDE Plasma, GNOME, SDDM, and shared apps
# pre-installed. GPU drivers are NOT baked (host-dependent, installed at
# configure time). The generic cloud image must already be downloaded to
# images/debian-12-generic-amd64.qcow2.

cleanup_desktop_build() {
    local vmid="${DESKTOP_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Desktop build VM ${vmid}..."
        remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 3; qm destroy ${vmid} --purge 2>/dev/null; true"
        remote_cmd "rm -f /var/tmp/${DESKTOP_OUTPUT_NAME}; true"
    fi
}

build_desktop_vm() {
    log "Building Desktop VM image (remote on Proxmox)..."
    local base_image="${IMAGES_DIR}/${DESKTOP_BASE_IMAGE}"
    local output="${IMAGES_DIR}/${DESKTOP_OUTPUT_NAME}"
    local vmid="${DESKTOP_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Desktop VM image already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Desktop build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201 --only desktop"
    fi

    if [[ ! -f "$base_image" ]]; then
        die "Debian cloud image not found: ${base_image}.
  Download from: https://cloud.debian.org/images/cloud/bookworm/latest/
  Save to: images/debian-12-generic-amd64.qcow2"
    fi

    trap cleanup_desktop_build EXIT

    remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 2; qm destroy ${vmid} --purge 2>/dev/null; true"

    # Upload cloud image to Proxmox
    log "Uploading Debian cloud image to Proxmox..."
    if ! remote_cmd "test -f /var/tmp/${DESKTOP_BASE_IMAGE}"; then
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_image" "root@${PROXMOX_HOST}:/var/tmp/${DESKTOP_BASE_IMAGE}"
    else
        log "  Cloud image already on host, skipping upload."
    fi

    # Detect management bridge
    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    # Create temporary build VM with cloud-init
    log "Creating build VM (VMID ${vmid})..."
    remote_cmd "qm create ${vmid} \
        --name desktop-build \
        --machine q35 \
        --bios ovmf \
        --efidisk0 local-lvm:1,format=raw,efitype=4m,pre-enrolled-keys=0 \
        --cores 4 \
        --memory 4096 \
        --cpu host \
        --scsihw virtio-scsi-pci \
        --serial0 socket \
        --vga std \
        --agent enabled=1 \
        --net0 virtio,bridge=${mgmt_bridge} \
        --ide2 local-lvm:cloudinit \
        --ostype l26"

    # Import and attach cloud image disk
    log "Importing cloud image disk..."
    remote_cmd "qm importdisk ${vmid} /var/tmp/${DESKTOP_BASE_IMAGE} local-lvm"
    remote_cmd "qm set ${vmid} \
        --scsi0 local-lvm:vm-${vmid}-disk-1,discard=on,ssd=1 \
        --boot order=scsi0"

    # Configure cloud-init for SSH access
    remote_cmd "qm set ${vmid} \
        --ciuser root \
        --sshkeys /root/.ssh/authorized_keys \
        --ipconfig0 ip=dhcp"

    # Resize disk for desktop packages
    remote_cmd "qm resize ${vmid} scsi0 32G"

    # Start VM
    log "Starting build VM..."
    remote_cmd "qm start ${vmid}"

    # Get VM MAC address for IP discovery
    local vm_mac
    vm_mac=$(remote_cmd "qm config ${vmid} | grep '^net0' | sed -n 's/.*\\(..:..:..:..:..:..\).*/\\1/p'" | tr '[:upper:]' '[:lower:]')
    log "VM MAC: ${vm_mac}"

    # Wait for DHCP lease and discover IP via ARP neighbor table
    log "Waiting for VM to acquire DHCP lease (up to 5 minutes)..."
    local vm_ip=""
    local ip_retries=0
    while [[ -z "$vm_ip" ]]; do
        ip_retries=$((ip_retries + 1))
        if (( ip_retries > 30 )); then
            die "Could not discover VM IP after 5 minutes. Check VM console."
        fi
        sleep 10
        vm_ip=$(remote_cmd "ip -4 neigh show dev ${mgmt_bridge} \
            | awk '/${vm_mac}/{print \$1}' | head -1" 2>/dev/null || true)
        if [[ -z "$vm_ip" ]]; then
            remote_cmd "subnet=\$(ip -4 addr show ${mgmt_bridge} | awk '/inet /{print \$2}' | head -1 | sed 's/\\.[0-9]*\\/.*//')
                for i in \$(seq 1 254); do ping -c1 -W1 \${subnet}.\$i >/dev/null 2>&1 & done; wait" 2>/dev/null || true
        fi
    done
    log "VM IP: ${vm_ip}"

    # Wait for SSH (cloud-init installs SSH keys, no guest agent needed)
    log "Waiting for SSH..."
    local ssh_retries=0
    while ! remote_cmd "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@${vm_ip} true 2>/dev/null"; do
        ssh_retries=$((ssh_retries + 1))
        if (( ssh_retries > 30 )); then
            die "SSH not available after 5 minutes."
        fi
        sleep 10
    done
    log "SSH connected."

    # Install desktop packages inside the VM (via SSH from the Proxmox host)
    log "Installing desktop environments and applications (this takes 10-15 minutes)..."
    remote_cmd "ssh -o StrictHostKeyChecking=no root@${vm_ip} bash -s" << 'INSTALL_EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "==> Waiting for cloud-init..."
cloud-init status --wait >/dev/null 2>&1 || true

echo "==> Updating package lists..."
apt-get update -qq

echo "==> Upgrading base system..."
apt-get dist-upgrade -y -qq

echo "==> Installing KDE Plasma, GNOME, SDDM, and shared applications..."
apt-get install -y --no-install-recommends \
    task-kde-desktop \
    kde-plasma-desktop \
    plasma-nm \
    konsole \
    dolphin \
    kate \
    kde-spectacle \
    ark \
    kde-config-screenlocker \
    task-gnome-desktop \
    gnome-session \
    gnome-terminal \
    nautilus \
    gnome-text-editor \
    gnome-screenshot \
    gnome-tweaks \
    gnome-shell-extension-manager \
    gnome-shell-extension-dashtodock \
    file-roller \
    sddm \
    firefox-esr \
    vlc \
    libreoffice \
    flameshot \
    xdg-user-dirs \
    fonts-noto \
    fonts-noto-color-emoji \
    pipewire \
    pipewire-audio \
    wireplumber \
    qemu-guest-agent

echo "==> Cleaning up..."
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

echo "==> Resetting cloud-init for next boot..."
cloud-init clean --logs

echo "==> Desktop image build complete."
INSTALL_EOF

    # Shutdown VM (use SSH shutdown since guest agent may not be started yet)
    log "Shutting down build VM..."
    remote_cmd "ssh -o StrictHostKeyChecking=no root@${vm_ip} 'shutdown -h now' 2>/dev/null || qm stop ${vmid}"
    sleep 15

    local stop_retries=0
    while remote_cmd "qm status ${vmid} 2>/dev/null | grep -q running"; do
        stop_retries=$((stop_retries + 1))
        if (( stop_retries > 24 )); then
            log "Force-stopping VM..."
            remote_cmd "qm stop ${vmid}"
            sleep 10
            break
        fi
        sleep 5
    done

    # Export the disk image
    log "Exporting VM disk image..."
    local disk_vol disk_path
    disk_vol=$(remote_cmd "qm config ${vmid} | grep '^scsi0:' | sed 's/^scsi0: //;s/,.*//' 2>/dev/null || echo ''")
    if [[ -n "$disk_vol" ]]; then
        disk_path=$(remote_cmd "pvesm path '${disk_vol}' 2>/dev/null || echo ''")
    fi

    if [[ -z "${disk_path:-}" ]]; then
        die "Could not determine disk path for VM ${vmid}. Check 'qm config ${vmid}' on the host."
    fi

    log "Disk path: ${disk_path}"
    log "Converting to qcow2 and downloading..."

    remote_cmd "rm -f /var/tmp/${DESKTOP_OUTPUT_NAME}"
    remote_cmd "qemu-img convert -f raw -O qcow2 '${disk_path}' /var/tmp/${DESKTOP_OUTPUT_NAME}"

    mkdir -p "$IMAGES_DIR"
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:/var/tmp/${DESKTOP_OUTPUT_NAME}" "$output"

    # Cleanup
    log "Cleaning up build VM and temporary files..."
    remote_cmd "qm destroy ${vmid} --purge 2>/dev/null; true"
    remote_cmd "rm -f /var/tmp/${DESKTOP_OUTPUT_NAME} /var/tmp/${DESKTOP_BASE_IMAGE}; true"

    trap - EXIT

    log "Desktop VM image: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Sunshine VM image (built remotely on Proxmox: ISO boot → unattended install → export)
SUNSHINE_ISO="Tiny11-2026-03-15.iso"
SUNSHINE_VIRTIO_ISO="virtio-win.iso"
SUNSHINE_OUTPUT_NAME="sunshine-win11-amd64.qcow2"
SUNSHINE_BUILD_VMID=992
SUNSHINE_ANSWER_DIR="${SCRIPT_DIR}/../roles/gaming_vm/files"

cleanup_sunshine_build() {
    local vmid="${SUNSHINE_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Sunshine build VM ${vmid}..."
        remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 3; qm destroy ${vmid} --purge 2>/dev/null; true"
        remote_cmd "rm -f /tmp/sunshine-answer.iso /tmp/${SUNSHINE_ISO} /tmp/${SUNSHINE_VIRTIO_ISO} /var/tmp/${SUNSHINE_OUTPUT_NAME}; true"
    fi
}

build_sunshine_vm() {
    log "Building Sunshine VM image (remote on Proxmox)..."
    local win_iso="${IMAGES_DIR}/${SUNSHINE_ISO}"
    local virtio_iso="${IMAGES_DIR}/isos/${SUNSHINE_VIRTIO_ISO}"
    local output="${IMAGES_DIR}/${SUNSHINE_OUTPUT_NAME}"
    local vmid="${SUNSHINE_BUILD_VMID}"

    if [[ -f "$output" ]]; then
        log "Sunshine VM image already exists at ${output}"
        log "  Delete it and re-run to rebuild."
        return
    fi

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Sunshine build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201 --only sunshine"
    fi

    if [[ ! -f "$win_iso" ]]; then
        die "Windows ISO not found: ${win_iso}.
  Place Tiny11-2026-03-15.iso in images/ directory."
    fi

    if [[ ! -f "$virtio_iso" ]]; then
        die "virtio-win ISO not found: ${virtio_iso}.
  Download from: https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso
  Save to: images/isos/virtio-win.iso"
    fi

    if [[ ! -f "${SUNSHINE_ANSWER_DIR}/autounattend.xml" ]]; then
        die "autounattend.xml not found at ${SUNSHINE_ANSWER_DIR}/autounattend.xml"
    fi

    command -v genisoimage &>/dev/null || command -v mkisofs &>/dev/null || \
        die "genisoimage or mkisofs required for answer ISO. Install: apt install genisoimage"

    trap cleanup_sunshine_build EXIT

    remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 2; qm destroy ${vmid} --purge 2>/dev/null; true"

    # Create answer ISO with autounattend.xml and post-install script
    log "Creating answer ISO..."
    local answer_tmp
    answer_tmp=$(mktemp -d)
    cp "${SUNSHINE_ANSWER_DIR}/autounattend.xml" "${answer_tmp}/"
    cp "${SUNSHINE_ANSWER_DIR}/post-install.ps1" "${answer_tmp}/"
    local answer_iso="${answer_tmp}/sunshine-answer.iso"
    if command -v genisoimage &>/dev/null; then
        genisoimage -quiet -J -r -o "$answer_iso" "$answer_tmp"
    else
        mkisofs -quiet -J -r -o "$answer_iso" "$answer_tmp"
    fi

    # Upload ISOs to Proxmox host
    log "Uploading Windows ISO to Proxmox (this may take several minutes)..."
    if ! remote_cmd "test -f /var/lib/vz/template/iso/${SUNSHINE_ISO} || test -f /tmp/${SUNSHINE_ISO}"; then
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$win_iso" "root@${PROXMOX_HOST}:/tmp/${SUNSHINE_ISO}"
    else
        log "  Windows ISO already on host, skipping upload."
    fi

    log "Uploading virtio-win ISO..."
    if ! remote_cmd "test -f /var/lib/vz/template/iso/${SUNSHINE_VIRTIO_ISO} || test -f /tmp/${SUNSHINE_VIRTIO_ISO}"; then
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$virtio_iso" "root@${PROXMOX_HOST}:/tmp/${SUNSHINE_VIRTIO_ISO}"
    else
        log "  virtio-win ISO already on host, skipping upload."
    fi

    log "Uploading answer ISO..."
    # shellcheck disable=SC2086
    scp $SSH_OPTS "$answer_iso" "root@${PROXMOX_HOST}:/tmp/sunshine-answer.iso"
    rm -rf "$answer_tmp"

    # Detect management bridge
    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    # Move ISOs to Proxmox ISO storage (must be done BEFORE qm create references them)
    log "Moving ISOs to Proxmox storage..."
    remote_cmd "mkdir -p /var/lib/vz/template/iso"
    remote_cmd "test -f /var/lib/vz/template/iso/${SUNSHINE_ISO} || cp /tmp/${SUNSHINE_ISO} /var/lib/vz/template/iso/${SUNSHINE_ISO}"
    remote_cmd "test -f /var/lib/vz/template/iso/${SUNSHINE_VIRTIO_ISO} || cp /tmp/${SUNSHINE_VIRTIO_ISO} /var/lib/vz/template/iso/${SUNSHINE_VIRTIO_ISO}"
    remote_cmd "cp /tmp/sunshine-answer.iso /var/lib/vz/template/iso/sunshine-answer.iso 2>/dev/null || true"

    # Create temporary build VM
    # Answer ISO on sata0 — Windows PE scans all CD/DVD drives for autounattend.xml
    log "Creating build VM (VMID ${vmid})..."
    remote_cmd "qm create ${vmid} \
        --name sunshine-build \
        --machine q35 \
        --bios ovmf \
        --efidisk0 local-lvm:0,format=raw,efitype=4m,pre-enrolled-keys=0 \
        --cores 4 \
        --memory 4096 \
        --cpu host \
        --scsihw virtio-scsi-pci \
        --scsi0 local-lvm:64 \
        --ide0 local:iso/${SUNSHINE_ISO},media=cdrom \
        --ide2 local:iso/${SUNSHINE_VIRTIO_ISO},media=cdrom \
        --sata0 local:iso/sunshine-answer.iso,media=cdrom \
        --net0 virtio,bridge=${mgmt_bridge} \
        --agent enabled=1 \
        --ostype win11 \
        --boot order='ide0;scsi0'"

    # Start VM and begin Windows installation
    log "Starting build VM..."
    remote_cmd "qm start ${vmid}"

    # OVMF shows "Press any key to boot from CD" — send Enter repeatedly
    log "Sending boot key to OVMF..."
    sleep 3
    for _ in 1 2 3 4 5; do
        remote_cmd "qm sendkey ${vmid} ret 2>/dev/null || true"
        sleep 2
    done

    log ""
    log "======================================================================="
    log "  Windows unattended installation is now running."
    log "  This typically takes 15-30 minutes depending on hardware."
    log ""
    log "  Monitor via Proxmox console: https://${PROXMOX_HOST}:8006"
    log "  Open VM ${vmid} console to watch progress."
    log ""
    log "  The script will poll for QEMU Guest Agent availability."
    log "  Once Windows is installed and the guest agent responds,"
    log "  the disk image will be exported."
    log "======================================================================="
    log ""

    # Wait for QEMU Guest Agent (indicates Windows is installed and running)
    log "Waiting for QEMU Guest Agent (up to 45 minutes)..."
    local ga_retries=0
    while ! remote_cmd "qm guest cmd ${vmid} ping >/dev/null 2>&1"; do
        ga_retries=$((ga_retries + 1))
        if (( ga_retries > 270 )); then
            log "ERROR: Guest Agent not responding after 45 minutes."
            log "Check the VM console for errors. The VM may need manual intervention."
            die "Build VM guest agent timeout. Check Proxmox console for VM ${vmid}."
        fi
        if (( ga_retries % 30 == 0 )); then
            log "  Still waiting for Guest Agent... (${ga_retries}/270, ~$((ga_retries / 6)) min)"
        fi
        sleep 10
    done
    log "Guest Agent responding! Windows installation appears complete."

    # Poll for post-install completion (marker file written by post-install.ps1)
    log "Waiting for post-install scripts to complete (up to 20 minutes)..."
    local post_retries=0
    local post_done=false
    while (( post_retries < 40 )); do
        post_retries=$((post_retries + 1))
        sleep 30
        local post_check
        post_check=$(remote_cmd "qm guest exec ${vmid} --timeout 10 -- cmd /c 'type C:\\post-install-done.txt' 2>/dev/null" || echo "")
        if echo "$post_check" | grep -q "COMPLETE"; then
            log "Post-install script completed successfully."
            post_done=true
            break
        fi
        if (( post_retries % 6 == 0 )); then
            log "  Still waiting for post-install... ($((post_retries / 2))/20 min)"
        fi
    done
    if [[ "$post_done" != "true" ]]; then
        die "Post-install marker not found after 20 minutes. The image is incomplete — check post-install.ps1 output in the VM."
    fi

    # Shutdown VM for export
    log "Shutting down build VM..."
    remote_cmd "qm guest cmd ${vmid} shutdown 2>/dev/null || qm stop ${vmid}"
    sleep 30

    # Wait for VM to fully stop
    local stop_retries=0
    while remote_cmd "qm status ${vmid} 2>/dev/null | grep -q running"; do
        stop_retries=$((stop_retries + 1))
        if (( stop_retries > 30 )); then
            log "Force-stopping VM..."
            remote_cmd "qm stop ${vmid}"
            sleep 10
            break
        fi
        sleep 5
    done

    # Export the disk image
    log "Exporting VM disk image..."
    local disk_path disk_vol
    # Extract storage:volume from qm config (e.g. "local-lvm:vm-992-disk-2")
    disk_vol=$(remote_cmd "qm config ${vmid} | grep '^scsi0:' | sed 's/^scsi0: //;s/,.*//' 2>/dev/null || echo ''")
    if [[ -n "$disk_vol" ]]; then
        disk_path=$(remote_cmd "pvesm path '${disk_vol}' 2>/dev/null || echo ''")
    fi

    if [[ -z "$disk_path" ]]; then
        die "Could not determine disk path for VM ${vmid}. Check 'qm config ${vmid}' on the host."
    fi

    log "Disk path: ${disk_path}"
    log "Converting to qcow2 and downloading..."

    remote_cmd "rm -f /var/tmp/${SUNSHINE_OUTPUT_NAME}"
    remote_cmd "qemu-img convert -f raw -O qcow2 '${disk_path}' /var/tmp/${SUNSHINE_OUTPUT_NAME}"

    mkdir -p "$IMAGES_DIR"
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:/var/tmp/${SUNSHINE_OUTPUT_NAME}" "$output"

    # Cleanup
    log "Cleaning up build VM and temporary files..."
    remote_cmd "qm destroy ${vmid} --purge 2>/dev/null; true"
    remote_cmd "rm -f /var/tmp/${SUNSHINE_OUTPUT_NAME} /tmp/sunshine-answer.iso; true"

    trap - EXIT

    log "Sunshine VM image: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# ── Main ─────────────────────────────────────────────────────────────

BUILD_TARGETS=()

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
        --only)
            [[ -n "${2:-}" ]] || die "--only requires a target (mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, moonlight, gaming, sunshine, desktop)"
            BUILD_TARGETS+=("$2")
            shift 2
            ;;
        *)
            die "Unknown argument: $1\nUsage: $0 --host <ip> [--only <target>] [--clean]\n  Targets: mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, moonlight, gaming, sunshine, desktop"
            ;;
    esac
done

should_build() {
    [[ ${#BUILD_TARGETS[@]} -eq 0 ]] && return 0
    local target
    for target in "${BUILD_TARGETS[@]}"; do
        [[ "$target" == "$1" ]] && return 0
    done
    return 1
}

check_deps

if should_build mesh || should_build router; then
    download_imagebuilder
fi

should_build mesh    && build_mesh_lxc
should_build router  && build_router_vm
should_build pihole  && build_pihole_lxc
should_build rsyslog && build_rsyslog_lxc
should_build jellyfin && build_jellyfin_lxc
should_build netdata    && build_netdata_lxc
should_build wireguard  && build_wireguard_lxc
should_build homeassistant && build_homeassistant_lxc
should_build kodi         && build_kodi_lxc
should_build moonlight    && build_moonlight_lxc
should_build gaming       && build_gaming_lxc
should_build sunshine     && build_sunshine_vm
should_build desktop      && build_desktop_vm

log ""
log "Done. Custom images in ${IMAGES_DIR}/:"
ls -lh "${IMAGES_DIR}/${MESH_OUTPUT_NAME}" "${IMAGES_DIR}/${ROUTER_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${PIHOLE_OUTPUT_NAME}" "${IMAGES_DIR}/${RSYSLOG_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${JELLYFIN_OUTPUT_NAME}" "${IMAGES_DIR}/${NETDATA_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${WIREGUARD_OUTPUT_NAME}" "${IMAGES_DIR}/${HOMEASSISTANT_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${KODI_OUTPUT_NAME}" "${IMAGES_DIR}/${MOONLIGHT_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${GAMING_OUTPUT_NAME}" "${IMAGES_DIR}/${SUNSHINE_OUTPUT_NAME}" \
    "${IMAGES_DIR}/${DESKTOP_OUTPUT_NAME}" \
    2>/dev/null || true

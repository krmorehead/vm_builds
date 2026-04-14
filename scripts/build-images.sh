#!/usr/bin/env bash
set -euo pipefail

# Builds custom images for the vm_builds project.
# Produces fourteen outputs:
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
#  11. Kiosk LXC template       — Debian 12 with Cage + Chromium + Mesa for kiosk dashboard (remote build on Proxmox)
#  12. Gaming LXC template     — Fedora with Sunshine + dsda-doom + Mesa VA-API + PipeWire (remote build on Proxmox)
#  13. Sunshine VM image        — Windows 11 with Sunshine + dsda-doom + virtio drivers    (remote build on Proxmox)
#  14. Desktop VM image         — Debian 12 with KDE + GNOME + SDDM + apps baked in    (remote build on Proxmox)
#
# Usage: ./build-images.sh [--clean] [--host <proxmox-ip>] [--only <target>]
#                          [--parallel] [--hosts <ip1>,<ip2>,...]
#   --clean          Remove cached Image Builder before downloading fresh copy
#   --host <ip>      Proxmox host for remote image builds. Required for remote-built templates.
#   --only <target>  Build only the specified target (mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, kiosk, moonlight, gaming, sunshine, desktop).
#   --parallel       Build images across multiple hosts in parallel.
#                    Reads host IPs from PRIMARY_HOST, AI_HOST, MESH_2_HOST env vars.
#   --hosts <ips>    Comma-separated list of Proxmox host IPs for parallel builds.
#                    Implies --parallel.
#
# Image versions are tracked in per-image sidecar files: images/<target>.version
# Filenames include the semver: pihole-1.0.0-debian-12-amd64.tar.zst
# Each build auto-bumps the patch version.

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
ROUTER_FILES_DIR="${SCRIPT_DIR}/image-builder/files-router-vm"

# Canonical sources for scripts shared between mesh and router images.
# Copied into both files directories before building to avoid duplication.
SHARED_SCRIPTS_DIR="${SCRIPT_DIR}/image-builder/shared-scripts"

sync_shared_scripts() {
    local target_dir="$1"
    if [[ -d "$SHARED_SCRIPTS_DIR" ]]; then
        for src in "$SHARED_SCRIPTS_DIR"/*; do
            [[ -f "$src" ]] || continue
            local fname
            fname="$(basename "$src")"
            cp -f "$src" "$target_dir/usr/sbin/$fname"
            chmod +x "$target_dir/usr/sbin/$fname"
        done
    fi
}

# Output names are computed per-build from sidecar .version files

# Shared Debian base template for all Debian-based LXC builds
DEBIAN_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"

# Pi-hole LXC template (built remotely on Proxmox via pct create/exec/vzdump)
PIHOLE_BUILD_VMID=998

# rsyslog LXC template (built remotely on Proxmox via pct create/exec/vzdump)
RSYSLOG_BUILD_VMID=997

# Gaming LXC template (built remotely on Proxmox — Fedora base with Sunshine + dsda-doom)
GAMING_FEDORA_VERSION="41"
GAMING_BASE_ROOTFS="fedora-${GAMING_FEDORA_VERSION}-default-amd64.tar.xz"
GAMING_LXC_IMAGE_URL="https://images.linuxcontainers.org/images/fedora/${GAMING_FEDORA_VERSION}/amd64/default"
GAMING_BUILD_VMID=990

# Desktop VM image (built remotely on Proxmox: cloud-init boot → apt install desktops → export disk)
DESKTOP_BASE_IMAGE="debian-12-generic-amd64.qcow2"
DESKTOP_BUILD_VMID=991

# Remote Proxmox host (set via --host flag)
PROXMOX_HOST=""
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# Parallel build mode (set via --parallel or --hosts)
PARALLEL_MODE=false
PARALLEL_HOSTS=()
CLEAN_MODE=false

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
    # batman-adv mesh routing (dormant until configured)
    kmod-batman-adv
    batctl-tiny
    # openssl CLI for HMAC verification in batman_trigger.sh
    openssl-util
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
    # batman-adv mesh routing (dormant until configured)
    kmod-batman-adv
    batctl-tiny
    # openssl CLI for HMAC verification in batman_trigger.sh
    openssl-util
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

# Inject the callhome heartbeat agent into a Debian-based build container.
# Called by each build_*_lxc() function after package installation.
inject_callhome_agent() {
    local vmid="$1"
    log "Injecting callhome agent into container ${vmid}..."

    # Copy callhome.py to the Proxmox host, then push into the container
    local callhome_src="${SCRIPT_DIR}/callhome.py"
    if [[ ! -f "$callhome_src" ]]; then
        log "WARNING: callhome.py not found at ${callhome_src}, skipping agent injection"
        return
    fi

    # shellcheck disable=SC2086
    scp $SSH_OPTS "$callhome_src" "root@${PROXMOX_HOST}:/tmp/callhome.py"
    remote_cmd "pct exec ${vmid} -- mkdir -p /opt/callhome"
    remote_cmd "pct push ${vmid} /tmp/callhome.py /opt/callhome/callhome.py"
    remote_cmd "rm -f /tmp/callhome.py"
    remote_cmd "pct exec ${vmid} -- chmod +x /opt/callhome/callhome.py"

    # Create /etc/default/callhome with placeholder (populated by configure role)
    remote_cmd "pct exec ${vmid} -- bash -c 'cat > /etc/default/callhome << \"CONF_EOF\"
# Populated by Ansible configure role at deploy time
CALLHOME_SERVER=
CALLHOME_PUBLIC_KEY=
CONF_EOF'"

    # Create systemd service unit
    remote_cmd "pct exec ${vmid} -- bash -c 'cat > /etc/systemd/system/callhome.service << \"UNIT_EOF\"
[Unit]
Description=Call-home heartbeat agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/callhome/callhome.py --container --interval 60 --interval-startup 5
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT_EOF'"

    remote_cmd "pct exec ${vmid} -- systemctl enable callhome.service 2>/dev/null || true"
    log "Callhome agent injected."
}

# Shared cleanup for any LXC build container
cleanup_lxc_build() {
    local vmid="$1"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up build container ${vmid}..."
        remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
    fi
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

# ── Image version helpers ─────────────────────────────────────────────

compute_filename() {
    local target="$1" version="$2"
    case "$target" in
        mesh)          echo "openwrt-mesh-lxc-${version}-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}-rootfs.tar.gz" ;;
        router)        echo "openwrt-router-${version}-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}-combined.img.gz" ;;
        pihole)        echo "pihole-${version}-debian-12-amd64.tar.zst" ;;
        rsyslog)       echo "rsyslog-${version}-debian-12-amd64.tar.zst" ;;
        jellyfin)      echo "jellyfin-${version}-debian-12-amd64.tar.zst" ;;
        netdata)       echo "netdata-${version}-debian-12-amd64.tar.zst" ;;
        wireguard)     echo "wireguard-${version}-debian-12-amd64.tar.zst" ;;
        homeassistant) echo "homeassistant-${version}-debian-12-amd64.tar.zst" ;;
        kodi)          echo "kodi-${version}-debian-12-amd64.tar.zst" ;;
        moonlight)     echo "moonlight-${version}-debian-12-amd64.tar.zst" ;;
        kiosk)         echo "kiosk-${version}-debian-12-amd64.tar.zst" ;;
        gaming)        echo "gaming-${version}-fedora-amd64.tar.zst" ;;
        sunshine)      echo "sunshine-${version}-win11-amd64.qcow2" ;;
        desktop)       echo "desktop-${version}-debian-12-amd64.qcow2" ;;
        *)             die "Unknown target for filename: $target" ;;
    esac
}

bump_version() {
    local version="$1" level="$2"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$version"
    case "$level" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "${major}.$((minor + 1)).0" ;;
        patch) echo "${major}.${minor}.$((patch + 1))" ;;
        *)     die "Invalid bump level: $level (use major, minor, or patch)" ;;
    esac
}

init_build_version() {
    local target="$1"
    _CUR_VERSION=$(cat "${IMAGES_DIR}/${target}.version" 2>/dev/null || echo "0.0.0")
    _NEW_VERSION=$(bump_version "$_CUR_VERSION" "patch")
}

finalize_build() {
    local target="$1" output="$2" version="$3"
    echo "$version" > "${IMAGES_DIR}/${target}.version"
    log "Build complete: $(basename "$output") v${version}"
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
    init_build_version "mesh"
    local output="${IMAGES_DIR}/$(compute_filename mesh "$_NEW_VERSION")"

    sync_shared_scripts "$MESH_FILES_DIR"
    log "Building mesh LXC rootfs..."
    local ib_dir="${BUILD_DIR}/${IB_NAME}"
    local pkg_list
    pkg_list=$(IFS=' '; echo "${MESH_PACKAGES[*]}")

    echo "${_NEW_VERSION}" > "${MESH_FILES_DIR}/etc/image_version"

    local make_log="${BUILD_DIR}/mesh-build.log"
    make -C "$ib_dir" image \
        PROFILE="generic" \
        PACKAGES="$pkg_list" \
        FILES="$MESH_FILES_DIR" \
        EXTRA_IMAGE_NAME="mesh-lxc" \
        2>&1 | tee "$make_log" | tail -5
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        die "Mesh LXC image build failed. Full log: ${make_log}"
    fi

    local rootfs
    rootfs=$(find "${ib_dir}/bin" -name '*mesh-lxc*rootfs.tar.gz' -print -quit 2>/dev/null)
    if [[ -z "$rootfs" ]]; then
        die "Mesh LXC rootfs not found in Image Builder output"
    fi

    mkdir -p "$IMAGES_DIR"
    cp "$rootfs" "$output"
    finalize_build "mesh" "$output" "$_NEW_VERSION"
    log "Mesh LXC rootfs: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

build_router_vm() {
    init_build_version "router"
    local output="${IMAGES_DIR}/$(compute_filename router "$_NEW_VERSION")"

    sync_shared_scripts "$ROUTER_FILES_DIR"
    log "Building router VM image..."
    local ib_dir="${BUILD_DIR}/${IB_NAME}"
    local pkg_list
    pkg_list=$(IFS=' '; echo "${ROUTER_PACKAGES[*]}")

    echo "${_NEW_VERSION}" > "${ROUTER_FILES_DIR}/etc/image_version"

    # Clean previous build artifacts to avoid profile collision
    make -C "$ib_dir" clean 2>/dev/null || true

    local make_log="${BUILD_DIR}/router-build.log"
    make -C "$ib_dir" image \
        PROFILE="generic" \
        PACKAGES="$pkg_list" \
        FILES="$ROUTER_FILES_DIR" \
        EXTRA_IMAGE_NAME="router" \
        2>&1 | tee "$make_log" | tail -5
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        die "Router VM image build failed. Full log: ${make_log}"
    fi

    local combined
    combined=$(find "${ib_dir}/bin" -name '*combined-ext4.img.gz' -print -quit 2>/dev/null)
    if [[ -z "$combined" ]]; then
        combined=$(find "${ib_dir}/bin" -name '*combined*.img.gz' -print -quit 2>/dev/null)
    fi
    if [[ -z "$combined" ]]; then
        die "Router VM image not found in Image Builder output"
    fi

    mkdir -p "$IMAGES_DIR"
    cp "$combined" "$output"
    finalize_build "router" "$output" "$_NEW_VERSION"
    log "Router VM image: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_pihole_build() { cleanup_lxc_build "${PIHOLE_BUILD_VMID}"; }

build_pihole_lxc() {
    init_build_version "pihole"
    log "Building Pi-hole LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename pihole "$_NEW_VERSION")"
    local vmid="${PIHOLE_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Pi-hole build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_pihole_build EXIT

    # Ensure no stale build container exists
    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    # Upload base template if not already cached on host
    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
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
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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

[dhcp]
active = false

[database]
maxDBdays = 30
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

    log "Configuring Pi-hole for kiosk iframe embedding..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        sed -i \"/X-Frame-Options: DENY/d\" /etc/pihole/pihole.toml
        sed -i \"s/frame-ancestors .none./frame-ancestors */g\" /etc/pihole/pihole.toml
    '"

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "pihole" "$output" "$_NEW_VERSION"
    log "Pi-hole LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_rsyslog_build() { cleanup_lxc_build "${RSYSLOG_BUILD_VMID}"; }

build_rsyslog_lxc() {
    init_build_version "rsyslog"
    log "Building rsyslog LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename rsyslog "$_NEW_VERSION")"
    local vmid="${RSYSLOG_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "rsyslog build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_rsyslog_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
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

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "rsyslog" "$output" "$_NEW_VERSION"
    log "rsyslog LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Jellyfin LXC template (built remotely on Proxmox via pct create/exec/vzdump)
JELLYFIN_BUILD_VMID=995

cleanup_jellyfin_build() { cleanup_lxc_build "${JELLYFIN_BUILD_VMID}"; }

build_jellyfin_lxc() {
    init_build_version "jellyfin"
    log "Building Jellyfin LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename jellyfin "$_NEW_VERSION")"
    local vmid="${JELLYFIN_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Jellyfin build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_jellyfin_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
    log "Container is running."

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

        # Create render group and add jellyfin to it
        groupadd -g 993 render 2>/dev/null || true
        usermod -a -G render jellyfin 2>/dev/null || true

        # Pre-configure Jellyfin
        mkdir -p /etc/jellyfin

        cat > /etc/jellyfin/jellyfin.conf << \"JELLYFIN_CONF_EOF\"
[Networking]
_port = 8096
_base_url = /

[MediaEncoder]
_vaapi_device = /dev/dri/renderD128
_vaapi_driver = auto
JELLYFIN_CONF_EOF

        cat > /etc/jellyfin/network.xml << \"NETWORK_EOF\"
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Network>
  <BaseUrl>/</BaseUrl>
  <Port>8096</Port>
  <EnableHttps>false</EnableHttps>
  <EnableUPnP>false</EnableUPnP>
</Network>
NETWORK_EOF

        cat > /etc/jellyfin/library.xml << \"LIBRARY_EOF\"
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Library>
  <SaveMetadataHidden>false</SaveMetadataHidden>
  <EnableAutomaticSeriesGrouping>false</EnableAutomaticSeriesGrouping>
  <EnableAutomaticPhotosGrouping>true</EnableAutomaticPhotosGrouping>
  <CollectionFolders>
    <CollectionFolder>
      <Name>Media</Name>
      <Path>/media</Path>
    </CollectionFolder>
  </CollectionFolders>
</Library>
LIBRARY_EOF

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

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "jellyfin" "$output" "$_NEW_VERSION"
    log "Jellyfin LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Netdata LXC template (built remotely on Proxmox via pct create/exec/vzdump)
NETDATA_BUILD_VMID=996

cleanup_netdata_build() { cleanup_lxc_build "${NETDATA_BUILD_VMID}"; }

build_netdata_lxc() {
    init_build_version "netdata"
    log "Building Netdata LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename netdata "$_NEW_VERSION")"
    local vmid="${NETDATA_BUILD_VMID}"


    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Netdata build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_netdata_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
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

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "netdata" "$output" "$_NEW_VERSION"
    log "Netdata LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# WireGuard LXC template (built remotely on Proxmox via pct create/exec/vzdump)
WIREGUARD_BUILD_VMID=989

# Kodi LXC template (built remotely on Proxmox via pct create/exec/vzdump)
KODI_BUILD_VMID=993

# Kiosk LXC template (built remotely on Proxmox via pct create/exec/vzdump)
KIOSK_BUILD_VMID=992

# Moonlight LXC template (built remotely on Proxmox via pct create/exec/vzdump)
MOONLIGHT_BUILD_VMID=988

# Home Assistant LXC template (built remotely on Proxmox via pct create/exec/vzdump)
HOMEASSISTANT_BUILD_VMID=994

cleanup_kodi_build() { cleanup_lxc_build "${KODI_BUILD_VMID}"; }

build_kodi_lxc() {
    init_build_version "kodi"
    log "Building Kodi LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename kodi "$_NEW_VERSION")"
    local vmid="${KODI_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Kodi build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_kodi_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing Kodi + headless Wayland VNC stack (sway + wayvnc)..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        apt-get install -y --no-install-recommends \
            kodi \
            kodi-peripheral-joystick \
            libcec6 \
            cec-utils \
            alsa-utils \
            sway \
            wayvnc \
            python3-websockify \
            xwayland

        # VA-API drivers for both Intel and AMD iGPU
        apt-get install -y --no-install-recommends \
            intel-media-va-driver \
            mesa-va-drivers \
            vainfo

        # Create kodi system user for headless operation
        useradd -r -m -G audio,video,input,render -s /bin/bash kodi 2>/dev/null || true

        # Headless Wayland display via sway compositor (virtual input support)
        mkdir -p /home/kodi/.config/sway
        cat > /home/kodi/.config/sway/config << \"SWAY_CFG\"
output HEADLESS-1 resolution 1920x1080 position 0,0
for_window [app_id=\".*\"] fullscreen enable
for_window [class=\".*\"] fullscreen enable
exec /usr/bin/kodi --windowing=wayland
SWAY_CFG
        chown -R kodi:kodi /home/kodi/.config

        cat > /etc/systemd/system/kodi-display.service << \"SERVICE_EOF\"
[Unit]
Description=Kodi Headless Wayland Display (sway)
After=systemd-user-sessions.service network-online.target sound.target
Wants=network-online.target

[Service]
User=kodi
Group=kodi
PAMName=login
Type=simple
Environment=WLR_BACKENDS=headless
Environment=WLR_LIBINPUT_NO_DEVICES=1
Environment=WLR_RENDERER=pixman
Environment=XDG_RUNTIME_DIR=/run/user/999
ExecStartPre=+/bin/sh -c \"mkdir -p /run/user/999 && chown kodi:kodi /run/user/999 && chmod 700 /run/user/999\"
ExecStart=/usr/bin/sway
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        # VNC capture of the Wayland display
        cat > /etc/systemd/system/kodi-vnc.service << \"SERVICE_EOF\"
[Unit]
Description=Kodi VNC Server (wayvnc)
After=kodi-display.service
Requires=kodi-display.service

[Service]
User=kodi
Group=kodi
Type=simple
Environment=WAYLAND_DISPLAY=wayland-1
Environment=XDG_RUNTIME_DIR=/run/user/999
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/wayvnc --render-cursor 0.0.0.0 5900
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        # WebSocket bridge for noVNC
        cat > /etc/systemd/system/kodi-vnc-ws.service << \"SERVICE_EOF\"
[Unit]
Description=Kodi VNC WebSocket bridge
After=kodi-vnc.service
Requires=kodi-vnc.service

[Service]
Type=simple
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/websockify 0.0.0.0:6082 localhost:5900
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        loginctl enable-linger kodi 2>/dev/null || true
        systemctl daemon-reload
        systemctl enable kodi-display kodi-vnc kodi-vnc-ws 2>/dev/null || true

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

        # Bake default guisettings.xml (web server on port 8080)
        cat > /home/kodi/.kodi/userdata/guisettings.xml << \"GUI_EOF\"
<settings version=\"2\">
  <setting id=\"services.webserver\">true</setting>
  <setting id=\"services.webserverport\">8080</setting>
  <setting id=\"services.esallinterfaces\">true</setting>
  <setting id=\"services.esenabled\">true</setting>
  <setting id=\"services.esport\">9090</setting>
</settings>
GUI_EOF

        chown -R kodi:kodi /home/kodi/.kodi

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    log "Verifying Kodi installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dpkg -l kodi | grep -c ^ii || { echo FAIL: kodi not installed; exit 1; }
        test -f /etc/systemd/system/kodi-display.service || { echo FAIL: service missing; exit 1; }
        test -f /home/kodi/.kodi/userdata/advancedsettings.xml || { echo FAIL: settings missing; exit 1; }
        id kodi || { echo FAIL: kodi user missing; exit 1; }
        echo ALL CHECKS PASSED
    '"
    log "Kodi smoke test passed."

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "kodi" "$output" "$_NEW_VERSION"
    log "Kodi LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_kiosk_build() { cleanup_lxc_build "${KIOSK_BUILD_VMID}"; }

build_kiosk_lxc() {
    init_build_version "kiosk"
    log "Building Kiosk LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename kiosk "$_NEW_VERSION")"
    local vmid="${KIOSK_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Kiosk build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_kiosk_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
        --hostname kiosk-build \
        --memory 1024 \
        --cores 2 \
        --rootfs local-lvm:4 \
        --net0 name=eth0,bridge=${mgmt_bridge},ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --features nesting=1 \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing sway compositor, Chromium, and Mesa drivers..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        apt-get install -y --no-install-recommends \
            sway \
            chromium \
            fonts-noto \
            fonts-noto-color-emoji \
            openssh-client \
            xwayland \
            wayvnc \
            python3-websockify

        # VA-API drivers for both Intel and AMD iGPU
        apt-get install -y --no-install-recommends \
            intel-media-va-driver \
            mesa-va-drivers \
            vainfo

        # Install Python and NiceGUI for the Home Hub web UI
        apt-get install -y --no-install-recommends python3 python3-pip
        pip3 install --break-system-packages nicegui==3.9.0

        # Create kiosk system user for headless operation
        useradd -r -m -G video,render -s /bin/bash kiosk 2>/dev/null || true
        mkdir -p /opt/kiosk/scripts/webui/pages
        chown -R kiosk:kiosk /opt/kiosk

        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    '"

    # Write systemd units and helper script via separate pct push calls
    # to avoid nested single-quote issues inside bash -c
    log "Installing systemd services..."

    local tmpdir
    tmpdir=$(mktemp -d)

    cat > "${tmpdir}/kiosk-web.service" << 'WEB_EOF'
[Unit]
Description=Kiosk Home Hub Web Server (NiceGUI)
After=network-online.target
Wants=network-online.target

[Service]
User=kiosk
Group=kiosk
Type=simple
WorkingDirectory=/opt/kiosk
ExecStart=/usr/bin/python3 /opt/kiosk/scripts/webui/kiosk_server.py --port 9001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
WEB_EOF

    cat > "${tmpdir}/wait-for-hub.sh" << 'WAIT_EOF'
#!/bin/bash
for i in $(seq 1 15); do
    wget -q -O /dev/null http://127.0.0.1:9001/ 2>/dev/null && exit 0
    sleep 1
done
echo "Hub server not ready"
exit 1
WAIT_EOF

    cat > "${tmpdir}/kiosk-display.service" << 'DISPLAY_EOF'
[Unit]
Description=Kiosk Dashboard (sway + Chromium)
After=systemd-user-sessions.service kiosk-web.service
Wants=network-online.target kiosk-web.service

[Service]
User=kiosk
Group=kiosk
Type=simple
Environment=WLR_LIBINPUT_NO_DEVICES=1
Environment=WLR_BACKENDS=headless
Environment=WLR_RENDERER=pixman
Environment=XDG_RUNTIME_DIR=/run/user/999
ExecStartPre=+/bin/sh -c 'mkdir -p /run/user/999 && chown kiosk:kiosk /run/user/999 && chmod 700 /run/user/999'
ExecStartPre=/opt/kiosk/wait-for-hub.sh
ExecStart=/usr/bin/sway
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
DISPLAY_EOF

    cat > "${tmpdir}/kiosk-vnc.service" << 'VNC_EOF'
[Unit]
Description=Kiosk VNC Server (wayvnc)
After=kiosk-display.service
Requires=kiosk-display.service

[Service]
User=kiosk
Group=kiosk
Environment=XDG_RUNTIME_DIR=/run/user/999
Environment=WAYLAND_DISPLAY=wayland-1
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/wayvnc --render-cursor 0.0.0.0 5900
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
VNC_EOF

    cat > "${tmpdir}/kiosk-vnc-ws.service" << 'VNCWS_EOF'
[Unit]
Description=Kiosk VNC WebSocket Bridge (websockify)
After=kiosk-vnc.service
Requires=kiosk-vnc.service

[Service]
Type=simple
ExecStart=/usr/bin/websockify 0.0.0.0:6080 localhost:5900
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
VNCWS_EOF

    # Push files into the container
    for f in kiosk-web.service kiosk-display.service kiosk-vnc.service kiosk-vnc-ws.service; do
        # shellcheck disable=SC2086
        scp $SSH_OPTS "${tmpdir}/${f}" "root@${PROXMOX_HOST}:/tmp/${f}"
        remote_cmd "pct push ${vmid} /tmp/${f} /etc/systemd/system/${f} && rm -f /tmp/${f}"
    done
    # shellcheck disable=SC2086
    scp $SSH_OPTS "${tmpdir}/wait-for-hub.sh" "root@${PROXMOX_HOST}:/tmp/wait-for-hub.sh"
    remote_cmd "pct push ${vmid} /tmp/wait-for-hub.sh /opt/kiosk/wait-for-hub.sh && rm -f /tmp/wait-for-hub.sh"
    remote_cmd "pct exec ${vmid} -- chmod +x /opt/kiosk/wait-for-hub.sh"

    # Create sway config for kiosk fullscreen mode
    remote_cmd "pct exec ${vmid} -- bash -c '
        mkdir -p /home/kiosk/.config/sway
        cat > /home/kiosk/.config/sway/config << \"SWAY_CFG\"
output HEADLESS-1 resolution 1920x1080 position 0,0
for_window [app_id=\".*\"] fullscreen enable
for_window [class=\".*\"] fullscreen enable
exec /usr/bin/chromium --kiosk --no-sandbox --ozone-platform=wayland --disable-gpu-compositing --noerrdialogs --disable-infobars --no-first-run --disable-translate --disable-features=TranslateUI --start-fullscreen http://127.0.0.1:9001/hub
SWAY_CFG
        chown -R kiosk:kiosk /home/kiosk/.config
        loginctl enable-linger kiosk 2>/dev/null || true
    '"
    remote_cmd "pct exec ${vmid} -- systemctl daemon-reload"

    rm -rf "${tmpdir}"

    # Bake webui application code into the image
    log "Baking webui Python files and build.py into image..."
    local webui_src="${SCRIPT_DIR}/webui"
    local webui_tar
    webui_tar=$(mktemp)

    # Build list of files to bake into the image
    local -a tar_files=(
        __init__.py theme.py data.py heartbeat.py manager.py
        host_state.py api_client.py metric_controller.py
        display_transfer.py kiosk_server.py
        pages/__init__.py pages/hub.py pages/bridge.py pages/mesh.py
        pages/router.py pages/viewer.py pages/launch.py
        pages/containers.py pages/cluster_dashboard.py
        pages/remote_kiosk.py pages/console.py pages/vnc_shared.py
    )
    # Include noVNC static assets if present
    if [[ -d "${SCRIPT_DIR}/webui/static/noVNC" ]]; then
        while IFS= read -r -d '' f; do
            tar_files+=("$f")
        done < <(cd "${SCRIPT_DIR}/webui" && find static/noVNC -type f -print0)
    fi

    tar czf "${webui_tar}" \
        -C "${SCRIPT_DIR}/webui" \
        "${tar_files[@]}" \
        -C "${SCRIPT_DIR}/.." build.py

    # shellcheck disable=SC2086
    scp $SSH_OPTS "${webui_tar}" "root@${PROXMOX_HOST}:/tmp/kiosk_webui.tar.gz"
    remote_cmd "pct push ${vmid} /tmp/kiosk_webui.tar.gz /tmp/kiosk_webui.tar.gz && rm -f /tmp/kiosk_webui.tar.gz"
    remote_cmd "pct exec ${vmid} -- bash -c '
        mkdir -p /opt/kiosk/scripts/webui/pages /opt/kiosk/scripts/webui/static
        touch /opt/kiosk/scripts/__init__.py
        tar xzf /tmp/kiosk_webui.tar.gz -C /opt/kiosk/scripts/webui/
        mv /opt/kiosk/scripts/webui/build.py /opt/kiosk/build.py 2>/dev/null || true
        chown -R kiosk:kiosk /opt/kiosk
        rm -f /tmp/kiosk_webui.tar.gz
    '"
    rm -f "${webui_tar}"

    # Enable services so they start on boot
    remote_cmd "pct exec ${vmid} -- bash -c '
        systemctl enable kiosk-web kiosk-display kiosk-vnc kiosk-vnc-ws 2>/dev/null || true
    '"

    log "Verifying Kiosk installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dpkg -l sway | grep -c ^ii || { echo FAIL: sway not installed; exit 1; }
        dpkg -l chromium | grep -c ^ii || { echo FAIL: chromium not installed; exit 1; }
        python3 -c \"import nicegui\" || { echo FAIL: nicegui not installed; exit 1; }
        test -f /etc/systemd/system/kiosk-display.service || { echo FAIL: display service missing; exit 1; }
        test -f /etc/systemd/system/kiosk-web.service || { echo FAIL: web service missing; exit 1; }
        test -f /etc/systemd/system/kiosk-vnc.service || { echo FAIL: vnc service missing; exit 1; }
        test -f /etc/systemd/system/kiosk-vnc-ws.service || { echo FAIL: vnc-ws service missing; exit 1; }
        dpkg -l wayvnc | grep -c ^ii || { echo FAIL: wayvnc not installed; exit 1; }
        dpkg -l python3-websockify | grep -c ^ii || { echo FAIL: websockify not installed; exit 1; }
        test -x /opt/kiosk/wait-for-hub.sh || { echo FAIL: wait-for-hub script missing; exit 1; }
        id kiosk || { echo FAIL: kiosk user missing; exit 1; }
        test -d /opt/kiosk/scripts/webui || { echo FAIL: kiosk webui dir missing; exit 1; }
        test -f /opt/kiosk/scripts/webui/kiosk_server.py || { echo FAIL: kiosk_server.py missing; exit 1; }
        test -f /opt/kiosk/build.py || { echo FAIL: build.py missing; exit 1; }
        echo ALL CHECKS PASSED
    '"
    log "Kiosk smoke test passed."

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "kiosk" "$output" "$_NEW_VERSION"
    log "Kiosk LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_moonlight_build() { cleanup_lxc_build "${MOONLIGHT_BUILD_VMID}"; }

build_moonlight_lxc() {
    init_build_version "moonlight"
    log "Building Moonlight LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename moonlight "$_NEW_VERSION")"
    local vmid="${MOONLIGHT_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Moonlight build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_moonlight_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
        fi
        sleep 2
    done
    log "Network ready."

    log "Installing runtime and build dependencies + headless VNC stack..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        # Runtime deps (marked manual — survives autoremove after build)
        # SDL2 for video output via Wayland under sway compositor
        # FFmpeg for video decode
        # VA-API drivers for Intel + AMD hardware decode
        # sway + wayvnc + websockify for headless VNC streaming
        apt-get install -y --no-install-recommends \
            libopus0 libexpat1 libasound2 libudev1 libavahi-client3 \
            libcurl4 libevdev2 libpulse0 libsdl2-2.0-0 \
            libavcodec59 libavutil57 \
            intel-media-va-driver mesa-va-drivers vainfo \
            ca-certificates \
            sway wayvnc python3-websockify xwayland

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

    log "Creating moonlight user and deploying VNC systemd units..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        useradd -r -m -G audio,video,input,render -s /bin/bash moonlight 2>/dev/null || true

        mkdir -p /home/moonlight/.config/sway
        cat > /home/moonlight/.config/sway/config << \"SWAY_CFG\"
output HEADLESS-1 resolution 1920x1080 position 0,0
for_window [app_id=\".*\"] fullscreen enable
for_window [class=\".*\"] fullscreen enable
exec /usr/local/bin/moonlight stream
SWAY_CFG
        chown -R moonlight:moonlight /home/moonlight/.config
        loginctl enable-linger moonlight 2>/dev/null || true

        cat > /etc/systemd/system/moonlight-display.service << \"SERVICE_EOF\"
[Unit]
Description=Moonlight Headless Wayland Display (sway)
After=network-online.target
Wants=network-online.target

[Service]
User=moonlight
Group=moonlight
PAMName=login
Type=simple
Environment=WLR_BACKENDS=headless
Environment=WLR_LIBINPUT_NO_DEVICES=1
Environment=WLR_RENDERER=pixman
Environment=SDL_VIDEODRIVER=wayland
Environment=XDG_RUNTIME_DIR=/run/user/999
ExecStartPre=+/bin/sh -c \"mkdir -p /run/user/999 && chown moonlight:moonlight /run/user/999 && chmod 700 /run/user/999\"
ExecStart=/usr/bin/sway
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        cat > /etc/systemd/system/moonlight-vnc.service << \"SERVICE_EOF\"
[Unit]
Description=Moonlight VNC Server (wayvnc)
After=moonlight-display.service
Requires=moonlight-display.service

[Service]
User=moonlight
Group=moonlight
Type=simple
Environment=WAYLAND_DISPLAY=wayland-1
Environment=XDG_RUNTIME_DIR=/run/user/999
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/wayvnc --render-cursor 0.0.0.0 5900
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        # WebSocket bridge for noVNC
        cat > /etc/systemd/system/moonlight-vnc-ws.service << \"SERVICE_EOF\"
[Unit]
Description=Moonlight VNC WebSocket bridge
After=moonlight-vnc.service
Requires=moonlight-vnc.service

[Service]
Type=simple
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/websockify 0.0.0.0:6083 localhost:5900
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

        systemctl daemon-reload
        systemctl enable moonlight-display moonlight-vnc moonlight-vnc-ws 2>/dev/null || true
    '"

    log "Verifying Moonlight installation..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        test -x /usr/local/bin/moonlight || { echo FAIL: moonlight-embedded not installed; exit 1; }
        /usr/local/bin/moonlight --version 2>&1 || true
        vainfo --display drm 2>&1 | head -5 || true
        echo ALL CHECKS PASSED
    '"
    log "Moonlight smoke test passed."

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "moonlight" "$output" "$_NEW_VERSION"
    log "Moonlight LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_wireguard_build() { cleanup_lxc_build "${WIREGUARD_BUILD_VMID}"; }

cleanup_homeassistant_build() { cleanup_lxc_build "${HOMEASSISTANT_BUILD_VMID}"; }

build_wireguard_lxc() {
    init_build_version "wireguard"
    log "Building WireGuard LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename wireguard "$_NEW_VERSION")"
    local vmid="${WIREGUARD_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "WireGuard build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_wireguard_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
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

        systemctl enable wg-quick@wg0 2>/dev/null || true

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

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "wireguard" "$output" "$_NEW_VERSION"
    log "WireGuard LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

build_homeassistant_lxc() {
    init_build_version "homeassistant"
    log "Building Home Assistant LXC template (remote on Proxmox)..."
    local base_template="${IMAGES_DIR}/${DEBIAN_BASE_TEMPLATE}"
    local output="${IMAGES_DIR}/$(compute_filename homeassistant "$_NEW_VERSION")"
    local vmid="${HOMEASSISTANT_BUILD_VMID}"

    if [[ -z "$PROXMOX_HOST" ]]; then
        die "Home Assistant build requires --host <proxmox-ip>. Example:
  ./build-images.sh --host 192.168.86.201"
    fi

    if [[ ! -f "$base_template" ]]; then
        die "Base template not found: ${base_template}. Download it first:
  wget -O ${base_template} \\
    http://download.proxmox.com/images/system/${DEBIAN_BASE_TEMPLATE}"
    fi

    trap cleanup_homeassistant_build EXIT

    remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    local remote_template="/var/lib/vz/template/cache/${DEBIAN_BASE_TEMPLATE}"
    if ! remote_cmd "test -f ${remote_template}"; then
        log "Uploading base template to Proxmox host..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$base_template" "root@${PROXMOX_HOST}:${remote_template}"
    fi

    local mgmt_bridge
    mgmt_bridge=$(remote_cmd "ip -o route show default | awk '{print \$5}' | head -1")
    log "Management bridge: ${mgmt_bridge}"

    log "Creating temporary build container (VMID ${vmid})..."
    remote_cmd "pct create ${vmid} local:vztmpl/${DEBIAN_BASE_TEMPLATE} \
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
        if (( net_retries > 30 )); then
            remote_cmd "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"
            die "Build container never got network after 60s"
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
          \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \\
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

        # Pre-pull Home Assistant container image
        systemctl start docker
        docker pull homeassistant/home-assistant:stable

        # Bake config directory and static configuration files
        mkdir -p /opt/homeassistant/config

        cat > /opt/homeassistant/config/docker-compose.yml << \"COMPOSE_EOF\"
services:
  home-assistant:
    image: homeassistant/home-assistant:stable
    container_name: home-assistant
    restart: always
    network_mode: host
    volumes:
      - /opt/homeassistant/config:/config
    environment:
      - TZ=UTC
    privileged: false
    security_opt:
      - no-new-privileges:true
    cap_add:
      - SYS_TIME
COMPOSE_EOF

        cat > /opt/homeassistant/config/configuration.yaml << \"HA_CFG_EOF\"
default_config:

http:
  server_port: 8123
  ip_ban_enabled: true
  login_attempts_threshold: 5
  use_x_forwarded_for: true
  use_x_frame_options: false
  trusted_proxies:
    - 127.0.0.1
    - "::1"
    - 10.10.10.0/24
    - 10.99.0.0/16

recorder:
  db_url: sqlite:///config/home-assistant_v2.db
  purge_keep_days: 10
  exclude:
    entities:
      - sensor.date
      - sensor.time
      - sun.sun
    domains:
      - updater

logger:
  default: INFO
  logs:
    homeassistant.core: WARNING
    homeassistant.components.recorder: WARNING
    homeassistant.components.http: WARNING

automation: []
HA_CFG_EOF

        # Bake systemd unit to start HA compose on boot
        cat > /etc/systemd/system/homeassistant-compose.service << \"HA_SVC_EOF\"
[Unit]
Description=Home Assistant Docker Compose
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/homeassistant/config
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
HA_SVC_EOF

        systemctl daemon-reload
        systemctl enable homeassistant-compose 2>/dev/null || true

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

    inject_callhome_agent "${vmid}"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "homeassistant" "$output" "$_NEW_VERSION"
    log "Home Assistant LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

cleanup_gaming_build() { cleanup_lxc_build "${GAMING_BUILD_VMID}"; }

build_gaming_lxc() {
    init_build_version "gaming"
    log "Building Gaming LXC template (remote on Proxmox)..."
    local base_rootfs="${IMAGES_DIR}/${GAMING_BASE_ROOTFS}"
    local output="${IMAGES_DIR}/$(compute_filename gaming "$_NEW_VERSION")"
    local vmid="${GAMING_BUILD_VMID}"

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

    inject_callhome_agent "${vmid}"

    log "Cleaning up package caches..."
    remote_cmd "pct exec ${vmid} -- bash -c '
        dnf clean all
        rm -rf /var/cache/dnf /tmp/* /var/tmp/*
    '"

    remote_cmd "pct exec ${vmid} -- bash -c 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    finalize_build "gaming" "$output" "$_NEW_VERSION"
    log "Gaming LXC template: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# ── Desktop VM image ─────────────────────────────────────────────────
# Debian 12 cloud image with KDE Plasma, GNOME, SDDM, and shared apps
# pre-installed. Both Intel and AMD GPU driver stacks are baked in so
# the image works on any host — only the matching driver loads at runtime.
# The generic cloud image must already be downloaded to
# images/debian-12-generic-amd64.qcow2.

cleanup_desktop_build() {
    local vmid="${DESKTOP_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Desktop build VM ${vmid}..."
        remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 3; qm destroy ${vmid} --purge 2>/dev/null; true"
        remote_cmd "rm -f /var/tmp/desktop-*-debian-12-amd64.qcow2; true"
    fi
}

build_desktop_vm() {
    init_build_version "desktop"
    log "Building Desktop VM image (remote on Proxmox)..."
    local base_image="${IMAGES_DIR}/${DESKTOP_BASE_IMAGE}"
    local output="${IMAGES_DIR}/$(compute_filename desktop "$_NEW_VERSION")"
    local vmid="${DESKTOP_BUILD_VMID}"

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

echo "==> Installing GPU drivers (both Intel and AMD -- no conflict)..."
apt-get install -y --no-install-recommends \
    xserver-xorg-video-intel \
    intel-media-va-driver \
    xserver-xorg-video-amdgpu \
    mesa-va-drivers \
    mesa-vulkan-drivers \
    vainfo

echo "==> Configuring SDDM as default display manager..."
echo "/usr/bin/sddm" > /etc/X11/default-display-manager
dpkg-reconfigure -f noninteractive sddm

echo "==> Enabling services..."
systemctl enable qemu-guest-agent 2>/dev/null || true
systemctl set-default graphical.target
systemctl enable sddm 2>/dev/null || true

echo "==> Creating desktop user with correct groups..."
useradd -m -G video,render,audio,sudo -s /bin/bash desktop 2>/dev/null || \
    usermod -a -G video,render,audio,sudo desktop

echo "==> Running xdg-user-dirs-update..."
su - desktop -c "xdg-user-dirs-update" 2>/dev/null || true

echo "==> Baking KDE Plasma configuration..."
DHOME=/home/desktop
mkdir -p "$DHOME/.config/kglobalshortcutsrc.d" "$DHOME/.config/autostart" "$DHOME/.config/gtk-3.0" "$DHOME/.local/share/plasma/layout-templates"

cat > "$DHOME/.config/kdeglobals" << "KDE_EOF"
[General]
ColorScheme=BreezeDark

[KDE]
SingleClick=false
LookAndFeelPackage=org.kde.breezedark.desktop
KDE_EOF

cat > "$DHOME/.config/kglobalshortcutsrc" << "KDE_SC_EOF"
[kwin]
Overview=Meta+Tab
Window Close=Alt+F4
Window Maximize=Meta+Up
Window Minimize=Meta+Down
Window Quick Tile Left=Meta+Left
Window Quick Tile Right=Meta+Right
Switch Window Down=Alt+Tab

[plasmashell]
activate task manager entry 1=Meta+1
activate task manager entry 2=Meta+2
activate task manager entry 3=Meta+3
show-on-mouse-pos=Meta+V

[org.kde.spectacle.desktop]
ActiveWindowScreenShot=Alt+Print
CurrentMonitorScreenShot=Ctrl+Print
FullScreenScreenShot=Print
RectangularRegionScreenShot=Ctrl+Shift+4
_launch=Meta+Shift+S

[flameshot.desktop]
Capture=Ctrl+Shift+4
KDE_SC_EOF

cat > "$DHOME/.config/plasma-org.kde.plasma.desktop-appletsrc" << "KDE_PANEL_EOF"
[PlasmaViews][Panel 2]
alignment=0
floating=1
panelVisibility=1

[PlasmaViews][Panel 2][Defaults]
thickness=44

[Containments][2]
activityId=
formfactor=2
immutability=1
lastScreen=0
location=4
plugin=org.kde.panel
wallpaperplugin=org.kde.image
KDE_PANEL_EOF

echo "==> Baking GNOME configuration..."
mkdir -p /etc/dconf/db/local.d/locks /etc/dconf/profile

cat > /etc/dconf/profile/user << "DCONF_PROFILE_EOF"
user-db:user
system-db:local
DCONF_PROFILE_EOF

cat > /etc/dconf/db/local.d/00-desktop-defaults << "DCONF_DEFAULTS_EOF"
[org/gnome/desktop/interface]
color-scheme='prefer-dark'
gtk-theme='Adwaita-dark'
clock-format='12h'
enable-hot-corners=true

[org/gnome/shell]
enabled-extensions=['dash-to-dock@micxgx.gmail.com']
favorite-apps=['firefox-esr.desktop', 'org.gnome.Nautilus.desktop', 'org.gnome.Terminal.desktop', 'org.gnome.TextEditor.desktop']

[org/gnome/shell/extensions/dash-to-dock]
dock-position='BOTTOM'
dash-max-icon-size=48
extend-height=false
dock-fixed=true
click-action='minimize'
intellihide=true
transparency-mode='DYNAMIC'

[org/gnome/desktop/wm/keybindings]
close=['<Alt>F4', '<Super>q']
minimize=['<Super>h']
toggle-maximized=['<Super>Up']
begin-move=['<Super>m']
switch-applications=['<Alt>Tab']
switch-windows=['<Super>Tab']

[org/gnome/shell/keybindings]
toggle-overview=['<Super>space']

[org/gnome/settings-daemon/plugins/media-keys]
screenshot=['Print', '<Shift><Super>3']
screenshot-clip=['<Shift><Control>4']
area-screenshot-clip=['<Shift><Control>4']
window-screenshot=['<Shift><Super>5']

[org/gnome/desktop/peripherals/touchpad]
natural-scroll=true
tap-to-click=true

[org/gnome/desktop/input-sources]
xkb-options=['caps:super']

[org/gnome/mutter]
edge-tiling=true
dynamic-workspaces=true
DCONF_DEFAULTS_EOF

cat > /etc/dconf/db/local.d/locks/desktop-defaults << "DCONF_LOCKS_EOF"
[org/gnome/shell]
enabled-extensions='dash-to-dock@micxgx.gmail.com'

[org/gnome/desktop/interface]
color-scheme='prefer-dark'
DCONF_LOCKS_EOF

dconf update 2>/dev/null || true

echo "==> Baking shared desktop polish..."
cat > "$DHOME/.config/autostart/flameshot.desktop" << "FLAME_EOF"
[Desktop Entry]
Type=Application
Name=Flameshot
Exec=flameshot
Icon=flameshot
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
FLAME_EOF

cat > "$DHOME/.config/gtk-3.0/bookmarks" << "BOOKMARKS_EOF"
file:///home/desktop/Downloads Downloads
file:///home/desktop/Documents Documents
file:///home/desktop/Pictures Pictures
file:///home/desktop/Videos Videos
file:///home/desktop/Music Music
BOOKMARKS_EOF

mkdir -p /etc/sddm.conf.d
cat > /etc/sddm.conf.d/default.conf << "SDDM_EOF"
[Theme]
Current=breeze

[General]
DefaultSession=plasma.desktop
SDDM_EOF

chown -R desktop:desktop "$DHOME"

echo "==> Cleaning up..."
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

echo "==> Resetting cloud-init for next boot..."
cloud-init clean --logs

echo "==> Desktop image build complete."
INSTALL_EOF

    # Inject callhome Python agent into the VM (same agent as Debian LXC containers)
    local callhome_src="${SCRIPT_DIR}/callhome.py"
    if [[ -f "$callhome_src" ]]; then
        log "Injecting callhome agent into Desktop VM..."
        # shellcheck disable=SC2086
        scp $SSH_OPTS "$callhome_src" "root@${PROXMOX_HOST}:/tmp/callhome.py"
        remote_cmd "scp -o StrictHostKeyChecking=no /tmp/callhome.py root@${vm_ip}:/tmp/callhome.py"
        remote_cmd "rm -f /tmp/callhome.py"
        remote_cmd "ssh -o StrictHostKeyChecking=no root@${vm_ip} bash -s" << 'CALLHOME_EOF'
set -euo pipefail
mkdir -p /opt/callhome
mv /tmp/callhome.py /opt/callhome/callhome.py
chmod +x /opt/callhome/callhome.py

cat > /etc/default/callhome << "CONF_EOF"
# Populated by Ansible configure role at deploy time
CALLHOME_SERVER=
CALLHOME_PUBLIC_KEY=
CONF_EOF

cat > /etc/systemd/system/callhome.service << "UNIT_EOF"
[Unit]
Description=Call-home heartbeat agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/callhome/callhome.py --container --interval 60 --interval-startup 5
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl enable callhome.service 2>/dev/null || true
echo "Callhome agent installed."
CALLHOME_EOF
        log "Callhome agent injected into Desktop VM."
    else
        log "WARNING: callhome.py not found at ${callhome_src}, skipping agent injection"
    fi

    remote_cmd "ssh -o StrictHostKeyChecking=no root@${vm_ip} 'echo ${_NEW_VERSION} > /etc/image_version'"

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

    local output_name
    output_name="$(basename "$output")"
    remote_cmd "rm -f /var/tmp/${output_name}"
    remote_cmd "qemu-img convert -f raw -O qcow2 '${disk_path}' /var/tmp/${output_name}"

    mkdir -p "$IMAGES_DIR"
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:/var/tmp/${output_name}" "$output"

    # Cleanup
    log "Cleaning up build VM and temporary files..."
    remote_cmd "qm destroy ${vmid} --purge 2>/dev/null; true"
    remote_cmd "rm -f /var/tmp/${output_name} /var/tmp/${DESKTOP_BASE_IMAGE}; true"

    trap - EXIT

    finalize_build "desktop" "$output" "$_NEW_VERSION"
    log "Desktop VM image: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# Sunshine VM image (built remotely on Proxmox: ISO boot → unattended install → export)
SUNSHINE_ISO="Tiny11-2026-03-15.iso"
SUNSHINE_VIRTIO_ISO="virtio-win.iso"
SUNSHINE_BUILD_VMID=989
SUNSHINE_ANSWER_DIR="${SCRIPT_DIR}/../roles/gaming_vm/files"

_sunshine_answer_tmp=""
cleanup_sunshine_build() {
    [[ -n "$_sunshine_answer_tmp" ]] && rm -rf "$_sunshine_answer_tmp"
    local vmid="${SUNSHINE_BUILD_VMID}"
    if [[ -n "$PROXMOX_HOST" ]]; then
        log "Cleaning up Sunshine build VM ${vmid}..."
        remote_cmd "qm stop ${vmid} 2>/dev/null; sleep 3; qm destroy ${vmid} --purge 2>/dev/null; true"
        remote_cmd "rm -f /tmp/sunshine-answer.iso /tmp/${SUNSHINE_ISO} /tmp/${SUNSHINE_VIRTIO_ISO} /var/tmp/sunshine-*-win11-amd64.qcow2; true"
    fi
}

build_sunshine_vm() {
    init_build_version "sunshine"
    log "Building Sunshine VM image (remote on Proxmox)..."
    local win_iso="${IMAGES_DIR}/${SUNSHINE_ISO}"
    local virtio_iso="${IMAGES_DIR}/isos/${SUNSHINE_VIRTIO_ISO}"
    local output="${IMAGES_DIR}/$(compute_filename sunshine "$_NEW_VERSION")"
    local vmid="${SUNSHINE_BUILD_VMID}"

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
    _sunshine_answer_tmp=$(mktemp -d)
    local answer_tmp="$_sunshine_answer_tmp"
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

    local output_name
    output_name="$(basename "$output")"
    remote_cmd "rm -f /var/tmp/${output_name}"
    remote_cmd "qemu-img convert -f raw -O qcow2 '${disk_path}' /var/tmp/${output_name}"

    mkdir -p "$IMAGES_DIR"
    # shellcheck disable=SC2086
    scp $SSH_OPTS "root@${PROXMOX_HOST}:/var/tmp/${output_name}" "$output"

    # Cleanup
    log "Cleaning up build VM and temporary files..."
    remote_cmd "qm destroy ${vmid} --purge 2>/dev/null; true"
    remote_cmd "rm -f /var/tmp/${output_name} /tmp/sunshine-answer.iso; true"

    trap - EXIT

    finalize_build "sunshine" "$output" "$_NEW_VERSION"
    log "Sunshine VM image: ${output}"
    log "  Size: $(du -h "$output" | cut -f1)"
}

# ── Parallel build ────────────────────────────────────────────────────

parallel_build() {
    if [[ ${#PARALLEL_HOSTS[@]} -eq 0 ]]; then
        [[ -n "${PRIMARY_HOST:-}" ]] && PARALLEL_HOSTS+=("$PRIMARY_HOST")
        [[ -n "${AI_HOST:-}" ]] && PARALLEL_HOSTS+=("$AI_HOST")
        [[ -n "${MESH_2_HOST:-}" ]] && PARALLEL_HOSTS+=("$MESH_2_HOST")
    fi

    if [[ ${#PARALLEL_HOSTS[@]} -eq 0 ]]; then
        die "--parallel requires at least one host.
  Set PRIMARY_HOST, AI_HOST, MESH_2_HOST in your environment, or use --hosts <ip1>,<ip2>,..."
    fi

    local -a all_local=(mesh router)
    local -a all_remote=(pihole rsyslog jellyfin netdata wireguard homeassistant kodi kiosk moonlight gaming sunshine desktop)
    local -a local_targets=() remote_targets=()

    if [[ ${#BUILD_TARGETS[@]} -gt 0 ]]; then
        for t in "${BUILD_TARGETS[@]}"; do
            if printf '%s\n' "${all_local[@]}" | grep -qx "$t"; then
                local_targets+=("$t")
            elif printf '%s\n' "${all_remote[@]}" | grep -qx "$t"; then
                remote_targets+=("$t")
            fi
        done
    else
        local_targets=("${all_local[@]}")
        remote_targets=("${all_remote[@]}")
    fi

    # Distribute remote targets round-robin across hosts
    local num_hosts=${#PARALLEL_HOSTS[@]}
    local -a host_target_lists=()
    for ((i = 0; i < num_hosts; i++)); do
        host_target_lists[$i]=""
    done
    for ((i = 0; i < ${#remote_targets[@]}; i++)); do
        local idx=$((i % num_hosts))
        host_target_lists[$idx]+="${remote_targets[$i]} "
    done

    log "Parallel build plan:"
    if [[ ${#local_targets[@]} -gt 0 ]]; then
        log "  controller: ${local_targets[*]}"
    fi
    for ((i = 0; i < num_hosts; i++)); do
        local targets="${host_target_lists[$i]}"
        [[ -n "${targets// /}" ]] && log "  ${PARALLEL_HOSTS[$i]}: ${targets% }"
    done
    log ""

    local log_dir
    log_dir=$(mktemp -d "${TMPDIR:-/tmp}/build-images-parallel.XXXXXX")
    local -a pids=() labels=() log_files=()
    local -a propagate=()
    [[ "$CLEAN_MODE" == true ]] && propagate+=(--clean)

    # Launch local builds (mesh, router) in background
    if [[ ${#local_targets[@]} -gt 0 ]]; then
        local -a args=("${propagate[@]}")
        for t in "${local_targets[@]}"; do args+=(--only "$t"); done
        log "Starting local builds: ${local_targets[*]}"
        "$0" "${args[@]}" > "${log_dir}/controller.log" 2>&1 &
        pids+=($!)
        labels+=("controller(${local_targets[*]})")
        log_files+=("${log_dir}/controller.log")
    fi

    # Launch per-host remote builds in background
    for ((i = 0; i < num_hosts; i++)); do
        local targets="${host_target_lists[$i]}"
        [[ -z "${targets// /}" ]] && continue
        local host="${PARALLEL_HOSTS[$i]}"
        local -a args=("${propagate[@]}" --host "$host")
        for t in $targets; do args+=(--only "$t"); done
        log "Starting builds on ${host}: ${targets% }"
        "$0" "${args[@]}" > "${log_dir}/${host}.log" 2>&1 &
        pids+=($!)
        labels+=("${host}(${targets% })")
        log_files+=("${log_dir}/${host}.log")
    done

    log "Waiting for ${#pids[@]} parallel build jobs..."
    log ""

    local failed=0
    for ((i = 0; i < ${#pids[@]}; i++)); do
        if wait "${pids[$i]}"; then
            log "  DONE: ${labels[$i]}"
        else
            local rc=$?
            log "  FAILED: ${labels[$i]} (exit code ${rc})"
            failed=1
        fi
    done

    log ""
    if [[ $failed -ne 0 ]]; then
        log "Some builds failed. Error details:"
        for ((i = 0; i < ${#log_files[@]}; i++)); do
            local lf="${log_files[$i]}"
            [[ -f "$lf" ]] || continue
            if grep -q "^ERROR:" "$lf" 2>/dev/null; then
                log "--- ${labels[$i]} ---"
                grep "^ERROR:" "$lf" | sed 's/^/  /'
            fi
        done
        log ""
        log "Full logs in: ${log_dir}/"
        return 1
    fi

    rm -rf "$log_dir"
    log "All parallel builds completed successfully."
}

# ── Main ─────────────────────────────────────────────────────────────

BUILD_TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN_MODE=true
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
            [[ -n "${2:-}" ]] || die "--only requires a target (mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, kiosk, moonlight, gaming, sunshine, desktop)"
            BUILD_TARGETS+=("$2")
            shift 2
            ;;
        --parallel)
            PARALLEL_MODE=true
            shift
            ;;
        --hosts)
            [[ -n "${2:-}" ]] || die "--hosts requires comma-separated IPs"
            IFS=',' read -ra PARALLEL_HOSTS <<< "$2"
            PARALLEL_MODE=true
            shift 2
            ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--host <ip>] [--only <target>] [--clean] [--parallel] [--hosts <ip1>,<ip2>,...]
  Targets: mesh, router, pihole, rsyslog, jellyfin, netdata, wireguard, homeassistant, kodi, kiosk, moonlight, gaming, sunshine, desktop"
            ;;
    esac
done

VALID_TARGETS=(mesh router pihole rsyslog jellyfin netdata wireguard homeassistant kodi kiosk moonlight gaming sunshine desktop)

if [[ ${#BUILD_TARGETS[@]} -gt 0 ]]; then
    for t in "${BUILD_TARGETS[@]}"; do
        if ! printf '%s\n' "${VALID_TARGETS[@]}" | grep -qx "$t"; then
            die "Unknown build target: '$t'
Valid targets: ${VALID_TARGETS[*]}
Hint: use 'router' (not 'openwrt') for the OpenWrt router VM image."
        fi
    done
fi

should_build() {
    [[ ${#BUILD_TARGETS[@]} -eq 0 ]] && return 0
    local target
    for target in "${BUILD_TARGETS[@]}"; do
        [[ "$target" == "$1" ]] && return 0
    done
    return 1
}

check_deps

# ── Parallel dispatch ────────────────────────────────────────────────
if [[ "$PARALLEL_MODE" == true ]]; then
    parallel_build
    exit $?
fi

# ── Sequential builds (single host) ─────────────────────────────────
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
should_build kiosk        && build_kiosk_lxc
should_build moonlight    && build_moonlight_lxc
should_build gaming       && build_gaming_lxc
should_build sunshine     && build_sunshine_vm
should_build desktop      && build_desktop_vm

log ""
log "Done. Custom images in ${IMAGES_DIR}/:"
ls -lh "${IMAGES_DIR}"/*.tar.gz "${IMAGES_DIR}"/*.tar.zst "${IMAGES_DIR}"/*.img.gz "${IMAGES_DIR}"/*.qcow2 \
    2>/dev/null || true

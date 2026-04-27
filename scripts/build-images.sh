#!/usr/bin/env bash
set -euo pipefail

# Builds custom images for the vm_builds project.
#
# Architecture:
#   - OpenWrt images (mesh, router) are built LOCALLY using the OpenWrt Image Builder.
#     These establish the VPN baseline — no remote API needed.
#   - All Debian/Fedora LXC images are built REMOTELY via NodeManager HTTP API.
#     The controller reads a recipe YAML, POSTs it to the NodeManager, which
#     orchestrates the build on the Proxmox host using PVE API + callhome agent.
#
# Usage: ./build-images.sh [--clean] [--force] [--host <proxmox-ip>] [--only <target>]
#                          [--parallel] [--hosts <ip1>,<ip2>,...]
#                          [--nm-port <port>]
#
# Versioning: Each service has images/<service>.version and images/<service>.recipe_hash.
# When a recipe changes, the next build auto-bumps the version. Use --force to rebuild
# even when the recipe hash hasn't changed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGES_DIR="${PROJECT_ROOT}/images"
BUILD_DIR="${SCRIPT_DIR}/.image-builder-cache"

OPENWRT_VERSION="24.10.0"
TARGET="x86"
SUBTARGET="64"
IB_NAME="openwrt-imagebuilder-${OPENWRT_VERSION}-${TARGET}-${SUBTARGET}.Linux-x86_64"
IB_ARCHIVE="${IB_NAME}.tar.zst"
IB_URL="https://downloads.openwrt.org/releases/${OPENWRT_VERSION}/targets/${TARGET}/${SUBTARGET}/${IB_ARCHIVE}"

MESH_FILES_DIR="${SCRIPT_DIR}/image-builder/files-mesh-lxc"
ROUTER_FILES_DIR="${SCRIPT_DIR}/image-builder/files-router-vm"
SHARED_SCRIPTS_DIR="${SCRIPT_DIR}/image-builder/shared-scripts"

PROXMOX_HOST=""
NM_PORT="${NM_PORT:-9001}"
NM_AUTH_TOKEN="${CALLHOME_PUBLIC_KEY:-}"
PARALLEL_MODE=false
PARALLEL_HOSTS=()
CLEAN_MODE=false
FORCE_BUILD=false

# ── Package lists (OpenWrt only) ────────────────────────────────────

MESH_PACKAGES=(
    wpad-mesh-openssl iw
    kmod-iwlwifi iwlwifi-firmware-iwl8265
    kmod-mt76 kmod-ath9k kmod-ath10k-ct ath10k-firmware-qca988x-ct
    kmod-batman-adv batctl-tiny openssl-util uhttpd
    -wpad-basic-openssl -wpad-basic-wolfssl -wpad-basic-mbedtls
    -wpad-basic -wpad-mini
    -firewall4 -nftables -odhcpd-ipv6only -dnsmasq -ppp -ppp-mod-pppoe
)

ROUTER_PACKAGES=(
    wpad-mesh-openssl kmod-iwlwifi iwlwifi-firmware-iwl8265
    curl ip-full tcpdump https-dns-proxy banip dawn
    kmod-batman-adv batctl-tiny openssl-util
    luci luci-ssl uhttpd
    -wpad-basic-openssl -wpad-basic-wolfssl -wpad-basic-mbedtls
    -wpad-basic -wpad-mini
)

DEBIAN_BASE_TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
FEDORA_BASE_TEMPLATE="fedora-41-default-amd64.tar.xz"
BUILD_BASE_DEBIAN="build-base-debian-12-amd64.tar.zst"
BUILD_BASE_FEDORA="build-base-fedora-41-amd64.tar.zst"
BUILD_BASE_VMID=997

# ── Remote-buildable services (recipes in roles/{service}_lxc/recipe.yml)
REMOTE_SERVICES=(pihole rsyslog jellyfin netdata wireguard homeassistant kodi kiosk moonlight gaming desktop)

# ── Functions ───────────────────────────────────────────────────────

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

sync_shared_scripts() {
    local target_dir="$1"
    if [[ -d "$SHARED_SCRIPTS_DIR" ]]; then
        for src in "$SHARED_SCRIPTS_DIR"/*; do
            [[ -f "$src" ]] || continue
            cp -f "$src" "$target_dir/usr/sbin/$(basename "$src")"
            chmod +x "$target_dir/usr/sbin/$(basename "$src")"
        done
    fi
}

check_deps() {
    for cmd in make wget tar zstd python3 curl; do
        command -v "$cmd" &>/dev/null || die "Missing dependency: $cmd"
    done
}

compute_filename() {
    local target="$1" version="$2"
    case "$target" in
        mesh)          echo "mesh-${version}-openwrt-${OPENWRT_VERSION}-x86-64-rootfs.tar.gz" ;;
        router)        echo "router-${version}-openwrt-${OPENWRT_VERSION}-x86-64-combined-ext4.img.gz" ;;
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
        desktop)       echo "desktop-${version}-debian-12-amd64.tar.zst" ;;
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
        *)     die "Invalid bump level: $level" ;;
    esac
}

recipe_hash() {
    local target="$1"
    local recipe="${PROJECT_ROOT}/roles/${target}_lxc/recipe.yml"
    if [[ -f "$recipe" ]]; then
        local hash_input
        hash_input="$(sha256sum "$recipe")"
        if grep -q 'baked_webui:' "$recipe" 2>/dev/null; then
            hash_input+="$(find "${PROJECT_ROOT}/scripts/webui" -name '*.py' -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null)"
        fi
        echo "$hash_input" | sha256sum | cut -d' ' -f1
    else
        echo "no-recipe"
    fi
}

recipe_changed() {
    local target="$1"
    local current_hash stored_hash
    current_hash=$(recipe_hash "$target")
    stored_hash=$(cat "${IMAGES_DIR}/${target}.recipe_hash" 2>/dev/null || echo "")
    [[ "$current_hash" != "$stored_hash" ]]
}

init_build_version() {
    local target="$1"
    _CUR_VERSION=$(cat "${IMAGES_DIR}/${target}.version" 2>/dev/null || echo "0.0.0")
    _NEW_VERSION=$(bump_version "$_CUR_VERSION" "patch")
}

finalize_build() {
    local target="$1" output="$2" version="$3"
    echo "$version" > "${IMAGES_DIR}/${target}.version"
    recipe_hash "$target" > "${IMAGES_DIR}/${target}.recipe_hash"
    log "Build complete: $(basename "$output") v${version}"
    log "  Recipe hash: $(cat "${IMAGES_DIR}/${target}.recipe_hash")"
}

# ── OpenWrt Image Builder (local) ──────────────────────────────────

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
        PROFILE="generic" PACKAGES="$pkg_list" \
        FILES="$MESH_FILES_DIR" EXTRA_IMAGE_NAME="mesh-lxc" \
        2>&1 | tee "$make_log" | tail -5
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        die "Mesh LXC image build failed. Full log: ${make_log}"
    fi
    local rootfs
    rootfs=$(find "${ib_dir}/bin" -name '*mesh-lxc*rootfs.tar.gz' -print -quit 2>/dev/null)
    [[ -z "$rootfs" ]] && die "Mesh LXC rootfs not found in Image Builder output"
    mkdir -p "$IMAGES_DIR"
    cp "$rootfs" "$output"
    finalize_build "mesh" "$output" "$_NEW_VERSION"
    log "Mesh LXC rootfs: ${output} ($(du -h "$output" | cut -f1))"
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
    make -C "$ib_dir" clean 2>/dev/null || true
    local make_log="${BUILD_DIR}/router-build.log"
    make -C "$ib_dir" image \
        PROFILE="generic" PACKAGES="$pkg_list" \
        FILES="$ROUTER_FILES_DIR" EXTRA_IMAGE_NAME="router" \
        2>&1 | tee "$make_log" | tail -5
    if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        die "Router VM image build failed. Full log: ${make_log}"
    fi
    local combined
    combined=$(find "${ib_dir}/bin" -name '*combined-ext4.img.gz' -print -quit 2>/dev/null)
    [[ -z "$combined" ]] && combined=$(find "${ib_dir}/bin" -name '*combined*.img.gz' -print -quit 2>/dev/null)
    [[ -z "$combined" ]] && die "Router VM image not found in Image Builder output"
    mkdir -p "$IMAGES_DIR"
    cp "$combined" "$output"
    finalize_build "router" "$output" "$_NEW_VERSION"
    log "Router VM image: ${output} ($(du -h "$output" | cut -f1))"
}

# ── Host preparation (ensures hardware stability) ─────────────────
#
# Applies host-level infrastructure fixes that must survive reboots.
# Runs automatically before any build operation on a remote host.
# Idempotent — skips if already applied and active in the running kernel.

prepare_host() {
    [[ -z "$PROXMOX_HOST" ]] && return 0
    local ssh="ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@${PROXMOX_HOST}"

    log "Checking host readiness on ${PROXMOX_HOST}..."

    local cmdline
    cmdline=$($ssh "cat /proc/cmdline")

    if echo "$cmdline" | grep -q 'usbcore.autosuspend=-1'; then
        log "  USB autosuspend already disabled in kernel cmdline — OK"
    else
        log "  USB autosuspend NOT in kernel cmdline — applying fix..."

        local grub_has_fix
        grub_has_fix=$($ssh "grep -c 'usbcore.autosuspend=-1' /etc/default/grub || echo 0")

        if [[ "$grub_has_fix" -eq 0 ]]; then
            log "  Adding usbcore.autosuspend=-1 to GRUB..."
            $ssh "bash -c '
                current=\$(grep \"^GRUB_CMDLINE_LINUX_DEFAULT=\" /etc/default/grub | sed \"s/^GRUB_CMDLINE_LINUX_DEFAULT=\\\"//;s/\\\"$//\")
                sed -i \"s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\\\"\${current} usbcore.autosuspend=-1\\\"|\" /etc/default/grub
                update-grub
            '"
        fi

        log "  Rebooting ${PROXMOX_HOST} to activate kernel parameter..."
        $ssh "reboot" || true
        sleep 10

        local attempt
        for attempt in $(seq 1 60); do
            if $ssh "cat /proc/cmdline" 2>/dev/null | grep -q 'usbcore.autosuspend=-1'; then
                log "  Host rebooted — USB autosuspend disabled in kernel cmdline"
                break
            fi
            [[ $attempt -eq 60 ]] && die "Host ${PROXMOX_HOST} did not come back after reboot within 5 minutes"
            sleep 5
        done
    fi

    local udev_rule="/etc/udev/rules.d/50-usb-realtek-no-autosuspend.rules"
    $ssh "test -f ${udev_rule}" 2>/dev/null || {
        log "  Deploying USB ethernet udev rule..."
        $ssh "cat > ${udev_rule} << 'UDEV'
# Disable USB autosuspend for Realtek RTL8153/8156 USB ethernet adapters.
ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"0bda\", ATTR{idProduct}==\"8153\", TEST==\"power/control\", ATTR{power/control}=\"on\"
ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"0bda\", ATTR{idProduct}==\"8156\", TEST==\"power/control\", ATTR{power/control}=\"on\"
UDEV
        udevadm trigger --subsystem-match=usb"
    }

    $ssh "bash -c '
        for dev in /sys/bus/usb/devices/*/; do
            if [ -f \"\${dev}idVendor\" ] && [ -f \"\${dev}power/control\" ]; then
                vendor=\$(cat \"\${dev}idVendor\" 2>/dev/null)
                if [ \"\$vendor\" = \"0bda\" ]; then
                    echo on > \"\${dev}power/control\"
                fi
            fi
        done
    '" 2>/dev/null

    # Ensure MASQUERADE for the container NAT bridge (if it exists).
    # Without this, build containers on NAT bridges can't reach the internet.
    local has_vmbr_ct
    has_vmbr_ct=$($ssh "ip -4 addr show vmbr_ct 2>/dev/null | grep -oP 'inet \K[0-9.]+' || echo ''")
    if [[ -n "$has_vmbr_ct" ]]; then
        local ct_subnet="${has_vmbr_ct%.*}.0/24"
        log "  NAT bridge vmbr_ct detected (${has_vmbr_ct}), ensuring MASQUERADE for ${ct_subnet}..."
        $ssh "iptables -t nat -C POSTROUTING -s ${ct_subnet} -o vmbr0 -j MASQUERADE 2>/dev/null \
              || iptables -t nat -A POSTROUTING -s ${ct_subnet} -o vmbr0 -j MASQUERADE"
        log "  MASQUERADE rule active."
    fi

    log "  Host ${PROXMOX_HOST} ready."
}

# ── Build base templates (SSH baseline — runs ONCE per distro) ─────
#
# Creates base templates with callhome agent pre-installed.
# This is the BASELINE step — uses SSH because VPN/API aren't established yet.
# After these templates exist, ALL subsequent builds use HTTP only.

_build_base_common() {
    local distro="$1" stock_template="$2" output_name="$3" pkg_cmd="$4" clean_cmd="$5"
    local extra_create_args="${6:-}"
    [[ -z "$PROXMOX_HOST" ]] && die "build-base requires --host <proxmox-ip>"

    local ssh="ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@${PROXMOX_HOST}"
    local vmid="$BUILD_BASE_VMID"
    local template_dir="/var/lib/vz/template/cache"

    log "Building ${distro} base template with callhome agent (VMID ${vmid})..."

    $ssh "test -f ${template_dir}/${stock_template}" || \
        die "Stock template not found: ${template_dir}/${stock_template}"

    $ssh "pct stop ${vmid} 2>/dev/null; pct destroy ${vmid} --purge 2>/dev/null; true"

    log "Creating build container ${vmid}..."
        # shellcheck disable=SC2086
    $ssh "pct create ${vmid} ${template_dir}/${stock_template} \
        --hostname build-base \
        --memory 512 --cores 1 \
        --rootfs local-lvm:2 \
        --net0 name=eth0,bridge=vmbr0,ip=dhcp \
        --nameserver 8.8.8.8 \
        --unprivileged 1 \
        --features nesting=1 \
        ${extra_create_args} \
        --start 1"

    log "Waiting for container to start..."
    local attempt
    for attempt in $(seq 1 30); do
        $ssh "pct exec ${vmid} -- test -f /etc/os-release" 2>/dev/null && break
        sleep 2
    done
    $ssh "pct exec ${vmid} -- test -f /etc/os-release" || die "Container ${vmid} failed to start"

    log "Waiting for network..."
    for attempt in $(seq 1 15); do
        $ssh "pct exec ${vmid} -- getent hosts 8.8.8.8" 2>/dev/null && break
        sleep 2
    done

    log "Installing python3..."
    $ssh "pct exec ${vmid} -- bash -c '${pkg_cmd}'"

    log "Injecting callhome agent..."
    scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        "${SCRIPT_DIR}/callhome.py" "root@${PROXMOX_HOST}:/tmp/callhome.py"
    $ssh "pct push ${vmid} /tmp/callhome.py /usr/local/bin/callhome.py && rm -f /tmp/callhome.py"
    $ssh "pct exec ${vmid} -- chmod +x /usr/local/bin/callhome.py"

    $ssh "pct exec ${vmid} -- bash -c 'mkdir -p /etc/default && cat > /etc/default/callhome << EOF
CALLHOME_SERVER=
CALLHOME_PUBLIC_KEY=
CALLHOME_INTERVAL=60
CALLHOME_MODE=container
EOF'"

    $ssh "pct exec ${vmid} -- bash -c 'cat > /etc/systemd/system/callhome.service << EOF
[Unit]
Description=Callhome Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/default/callhome
ExecStart=/usr/bin/python3 /usr/local/bin/callhome.py --container
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF'"
    $ssh "pct exec ${vmid} -- systemctl daemon-reload"
    $ssh "pct exec ${vmid} -- systemctl enable callhome.service"
    $ssh "pct exec ${vmid} -- python3 -c 'import json, urllib.request; print(\"callhome deps OK\")'"
    $ssh "pct exec ${vmid} -- bash -c '${clean_cmd}'"

    log "Stopping container and creating template..."
    $ssh "pct stop ${vmid}"
    sleep 2
    $ssh "vzdump ${vmid} --compress zstd --mode stop --dumpdir ${template_dir}"

    local archive
    archive=$($ssh "ls -t ${template_dir}/vzdump-lxc-${vmid}-*.tar.zst 2>/dev/null | head -1")
    [[ -z "$archive" ]] && die "vzdump archive not found for VMID ${vmid}"
    $ssh "mv '${archive}' '${template_dir}/${output_name}'"
    log "Base template: ${template_dir}/${output_name}"

    $ssh "pct destroy ${vmid} --purge 2>/dev/null || true"

    mkdir -p "$IMAGES_DIR"
    scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        "root@${PROXMOX_HOST}:${template_dir}/${output_name}" \
        "${IMAGES_DIR}/${output_name}"

    log "${distro} base template: ${IMAGES_DIR}/${output_name}"
    log "  Includes: python3, callhome agent, systemd service"
    log "  This is the BASELINE. All subsequent builds use HTTP only."
}

build_base() {
    _build_base_common "debian" \
        "$DEBIAN_BASE_TEMPLATE" \
        "$BUILD_BASE_DEBIAN" \
        "apt-get update -qq && apt-get install -y --no-install-recommends python3 ca-certificates" \
        "apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*"
}

build_base_fedora() {
    _build_base_common "fedora" \
        "$FEDORA_BASE_TEMPLATE" \
        "$BUILD_BASE_FEDORA" \
        "dnf install -y --skip-unavailable python3 ca-certificates" \
        "dnf clean all && rm -rf /var/cache/dnf /tmp/* /var/tmp/*" \
        "--ostype unmanaged"
}

# ── HTTP dispatch to NodeManager ───────────────────────────────────

build_remote_service() {
    local service="$1"
    local host="$2"
    local force="${3:-false}"
    local recipe_file="${PROJECT_ROOT}/roles/${service}_lxc/recipe.yml"

    if [[ ! -f "$recipe_file" ]]; then
        die "Recipe not found: ${recipe_file}"
    fi
    if [[ -z "$NM_AUTH_TOKEN" ]]; then
        die "CALLHOME_PUBLIC_KEY env var required for authenticated builds"
    fi

    if [[ "$force" != "true" ]] && ! recipe_changed "$service"; then
        log "Skipping ${service}: recipe unchanged (hash matches $(cat "${IMAGES_DIR}/${service}.version" 2>/dev/null || echo '?'))"
        return 0
    fi

    local nm_url="http://${host}:${NM_PORT}"
    log "Building ${service} on ${host} via NodeManager API..."
    log "  Recipe: ${recipe_file}"
    log "  Endpoint: POST ${nm_url}/api/build/${service}"

    local response http_code
    response=$(curl -sS --connect-timeout 30 --max-time 1800 \
        -X POST "${nm_url}/api/build/${service}" \
        -H "Content-Type: application/x-yaml" \
        -H "x-callhome-token: ${NM_AUTH_TOKEN}" \
        --data-binary "@${recipe_file}" \
        -w "\n%{http_code}" 2>&1) || {
        die "Failed to connect to NodeManager at ${nm_url}"
    }

    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        log "Build failed (HTTP ${http_code}):"
        echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
        return 1
    fi

    local success template_path elapsed_s
    success=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',''))" 2>/dev/null || echo "")
    template_path=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('template_path',''))" 2>/dev/null || echo "")
    elapsed_s=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('elapsed_seconds',''))" 2>/dev/null || echo "?")

    if [[ "$success" != "True" ]]; then
        log "Build reported failure:"
        echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
        return 1
    fi

    log "Build succeeded on ${host} in ${elapsed_s}s"
    log "  Remote template: ${template_path}"

    # Download the built template via SCP from the host filesystem
    init_build_version "$service"
    local output="${IMAGES_DIR}/$(compute_filename "$service" "$_NEW_VERSION")"
    mkdir -p "$IMAGES_DIR"

    log "Downloading template to ${output}..."
    scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        "root@${host}:${template_path}" "${output}" || {
        die "Failed to download template from ${host}:${template_path}"
    }

    finalize_build "$service" "$output" "$_NEW_VERSION"
    log "${service} template: ${output} ($(du -h "$output" | cut -f1))"
}

# ── Parallel build ─────────────────────────────────────────────────

parallel_build() {
    if [[ ${#PARALLEL_HOSTS[@]} -eq 0 ]]; then
        [[ -n "${PRIMARY_HOST:-}" ]] && PARALLEL_HOSTS+=("$PRIMARY_HOST")
        [[ -n "${AI_HOST:-}" ]] && PARALLEL_HOSTS+=("$AI_HOST")
        [[ -n "${MESH_2_HOST:-}" ]] && PARALLEL_HOSTS+=("$MESH_2_HOST")
    fi
    [[ ${#PARALLEL_HOSTS[@]} -eq 0 ]] && die "--parallel requires at least one host."

    local -a local_targets=() remote_targets=()
    if [[ ${#BUILD_TARGETS[@]} -gt 0 ]]; then
        for t in "${BUILD_TARGETS[@]}"; do
            if [[ "$t" == "mesh" || "$t" == "router" ]]; then
                local_targets+=("$t")
            elif printf '%s\n' "${REMOTE_SERVICES[@]}" | grep -qx "$t"; then
                remote_targets+=("$t")
            fi
        done
    else
        local_targets=(mesh router)
        remote_targets=("${REMOTE_SERVICES[@]}")
    fi

    local num_hosts=${#PARALLEL_HOSTS[@]}
    local -a host_target_lists=()
    for ((i = 0; i < num_hosts; i++)); do host_target_lists[$i]=""; done
    for ((i = 0; i < ${#remote_targets[@]}; i++)); do
        local idx=$((i % num_hosts))
        host_target_lists[$idx]+="${remote_targets[$i]} "
    done

    log "Parallel build plan:"
    [[ ${#local_targets[@]} -gt 0 ]] && log "  controller: ${local_targets[*]}"
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
    [[ "${FORCE_BUILD:-false}" == true ]] && propagate+=(--force)

    if [[ ${#local_targets[@]} -gt 0 ]]; then
        local -a args=("${propagate[@]}")
        for t in "${local_targets[@]}"; do args+=(--only "$t"); done
        log "Starting local builds: ${local_targets[*]}"
        "$0" "${args[@]}" > "${log_dir}/controller.log" 2>&1 &
        pids+=($!); labels+=("controller(${local_targets[*]})"); log_files+=("${log_dir}/controller.log")
    fi

    for ((i = 0; i < num_hosts; i++)); do
        local targets="${host_target_lists[$i]}"
        [[ -z "${targets// /}" ]] && continue
        local host="${PARALLEL_HOSTS[$i]}"
        local -a args=("${propagate[@]}" --host "$host")
        for t in $targets; do args+=(--only "$t"); done
        log "Starting builds on ${host}: ${targets% }"
        "$0" "${args[@]}" > "${log_dir}/${host}.log" 2>&1 &
        pids+=($!); labels+=("${host}(${targets% })"); log_files+=("${log_dir}/${host}.log")
    done

    log "Waiting for ${#pids[@]} parallel build jobs..."
    local failed=0
    for ((i = 0; i < ${#pids[@]}; i++)); do
        if wait "${pids[$i]}"; then
            log "  DONE: ${labels[$i]}"
        else
            log "  FAILED: ${labels[$i]} (exit code $?)"
            failed=1
        fi
    done

    if [[ $failed -ne 0 ]]; then
        log "Some builds failed. Logs in: ${log_dir}/"
        for ((i = 0; i < ${#log_files[@]}; i++)); do
            [[ -f "${log_files[$i]}" ]] || continue
            if grep -q "^ERROR:" "${log_files[$i]}" 2>/dev/null; then
                log "--- ${labels[$i]} ---"
                grep "^ERROR:" "${log_files[$i]}" | sed 's/^/  /'
            fi
        done
        return 1
    fi
    rm -rf "$log_dir"
    log "All parallel builds completed successfully."
}

# ── Main ───────────────────────────────────────────────────────────

BUILD_TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)     CLEAN_MODE=true; rm -rf "$BUILD_DIR"; shift ;;
        --force)     FORCE_BUILD=true; shift ;;
        --host)      [[ -n "${2:-}" ]] || die "--host requires an IP"; PROXMOX_HOST="$2"; shift 2 ;;
        --only)      [[ -n "${2:-}" ]] || die "--only requires a target"; BUILD_TARGETS+=("$2"); shift 2 ;;
        --parallel)  PARALLEL_MODE=true; shift ;;
        --nm-port)   [[ -n "${2:-}" ]] || die "--nm-port requires a port"; NM_PORT="$2"; shift 2 ;;
        --hosts)
            [[ -n "${2:-}" ]] || die "--hosts requires comma-separated IPs"
            IFS=',' read -ra PARALLEL_HOSTS <<< "$2"; PARALLEL_MODE=true; shift 2 ;;
        *) die "Unknown argument: $1
Usage: $0 [--host <ip>] [--only <target>] [--clean] [--force] [--parallel] [--hosts <ips>] [--nm-port <port>]
  --force: rebuild even if recipe hash unchanged
  Baseline:       build-base, build-base-fedora (SSH — runs ONCE per distro)
  Local targets:  mesh, router
  Remote targets: ${REMOTE_SERVICES[*]}" ;;
    esac
done

VALID_TARGETS=(build-base build-base-fedora mesh router "${REMOTE_SERVICES[@]}")

if [[ ${#BUILD_TARGETS[@]} -gt 0 ]]; then
    for t in "${BUILD_TARGETS[@]}"; do
        printf '%s\n' "${VALID_TARGETS[@]}" | grep -qx "$t" || \
            die "Unknown target: '$t'. Valid: ${VALID_TARGETS[*]}"
    done
fi

should_build() {
    [[ ${#BUILD_TARGETS[@]} -eq 0 ]] && return 0
    for target in "${BUILD_TARGETS[@]}"; do [[ "$target" == "$1" ]] && return 0; done
    return 1
}

check_deps

if [[ "$PARALLEL_MODE" == true ]]; then
    parallel_build
    exit $?
fi

# ── Sequential builds ──────────────────────────────────────────────

# Host preparation (USB stability, udev rules) — idempotent, reboots if needed
prepare_host

# Baseline: build-base templates (SSH — one-time foundation)
should_build build-base         && build_base
should_build build-base-fedora  && build_base_fedora

# Local OpenWrt builds (no --host needed)
if should_build mesh || should_build router; then
    download_imagebuilder
fi
should_build mesh   && build_mesh_lxc
should_build router && build_router_vm

# Remote builds via NodeManager HTTP API (requires --host)
for svc in "${REMOTE_SERVICES[@]}"; do
    if should_build "$svc"; then
        [[ -z "$PROXMOX_HOST" ]] && die "${svc} build requires --host <proxmox-ip>"
        build_remote_service "$svc" "$PROXMOX_HOST" "${FORCE_BUILD:-false}"
    fi
done

log ""
log "Done. Custom images in ${IMAGES_DIR}/:"
ls -lh "${IMAGES_DIR}"/*.tar.gz "${IMAGES_DIR}"/*.tar.zst "${IMAGES_DIR}"/*.img.gz \
    2>/dev/null || true

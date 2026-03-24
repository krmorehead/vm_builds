---
name: image-management-patterns
description: Image management and local storage patterns for VM and LXC images. Use when managing VM images, handling custom image builds, or planning image storage strategies.
---

# Image Management Rules

## Local Image Storage Requirement

1. ALL VM images and LXC templates are stored locally in the `images/` directory (gitignored) and uploaded to the Proxmox host during provisioning. This is a deliberate design decision, not a convenience shortcut.

## Why Local Images

2. **No internet dependency on Proxmox host:** The host may not have internet access (e.g., after cleanup destroys the router VM). Enterprise repos may be unreachable without a paid subscription.

3. **Reproducibility:** Pinning exact image versions locally ensures every build uses the same image. Remote repos can remove or rename versions.

4. **Speed:** Local uploads are faster than downloading from the internet, especially on slow WAN links.

5. **Future self-hosting:** Images can be served from a local NAS or HTTP server for multi-node deployments.

## Directory Layout

6. ```
   images/                                              (gitignored)
   ├── openwrt-router-24.10.0-x86-64-combined.img.gz   Custom router VM (build-images.sh)
   ├── openwrt-mesh-lxc-24.10.0-x86-64-rootfs.tar.gz   Custom mesh LXC (build-images.sh)
   ├── debian-12-standard_12.12-1_amd64.tar.zst         LXC template
   └── ...future images...
   ```

7. NEVER commit images to git. The `images/` directory is listed in `.gitignore`. Document the expected image filename and download URL in role defaults and in `docs/architecture/`.

## Custom Images via Image Builder

8. `build-images.sh` uses the OpenWrt Image Builder to create pre-configured images with packages pre-installed and UCI defaults baked in. This eliminates runtime `opkg install` and resolves firewall/networking conflicts in LXC containers.

9. Per the project's "Bake, don't configure at runtime" principle, custom images are REQUIRED. Provision roles verify the image exists and hard-fail with an actionable message if missing:

   ```yaml
   - name: Fail if image is missing
     ansible.builtin.fail:
       msg: "Image not found: {{ image_path }}. Run ./build-images.sh to build it."
     when: not (image_stat.stat.exists | default(false))
   ```

## Upload Pattern for VMs

10. ```yaml
    - name: Upload image to Proxmox
      ansible.builtin.copy:
        src: "{{ openwrt_image_path }}"
        dest: "/tmp/openwrt-upload"
        mode: "0644"
      when: not vm_exists | bool

    - name: Decompress gzip image
      ansible.builtin.command:
        cmd: gunzip -f /tmp/openwrt-upload
      when:
        - not vm_exists | bool
        - openwrt_image_path.endswith('.gz')
    ```

## Adding a New Image

11. Process for adding a new image:
    1. Download the image to `images/` (use `wget`, browser, or `pveam` locally)
    2. Add the path variable to `group_vars/all.yml`
    3. Reference the variable in the provision role's defaults and tasks
    4. Document the download URL and expected checksum in the role README or architecture doc
    5. Add the filename to `Before running tests` in the ansible-testing skill

## VA-API Driver Portability

12. Image builds for services that use the iGPU for hardware acceleration (Jellyfin, Kodi, Moonlight) SHOULD include BOTH Intel and AMD VA-API driver packages. At runtime, only the matching driver loads.

13. Intel: `intel-media-va-driver` + `vainfo`  
    AMD: `mesa-va-drivers` + `vainfo`

14. This avoids rebuilding images when a container is moved to different hardware. The configure role reads `igpu_vendor` to set `LIBVA_DRIVER_NAME` appropriately.

## From-Source Compilation in Image Builds

19. When upstream packages don't provide x86-64 Debian binaries (e.g., moonlight-embedded only has armhf packages for Raspberry Pi), compile from source inside a build container. Pattern:
    1. Install runtime deps FIRST (`apt-get install`). Apt marks these as manually installed.
    2. Install build-only deps (cmake, gcc, -dev packages) in a SECOND `apt-get install`.
    3. Clone, compile, `make install`.
    4. `apt-get purge` build-only packages, then `apt-get autoremove`. Runtime deps survive because they're marked manual.

20. ALWAYS include runtime versions of FFmpeg / SDL2 / etc. in step 1. If `libavcodec59` is only pulled in as a transitive dependency of `libavcodec-dev`, autoremove deletes it after purging the -dev package.
    - Previous bug: `libavcodec59` and `libavutil57` were autoremoved because they were only transitive deps of the `-dev` packages. Fix: add them to the first (runtime) `apt-get install` block.

21. For LXC containers without X11, pass `-DENABLE_X11=OFF` (or equivalent cmake/configure flag) when compiling applications that optionally link X11/EGL/GLES. Without this, the binary links against libraries that get autoremoved.
    - Previous bug: moonlight-embedded linked `libEGL.so.1` at compile time. After autoremove, the binary failed to load. Fix: `cmake -DENABLE_X11=OFF` eliminates the dependency entirely.

22. Verification in build scripts MUST use full paths for binaries installed to `/usr/local/bin/`. `pct exec` uses a restricted PATH (`/sbin:/bin:/usr/sbin:/usr/bin`) that excludes `/usr/local/bin/`.
    - Previous bug: `which moonlight` via `pct exec` failed because `/usr/local/bin/` isn't in PATH. Fix: `test -x /usr/local/bin/moonlight`.

23. Allocate sufficient build container resources for compilation: 4GB disk (2GB too small for build deps + FFmpeg), 1024MB RAM, 2 cores.

## Windows VM Images

15. Windows images are built via unattended install on the Proxmox host: create temp VM, boot Tiny11 ISO + virtio-win ISO + answer ISO, wait for Guest Agent + post-install marker, export disk via `qemu-img convert` to qcow2. Build VMID 992 is reserved for this.

16. Windows qcow2 images are 8-18 GB. Upload to `/var/tmp/` (real disk), NOT `/tmp/` (tmpfs, typically ~7.8 GB). Same applies to `qemu-img convert` output during the build.

17. EFI disks on LVM-thin storage MUST use `format=raw`. The `qcow2` format is unsupported and causes build failures.

18. Post-install validation: the build script MUST hard-fail if the post-install marker file is missing after timeout. Proceeding without it produces an incomplete image (no SSH server, no Guest Agent, no software) that fails silently during deployment.
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
---
name: windows-vm-patterns
description: Windows 11 VM provisioning on Proxmox with iGPU passthrough, QEMU Guest Agent, autounattend.xml, and PowerShell configuration. Use when creating Windows VMs, managing iGPU PCI passthrough, or configuring Sunshine/Moonlight streaming.
---

# Windows VM Patterns

## iGPU PCI Passthrough

1. Bind iGPU to vfio-pci via runtime sysfs manipulation (echo to `/sys/bus/pci/drivers/`). Do NOT write modprobe config files (`blacklist-igpu.conf`, `vfio-pci-igpu.conf`). Sysfs-only binding is reversible without initramfs updates.

2. NEVER bind the sole GPU on an AMD host to vfio-pci. The amdgpu driver removal triggers a kernel panic when it is the only framebuffer. Hard-fail if `lspci | grep -c 'VGA compatible controller'` < 2 and vendor is AMD. Intel single-GPU hosts survive (SSH stays up).

3. Cleanup MUST `modprobe -r` the GPU driver BEFORE `modprobe` to re-probe devices. Simply running `modprobe i915` after vfio-pci unbind is a no-op if the module is already loaded. Sequence: unbind → `modprobe -r` → PCI rescan → `modprobe` → wait for DRI.

4. Previous bug: cleanup removed `/etc/modprobe.d/blacklist-igpu.conf` — a file the role never created. Cleanup MUST only remove artifacts the deploy creates.

## Per-Host VMID Computation

5. When multiple hosts share a Proxmox node (common in test environments), compute VMID as `base + groups['flavor_group'].index(inventory_hostname)`. Apply the same computation in provision, verify, AND cleanup.

6. The default cleanup list must include the full range of possible per-host VMIDs (base, base+1, base+2, ...) to cover all group sizes.

## Image Upload: Use /var/tmp, Not /tmp

7. Proxmox `/tmp` is often tmpfs (~7.8 GB). Windows qcow2 images are 8-18 GB. ALWAYS use `/var/tmp/` (real disk) for image uploads and `qemu-img convert` output.

8. Previous bug: `qemu-img convert` to `/tmp/` failed with "No space left on device" on a host with 8 GB tmpfs.

## QEMU Guest Agent Communication

9. Use `qm guest cmd <vmid> ping` to detect GA readiness. Use `qm guest exec <vmid> --timeout N -- cmd /c "..."` for in-VM commands. These bypass the network entirely — no SSH, no sshpass, no ProxyJump needed.

10. IP discovery: `qm guest cmd <vmid> network-get-interfaces` returns JSON. Parse with Python, skip Loopback interfaces AND 169.254.x.x (APIPA/link-local) addresses, extract first routable IPv4 address. Retry until a DHCP lease is obtained.

11. Previous bug: Guest Agent responded to ping before Windows DHCP client obtained a lease. The first IP returned was 169.254.143.117 (APIPA). SSH to this link-local address timed out. Fix: filter `169.254.` prefix in the Python parser and increase retries.

12. `qm guest cmd ping` is a simple command — use `ansible.builtin.command`, not `ansible.builtin.shell`. Only use shell when there's an actual pipeline.

## Windows Unattended Installation

13. Disable Windows Defender and Windows Update in the `specialize` pass via `RunSynchronous` + `reg add` to Group Policy keys. Do NOT use `FirstLogonCommands` — Tamper Protection blocks Defender changes from user-context commands.

14. Re-enable Defender and set Windows Update to Manual at the end of `post-install.ps1`. This prevents AV interference during setup but leaves the system in a safe state.

15. Post-install completion: write a marker file (`C:\post-install-done.txt`) at the end of `post-install.ps1`. Poll with `qm guest exec` checking for the marker. Hard-fail if marker not found within timeout — proceeding with an incomplete image violates the one-path principle.

## PowerShell via Ansible

16. NEVER wrap `ansible.builtin.raw` commands in `powershell -Command "..."` when the SSH shell is already PowerShell. The outer shell expands `$variables` in double-quoted strings before passing to the inner PowerShell instance.

17. Previous bug: `$svc = Get-Service ...` in `powershell -Command "... $svc ..."` — the outer PowerShell expanded `$svc` to empty, causing syntax errors.

## EFI Disk Format

18. On LVM-thin storage, EFI disks MUST use `format=raw`. The `qcow2` format is unsupported for EFI disks on LVM-thin and causes "unsupported format" errors.

## LAN Host VM Connectivity

19. VMs on LAN hosts get LAN IPs (e.g., 10.10.10.x) not directly routable from the controller. Add conditional `ProxyJump` via PRIMARY_HOST when the host is in `lan_hosts`.

---
name: vm-provisioning-patterns
description: VM provisioning patterns and step-by-step service creation. Use when creating new VM roles, implementing VM lifecycle management, or handling VM existence checks.
---

# VM Provisioning Patterns

## VM Existence Check Requirements

1. Each provision role MUST check for existing VM/container before creating. Guard ALL creation tasks with `when: not vm_exists | bool` (VMs) or `when: not lxc_exists | bool` (containers via `proxmox_lxc`).

2. Standard VM existence check pattern:
   ```yaml
   - name: Check if VM already exists
     ansible.builtin.command:
       cmd: qm status {{ vm_id }}
     register: vm_status
     failed_when: false
     changed_when: false

   - name: Set VM existence flag
     ansible.builtin.set_fact:
       vm_exists: "{{ vm_status.rc == 0 }}"
   ```

## VM Startup Configuration

3. Every VM MUST configure `--onboot 1 --startup order=N` via `qm set`. This task runs unconditionally to self-heal. Define `<type>_vm_startup_order` in role defaults.

4. Auto-start configuration pattern:
   ```yaml
   - name: Configure VM to start on boot
     ansible.builtin.command:
       cmd: >-
         qm set {{ vm_id }}
         --onboot 1
         --startup order={{ vm_startup_order }}
   ```

## Step-by-Step: Adding a New VM Type

5. Complete process for creating a new VM service (using `homeassistant` as example):

**Create provision role:**
```
roles/homeassistant_vm/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
```

**Create configure role:**
```
roles/homeassistant_configure/
├── defaults/main.yml
├── meta/main.yml
└── tasks/main.yml
```

**Add VMID to group_vars:**
```yaml
# inventory/group_vars/all.yml
homeassistant_vm_id: 200
homeassistant_vm_name: homeassistant
homeassistant_vm_memory: 2048
homeassistant_vm_cores: 2
homeassistant_vm_disk_size: 32G
homeassistant_image_path: images/haos.qcow2
```

**Add to inventory and site.yml, update Molecule, create VM-specific skill.**

## Add Host Pattern

6. Dynamic inventory pattern:
   ```yaml
   - name: Add VM to dynamic inventory
     ansible.builtin.add_host:
       name: "{{ vm_name }}"
       groups: dynamic_group
       ansible_host: "<bootstrap_ip>"
   ```

## Design Principles

7. **Bake, don't configure at runtime**: Custom images are REQUIRED. Provision roles verify the image exists and hard-fail if missing. Configure roles NEVER install packages.

8. **One path, no fallbacks**: NEVER add stock/generic image fallback logic. One tested code path per feature. Missing prerequisites fail with an actionable error message.

9. **Follow community standards**: Check upstream tooling before writing custom workarounds.

## Documented Exceptions

10. Three documented exceptions to bake principle (each MUST be explicitly documented):
    - **Docker pull of pinned image tag**: deterministic and versioned
    - **Desktop VMs via cloud image + apt**: too large and hardware-dependent for pre-built images
    - **Windows VMs via ISO + autounattend.xml**: install-from-ISO IS the bake approach for Windows

11. Any OTHER runtime package installation is rejected. If you need a new package, add it to the image build script.
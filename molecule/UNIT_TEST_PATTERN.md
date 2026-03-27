# Unit Test Pattern for Per-Feature Scenarios

Every per-feature molecule scenario (`molecule/<service>-lxc/`,
`molecule/<service>-vm/`) is a **complete unit test** that owns the full
lifecycle: image build, deployment, configuration, verification, and teardown.

## Test sequence

```yaml
scenario:
  test_sequence:
    - dependency
    - syntax
    - cleanup      # ensure clean state from prior runs
    - prepare      # build image if not cached
    - converge     # provision + configure from image
    - verify       # ALL service-specific functionality
    - cleanup      # tear down
```

## Prepare pattern

Each scenario has a `prepare.yml` that invokes `build-images.sh` to build
the service image if it doesn't already exist in `images/`.

### Remote-built services (LXC/VM on Proxmox)

```yaml
---
- name: Build <service> image
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Build <service> image if not cached
      ansible.builtin.command:
        cmd: >-
          {{ playbook_dir }}/../../scripts/build-images.sh
          --host {{ lookup('env', 'PRIMARY_HOST') }}
          --only <target>
      register: _build
      changed_when: "'already exists' not in _build.stdout"
```

### Local-built services (OpenWrt Image Builder)

```yaml
---
- name: Build <service> image
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Build <service> image if not cached
      ansible.builtin.command:
        cmd: >-
          {{ playbook_dir }}/../../scripts/build-images.sh
          --only <target>
      register: _build
      changed_when: "'already exists' not in _build.stdout"
```

## Day-to-day workflow

```bash
# Full unit test (builds image if needed, tests everything, tears down)
molecule test -s pihole-lxc

# Quick iteration (skip image build, keep state)
molecule converge -s pihole-lxc
molecule verify -s pihole-lxc

# Force image rebuild
rm images/pihole-debian-12-amd64.tar.zst
molecule test -s pihole-lxc
```

## Image caching

- `build-images.sh --only <target>` is idempotent: skips if the output file
  exists in `images/`.
- Day-to-day iteration is fast because prepare is a no-op when the image
  is cached.
- Images persist across test runs. E2E tests consume the same cached images.
- Force a rebuild by deleting the cached image file, then re-running the
  unit test.

## Verify coverage

Per-feature verify must cover ALL service-specific functionality:
- Container/VM state (running, config, onboot, startup order)
- Service health (process running, ports listening)
- Service functionality (DNS resolution, log ingestion, streaming, etc.)
- Error handling (what happens when upstream is down)

E2E verify (`molecule/default/verify.yml`) only checks:
- Infrastructure assertions (bridges, iGPU, IOMMU)
- Basic service health (running, correct config)
- Cross-service integration (DNS queries, syslog forwarding, VPN handshakes)

## Adding a new service

1. Create `molecule/<service>-lxc/prepare.yml` using the pattern above
2. Add `prepare` to `test_sequence` in `molecule/<service>-lxc/molecule.yml`
3. Ensure verify covers all service-specific functionality
4. Add basic health + integration checks to E2E verify

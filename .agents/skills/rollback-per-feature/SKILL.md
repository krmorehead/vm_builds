---
name: rollback-per-feature
description: Per-feature rollback implementation patterns and tag conventions. Use when implementing rollback for specific features, managing UCI changes, or designing rollback procedures.
---

# Per-Feature Rollback Implementation

## Rollback Tag Convention

1. Per-feature rollback uses Ansible tags in `cleanup.yml`. Convention: `--tags <feature>-rollback` runs the inverse of `--tags <feature>`.

2. Feature Tags follow this pattern:
   ```
   Feature Tags (current)
   ├── Apply: openwrt-security, openwrt-vlans, openwrt-dns, openwrt-mesh
   └── Rollback: openwrt-security-rollback, openwrt-vlans-rollback, openwrt-dns-rollback, openwrt-mesh-rollback
   ```

3. Apply tags go in `site.yml` plays. Rollback tags go in `cleanup.yml` plays. Both use the `never` meta-tag so they don't run unless explicitly requested.

## OpenWrt UCI Rollback Pattern

4. UCI changes on OpenWrt are atomic per `uci commit`. ALWAYS group related UCI changes into a single commit. Rollback reverses them with `uci delete` or `uci set` to defaults, then `uci commit`.

5. Per-feature rollback pattern (OpenWrt UCI):

   ```yaml
   # In playbooks/cleanup.yml
   - name: Rollback security hardening
     hosts: openwrt
     tags: [openwrt-security-rollback, never]
     gather_facts: false
     tasks:
       - name: Remove banIP and revert SSH config
         ansible.builtin.raw: |
           opkg remove banip 2>/dev/null
           uci delete dropbear.@dropbear[0].PasswordAuth 2>/dev/null
           uci delete dropbear.@dropbear[0].RootPasswordAuth 2>/dev/null
           uci commit dropbear
           /etc/init.d/dropbear restart
   ```

6. The `never` tag prevents rollback from running during full cleanup. It only runs when explicitly requested: `--tags openwrt-security-rollback`.

## Package Installation Rollback

7. Package installation rollback uses `opkg remove`. ALWAYS list installed packages in the feature's rollback procedure.

## State Tracking for Rollback

8. `deploy_stamp` records what ran. Rollback tasks MUST check `ansible_local.vm_builds.plays` to determine what needs undoing. NEVER attempt to roll back a feature that was never applied.

9. Use deploy_stamp for idempotent feature application — skip expensive operations when the feature is already at the current version.

## Auth State Rollback

10. Rollback MUST fully restore the baseline auth/connection state. If a feature deploys SSH keys and disables password auth, rollback MUST re-enable password auth AND clear the root password (restoring empty-password baseline).

11. Partial auth rollback leaves the system in an undefined state — neither key nor password works.

## cleanup.sh Integration

12. `cleanup.sh` supports a `rollback` subcommand that delegates to `build.py`:

    ```bash
    ./cleanup.sh rollback security test.env
    # → build.py --playbook cleanup --tags openwrt-security-rollback
    ```

13. The subcommand maps the feature name to the rollback tag using the convention `openwrt-<feature>-rollback`.

## Stub Prevention

14. NEVER add stub plays/rollback for features that depend on unimplemented projects. Integration plays are owned by the downstream project that creates the dependency. Keep `site.yml` and `cleanup.yml` free of dead code.
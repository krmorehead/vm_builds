---
name: service-config-validation
description: Service configuration validation and config management patterns. Use when validating service configs, managing config file ordering, or implementing service health checks.
---

# Service Configuration & Validation Rules

## Config Validation Requirement

1. Configure roles that deploy config files into LXC containers SHOULD validate the config before restarting the service. If the config is invalid, the service won't start and the container loses the service until the config is fixed.

2. Pattern: use an Ansible handler chain where validation runs before restart:

   ```yaml
   # handlers/main.yml
   - name: Validate config
     ansible.builtin.command:
       cmd: <service> --check-config  # e.g., rsyslogd -N1, nginx -t
     listen: _restart_service
     changed_when: false

   - name: Restart service
     ansible.builtin.command:
       cmd: systemctl restart <service>
     listen: _restart_service
   ```

3. Handlers with the same `listen` event run in definition order. If validation fails, the chain stops and the restart never executes.

## Health Check Pattern

4. After flush_handlers, add a health check with retries to confirm the service came up:

   ```yaml
   - name: Wait for service
     ansible.builtin.command:
       cmd: systemctl is-active <service>
     retries: 5
     delay: 2
     until: result.stdout | trim == 'active'
   ```

5. Previous bug: rsyslog `20-forward.conf` deployment had no config validation. An invalid template would have crashed rsyslog on restart, killing log reception for all upstream senders.

## Config File Ordering for Optional Runtime Configs

6. When baked image configs need to interoperate with optional runtime configs, use numbered filenames in `/etc/<service>.d/` to control processing order:

   ```
   10-base.conf       — module loads, template definitions (baked)
   20-optional.conf   — runtime config deployed by configure role
   50-routing.conf    — final routing/filtering (baked)
   ```

7. This pattern is needed when:
   - The runtime config needs to intercept messages before the baked config processes them
   - The baked config uses `stop` to prevent messages from falling through

8. Previous bug: rsyslog used a named ruleset for TCP-received messages. Messages in a named ruleset never enter the default ruleset, so the optional forwarding config never saw remote messages.

## Diagnostics Pattern

9. Every VM type SHOULD include diagnostic tasks at key milestones in its roles. These run on every build and provide debug context when things fail.

10. Standard diagnostic milestones for any VM:
    - **Post-bootstrap** (`<type>_vm`): VM status, bridge layout, bootstrap IP, `dmesg` errors
    - **Post-configure** (`<type>_configure`): Service status, network state, final config
    - **Final report** (`<type>_configure`): Summary of all configured parameters

11. Rules:
    - `changed_when: false` and `failed_when: false` — diagnostics MUST NOT break the build
    - Register output and display via `debug: var:` so it appears in logs
    - Include `dmesg` checks — kernel errors are often the root cause when app-level symptoms mislead
    - Include protocol-level checks — ICMP ping working does NOT mean TCP/HTTP works

## Handler Conventions for LXC Service Roles

12. Configure roles that run inside LXC containers via `pct_remote` MUST use `ansible.builtin.systemd` for service restarts in handlers, not `ansible.builtin.command: cmd: systemctl restart ...`.

13. Use `ansible.builtin.command` only for operations that have no module equivalent: config validation, status checks, and binary execution.

14. Previous bug: `rsyslog_configure` handler used `ansible.builtin.command` for restart while `pihole_configure` used `ansible.builtin.systemd`. Fixed for consistency.

## Logrotate in LXC Containers

15. When writing logrotate configs baked into LXC images, use `root adm` as the file ownership — NOT `syslog adm`. The `syslog` user may not exist in minimal container templates.

16. Previous bug: logrotate config with `create 0640 syslog adm` failed in the rsyslog container because the `syslog` user didn't exist in the Proxmox Debian 12 standard template.
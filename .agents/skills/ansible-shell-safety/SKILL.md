---
name: ansible-shell-safety
description: Ansible shell task safety patterns, pipefail requirements, raw heredoc pitfalls, and deprecated patterns. Use when writing shell tasks, handling heredocs, or debugging Ansible failures.
---

# Ansible Shell Task Safety

## Shell Task Requirements

ALWAYS use `set -o pipefail` in any shell task that contains a pipeline (`|`). Without it, only the exit code of the LAST command in the pipeline is checked — failures in earlier commands are silently swallowed.

ALWAYS set `executable: /bin/bash` on shell tasks that use bash-specific features. The default shell may be `/bin/sh` which doesn't support `pipefail`.

**Exception:** `ansible.builtin.raw` tasks and commands that run on OpenWrt/BusyBox ash. BusyBox ash does NOT support `pipefail`.

## Shell Task Pattern

ALWAYS use the block scalar (`cmd: |`) format for pipefail commands, not the folded scalar (`cmd: >-`). This keeps `set -o pipefail` on its own line:

```yaml
# GOOD — pipeline failure propagates correctly
- name: Get gateway
  ansible.builtin.shell:
    cmd: |
      set -o pipefail
      ip route show default | awk '{print $3}' | head -1
    executable: /bin/bash
```

## Raw Heredoc Pitfalls

When using `ansible.builtin.raw: |` with shell heredocs, the parser may fail on content that looks like Jinja2:

- `${var:-default}` — use `$var` or avoid defaults
- `|| true` inside heredocs — can confuse the parser
- POSIX character classes in `tr` (e.g., `[:space:]`) — colons interact with YAML/Jinja2 parsing

ALWAYS run `ansible-playbook --syntax-check playbooks/site.yml` after modifying `raw:` tasks with heredocs.

## Deprecated Patterns

NEVER use `local_action`. It was deprecated in Ansible and trips lint errors.

```yaml
# GOOD — modern equivalent
- name: Do something locally
  ansible.builtin.file:
    path: /tmp/foo
    state: directory
  delegate_to: localhost
```

NEVER use short module names (e.g., `command`). ALWAYS use FQCNs (e.g., `ansible.builtin.command`).

## Common Failures

| Issue | Cause | Fix |
|-------|-------|-----|
| `deprecated-local-action` lint error | Used `local_action` syntax | Replace with `delegate_to: localhost` |
| Silent pipeline failures | Missing `set -o pipefail` | Add pipefail to all shell tasks with pipes |
| Heredoc parsing errors | Jinja2-like content in raw tasks | Use `$var` instead of `${var}`, avoid `|| true` |
| Bash features fail | Wrong shell executable | Set `executable: /bin/bash` for bash-specific features |

## Shell Task Audit Pattern

This class of bug is silent and recurring. Periodically scan the codebase:

```bash
# Find shell tasks with pipes but no pipefail
rg -l 'ansible.builtin.shell' roles/ molecule/ playbooks/ | \
  xargs rg -l '|' | sort -u
# Then manually check each file for set -o pipefail
```

Previous bug: a single audit pass found missing `pipefail` in 6 roles and both cleanup playbooks. All were silent — no test caught them.

## Molecule Env Var Handling

Molecule's `provisioner.env` section uses `${VAR_NAME}` syntax for variable substitution. NEVER use shell-style defaults like `${VAR:-default}` — the parser treats `:-}` as part of the variable name and fails.

For required env vars: use `${VAR_NAME}` and ensure the var is always set in `test.env`. For optional env vars: do NOT add them to `provisioner.env` at all. The role's `defaults/main.yml` already uses `lookup('env', 'VAR_NAME') | default('', true)`.

Previous bug: `RSYSLOG_HOME_SERVER: ${RSYSLOG_HOME_SERVER:-}` in `molecule.yml` caused "Invalid placeholder in string" and prevented all molecule runs from starting.

## File Deployment via copy + pct push (LXC Containers)

NEVER use heredocs inside `ansible.builtin.command` or `ansible.builtin.shell` for deploying config files into LXC containers via `pct exec -- bash -c 'cat > file << EOF ... EOF'`. YAML string folding (both `>-` and `|`) interacts badly with heredoc syntax, causing bash to receive malformed commands.

ALWAYS use the `ansible.builtin.copy` + `pct push` pattern:

```yaml
- name: Write config to host temp
  ansible.builtin.copy:
    content: |
      [Section]
      key = {{ ansible_variable }}
      static = value
    dest: /tmp/service_config.conf
    mode: "0644"

- name: Push into container
  ansible.builtin.command:
    cmd: pct push {{ ct_id }} /tmp/service_config.conf /etc/service/config.conf
  changed_when: true

- name: Clean up temp
  ansible.builtin.file:
    path: /tmp/service_config.conf
    state: absent
```

This pattern:
- Preserves Jinja2 variable interpolation (in `copy.content`)
- Avoids shell escaping across 4 layers (local bash → SSH → pct exec → bash -c)
- Works reliably for XML, YAML, INI, and any other config format
- Is idempotent (copy + push overwrites existing files)

Previous bug: `pct exec {{ ct_id }} -- bash -c 'cat > /etc/jellyfin/network.xml << "NETWORK_EOF" ...'` via `ansible.builtin.command` with `>-` folded scalar collapsed all newlines onto a single line. Bash received the heredoc delimiter and content on the same line, causing `syntax error near unexpected token '<'`. Same issue affected Kodi's `guisettings.xml` and `.asoundrc` deployments.

## Shell operators in pct exec

When passing shell operators (`||`, `&&`, `2>/dev/null`, `>`) to `pct exec`, they are interpreted as literal arguments, not shell syntax. ALWAYS wrap in `bash -c '...'`:

```yaml
# BAD — 2>/dev/null and || true are literal args to groupadd
cmd: pct exec {{ ct_id }} -- groupadd -g 993 render 2>/dev/null || true

# GOOD — bash interprets the shell operators
cmd: pct exec {{ ct_id }} -- bash -c 'groupadd -g 993 render 2>/dev/null || true'
```

Previous bug: `groupadd` received `2>/dev/null` and `||` as arguments, failing with "Usage: groupadd [options] GROUP".

## build-images.sh Shell Escaping

When writing config files via `pct exec -- bash -c '...'` inside `remote_cmd "..."`, there are 4 layers of shell interpretation: (1) local bash double quotes, (2) SSH, (3) remote bash (parsing single quotes around bash -c), (4) bash -c with heredoc.

For heredocs with quoted delimiters (`<< "EOF"`), NO expansion happens inside. To get a literal `$var` in the file: use `\$var` (single backslash-dollar). Local bash expands `\$` to `$`, then it passes through unchanged.

NEVER use `\\\$var` (triple backslash-dollar) — it produces `\$var` in the file, not `$var`.

Previous bug: `\\\$AllowedSender` produced `\$AllowedSender` in the rsyslog config file, which was silently ignored as a deprecated directive. `\\\$inputname` produced `\$inputname` which caused a parse error, preventing rsyslog from starting.

ALWAYS verify baked config by creating a test container from the template and inspecting the actual file content with `pct exec -- cat /path/to/config`.
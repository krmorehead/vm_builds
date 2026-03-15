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

## build-images.sh Shell Escaping

When writing config files via `pct exec -- bash -c '...'` inside `remote_cmd "..."`, there are 4 layers of shell interpretation: (1) local bash double quotes, (2) SSH, (3) remote bash (parsing single quotes around bash -c), (4) bash -c with heredoc.

For heredocs with quoted delimiters (`<< "EOF"`), NO expansion happens inside. To get a literal `$var` in the file: use `\$var` (single backslash-dollar). Local bash expands `\$` to `$`, then it passes through unchanged.

NEVER use `\\\$var` (triple backslash-dollar) — it produces `\$var` in the file, not `$var`.

Previous bug: `\\\$AllowedSender` produced `\$AllowedSender` in the rsyslog config file, which was silently ignored as a deprecated directive. `\\\$inputname` produced `\$inputname` which caused a parse error, preventing rsyslog from starting.

ALWAYS verify baked config by creating a test container from the template and inspecting the actual file content with `pct exec -- cat /path/to/config`.
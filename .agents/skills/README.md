# Skills for vm_builds Project

This directory contains skills for the vm_builds Ansible project. Each skill has a specific focus and is designed to be concise for LLM consumption.

## Skills

| Skill | Lines | Focus |
|-------|-------|-------|
| writing-skills | 81 | How to write skills. Minimize context bloat, avoid perpendicular examples |
| build-entry-point | 77 | Build.py orchestration, shell delegation, host probing with fallback |
| build-testing | 59 | Test coverage for build.py functions. Test classes, error paths |
| python-code-style | 89 | Python conventions. No sys.exit(), quote stripping, type hints |
| lan-ssh-patterns | 88 | SSH ProxyJump for LAN hosts. Baseline dependency, keepalives |
| lan-node-setup | 125 | Add LAN hosts, env vars, inventory, bootstrap flow |
| molecule-cleanup | 97 | Molecule cleanup. Credential safety, explicit VMIDs |
| molecule-testing | 148 | Molecule commands, TDD workflow, layered scenarios, optimization |
| molecule-verify | 136 | Molecule verification patterns. Batch operations, multi-node |

**Total: 900 lines across 9 skills**

## Guidelines

- **< 100 lines ideal, < 200 lines hard limit**
- Single GOOD implementation example per pattern (no BAD examples)
- Lead with NEVER/ALWAYS constraints before examples
- Remove explanations of what things are
- Focus on previous bugs and prevention
- Pack descriptions with trigger words

## Migration from Cursor

Created from `.cursor/skills/`:
- `ansible-testing` → `molecule-testing`, `molecule-cleanup`, `molecule-verify`
- `build-conventions` → `build-entry-point`, `python-code-style`, `build-testing`
- `multi-node-ssh` → `lan-ssh-patterns`, `lan-node-setup`

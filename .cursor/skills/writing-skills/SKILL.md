---
name: writing-skills
description: Author effective Cursor skills optimized for LLM consumption. Use when creating, editing, or reviewing SKILL.md files, or when the user asks to capture knowledge as a skill.
---

# Writing Skills for LLMs

Skills are injected into LLM context. The reader is an LLM, not a human. Optimize accordingly.

## Core principles

1. **Token budget is shared.** Every line competes with conversation history, other skills, and the user's code. Justify each sentence's inclusion.
2. **LLMs already know general knowledge.** Don't explain what Ansible is. Don't explain what a bridge is. Only state things the LLM would get wrong or miss without the skill.
3. **LLMs repeat mistakes.** Skills exist to prevent recurrence. The highest-value content is "thing that went wrong + what to do instead."
4. **LLMs follow patterns.** Give one concrete example of the right way. The LLM will generalize. Two examples only if the pattern has important variants.
5. **LLMs obey constraints better than suggestions.** "NEVER run `ifdown --all`" works. "Consider avoiding `ifdown --all`" does not.

## Structure template

```markdown
---
name: kebab-case-name
description: What + When in third person. Include trigger words.
---

# Title

## Context (2-3 sentences max)
Why this skill exists. What goes wrong without it.

## Rules
Numbered list of constraints. Use NEVER/ALWAYS for hard rules.

## Patterns
Concrete examples: bad → good, with minimal surrounding code.

## Decision tree (optional)
For multi-branch logic, use a text tree. LLMs parse these well.
```

## Writing rules

- **< 200 lines ideal, < 500 hard limit.** If longer, split into SKILL.md + reference.md.
- **Lead with constraints.** Put NEVER/ALWAYS rules before examples. LLMs weight early content higher.
- **Use code blocks for patterns.** `# BAD` / `# GOOD` labels inside the block. No prose between them — the contrast teaches.
- **One concern per skill.** "Proxmox safety" and "Ansible testing" are separate skills. Don't combine.
- **No hedging.** Remove "you might want to", "it's generally better to", "consider". State facts.
- **Description is the trigger.** The LLM reads the description to decide whether to load the skill. Pack it with specific terms the user or task would mention.

## Anti-patterns

```markdown
# BAD: Explains what LLMs already know
"Ansible is a configuration management tool that uses YAML..."

# BAD: Vague suggestion
"Try to avoid running dangerous commands on remote hosts"

# BAD: Wall of prose
Three paragraphs explaining why ifdown is dangerous...

# GOOD: Constraint + consequence
"NEVER run `ifdown --all` on a remote Proxmox host — it kills the
management network and requires physical console access to recover."

# GOOD: Pattern with minimal context
## Safe bridge teardown
- Iterate bridges, skip vmbr0 (management)
- `ip link set $br down && ip link delete $br` per bridge
- Then `ifup --all --force` to restore from config
```

## Skill architecture: general vs specific

When a project manages multiple components of the same kind (VMs, services, microservices), split skills into layers:

- **General skill** — patterns that apply to ALL components of that kind. Contains the skeleton, checklist, shared conventions, and architectural constraints.
- **Component-specific skill** — patterns particular to ONE component. Contains its package manager, service model, restart sequences, firewall behavior, network topology, and bug history.

```
# GOOD: layered skills
.cursor/skills/vm-lifecycle/SKILL.md      # general: two-role pattern, VMID allocation, deploy_stamp
.cursor/skills/openwrt-build/SKILL.md     # specific: UCI, firewall zones, two-phase restart, opkg
.cursor/skills/homeassistant-build/SKILL.md  # specific: HAOS API, add-on management

# BAD: everything in one file
.cursor/skills/vm-lifecycle/SKILL.md      # general + OpenWrt firewall zones + HAOS API + ...
```

**Litmus test**: If a pattern mentions a specific component's name, package manager, init system, or network topology, it belongs in the component-specific skill, not the general one.

**Why this matters**: An LLM working on a new VM type gets only the general patterns without noise from other VMs. An LLM working on OpenWrt gets both general + OpenWrt-specific. Without separation, OpenWrt-specific lessons (like firewall zone rebinding) pollute the context for every other VM type.

Previous bug: firewall zone rebinding after network restarts, UCI commands, opkg feed switching, and bootstrap IP migration were all stored in `vm-lifecycle`. An LLM adding a Home Assistant VM would see all that noise and might incorrectly apply OpenWrt patterns to a different VM type.

## Decision: skill vs rule

| Use a **skill** when | Use a **rule** when |
|---|---|
| Multi-step procedure | Single convention |
| Domain knowledge needed | Style / formatting preference |
| Decision tree or conditional logic | Always-on constraint |
| > 10 lines of guidance | < 50 lines |
| Loaded on demand | Applied to every session or file type |

## Validating a skill

After writing, check:
- [ ] Would an LLM get this wrong without the skill? If no, delete it.
- [ ] Can any sentence be removed without losing information? If yes, remove it.
- [ ] Are there concrete examples of the right pattern? If no, add one.
- [ ] Is the description specific enough to trigger on relevant tasks? Test by asking: "would the LLM pick this skill if the user said X?"

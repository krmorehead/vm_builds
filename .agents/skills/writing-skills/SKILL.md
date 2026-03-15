---
name: writing-skills
description: Use when creating new skills, writing skill documentation, or building LLM-optimized skills. Includes skill structure patterns, constraint formatting, and validation guidelines.
---

# Writing Skills for LLMs

## When to Use
This skill triggers when you:
- Create new skill files (.agents/skills/*/SKILL.md)
- Write or edit existing skill documentation
- Build skills optimized for LLM consumption
- Need skill structure guidance
- Want to validate skill effectiveness

## Core principles

1. **Token budget is shared.** Every line competes with conversation history, other skills, and user code. Justify each sentence's inclusion.
2. **LLMs already know general knowledge.** Don't explain what Ansible is. Don't explain what a bridge is. Only state things the LLM would get wrong or miss without the skill.
3. **LLMs repeat mistakes.** Skills exist to prevent recurrence. The highest-value content is "thing that went wrong + what to do instead."
4. **LLMs follow patterns.** Give one concrete example of the right way. The LLM will generalize. Two examples only if the pattern has important variants.
5. **LLMs obey constraints better than suggestions.** "NEVER run `ifdown --all`" works. "Consider avoiding `ifdown --all`" does not.

## Size constraints

- **< 100 lines ideal, < 200 hard limit.** Longer = context bloat = skill ignored.
- If longer, split into separate skills by concern.
- Remove explanations of what X is. Focus on what goes wrong without guidance.

## Structure template

```markdown
---
name: skill-name
description: What + When in third person. Include specific trigger words.
---

# Title

## Rules (numbered)
NEVER/ALWAYS constraints. Concrete and actionable.

## Patterns
Single correct implementation example. No BAD/GOOD contrast.
```

## Anti-patterns

```markdown
# BAD: Explains what tool is
"Molecule is a testing framework for Ansible..."

# BAD: BAD/GOOD perpendicular examples (context bloat)
# BAD — do this
# GOOD — do that instead

# BAD: WALL of examples for the same thing
Three variations of the same pattern

# GOOD: Constraint + single implementation
"NEVER call sys.exit() from functions."

def example():
    return None
```

## Writing rules

- **Lead with constraints.** NEVER/ALWAYS before examples.
- **One example per pattern.** LLMs generalize.
- **No hedging.** Delete "consider", "might want to", "generally better".
- **Description has trigger words.** Pack with terms user/tasks mention.
- **Canonical location is `.agents/skills/`.** All skills live in `.agents/skills/<name>/SKILL.md`. Legacy `.cursor/skills/` files may still exist but `.agents/skills/` is the authoritative source.

## Validating

After writing:
- Try removing each sentence. Does meaning change? If no, delete.
- Count implementation examples. More than 1? Remove extras.
- Check line count. >100? Split or prune.
- Test description: triggers right skill if user said X?

## Validating a skill

After writing, check:
- [ ] Would an LLM get this wrong without the skill? If no, delete it.
- [ ] Can any sentence be removed without losing information? If yes, remove it.
- [ ] Are there concrete examples of the right pattern? If no, add one.
- [ ] Is the description specific enough to trigger on relevant tasks? Test by asking: "would the LLM pick this skill if the user said X?"

## Skill architecture: general vs specific

When a project manages multiple components of the same kind (VMs, services, microservices), split skills into layers:

- **General skill** — patterns that apply to ALL components of that kind. Contains the skeleton, checklist, shared conventions, and architectural constraints.
- **Component-specific skill** — patterns particular to ONE component. Contains its package manager, service model, restart sequences, firewall behavior, network topology, and bug history.

```
# GOOD: layered skills
.agents/skills/vm-lifecycle-architecture/SKILL.md    # general: two-role pattern, VMID allocation, deploy_stamp
.agents/skills/openwrt-network-topology/SKILL.md    # specific: UCI, firewall zones, two-phase restart, opkg
.agents/skills/service-config-validation/SKILL.md   # specific: service validation patterns

# BAD: everything in one file
.agents/skills/vm-lifecycle/SKILL.md                # general + OpenWrt firewall zones + service validation + ...
```

**Litmus test**: If a pattern mentions a specific component's name, package manager, init system, or network topology, it belongs in the component-specific skill, not the general one.

**Why this matters**: An LLM working on a new VM type gets only the general patterns without noise from other VMs. An LLM working on OpenWrt gets both general + OpenWrt-specific. Without separation, OpenWrt-specific lessons (like firewall zone rebinding) pollute the context for every other VM type.

Previous bug: firewall zone rebinding after network restarts, UCI commands, opkg feed switching, and bootstrap IP migration were all stored in `vm-lifecycle`. An LLM adding a Home Assistant VM would see all that noise and might incorrectly apply OpenWrt patterns to a different VM type.

## Decision: skill vs rule

| Use a **skill** when | Use a **rule** when |
|---|---|
| Domain knowledge needed | Single convention |
| Multi-step procedure | Style preference |
| Decision tree or branching | Always-on constraint |
| > 10 lines | < 50 lines, simple pattern |

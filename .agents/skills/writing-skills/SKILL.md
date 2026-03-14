---
name: writing-skills
description: Author effective skills optimized for LLM consumption. Minimize context bloat, avoid perpendicular examples, keep concise.
---

# Writing Skills for LLMs

## Core principles

1. **Token budget is shared.** Every line competes with conversation history, other skills, and user code.
2. **LLMs already know general knowledge.** Don't explain what tool/tech is. Only state things LLMs get wrong without the skill.
3. **LLMs repeat mistakes.** Focus on previous bugs and how to prevent them.
4. **LLMs follow patterns.** One simple implementation example. Multiply examples waste tokens.
5. **Context bloat kills utility.** BAD/GOOD perpendicular examples over-teach. Simple correct implementation is enough.

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

## Validating

After writing:
- Try removing each sentence. Does meaning change? If no, delete.
- Count implementation examples. More than 1? Remove extras.
- Check line count. >100? Split or prune.
- Test description: triggers right skill if user said X?

## Decision: skill vs rule

| Use a **skill** when | Use a **rule** when |
|---|---|
| Domain knowledge needed | Single convention |
| Multi-step procedure | Style preference |
| Decision tree or branching | Always-on constraint |
| > 10 lines | < 50 lines, simple pattern |

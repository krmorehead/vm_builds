# OpenCode Rules Writing

Use when creating AGENTS.md files, configuring OpenCode rule precedence, implementing team coding standards, setting up opencode.json instructions, or managing external rule references.

## Rules

1. NEVER place global rules in project AGENTS.md
2. ALWAYS test rule precedence before deployment  
3. NEVER reference external files without fallbacks
4. ALWAYS document external dependencies
5. NEVER duplicate rules across files
6. ALWAYS use trigger words: AGENTS.md, opencode.json, rule precedence, external references

## Validation

After writing rules:
- Does AGENTS.md use project-specific language?
- Does opencode.json include remote URL patterns?
- Are external file references properly documented?
- Would skill trigger if user mentions "AGENTS.md" or "rule precedence"?

## Patterns

AGENTS.md structure for precedence:

```markdown
# Project Name

## Structure
- packages/ - workspace packages
- infra/ - infrastructure definitions

## Standards
- Use TypeScript with strict mode
- Shared code in packages/core/

## External References
@docs/guidelines.md - load when referenced
```

opencode.json for remote rules:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "instructions": ["CONTRIBUTING.md", "https://raw.githubusercontent.com/org/rules/main/style.md"]
}
```

## Anti-patterns

NEVER explain what tools are in rules
NEVER create duplicate rules in multiple files
NEVER reference external files without fallbacks
NEVER ignore rule precedence conflicts
---
name: writing-skills
description: Author effective skills optimized for LLM consumption. Create, edit, or review SKILL.md files.
compatibility: opencode
---

## Structure

One folder per skill with SKILL.md inside.

Locations (searched in order):
- Project: .agents/skills/<name>/SKILL.md
- Global: ~/.agents/skills/<name>/SKILL.md
- Legacy: .opencode/skills/, .claude/skills/, ~/.config/opencode/skills/, ~/.claude/skills/

## Frontmatter

Required fields:
```yaml
---
name: skill-name
description: 1-1024 chars, specific for agent selection
---
```

Optional:
- license: SPDX identifier
- compatibility: opencode, claude, or both
- metadata: key-value map

## Name Rules

1-64 characters, lowercase alphanumeric with single hyphens:
- No consecutive `--`
- Cannot start/end with `-`
- Must match directory name
- Regex: `^[a-z0-9]+(-[a-z0-9]+)*$`

## Discovery

OpenCode walks up from CWD to git worktree root, loading all matching `skills/*/SKILL.md` in each directory. Global paths always load.

Agents call `skill({ name: "skill-name" })` to load.

## Example

```markdown
---
name: git-release
description: Create consistent releases and changelogs
---

## What I do

- Draft release notes from merged PRs
- Propose a version bump
- Provide copy-pasteable `gh release create` command

## When to use me

Use when preparing a tagged release. Ask clarifying questions if versioning scheme is unclear.
```

Common sections: What I do, When to use me, Conventions, Patterns.

## Agent Loading

Agents load skills via the `skill` tool. Each visible skill appears in `<available_skills>` with name and description, used to select the right skill for the task.

## Permissions

In opencode.json:
```json
{
  "permission": {
    "skill": {
      "*": "allow",
      "internal-*": "deny",
      "experimental-*": "ask"
    }
  }
}
```

Per-agent overrides:
- Custom: in agent frontmatter
- Built-in: in `agent.<name>.permission.skill`

Values: allow, deny, ask

## Tool Control

Disable skill tool entirely:
- Custom agent: `tools: { skill: false }`
- Built-in: `agent.<name>.tools.skill: false`

## Troubleshoot

- SKILL.md must be all caps
- Frontmatter needs name and description
- Skill names unique across all locations
- Check permission patterns (deny hides skills)

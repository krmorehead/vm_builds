# OpenCode Rules Translation Project

Translate all rules from `.cursor/rules/` directory to `.agents/skills/` directory following OpenCode rules writing patterns.

## Project Overview

**Self-contained.** Each rule file translates independently into a skill following writing-skills patterns: NEVER/ALWAYS constraints, single implementation examples, minimal context bloat.

**Files to translate:** 11 rule files in `.cursor/rules/` → 11 corresponding skills in `.agents/skills/`

## Milestone dependency graph
M0 (project setup)
├── M1 (proxmox-safety-rules) ← self-contained
├── M2 (project-structure-rules) ← self-contained
├── M3 (ansible-conventions) ← self-contained
├── M4 (testing-workflow) ← self-contained
├── M5 (async-job-patterns) ← self-contained
├── M6 (task-ordering) ← self-contained
├── M7 (secret-generation) ← self-contained
├── M8 (clean-baselines) ← self-contained
├── M9 (use-idle-time) ← self-contained
├── M10 (learn-from-mistakes) ← self-contained
├── M11 (project-plan-review) ← self-contained
└── M12 (validation + docs) ← self-contained

## Milestone 0: Project Setup
_Self-contained._
Set up project structure and validate translation approach.

**Implementation pattern:**
- Create project directory structure
- Define translation strategy and validation approach
- Test writing-skills integration

**Tasks:**
- [ ] Create project directory structure
- [ ] Validate existing .cursor/rules files
- [ ] Define translation approach using writing-skills patterns
- [ ] Set up todo tracking

**Verify:**
- [ ] All 11 source files identified and readable
- [ ] Translation strategy documented
- [ ] Project plan follows project-planning-structure skill patterns

**Rollback:**
Remove project directory and reset todo list.

## Milestone 1: proxmox-safety-rules Translation
_Self-contained._
Translate `.cursor/rules/proxmox-safety.mdc` to `.agents/skills/proxmox-safety-rules/SKILL.md`.

**Implementation pattern:**
- Create skill directory structure following writing-skills patterns
- Convert explanatory content to NEVER/ALWAYS constraints
- Extract single implementation examples
- Minimize context bloat (<100 lines ideal)

**Tasks:**
- [ ] Read and analyze source rule file
- [ ] Create `.agents/skills/proxmox-safety-rules/` directory
- [ ] Extract critical safety rules as NEVER/ALWAYS constraints
- [ ] Convert examples to single implementation patterns
- [ ] Update skill description with trigger words

**Verify:**
- [ ] Skill follows writing-skills structure template
- [ ] Contains concrete NEVER/ALWAYS constraints
- [ ] Has single implementation example
- [ ] Description includes proxmox, safety, remote host triggers
- [ ] Total lines <100 where possible

**Rollback:**
Remove skill directory and update todo list.

## Milestone 2: project-structure-rules Translation
_Self-contained._
Translate `.cursor/rules/project-structure.mdc` to `.agents/skills/project-structure-rules/SKILL.md`.

**Implementation pattern:**
- Focus on architectural patterns and design principles
- Extract "bake don't configure" and other key principles
- Convert checklist items to actionable rules
- Maintain project context without explaining tools

**Tasks:**
- [ ] Read and analyze source rule file
- [ ] Create `.agents/skills/project-structure-rules/` directory
- [ ] Extract design principles as constraints
- [ ] Convert VM/lifecycle patterns to rules
- [ ] Maintain project structure knowledge

**Verify:**
- [ ] Design principles converted to NEVER/ALWAYS rules
- [ ] Architecture patterns preserved
- [ ] VM lifecycle knowledge maintained
- [ ] Trigger words: project structure, vm lifecycle, architecture

**Rollback:**
Remove skill directory and update todo list.

## Milestone 3: ansible-conventions Translation
_Self-contained._
Translate `.cursor/rules/ansible-conventions.mdc` to `.agents/skills/ansible-conventions/SKILL.md`.

**Implementation pattern:**
- Focus on task structure and module usage rules
- Extract OpenWrt/BusyBox constraints
- Convert two-phase patterns to implementation guidance

**Tasks:**
- [ ] Read and analyze source rule file
- [ ] Create `.agents/skills/ansible-conventions/` directory
- [ ] Extract module usage and naming conventions
- [ ] Convert OpenWrt constraints to rules
- [ ] Extract two-phase restart pattern

**Verify:**
- [ ] FQCN and module usage rules clearly stated
- [ ] OpenWrt/BusyBox constraints captured
- [ ] Task structure guidelines preserved
- [ ] Trigger words: ansible, modules, openwrt, conventions

**Rollback:**
Remove skill directory and update todo list.
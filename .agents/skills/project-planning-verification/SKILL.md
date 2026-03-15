---
name: project-planning-verification
description: Project milestone verification and rollback patterns. Use when designing verify sections, rollback procedures, or testing strategies for project milestones.
---

# Project Verification & Rollback Rules

## Verification Requirements

1. NEVER defer all testing to a final milestone. Each milestone owns its own assertions.

2. **Verify sections** must include assertions to add to molecule scenarios, proving the milestone completed successfully.

3. Every feature milestone MUST include an **implementation pattern** note specifying: which task file to create, which plays to add to `site.yml`, which tags to use, and which molecule scenario to create. NEVER leave "how it integrates" as an open question.

4. Feature milestones MUST reference the relevant skills by name so implementers know which skills to load.

## Rollback Completeness Requirements

5. Rollback MUST fully restore the baseline state, including auth credentials and connection methods. If a milestone changes how Ansible connects to a target (e.g., password → key auth), the rollback MUST reverse the connection method too.

6. When a milestone changes the auth/connection method for a dynamic group, the plan MUST specify how subsequent plays and per-feature scenarios detect and adapt to the new auth method.

7. Every rollback section must undo EVERYTHING the milestone changed. Check: UCI config, packages, files, auth state, cron jobs, service enablement. A partial rollback leaves the system in an undefined state.

## Rollback Pattern Requirements

8. Per the project's "Bake, don't configure at runtime" principle: every package belongs in the image build, NOT at runtime. If a plan proposes `opkg install` or `apt install` during converge, reject it. Configure roles only do host-specific topology changes.

9. Rollback tags must be clearly defined. Use `--tags <feature>-rollback` pattern for consistent rollback execution.

## Testing Strategy Requirements

10. Every plan MUST include a **Testing Strategy** section with:
    - (a) parallelism in `molecule/default`
    - (b) per-feature scenario hierarchy
    - (c) day-to-day workflow (bash commands)
    - (d) teardown table showing what each scenario creates and destroys and its baseline impact.

11. Before execution, ALWAYS run the plan through the **Plan Review Checklist** against all referenced skills. Previous bug: the OpenWrt router plan had two critical issues (SSH auth transition, dynamic group persistence) that would have broken implementation — both caught only by cross-referencing skills during review.

## Secret Management in Verification

12. When a milestone generates secrets (keys, tokens, PSKs):
    - Document which env vars are auto-generated and which require user input
    - Specify the generated file: `test.env.generated` (test) or `.env.generated` (production)
    - Include a verify assertion checking the generated file exists and contains the expected keys
    - Include cleanup of the generated file in rollback
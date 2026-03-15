---
name: use-idle-time
description: Use wait time during test runs and long commands productively for documentation updates, code review, and architecture validation instead of just polling.
---

# Use Idle Time Productively

Use when running long-running commands (`molecule test`, `molecule converge`, `ansible-playbook` on hardware) to maximize productivity during wait periods.

## Rules

1. NEVER block on polling alone - start productive work immediately after backgrounding
2. ALWAYS tell the user what you're reviewing while waiting for context
3. NEVER make code changes to files that the running test depends on
4. NEVER start a second molecule run - only one can run at a time
5. NEVER forget to check on the test - interleave status checks every 60-120 seconds
6. ALWAYS prioritize fixing test failures over finishing review work

## Patterns

Priority order during idle time:

```bash
# When molecule test starts (~4-5 min), work in this order:

1. Review and update architecture docs (docs/architecture/)
2. Review and update skills (.agents/skills/)
3. Review and update rules (.cursor/rules/)
4. Code review against original intent (compare to project plan)
5. General code cleanliness (scan for dead code, naming issues)
```

Interleaved status checks:

```bash
# While reviewing docs during molecule test:
# Every 60-120 seconds:
# - Check test status
# - Continue productive work if still running
# - Stop and fix if test fails
```

Long command patterns:

```bash
# Most common triggers:
molecule test           # ~4-5 minutes
molecule converge       # ~3-4 minutes  
ansible-playbook        # ~2-5 minutes on hardware

# Use this time to review 2-3 files maximum
```

## Anti-patterns

NEVER explain what idle time is in productive time rules
NEVER just poll without doing productive work
NEVER ignore failing tests while doing review work
NEVER make changes to active test dependencies
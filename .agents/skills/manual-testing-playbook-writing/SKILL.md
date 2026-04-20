---
name: manual-testing-playbook-writing
description: How to write comprehensive manual testing playbooks. Use when creating manual test procedures for new features, writing playbook sections in docs/manual-testing-playbooks.md, or when a project plan includes Step 6 manual testing.
---

# Manual Testing Playbook Writing

## When to trigger

Any project plan that includes Standard Work Cycle Step 6 (manual testing)
MUST produce a playbook in `docs/manual-testing-playbooks.md` as a plan
deliverable. This skill governs HOW to write that playbook.

## Rules

1. NEVER defer playbook writing to implementation. The playbook is written
   DURING plan creation and committed alongside the project plan. The
   implementation milestone EXECUTES the playbook — it does not CREATE it.

2. NEVER duplicate playbook content in the project plan. The plan references
   the playbook by file path and section number. One source of truth.

3. NEVER use "or" when listing features to test. "Test App A or App B" means
   only one gets tested. Enumerate and test EVERY feature individually.

4. NEVER write vague expected outcomes. "Verify it works" is not an outcome.
   "KasmVNC iframe shows the Jellyfin library, NOT the kiosk hub" is.

5. ALWAYS update `.agents/skills/webui-manual-testing/SKILL.md` to list the
   new playbook immediately — not deferred to a later milestone.

## Exhaustive feature enumeration

Before writing test steps, inventory EVERY testable surface in the feature.
Read the source code — `data.py` constants, hub service lists, nav items,
route registrations, page modules. Do not rely on memory or the user's
description.

Categorize by interaction type, then test EVERY item in each category:

| Category | How it works | What to verify |
|----------|-------------|----------------|
| **Screen takeover** | Stops current app, launches VM/CT | Launch confirmation → takeover → interact → exit → return to hub → still connected |
| **External web UI** | Opens in `/view` iframe with nav bar | Viewer loads → iframe content renders → interact inside iframe → back to hub |
| **Internal page** | Navigates within the app | Page loads → content renders → interact → back to previous page |
| **API action** | Button triggers backend operation | Click → status feedback → verify real effect on infrastructure |

If a hub has 15 services, the playbook tests all 15. If a dashboard has
6 host cards, test all 6. Partial coverage is not coverage.

## Playbook section structure

```markdown
### N.X Section title

<1-2 line description of what this section tests>

1. Specific action (what to click, what command to run)
2. Specific expected outcome (what appears, NOT "verify it loads")
3. Specific interaction (click inside the app, navigate, type)
4. Specific exit (how to leave — each app has different exit UX)
5. Verify connection/state is preserved after exit
```

Per-app steps within a section use bold headers:

```markdown
**App Name (context):**

1. Step with specific expected outcome
2. ...
```

## Prerequisites section

ALWAYS include infrastructure setup the tester needs BEFORE the test:

- SSH tunnel commands (with exact `ssh -L` syntax) for every port the
  browser must reach. Do not assume the tester is on the fleet LAN.
- Service health checks (systemd status commands) to run before proceeding.
- References to earlier playbook tunnels that must still be active.
- Environment setup (`source test.env`, virtualenv activation).

## Host-awareness

Apps live on specific hosts. The playbook MUST specify which host to VNC
into / connect to for each test. Do not write "launch Kodi from the hub"
when Kodi only exists on home's kiosk. State "VNC into home's kiosk"
explicitly. If testing a host without an app, verify the tile shows
"Not available" — that IS a test.

## Error and edge case section

Every playbook MUST end with error/edge case testing:

- Invalid/nonexistent targets (bad URLs, missing nodes)
- Service failure (stop a service, verify degraded UI state)
- Browser resize (responsive layout at multiple widths)
- Connection loss and reconnection

## Reference pattern (in project plans)

The project plan's manual testing milestone references the playbook:

```markdown
- [ ] **EXECUTE Playbook N, ALL sections (N.1–N.X)** against the fully
  converged system. Run every command. Click every button. Verify every
  expected outcome. No section may be skipped.
```

NEVER say "walkthrough," "review," or "verify playbook exists." The verb
is EXECUTE. The milestone is hands-on hardware testing, not a read-through.

## Previous bugs

- (2026-04-12): Playbook said "Launch Desktop or Jellyfin" — "or" meant
  only one was tested. Kodi was never mentioned. Internal pages and
  external web UIs were skipped entirely. Fix: enumerate all 15 hub
  services, test each one individually.
- (2026-04-12, repeated twice): Agent described playbook content in the
  project plan but never wrote it into `docs/manual-testing-playbooks.md`.
  User had to ask twice. Fix: playbook is written during plan creation.
- (2026-04-12): Plan said "verify Playbook 13 exists" — checking file
  existence, not execution. Fix: milestone says "EXECUTE all sections."

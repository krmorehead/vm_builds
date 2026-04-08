---
name: webui-ux-principles
description: UX design and maintenance principles for building human-intuitive web UIs. Use when designing new pages, reviewing UI changes, fixing layout issues, choosing icons/colors, or when the user asks about UX quality.
---

# Web UI UX Principles

## Context

The web UIs are used by real operators managing a fleet of Proxmox hosts.
Operators scan dashboards quickly, act on alerts, and need to trust what
they see. Every color, icon, label, and layout choice either builds or
erodes that trust. A red dot on a healthy host wastes investigation time.
A confusing button label causes the wrong action.

## Rule 1: Color is a language — use it consistently

Colors carry meaning. Users learn the mapping once and apply it everywhere.
Breaking the mapping forces users to re-evaluate every status indicator.

```
Green  = good, running, healthy, reachable, success
Orange = attention needed, degraded, stale, unconfigured, limitation
Red    = bad, error, failure, crashed, critical alert
Grey   = neutral, unknown, no data, inactive, stopped (intentional)
Blue   = informational, first-time setup, neutral action
```

### Hard rules

1. NEVER use red for non-error states. "No data," "stopped," "not configured,"
   "no WoL support" are NOT errors. Use grey or orange.
2. NEVER use green for states that haven't been confirmed. A host that hasn't
   heartbeated yet is unknown (grey), not healthy (green).
3. ALWAYS use the same color for the same state across all pages. If "offline"
   is grey on the dashboard, it MUST be grey on the detail page.
4. NEVER invent new color meanings. If you need a fifth state, discuss it
   first. Every new color dilutes the existing vocabulary.

### Why this matters

Previous bug: "No WoL" badges, stopped containers, disconnected mesh nodes,
and "no data" states all used red. Operators opened investigation tickets
for hosts that were functioning correctly. A systematic audit replaced all
of them with grey (inactive) or orange (limitation).

## Rule 2: Icons must be instantly recognizable

An icon's job is to tell the user what something IS before they read the
label. If the icon requires thought, it's wrong.

### Hard rules

1. NEVER use abstract/metaphorical icons when a literal one exists.
   `router` for router. `hub` for mesh. `swap_horiz` for bridge.
2. NEVER reuse the same icon for different nav items. Users navigate by
   icon shape — duplicates cause misclicks.
3. NEVER use an icon that visually resembles something it's not. The
   `monitoring` icon looks like a desktop computer — don't use it for
   fleet/network views.
4. ALWAYS test icons at 24px. If you can't identify it at that size
   without the label, pick a different icon.

### Previous bugs

- `monitoring` icon was used for "View Fleet" button — users thought it
  was a "Desktop" button. Changed to `lan`.
- `dns` was used for both "Hosts" and "Containers" nav items. Changed
  Containers to `view_in_ar`.

## Rule 3: Labels speak human, not machine

Every piece of text visible to the user must be understandable by someone
who has never seen the source code.

### Hard rules

1. NEVER show raw exit codes, return codes, PIDs, or internal identifiers.
   Map them to descriptions: exit code 2 → "host unreachable or task failed."
2. NEVER show JSON, stack traces, or raw API responses in the UI.
3. NEVER use technical jargon when a plain word exists. "Deploy" not
   "converge." "Offline" not "unreachable=1."
4. ALWAYS include units: "85% disk", "3.2 GB free", "4h 23m uptime" — not
   "85", "3.2", "15732".
5. Button labels describe the ACTION: "Start Deploy", "Add Host", "View Fleet."
   Not "Submit", "Go", "OK."

### Previous bugs

- "rc=2" in deploy badges — users guessed "release candidate 2."
  Fixed with `exit_code_label()`.
- "100/100" for health — meaningless without context. Changed to
  percentage with label.

## Rule 4: Layout must survive the browser

Users resize windows, use tablets, split screens. The UI must work at
every reasonable width without manual adjustment.

### Hard rules

1. ALWAYS test at 1024px, 768px, and full-width. No horizontal scrolling
   at 1024px.
2. NEVER use fixed pixel widths for containers. Use `w-full`, `max-w-*`,
   `flex-wrap`, and responsive grid classes.
3. NEVER let buttons overlap or escape their parent container. If buttons
   don't fit, wrap them to the next line.
4. ALWAYS use NiceGUI/Quasar's layout primitives (`ui.row`, `ui.column`,
   `ui.card`, `ui.expansion`) instead of custom CSS positioning.
5. Cards in a grid MUST use `flex-wrap` so they reflow at narrow widths
   instead of overflowing.

### Previous bugs

- "View Fleet" icon button overlapped "Build Images" and "View Fleet"
  text buttons at narrow widths. Looked terrible at any browser size.

## Rule 5: Reduce clicks to common actions

The most frequent workflows must be the shortest. Every extra click is
friction that accumulates over hundreds of uses.

### Decision framework

```
Frequency          | Max clicks to complete
Most-used action   | 1 click (visible on dashboard)
Daily action       | 2 clicks (one nav + one action)
Weekly action      | 3 clicks (nav + page + action)
Setup/config       | Any depth (done rarely)
```

### Hard rules

1. The dashboard MUST show fleet health at a glance — no clicks needed
   to know if something is wrong.
2. Host details MUST be one click from the fleet view (click card →
   detail page).
3. Adding a new host MUST be accessible from the fleet nodes page
   directly, not buried in settings.
4. NEVER put the primary workflow behind a menu or dropdown. If the main
   use case is "check fleet health and add/update hosts," those actions
   must be front and center.

### Previous bugs

- "Full Deploy" was the primary dashboard action, but operators rarely
  deploy to all hosts. The main workflow is per-host management. Fixed
  by making host cards and fleet view the primary entry point.

## Rule 6: States must be exhaustive and honest

Every possible state must have a visual representation. Missing states
create confusion ("why is this blank?").

### The state inventory

For any entity (host, container, service), define ALL states:

```
healthy + heartbeating   → green dot, metrics shown
reachable + no heartbeat → orange dot, "Online — no heartbeat"
unreachable              → grey dot, "Offline"
error / crashed          → red dot, error description shown
unknown / no data        → grey dot, "—" for metrics
```

### Hard rules

1. NEVER leave a cell, badge, or indicator blank. Show "—" or "unknown"
   for missing data. Blank space looks like a rendering bug.
2. ALWAYS show degraded states honestly. "Reachable but no heartbeat" is
   orange (degraded), not green (healthy) and not red (error).
3. NEVER hide error details. If a host is unhealthy, show WHY — the
   `.errors` list from the domain object.
4. When telemetry is unavailable, show metrics as "—" or "0%" with
   muted styling, not hidden. The user needs to know the data is absent,
   not that the gauge is broken.

### Previous bugs

- Fleet card showed "No nodes registered" when the API server wasn't
  running. Hosts existed (in env), just weren't heartbeating. Fixed by
  showing configured hosts with "waiting for heartbeats" message.

## Rule 7: CSS must be modular and reusable

Custom one-off styles create visual drift between pages. A design system
means every page looks like it belongs to the same application.

### Hard rules

1. NEVER hardcode hex colors in page files. Use `theme.COLOR_SUCCESS`,
   `theme.COLOR_WARNING`, etc.
2. NEVER write one-off CSS classes. If a pattern appears twice, extract
   it to `theme.py` as a global class.
3. ALWAYS use the design system CSS classes: `.action-btn`, `.outline-btn`,
   `.subtle-btn`, `.t-primary`, `.t-success`, `.t-error`, `.t-disabled`,
   `.mono-val`, `.metric-label`, `.card-title`, `.section-label`, `.sep-accent`.
4. NEVER mix styling approaches. Use either Quasar utility classes OR
   custom theme classes, not both inline on the same element.
5. When adding a new visual pattern, add it to `theme.py` with a semantic
   name, not to the page file with inline styles.

### Previous bugs

- Hardcoded `#4ade80` in mesh.py for batman status. Three other pages
  used different greens for the same meaning. Fixed by using
  `theme.COLOR_SUCCESS`.
- Signal quality colors hardcoded in bridge.py with a custom gradient.
  Fixed by adding `theme.signal_color()`.

## Rule 8: Information hierarchy — most important first

Users scan top-to-bottom, left-to-right. The most critical information
must be in the visual priority position.

### Hard rules

1. Dashboard: Fleet health score → online/offline counts → critical
   alerts → action buttons. In that order.
2. Host detail: Status + name + IP → resources (disk/memory) → guests →
   deploy history → network → extensions. Most-actionable first.
3. Alerts and errors ABOVE normal status. A critical alert buried below
   a healthy-looking summary will be missed.
4. NEVER show raw data dumps (log lines, full JSON) at the top level.
   Summarize first, expand for details on demand.

## Rule 9: Transitions and navigation must be predictable

Users build a mental model of the app's structure. Breaking that model
(unexpected navigation, changing page content without visual cue) causes
disorientation.

### Hard rules

1. Clicking a card or list item → detail view. ALWAYS.
2. Back button → previous page. ALWAYS.
3. Sidebar navigation → corresponding page. ALWAYS. No special behavior
   on hover, no context-dependent destinations.
4. NEVER change page content silently. If data refreshes, show a loading
   indicator or timestamp update.
5. Form submissions show feedback: success message, error message, or
   loading state. NEVER leave the user wondering if their click worked.

## Maintenance: the UX review loop

After any batch of UI changes:

1. **Human eye pass**: Open each page, look at it as if you've never seen
   it. Does anything look wrong? Are colors consistent? Do labels make
   sense without context?
2. **Resize test**: Drag the browser to 1024px, 768px, mobile. Do cards
   wrap cleanly? Do buttons still fit? Are tables scrollable?
3. **State completeness**: For every entity shown, verify all states
   are represented. What does an empty fleet look like? What does a
   single offline host look like?
4. **Workflow timing**: How many clicks to do the most common task?
   Can you reduce it?
5. **Update skills**: If the review found issues, add them to the
   "Previous bugs" section of this skill and `webui-design-system`.

## Anti-patterns

```
# BAD: "I'll fix the UX manually via SSH"
The fix must be in the automation. Manual deployment is not maintaining UX.

# BAD: "It works at my screen size"
Test at 3+ widths. Your screen is not the only screen.

# BAD: "Red stands out, so I'll use it for this badge"
Red means error. If it's not an error, don't use red. Stand out with
position, size, or a non-error color.

# BAD: "The user can figure out what rc=2 means"
They can't. They won't. Map it to human text.

# BAD: "I'll add the icon later"
An icon-less nav item or a wrong icon ships confusion to every user
until it's fixed. Get it right the first time.
```

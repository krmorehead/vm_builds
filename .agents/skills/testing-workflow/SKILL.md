---
name: testing-workflow
description: Test-first development, TDD workflow, molecule testing patterns, and diagnostic approaches for Ansible playbooks and infrastructure automation.
---

# Testing Workflow and TDD Patterns

Use when running molecule tests, implementing TDD workflow, diagnosing test failures, or establishing testing baselines for Ansible playbooks.

## Rules

**Standard Work Cycle (MANDATORY for any image/role change):**
0. ALWAYS follow the 6-step standard work cycle when changing build-images.sh or configure roles:
   Step 1: Update build-images.sh → Step 2: Build images IN PARALLEL on test units (REQUIRED, NOT OPTIONAL) →
   Step 3: Write tests/playbook updates while images build → Step 4: Run E2E molecule test →
   Step 5: Code review while E2E runs → Step 6: Manual playbook verification after E2E passes
1. NEVER skip Step 2 (image rebuild). If you modify build-images.sh and run molecule test without rebuilding, the old image lacks baked content and configure roles WILL fail
2. NEVER add "legacy image fallback" code in configure roles. The image is the source of truth — rebuild it
3. You have 6 Proxmox hosts available — build images IN PARALLEL across them: `build-images.sh --host <ip> --only <target>` on each host simultaneously
4. The `proxmox_lxc` version-mismatch system auto-rebuilds containers from fresh images. Fresh image + version bump = automatic container recreation

**Fail-Fast Image Iteration (CRITICAL — this is why we have 6 hosts):**
5. When an image has a problem, FIX THAT ONE IMAGE and rebuild it. Do NOT run a full E2E to discover what broke — you already know the image is broken
6. Build a single image with `build-images.sh --host <ip> --only <target>` (~2-3 min). Test it with its per-feature molecule scenario (`molecule test -s <type>-lxc`). If it fails, fix and rebuild. FAST loop
7. Once all individual images pass their per-feature tests, THEN run the full E2E (`molecule test`)
8. The full E2E should be FAST because all images are already cached on all hosts from Step 2. If E2E is slow, something is wrong — probably re-uploading images or re-installing packages at runtime
9. NEVER run `molecule test` (full E2E with cleanup) as the primary iteration loop. Use `molecule converge && molecule verify` for fast iteration. Full `molecule test` is for clean-state proof only
10. Treat Proxmox hosts as bakeable targets. Host-level infrastructure (socat, iptables rules, systemd units) is part of the image/deploy pipeline, not hand-configured. If a host needs a package or service, bake it or deploy it via Ansible — same as containers

**TDD and Test-First Development:**
5. ALWAYS reproduce production bugs on test machine first using `molecule test` or `molecule converge`
6. NEVER iterate on production when test machine is available  
7. ALWAYS write verify assertions before implementing features (TDD)
8. NEVER consider a fix complete until `molecule test` passes end-to-end

**Environment and Setup Validation:**
9. ALWAYS validate environment before ANY molecule commands: `set -a && source test.env && set +a`
10. ALWAYS test SSH and Ansible connectivity before running molecule: `ansible home -m ping`
11. ALWAYS run lint checks (`ansible-lint && yamllint .`) after ANY code changes

**Proactive Testing Triggers:**
12. Test IMMEDIATELY when creating new service roles or container types
13. Test IMMEDIATELY when adding Docker-in-LXC or container-specific patterns  
14. Test IMMEDIATELY when you see variable scoping issues or undefined variable errors
15. NEVER proceed with development when environment validation fails

**Manual Testing Prerequisites:**
16. NEVER start manual testing unless `molecule test` has completed and ALL 6 hosts are on the 10.10.10.x LAN with ALL containers deployed
17. NEVER rationalize "No route to host" during manual testing as "expected in pre-mesh" — if you see it, converge first
18. The system ALWAYS ends `molecule test` in a pristine state. Manual testing starts from that pristine state. Build first, test second.

**Controller VPN and localhost plays:**
15. Controller VPN play in site.yml targets `localhost` with `connection: local`. It uses explicit `sudo` calls (not `become: true`) because `setup.sh` installs a targeted NOPASSWD sudoers entry
16. `become: true` on localhost requires an interactive password or `ANSIBLE_BECOME_PASSWORD` — both break non-interactive molecule runs. NEVER use `become: true` on localhost for scoped operations
17. The controller is a bakeable target — wireguard-tools installed by `setup.sh`, wg0 configured by site.yml, torn down by cleanup.yml
18. Verify assertions for controller VPN use explicit `sudo wg show wg0` (not `become: true`)

**Cleanup and Safety:**
19. NEVER use blanket cleanup that destroys all resources - use explicit VMIDs
20. NEVER add graceful degradation for expected hardware (iGPU, WiFi, VT-d)

**Failure Diagnosis:**
14. ALWAYS check dmesg first when diagnosing test failures
15. NEVER assume ICMP working means TCP works - test with actual protocols

**Recent Testing Lessons:**
16. ALWAYS validate missing variables in role defaults - undefined variables in Jinja2 templates cause test failures
17. ALWAYS test environment setup BEFORE running molecule (dependency installation, template availability)
18. ALWAYS use default values for hardware-dependent variables (igpu_render_device) to prevent test failures
19. ALWAYS validate generated env file paths exist before relying on dynamic network configuration

**Scalable UI Testing (NiceGUI web UI):**
20. NEVER hardcode UI strings in tests. Import `Routes`, `PageTitles`, `Labels`, `ApiRoutes` from `scripts.webui.data`
21. ALWAYS derive test data from production data structures (`data.DISPLAY_APPS`, `data.HUB_SERVICES`, `data.NAV_SECTIONS`) instead of repeating labels
22. When adding a new page/button/label: add the constant to `data.py` first, then use it in both the page module and tests
23. Previous bug: 26 tests broke when hub section names changed because tests hardcoded strings. Now all derive from `data.HUB_SERVICES` sections

## Patterns

TDD iteration pattern:

```yaml
# 1. Write/update verify assertion first
- name: Verify service is running
  ansible.builtin.assert:
    that: service_status.rc == 0

# 2. Run molecule test - assertion should fail
molecule test

# 3. Implement fix in role
# roles/service_configure/tasks/main.yml

# 4. Run molecule test - assertion should pass
molecule test
```

Converge vs test workflow:

```bash
# Day-to-day iteration (preserves baseline, FAST)
molecule converge && molecule verify

# Per-image iteration (fix ONE broken image, FASTEST)
./build-images.sh --host $PRIMARY_HOST --only pihole  # ~2-3 min
molecule test -s pihole-lxc                            # per-feature only

# Full E2E clean-state validation (CI, final proof, LAST STEP)
# Only run after all images pass individual tests
molecule test
```

Fail-fast iteration priority:

```
Fix image → rebuild 1 image (~2 min) → per-feature test (~5 min)
         ↑ loop here until image works ↓
         ↓ then run full E2E once      ↓
```

Diagnostic order:

```yaml
# When test fails, follow this order:
1. Read full error context (grep for FAILED, fatal:, UNREACHABLE)
2. Check dmesg on target (kernel-level errors)
3. Check interface/bridge state (ip addr, ip route)
4. Check firewall state (zone bindings, nftables)
5. Test actual protocols (not just ping)
6. Add permanent diagnostics
```

## Early Bug Detection Patterns

**Environment Issues (Catch Early):**
- "Could not resolve hostname none" → Environment variables not exported
- SSH connection failures → PRIMARY_HOST or HOME_API_TOKEN issues
- "ansible_date_time is undefined" → Facts not gathered, wrong host context

**Container/Docker Issues (Catch Early):**
- "pct: command not found" → Running on container instead of Proxmox host
- "proxmox_vmid is undefined" → Variable scoping issues, use `homeassistant_ct_id`
- Docker daemon access fails → Wrong execution context for `pct exec` commands

**File Deployment Issues (Catch Early):**
- Template deployment fails in containers → Use shell commands with `pct exec`
- "Recursive loop detected" → Self-referencing variables in defaults/main.yml
- YAML parsing errors → Avoid Jinja2 templates for container file writing

**Testing Strategy:**
- Run `molecule converge` after ANY container/Docker code changes
- Run `molecule verify` immediately when assertions fail
- Test with actual protocol (HTTP, Docker commands) not just connectivity

## Anti-fake-test doctrine

**The cardinal rule: tests must test REAL things.**

A test that mocks the thing it claims to test is a lie. It provides false
confidence while real problems go undetected. The test suite is an early
warning system for infrastructure problems — mocking the warnings away
defeats the entire purpose.

**The ONLY time mocking is justified — launching an irreversible external process:**
- `subprocess.run` for `ansible-playbook` — would modify real infrastructure
- `subprocess.run` for `build-images.sh` — would rebuild images for ~15 min
- `subprocess.Popen` for API server startup — would spawn a real background server
- `asyncio.create_subprocess_exec` for deploy/build — same as subprocess.run

That's it. NOTHING ELSE qualifies. If the operation is recoverable, fast,
or runs against infrastructure you control — run it for real.

**The test for whether a mock is justified:**
Ask: "Would running this for real cause IRREVERSIBLE side effects that take
more than 30 seconds to undo?" If no, run the real thing. Laziness is not
a justification.

**Explicitly NOT justified (remove these mocks):**
- `subprocess.run` for SSH — SSH to real hosts. If it fails, the test should fail.
- `subprocess.run` for `wg show`, `docker info`, `pihole` — test the real binary.
  If the binary doesn't exist, the function should return None naturally.
- `probe_host` against hosts you control — real TCP probe. Period.
- MagicMock for objects you can construct — write a real class with `.push()`.
  If the object is trivial (a list wrapper), build a real one, don't mock it.
- `monkeypatch(subprocess.run)` to "test the timeout path" — use a real
  unreachable IP (10.254.254.254). Real timeouts test real timeout handling.
- `monkeypatch(subprocess.run)` to "test the restart path" — restart the
  real service on the real host. Callhome restarts in seconds. That's not
  an irreversible side effect. Callhome heartbeats are a PREREQUISITE for
  the entire test suite — all 6 hosts MUST be heartbeating or the test
  fails. You can NEVER justify NOT calling home.
- "Can't construct a NiceGUI element outside UI context" — if the function
  takes a generic interface (.text, .style), build a real object. Don't
  reach for MagicMock because you're too lazy to write 3 lines of Python.
- `monkeypatch.setenv/delenv` for env vars — this IS acceptable. Controlling
  environment variables is setting test inputs, not mocking behavior.

**Mandatory: every mock MUST have an inline comment justifying WHY:**
Every `patch()` or `monkeypatch` call MUST include a comment explaining:
1. WHY this specific mock is necessary (what IRREVERSIBLE side effect it prevents)
2. HOW the test still genuinely validates the feature despite the mock
If you cannot write both sentences, the mock is not justified — remove it
and test the real thing.

Previous bugs:
- batman API tests mocked `heartbeat._ssh_exec` — the exact SSH call that
  triggers batman on real nodes. Tests passed while batman was broken.
- callhome collector tests mocked `subprocess.run` to test "binary not found"
  when the real binary was either installed (test the real behavior) or not
  installed (function returns None naturally). The mock was pure laziness.
- kickstart tests used fake hosts (10.0.0.1) with fake subprocess.run to
  test restart logic. Real hosts exist at known IPs with real containers.
  The mock prevented testing whether kickstart actually works.

**When mocking is NEVER justified:**
- `probe_host` against hosts you control — if a host is down, the test MUST fail
- Network connectivity to your own infrastructure — that's what tests ARE FOR
- Hardware detection on machines you own — if iGPU is missing, you need to know
- Reading real config files (inventory, test.env) — parse the real thing
- SSH commands to your own fleet — if the command fails, the test MUST fail
- API endpoints that trigger real operations on real nodes — test the REAL operation
- subprocess.run for any recoverable operation — restart a service, probe a port,
  run a status command. If it takes < 30s and is undoable, run it for real.
- MagicMock for any object you could construct in < 5 lines of real Python

**How to tell if a test is fake:**
1. Does it mock the very thing it claims to verify? → FAKE
2. Would it pass identically if the infrastructure were on fire? → FAKE
3. Does it test string patterns in YAML files instead of running the code? → FAKE
4. Does the test name say "verify X works" but never actually runs X? → FAKE

**Real test examples (from this project):**
- `TestInfrastructureHealth.test_ai_host_reachable` — TCP probes 192.168.86.220.
  When ai crashed, this test FAILED with an actionable message. That's real.
- `TestWolHostExclusion.test_ai_not_in_wol_script` — Parses the actual wol.sh
  and asserts ai is excluded. A static linter, but it checks REAL file content
  against REAL inventory data. That's real.
- `TestNoUnguardedGPUDriverUnload` — Scans all YAML files for `modprobe -r`
  without VGA count guards. Catches the exact class of bug that crashed ai. Real.

**Fake test examples (deleted from this project):**
- `TestResolveProxmoxHost.test_returns_primary_when_reachable` — Mocked
  `probe_host` to always return True, then asserted resolve returned the IP.
  Trivially obvious. Meanwhile all 3 WAN hosts were unreachable and this
  test passed happily. DELETED.
- `TestJellyfinLxcRole.test_task_uses_fqcn` — Read YAML files and checked
  if strings contained `ansible.builtin.`. Never ran Ansible. A linter
  pretending to be a test. DELETED.
- `TestBuildImages.test_mesh_target_builds` — Checked if bash script contained
  the string "mesh" in a case statement. Never ran the script. DELETED.

**Previous bug:** `TestResolveProxmoxHost` (5 tests) monkeypatched `probe_host`
with fake IPs. All 5 passed while `ai` was crashed and unreachable for an
entire test cycle. The mocked tests gave false confidence that host resolution
was "tested" when no real probing ever happened. Replaced with
`TestInfrastructureHealth` that probes real hosts from test.env.

## Unreachable host = SHOW STOPPER

When ANY host in the fleet is unreachable OR SSH authentication fails:

1. **STOP ALL WORK.** Do not continue development. Do not run more tests.
   Do NOT build other hosts. Do NOT "skip this host and continue."
2. **Investigate cause.** Check terminal history for destructive operations.
3. **Report severity.** For `wol_capable: false` hosts (ai), physical access
   is required. Say this explicitly to the user.
4. **Do NOT validate features against the wrong host.** If `ai` runs Sunshine
   and `ai` is down, then Moonlight verification is impossible regardless of
   what passes on `home`.
5. **NEVER say "pre-existing" or "not caused by our changes."** Every
   unreachable host was caused by something. Find it.

### Mandatory fleet connectivity checks

Run SSH to ALL hosts at these checkpoints — no exceptions:
1. **Session start** — before beginning any work
2. **After every build/deploy/converge** — ALL hosts, not just the target
3. **Before proceeding to next host** — in sequential multi-host operations
4. **Every 30 minutes** — during long sessions

What counts as a failure (ALL are SHOW STOPPERS):
- SSH "Permission denied" (lost management access)
- SSH connection refused or timed out (host or sshd down)
- Proxmox API (port 8006) connection refused
- SuperManager showing 0% disk AND 0% memory (Manager SSH failing)
- Ansible `unreachable=1` in any play recap

### Previous catastrophes

1. (2026-03-23): Agent dismissed ai unreachable THREE TIMES in one session.
   Ran 4 hours of tests that could never validate the actual feature
   (streaming from ai). Root cause was `modprobe -r amdgpu`. Physical
   power-on required 3000 miles away.

2. (2026-04-08): ai SSH auth failed (Permission denied). Agent continued
   building mesh1, mesh2, bridge-1, bridge-2 individually over 20+ minutes
   instead of stopping immediately. User had to call out the show stopper.
   The "skip broken host, build the next one" instinct is ALWAYS WRONG.

3. (2026-04-09): ai SSH intermittently failed with "Permission denied" and
   oscillating host key fingerprints. Initially misdiagnosed as USB ethernet
   link drops. Real cause: kiosk container on bridge-1 had IP 192.168.86.220
   (same as ai's host IP) due to WAN containers sharing the household subnet.
   The offset+base+index formula was fundamentally broken for a shared broadcast
   domain. Permanently fixed by replacing with per-host NAT bridges
   (10.99.x.x via vmbr_ct) — containers can never collide with host IPs.
   Lesson: when SSH auth intermittently fails with changing host keys,
   check for IP address conflicts before investigating hardware issues.

## SuperManager timestamp timezone awareness

When verifying the heartbeat chain via `/api/nodes`, the `last_seen`
timestamps use the **SuperManager's local timezone** (e.g., PDT = UTC-7),
NOT UTC. A `last_seen` of `13:25:00` when `date` shows `01:25 PM PDT` is
CURRENT — not 7 hours stale.

ALWAYS compare timestamps against `date` output on the controller, not
against an assumed UTC reference.

Previous debugging waste (2026-04-09): ~30 minutes spent investigating
"stale" heartbeats that were actually current. The SuperManager was running
in PDT, and `13:25` was incorrectly assumed to be 7 hours behind the
`20:25 UTC` clock. The relay was working perfectly the entire time.

## Theme/UI helper test coverage

When adding new theme helpers (e.g., `status_color`, `status_dot`), ALWAYS
add corresponding unit tests in `test_webui_data.py`. Theme helpers map
semantic strings to colors — if the mapping is wrong, the entire UI has
wrong color semantics (red for healthy, grey for errors, etc.).

Previous bug: `theme.status_color` was added with mappings for `online`,
`stale`, `reachable`, `offline`, `unreachable`, `unknown`, but no unit tests
existed. During manual testing, the `unknown` status was colored red (error)
instead of grey (neutral), making normal unprobed hosts look broken. Adding
7 tests caught the mapping table error immediately.

## Anti-patterns

NEVER explain what TDD is in testing workflow rules
NEVER use graceful skip for hardware expected on every host
NEVER just poll during long-running commands - use idle time productively
NEVER add failed_when: false on connection tests (let real errors fail immediately)
NEVER debug container issues without testing on the actual host first
NEVER mock probe_host or network connectivity to hosts you control
NEVER mock SSH commands that trigger real operations on real nodes you own
NEVER write tests that check YAML file contents instead of running the code
NEVER write tests that would pass identically if the infrastructure were offline
NEVER call a test "verify X works" unless it actually exercises X end-to-end
NEVER use `patch()` or `monkeypatch` without an inline comment justifying WHY the mock exists and HOW the test still genuinely validates the feature
NEVER dismiss an unreachable host as "pre-existing" — investigate immediately
NEVER continue development with ANY host unreachable — it is a show stopper
NEVER "skip a broken host and build the next one" — ALL hosts must be healthy first
NEVER proceed to the next build without SSH-checking ALL hosts after the previous build
NEVER treat SSH "Permission denied" as less severe than EHOSTUNREACH — both = FULL STOP
NEVER run `molecule test` (full E2E) as the primary iteration loop — use `molecule converge && molecule verify` for fast iteration
NEVER run full E2E to discover a single-image problem — rebuild that one image and run its per-feature scenario
NEVER let E2E runs take 30+ minutes — if they do, images are being re-uploaded or packages installed at runtime, which means the bake pipeline is broken
NEVER treat Proxmox host config as "not our problem" — host-level systemd units, iptables rules, and packages are deployable and testable infrastructure, same as container images
NEVER use `nohup ... &` for persistent host services — deploy systemd units with Restart=always. Background processes die when SSH sessions close
NEVER use `become: true` on localhost plays — use explicit `sudo` in shell/command tasks. The NOPASSWD sudoers entry is already installed by setup.sh (wireguard-tools, wg-quick, wg, install). This is a solved problem
NEVER treat the build machine as exempt from "bake don't configure" — wireguard-tools and sudoers are already installed by setup.sh; wg0 is configured automatically by site.yml on every converge
NEVER fabricate test data (curl heartbeats, fake check-ins, simulated nodes) to populate a UI for "manual testing" — this is mocking with extra steps and proves nothing about real functionality
NEVER claim "manual testing passed" after only VIEWING pages — every interactive feature (buttons, toggles, mode switches) must be ENGAGED against real hardware
NEVER say "the fleet dashboard shows 5 nodes" when those 5 nodes were curl'd into existence 10 seconds ago — real nodes heartbeat from real kiosk_server instances
NEVER add retries to a verify task without first diagnosing WHY the check fails — retries mask root causes and waste minutes per test run
NEVER use `retries > 6` or `delay > 10` in verify — if it isn't ready in 60s it's broken, not slow
NEVER increase retry count or delay as a "fix" for a failing check — find and fix the root cause, then remove the retries
NEVER leave debug/diagnostic tasks in verify.yml after the root cause is fixed — permanent diagnostics go in converge roles
NEVER add SSH fallback paths in verify.yml — if the API check fails, the 4-tier system is broken. Fix the relay chain, don't add `pct exec` fallback
NEVER use `pct exec` to read container config when `/api/config/self` over VPN is available — the API is faster and proves the NM is functional
NEVER skip VPN connectivity verification — ALL NM API queries require the controller's wg0 tunnel. If VPN is down, verify will produce confusing failures

### API-first verification (verified 2026-04-18)

The E2E verify playbook uses the 4-tier API as the PRIMARY verification path.
Every hard failure includes investigation prompts so agents don't go off track:

- **Fleet readiness gate**: `/api/fleet/ready` — HARD FAIL if any service not heartbeating
- **Heartbeat circuit breaker**: `/api/fleet/stale` — kills the run immediately
- **Per-service health**: `/api/container/{id}/ready` — extensions prove baked content
- **Kiosk config validation**: `/api/config/self` over VPN (no `pct exec`)
- **Display service health**: `systemd_services.kiosk-display` from heartbeat
- **Baked content**: `extensions.config_files` file hashes via heartbeat
- **VPN connectivity**: Hard-fail with investigation steps if controller can't reach hub

SSH checks remain ONLY for:
- Hypervisor validation: `pct config/status` (Proxmox host-side, not container-side)
- L3 integration proofs: cross-service connectivity (logger, ping, DNS resolution)
- OpenWrt deep checks: UCI config, `iw` radio state (no HTTP API on OpenWrt VM)

### Previous catastrophe: fabricated manual test (2026-04-09)

Agent was asked to manually test the Cluster Manager GUI. Instead of
starting real kiosk_server instances on the 6 physical test nodes and
letting them heartbeat naturally, the agent:
1. Started kiosk_server on localhost
2. Used `curl -X POST /api/checkin` to fabricate 3 (then 5) fake nodes
3. Viewed the fleet dashboard showing the fabricated data
4. Declared "manual testing complete — all features working as designed"
5. Never clicked batman toggle, never engaged any interactive feature
6. Never used real infrastructure for anything

This is IDENTICAL to mocking — the dashboard looked pretty because it
was fed fake data. It proved exactly nothing about whether:
- Batman mode actually propagates through the event system to real containers
- Bridge WiFi restart actually triggers wifi_setup.sh on real bridge nodes
- The Cluster Manager can actually reach real child Managers over HTTP
- Real heartbeats from real kiosk_server instances are properly ingested

The correct approach: deploy kiosk containers on all 6 test hosts, start
real kiosk_server instances, let real heartbeats flow, then engage every
interactive feature and verify the real operation completed on real hardware.
---
name: learn-from-mistakes
description: Update skills and rules when encountering new issues to prevent recurrence. Includes hard-fail patterns, code custodianship, and mandatory testing requirements.
---

# Learn from Mistakes

Use when debugging failures, implementing workarounds, or encountering unexpected errors to prevent recurrence and maintain code quality standards.

## Rules

1. ALWAYS fix immediate problem first - don't stop to write skills mid-debug
2. ALWAYS check if existing skills should be updated after fixing issues
3. NEVER add graceful degradation for expected hardware (iGPU, WiFi, IOMMU)
4. ALWAYS do credentials safety audit after completing features
5. ALWAYS follow the 6-step standard work cycle for image/role changes
6. NEVER skip image rebuild after changing build-images.sh
7. NEVER add "legacy image fallback" code in configure roles — rebuild the image
8. ALWAYS build images IN PARALLEL across 6 hosts (use --host <ip> --only <target>)
9. ALWAYS run full test suite after code changes
10. NEVER consider task complete until `molecule test` passes
11. ALWAYS generalize ad-hoc diagnostics and make them permanent
12. NEVER commit or present changes as complete without passing tests

## Patterns

Skills update process:

```bash
# When encountering new issue:
1. Fix immediate problem first
2. Search existing .agents/skills/ and AGENTS.md files
3. If lesson is new, add to relevant skill
4. Use NEVER/ALWAYS constraints, not suggestions
5. Include one-line "what went wrong" before rule
```

Code custodianship audit:

```bash
# After completing feature/fix:
1. Grep cleanup playbooks for authorized_keys, pveum, token, .ssh
2. Pipefail audit: grep shell tasks with | for set -o pipefail
3. Cleanup parity: diff molecule/*/cleanup.yml and playbooks/cleanup.yml
4. Doc accuracy: verify docs/architecture/ matches actual exports
5. Verify coverage: every role in site.yml needs verify.yml assertion
6. Host safety: run pytest tests/test_host_safety.py — catches modprobe -r
   amdgpu/i915 in broad-scope plays, shutdown commands in cleanup files
7. WoL safety: run pytest tests/test_wol.py — non-WoL hosts excluded from wol.sh
```

Mandatory testing sequence:

```bash
# After ANY code change:
1. ansible-lint && yamllint .          # Syntax/style
2. molecule test                       # Full integration test
3. Update verify.yml if needed         # Add assertions
4. No untested merges                  # Tests must pass first
```

## Documentation accuracy

When changing a role's exported facts, bridge names, device paths, or connection patterns, ALWAYS update `docs/architecture/` in the same commit.

- `overview.md` role-reference diagrams MUST list the same exports — update both if you update one
- NEVER document planned/future exports as if they already exist (mark with "(future)" or omit)
- NEVER hardcode bridge names (`vmbr0`, `vmbr1`) in docs — use "WAN bridge" / "LAN bridge"

Previous bug: `overview.md` listed `gpu_pci_devices` as an export of `proxmox_pci_passthrough`, but the role only exports `wifi_pci_devices`.

## Handler conventions

Prefer `ansible.builtin.systemd` over `ansible.builtin.command: cmd: systemctl restart` for service management in handlers. Use `command` only for status checks and config validation.

## Anti-patterns

NEVER explain what mistakes are in learning rules
NEVER add graceful skip for hardware that should be present
NEVER skip testing because "it works locally"
NEVER delete ad-hoc diagnostics without making them permanent
NEVER run `modprobe -r amdgpu` in broad-scope cleanup — kernel-panics single-GPU AMD hosts
NEVER shut down or crash hosts with `wol_capable: false` — they cannot be recovered remotely
NEVER add a host to wol.sh without verifying its NIC supports Wake-on-LAN
NEVER mock `probe_host` to test infrastructure health — probe REAL hosts from test.env
NEVER write pytest tests that only check YAML string content instead of running code
NEVER write a test that passes identically when infrastructure is offline (unless testing pure Python)
NEVER name a test "verify X works" without actually exercising X
NEVER dismiss an unreachable host as "pre-existing" or "not caused by our changes"
NEVER continue development when ANY host in the fleet is unreachable
NEVER deviate from the project plan without explicit user approval
NEVER co-locate a streaming server and client on the same host
NEVER assign all services to the same node when 4 nodes are available
NEVER add retries to verify tasks without understanding WHY the check fails
NEVER increase retry count or delay as a "fix" — find the root cause
NEVER leave legacy systemd services running when deploying renamed replacements
NEVER assume LAN hosts get the same cleanup as primary hosts — they are excluded from `proxmox:!lan_hosts`

## Retries are a symptom, not a fix (CRITICAL)

When a verify task needs retries, there are ONLY two valid reasons:
1. **Known propagation delay** (heartbeat relay, DNS propagation) — document
   the expected delay and set retries to exactly cover it.
2. **Service startup time** (Docker pull, database init) — known and bounded.

Everything else is a bug. Adding retries to a failing check is like adding
painkillers instead of treating the fracture. The check still fails on every
molecule run, you just wait longer before it tells you.

**The compounding cost:** A single 6-minute retry block doesn't just waste
6 minutes once. It wastes 6 minutes on EVERY test run, for EVERY developer,
for EVERY iteration. Over a month of test cycles, that's HOURS of accumulated
waste — all to avoid spending 10 minutes diagnosing the actual root cause.

**When you see a check failing with retries:**
1. Query the fleet API first: `curl <SM_URL>/api/fleet/ready?services=<name>` and `curl <SM_URL>/api/container/<name>/ready` — this shows heartbeat status, extensions, and last-seen time.
2. If the fleet API shows the service as not ready, check the relay chain: Container → NM → CM → SM. Query each hop's `/api/nodes` endpoint.
3. For host-level issues only (not container health): check `journalctl -u kiosk-server` on the Proxmox host for NM errors, or `pct exec <vmid> -- journalctl -u callhome` for callhome agent errors.
4. Fix the root cause. Remove the retries. The ideal retry count is zero.

Previous catastrophe (2026-04-14): A legacy VNC proxy held port 6080 on
mesh1, preventing the new display proxy from starting. Instead of SSHing
in and checking `systemctl status` (would have shown 957 restarts and the
port conflict in 10 seconds), retries were added and increased from 30s
to 100s. The SM heartbeat check used 6 MINUTES of retries. The CM check
was another 6 minutes. The verify phase burned 12+ minutes of retries on
EVERY molecule test run, for MULTIPLE runs, never once succeeding. The
root cause was a one-line fix: stop the old service before starting the
new one.

## Use the 4 nodes intelligently

Different molecule scenarios assign different groups to the same host.
Each test scenario should use the topology that exercises the feature:

- **Cross-subnet streaming**: server on WAN host, client on LAN host
- **Mesh WiFi**: mesh nodes on satellite hosts, not router_nodes
- **Per-feature isolation**: only the groups needed for that feature

Previous catastrophe: Agent put Moonlight (streaming client) on home, which
also runs Sunshine (streaming server). This is physically nonsensical — you
can't stream to yourself. It also eliminated cross-subnet testing via
WireGuard VPN, which was the ENTIRE purpose of the Moonlight project.
The user explicitly said to use mesh1. The agent deviated from the plan.

## Unreachable host protocol (MANDATORY — SHOW STOPPER)

When ANY host shows `unreachable=1` in a PLAY RECAP or fails a connectivity
probe:

1. **FULL STOP.** Do not continue development. Do not run more tests. Do not
   say "pre-existing." An unreachable host is a 5-alarm emergency.
2. **Investigate the cause immediately.** Check terminal history for what ran
   on that host. Search for `modprobe -r`, `shutdown`, `poweroff`, GPU
   operations, or any destructive command that touched the host.
3. **Check if YOUR session caused it.** Cross-reference the host's last-seen
   timestamp with commands from this session and recent sessions.
4. **Report the severity to the user.** For `wol_capable: false` hosts, this
   means physical access is required. For hosts 3000 miles away, this could
   cost days of downtime. Say this explicitly.
5. **Do NOT validate features that depend on the unreachable host.** If `ai`
   is down and `ai` runs Sunshine/Doom, then Moonlight verification is
   IMPOSSIBLE. Saying "Moonlight tests pass on home" is meaningless when the
   streaming server is offline.
6. **Block the session on recovery.** The user must know that no further
   progress is possible until the host is restored.

Previous catastrophe (Moonlight session, 2026-03-23): `ai` went unreachable
from `modprobe -r amdgpu` in an earlier sunshine-vm cleanup. The agent saw
`unreachable=1` in THREE separate test runs over 4 hours and dismissed it
every time as "pre-existing, not our problem." It ran converge, verify, and
cleanup cycles that could never validate the actual feature (Moonlight
streaming from ai's Sunshine server). The entire session was wasted. `ai`
required physical power-on with no remote recovery, 3000 miles from the
operator. This dismissal pattern is the single most expensive failure mode
in this project.

Previous bug: E2E cleanup ran `modprobe -r amdgpu` on `ai` (single AMD GPU, USB ethernet). Kernel panicked, host crashed. Required physical power-on. Now caught by `tests/test_host_safety.py`.

Previous bug: `TestResolveProxmoxHost` (5 tests) monkeypatched `probe_host` with fake IPs. All 5 passed while `ai` was crashed and all 3 WAN hosts were unreachable. Nobody knew because the "host probing tests" never probed any hosts. Replaced with `TestInfrastructureHealth` that probes real hosts. Now `pytest tests/` is the infrastructure early warning system.

## Controller is a bakeable target (2026-04-17)

- The build machine (where molecule runs) is NOT exempt from the "bake,
  don't configure" principle. wireguard-tools and NOPASSWD sudoers are
  already installed by `setup.sh`. wg0 is configured automatically by
  `site.yml` on every converge. No manual steps needed.
- NEVER use `become: true` on localhost Ansible plays. It prompts for sudo
  password, blocking non-interactive molecule runs. Use explicit `sudo`
  calls — the NOPASSWD sudoers entry is already installed.
- Previous bug (2026-04-17): Controller VPN play used `become: true` on
  localhost. After 60 minutes of successful converge on all 6 Proxmox hosts,
  the pipeline failed with "sudo: a password is required" at the very last
  play. Fix: explicit `sudo` calls with NOPASSWD sudoers (already installed
  by `setup.sh`).

## Callhome config rewrite must preserve identity (2026-04-17)

- When `kiosk_configure` rewrites callhome config on sibling containers to
  point them at the local NodeManager, it MUST preserve `CALLHOME_HOSTNAME`
  and other baked-in variables. Use targeted `sed -i` to update only
  `CALLHOME_SERVER` and `CALLHOME_PUBLIC_KEY`. NEVER use `printf` or heredoc
  to overwrite the entire file.
- This applies to ALL container types (Debian and OpenWrt). OpenWrt containers
  use `callhome.sh` (BusyBox) instead of the Python callhome agent, and their
  `/etc/default/callhome` contains additional baked variables like
  `CALLHOME_HOSTNAME` that determine fleet service identity.
- Previous bug (2026-04-17): `printf` overwrite on OpenWrt containers destroyed
  `CALLHOME_HOSTNAME=openwrt-mesh`. Containers heartbeated with wrong identity,
  fleet readiness API couldn't match them, verify failed.

## Display service resilience to unconfigured endpoints (2026-04-17)

- Display services (moonlight-display, kodi-display, etc.) that depend on a
  remote server endpoint MUST handle the case where the endpoint is not
  configured. The xstartup script should check for a valid config before
  launching the streaming client. If no server is configured, `sleep infinity`
  is preferable to crash-looping.
- Previous bug (2026-04-17): `moonlight-display.service` on mesh1 crash-looped
  because `MOONLIGHT_SERVER_IP` was empty in test.env. The `moonlight stream`
  command failed immediately, Xvnc restarted, creating a deadlock loop that
  consumed resources. Fix: xstartup checks `/etc/moonlight.conf` for a
  configured `address` before launching. If missing, sleeps instead of crashing.

## KasmVNC migration lessons (2026-04-14)

- When removing an enum used in API JSON responses (e.g., `DisplayType`), grep ALL
  serialization points — not just the model definition. The `manager.py` display API
  handlers used `handler.display_type.value` which broke at runtime after the enum was
  removed. Unit tests caught handler logic but not the API serialization path.
- KasmVNC Xvnc flags are case-sensitive: `-DisableBasicAuth` (capital D) is correct,
  NOT `-disableBasicAuth`. The lowercase variant silently fails and enables the auth
  prompt, which blocks iframe embedding.
- When building images with `build-images.sh`, a shared constant (like `KASMVNC_VERSION`)
  passed through `bash -s` into SSH heredocs requires only a SINGLE declaration point.
  Never duplicate constants — pass them as positional arguments to subshells.
- NiceGUI pages that construct button text from URL query parameters must guard against
  the default value being the same as the prefix, or you get "Launch Launch" instead
  of just "Launch".
- Stale Python bytecache (`__pycache__/*.pyc`) from deleted modules can cause the OLD
  module's code to be served even after the source file is deleted. Always clear pycache
  when removing Python modules from a running system.
- Chromium's NetworkService subprocess crashes on startup in LXC containers when
  `DBUS_SESSION_BUS_ADDRESS` is not set in the systemd service environment. The crash
  prevents HTTP page loads, resulting in "Untitled" Chromium windows. Fix: add
  `Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus` to the
  KasmVNC systemd service. Additionally, bake `dbus-x11` and `libxtst6` into the
  kiosk image and add a 5-second delayed F5 reload in xstartup to handle any
  remaining startup race conditions between the renderer and NetworkService.

## API-driven fleet cutover — no SSH fallbacks (2026-04-18)

- After VPN + heartbeat are established, ALL configure roles and verification
  MUST use `ansible.builtin.uri` → NM/SM API over VPN. NEVER SSH.
- NEVER add SSH fallback paths in `verify.yml`. If the API is unreachable,
  the 4-tier system is broken — hard-fail with INVESTIGATE prompts, don't
  work around it with `pct exec`.
- NEVER describe infrastructure failures as "expected" or "known." Every
  failure represents a broken system that must be fixed.
- Hard-failure messages MUST include: (1) `HARD FAILURE` prefix, (2) what
  specifically failed, (3) `INVESTIGATE:` section with concrete diagnostic
  commands (`curl`, `journalctl`, `wg show`, relay chain tracing).
- Previous catastrophe (2026-04-18): mesh1 NodeManager was unreachable at
  10.10.10.210:9001 from the controller. Agent described it as "expected (no
  L3 route)." User correctly identified this as a hard failure — all 6 units
  MUST be reachable via VPN+HTTP as the base path. WiFi mesh is a bonus for
  reachability, not a prerequisite.
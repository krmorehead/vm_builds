---
name: openwrt-security-transition
description: OpenWrt SSH authentication transition and security hardening patterns. Use when implementing SSH key authentication, security hardening, or managing authentication transitions.
---

# OpenWrt Security Transition Rules

## SSH Auth Transition Order

1. After security hardening (M1), OpenWrt switches from password auth (empty password) to key-only auth. This is a critical ordering problem with steps that MUST happen in exact order within single play:

**Step 1: Deploy key** — copy public key to OpenWrt via `raw` (password auth still works)

**Step 2: Verify key auth** — test SSH with key to confirm it works

**Step 3: Disable password auth** — `uci set dropbear.@dropbear[0].PasswordAuth='off'`

**Step 4: Re-register `openwrt` host** — `add_host` with `-i <key_path>` in SSH args

2. Steps 1-4 MUST happen in this exact order within a single play. If step 3 runs before step 2 confirms key auth works, the VM becomes unreachable.

## Key Configuration

3. The key path comes from `OPENWRT_SSH_PRIVATE_KEY` env var (optional, defined in role `defaults/main.yml`). When not set, security hardening skips SSH lockdown and only installs banIP.

## Rollback Pattern

4. **Rollback** must reverse this completely: re-enable password auth in dropbear, clear the root password in `/etc/shadow` (restore empty-password baseline), and remove the authorized key.
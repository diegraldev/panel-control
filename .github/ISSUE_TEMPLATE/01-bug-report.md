---
name: Bug report
about: Report incorrect behavior of the panel, the agent or the deploy
title: "[Bug]: "
labels: bug, triage
---

## Context

<!-- Where does it happen? Phone (Termux), PC (agent), WoL listener, deploy, UI... -->

- Affected component:
- Deployed commit (`curl -s http://100.113.245.28:8080/version`):
- Did it start after a change/deploy? When?

## Steps to reproduce

1.
2.
3.

## Expected behavior

## Observed behavior (evidence)

<!-- Paste logs, journalctl output, screenshots or curl responses. -->

```text

```

## Environment

- Side: [ ] phone Termux  [ ] PC  [ ] both
- Python / Fedora / Android version:
- Tailscale active: [ ] yes [ ] no
- Service status (if applicable):
  - `systemctl --user status panel-agent.service`
  - `systemctl status wol-listener.service`

## Impact

- Does it affect status, commands, shutdown/WoL or only the UI?
- How often does it happen?
- Is there a risk of losing remote control of the PC?

## Acceptance criteria

- [ ] The issue reproduces deterministically (or its intermittency is documented).
- [ ] The root cause is identified in a specific file/line.
- [ ] A verification exists that passes with the fix and fails without it.
- [ ] The HTTP contract (`docs/AGENT_CONTEXT.md` §2) is still intact.

## Proposal (optional)

<!-- If you already have an idea for the fix, describe it and note the risk/rollback. -->

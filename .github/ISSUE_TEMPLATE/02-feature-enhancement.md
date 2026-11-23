---
name: Feature / enhancement
about: Propose a new capability or improve an existing one without breaking the contract
title: "[Feature]: "
labels: enhancement, triage
---

## Context

<!-- What problem does it solve? What are today's limitations of the panel? -->

- Component(s) involved: [ ] phone  [ ] PC  [ ] listener  [ ] UI
- Is there anything similar already in the repo?

## Motivation

<!-- A concrete use case. E.g.: "I want to see X without opening the PC". -->

## Proposal

<!-- High-level design: new endpoints/fields/commands, UI changes, etc. -->

## Contract compatibility

- [ ] Does not change names of existing endpoints, JSON fields or commands.
      (If it does, explain the migration plan under "Impact".)
- [ ] Follows the rules in `../AGENTS.md` (user service, no forks in the hot path,
      background battery cache, Host/Origin/token security).
- [ ] Does it require a new decision in `docs/DECISIONS.md`? Justify.

## Alternatives considered

<!-- What was rejected and why (so a future agent does not retry it). -->

## Impact

- Performance: does it add forks, polling or traffic? How much?
- Phone battery: does it increase wakeups or Termux:API calls?
- Security: new endpoint, token or exposed surface on Tailscale?

## Acceptance criteria

- [ ] Implemented on a feature branch and delivered through a PR (never a direct commit to `main`).
- [ ] `python3 -m py_compile` / `bash -n` pass.
- [ ] Manual verification documented (curl / journalctl / screenshot).
- [ ] `docs/DECISIONS.md` updated if applicable.

## Risk / rollback

<!-- Explain how to revert if the auto-deploy (10 s) ships a problematic change. -->

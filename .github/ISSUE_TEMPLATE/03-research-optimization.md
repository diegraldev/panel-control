---
name: Research / optimization
about: Analyze battery, latency or performance and propose evidence-based improvements
title: "[Research]: "
labels: research, optimization, triage
---

## Context

<!-- What is to be investigated. E.g.: excessive battery drain on the Android phone. -->

- Component / layer under study:
- Measurable symptom (e.g.: "battery drops 15%/h with the screen off"):
- Commit / version since which it has been observed:

## Research question

<!-- One concrete sentence the investigation must answer with data. -->

## Baseline evidence (before)

<!-- Real measurements of the current state: commands, counters, logs, perf. -->

| Metric | Current value | How measured |
|---|---|---|
| | | |

```text
<!-- journalctl, top, dumpsys battery, /proc, curl -w time_total ... -->

```

## Hypotheses

<!-- Candidate causes, ordered by probability. Point to a file/function. -->

1.
2.
3.

## Constraints (do not break)

- The HTTP contract and state JSON (`docs/AGENT_CONTEXT.md` §2) are sacred.
- `AGENTS.md` rules 1–10: user service, no sudo from the agent, no forks in the
  hot path, background battery cache, command idempotency.
- Battery: pattern ADR-007 — `termux-battery-status` in a background thread every
  30 s, NEVER in the HTTP handler; `termux-wake-lock` active.
- Do not introduce changes that increase phone wakeups without measuring
  before/after.

## Proposed optimization

<!-- Concrete changes, per file. Prioritize highest impact and lowest risk. -->

1.
2.
3.

## Expected impact and "after" measurement

| Metric | Before | After | Δ | How verified |
|---|---|---|---|---|

## Acceptance criteria

- [ ] Hypothesis confirmed or rejected with data (not assumptions).
- [ ] Before/after table filled in; the change does not degrade performance or features.
- [ ] `python3 -m py_compile` / `bash -n` pass and the panel still responds.
- [ ] New findings or decisions documented in `docs/DECISIONS.md`.

## Risk / rollback

<!-- Especially important: auto-deploy applies in 10 s. How to revert quickly. -->

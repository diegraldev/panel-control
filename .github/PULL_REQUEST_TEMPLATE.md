## Summary

<!-- 1–3 sentences: what changes and why. -->

## Motivation / context

<!-- Related issue, observed problem or decision being applied. -->
Closes #

## Change

<!-- Which files and why. One bullet per relevant file. -->

- `panel_celular/...`:
- `panel_pc/...`:
- Documentation (`AGENTS.md` / `docs/`):

## Contract compatibility

- [ ] HTTP contract intact (endpoints, JSON fields and commands; see `docs/AGENT_CONTEXT.md` §2).
- [ ] Rules in `AGENTS.md` are respected (user service, no sudo from the agent,
      no forks in the hot path, background battery cache, Host/Origin/token security).
- [ ] If applicable, `docs/DECISIONS.md` has a new entry.

## Tests

<!-- Real evidence: commands run and their output. "It works" is not enough. -->

```bash
python3 -m py_compile panel_celular/server.py panel_celular/wol.py panel_pc/agent.py panel_pc/wol-listener.py
bash -n panel_celular/boot_panel panel_pc/*.sh
git diff --check
```

Manual verification (curl / journalctl / screenshot / before-after):

```text

```

## Impact

- Performance / latency:
- Phone battery consumption:
- Security (tokens, Host/Origin, Tailscale surface):
- Deploy: does it restart `server.py`? Does it affect agent long-polls?

## Risk / rollback

<!-- What can go wrong after the 10 s auto-deploy and how to revert quickly. -->

## Checklist

- [ ] `py_compile` and `bash -n` pass.
- [ ] `git diff --check` clean.
- [ ] No credentials, test IPs or new personal paths without justification.
- [ ] Documentation updated if operational behavior changed.
- [ ] Changes are committed on a feature branch, not directly on `main`; the PR is the only path to `main`.

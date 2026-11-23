# Remote Control

Minimal monochrome web panel served from an Android phone (Termux) over Tailscale.
It shows the PC status and can wake it (Wake-on-LAN), shut it down, and open/close T3 Code.

## Structure
- `panel_celular/` — Python server + HTML + wol.py + auto-deploy + boot script
- `panel_pc/` — PC agent (RAM, CPU, GPU usage/temps, T3 Code, commands)

## Documentation / for agents

If you are an agent (or about to hand work to one), start with **[AGENTS.md](AGENTS.md)**:
short memory with the non-negotiable rules, useful commands and how to verify changes.
Then consult:

- [docs/AGENT_CONTEXT.md](docs/AGENT_CONTEXT.md) — detailed architecture, HTTP
  contract, environment variables, systemd services, auto-deploy flow and gotchas.
- [docs/DECISIONS.md](docs/DECISIONS.md) — decisions already made (simple ADRs):
  user vs. system service, AppImage extract-and-run, battery cache, rate-limit-free
  Atom feed, WoL flag, etc. **Do not revert them without a new decision.**
- [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/) — bug, feature and
  research/optimization templates (context, evidence, acceptance criteria).
- [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) — summary,
  tests, impact, risk/rollback and checklist.

## Auto-deploy
The phone polls GitHub every 10s (`DEPLOY_POLL`, via a cached Atom feed that costs
zero API rate-limit units). On a new commit on `main`, it downloads, copies the
files and restarts automatically (only if `server.py` itself changed).

## PC setup (important)

The agent must run as a **user** systemd service, not a system service:

```bash
mkdir -p ~/.config/systemd/user
cp panel_pc/panel-agent.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now panel-agent.service
```

Reason: T3 Code is launched from the agent. If the agent runs as a system service it
has the `unconfined_service_t` SELinux context, and T3 Code inherits it — the app opens
but `sudo`/`pkexec` do not work inside it. As a user service everything inherits
`unconfined_u` (the desktop session context), so admin access works normally.

`t3-open` also sets `APPIMAGE_EXTRACT_AND_RUN=1` (the AppImage is extracted instead of
FUSE-mounted, so it works even with a restricted capability set) and launches the app
in a transient **`systemd-run --user --scope`**, so it survives agent restarts and keeps
the session context.

## Performance / stability / security improvements
Summary (measurements from the original report, available in git history):
state collection 1750 ms → 3 ms, command latency 6.4 s → 0.002 s,
agent CPU 13.9% → 1.2%, 0% read loss, CORS/Host restrictions and auto-deploy
without consuming GitHub API rate limit. The interface keeps all endpoints,
JSON fields and commands; only the styling changed (black/white with cyan accents).

New options (all optional, with backwards-compatible defaults):
- `PANEL_INTERVAL` (agent, s): how often status is published (default 1).
- `GPU_SAMPLE_PERIOD` (agent, s): GPU sampling window (default 2).
- `PANEL_TOKEN` (both): when set, requires `X-Panel-Token` on POST requests.
- `ALLOWED_HOSTS` (panel): additional allowed hosts, comma-separated.
- `DEPLOY_POLL` (panel, s): GitHub check interval (default 10; free 304 responses).

## Usage
Open `http://100.113.245.28:8080` with Tailscale enabled.

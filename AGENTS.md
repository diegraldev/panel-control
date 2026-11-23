# AGENTS.md — memory for the remotecontrol repo

Read this first. It is the short memory for agents: facts that cannot easily be
deduced from the code, the rules, and the check used to verify changes. The detailed
guide is in `docs/AGENT_CONTEXT.md` and the decisions in `docs/DECISIONS.md`.

## Purpose

A minimal web panel served from an **Android phone running Termux over Tailscale**
to monitor and control a PC: status (CPU/RAM/GPU/temps), Wake-on-LAN, shutdown,
opening/closing T3 Code, and sleep mode. This is not a generic product: it targets a
single real device (fixed IPs and MAC, absolute paths under `/home/diegral`).

## Architecture (flow)

- `panel_celular/server.py` — HTTP server on the phone (port 8080). Keeps state in
  memory + atomic JSON files. Exposes `/status`, `/cmd` (long-poll `?wait=N`),
  `/cmd/ack`, `/estado`, `/battery`, `/version`. Auto-deploy: polls the Atom feed
  `https://github.com/diegraldev/remotecontrol/commits/main.atom` every `DEPLOY_POLL`
  (10 s) with `If-None-Match` (304 = 0 bytes, no API rate limit consumed).
  Downloads the `main` tarball, copies `index.html`, `server.py`, `wol.py`; it only
  restarts (`os.execv`) if `server.py` changed.
- `panel_celular/index.html` — monochrome UI (black/white + cyan). Calls `/status` and
  `/battery` every 1 s. `TIMEOUT=15000` ms without state ⇒ shows "pc is off".
- `panel_celular/boot_panel` — Termux:Boot script: `termux-wake-lock`, starts
  `python3 server.py` with `REPO_URL=diegraldev/remotecontrol` if it is not running.
- `panel_pc/agent.py` — agent on the PC (user `diegral`). Publishes status every
  `PANEL_INTERVAL` (1 s) and long-polls for commands. Two separate keep-alive
  connections (publishing vs. commands) so a `wait=10` never blocks publishing.
  Background sampling: CPU from `/proc/stat` deltas, temps from sysfs,
  GPUs from persistent `nvidia-smi -lms` / `intel_gpu_top`, IP from cached ioctl.
- `panel_pc/wol-listener.py` — **root** service on the PC, port 8091, `POST /wake`.
  It does NOT write the autologin flag (that flag is written by the agent **at
  shutdown**; writing it here would corrupt the next manual boot — see DECISIONS).
- `panel_pc/wol-autologin.sh` — before SDDM: if `/var/lib/wol-autologin/pending`
  exists, enable autologin to niri and remove the flag; otherwise disable autologin.
- `panel_pc/sleep-mode.sh` — brightness/volume/RGB to 0 (`on`) and restore (`off`),
  invoked via `systemd-run --user --scope`.

## Rules for agents (non-negotiable)

1. **Do not break the HTTP contract**: same endpoints, same JSON fields, same
   commands (`shutdown`, `wake`, `cancelar`, `t3-open`, `t3-close`, `sleep-on`,
   `sleep-off`). `index.html` and the agent are consumers; renaming anything
   breaks the panel.
2. **The PC agent must be a USER service** (`systemctl --user`), never a system
   service. As a system service it gets the `unconfined_service_t` SELinux context
   and T3 Code cannot use `sudo`/`pkexec`. With `--user` it inherits `unconfined_u`
   from the session.
3. **Do not use `sudo` from the agent**: it runs with a service context. Write the
   WoL flag directly (the directory is user-owned) and shut down via
   gdbus/logind + polkit.
4. **T3 Code is launched with `APPIMAGE_EXTRACT_AND_RUN=1`** (no FUSE: capabilities
   are missing) in a transient `systemd-run --user --scope` (a oneshot --user
   service kills the cgroup when `gtk-launch` finishes). Clone the full graphical
   session environment.
5. **Do not reintroduce forks in the hot path** (`sensors`, `pgrep`, `hostname -I`,
   `nvidia-smi` per sample). Current performance is 3 ms per collection, 1.2% CPU.
6. **Phone battery**: `termux-battery-status` is called in a background thread
   every 30 s with caching; NEVER inside the HTTP handler (it blocked/crashed the
   connection). PC wake runs in a daemon thread: the HTTP response must not wait
   for the powered-off PC.
7. **Security**: keep `_guard` (Host + Origin + optional `X-Panel-Token` on POST).
   Respect `ALLOWED_HOSTS` and the tailnet range `100.64.0.0/10`.
8. **Command idempotency**: the agent stores `(comando, ts)` in `last_key` and
   skips duplicates if an ack was lost. Do not remove this mechanism.
9. Commit changes on a feature branch and open a PR; never commit or push
   directly to `main`. Leave `main` untouched — all changes reach it through
   reviewed PRs.
10. **Write all documentation, issues, comments and responses in English.** Existing
    code mixes English and Spanish; match the file you are editing, but all new
    prose must be in English.

## Useful commands

```bash
# Check syntax (there is no test suite; this is the mandatory minimum check)
python3 -m py_compile panel_celular/server.py panel_celular/wol.py panel_pc/agent.py panel_pc/wol-listener.py
bash -n panel_celular/boot_panel panel_pc/*.sh

# Test the server locally (no deploy, no Termux)
cd panel_celular && PORT=8080 python3 server.py
curl -s localhost:8080/version && curl -s localhost:8080/status

# Check the commit deployed on the phone
curl -s http://100.113.245.28:8080/version

# PC: service status
systemctl --user status panel-agent.service
systemctl status wol-listener.service wol-autologin.service panel-rgb-perms.service
journalctl --user -u panel-agent.service -f
PANEL_DEBUG=1 python3 panel_pc/agent.py   # command logs
```

## How to verify changes

1. `python3 -m py_compile` on every `.py` touched + `bash -n` on the `.sh` files.
2. If you touched `server.py`/`index.html`: start the server locally and check that
   `/status`, `/version`, `/battery` and `/cmd?wait=1` respond, and that a valid
   `POST /cmd` arrives via long-poll followed by `POST /cmd/ack`.
3. If you touched the agent: verify the published JSON keeps every key
   (`hostname, ip, ram, cpu_percent, cpu_temp, gpus, t3_open, sleep_active, timestamp`).
4. Confirm `git diff --check` reports no broken whitespace.

## Gotchas

- The UI considers the PC "offline" if `recibido_ts` is older than 15 s; publishing
  must keep flowing even when there are no commands.
- `store_status()` writes to disk **at most every 5 s**; when debugging,
  `status.json` may lag behind memory.
- `wol.py` exists twice: `./wol.py` (root) and `panel_celular/wol.py`; deploy only
  updates the `panel_celular/` copy. Keep both in sync.
- The IPs `100.113.245.28` (phone) and `100.69.149.63:8091` (listener) and the MAC
  `d8:bb:c1:22:1a:d1` are real environment data; they are not examples.
- `REPORT.html` was deleted in the last commit; the README no longer refers to it.
  Do not recreate it unless asked, and do not add new broken references.
- `DEPLOY_POLL` only takes effect if `REPO_URL` is set (set by `boot_panel`).

## Documentation index

- `README.md` — user summary and getting started.
- `docs/AGENT_CONTEXT.md` — detailed context: flow chart, env vars, HTTP surface,
  operational gotchas, verification checklist.
- `docs/DECISIONS.md` — architecture decisions already made (simple ADRs).
- `.github/ISSUE_TEMPLATE/` and `.github/PULL_REQUEST_TEMPLATE.md` — templates.

# Detailed agent context — remotecontrol

Reference document. For the short memory (rules and checks) see `../AGENTS.md`.
For the *why* behind decisions see `DECISIONS.md`.

## 1. System map

```
   [ Android phone / Termux ]                 [ PC / Fedora / user diegral ]
   ┌──────────────────────────┐                 ┌───────────────────────────────┐
   │ panel_celular/server.py  │  Tailscale      │ panel_pc/agent.py (--user)    │
   │  HTTP :8080              │<─── POST /status│  publishes every 1 s           │
   │  state in memory+JSON    │     GET /cmd    │  long-poll /cmd?wait=10       │
   │  auto-deploy (Atom feed) │─── commands────>│  runs: shutdown/wake/t3/...    │
   │  /battery (30 s cache)   │                 │  gdbus -> logind -> poweroff  │
   └────────────┬─────────────┘                 └────────────┬──────────────────┘
                │ POST /wake (fire & forget)                 │ writes flag
                │ (optional X-Wake-Token)                    │ /var/lib/wol-autologin/pending
                v                                            v
   ┌──────────────────────────┐                 ┌───────────────────────────────┐
   │ magic packet UDP :9      │                 │ wol-autologin.sh (before SDDM)│
   │ (wake_pc_async, thread)  │                 │ enables autologin if flag set │
   └──────────────────────────┘                 └───────────────────────────────┘
```

Phone boot: `Termux:Boot` runs `panel_celular/boot_panel`
(`termux-wake-lock` + `nohup python3 server.py` with `REPO_URL`).

## 2. HTTP surface (contract; do not rename)

| Method | Path | Used by | Effect |
|---|---|---|---|
| GET | `/` , `/index.html` | browser | HTML with gzip + ETag (304 if unchanged) |
| GET | `/status` | browser | latest cached state (`recibido_ts`, `recibido_en_celular`) |
| POST | `/status` | PC agent | stores state (memory; disk at most once per 5 s) |
| GET | `/cmd?wait=N` | PC agent | long-poll until a command arrives (N≤30) |
| POST | `/cmd` | browser | queues `{"comando": ..., "ts": ...}`; `wake` fires packet async |
| POST | `/cmd/ack` | PC agent | stores result and clears the queue |
| GET | `/ack` | debug | last command result |
| GET | `/estado` | debug | free-form blob |
| POST | `/estado_cmd` | debug | stores free-form blob |
| GET | `/battery` | browser | `{"level": %, "charging": bool}` from the 30 s cache |
| GET | `/version` | debug / deploy | `{"sha": ..., "repo": ...}` |

Additive iOS routes (new paths / one new query flag; the contract above is unchanged):

| Method | Path | Used by | Effect |
|---|---|---|---|
| GET | `/siri` | Siri Shortcuts | plain-text status sentence, or `PC is off.` |
| POST | `/cmd?spoken=1` | Siri Shortcuts | same command, plain-text sentence reply; a missing/invalid `ts` is auto-filled |
| GET | `/manifest.webmanifest`, `/sw.js` | iPhone PWA | embedded manifest and service worker (no new deployable files) |
| GET | `/push/config`, `/push/subscriptions` | UI / debug | push availability + VAPID public key; subscription count/endpoints (no secrets) |
| POST | `/push/subscribe`, `/push/unsubscribe`, `/push/test` | iPhone PWA | store/remove a subscription; enqueue one test notification per subscriber |

`GET /siri` and `POST /cmd?spoken=1` (also triggered by `Accept: text/plain`) are guarded
exactly like the original routes. A spoken invalid command returns `400` plain text;
guard failures keep the original JSON body. All push routes inherit
`_guard(mutating=True)` — there is no second guard. Push endpoints never block a
request: transition detection and delivery run in daemon threads, and `PANEL_PUBLIC_URL`
unset (or a missing `cryptography` module) disables push with a clear config error.

Valid commands (`CMD_VALUES`): `shutdown`, `wake`, `cancelar`, `t3-open`,
`t3-close`, `sleep-on`, `sleep-off`.

State JSON fields published by the agent (keep all of them):
`hostname`, `ip`, `ram` (total_mb/used_mb/free_mb/percent), `cpu_percent`,
`cpu_temp` (max/source), `gpus` (name/usage/temp/type), `t3_open`, `sleep_active`,
`timestamp`. The server adds `recibido_en_celular` and `recibido_ts`.

## 3. Environment variables

Phone server (`panel_celular/server.py`):

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | listen port |
| `PC_MAC` | `d8:bb:c1:22:1a:d1` | MAC for WoL |
| `REPO_URL` | `""` (deploy disabled) | `owner/repo` for auto-deploy |
| `REPO_TOKEN` | `""` | Bearer token to download the tarball (private repo) |
| `DEPLOY_POLL` | `10` | seconds between Atom feed checks |
| `PANEL_TOKEN` | `""` | if set, requires `X-Panel-Token` or `?token=` on POST |
| `WAKE_TOKEN` | `""` | sent as `X-Wake-Token` to the listener |
| `WAKE_URL` | `http://100.69.149.63:8091/wake` | root listener on the PC |
| `ALLOWED_HOSTS` | `""` | extra allowed hosts, comma-separated |
| `PANEL_PUBLIC_URL` | `""` | HTTPS `https://<phone>.<tailnet>.ts.net` name; unset disables push |
| `VAPID_SUBJECT` | `https://github.com/diegraldev/remotecontrol` | VAPID JWT `sub` (a `mailto:` or `https:` URL) |
| `PUSH_TTL` | `60` | seconds of TTL sent to the push service |
| `PUSH_CHECK_INTERVAL` | `2` | seconds between transition-detector polls |
| `PANEL_DATA_DIR` | `BASE` | directory for `vapid.json` / `subscriptions.json` (tests isolate it) |

PC agent (`panel_pc/agent.py`):

| Var | Default | Purpose |
|---|---|---|
| `PANEL_URL` | `http://100.113.245.28:8080` | panel URL |
| `PANEL_INTERVAL` | `1` | seconds between status publishes |
| `PC_MAC` | `d8:bb:c1:22:1a:d1` | used by `wake` |
| `T3_APPIMAGE` | absolute path in `~/Downloads` | AppImage to launch |
| `GPU_SAMPLE_PERIOD` | `2` | GPU sampling window |
| `PANEL_TOKEN` | `""` | token for POSTs to the panel |
| `PANEL_DEBUG` | `""` | `1` enables command logs |

`panel-agent.service` pins `PANEL_URL` and `PC_MAC` in the unit Environment.

## 4. systemd services on the PC

| Unit | Type / context | Function |
|---|---|---|
| `panel-agent.service` | **--user**, `WantedBy=default.target` | agent; see the SELinux rule in AGENTS.md |
| `wol-listener.service` | system (root), port 8091 | receives the wake signal from the phone |
| `wol-autologin.service` | system, `Before=sddm.service` | conditional autologin setup |
| `panel-rgb-perms.service` | system, oneshot | `chmod 666` of RGB LED sysfs nodes at boot |
| `panel-sleep.service` | oneshot template (`%i` = on/off) | brightness/volume/RGB; used by `sleep-mode.sh` via `systemd-run --user --scope` |

Auxiliary rules: `99-panel-rgb.rules` (udev, LED permissions),
`polkit-poweroff.rules` (allows poweroff/reboot for user `diegral`).

## 5. Auto-deploy, step by step

1. `deploy_loop()` calls `check_and_deploy()` at startup and every `DEPLOY_POLL`.
2. `remote_sha()` requests `commits/main.atom` with `If-None-Match: <saved etag>`.
   - On changes: parses `<id>tag:github.com,2008:Grit::Commit/<sha></id>`.
   - On `304`: returns the local SHA and keeps the ETag ⇒ zero bytes, zero API units.
   - If the feed fails: falls back to REST `/repos/.../commits?per_page=1&sha=main`
     (consumes rate limit; contingency only).
3. If the SHA changed: downloads `archive/refs/heads/main.tar.gz`, extracts into
   `.deploy/` with `--strip-components=1`.
4. Compares bytes of the three deployable files against the local ones.
   - `index.html`/`wol.py`: `os.replace` directly (hot reload, no restart).
   - `server.py`: saved as `.new`, state dumped (`_flush_state()`), then
     `os.execv` restarts the process.
5. `version.json` stores `{sha, ts, etag}` (gitignored).

**Implication for agents:** a push to `main` deploys to the phone in ~10 s. Any
change to `server.py` must therefore be backwards compatible: the restart is
automatic and there is no manual rollback unless the commit is reverted.

## 6. Optimizations already achieved (do not regress)

From commit `e5606b7` and later (original report `REPORT.html`, later removed):

- State collection: **1750 ms → 3 ms**.
- Command latency: **6.4 s → 0.002 s** (long-poll + TCP_NODELAY).
- Agent CPU: **13.9% → 1.2%**.
- 0% read loss (atomic tmp+rename writes).
- HTTP/1.1 keep-alive; `TCP_NODELAY` avoids Nagle's ~40 ms delayed ACK.
- Phone battery: `termux-battery-status` in a separate thread every 30 s, not in
  the HTTP handler; `termux-wake-lock` keeps the process alive with the screen off.
- No forks in the hot path: CPU from `/proc/stat`, temps from sysfs, IP via ioctl,
  T3 Code via `/proc/*/comm` scan, GPUs via long-lived processes.

## 7. Operational gotchas (errors already found in git history)

- **Battery in the handler**: calling `termux-battery-status` (or `dumpsys`)
  inside the request crashed the connection ⇒ background-thread cache
  (commits `833757e`→`2dc3cd5`).
- **Autologin flag**: the listener must NOT write it; by the time the signal
  arrives the PC has already booted and the flag was consumed ⇒ writing it there
  dirties the next manual boot (`45503da`).
- **Flag without sudo**: the agent cannot `sudo mkdir /var/lib/...` (service
  context) ⇒ writes directly; the directory is already user-owned (`b59313b`).
- **Sleep toggle**: do not change `aria-pressed` locally; sync with real
  brightness, and never save 0 as the restore value (`594634c`).
- **RGB without sudo**: udev rule + chmod, no `sudo` from the agent (`e24ac32`).
- **Transient scope**: `systemd-run --user --scope`, not a oneshot service —
  systemd kills the cgroup when `gtk-launch` ends (`fe119fb`).
- **Session environment**: clone `DISPLAY/WAYLAND_DISPLAY/DBUS_SESSION_BUS_ADDRESS/...`
  from a graphical process (`0a148f4`); otherwise the Qt plugin fails.
- **Two `wol.py` files**: deploy only updates `panel_celular/wol.py`; the root
  copy remains for standalone use. Sync them if the protocol changes.
- **`REPORT.html`**: referenced in the old README but deleted in `7903025`.
  Do not recreate it.

## 8. Checklist before calling a change done

1. `python3 -m py_compile` on every `.py` touched; `bash -n` on the `.sh` files.
2. `git diff --check` clean.
3. HTTP contract intact (section 2 table) and state JSON intact (section 2).
4. If you touched phone files: serve locally (`PORT=8080 python3 server.py`) and
   test `/version`, `/status`, `/battery`, `/cmd?wait=1` plus `POST /cmd`/`ack`.
5. If you touched the agent: verify no synchronous `subprocess` was added to
   `status()` or `publish_loop()`.
6. If the change affects deploy, remember `index.html`/`wol.py` hot-reload but
   `server.py` restarts the process and dumps state.
7. Record any new architectural decision in `DECISIONS.md`.

## 9. iOS Siri and Web Push (additive)

- `server.py` detects online/offline transitions in a daemon thread using the same
  15 s `recibido_ts` rule as the UI, confirmed by two consecutive polls. A pure
  `transition_decision()` (no I/O, no locks) makes this unit-testable; the first
  observation only establishes the baseline, so a restart never sends a spurious alert.
- Deliveries go through a single bounded queue (`_push_queue`, max 64) and one daemon
  worker. `410`/`404` prunes the subscription from memory and `subscriptions.json`;
  429/5xx/timeouts are logged and dropped, never retried in a loop.
- Payloads use the declarative Web Push format (`web_push: 8030`) with `navigate` and
  `app_badge`, so iOS 18.4+ renders them without a live service worker; `sw.js` calls
  `showNotification()` for older versions. The manifest and SW are string constants in
  `server.py` — the deploy copy list is still `index.html`, `server.py`, `wol.py`.
- `cryptography` is optional: with it missing, `/push/config` reports the reason and
  mutating push routes return 503; the panel, Siri and all original endpoints keep
  working. `vapid.json` is generated once and reused (mode 0600).
- The one-time phone setup is manual (`boot_panel` is not auto-deployed). Exact
  Shortcut recipes and verification steps are in `IOS-SIRI-AND-PUSH-SETUP.md`; the
  design rationale is ADR-013 in `DECISIONS.md`. Tests: `tests/test_server.py`
  (`python3 -m unittest discover -s tests -v`).

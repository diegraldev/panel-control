# Architecture decisions (simple ADRs)

Short format: **Context → Decision → Consequences**. When you make a new decision,
add an entry. This prevents a future agent from "fixing" something that was already
tested and rejected. Commit numbers refer to the real history of `main`.

---

## ADR-001 — PC agent as a USER service, not a system service
(`b8d99e8`, confirmed in `panel-agent.service`)

- **Context:** T3 Code is launched from the agent. As a system service the SELinux
  context is `unconfined_service_t`, which the app inherits ⇒ `sudo`/`pkexec`
  fail inside T3 Code even though the app opens.
- **Decision:** install `panel-agent.service` in `~/.config/systemd/user/` and
  enable it with `systemctl --user`. `WantedBy=default.target`.
- **Consequences:** the agent inherits `unconfined_u` from the desktop session;
  a graphical user session must exist. No `CAP_PERFMON` is needed:
  `perf_event_paranoid=2` allows `intel_gpu_top` without privileges.

## ADR-002 — T3 Code via AppImage extract-and-run + transient scope
(`ce9b663`, `e6f1c24`, `128a556`, `fe119fb`)

- **Context:** the service restricts capabilities (no `CAP_SYS_ADMIN`) ⇒ the
  AppImage FUSE mount fails with "Permission denied". Also, a `--user` oneshot
  service dies as soon as `gtk-launch` ends (systemd tears down the cgroup).
- **Decision:** `APPIMAGE_EXTRACT_AND_RUN=1` and launch with
  `systemd-run --user --scope --collect --unit=t3code-panel /usr/bin/gtk-launch com.t3tools.T3Code`,
  passing the full session environment via `--setenv=...`.
- **Consequences:** the app survives agent restarts and keeps the session context;
  the process comm is `t3code` (used for detection and shutdown via `/proc/*/comm`).

## ADR-003 — Auto-deploy via Atom feed with ETag, not the REST API
(`e5606b7`)

- **Context:** the phone checks GitHub every 10 s; the REST API has a rate limit
  (60 req/h unauthenticated) and every response consumes quota.
- **Decision:** read `commits/main.atom` with `If-None-Match`. A 304 costs 0 bytes
  and 0 quota units. REST remains only as a fallback if the feed fails.
- **Consequences:** deployment in ~10 s without a token; requires `REPO_URL` to
  be set (done by `boot_panel`). The ETag is stored in `version.json`.

## ADR-004 — Restart only if `server.py` changed
(`panel_celular/server.py::check_and_deploy`)

- **Context:** restarting the server interrupts agent long-polls and can lose
  in-memory state unless it is flushed.
- **Decision:** compare bytes of the three deployable files; `index.html` and
  `wol.py` are replaced hot; `server.py` is flushed (`_flush_state`) and the
  process is restarted with `os.execv`.
- **Consequences:** UI changes arrive without interruption; server changes are
  atomic but restart the process.

## ADR-005 — In-memory state + deferred 5 s disk writes
(`store_status`)

- **Context:** the agent publishes every 1 s; writing `status.json` on every POST
  wears the phone's flash and opens a window for partial reads.
- **Decision:** in-memory cache with `threading.Lock`, atomic `write_json()`
  (tmp + `os.replace`) and a disk write at most every 5 s. State is flushed on
  restart (`_flush_state`).
- **Consequences:** `status.json` can lag up to 5 s behind memory; the browser
  always sees fresh data via `/status`.

## ADR-006 — Long-poll + two keep-alive connections in the agent
(`e5606b7`)

- **Context:** with plain polling, a command took up to one interval to arrive
  (6.4 s measured), and a `GET /cmd?wait=10` blocked status publishing.
- **Decision:** the panel supports `GET /cmd?wait=N` with `threading.Condition`;
  the agent uses one `Client` for publishing and another for commands, both
  HTTP/1.1 keep-alive, with `TCP_NODELAY` on the server and a socket timeout to
  avoid thread leaks.
- **Consequences:** commands arrive in ~2 ms; publishing never stops; if the
  long-poll fails, the agent falls back to short polling and retries.

## ADR-007 — Battery and wake outside the HTTP handler
(`833757e`, `fca7d4c`, `441aa05`, `2dc3cd5`)

- **Context:** calling `termux-battery-status` (or `dumpsys`) inside the handler
  blocked/crashed the connection; connecting to the listener with the PC off
  hung the response until the socket timeout.
- **Decision:** a daemon thread updates `_battery_cache` every 30 s and `/battery`
  only reads the cache; `wake_pc_async()` sends the magic packet and notifies the
  listener in a daemon thread. `termux-wake-lock` prevents Android from
  suspending the server.
- **Consequences:** the battery endpoint is free per request; the UI can poll it
  every 1 s at no cost; the `wake` response is immediate.
  **Any future battery optimization must preserve this pattern.**

## ADR-008 — Shutdown without sudo: gdbus/logind + polkit rule
(`898fdbb`, `b59313b`)

- **Context:** the agent runs with a service context and its `sudo` does not work.
- **Decision:** shut down with `gdbus call --system ... Manager.PowerOff false` and
  allow the action for `diegral` via `polkit-poweroff.rules`. The autologin flag
  is written directly to `/var/lib/wol-autologin/pending` (without sudo).
- **Consequences:** immediate shutdown with no countdown; requires the polkit
  rule installed in `/etc/polkit-1/rules.d/`.

## ADR-009 — The WoL flag is written at shutdown, not by the listener
(`45503da`)

- **Context:** the phone notifies the listener just before the magic packet, but
  by then the PC has **already booted** and `wol-autologin.sh` has already
  consumed/deleted the flag. Writing it in the listener leaves it for the next
  manual boot.
- **Decision:** `run_command("shutdown")` writes the flag before powering off;
  `wol-listener.py::do_POST` only replies `ok` and touches no disk.
- **Consequences:** autologin only after a shutdown initiated from the panel;
  manual boots still ask for a password.

## ADR-010 — RGB permissions via udev, not sudo
(`e24ac32`, `fe73ae6`, `panel_pc/99-panel-rgb.rules`, `panel-rgb-perms.service`)

- **Context:** `sleep-mode.sh` needs to write LED sysfs nodes; `sudo` is not
  available from the agent.
- **Decision:** udev rule `MODE=0666, TAG+=uaccess` plus a oneshot service that
  runs `chmod 666` at boot in case udev did not apply it.
- **Consequences:** sleep mode works without elevated credentials.

## ADR-011 — Stable contract, changing style
(`16195f8`, `README.md`)

- **Context:** performance improvements and a visual redesign took place
  (monochrome, black/white with cyan accents).
- **Decision:** keep every endpoint, JSON field and command; change only styles
  and internal organization.
- **Consequences:** panel and agent can evolve independently as long as the
  contract is respected. Tests/verification focus on that contract.

---

## ADR-012 — Spaced deploy checks and visibility-aware UI polling
(`panel_celular/boot_panel`, `panel_celular/server.py`, `panel_celular/index.html`,
`panel_pc/agent.py`)

- **Context:** the phone is a dedicated always-on server. The deploy loop woke
  the radio and ran a DNS + TLS request every 10 s (~8640/day), the UI polled
  `/battery` every second even though the server cache only changes every
  30 s, and a hidden tab kept issuing both requests. The agent retried a
  long-poll every 0.2 s while the phone was unreachable. No in-repo
  measurement of `%/h` exists yet (issue #2, proposal P1); the wake lock (P2)
  is deliberately untouched because removing it needs on-device validation.
- **Decision:** `boot_panel` exports `DEPLOY_POLL=300` (the server default of
  10 s is kept for local use); `POST /version?check=1` schedules an immediate
  check in a daemon thread, with `GET /version` unchanged and a non-blocking
  lock preventing overlapping checks. The UI stops all polling when the tab is
  hidden, refreshes immediately on refocus, and polls `/battery` every 30 s
  while visible (`/status` stays at 1 s). The agent backs off failed publish
  and command requests (capped at 3 s / 5 s) and restores the long-poll after
  three successful short polls.
- **Consequences:** ~96% fewer feed connections/day, no HTTP from hidden tabs,
  no redundant battery reads, and no reconnect storm during outages. Deploys
  arrive within 5 minutes automatically, or on demand via the POST trigger.
  Command latency (~2 ms) and the 15 s freshness timeout are unchanged.
  Rollback: remove the `DEPLOY_POLL` export in `boot_panel` and revert the
  polling/backoff changes; no service or OS change is involved.

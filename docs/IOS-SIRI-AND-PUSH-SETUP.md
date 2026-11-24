# iPhone: Siri voice control and Web Push alerts

This guide sets up two additive features of the panel from an iPhone:

- **Siri Shortcuts** that speak the PC status and send every command by voice.
- **Web Push notifications** ("PC went offline" / "PC is back online") on the iPhone
  Home Screen, with an app badge.

## What you get — and what you do not

You get a normal iOS notification with text and a badge number, plus full Siri voice
control of all seven commands. You do **not** get a Dynamic Island, a Lock Screen Live
Activity, notification actions or images: those require a native app with ActivityKit
and APNs, which is out of scope here. See
[RESEARCH-ios-dynamic-island-siri.md](RESEARCH-ios-dynamic-island-siri.md) for the
research behind this decision.

## Prerequisites

1. Tailscale installed and connected on the iPhone and on the Android phone that runs
   the panel.
2. Tailscale admin console → **DNS**: enable **MagicDNS** and **HTTPS certificates**.
3. Termux:Boot and Termux on the phone, with the panel already working over the tailnet
   IP.
4. For push only: `cryptography` installed in Termux (Part D).

All commands below use the real tailnet IP `100.113.245.28` and the phone's MagicDNS
name `<phone>.<tailnet>.ts.net`. Replace the latter with the exact name shown in the
Tailscale admin console.

---

## Part A — Siri (works over the tailnet IP, no HTTPS needed)

Siri uses the existing HTTP contract, so this part needs no certificates, service worker
or VAPID keys. Open Safari on the iPhone and confirm that
`http://100.113.245.28:8080/status` returns JSON.

Create six shortcuts in the **Shortcuts** app. For each one:

1. **Get Contents of URL** with:
   - **URL**: the URL from the table below.
   - **Method**: `POST` (or `GET` for `PC status`).
   - **Headers**: add `Content-Type: application/json` for POSTs. Add
     `X-Panel-Token: <token>` **only if** `PANEL_TOKEN` is set on the phone. Leave
     `Host` alone.
   - **Request Body**: **JSON**, with the exact key/value shown below.
2. Final action: **Speak Text**, with the output of *Get Contents of URL* (tap the
   output token in the Speak Text field). The POST reply is already a plain sentence,
   so no JSON parsing action is needed.
3. Name the shortcut exactly as in the first column — the name is the default spoken
   phrase. For a custom phrase, open the shortcut's details → **Add to Siri** and record
   it.

| Shortcut name / phrase | Request |
|---|---|
| `turn on my PC` | `POST http://100.113.245.28:8080/cmd?spoken=1`, body `{"comando":"wake"}` |
| `shut down my PC` | `POST .../cmd?spoken=1`, body `{"comando":"shutdown"}` — add a **Choose from Menu** action first (`cancel` / `confirm`), and only continue on `confirm` |
| `open T3 Code` | `POST .../cmd?spoken=1`, body `{"comando":"t3-open"}` |
| `close T3 Code` | `POST .../cmd?spoken=1`, body `{"comando":"t3-close"}` |
| `sleep mode` | `POST .../cmd?spoken=1`, body `{"comando":"sleep-on"}` or `{"comando":"sleep-off"}` chosen by an **If** action on `GET .../siri` **contains** `Sleep mode is off.` |
| `PC status` | `GET http://100.113.245.28:8080/siri` → **Speak Text** of the output |

Notes:

- You do **not** need a `ts` action any more: the server fills a fresh millisecond
  timestamp when the client omits it. Shortcuts you already have with an explicit
  Random Number `ts` keep working unchanged.
- Shortcuts run on the device that invokes them. A HomePod or Apple Watch needs its own
  Tailscale connection and its own shortcuts; Siri on the iPhone will not relay to them.
- Built-in shortcut names can collide with system phrases — use **Add to Siri** with a
  distinctive phrase if Siri picks the wrong action.
- Do **not** use Tailscale Funnel: it would expose the panel publicly and defeat the
  `Host`/`Origin`/token guards.

Verify on the PC:

```bash
journalctl --user -u panel-agent.service -f
```

Each spoken command should appear as a normal queued command.

---

## Part B — HTTPS via Tailscale Serve

Web Push and service workers require a **secure context**. `http://100.113.245.28:8080`
is not one, so push only works through the phone's private HTTPS name.

In the phone's Termux session run:

```bash
tailscale serve --bg 8080
```

(Use the current CLI equivalent on your Tailscale version.) Then open
`https://<phone>.<tailnet>.ts.net/version` in Safari on the iPhone: it must return JSON.
Serve uses publicly trusted Let's Encrypt certificates, so no custom CA profile is
needed.

- **Never** run `tailscale funnel`: it publishes the panel to the internet.
- There are intermittent iOS reports of secure-connection failures with `*.ts.net`
  (see [tailscale/tailscale#19147](https://github.com/tailscale/tailscale/issues/19147)).
  If Safari rejects the certificate, keep using Siri over the tailnet IP (Part A) and
  retry push later; the panel itself is unaffected.

---

## Part C — Add the panel to the Home Screen

1. Open `https://<phone>.<tailnet>.ts.net/` in Safari.
2. **Share** → **Add to Home Screen**.
3. Keep **Open as Web App** on (default since iOS 26) and confirm.
4. Launch the panel from its new icon (not from Safari).
5. Tap **enable alerts** and allow notifications.

The button is hidden unless `/push/config` reports `push_available: true` and the page
is a secure context. If `PANEL_TOKEN` is set on the phone, enter it in the *panel token*
field that appears before enabling alerts.

Optional: Settings → Notifications → select `remote_control` and assign a Focus mode.
The manifest has a stable `id: "/"`, so notification/Focus settings stay in sync if the
app is installed on several devices.

On iOS 18.4+ WebKit renders the declarative JSON payload directly, so the notification
still appears even if the service worker was evicted by ITP. On older versions `sw.js`
calls `showNotification()` instead.

---

## Part D — One-time setup on the phone (manual)

`boot_panel` is **not** auto-deployed, so these changes must be applied once by hand on
the device; the repository copy is updated too for future reinstalls.

1. Install the crypto dependency (required only for push):

   ```bash
   pkg install python-cryptography
   python3 -c "import cryptography; print(cryptography.__version__)"
   ```

   Alternative if the repo package is unavailable: `pip install cryptography` (may
   need `pkg install openssl rust` and a source build). If the import fails the panel
   and Siri keep working and `/push/config` explains that push is disabled.

2. Add these exports to `/data/data/com.termux/files/home/panel/boot_panel`, before the
   `python3 server.py` launch line:

   ```bash
   export PANEL_PUBLIC_URL=https://<phone>.<tailnet>.ts.net
   export VAPID_SUBJECT=mailto:you@example.com    # or keep the default https: URL
   ```

3. Kill the running `python3 server.py` so Termux:Boot / the `pgrep` guard restarts it
   with the new environment (or start it manually with the same exports).

4. Confirm the result from Safari or Termux:

   ```bash
   curl -s https://<phone>.<tailnet>.ts.net/push/config
   ```

   It must show `"push_available": true` and an 87-character `public_key`.

`vapid.json` (private key, mode 0600) and `subscriptions.json` are created next to
`server.py` and are gitignored. The VAPID key is generated once and reused across
restarts, so existing subscriptions stay valid.

---

## Part E — Verification and troubleshooting

1. `POST /push/test` (or tap the button's test action if enabled) → one test
   notification with body `Test notification from remote_control`.
2. Force the PC offline (stop the agent) and wait more than 15 s → exactly one
   **PC went offline** notification and badge `1`.
3. Start the agent again → exactly one **PC is back online** notification and the badge
   is cleared (`app_badge: "0"`).
4. `GET /push/subscriptions` → count and endpoints only (no secrets). When Apple answers
   `410`/`404`, the subscription is removed from memory and disk automatically.

Troubleshooting:

| Symptom | Check |
|---|---|
| `enable alerts` button not visible | HTTPS URL, `push_available: true`, `window.isSecureContext` |
| Subscribe fails immediately | Permission request must happen in the tap handler; install from Home Screen |
| No notification on transition | Agent publishing every 1 s; PC really stale > 15 s; `tailnet` reachable from the phone |
| `BadJwtToken` in logs | VAPID key must match the `applicationServerKey` used to subscribe; VAPID `sub` must be a `mailto:` or `https:` URL |
| `410 Gone` | Subscription expired; it is pruned — reinstall the web app and enable alerts again |

## Security note

If `PANEL_TOKEN` is used, the token lives in the Shortcuts database (the user's own
iCloud devices) and in the Home Screen web app — never in a spoken phrase. Keep the
`Host`/`Origin`/token guards enabled and never expose the panel outside the tailnet.
Subscription endpoints are capability URLs: `subscriptions.json` and `vapid.json` must
not be shared or committed.

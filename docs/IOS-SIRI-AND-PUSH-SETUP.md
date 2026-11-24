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
   This tailnet only issues certificates for the PC's own MagicDNS name, which is why
   Part B terminates HTTPS on the PC.
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
  distinctive phrase if Siri picks the wrong action. The spoken phrase can be in any
  language Siri supports: record "Enciende mi PC", "Apaga mi PC" or "Abre T3 Code" as
  the phrase for the matching shortcut.
- Do **not** use Tailscale Funnel: it would expose the panel publicly and defeat the
  `Host`/`Origin`/token guards.

Verify on the PC:

```bash
journalctl --user -u panel-agent.service -f
```

Each spoken command should appear as a normal queued command.

---

## Part B — HTTPS via Tailscale Serve on the PC

Web Push and service workers require a **secure context**.
`http://100.113.245.28:8080` is not one, so the web app must be opened through an
HTTPS name. Termux on Android has no `tailscale` CLI, and this tailnet only issues
HTTPS certificates for the PC's own MagicDNS name, so HTTPS terminates on the PC
and is reverse-proxied to the phone.

On the PC, run once (tailscaled runs as root, so the serve config needs `sudo`):

```bash
sudo tailscale serve --bg --yes --https=443 http://192.168.100.15:8080
```

The backend must be the phone's **LAN IP**, not `100.113.245.28`: tailscaled's serve
proxy cannot dial a peer's Tailscale IP (requests hang with
`http: proxy error: context canceled`), while the same-subnet address works. The
phone is a fixed device on the home network, so this address is stable; if it ever
changes, update the serve target and rerun the command.

The web app URL is therefore **`https://msi-laptop.tail25e286.ts.net/`**. Verify
from the PC and then in Safari on the iPhone:

```bash
curl -s --resolve msi-laptop.tail25e286.ts.net:443:100.69.149.63 \
  https://msi-laptop.tail25e286.ts.net/version
```

Serve uses a publicly trusted Let's Encrypt certificate that tailscaled renews
automatically, so no custom CA profile is needed. Check the config any time with
`tailscale serve status --json`; disable it with
`sudo tailscale serve --https=443 off`.

- **Never** run `tailscale funnel`: it publishes the panel to the internet.
- The phone server sends push notifications itself, so alerts still arrive when the
  PC is off. Only the web app itself is unreachable until the PC is back; Siri
  (Part A) talks directly to the phone IP and is unaffected.
- There are intermittent iOS reports of secure-connection failures with `*.ts.net`
  (see [tailscale/tailscale#19147](https://github.com/tailscale/tailscale/issues/19147)).
  If Safari rejects the certificate, keep using Siri over the tailnet IP (Part A) and
  retry push later; the panel itself is unaffected.

---

## Part C — Add the panel to the Home Screen

1. Open `https://msi-laptop.tail25e286.ts.net/` in Safari.
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

## Part D — One-time setup on the phone (already applied)

`boot_panel` is **not** auto-deployed, so these changes are applied once by hand on
the device; the repository copy carries the same values (commented out, to be
uncommented when provisioning a new device). The steps below are the configuration
currently running on `100.113.245.28`.

1. The crypto dependency is installed (required only for push):

   ```bash
   pkg install python-cryptography
   python3 -c "import cryptography; print(cryptography.__version__)"
   ```

   Currently reports `50.0.2`. If the import fails, the panel and Siri keep working
   and `/push/config` explains that push is disabled.

2. The active Termux:Boot script is
   `/data/data/com.termux/files/home/.termux/boot/panel` (note: **not**
   `~/panel/boot_panel`; the deploy only replaces `index.html`, `server.py` and
   `wol.py`). It already exports, before the `python3 server.py` launch line:

   ```bash
   export DEPLOY_POLL=300
   export PANEL_PUBLIC_URL=https://msi-laptop.tail25e286.ts.net
   export VAPID_SUBJECT=mailto:diegraldev@users.noreply.github.com
   ```

3. The running `python3 server.py` was restarted with those same exports, so the
   current process has the new environment. After a reboot Termux:Boot starts it with
   the boot script above.

4. Confirm the result through the PC proxy or directly in Termux:

   ```bash
   curl -s https://msi-laptop.tail25e286.ts.net/push/config
   ```

   It reports `"push_available": true`, an 87-character `public_key`,
   `"token_required": false` and the exact `public_url` above. For future SSH
   management, Termux `sshd` runs on port 8022 with key authentication; never expose
   that port outside the tailnet.

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

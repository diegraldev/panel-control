# iOS Dynamic Island / Live Activities and Siri Shortcuts for `remotecontrol`

Research date: **2026-11-24**. Scope: what an iPhone can actually do with the panel served from
the Android/Termux phone at `http://100.113.245.28:8080`, and what must change (or be built)
for real, working feedback and voice control. All statements below were checked against the
primary sources listed in [Sources](#4-sources-verified-2026-11-24); anything that still needs
on-device confirmation is called out explicitly.

The panel's current contract (verified in `panel_celular/server.py` and `panel_celular/index.html`):

- `GET /status` — JSON. Important fields: `hostname`, `ip`, `ram` (`percent`, `used_mb`, `total_mb`),
  `cpu_percent`, `cpu_temp.max`, `gpus`, `t3_open`, `sleep_active`, `recibido_ts`,
  `recibido_en_celular`. The UI considers the PC online when `recibido_ts` is no older than 15 s.
- `POST /cmd` — JSON body `{"comando": <one of shutdown, wake, cancelar, t3-open, t3-close,
  sleep-on, sleep-off>, "ts": <any JSON number>}`; returns `{"ok": true, "comando": ...}`.
- `GET /version`, `GET /battery`, `GET /ack`, `GET /estado`, `POST /cmd/ack`, `POST /estado_cmd`.
- Mutating requests are protected by `_guard(mutating=True)`: `Host` must be allowed/tailnet,
  `Origin` must match, and if `PANEL_TOKEN` is set, either `X-Panel-Token: <token>` or
  `?token=<token>` must be present.
- The PC agent deduplicates commands with the key `(comando, ts)` (`panel_pc/agent.py`,
  `last_key`), so a freshly generated `ts` is useful even though the server does not interpret it.

## 0. Bottom line

| Goal | Possible from a web app / PWA on iPhone? | What is required |
|---|---|---|
| Real Dynamic Island / Lock Screen Live Activity driven by the panel | **No.** No WebKit API exposes ActivityKit or Live Activities to websites or Home Screen web apps. | A native iOS app (or App Clip where supported) with an ActivityKit + WidgetKit extension, APNs push-to-start tokens, Apple Developer Program, and distribution. |
| "Something visible when the PC goes offline / changes state" | **Yes**, as a normal **notification with text + app badge** (plus Focus integration). | Add the panel to the Home Screen, enable notifications, serve it over **HTTPS**, register a Web Push subscription, generate VAPID keys, and have `server.py` push on state transitions. |
| Voice commands: wake / shutdown / T3 / sleep / status | **Yes**, via **Apple Shortcuts + Siri**. No native app needed. | One shortcut per phrase using "Get Contents of URL" (POST with JSON body and headers). iPhone must be on the Tailscale tailnet when the phrase runs. |
| Siri speaking a useful status sentence | **Yes**, but best with a small additive plain-text endpoint (`GET /siri` or `?format=text`) rather than raw JSON. | Parse JSON with "Get Dictionary Value" and end with "Speak Text", or add an additive text endpoint to `server.py`. |

Honest summary: **Siri control is straightforward and cheap; web push notifications are a
moderate amount of work; a true Dynamic Island Live Activity is not achievable in this repo
without a native iOS app.** Anything claiming a Safari/PWA Live Activity API is mistaken, or is
referring to a notification, a badge, or a Live Activity owned by the native Google/Safari app.

---

## 1. Dynamic Island / Lock Screen Live Activity indicator from a web app

### 1.1 What a Live Activity actually is

Apple's own definition ([ActivityKit](https://developer.apple.com/documentation/activitykit)):

> "With the ActivityKit framework, you can start a Live Activity to share live updates from your
> app as Live Activities that appear in the Dynamic Island and other highly visible locations."

Key technical points from Apple's documentation:

- Live Activities are an **iOS 16.1+** feature; the UI is built with **SwiftUI** views inside a
  **WidgetKit** extension. The app configures, starts, updates and ends them through `Activity`
  from **ActivityKit**. ([ActivityKit framework](https://developer.apple.com/documentation/activitykit))
- Updates come either from the running app or from **ActivityKit push notifications**. A new
  Live Activity obtains a dedicated **push token from APNs**, which the app must send to the
  server; since iOS 17.2 a Live Activity can even be **started remotely** with a push
  ([Starting and updating Live Activities with ActivityKit push notifications](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications),
  [Schibsted](https://schibsted-vend.pl/blog/creating-real-time-news-experience-with-ios-live-activities/)).
- Live Activities are **not** a notification style that a push payload can opt into. The push
  token is issued to an *app process* by ActivityKit; there is no equivalent capability URL for
  a website.
- They are unavailable on visionOS, and Apple added macOS support much later (macOS 27.3 era,
  per current Apple platform availability metadata on the ActivityKit page).

### 1.2 The exact web / WebKit boundary

**A website or PWA in iOS Safari cannot start or update a Live Activity.**

Verified facts:

- Maximiliano Firtman's continuously maintained iOS PWA compatibility table (last updated
  13 Oct 2026) lists: Web Push ✅ 16.4 (installed home-screen web apps only), Declarative Web
  Push ✅ 18.4 (installed only), Web Notifications ✅ 16.4, App Badging ✅ 16.4 (installed
  only) — and no Live Activity / widget capability at all. ([iOS PWA Compatibility](https://firt.dev/notes/pwa-ios))
- MobiLoud's 2026 guide states explicitly: *"No widgets or Live Activities. These come from
  Apple's native frameworks, WidgetKit and ActivityKit, which web apps can't use."* It also
  notes PWA notifications on iPhone are **text and badges only**, with no images or action
  buttons, and no silent push. ([Do Progressive Web Apps Work on iOS? The Complete Guide for 2026](https://mobiloud.com/blog/progressive-web-apps-ios))
- Apple's Web Push documentation for the web describes Push API + Notifications API + Badging
  API + Service Workers. It mentions rich capabilities *not* being available: *"Safari doesn't
  support invisible push notifications. Present push notifications to the user immediately
  after your service worker receives them."* ([Sending web push notifications in web apps and browsers](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers))
- WebKit added **Web Push for Home Screen web apps in iOS/iPadOS 16.4**, using APNs, with
  Focus integration, per-app notification settings, Badging API, and Manifest ID for Focus
  sync across devices. ([Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/))
- **iOS 26** removed "installability" requirements: every site added to the Home Screen opens
  as a web app by default, but this changes presentation only — no new OS integrations.
  ([WebKit Features in Safari 26.0](https://webkit.org/blog/17333/new-webkit-features-in-safari-26-0/))
- **iOS 18.4** added **Declarative Web Push**, which lets a notification render from a
  standardized JSON payload *without a running service worker* (important for reliability,
  because ITP can delete service worker registrations). ([Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/))
- As of 2026, Safari 27 added **no new Home Screen web app capabilities** ([MobiLoud](https://mobiloud.com/blog/progressive-web-apps-ios));
  the recent "Live Activities in Safari" headlines concern **Safari's own download progress**
  and **macOS menu-bar Live Activities**, not an API available to web pages.

Therefore the maximum web-only result is: **a normal iOS notification on the Lock Screen /
Notification Center / Apple Watch, optionally with a badge on the Home Screen icon**, with Focus
filters applying to it. It will **not** appear as a pill in the Dynamic Island and will not be a
persistent, glanceable Live Activity.

### 1.3 The Google live sports scores case (clarification)

The user's example needs care:

- When you see live scores *inside a Safari web page* (e.g. a Google search result page), that is
  ordinary web content. It is not a Dynamic Island Live Activity.
- The Dynamic Island / Lock Screen scoreboard comes from the **native Google app**, after the
  user follows teams and enables **Live Activities** for that app. Google's own support page
  says: *"To turn on or off 'Live Activities,' go to Settings. Find the Google app. Tap Live
  Activities. Toggle Allow Live Activities"* ([Google: Change your Google notifications](https://support.google.com/websearch/answer/9919653?hl=en&co=GENIE.Platform%3DiOS)).
  Third-party guides confirm the flow is "Pin Live Score" inside the Google app and granting
  Live Activities permission ([MacRumors](https://macrumors.com/how-to/get-live-sports-scores-on-your-iphone-lock-screen),
  [Seattle Times](https://seattletimes.com/business/tech-tip-how-to-follow-the-game-even-when-you-cant-see-it-live)).
- So Google is doing exactly what section 1.1 describes: a **native app** using ActivityKit and
  APNs. A website cannot copy that behaviour.

### 1.4 What can actually be built in this repo

#### Option A (recommended web-only): Web Push PWA notifications for PC state changes

**Architecture:** the panel itself detects the online → offline transition (using the existing
15 s `recibido_ts` rule) and sends a Web Push message to each iPhone subscription. The iPhone
shows a normal notification ("PC went offline", "PC is back online", "Wake packet sent", etc.)
and can update a badge count.

Required pieces and their placement:

1. **HTTPS origin.** Service workers, `pushManager` and notifications need a **secure context**.
   `http://100.113.245.28:8080` is neither HTTPS nor localhost, so a PWA served there **cannot**
   register a service worker or subscribe to push. Use **Tailscale Serve** on the Android phone:
   enable HTTPS certificates (MagicDNS) in the Tailscale admin console and run
   `tailscale serve --bg 8080` (or the current CLI equivalent), yielding a private
   `https://<phone>.<tailnet>.ts.net` URL. Serve uses publicly trusted Let's Encrypt
   certificates for `*.ts.net`, so iOS trusts it without a custom CA profile.
   ([Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve),
   [Enabling HTTPS](https://tailscale.com/kb/1153/enabling-https)). Avoid Tailscale Funnel:
   it makes the panel public, defeating the `_guard`/tailnet security model.
   **Caveat to verify on the device:** there are intermittent iOS reports of secure-connection
   failures with Serve (`*.ts.net`), so test Safari + Shortcuts + push from the actual iPhone
   before relying on it ([tailscale/tailscale#19147](https://github.com/tailscale/tailscale/issues/19147)).
2. **Manifest + service worker served by `server.py`.** Auto-deploy currently copies only
   `index.html`, `server.py` and `wol.py` from the tarball (see `_check_and_deploy_now`). A new
   `sw.js` or `manifest.webmanifest` file **would not be deployed**. Either extend that copy
   list, or (simpler, no deploy-logic change) embed both as string constants in `server.py` and
   add `GET /manifest.webmanifest` and `GET /sw.js` routes to the existing `do_GET`.
3. **`index.html` subscribe button.** On a tap (required user gesture — Apple insists the
   permission request happen immediately inside a gesture handler), call
   `window.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: <VAPID public key bytes>})`
   or, on iOS 18.4+, the declarative form; then POST the resulting subscription (endpoint +
   `p256dh` + `auth`) to a new additive endpoint such as `POST /push/subscribe`.
   With Declarative Web Push, `window.pushManager` exists without a service worker
   ([Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/)).
4. **VAPID keys on the Android phone.** Generate a P-256 key pair and keep the private key in
   Termux; the public key goes into the subscription call. Apple requires the VAPID JWT
   `sub` claim to be a `mailto:` or `https:` URL, `aud` to be the push-service origin, and a
   short expiry (≤ 1 day); the key must match the subscription's `applicationServerKey`.
   ([Apple web push docs](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers))
5. **Transition detector in `server.py`.** A small daemon thread can check every few seconds:
   if a previously online PC becomes stale (`now - recibido_ts > 15`), push once ("PC offline");
   when fresh status returns, push once ("PC online") and clear the flag. Keep this outside the
   HTTP handler, mirroring the existing battery/deploy background-thread pattern. Do not touch
   `store_status()`'s 5 s disk-throttle or the status JSON keys.
6. **Send to Apple's push service.** POST to the subscription endpoint (`https://*.push.apple.com`
   must be reachable from the phone) with `Authorization: vapid ...`, `TTL`, `Urgency: high`,
   `Topic: pc-state` (≤ 32 URL-safe chars), and a ≤ 4 KB encrypted payload. Apple documents all
   of these headers and error codes, including `410` (expired subscription — remove it from
   storage) and `BadJwtToken`. ([Apple web push docs](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers))
   Use the declarative JSON format so the notification renders even if the service worker was
   evicted:

   ```json
   {
     "web_push": 8030,
     "notification": {
       "title": "remote_control",
       "lang": "en-US",
       "dir": "ltr",
       "body": "PC went offline",
       "navigate": "https://<phone>.<tailnet>.ts.net/",
       "silent": false,
       "app_badge": "1"
     }
   }
   ```

   `navigate` is required by the declarative format and should point at the HTTPS panel URL.
   Keep a compatible service-worker `push` handler that calls `showNotification(...)` for older
   browsers. ([Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/))
7. **iOS-side setup (one time).** Open the HTTPS URL in Safari → Share → Add to Home Screen →
   keep **Open as Web App** on (default since iOS 26) → open from the icon → tap the panel's
   "Enable alerts" button → allow notifications. Optionally use Settings → Notifications to put
   the web app into a Focus mode. A Manifest `id` plus a stable home-screen name keeps Focus
   settings in sync if the app is installed on several devices.
   ([WebKit Web Push for iOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/),
   [Apple iPhone guide: open as web app](https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios))
8. **Keep the HTTP contract intact.** All of the above are *additive* endpoints/routes. Do not
   rename or change `/status`, `/cmd`, `/cmd/ack`, etc. New mutating endpoints must call
   `_guard(mutating=True)` so the `Host`/`Origin`/token protections still apply. Because changes
   to `server.py` trigger the panel's `os.execv` self-restart, deploy in one coherent commit.

**Limitations to be honest about:** no Dynamic Island; no Lock Screen "live" card; no images or
action buttons in iOS web notifications; no silent/background refresh; every push must be
visible; subscription endpoints are capability URLs and must be stored securely; ITP may clean
up storage for Safari-based sites (Home Screen web apps are treated better, and declarative push
survives service-worker removal); tapping the notification opens the web app, which still needs
an active Tailscale connection to render live data.

#### Option B (only way to get a real Live Activity): native companion app

A small native iOS app can reuse **the exact same HTTP contract**: fetch `http(s)://…/status`
and POST `/cmd`, then own an ActivityKit Live Activity showing, for example, `CPU 23% · RAM 41%
· T3 open` with buttons that POST commands via `LiveActivityIntent`. Remote start/update needs
APNs ActivityKit push tokens and a server component (the same `server.py` can send them).
Price: Apple Developer Program membership, SwiftUI + WidgetKit code, App Store/TestFlight or
sideload distribution, signing, and a second codebase. An **App Clip** is a lighter delivery
option but still native code and still subject to Apple's entitlements/availability; verify
current Live Activity support in App Clips before committing to it. This is the only path that
actually produces a Dynamic Island indicator.

#### Option C (zero server changes): Shortcuts as "pull + notify"

Shortcuts can GET `/status`, branch on online/offline and call **Show Notification**, and can be
added to Siri or a personal automation. However, **there is no generic webhook trigger for
Shortcuts**: personal automations fire on device events (time, location, Focus, charger, etc.),
not when a server changes state. This is good for on-demand Siri status and manual checks, not
for instant offline alerts.

### 1.5 Decision matrix

| Approach | Dynamic Island | Lock Screen alert | Setup effort | Notes |
|---|---|---|---|---|
| Native app + ActivityKit | ✅ | ✅ (Live Activity) | High | Only real solution for the pill; requires APNs tokens and Apple Developer Program. |
| Home Screen PWA + Web Push | ❌ | ✅ (normal notification + badge) | Medium | Best value inside this repo; needs HTTPS via Tailscale Serve, VAPID, subscribe flow. |
| Shortcuts + "Show Notification" | ❌ | ⚠️ only when a shortcut/automation runs | Low | Cannot react to server-side state changes in real time. |
| Safari tab polling only (current `index.html`) | ❌ | ❌ | Already done | Works only while the page is open; `visibilitychange` deliberately stops polling when hidden. |

---

## 2. Siri commands & Apple Shortcuts on iPhone

### 2.1 How Siri reaches the panel

Apple's Shortcuts guide is explicit: *"You can ask Siri to run any shortcut in your collection…
Activate Siri… then say the name of a shortcut… After running the shortcut, Siri tells you the
result."* ([Run shortcuts with Siri](https://support.apple.com/guide/shortcuts/run-shortcuts-with-siri-apd07c25bb38/ios)).
A custom voice phrase can also be recorded with **Add to Siri** in the shortcut's details
([Add Siri Shortcuts on iPhone](https://support.apple.com/guide/iphone/iph7d118960c/13.0/ios/13.0)).
So the voice phrase never goes to the server; Siri runs a local Shortcut, which makes the HTTP
request from the iPhone. **The iPhone must be connected to the Tailscale tailnet at that moment**
(or the HTTPS `*.ts.net` URL from Option A must resolve through Tailscale).

Suggested phrase → endpoint mapping:

| Spoken phrase | Shortcut name / trigger | HTTP request |
|---|---|---|
| "Hey Siri, turn on my PC" | `turn on my PC` | `POST /cmd` body `{"comando":"wake","ts":…}` |
| "Hey Siri, shut down my PC" | `shut down my PC` | `POST /cmd` body `{"comando":"shutdown","ts":…}` (optionally an "Ask for Input"/confirm step) |
| "Hey Siri, open T3 Code" | `open T3 Code` | `POST /cmd` body `{"comando":"t3-open","ts":…}` |
| "Hey Siri, close T3 Code" | `close T3 Code` | `POST /cmd` body `{"comando":"t3-close","ts":…}` |
| "Hey Siri, PC status" | `PC status` | `GET /status` (or the proposed `GET /siri` text endpoint) |
| "Hey Siri, sleep mode" | `sleep mode` | `GET /status` → parse `sleep_active` → POST `sleep-off`/`sleep-on` |

Use distinctive names that do not collide with built-in Siri commands; Apple notes that a
shortcut with the same name as a standard command takes precedence, which can be surprising.

### 2.2 "Get Contents of URL" mechanics

Apple's own tutorial confirms the action supports GET/POST/PUT/PATCH/DELETE, that POST adds a
**Request Body** (JSON, Form or File), and that the result is JSON that must be parsed
([Request your first API](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios)).
The action's parameter list is `URL`, `Method`, `Headers` ("+ Add new header"), and `Request Body`
([Matthew Cassinelli's action reference](https://matthewcassinelli.com/actions/get-contents-of-url/)).
Community examples show headers such as `Authorization` being set this way
([openHAB community](https://community.openhab.org/t/sending-commands-to-rest-api-via-siri-shortcuts-whilst-outside-of-lan/84458)).

Concrete configuration for a command shortcut:

- URL: `http://100.113.245.28:8080/cmd` (or the HTTPS Tailscale URL)
- Tap **Show More** → Method: **POST**
- Headers:
  - `Content-Type` = `application/json`
  - `X-Panel-Token` = `<PANEL_TOKEN>` — only if `PANEL_TOKEN` is set on the phone. Alternatively
    use the server's already-supported query fallback: `…/cmd?token=<PANEL_TOKEN>`.
  - Leave `Host` alone: with the IP URL it is `100.113.245.28:8080` and passes `_host_ok`. With a
    Tailscale HTTPS URL it is `<phone>.<tailnet>.ts.net`, which passes `_host_ok` via MagicDNS
    resolution to a `100.64.0.0/10` address.
- Request Body → **JSON**, with keys:
  - `comando` — **Text**: `wake`, `shutdown`, `t3-open`, `t3-close`, `sleep-on`, `sleep-off`
  - `ts` — **Number**: any changing value. The server stores it verbatim and the agent only
    uses it as part of the idempotency key `(comando, ts)`; it does **not** need to be a real
    Unix timestamp. A **Random Number** action (e.g. 1…1 000 000 000) is the simplest reliable
    way to avoid two consecutive identical commands being suppressed as duplicates. A timestamp
    converted to a number also works; Apple documents how Shortcuts reads Unix timestamps with
    `Date`/`Adjust Date`/`Format Date` ([Format Date timestamps](https://support.apple.com/guide/shortcuts/format-date-timestamps-apdfb33b0e17/ios)).
- The server sends no `Origin` header from Shortcuts, so `_origin_ok` accepts the request.

For the status shortcut: plain **GET** to `http://100.113.245.28:8080/status`; the response is a
JSON dictionary, accessible with **Get Dictionary Value** (keys `hostname`, `ip`, `cpu_percent`,
`t3_open`, `sleep_active`, and nested `ram.percent`). ([Get Dictionary Value](https://support.apple.com/guide/shortcuts/get-dictionary-value-action-apdf01294032/ios))

### 2.3 JSON vs plain text / spoken responses

By default the last action's output becomes the shortcut result, and *"Siri tells you the
result"* ([Apple](https://support.apple.com/guide/shortcuts/run-shortcuts-with-siri-apd07c25bb38/ios)).
Passing a JSON dictionary straight to the end is therefore a poor experience: Siri may read or
show raw JSON such as `{"ok": true, "comando": "wake"}`.

Best practice:

- End every command shortcut with a **Speak Text** action containing a short, human sentence
  ("Wake packet sent", "Shutting down the PC") — optionally gated by an **If** that checks
  `Get Dictionary Value → ok` from the POST response.
- Optionally add **Show Notification** for a persistent confirmation
  ([Show Notification](https://support.apple.com/guide/shortcuts/use-the-show-notification-action-apd2175adcab/ios)).
- For `PC status`, add a tiny server endpoint returning `Content-Type: text/plain` with a single
  sentence. A plain-text result is spoken naturally and avoids brittle JSON parsing in
  Shortcuts. This is optional; the existing JSON `/status` can still be parsed with
  "Get Dictionary Value".

### 2.4 Recommended additive, Siri-friendly endpoints

These keep the existing contract unchanged (new routes only):

- `GET /siri` → `200 text/plain`, e.g.
  `PC online on desk-3700 at 100.69.149.63. CPU 12 percent, RAM 41 percent. T3 Code is open. Sleep mode is off.`
  If `recibido_ts` is older than 15 s or no data exists: `PC is off.`
- `GET /siri?format=json` (or leave `/status` as-is) for structured clients.
- `POST /cmd?spoken=1` → `200 text/plain` with the same short confirmation as the UI
  (`✓ wake command sent`). The existing JSON response remains the default.
- Keep `GET /version`, `GET /battery` unchanged; they already work from Shortcuts.

`_send()` already accepts a `ctype` argument, so `self._send(200, text, "text/plain")` is a
minimal change. Any new mutating route must still go through `_guard(mutating=True)`.

**Security note:** putting `PANEL_TOKEN` in a shortcut is safer than putting it in a spoken
phrase; the phrase is only a local trigger. The token lives in the Shortcuts database and is
synced via iCloud to the user's own devices. Prefer the `X-Panel-Token` header; use the
`?token=` fallback only if the header proves awkward. Do not expose the panel with Tailscale
Funnel just to make Siri work — Siri runs on the iPhone, which is already on the tailnet.

### 2.5 Shortcut recipes (user setup)

One-time preparation: install Tailscale on the iPhone and keep it connected; verify in Safari
that `http://100.113.245.28:8080/status` returns JSON. Then, in the Shortcuts app:

1. Tap **+** → **New Shortcut** → **Rename** it exactly as the phrase you want to speak.
2. Add a **Text** action containing the URL `http://100.113.245.28:8080/cmd`.
3. Add **Get Contents of URL**; tap **Show More**; choose **POST**; add the two headers
   (`Content-Type`, and `X-Panel-Token` if needed); set Request Body to **JSON** and add
   `comando` (Text) and `ts` (Number — e.g. from **Random Number**).
4. Add **Get Dictionary Value** with key `ok` on the URL action's output, then an **If** action:
   - Yes → **Speak Text**: `Wake command sent.`
   - No → **Speak Text**: `The panel rejected the command.`
5. Optionally add **Show Notification** with the same sentence.
6. Open the shortcut's details and use **Add to Siri** to record a custom phrase if the name
   alone is not reliable.

Repeat with the command values from the table in 2.1. For "PC status", use a **GET** to
`http://100.113.245.28:8080/siri` (or `/status` plus "Get Dictionary Value" actions) and finish
with **Speak Text**. For a single "sleep mode" shortcut, GET `/status`, extract `sleep_active`,
then branch with **If** and POST `sleep-off` or `sleep-on` accordingly.

Apple's Shortcuts guide documents creating shortcuts, adding actions, renaming, and Siri
invocation ([Create a custom shortcut](https://support.apple.com/guide/shortcuts/create-a-custom-shortcut-apd84c576f8c/ios),
[Rename shortcuts](https://support.apple.com/guide/shortcuts/rename-shortcuts-apdd57094696/ios)).

### 2.6 Gotchas and verification

- **Tailnet reachability:** the request originates on the iPhone. If Tailscale is disconnected,
  or the Android phone running the panel is asleep/offline, the shortcut fails. Web Push (1.4)
  is better for *inbound* alerts precisely because Apple's APNs delivers them even when the
  tailnet is idle.
- **Locked device:** Apple notes that a shortcut that opens an app while locked asks for
  unlocking first ([Run shortcuts with Siri](https://support.apple.com/guide/shortcuts/run-shortcuts-with-siri-apd07c25bb38/ios));
  pure network shortcuts do not open an app, but Siri must still be enabled on the device.
- **HomePod / other devices:** Siri can run shortcuts from HomePod, Apple Watch and Mac, but the
  HTTP call happens on that device; it would need its own Tailscale connectivity. Keep these
  shortcuts phone-scoped unless the tailnet covers those devices too.
- **Idempotency:** always send a changing `ts`; see 2.2. Do not rely on the agent's
  `(comando, ts)` dedupe with a constant value.
- **Shutdown confirmation:** `POST /cmd` with `shutdown` has no server-side confirmation step.
  Add an "Ask for Input" / "Choose from Menu" action in the shortcut if accidental shutdowns
  are a concern; the existing web UI already uses a modal.
- **Verification checklist after implementation:**
  1. `python3 -m py_compile panel_celular/server.py` and `bash -n` for shell files (unchanged here).
  2. Locally: `curl -s localhost:8080/status`, `curl -s localhost:8080/siri` (if added),
     and `curl -s -X POST -H 'Content-Type: application/json' -d '{"comando":"wake","ts":123}' localhost:8080/cmd`.
  3. From the iPhone over Tailscale: Safari GET `/status`; run each Siri phrase; confirm the
     spoken confirmation and the PC agent log (`journalctl --user -u panel-agent.service -f`).
  4. If Web Push is added: confirm the subscription survives closing the PWA, that a forced
     offline transition produces exactly one notification, and that a 410 response prunes the
     stored subscription.

---

## 3. Recommended implementation order

1. **Siri/Shortcuts first** — no server change is strictly required; only optional additive
   text endpoints. Highest value for the smallest effort, and it uses the existing contract.
2. **Web Push PWA notifications** — enable Tailscale Serve HTTPS, embed manifest + service
   worker in `server.py`, add subscribe endpoints and a transition watchdog, test on the real
   iPhone. Gives real "PC went offline" alerts without a native app.
3. **Native companion app** — only if a genuine Dynamic Island / Lock Screen Live Activity with
   glanceable live metrics and tap-to-command buttons is a hard requirement.

No files other than this research document were modified.

---

## 4. Sources (verified 2026-11-24)

Primary / Apple:

- [ActivityKit — Apple Developer](https://developer.apple.com/documentation/activitykit)
- [Starting and updating Live Activities with ActivityKit push notifications](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications)
- [Live Activities essentials — WWDC26](https://developer.apple.com/videos/play/wwdc2026/223)
- [Sending web push notifications in web apps and browsers — Apple Developer](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)
- [Web Push for Web Apps on iOS and iPadOS — WebKit](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- [Meet Declarative Web Push — WebKit](https://webkit.org/blog/16535/meet-declarative-web-push/)
- [WebKit Features in Safari 26.0](https://webkit.org/blog/17333/new-webkit-features-in-safari-26-0/)
- [Use Siri to run shortcuts with your voice — Apple Shortcuts User Guide](https://support.apple.com/guide/shortcuts/run-shortcuts-with-siri-apd07c25bb38/ios)
- [Request your first API — Apple Shortcuts User Guide](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios)
- [Handling timestamps using Format Date — Apple Shortcuts User Guide](https://support.apple.com/guide/shortcuts/format-date-timestamps-apdfb33b0e17/ios)
- [API limitations in Shortcuts](https://support.apple.com/guide/shortcuts/api-limitations-apd891a6c84e/ios)
- [Create a custom shortcut](https://support.apple.com/guide/shortcuts/create-a-custom-shortcut-apd84c576f8c/ios)
- [Show Notification action](https://support.apple.com/guide/shortcuts/use-the-show-notification-action-apd2175adcab/ios)
- [Add Siri Shortcuts on iPhone](https://support.apple.com/guide/iphone/iph7d118960c/13.0/ios/13.0)
- [Change your Google notifications — Google Support](https://support.google.com/websearch/answer/9919653?hl=en&co=GENIE.Platform%3DiOS)

Independent / implementation references:

- [iOS PWA Compatibility — Maximiliano Firtman](https://firt.dev/notes/pwa-ios) (updated 2026-10-13)
- [Do Progressive Web Apps Work on iOS? The Complete Guide for 2026 — MobiLoud](https://mobiloud.com/blog/progressive-web-apps-ios) (updated 2026-10-21)
- [Get Contents of URL action reference — Matthew Cassinelli](https://matthewcassinelli.com/actions/get-contents-of-url/)
- [Push API — MDN](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Enabling HTTPS on a tailnet — Tailscale](https://tailscale.com/kb/1153/enabling-https)
- [iOS secure-connection reports with Tailscale Serve — tailscale#19147](https://github.com/tailscale/tailscale/issues/19147)
- [How to Get Live Sports Scores on Your iPhone Lock Screen — MacRumors](https://macrumors.com/how-to/get-live-sports-scores-on-your-iphone-lock-screen)

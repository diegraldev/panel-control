# Implementation plan — iOS feedback (Web Push) and Siri Shortcuts

**Author:** Agent 2 (Architecture & Planning)
**Date:** 2026-11-24
**Status:** ready for Agent 3 to implement
**Research basis:** [RESEARCH-ios-dynamic-island-siri.md](RESEARCH-ios-dynamic-island-siri.md)

This plan converts the research findings into a concrete, file-by-file implementation. It
covers two additive features on top of the existing panel:

1. **Siri voice control** (`GET /siri`, `POST /cmd?spoken=1`) — no extra dependencies, no HTTPS
   requirement, uses the existing HTTP contract.
2. **Web Push notifications on iPhone** for PC online/offline transitions — requires HTTPS
   (Tailscale Serve), a PWA added to the Home Screen, VAPID keys, and RFC 8291 payload
   encryption, all implemented in `server.py` background threads.

A true Dynamic Island / Live Activity is **out of scope**: it requires a native iOS app with
ActivityKit + APNs (research §1.2). This plan deliberately delivers the maximum that a
web-only solution can: a normal iOS notification with text + app badge, plus full Siri voice
control. The setup guide must state this limitation plainly.

---

## 1. Non-negotiable constraints (how this plan satisfies them)

| Constraint (AGENTS.md) | How the design complies |
|---|---|
| Rule 1 — HTTP contract unchanged | Every existing endpoint, JSON field and all 7 `CMD_VALUES` stay exactly as-is. All new routes are new paths or a new **query flag** on `/cmd`; the default JSON response of `POST /cmd` is byte-for-byte unchanged. |
| Rule 5 — no forks in hot path | No new `subprocess` call anywhere near request handling or sampling. Web Push uses pure in-process crypto + `urllib` in a daemon worker thread. |
| Rule 6 — no blocking in HTTP handlers | Transition detection runs in its own daemon thread; push delivery runs in a single daemon queue-worker. POST `/cmd` keeps using the existing `wake_pc_async()` daemon thread. No handler ever opens a push-service connection. |
| Rule 7 — security | New mutating routes (`/push/subscribe`, `/push/unsubscribe`, `/push/test`) fall through the **existing** `self._guard(mutating=True)` at the top of `do_POST` — no bypass. New GET routes call the existing `self._guard()`. No use of Tailscale Funnel. |
| Rule 8 — idempotency | The `(comando, ts)` dedupe in `agent.py` is untouched. `POST /cmd` gains a *fresh millisecond `ts`* when the client omits it, which **strengthens** dedupe instead of weakening it. |
| Rule 9 — branch + PR | Agent 3 works on `feat/ios-dynamic-island-siri`, opens a PR; never touches `main` directly. |
| Rule 10 — English prose | All new docs, comments and responses in English. |
| Auto-deploy (ADR-003/004) | `manifest.webmanifest` and `sw.js` are **embedded as string constants in `server.py`** and served by new routes. The deploy copy list stays `index.html`, `server.py`, `wol.py` — no deploy-logic change. `index.html` still hot-reloads; the one-time `server.py` restart on deploy remains expected behaviour. |

---

## 2. File-by-file change map

| File | Action | Notes |
|---|---|---|
| `panel_celular/server.py` | **Modify** (all server-side logic) | New imports, env vars, state, crypto helpers, background threads, 7 new routes, 2 embedded assets. Largest change. |
| `panel_celular/index.html` | **Modify** (small, additive) | Manifest link + "Enable alerts" / "Disable alerts" button + JS. Zero changes to existing polling, buttons or IDs. Hot-reloads via deploy. |
| `panel_celular/boot_panel` | **Modify** (comments + optional exports) | **Not auto-deployed** — must also be applied once by hand on the phone (see setup guide). |
| `.gitignore` | **Modify** | Add `vapid.json`, `subscriptions.json`. |
| `docs/IOS-SIRI-AND-PUSH-SETUP.md` | **Create** | Step-by-step iPhone + phone setup and the 6 exact Shortcut recipes. |
| `tests/test_server.py` | **Create** | stdlib `unittest`, ephemeral port, contract + new endpoints + transition tests. |
| `docs/DECISIONS.md` | **Add ADR-013** | Record the "embed PWA assets in server.py" and "graceful crypto fallback" decisions. |
| `docs/AGENT_CONTEXT.md` | **Modify** | Extend the HTTP surface table and env-var table with the additive items. |
| `README.md` | **Modify (optional)** | One short section linking the setup guide. Do **not** reintroduce `REPORT.html`. |
| `panel_pc/agent.py` | **No change** | Existing agent already satisfies the contract; spoken `ts` is just a normal number to it. |

---

## 3. `panel_celular/server.py` — detailed design

### 3.1 New module-level constants and environment variables

Add next to the existing constant block (lines 45–69). All defaults keep the current behaviour
when the new features are unconfigured.

```python
import base64
import queue
import urllib.parse

# Web Push configuration (all optional; absence disables push, never the panel)
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "https://github.com/diegraldev/remotecontrol")
PANEL_PUBLIC_URL = os.environ.get("PANEL_PUBLIC_URL", "").rstrip("/")  # https://<phone>.<tailnet>.ts.net
PUSH_TTL = int(os.environ.get("PUSH_TTL", "60"))
PUSH_CHECK_INTERVAL = float(os.environ.get("PUSH_CHECK_INTERVAL", "2"))
OFFLINE_MS = 15000          # same 15 s rule already used by index.html
DATA_DIR = os.environ.get("PANEL_DATA_DIR", BASE)   # tests point this at a temp dir
VAPID_FILE = os.path.join(DATA_DIR, "vapid.json")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
TOPIC = "pc-state"          # <= 32 URL-safe chars, required by Apple
```

State files for the existing contract stay in `BASE` (no behaviour change). The two new files
live in `DATA_DIR` so the test suite can isolate them with `PANEL_DATA_DIR`.

**Graceful crypto dependency** (Termux Python 3 has no stdlib AES-GCM/ECDH/ECDSA):

```python
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    _CRYPTO_OK, _CRYPTO_ERR = True, ""
except Exception as e:              # pragma: no cover - depends on the device
    hashes = serialization = ec = decode_dss_signature = AESGCM = HKDF = None
    _CRYPTO_OK, _CRYPTO_ERR = False, str(e)
```

Rationale and rollout:

- `cryptography` is the minimal realistic dependency. Pure stdlib cannot do AES-128-GCM, ECDH
  or ECDSA. On Termux install it with `pkg install python-cryptography` (preferred — repo
  package); `pip install cryptography` also works but may need `pkg install openssl rust`
  and a source build. The setup guide documents both and the check command
  `python3 -c "import cryptography; print(cryptography.__version__)"`.
- If the import fails, `_CRYPTO_OK = False`: the panel, Siri endpoints and every existing
  endpoint keep working; push endpoints return a clear `503` with the import error in the body
  (for `/push/config` the JSON instead carries `push_available: false` and `error`). This is
  critical — push must never be able to break the core panel.
- Alternative rejected: shelling out to the `openssl` CLI. It would add forks (Rule 5), and
  OpenSSL's CLI cannot conveniently produce AES-GCM records. Do not reintroduce it.

### 3.2 New in-memory state and locks

```python
_subscriptions = {}            # endpoint -> {"endpoint", "keys": {...}, "added_ts"}
_subscriptions_lock = threading.Lock()
_vapid = None                  # lazily loaded {"public_key_b64u", "private_key_pem"}
_vapid_lock = threading.Lock()
_pc_online = None              # None = unknown/baseline not yet established
_transition_lock = threading.Lock()
_push_queue = queue.Queue(maxsize=64)
_last_ts_ms = 0
_ts_lock = threading.Lock()
```

All access to the new dicts goes through their locks; snapshots are taken before any network
work. `load_state()` is extended to read `SUBSCRIPTIONS_FILE` into `_subscriptions` (using the
existing `read_json`) so subscriptions survive a `server.py` restart. `VAPID_FILE` is **not**
re-generated on every restart — it is persisted once and reused (see 3.4).

### 3.3 Pure transition decision function (testable without threads)

```python
def is_online(status, now=None):
    """Same rule as index.html: fresh recibido_ts from a PC that ever checked in."""
    now = now if now is not None else time.time()
    return bool(status.get("recibido_en_celular")) and \
           (now - float(status.get("recibido_ts") or 0)) * 1000 < OFFLINE_MS


def transition_decision(status, previous, now=None):
    """Return (event, new_state). event is None | 'offline' | 'online'.

    Pure function: no I/O, no locks, no side effects. The first observation only
    establishes the baseline, so a restart while the PC is off sends no spurious alert.
    """
    online = is_online(status, now)
    if previous is None:
        return None, online
    if previous and not online:
        return "offline", False
    if not previous and online:
        return "online", True
    return None, online
```

`current_status()` returns `{"status": "no data"}` when nothing has been published yet;
`is_online()` returns `False` for that dict, which is the correct "PC off" baseline.

### 3.4 VAPID key management and RFC 8291 encryption

All of these are module-level helpers, called only from the push worker (or once at startup for
`get_vapid()`); none is called from a request path after startup.

`b64u(raw: bytes) -> str` and `unb64u(s: str) -> bytes`: base64url **without padding**
(`base64.urlsafe_b64encode(...).rstrip(b"=")` / pad with `=` on decode).

`get_vapid()`:

1. Fast path: return module `_vapid` if set.
2. Under `_vapid_lock`: if `VAPID_FILE` exists, read `private_key_pem` with
   `serialization.load_pem_private_key(pem.encode(), password=None)`, derive the public key's
   X962 **UncompressedPoint** bytes (65 bytes), return the dict.
3. Else generate `ec.generate_private_key(ec.SECP256R1())`, serialize with
   `PrivateFormat.PKCS8`, `NoEncryption()`, store
   `{"public_key_b64u": b64u(pub65), "private_key_pem": pem_str}` via `write_json`, then
   `os.chmod(VAPID_FILE, 0o600)`.
4. Raise `RuntimeError(_CRYPTO_ERR)` immediately when `_CRYPTO_OK` is false.
5. Call once from `main()` before `serve_forever()` (one-time keygen, no fork). If it raises,
   log `[PUSH] disabled: ...` and leave `_vapid = None`; the server continues normally.

`make_vapid_auth(endpoint: str) -> str`:

- `aud` = `urllib.parse.urlsplit(endpoint)`, then `f"{scheme}://{netloc}"` (Apple requires the
  push service origin, e.g. `https://web.push.apple.com`).
- JWT header `{"alg": "ES256", "typ": "JWT"}`; claims
  `{"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUBJECT}` (≤ 24 h expiry).
- `signing_input = b64u(header_json) + b"." + b64u(claims_json)`.
- `der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))`; **convert DER → raw r||s**:
  `r, s = decode_dss_signature(der)`, `raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")`.
  Skipping this conversion produces an invalid JWT (`BadJwtToken` from Apple) — call it out in
  a code comment.
- Return `f'vapid t={signing_input}.{b64u(raw)}, k={vapid["public_key_b64u"]}'`.

`encrypt_payload(plaintext: bytes, keys: dict) -> bytes` (RFC 8291 §2):

1. `client_pub = unb64u(keys["p256dh"])` (65 bytes), `auth = unb64u(keys["auth"])` (16 bytes).
2. `ephemeral = ec.generate_private_key(ec.SECP256R1())`; `as_pub = ephemeral.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)`.
3. `shared = ephemeral.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_pub))`.
4. `info = b"WebPush: info\x00" + client_pub + as_pub`;
   `ikm = HKDF(hashes.SHA256(), length=32, salt=auth, info=info).derive(shared)`.
5. `salt = os.urandom(16)`;
   `cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)`;
   `nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)`.
   (Two HKDF calls with the same salt+IKM reuse the same PRK — equivalent to RFC 8291's
   extract-then-expand.)
6. `record = plaintext + b"\x02"` (delimiter, no extra padding — payload is far below 4 KB).
   `ct = AESGCM(cek).encrypt(nonce, record, None)` (AAD empty; `encrypt()` appends the 16-byte
   tag).
7. Return `salt + (4096).to_bytes(4, "big") + bytes([65]) + as_pub + ct`.

`send_web_push(endpoint, keys, payload: dict) -> "ok" | "prune" | "error"`:

- `body = encrypt_payload(json.dumps(payload, separators=(",", ":")).encode(), keys)`.
- `urllib.request.Request(endpoint, data=body, method="POST", headers={
  "Authorization": make_vapid_auth(endpoint),
  "Content-Type": "application/octet-stream",
  "Content-Encoding": "aes128gcm",
  "TTL": str(PUSH_TTL), "Urgency": "high", "Topic": TOPIC})`.
- `urlopen(..., timeout=10).read()`; 200/201 → `"ok"`.
- `HTTPError 410` or `404` → `"prune"` (caller removes the subscription — Apple documents this).
- anything else (429, 5xx, timeout, DNS) → log and return `"error"`; **no retry loop inside
  the worker** (never block the queue), no crash of the loop.

### 3.5 Background threads (started from `main()` only)

```python
def start_background_workers():
    threading.Thread(target=_transition_loop, daemon=True).start()
    threading.Thread(target=_push_worker, daemon=True).start()
```

- `_transition_loop()`: every `PUSH_CHECK_INTERVAL` seconds:
  1. `status = current_status()` (already lock-protected).
  2. `with _transition_lock:` compute `event, new = transition_decision(status, _pc_online)`;
     assign `_pc_online = new`; if no event, continue.
  3. Build payload via `_notification_payload(event)` (below), snapshot subscriptions under
     `_subscriptions_lock`, and `enqueue_push(payload)`.
  - **Debounce:** require the new state to be confirmed by **two consecutive polls** before
    emitting (a small `_pending_event` + counter under `_transition_lock`). With a 2 s poll and
    the 15 s freshness threshold this only filters boundary flapping; it does not add
    meaningful latency. Reset the counter when the observation flips back.
  - Entire loop body wrapped in `try/except Exception` with a log, so one bad subscription can
    never kill it.
- `_push_worker()`: `while True: item = _push_queue.get(); ...` → call `send_web_push`;
  if result `"prune"`, remove that endpoint from `_subscriptions` and rewrite
  `SUBSCRIPTIONS_FILE` atomically with `write_json`; finally `_push_queue.task_done()`.
  Wrap in `try/except` per item.
- `enqueue_push(payload)`: snapshot subscriptions, `put_nowait((endpoint, keys, payload))` for
  each; on `queue.Full`, log and drop (state alerts are ephemeral; never block a request or the
  detector).
- `main()` additions (all before `serve_forever()`, after `load_state()`):
  `ASSETS = Assets(...)`, existing battery/deploy threads unchanged, then
  `get_vapid()` in a try/except, then `start_background_workers()`.

**Why not hook `store_status()`?** That function runs in the POST handler thread (agent
publishing every second). Putting detection or push there would violate Rule 6 and add latency
to the hot path. The detector polls the in-memory cache instead — zero work per publish.

### 3.6 Notification payload

```python
def _notification_payload(event):
    offline = (event == "offline")
    body = "PC went offline" if offline else "PC is back online"
    return {
        "web_push": 8030,                       # declarative web push marker (WebKit)
        "notification": {
            "title": "remote_control",
            "lang": "en-US", "dir": "ltr",
            "body": body,
            "navigate": PANEL_PUBLIC_URL + "/",  # required by declarative format
            "silent": False,
            "app_badge": "1" if offline else "0",  # clear the badge when back online
        },
    }
```

One payload format serves both paths: on iOS 18.4+ the OS renders the declarative JSON
directly (survives service-worker eviction); on older versions `sw.js` parses the same JSON and
calls `showNotification()`. The JSON is < 400 bytes, well under Apple's 4 KB limit.

`_notification_payload("test")` uses body `"Test notification from remote_control"` and
`app_badge: "1"`.

### 3.7 Embedded PWA assets (new routes, no new deployable files)

Add two string constants near the assets section (lines 357–388). Use normal Python triple-quoted
strings; the SVG icon data URI is the exact one already in `index.html` (URL-encoded, single
quotes inside).

- `MANIFEST_JS` → JSON string:
  `{"id":"/","name":"remote_control","short_name":"remote","start_url":"/","scope":"/",
  "display":"standalone","background_color":"#050505","theme_color":"#050505",
  "icons":[{"src":"data:image/svg+xml,...","sizes":"any","type":"image/svg+xml","purpose":"any"}]}`
  The stable `id: "/"` keeps Focus/notification settings in sync if installed on several
  devices.
- `SW_JS` → minimal service worker:
  - `push` handler: parse `event.data.json()` when possible, read `.notification`, call
    `self.registration.showNotification(title, {body, lang, dir, tag: "pc-state", renotify: true,
    data: {url: navigate || "/"}})` inside `event.waitUntil(...)`. The `tag` replaces the previous
    state notification instead of stacking duplicates.
  - `notificationclick` handler: `event.notification.close()`, then focus an existing window
    whose URL matches the target, else `clients.openWindow(target)`.
  - **No caching/precache logic** — the panel needs live data and must not serve stale status.

**Auto-deploy statement (explicit):** these two routes mean `manifest.webmanifest` and `sw.js`
are generated from `server.py` itself, so the deploy list in `_check_and_deploy_now()`
(`for name in ("index.html", "server.py", "wol.py")`) stays unchanged. The alternative — adding
the two files to the tarball copy list — is deliberately rejected because it would require a
deploy-logic change and a restart-sensitive file set. The only deploy consequence is the normal
one: `index.html` changes hot-reload, `server.py` changes trigger `_flush_state()` + `os.execv`.

### 3.8 New/changed routes in `Handler`

Add these to `do_GET` **before** the final `else` (all call `self._guard()` via the existing call
at the top, so no new guard code is needed):

| Route | Response | Notes |
|---|---|---|
| `GET /siri` | `200 text/plain; charset=utf-8` | Spoken status sentence — see 3.9. |
| `GET /manifest.webmanifest` | `200 application/manifest+json` | Body = `MANIFEST_JS`. No ETag needed. |
| `GET /sw.js` | `200 application/javascript; charset=utf-8` | Body = `SW_JS`. Add `Service-Worker-Allowed: /` via `_send(extra=...)`. |
| `GET /push/config` | `200 application/json` | `{"push_available": bool, "public_key": str\|null, "token_required": bool, "public_url": str, "error": str\|null}`. `push_available` = `_CRYPTO_OK and bool(PANEL_PUBLIC_URL)`; `public_key` = `get_vapid()["public_key_b64u"]` when available. Never 5xx — the UI degrades gracefully. |
| `GET /push/subscriptions` | `200 application/json` | Debug: `{"count": N, "endpoints": [url, ...]}` (no keys/secrets). |

Add these to `do_POST` after the existing branches (they inherit the existing
`self._guard(mutating=True)` call; **do not add a second guard**):

| Route | Request body | Success response |
|---|---|---|
| `POST /push/subscribe` | `{"subscription": {"endpoint": "https://…", "keys": {"p256dh": "…", "auth": "…"}}}` | `{"ok": true, "subscriptions": N}`; replaces an existing record for the same endpoint. |
| `POST /push/unsubscribe` | `{"endpoint": "https://…"}` | `{"ok": true}`; no-op if unknown. |
| `POST /push/test` | `{}` (or empty body) | `{"ok": true, "queued": N}`; enqueues a test notification for every subscription. |

Validation for `/push/subscribe`: require `https://` endpoint, both `keys` strings, and
(when `_CRYPTO_OK`) that `unb64u(p256dh)` is 65 bytes and `unb64u(auth)` is 16 bytes. Return
`400 {"error": "..."}` on failure. If `push_available` is false, return
`503 {"error": "Web Push unavailable: <reason>"}` (reason = crypto import error or missing
`PANEL_PUBLIC_URL`). Persist with `write_json(SUBSCRIPTIONS_FILE, _subscriptions)` under the
lock. Prune on `"prune"` result as described in 3.5.

### 3.9 Siri endpoints (exact behaviour)

**`GET /siri`** — read-only, plain text, `_guard()` already applied:

- `status = current_status()`. If `not is_online(status)` → `200 "PC is off."`
- Otherwise build a single-line sentence with only the fields that exist:
  `PC online on <hostname> at <ip>. CPU <cpu_percent> percent, RAM <ram.percent> percent. T3 Code is <open|closed>. Sleep mode is <on|off>.`
  - `t3_open` / `sleep_active` → `open/closed`, `on/off` (use `bool(value)`).
  - Missing `None` numeric fields: omit that clause entirely; if everything is missing, still
    answer `PC online on <hostname>.` (hostname from the status dict).
  - Collapse any whitespace/newlines in dynamic values so Siri reads one clean sentence
    (`re.sub(r"\s+", " ", value)`).
- `self._send(200, text, "text/plain; charset=utf-8")` — `_send()` already sets
  `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

**`POST /cmd?spoken=1`** (also triggered by `Accept: text/plain`):

- Modify `do_POST` to keep the query string:
  `path, _, query = self.path.partition("?")`, then
  `spoken = ("spoken=1" in query.split("&")) or "text/plain" in self.headers.get("Accept", "")`.
- Compute a fresh timestamp for **every** `/cmd` request whose `ts` is missing, `null`, a bool,
  or not a number (not just spoken ones — this fixes a latent dedupe collision for all
  clients and does not change the response contract):
  `ts = next_ts_ms()` (3.2) when the check fails; otherwise keep the caller's value verbatim.
- Keep the command validation (`cmd not in CMD_VALUES` → 400), `cancelar` → `clear_command()`,
  `wake` → `wake_pc_async()`, `queue_command(cmd, ts)` — **all unchanged**.
- When `spoken`, respond with `text/plain; charset=utf-8` using this exact map instead of the
  JSON dict:

  | Command | Spoken sentence |
  |---|---|
  | `wake` | `Wake packet sent.` |
  | `shutdown` | `Shutting down the PC.` |
  | `t3-open` | `Opening T3 Code.` |
  | `t3-close` | `Closing T3 Code.` |
  | `sleep-on` | `Sleep mode on.` |
  | `sleep-off` | `Sleep mode off.` |
  | `cancelar` | `Pending command canceled.` |

- Invalid-command errors in spoken mode also return plain text (`Invalid command.`) via a
  small `_fail(spoken, code, msg)` helper; guard failures (401/403) keep the existing JSON body
  (rare misconfiguration, not part of the Siri happy path).
- When not spoken: `self._send(200, {"ok": True, "comando": cmd})` — unchanged.

---

## 4. `panel_celular/index.html` — additive UI changes

1. In `<head>` add:
   `<link rel="manifest" href="/manifest.webmanifest">`
2. Add one small section near the existing buttons (outside `#content`, so it is available even
   when the PC is off), e.g. under the message area:

   ```html
   <div class="buttons" id="pushRow" style="margin-top:.75rem">
     <button id="pushBtn" class="hidden" onclick="togglePush()">enable alerts</button>
   </div>
   <input id="pushToken" class="hidden" type="password" placeholder="panel token"
          aria-label="Panel token" style="...">
   ```

3. Add JS (all new functions, no edits to `refresh()`, `cmd()`, polling or visibility logic):
   - `let pushConfig = null; let pushSub = null;`
   - On page load: `fetch('/push/config').then(r=>r.json()).then(c=>{ pushConfig=c; if (c.push_available) showPushButton(); })`
     — this prefetch is essential: the permission request must be the **first** thing in the
     click handler on iOS, with no `await` before it.
   - `urlBase64ToUint8Array(b64u)` helper (standard atob + pad + Uint8Array loop).
   - `togglePush()`:
     - If `pushSub` exists → `await pushSub.unsubscribe()`, `POST /push/unsubscribe` with
       `{endpoint: pushSub.endpoint}`, update button text.
     - Else: choose manager — `const pm = window.pushManager || (await navigator.serviceWorker.register('/sw.js')).pushManager;`
       then **immediately** (first statement, before any `await` other than the register when
       strictly necessary) call
       `pm.subscribe({userVisibleOnly:true, applicationServerKey: urlBase64ToUint8Array(pushConfig.public_key)})`.
       On iOS 18.4+ `window.pushManager` exists (declarative) and no service worker is needed;
       on older iOS the registered SW's `pushManager` is used. Apple requires this inside the
       user gesture — the button `onclick` is that gesture.
     - `POST /push/subscribe` with JSON body `{subscription: sub.toJSON()}` plus
       `Content-Type: application/json` and, if `pushConfig.token_required`,
       `X-Panel-Token: <value from #pushToken>` (store the value in `localStorage` after first
       successful use). The existing UI already assumes `PANEL_TOKEN` is unset; this field is
       only for users who enable it.
     - Set `#msg` to `✓ alerts enabled` / `✓ alerts disabled` / a short error.
   - Guard feature detection: only show the button when `pushConfig.push_available` and
     `window.isSecureContext` (over plain HTTP it stays hidden — no broken button).

No change to the 15 s timeout, the 1 s status poll, the 30 s battery poll, or any existing ID.

---

## 5. `docs/IOS-SIRI-AND-PUSH-SETUP.md` — guide outline (Agent 3 to write)

All instructions in English, with exact values and verification commands.

1. **What you get / what you don't** — short honest note: normal notification + badge and Siri
   voice control; no Dynamic Island without a native app (link the research doc).
2. **Prerequisites** — Tailscale installed and connected on the iPhone and the Android phone;
   Tailscale admin console → DNS → **MagicDNS + HTTPS certificates enabled**.
3. **Part A — Siri (works over the tailnet IP, no HTTPS needed):**
   - Verify in Safari: `http://100.113.245.28:8080/status` returns JSON.
   - Six shortcuts, each with: exact **name** (= spoken phrase), URL, Method, Headers, Request
     Body, and final action. Use `POST /cmd?spoken=1` so the **Get Contents of URL** output is a
     plain sentence; end with **Speak Text** using that output (no JSON parsing needed). No `ts`
     action is required any more (server auto-fills), but an explicit Random Number `ts` is
     harmless — mention it for users who already have shortcuts.
   - Recipes table:

     | Shortcut name / phrase | Request |
     |---|---|
     | `turn on my PC` | `POST .../cmd?spoken=1`, `{"comando":"wake"}` |
     | `shut down my PC` | `POST .../cmd?spoken=1`, `{"comando":"shutdown"}` — add **Choose from Menu** confirm first |
     | `open T3 Code` | `POST .../cmd?spoken=1`, `{"comando":"t3-open"}` |
     | `close T3 Code` | `POST .../cmd?spoken=1`, `{"comando":"t3-close"}` |
     | `sleep mode` | `POST .../cmd?spoken=1` with `sleep-on`/`sleep-off` chosen by an **If** on `GET /siri` containing `Sleep mode is off.` |
     | `PC status` | `GET /siri` → **Speak Text** of the output |

   - Headers for every POST: `Content-Type: application/json`; add `X-Panel-Token: <token>`
     only if `PANEL_TOKEN` is set on the phone. Leave `Host` alone. Do not use Funnel.
   - **Add to Siri** from the shortcut's details for a custom phrase; note built-in-name
     collisions and iPhone-scoped execution (HomePod/Watch need their own tailnet access).
   - Verify with `journalctl --user -u panel-agent.service -f` on the PC.
4. **Part B — HTTPS via Tailscale Serve:** in the phone's Termux session run
   `tailscale serve --bg 8080` (note the current CLI equivalent), confirm
   `https://<phone>.<tailnet>.ts.net/version` from Safari; **never** use `tailscale funnel`.
   Note the intermittent `*.ts.net` iOS TLS reports (research issue link) and the fallback of
   using Siri over the tailnet IP.
5. **Part C — PWA:** open the HTTPS URL in Safari → Share → **Add to Home Screen** → keep
   **Open as Web App** on → launch from the icon → tap **enable alerts** → Allow notifications.
   Optional: Settings → Notifications → Focus. Mention iOS 18.4+ declarative push and why the
   notification still renders if the service worker is evicted.
6. **Part D — phone-side one-time setup (manual, because `boot_panel` is not auto-deployed):**
   `pkg install python-cryptography` (or pip alternative), verify import, then add to
   `/data/data/com.termux/files/home/panel/boot_panel` before the launch line:

   ```bash
   export PANEL_PUBLIC_URL=https://<phone>.<tailnet>.ts.net
   export VAPID_SUBJECT=mailto:<your-address>    # or keep the default https: URL
   ```

   Kill the running `python3 server.py` so Termux:Boot / the `pgrep` guard restarts it with the
   new environment (or start it manually with the same env). Confirm
   `curl -s https://<phone>.<tailnet>.ts.net/push/config` shows `push_available: true`.
   Also commit the same commented exports into the repo's `boot_panel` for future reinstalls.
7. **Part E — verification:** `POST /push/test`, force the PC off (or stop the agent) and wait
   > 15 s → exactly one "PC went offline" notification; start the agent → exactly one "PC is
   back online" and badge cleared; confirm a 410 removes the subscription
   (`GET /push/subscriptions`). Troubleshooting list.
8. **Security note:** token lives in the Shortcuts database (iCloud, user's own devices), never
   in a spoken phrase; keep Host/Origin/token guards; no public exposure.

---

## 6. `tests/test_server.py` — automated suite

Use only the stdlib (`unittest`, `http.client` or `urllib.request`, `tempfile`, `threading`,
`unittest.mock`). No pytest dependency, so it runs on the PC and in CI with `python3 -m unittest
discover -s tests -v`.

**Fixture pattern:** in `setUpClass`, set `os.environ["PANEL_DATA_DIR"] = tempfile.mkdtemp()`
**before** importing `server` via `importlib.util.spec_from_file_location` (or set the module
attrs directly after a normal import), set `server.REPO = ""` (no deploy thread),
`server.PANEL_TOKEN = ""`, create a minimal temp `index.html` and assign
`server.ASSETS = server.Assets(tmp_index)`. Start the real
`server.Server(("127.0.0.1", 0), server.Handler)` in a daemon thread; store
`server.server_address[1]` for requests. Use HTTP/1.1 `Connection: close` or a fresh
`http.client.HTTPConnection` per test to avoid keep-alive cross-test contamination. `tearDown`
clears `_status_cache`, `_cmd_cache`, `_ack_cache`, `_estado_cache`, `_subscriptions`,
`_pc_online`, `_vapid`, resets `PANEL_TOKEN`, and shuts the server down.

**Contract tests (must all pass unchanged):**

1. `test_existing_get_endpoints` — `/`, `/version`, `/status`, `/cmd`, `/ack`, `/estado`,
   `/battery`, `/cmd?wait=1` all return 200 with the expected Content-Type; `/status` before any
   publish is `{"status": "no data"}`.
2. `test_existing_command_roundtrip` — `POST /cmd {"comando":"wake","ts":123}` →
   `{"ok":true,"comando":"wake"}`; `GET /cmd` shows the queued command; `POST /cmd/ack` clears
   it and `GET /ack` shows the result. Repeat for every one of the 7 `CMD_VALUES` (including
   `cancelar`, which must not queue).
3. `test_existing_status_publish` — `POST /status` with the full agent JSON keeps every key
   (`hostname, ip, ram, cpu_percent, cpu_temp, gpus, t3_open, sleep_active, timestamp`) and
   adds `recibido_ts` / `recibido_en_celular`; `GET /status` matches.
4. `test_status_disk_throttle_unchanged` — two publishes 1 s apart still write `status.json`
   at most once (assert mtime/content count), matching ADR-005.
5. `test_malformed_json` — `POST /cmd` with invalid JSON → 400 with `error`.
6. `test_version_check_additive` — `POST /version?check=1` → `{"ok":true,"check":"queued"}`
   with `REPO=""`; `GET /version` unchanged.

**Siri tests:**

7. `test_siri_offline` — no status → `200 text/plain`, body `PC is off.`
8. `test_siri_online` — publish fresh status with all fields → sentence contains hostname, IP,
   CPU/RAM percentages, `T3 Code is open`, `Sleep mode is off`; Content-Type is `text/plain`.
9. `test_siri_partial_status` — missing `ram`/`gpus` → no exception, still a clean sentence.
10. `test_spoken_cmd_plain_text_and_ts` — `POST /cmd?spoken=1 {"comando":"t3-open"}` →
    `200 text/plain` body `Opening T3 Code.`; read the queued command from `GET /cmd` and assert
    `ts` is an `int` within 5 s of `int(time.time()*1000)`.
11. `test_spoken_cmd_accept_header` — same via `Accept: text/plain` (no query).
12. `test_spoken_ts_unique` — two spoken commands back-to-back with identical command → two
    different `ts` values (dedupe safety).
13. `test_spoken_invalid_command` — `spoken=1` + bad command → `400 text/plain` `Invalid command.`
14. `test_guard_still_active` — with `server.PANEL_TOKEN = "secret"`: POST without token → 401;
    with `X-Panel-Token: secret` → 200; with `?token=secret` → 200; restore in `finally`.

**PWA asset / config tests:**

15. `test_manifest_route` — `GET /manifest.webmanifest` → 200, parseable JSON with
    `id == "/"`, `start_url == "/"`, `display == "standalone"`.
16. `test_sw_route` — `GET /sw.js` → 200 JS containing `addEventListener('push'` and
    `showNotification`, plus `Service-Worker-Allowed: /` header.

**Push config / subscription tests:**

17. `test_push_config_shape` — keys `push_available`, `public_key`, `token_required`,
    `public_url`, `error` exist and types are correct; with `PANEL_PUBLIC_URL=""`,
    `push_available` is false.
18. `test_push_config_no_crypto` — with `patch.object(server, "_CRYPTO_OK", False)` →
    `push_available: false` and a non-null `error`; **no 5xx**.
19. `@unittest.skipUnless(server._CRYPTO_OK, "cryptography not installed")`
    `test_vapid_public_key` — 87-char base64url string, decodes to 65 bytes starting `0x04`.
20. `test_subscribe_validation_and_storage` — valid subscription is stored (file written);
    duplicate endpoint replaces; invalid endpoint/keys → 400; subscribe while push unavailable
    → 503.
21. `test_unsubscribe` — removes only the matching endpoint.
22. `test_push_test_enqueues` — with patched `enqueue_push`, `POST /push/test` returns
    `{"ok":true,"queued":N}` and calls it once per subscription.
23. `test_send_push_prunes_on_410` — patch `urllib.request.urlopen` to raise
    `HTTPError(..., 410, ...)`, assert `"prune"` and that the worker-side removal helper drops
    the subscription. (Patch crypto helpers so this test runs even without `cryptography`.)
24. `@skipUnless(_CRYPTO_OK)` `test_encrypt_payload_structure` — generate a client P-256 key in
    the test, call `encrypt_payload`, assert body length > 16+4+1+65+len(payload)+16, first 16
    bytes are the salt, bytes 16:20 == `4096`, byte 20 == 65, bytes 21:86 == server pub.

**Transition tests (pure function, no threads / no sleeps):**

25. `test_transition_baseline_no_event` — first observation (online or offline) →
    `(None, state)`.
26. `test_transition_offline_once` — online → stale status (`recibido_ts = now-16`) →
    `("offline", False)`; repeating the stale status → `(None, False)` (exactly one event).
27. `test_transition_online_again` — after offline, fresh status → `("online", True)`;
    repeating → no event.
28. `test_transition_ignores_old_persisted_status` — status published in a previous run with a
    stale `recibido_ts` → baseline offline, no spurious push on startup.
29. `test_is_online_boundary` — 14.9 s fresh → online; 15.1 s → offline; missing
    `recibido_en_celular` → offline. Matches `index.html`'s 15 s rule exactly.

---

## 7. Edge cases to handle explicitly

- **Restart while PC is off:** `_pc_online = None` baseline means no "went offline" notification
  at server startup. The persisted `status.json` is read into memory but never pushed on load.
- **Agent silence vs. PC off:** the design cannot distinguish them (same 15 s rule as the UI).
  The notification says "PC went offline" — acceptable and consistent with the panel.
- **Flapping at the 15 s boundary:** two-consecutive-poll debounce (3.5) prevents duplicates.
- **Push unavailable (no HTTPS / no `cryptography`):** panel and Siri still fully work; the
  button is hidden and `/push/config` explains why. Never raise from a handler.
- **`PANEL_PUBLIC_URL` unset:** `web_push` payloads need `navigate`; disable push with a clear
  config error rather than sending a payload Apple may reject.
- **Subscription endpoint expires (410/404):** pruned from memory and disk on the next push.
- **Apple errors 429/5xx or timeouts:** logged, subscription kept, queue never blocked.
- **Two spoken commands in the same millisecond:** `next_ts_ms()` monotonic guard (3.2) keeps
  the agent's dedupe key unique.
- **`ts` supplied as `null`:** auto-filled for all `/cmd` calls, not only spoken ones.
- **`Host` with Tailscale HTTPS name:** `_host_ok()` already resolves bare hostnames to
  `100.64.0.0/10`; no change needed. Shortcuts send no `Origin`, so `_origin_ok()` accepts.
- **`Content-Length` / keep-alive:** new handlers use `_send()` only; do not touch `_body()` or
  `protocol_version`. `do_HEAD = do_GET` automatically covers the new GET routes.
- **`do_GET` query parsing:** `/cmd` must keep its existing `wait=` parsing verbatim; the
  `spoken` flag is parsed only in `do_POST`.

---

## 8. Implementation order for Agent 3

1. Add env vars, imports with graceful fallback, `DATA_DIR`, new state/locks, `load_state()`
   extension, `.gitignore` entries. Compile-check.
2. Implement VAPID + RFC 8291 helpers and `send_web_push()`; verify `make_vapid_auth()` DER→
   raw signature conversion with a local unit check (cryptography is present in this workspace:
   50.0.2).
3. Implement `is_online()`, `transition_decision()`, `_notification_payload()`,
   `enqueue_push()`, `_transition_loop()`, `_push_worker()`, `start_background_workers()` and
   the `main()` wiring. No handler changes yet.
4. Add the 7 new routes and the `spoken`/`ts` logic in `do_POST`; keep existing branches
   byte-identical otherwise.
5. Add `MANIFEST_JS` / `SW_JS` constants and the manifest/SW routes. Compile-check.
6. Add the `index.html` button + JS (additive only).
7. Write `tests/test_server.py`; run it; fix regressions.
8. Write `docs/IOS-SIRI-AND-PUSH-SETUP.md`; update `docs/DECISIONS.md` (ADR-013),
   `docs/AGENT_CONTEXT.md`, optional `README.md` section, and the commented exports in
   `boot_panel`.
9. Run the full verification checklist (§9), commit on the feature branch and open a PR.

---

## 9. Verification checklist

```bash
# 1. Syntax / whitespace (mandatory minimum)
python3 -m py_compile panel_celular/server.py panel_celular/wol.py panel_pc/agent.py panel_pc/wol-listener.py
bash -n panel_celular/boot_panel panel_pc/*.sh
git diff --check

# 2. Automated suite
python3 -m unittest discover -s tests -v

# 3. Local server smoke test (no deploy, no Termux, no HTTPS)
cd panel_celular && PORT=8080 PANEL_DATA_DIR=/tmp/opencode/panel-test python3 server.py
curl -s localhost:8080/version
curl -s localhost:8080/status
curl -s localhost:8080/siri
curl -s localhost:8080/push/config
curl -s localhost:8080/manifest.webmanifest | head -c 200
curl -s localhost:8080/sw.js | head -c 200
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"comando":"wake","ts":123}' localhost:8080/cmd
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"comando":"wake"}' 'localhost:8080/cmd?spoken=1'
curl -s 'localhost:8080/cmd?wait=1'   # must still long-poll correctly

# 4. Contract regression: existing fields/keys/commands all present in test output
# 5. On-device (only after PR merge/deploy): Safari over Tailscale Serve /siri + /push/config,
#    each Siri phrase, one notification per forced transition, 410 pruning.
```

Agent 3 must not consider the work done until items 1–3 pass and the PR is open. The on-device
items (5) are a handoff to the user: they require the real iPhone and Tailscale admin console.

---

## 10. Decisions to record in `docs/DECISIONS.md` (ADR-013)

- **Context:** iOS cannot render Live Activities from a web app; HTTPS + service workers are
  required for push; the phone only auto-deploys three files.
- **Decision:** ship Siri text endpoints and Web Push as *additive* routes in `server.py`; embed
  the manifest and service worker as string constants; require `cryptography` only for push with
  a graceful runtime fallback; keep the transition watchdog in a daemon thread with a pure,
  unit-testable decision function.
- **Consequences:** no HTTP contract change, no deploy-logic change, no hot-path work; push is
  best-effort and can be disabled without affecting the panel; a true Dynamic Island still needs
  a native app (tracked separately, not in this PR).

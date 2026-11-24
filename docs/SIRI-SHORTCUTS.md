# iPhone: Siri voice control

Six Siri Shortcuts speak the PC status and send every command by voice. They use
the existing HTTP contract, so no HTTPS, proxy or extra service is needed.

## Requirements

- Tailscale connected on the iPhone.
- The panel reachable at `http://100.113.245.28:8080/status` from Safari.
- `PANEL_TOKEN` is not set on the phone, so no `X-Panel-Token` header is needed.
  Leave the `Host` header alone.

## Shortcuts

For each row, create a shortcut in the **Shortcuts** app:

1. **Get Contents of URL** with the URL and method below. For POSTs add header
   `Content-Type: application/json` and a **JSON** body with the exact key/value.
2. Final action **Speak Text**, using the output of *Get Contents of URL*. The
   reply is already a plain sentence, so no JSON parsing action is needed.
3. Open the shortcut details → **Add to Siri** and record the Spanish phrase.

| Shortcut / phrase | Request |
|---|---|
| `Enciende mi PC` | `POST http://100.113.245.28:8080/cmd?spoken=1`, body `{"comando":"wake"}` |
| `Apaga mi PC` | `POST .../cmd?spoken=1`, body `{"comando":"shutdown"}`; add a **Choose from Menu** action first (`cancelar` / `confirmar`) and continue only on `confirmar` |
| `Abre T3 Code` | `POST .../cmd?spoken=1`, body `{"comando":"t3-open"}` |
| `Cierra T3 Code` | `POST .../cmd?spoken=1`, body `{"comando":"t3-close"}` |
| `Modo dormir` | `GET .../siri`; with an **If** action check whether the reply contains `Sleep mode is off.` — if yes POST `{"comando":"sleep-on"}`, otherwise `{"comando":"sleep-off"}` |
| `Estado del PC` | `GET http://100.113.245.28:8080/siri` → **Speak Text** of the output |

The server fills a fresh millisecond timestamp when the client omits `ts`, so no
Random Number action is needed. Older shortcuts with an explicit `ts` keep working.

## Notes

- Shortcuts run on the device that invokes them. A HomePod or Apple Watch needs its
  own Tailscale connection and its own shortcuts.
- If Siri picks the wrong action, record a more distinctive phrase via **Add to
  Siri** (the shortcut name can collide with system phrases).
- Never use Tailscale Funnel: it would expose the panel publicly and defeat the
  `Host` / `Origin` / token guards.

## Verify

On the PC, watch the agent log while speaking each phrase:

```bash
journalctl --user -u panel-agent.service -f
```

Each spoken command appears as a normal queued command.

Tests: `python3 -m unittest discover -s tests -v`.

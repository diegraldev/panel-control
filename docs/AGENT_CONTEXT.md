# Contexto detallado para agentes — remotecontrol

Documento de referencia. Para la memoria corta (reglas y checks) ver `../AGENTS.md`.
Para el *por qué* de las decisiones ver `DECISIONS.md`.

## 1. Mapa del sistema

```
   [ Celular Android / Termux ]                 [ PC Fedora / usuario diegral ]
   ┌──────────────────────────┐                 ┌───────────────────────────────┐
   │ panel_celular/server.py  │  Tailscale      │ panel_pc/agent.py (--user)    │
   │  HTTP :8080              │<─── POST /status│  publica cada 1 s             │
   │  estado en memoria+JSON  │     GET /cmd    │  long-poll /cmd?wait=10       │
   │  auto-deploy (Atom feed) │─── comandos────>│  ejecuta: shutdown/wake/t3/.. │
   │  /battery (cache 30 s)   │                 │  gdbus -> logind -> poweroff  │
   └────────────┬─────────────┘                 └────────────┬──────────────────┘
                │ POST /wake (fire&forget)                   │ escribe flag
                │ (X-Wake-Token opcional)                    │ /var/lib/wol-autologin/pending
                v                                            v
   ┌──────────────────────────┐                 ┌───────────────────────────────┐
   │ magic packet UDP :9      │                 │ wol-autologin.sh (antes SDDM) │
   │ (wake_pc_async, hilo)    │                 │ habilita autologin si hay flag│
   └──────────────────────────┘                 └───────────────────────────────┘
```

Arranque del celular: `Termux:Boot` ejecuta `panel_celular/boot_panel`
(`termux-wake-lock` + `nohup python3 server.py` con `REPO_URL`).

## 2. Superficie HTTP (contrato; no cambiar nombres)

| Método | Ruta | Quién la usa | Efecto |
|---|---|---|---|
| GET | `/` , `/index.html` | navegador | HTML con gzip + ETag (304 si no cambió) |
| GET | `/status` | navegador | último estado cacheado (`recibido_ts`, `recibido_en_celular`) |
| POST | `/status` | agente PC | guarda estado (memoria; disco ≤1 vez/5 s) |
| GET | `/cmd?wait=N` | agente PC | long-poll hasta que llega un comando (N≤30) |
| POST | `/cmd` | navegador | encola `{"comando": ..., "ts": ...}`; `wake` dispara packet async |
| POST | `/cmd/ack` | agente PC | guarda resultado y limpia la cola |
| GET | `/ack` | debug | último resultado |
| GET | `/estado` | debug | blob libre |
| POST | `/estado_cmd` | debug | guarda blob libre |
| GET | `/battery` | navegador | `{"level": %, "charging": bool}` desde caché 30 s |
| GET | `/version` | debug / deploy | `{"sha": ..., "repo": ...}` |

Comandos válidos (`CMD_VALUES`): `shutdown`, `wake`, `cancelar`, `t3-open`,
`t3-close`, `sleep-on`, `sleep-off`.

Campos del JSON de estado publicado por el agente (todos conservar):
`hostname`, `ip`, `ram` (total_mb/used_mb/free_mb/percent), `cpu_percent`,
`cpu_temp` (max/source), `gpus` (name/usage/temp/type), `t3_open`, `sleep_active`,
`timestamp`. El servidor añade `recibido_en_celular` y `recibido_ts`.

## 3. Variables de entorno

Servidor (`panel_celular/server.py`):

| Var | Default | Uso |
|---|---|---|
| `PORT` | `8080` | puerto de escucha |
| `PC_MAC` | `d8:bb:c1:22:1a:d1` | MAC para WoL |
| `REPO_URL` | `""` (deploy desactivado) | `owner/repo` para auto-deploy |
| `REPO_TOKEN` | `""` | Bearer para descargar el tarball (repo privado) |
| `DEPLOY_POLL` | `10` | segundos entre chequeos del feed Atom |
| `PANEL_TOKEN` | `""` | si se define, exige `X-Panel-Token` o `?token=` en POST |
| `WAKE_TOKEN` | `""` | se envía como `X-Wake-Token` al listener |
| `WAKE_URL` | `http://100.69.149.63:8091/wake` | listener root en el PC |
| `ALLOWED_HOSTS` | `""` | hosts extra permitidos (comma-separated) |

Agente (`panel_pc/agent.py`):

| Var | Default | Uso |
|---|---|---|
| `PANEL_URL` | `http://100.113.245.28:8080` | URL del panel |
| `PANEL_INTERVAL` | `1` | s entre publicaciones de estado |
| `PC_MAC` | `d8:bb:c1:22:1a:d1` | usada por `wake` |
| `T3_APPIMAGE` | ruta absoluta en `~/Downloads` | AppImage a lanzar |
| `GPU_SAMPLE_PERIOD` | `2` | ventana de muestreo GPU |
| `PANEL_TOKEN` | `""` | token para los POST al panel |
| `PANEL_DEBUG` | `""` | `1` activa logs de comandos |

`panel-agent.service` fija `PANEL_URL` y `PC_MAC` en el Environment del unit.

## 4. Servicios systemd en el PC

| Unidad | Tipo / contexto | Función |
|---|---|---|
| `panel-agent.service` | **--user**, `WantedBy=default.target` | agente; ver regla SELinux en AGENTS.md |
| `wol-listener.service` | sistema (root), puerto 8091 | recibe la señal de wake del celular |
| `wol-autologin.service` | sistema, `Before=sddm.service` | configura autologin condicional |
| `panel-rgb-perms.service` | sistema, oneshot | `chmod 666` de sysfs LEDs RGB |
| `panel-sleep.service` | plantilla oneshot (con `%i` = on/off) | brillo/volumen/RGB; la usa `sleep-mode.sh` vía `systemd-run --user --scope` |

Reglas auxiliares: `99-panel-rgb.rules` (udev, permisos LED),
`polkit-poweroff.rules` (permite poweroff/reboot al usuario `diegral`).

## 5. Auto-deploy, paso a paso

1. `deploy_loop()` llama `check_and_deploy()` al arrancar y cada `DEPLOY_POLL`.
2. `remote_sha()` pide `commits/main.atom` con `If-None-Match: <etag guardado>`.
   - Si hay cambios: parsea `<id>tag:github.com,2008:Grit::Commit/<sha></id>`.
   - Si `304`: devuelve el SHA local y conserva el ETag ⇒ cero bytes, cero API.
   - Si falla el feed: fallback a REST `/repos/.../commits?per_page=1&sha=main`
     (consume rate limit; solo es contingencia).
3. Si el SHA cambió: descarga `archive/refs/heads/main.tar.gz`, extrae en
   `.deploy/` con `--strip-components=1`.
4. Compara byte a byte `index.html`, `server.py`, `wol.py` contra los locales.
   - `index.html`/`wol.py`: `os.replace` directo (se sirven/recargan sin reinicio).
   - `server.py`: se guarda como `.new`, se vuelca el estado (`_flush_state()`) y
     `os.execv` reinicia el proceso.
5. `version.json` guarda `{sha, ts, etag}` (gitignored).

**Implicación para agentes:** un push a `main` despliega en ~10 s en el celular.
Por eso cualquier cambio en `server.py` debe ser retrocompatible: el reinicio es
automático y no hay rollback manual a menos que se revierta el commit.

## 6. Optimizaciones ya conseguidas (no regressione)

Del commit `e5606b7` y posteriores (reporte original `REPORT.html`, eliminado después):

- Recolección de estado: **1750 ms → 3 ms**.
- Latencia de comando: **6.4 s → 0.002 s** (long-poll + TCP_NODELAY).
- CPU del agente: **13.9% → 1.2%**.
- 0% de pérdida de lecturas (escrituras atómicas tmp+rename).
- HTTP/1.1 keep-alive; `TCP_NODELAY` evita el retardo de ACK de Nagle (~40 ms).
- Batería del celular: `termux-battery-status` en hilo aparte cada 30 s, no en el
  handler HTTP; `termux-wake-lock` mantiene el proceso vivo con la pantalla apagada.
- Sin forks en el hot path: CPU desde `/proc/stat`, temps desde sysfs, IP por ioctl,
  T3 Code por scan de `/proc/*/comm`, GPUs con procesos largos.

## 7. Gotchas operativos (errores ya encontrados en git history)

- **Batería en el handler**: leer `termux-battery-status` dentro del request crashing
  la conexión ⇒ cache en background (commits `833757e`→`2dc3cd5`).
- **Flag de autologin**: el listener NO debe escribirlo; la señal llega cuando la PC
  ya arrancó y el flag ya se consumió ⇒ reescribirlo ensucia el próximo arranque
  manual (`45503da`).
- **Flag sin sudo**: el agente no puede `sudo mkdir /var/lib/...` (contexto de
  servicio) ⇒ escribe directo; el directorio ajustado ya existe (`b59313b`).
- **Sleep toggle**: no cambiar `aria-pressed` localmente; sincronizar con el brillo
  real, y nunca guardar 0 como valor de restauración (`594634c`).
- **RGB sin sudo**: regla udev + chmod, no `sudo` desde el agente (`e24ac32`).
- **Scope transitorio**: `systemd-run --user --scope`, no un service oneshot —
  systemd mata el cgroup cuando `gtk-launch` termina (`fe119fb`).
- **Entorno de sesión**: clonar `DISPLAY/WAYLAND_DISPLAY/DBUS_SESSION_BUS_ADDRESS/...`
  de un proceso gráfico (`0a148f4`); si no, falla el plugin Qt.
- **Dos `wol.py`**: el deploy solo actualiza `panel_celular/wol.py`; la copia de la
  raíz quedó para uso standalone. Sincronizar si se cambia el protocolo.
- **`REPORT.html`**: referenciado en README pero borrado en `7903025`. No recrear.

## 8. Checklist antes de dar por terminado un cambio

1. `python3 -m py_compile` en todos los `.py` tocados; `bash -n` en los `.sh`.
2. `git diff --check` limpio.
3. Contrato HTTP intacto (tabla §2) y JSON de estado intacto (§2).
4. Si tocaste archivos del celular: servir en local (`PORT=8080 python3 server.py`) y
   probar `/version`, `/status`, `/battery`, `/cmd?wait=1` + `POST /cmd`/`ack`.
5. Si tocaste el agente: verificar que no se añadió ningún `subprocess` síncrono
   en `status()` ni en `publish_loop()`.
6. Si la change afecta al deploy, recordar que `index.html`/`wol.py` se actualizan
   sin reinicio pero `server.py` reinicia el proceso y vuelca el estado.
7. Documentar en `DECISIONS.md` cualquier decision arquitectónica nueva.

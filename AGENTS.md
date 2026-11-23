# AGENTS.md — memoria del repo remotecontrol

Lee esto primero. Es la memoria corta para agentes: hechos que NO se deducen
fácilmente del código, reglas y el check para verificar cambios. La guía detallada
está en `docs/AGENT_CONTEXT.md` y las decisiones en `docs/DECISIONS.md`.

## Propósito

Panel web minimalista servido desde un **celular Android con Termux sobre Tailscale**
para vigilar y controlar un PC: estado (CPU/RAM/GPU/temps), Wake-on-LAN, apagado,
abrir/cerrar T3 Code y modo sleep. No es un producto genérico: es un único dispositivo
real (IPs y MAC fijas, rutas absolutas en `/home/diegral`).

## Arquitectura (flujo)

- `panel_celular/server.py` — servidor HTTP en el celular (puerto 8080). Guarda estado
  en memoria + archivos JSON atómicos. Expone `/status`, `/cmd` (long-poll `?wait=N`),
  `/cmd/ack`, `/estado`, `/battery`, `/version`. Auto-deploy: consulta el Atom feed
  `https://github.com/diegraldev/remotecontrol/commits/main.atom` cada `DEPLOY_POLL`
  (10 s) con `If-None-Match` (304 = 0 bytes, sin consumir rate limit de la API).
  Descarga el tarball de `main`, copia `index.html`, `server.py`, `wol.py`; solo
  reinicia (`os.execv`) si cambió `server.py`.
- `panel_celular/index.html` — UI mono (negro/blanco + cian). Llama a `/status` y
  `/battery` cada 1 s. `TIMEOUT=15000` ms sin estado ⇒ muestra "pc is off".
- `panel_celular/boot_panel` — script de Termux:Boot: `termux-wake-lock`, arranca
  `python3 server.py` con `REPO_URL=diegraldev/remotecontrol` si no está corriendo.
- `panel_pc/agent.py` — agente en el PC (usuario `diegral`). Publica estado cada
  `PANEL_INTERVAL` (1 s) y hace long-poll de comandos. Dos conexiones keep-alive
  separadas (publicar vs. comandos) para que un `wait=10` no bloquee el publish.
  Muestreo en background: CPU por delta de `/proc/stat`, temps desde sysfs,
  GPUs con `nvidia-smi -lms` / `intel_gpu_top` persistentes, IP por ioctl caché.
- `panel_pc/wol-listener.py` — servicio **root** en el PC, puerto 8091, `POST /wake`.
  NO escribe el flag de autologin (ese flag lo escribe el agente **al apagar**;
  escribirlo aquí contaminaría el siguiente arranque manual — ver DECISIONS).
- `panel_pc/wol-autologin.sh` — antes de SDDM: si existe `/var/lib/wol-autologin/pending`,
  habilita autologin a niri y borra el flag; si no, deshabilita autologin.
- `panel_pc/sleep-mode.sh` — brillo/volumen/RGB a 0 (`on`) y restauración (`off`),
  invocado por `systemd-run --user --scope`.

## Reglas para agentes (no negociables)

1. **No romper el contrato HTTP**: mismos endpoints, mismos campos JSON, mismos
   comandos (`shutdown`, `wake`, `cancelar`, `t3-open`, `t3-close`, `sleep-on`,
   `sleep-off`). `index.html` y el agente son consumidores; cambiar un nombre rompe el panel.
2. **El agente PC debe ser servicio de USUARIO** (`systemctl --user`), nunca de sistema.
   Como sistema obtiene contexto SELinux `unconfined_service_t` y T3 Code no puede
   usar `sudo`/`pkexec`. Con --user hereda `unconfined_u` de la sesión.
3. **No usar `sudo` desde el agente**: corre con contexto de servicio. Escribir el flag
   de WoL directamente (el directorio es del usuario) y apagar vía gdbus/logind + polkit.
4. **T3 Code se lanza con `APPIMAGE_EXTRACT_AND_RUN=1`** (sin FUSE: faltan capabilities)
   y en `systemd-run --user --scope` transitorio (un servicio oneshot --user mata el
   cgroup al terminar `gtk-launch`). Clonar el entorno completo de la sesión gráfica.
5. **No reintroducir forks en el hot path** (`sensors`, `pgrep`, `hostname -I`,
   `nvidia-smi` por muestra). El rendimiento actual es 3 ms/recolección, 1.2% CPU.
6. **Batería del celular**: `termux-battery-status` se llama en un hilo background
   cada 30 s con caché; NUNCA dentro del handler HTTP (bloqueaba/crashaba la conexión).
   El wake PC va en hilo daemon: la respuesta HTTP no debe esperar a la PC apagada.
7. **Seguridad**: mantener `_guard` (Host + Origin + `X-Panel-Token` opcional en
   POST). Respetar `ALLOWED_HOSTS` y el rango tailnet `100.64.0.0/10`.
8. **Idempotencia de comandos**: el agente guarda `(comando, ts)` en `last_key` y
   omite duplicados si se perdió el ack. No eliminar ese mecanismo.
9. No hacer commits/pushes salvo petición explícita; dejar cambios en el working tree.
10. Escribir respuestas y comentarios nuevos en español; el código existente mezcla
    inglés/español, mantén consistencia con el archivo que toques.

## Comandos útiles

```bash
# Verificar sintaxis (no hay test suite; esto es el check mínimo obligatorio)
python3 -m py_compile panel_celular/server.py panel_celular/wol.py panel_pc/agent.py panel_pc/wol-listener.py
bash -n panel_celular/boot_panel panel_pc/*.sh

# Probar el servidor localmente (sin deploy, sin Termux)
cd panel_celular && PORT=8080 python3 server.py
curl -s localhost:8080/version && curl -s localhost:8080/status

# Ver el commit desplegado en el celular
curl -s http://100.113.245.28:8080/version

# PC: estado de los servicios
systemctl --user status panel-agent.service
systemctl status wol-listener.service wol-autologin.service panel-rgb-perms.service
journalctl --user -u panel-agent.service -f
PANEL_DEBUG=1 python3 panel_pc/agent.py   # logs de comandos
```

## Cómo verificar cambios

1. `python3 -m py_compile` en todos los `.py` tocados + `bash -n` en los `.sh`.
2. Si tocaste `server.py`/`index.html`: levantar el server en local y comprobar que
   `/status`, `/version`, `/battery` y `/cmd?wait=1` responden y que un `POST /cmd`
   válido llega por long-poll seguido de `POST /cmd/ack`.
3. Si tocaste el agente: comprobar que el JSON publicado conserva todas las claves
   (`hostname, ip, ram, cpu_percent, cpu_temp, gpus, t3_open, sleep_active, timestamp`).
4. Confirmar que `git diff --check` no reporta whitespace roto.

## Gotchas

- La UI considera la PC "offline" si `recibido_ts` tiene >15 s; el publish debe seguir
  fluyendo aunque no haya comandos.
- `store_status()` escribe a disco **como máximo cada 5 s**; si haces debug de
  `status.json` puede estar desactualizado respecto a la memoria.
- `wol.py` existe dos veces: `./wol.py` (raíz) y `panel_celular/wol.py`; el deploy
  actualiza solo la copia de `panel_celular/`. Mantener ambas sincronizadas.
- Las IPs `100.113.245.28` (celular) y `100.69.149.63:8091` (listener) y la MAC
  `d8:bb:c1:22:1a:d1` son datos reales del entorno; no son ejemplos.
- `REPORT.html` se eliminó en el último commit; el README aún lo menciona: no lo
  recrees salvo que se pida, pero tampoco dejes referencias rotas nuevas.
- `DEPLOY_POLL` solo tiene efecto si `REPO_URL` está definido (lo pone `boot_panel`).

## Índice de documentación

- `README.md` — resumen de usuario y puesta en marcha.
- `docs/AGENT_CONTEXT.md` — contexto detallado: flowchart, env vars, superficie HTTP,
  gotchas operativos, checklist de verificación.
- `docs/DECISIONS.md` — decisiones de arquitectura ya tomadas (ADR simple).
- `.github/ISSUE_TEMPLATE/` y `.github/PULL_REQUEST_TEMPLATE.md` — plantillas.

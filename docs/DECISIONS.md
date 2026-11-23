# Decisiones de arquitectura (ADR simple)

Formato corto: **Contexto → Decisión → Consecuencias**. Cuando tomes una decisión
nueva, añade una entrada. Esto evita que un agente futuro "arregle" algo que ya se
probó que no funciona. Los números de commit son del historial real de `main`.

---

## ADR-001 — Agente del PC como servicio de **usuario**, no de sistema
(`b8d99e8`, confirmado en `panel-agent.service`)

- **Contexto:** T3 Code se lanza desde el agente. Como servicio de sistema el
  contexto SELinux es `unconfined_service_t`, que hereda la app ⇒ `sudo`/`pkexec`
  fallan dentro de T3 Code aunque la app abre.
- **Decisión:** instalar `panel-agent.service` en `~/.config/systemd/user/` y
  habilitarlo con `systemctl --user`. `WantedBy=default.target`.
- **Consecuencias:** el agente hereda `unconfined_u` de la sesión; requiere que
  exista una sesión gráfica del usuario. No hace falta `CAP_PERFMON`:
  `perf_event_paranoid=2` permite `intel_gpu_top` sin privilegios.

## ADR-002 — T3 Code vía AppImage extract-and-run + scope transitorio
(`ce9b663`, `e6f1c24`, `128a556`, `fe119fb`)

- **Contexto:** el servicio restringe capabilities (sin `CAP_SYS_ADMIN`) ⇒ el
  montaje FUSE del AppImage falla con "Permission denied". Además un servicio
  oneshot `--user` muere en cuanto `gtk-launch` termina (systemd limpia el cgroup).
- **Decisión:** `APPIMAGE_EXTRACT_AND_RUN=1` y lanzar con
  `systemd-run --user --scope --collect --unit=t3code-panel /usr/bin/gtk-launch com.t3tools.T3Code`,
  pasando el entorno completo de la sesión (`--setenv=...`).
- **Consecuencias:** la app sobrevive a reinicios del agente y mantiene el contexto
  de sesión; el cname del proceso es `t3code` (detección y cierre por `/proc/*/comm`).

## ADR-003 — Auto-deploy por Atom feed con ETag, no por API REST
(`e5606b7`)

- **Contexto:** el celular consulta GitHub cada 10 s; la API REST tiene rate limit
  (60 req/h sin token) y cada respuesta consume cuota.
- **Decisión:** leer `commits/main.atom` con `If-None-Match`. Un 304 cuesta 0 bytes
  y 0 unidades de cuota. REST queda solo como fallback si falla el feed.
- **Consecuencias:** despliegue en ~10 s sin token; requiere que `REPO_URL` esté
  definido (lo pone `boot_panel`). El ETag se guarda en `version.json`.

## ADR-004 — Reiniciar solo si cambia `server.py`
(`panel_celular/server.py::check_and_deploy`)

- **Contexto:** reiniciar el servidor corta los long-poll del agente y puede
  perder estado en memoria si no se vuelca.
- **Decisión:** comparar bytes de los tres archivos desplegables; `index.html` y
  `wol.py` se reemplazan en caliente; `server.py` se vuelca (`_flush_state`) y se
  reinicia con `os.execv`.
- **Consecuencias:** los cambios de UI llegan sin interrupción; los cambios de
  servidor son atómicos pero reinician el proceso.

## ADR-005 — Estado en memoria + escritura diferida a 5 s
(`store_status`)

- **Contexto:** el agente publica cada 1 s; escribir `status.json` en cada POST
  desgasta la flash del celular y abre ventana de lecturas parciales.
- **Decisión:** caché en memoria con `threading.Lock`, `write_json()` atómico
  (tmp + `os.replace`) y compulsory disk write como máximo cada 5 s. Al reiniciar
  se vuelca el estado (`_flush_state`).
- **Consecuencias:** `status.json` puede ir hasta 5 s por detrás de la memoria;
  el navegador siempre ve el dato fresco vía `/status`.

## ADR-006 — Long-poll + dos conexiones keep-alive en el agente
(`e5606b7`)

- **Contexto:** con polling simple, un comando tardaba hasta un intervalo en
  llegar (6.4 s medidos) y un `GET /cmd?wait=10` bloqueaba la publicación de estado.
- **Decisión:** el panel soporta `GET /cmd?wait=N` con `threading.Condition`; el
  agente usa un `Client` para publicar y otro para comandos, ambos HTTP/1.1
  keep-alive, con `TCP_NODELAY` en el servidor y timeout de socket para no
  fugas de hilos.
- **Consecuencias:** comandos en ~2 ms; el publish nunca se detiene; si el
  long-poll falla, el agente cae a polling corto y reintenta.

## ADR-007 — Batería y wake fuera del handler HTTP
(`833757e`, `fca7d4c`, `441aa05`, `2dc3cd5`)

- **Contexto:** llamar a `termux-battery-status` (o `dumpsys`) dentro del handler
  bloqueaba/crashaba la conexión; conectar al listener con la PC apagada colgaba
  la respuesta hasta el socket timeout.
- **Decisión:** hilo daemon que actualiza `_battery_cache` cada 30 s y `/battery`
  solo lee la caché; `wake_pc_async()` envía el magic packet y avisa al listener
  en un hilo daemon. `termux-wake-lock` evita que Android suspenda el server.
- **Consecuencias:** el endpoint de batería no cuesta nada por request; la UI
  puede consultarlo cada 1 s sin impacto; la respuesta de `wake` es inmediata.
  **Cualquier optimización de batería futura debe preservar este patrón.**

## ADR-008 — Apagado sin sudo: gdbus/logind + regla polkit
(`898fdbb`, `b59313b`)

- **Contexto:** el agente corre con contexto de servicio y su `sudo` no funciona.
- **Decisión:** apagar con `gdbus call --system ... Manager.PowerOff false` y
  permitir la acción para `diegral` mediante `polkit-poweroff.rules`. El flag de
  autologin se escribe directamente en `/var/lib/wol-autologin/pending` (sin sudo).
- **Consecuencias:** apagado inmediato sin cuenta atrás; requiere la regla polkit
  instalada en `/etc/polkit-1/rules.d/`.

## ADR-009 — Flag de WoL lo escribe el apagado, no el listener
(`45503da`)

- **Contexto:** el celular avisa al listener justo antes del magic packet, pero
  para entonces la PC **ya arrancó** y `wol-autologin.sh` ya consumió/borró el flag.
  Escribirlo en el listener lo dejaría para el arranque manual siguiente.
- **Decisión:** `run_command("shutdown")` escribe el flag antes de apagar;
  `wol-listener.py::do_POST` solo responde `ok` y no toca disco.
- **Consecuencias:** autologin solo tras un apagado iniciado desde el panel;
  arranques manuales siguen pidiendo contraseña.

## ADR-010 — Permisos RGB por udev, no por sudo
(`e24ac32`, `fe73ae6`, `panel_pc/99-panel-rgb.rules`, `panel-rgb-perms.service`)

- **Contexto:** `sleep-mode.sh` necesita escribir en sysfs de LEDs; `sudo` no está
  disponible desde el agente.
- **Decisión:** regla udev `MODE=0666, TAG+=uaccess` + servicio oneshot que
  hace `chmod 666` en arranque por si udev no aplicó.
- **Consecuencias:** el modo sleep funciona sin credenciales elevadas.

## ADR-011 — Contrato estable, estilo cambiante
(`16195f8`, `README.md`)

- **Contexto:** se hicieron mejoras de rendimiento y un rediseño visual (mono,
  negro/blanco con acento cian).
- **Decisión:** conservar todos los endpoints, campos JSON y comandos; cambiar
  solo estilos y organización interna.
- **Consecuencias:** el panel y el agente pueden evolucionar de forma independiente
  mientras el contrato se respete. Los tests/verificación se centran en ese contrato.

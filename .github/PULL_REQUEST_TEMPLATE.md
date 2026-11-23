## Resumen

<!-- 1–3 frases: qué cambia y para qué. -->

## Motivación / contexto

<!-- Issue relacionado, problema observado o decisión que se aplica. -->
Closes #

## Cambio

<!-- Qué archivos y por qué. Un bullet por archivo relevante. -->

- `panel_celular/...`:
- `panel_pc/...`:
- Documentación (`AGENTS.md` / `docs/`):

## Compatibilidad

- [ ] Contrato HTTP intacto (endpoints, campos JSON y comandos; ver `docs/AGENT_CONTEXT.md` §2).
- [ ] Se respetan las reglas de `AGENTS.md` (servicio --user, sin sudo desde el agente,
      sin forks en el hot path, caché de batería en background, seguridad Host/Origin/token).
- [ ] Si aplica, `docs/DECISIONS.md` tiene una entrada nueva.

## Pruebas

<!-- Evidencia real: comandos ejecutados y su salida. No basta con "funciona". -->

```bash
python3 -m py_compile panel_celular/server.py panel_celular/wol.py panel_pc/agent.py panel_pc/wol-listener.py
bash -n panel_celular/boot_panel panel_pc/*.sh
git diff --check
```

Verificación manual (curl / journalctl / screenshot / antes-después):

```text

```

## Impacto

- Rendimiento / latencia:
- Consumo de batería del celular:
- Seguridad (tokens, Host/Origin, superficie Tailscale):
- Deploy: ¿reinicia `server.py`? ¿Afecta a los long-poll del agente?

## Riesgo / rollback

<!-- Qué puede salir mal tras el auto-deploy de 10 s y cómo revertir rápido. -->

## Checklist

- [ ] `py_compile` y `bash -n` pasan.
- [ ] `git diff --check` limpio.
- [ ] Sin credenciales, IPs de prueba ni rutas personales nuevas sin justificación.
- [ ] Documentación actualizada si el comportamiento operativo cambió.
- [ ] No se hicieron commits/pushes salvo petición explícita del usuario.

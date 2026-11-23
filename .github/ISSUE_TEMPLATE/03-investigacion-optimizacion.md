---
name: Investigación / optimización
about: Analizar consumo, latencia o rendimiento y proponer mejoras con evidencia
title: "[Investigación]: "
labels: research, optimization, triage
---

## Contexto

<!-- Qué se quiere investigar. Ej.: consumo excesivo de batería del celular Android. -->

- Componente / capa bajo estudio:
- Síntoma medible (ej.: "batería baja 15%/h con la pantalla apagada"):
- Commit / versión desde la que se observa:

## Pregunta de investigación

<!-- Una frase concreta que la investigación debe responder con datos. -->

## Evidencia base (antes)

<!-- Mediciones reales del estado actual: comandos, contadores, logs, perf. -->

| Métrica | Valor actual | Cómo se midió |
|---|---|---|
| | | |

```text
<!-- journalctl, top, dumpsys battery, /proc, curl -w time_total ... -->

```

## Hipótesis

<!-- Causas candidatas, ordenadas por probabilidad. Señala archivo/función. -->

1.
2.
3.

## Restricciones (no romper)

- El contrato HTTP y el JSON de estado (`docs/AGENT_CONTEXT.md` §2) son sagrados.
- `AGENTS.md` reglas 1–10: servicio --user, sin sudo, sin forks en el hot path,
  caché de batería en background, idempotencia de comandos.
- Batería: patrón ADR-007 — `termux-battery-status` en hilo background cada 30 s,
  NUNCA en el handler HTTP; `termux-wake-lock` activo.
- Sin registrar cambios que aumenten wakeups del celular sin medirlos antes/después.

## Propuesta de optimización

<!-- Cambios concretos, por archivo. Prioriza los de mayor impacto y menor riesgo. -->

1.
2.
3.

## Impacto esperado y medición "después"

| Métrica | Antes | Después | Δ | Cómo se verificó |
|---|---|---|---|---|

## Criterios de aceptación

- [ ] Hipótesis confirmada o descartada con datos (no con suposiciones).
- [ ] Tabla antes/después rellena; el cambio no degrada rendimiento ni funciones.
- [ ] `python3 -m py_compile` / `bash -n` pasan y el panel sigue respondiendo.
- [ ] Decisiones o hallazgos nuevos documentados en `docs/DECISIONS.md`.

## Riesgo / rollback

<!-- Especialmente importante: el auto-deploy aplica en 10 s. Cómo revertir rápido. -->

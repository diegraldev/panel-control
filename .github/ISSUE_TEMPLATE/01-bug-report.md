---
name: Bug / fallo observado
about: Reportar un comportamiento incorrecto del panel, el agente o el deploy
title: "[Bug]: "
labels: bug, triage
---

## Contexto

<!-- ¿Dónde ocurre? Celular (Termux), PC (agente), listener WoL, deploy, UI... -->

- Componente afectado:
- Commit desplegado (`curl -s http://100.113.245.28:8080/version`):
- ¿Empezó tras un cambio/deploy? ¿Cuándo?

## Pasos para reproducir

1.
2.
3.

## Comportamiento esperado

## Comportamiento observado (evidencia)

<!-- Pegar logs, salida de journalctl, capturas o respuestas curl. -->

```text

```

## Entorno

- Lado: [ ] celular Termux  [ ] PC  [ ] ambos
- Versión de Python / Fedora / Android:
- Tailscale activo: [ ] sí [ ] no
- Estado de servicios (si aplica):
  - `systemctl --user status panel-agent.service`
  - `systemctl status wol-listener.service`

## Impacto

- ¿Afecta al estado, a los comandos, al apagado/WoL o solo a la UI?
- ¿Con qué frecuencia?
- ¿Hay riesgo de perder el control remoto del PC?

## Criterios de aceptación

- [ ] El fallo se reproduce de forma determinista (o se documenta su intermitencia).
- [ ] La causa raíz está identificada en un archivo/línea concretos.
- [ ] Existe una verificación que pasa con el fix y falla sin él.
- [ ] El contrato HTTP (`docs/AGENT_CONTEXT.md` §2) sigue intacto.

## Propuesta (opcional)

<!-- Si ya tienes una idea del fix, descríbela y señala el riesgo/rollback. -->

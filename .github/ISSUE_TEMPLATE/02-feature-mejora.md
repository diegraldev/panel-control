---
name: Feature / mejora
about: Proponer una nueva capacidad o mejorar una existente sin romper el contrato
title: "[Feature]: "
labels: enhancement, triage
---

## Contexto

<!-- ¿Qué problema resuelve? ¿Qué limitaciones tiene hoy el panel? -->

- Componente(s) implicado(s): [ ] celular  [ ] PC  [ ] listener  [ ] UI
- ¿Ya existe algo parecido en el repo?

## Motivación

<!-- Caso de uso concreto. Ej.: "quiero ver X sin abrir la PC". -->

## Propuesta

<!-- Diseño a alto nivel: nuevos endpoints/campos/comandos, cambios de UI, etc. -->

## Compatibilidad con el contrato

- [ ] No cambia nombres de endpoints, campos JSON ni comandos existentes.
      (Si los cambia, explicar el plan de migración en "Impacto".)
- [ ] Sigue las reglas de `../AGENTS.md` (servicio --user, sin forks en hot path,
      caché de batería en background, seguridad Host/Origin/token).
- [ ] ¿Requiere una decisión nueva en `docs/DECISIONS.md`? Justificar.

## Alternativas consideradas

<!-- Qué se descartó y por qué (evita que un agente futuro lo reintente). -->

## Impacto

- Rendimiento: ¿añade forks, polling o tráfico? ¿Cuánto?
- Batería del celular: ¿aumenta wakeups o llamadas a Termux:API?
- Seguridad: ¿nuevo endpoint, token o superficie expuesta en Tailscale?

## Criterios de aceptación

- [ ] Implementación en el working tree (sin commit salvo petición).
- [ ] `python3 -m py_compile` / `bash -n` pasan.
- [ ] Verificación manual documentada (curl / journalctl / screenshot).
- [ ] `docs/DECISIONS.md` actualizado si aplica.

## Riesgo / rollback

<!-- Cómo revertir si el auto-deploy (10 s) despliega un cambio problemático. -->

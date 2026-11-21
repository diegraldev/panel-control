# Panel de Control (PC <-> Celular via Tailscale)

Panel web minimalista servido en un celular Android (Termux) que muestra el estado
de una PC y permite prenderla (Wake-on-LAN), apagarla y abrir/cerrar T3 Code.

## Estructura
- `panel_celular/` — servidor Python + HTML + wol.py + auto-deploy desde GitHub
- `panel_pc/` — agente Python (envia RAM/CPU/temp/GPU/T3 Code y ejecuta comandos)
- `wol.py` — magic packet UDP

## Auto-deploy
El celular consulta GitHub cada 30s (`DEPLOY_POLL`) y, si hay commit nuevo en `main`,
descarga, copia los archivos y se reinicia. Configurar variables de entorno en Termux:
- `REPO_URL` = `usuario/repo`
- `REPO_TOKEN` = token de GitHub (solo si el repo es privado)

## Uso
Abrir `http://100.113.245.28:8080` con Tailscale activo.

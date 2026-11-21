# Remote Control

Minimal black-and-white web panel served from an Android phone (Termux) over Tailscale.
It shows the PC status and can wake it (Wake-on-LAN), shut it down, and open/close T3 Code.

## Structure
- `panel_celular/` — Python server + HTML + wol.py + auto-deploy + boot script
- `panel_pc/` — PC agent (RAM, CPU, GPU usage/temps, T3 Code, commands)

## Auto-deploy
The phone polls GitHub every 30s (`DEPLOY_POLL`). On a new commit on `main`, it
downloads, copies the files and restarts automatically.

## Usage
Open `http://100.113.245.28:8080` with Tailscale enabled.

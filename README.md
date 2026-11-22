# Remote Control

Minimal black-and-white web panel served from an Android phone (Termux) over Tailscale.
It shows the PC status and can wake it (Wake-on-LAN), shut it down, and open/close T3 Code.

## Structure
- `panel_celular/` — Python server + HTML + wol.py + auto-deploy + boot script
- `panel_pc/` — PC agent (RAM, CPU, GPU usage/temps, T3 Code, commands)

## Auto-deploy
The phone polls GitHub every 30s (`DEPLOY_POLL`). On a new commit on `main`, it
downloads, copies the files and restarts automatically.

## PC setup (important)

The agent must run as a **user** systemd service, not a system service:

```bash
mkdir -p ~/.config/systemd/user
cp panel_pc/panel-agent.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now panel-agent.service
```

Reason: T3 Code is launched from the agent. If the agent runs as a system service it
has the `unconfined_service_t` SELinux context, and T3 Code inherits it — the app opens
but `sudo`/`pkexec` do not work inside it. As a user service everything inherits
`unconfined_u` (the desktop session context), so admin access works normally.

`t3-open` also sets `APPIMAGE_EXTRACT_AND_RUN=1` (the AppImage is extracted instead of
FUSE-mounted, so it works even with a restricted capability set) and launches the app
in a transient **`systemd-run --user --scope`**, so it survives agent restarts and keeps
the session context.

## Usage
Open `http://100.113.245.28:8080` with Tailscale enabled.

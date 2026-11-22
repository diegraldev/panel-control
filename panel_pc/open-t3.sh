#!/usr/bin/env bash
# Ejecutado por systemd --user: hereda el entorno de la sesion graphics
# (DISPLAY, WAYLAND_DISPLAY, XDG_DATA_DIRS, DBUS, etc.).
exec /usr/bin/gtk-launch com.t3tools.T3Code

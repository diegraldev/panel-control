#!/usr/bin/env bash
# Ejecutado por systemd --user (contexto unconfined_u), no por el agente root.
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-1
export XDG_RUNTIME_DIR=/run/user/1000
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=niri
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
exec gtk-launch com.t3tools.T3Code

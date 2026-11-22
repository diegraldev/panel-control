#!/usr/bin/env bash
# Modo sleep: baja brillo, volumen y RGB a 0 (o los restaura).
# Se ejecuta por systemd --user (contexto de sesion).
# Uso: sleep-mode.sh on | off
set -u
ACTION="$1"

case "$ACTION" in
  on)
    # Guardar estado actual para poder restaurar
    mkdir -p /tmp/panel-sleep
    brightnessctl -m -q > /tmp/panel-sleep/brightness 2>/dev/null || echo "" > /tmp/panel-sleep/brightness
    wpctl get-volume @DEFAULT_AUDIO_SINK@ > /tmp/panel-sleep/volume 2>/dev/null || echo "" > /tmp/panel-sleep/volume
    # Guardar perfil RGB actual
    /home/diegral/.local/share/rgb-backlight/rgb-profile.sh save all >/dev/null 2>&1 || true

    # Bajar a 0
    brightnessctl set 0 >/dev/null 2>&1 || true
    wpctl set-volume @DEFAULT_AUDIO_SINK@ 0 >/dev/null 2>&1 || true
    # RGB off (sysfs, requiere root; lo hace el helper sudo)
    /usr/bin/sudo -n /usr/local/lib/panel-pc/rgb-off.sh >/dev/null 2>&1 || true
    ;;
  off)
    # Restaurar brillo
    if [ -s /tmp/panel-sleep/brightness ]; then
      B=$(grep -oE '[0-9]+%' /tmp/panel-sleep/brightness | head -1 | tr -d '%')
      [ -n "$B" ] && brightnessctl set "${B}%" >/dev/null 2>&1 || true
    fi
    # Restaurar volumen
    if [ -s /tmp/panel-sleep/volume ]; then
      V=$(grep -oE 'Volume: [0-9.]+' /tmp/panel-sleep/volume | awk '{print $2}')
      [ -n "$V" ] && wpctl set-volume @DEFAULT_AUDIO_SINK@ "$V" >/dev/null 2>&1 || true
    fi
    # Restaurar RGB
    /home/diegral/.local/share/rgb-backlight/rgb-profile.sh restore all >/dev/null 2>&1 || true
    ;;
esac

#!/usr/bin/env bash
# Modo sleep: baja brillo, volumen y RGB a 0 (o los restaura).
# Se ejecuta por systemd --user (contexto de sesion).
# Uso: sleep-mode.sh on | off
set -u
ACTION="$1"

case "$ACTION" in
  on)
    # Guardar estado actual para poder restaurar (nunca guardar 0)
    mkdir -p /tmp/panel-sleep
    BR=$(brightnessctl -m -q 2>/dev/null | grep -oE '[0-9]+%' | head -1 | tr -d '%')
    if [ -z "$BR" ] || [ "$BR" -eq 0 ] 2>/dev/null; then BR=60; fi
    echo "intel_backlight,backlight,${BR}%" > /tmp/panel-sleep/brightness
    VOL=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null | awk '{print $2}')
    if [ -z "$VOL" ] || [ "$(echo "$VOL <= 0" | awk '{print ($1<=0)}')" = "1" ]; then VOL=0.50; fi
    echo "Volume: $VOL" > /tmp/panel-sleep/volume
    # Guardar perfil RGB con brillo > 0
    /home/diegral/.local/share/rgb-backlight/rgb-profile.sh save all >/dev/null 2>&1 || true
    sed -i 's/brightness=0/brightness=8/' /home/diegral/.config/rgb-backlight/*.state 2>/dev/null || true
    # Guardar perfil RGB actual
    /home/diegral/.local/share/rgb-backlight/rgb-profile.sh save all >/dev/null 2>&1 || true

    # Bajar a 0
    brightnessctl set 0 >/dev/null 2>&1 || true
    wpctl set-volume @DEFAULT_AUDIO_SINK@ 0 >/dev/null 2>&1 || true
    # RGB off: escritura directa al sysfs (permiso via regla udev).
    for led in /sys/class/leds/msi::kbd_backlight/brightness /sys/class/leds/razer::logo_backlight/brightness; do
      [ -w "$led" ] && echo 0 > "$led" 2>/dev/null || true
    done
    ;;
  off)
    # Restaurar brillo (si el guardado era 0, usar 60%)
    B=60
    if [ -s /tmp/panel-sleep/brightness ]; then
      SAVED=$(grep -oE '[0-9]+%' /tmp/panel-sleep/brightness | head -1 | tr -d '%')
      if [ -n "$SAVED" ] && [ "$SAVED" -gt 0 ] 2>/dev/null; then B="$SAVED"; fi
    fi
    brightnessctl set "${B}%" >/dev/null 2>&1 || true

    # Restaurar volumen (si el guardado era 0, usar 0.5)
    V=0.50
    if [ -s /tmp/panel-sleep/volume ]; then
      SAVED=$(grep -oE 'Volume: [0-9.]+' /tmp/panel-sleep/volume | awk '{print $2}')
      if [ -n "$SAVED" ]; then
        # comparar como entero para evitar decimales en [ ]
        if [ "$(echo "$SAVED > 0" | awk '{print ($1>0)}')" = "1" ]; then V="$SAVED"; fi
      fi
    fi
    wpctl set-volume @DEFAULT_AUDIO_SINK@ "$V" >/dev/null 2>&1 || true

    # Restaurar RGB (asegurar brillo mayor que 0)
    RGB=$(grep -oE 'brightness=[0-9]+' /home/diegral/.config/rgb-backlight/keyboard.state 2>/dev/null | cut -d= -f2)
    if [ -z "$RGB" ] || [ "$RGB" -eq 0 ] 2>/dev/null; then
      sed -i 's/brightness=0/brightness=8/' /home/diegral/.config/rgb-backlight/keyboard.state 2>/dev/null || true
    fi
    /home/diegral/.local/share/rgb-backlight/rgb-profile.sh restore all >/dev/null 2>&1 || true
    ;;
esac

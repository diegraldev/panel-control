#!/usr/bin/env bash
# Ejecutado como root via sudo: apaga los LEDs RGB del teclado y mouse.
for led in /sys/class/leds/msi::kbd_backlight/brightness /sys/class/leds/razer::logo_backlight/brightness; do
  [ -w "$led" ] && echo 0 > "$led" 2>/dev/null || true
done

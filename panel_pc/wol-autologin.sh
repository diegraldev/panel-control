#!/usr/bin/env bash
# Se ejecuta al ARRANCAR, antes de SDDM.
# Si existe el flag, habilita autologin; si no, lo deshabilita.
FLAG=/var/lib/wol-autologin/pending
CONF=/etc/sddm.conf.d/99-wol-autologin.conf

if [ -f "$FLAG" ]; then
  cat > "$CONF" <<'EOF'
[Autologin]
User=diegral
Session=niri.desktop
Relogin=false
EOF
  rm -f "$FLAG"
  echo "WoL detectado: autologin habilitado para diegral"
else
  rm -f "$CONF"
  echo "Arranque manual: autologin deshabilitado (se pedira contraseña)"
fi

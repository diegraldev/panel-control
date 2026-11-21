#!/usr/bin/env bash
# Se ejecuta ANTES de apagar: marca que el proximo arranque sera remoto (WoL).
mkdir -p /var/lib/wol-autologin
echo "pending" > /var/lib/wol-autologin/pending

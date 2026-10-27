#!/bin/bash
# =====================================================================
#  Rulat de amper-update (root) după despachetarea unei versiuni noi, înainte de repornire.
#  Aici intră pașii de sistem pe care îi cere o versiune: fișiere systemd noi sau schimbate, pachete etc.
#  Trebuie să fie idempotent și rapid; codul nou e deja în /opt/amper (legătura a fost schimbată).
# =====================================================================
set -uo pipefail
NEW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGED=0
for unit in amper-update.service amper-wifi.service amper-password-reset.service amper-grow-root.service amper-backup.service amper-backup.timer; do
  if [ -f "$NEW/deploy/$unit" ] && ! cmp -s "$NEW/deploy/$unit" "/etc/systemd/system/$unit"; then
    cp "$NEW/deploy/$unit" /etc/systemd/system/ && CHANGED=1
  fi
done
[ "$CHANGED" -eq 1 ] && systemctl daemon-reload
# unități noi, care trebuie și activate (o versiune veche nu le avea)
for unit in amper-password-reset.service; do
  [ -f "/etc/systemd/system/$unit" ] && ! systemctl is-enabled -q "$unit" 2>/dev/null && systemctl enable "$unit" >/dev/null 2>&1
done
exit 0

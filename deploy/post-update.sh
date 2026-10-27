#!/bin/bash
# =====================================================================
#  Rulat de amper-update (root) după despachetarea unei versiuni noi, înainte de repornire.
#  Aici intră pașii de sistem pe care îi cere o versiune: fișiere systemd noi sau schimbate, pachete etc.
#  Trebuie să fie idempotent și rapid; codul nou e deja în /opt/amper (legătura a fost schimbată).
# =====================================================================
set -uo pipefail
NEW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGED=0
for unit in amper-update.service amper-wifi.service amper-grow-root.service amper-backup.service amper-backup.timer; do
  if [ -f "$NEW/deploy/$unit" ] && ! cmp -s "$NEW/deploy/$unit" "/etc/systemd/system/$unit"; then
    cp "$NEW/deploy/$unit" /etc/systemd/system/ && CHANGED=1
  fi
done
[ "$CHANGED" -eq 1 ] && systemctl daemon-reload
exit 0

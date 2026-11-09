#!/bin/bash
# =====================================================================
#  Jurnalul sistemului păstrat pe card (journald Storage=persistent), fără repornirea Pi-ului.
#  Rulat de install.sh (și, prin el, de tools/build-image.sh cu --no-restart) și de post-update.sh.
#  Idempotent: repornește systemd-journald doar dacă s-a schimbat ceva.
# =====================================================================
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST=/etc/systemd/journald.conf.d/50-amper.conf
CHANGED=0
mkdir -p /etc/systemd/journald.conf.d
if ! cmp -s "$SRC/journald-amper.conf" "$DST"; then
  cp "$SRC/journald-amper.conf" "$DST" && CHANGED=1
fi
[ -d /var/log/journal ] || { mkdir -p /var/log/journal && CHANGED=1; }
mkdir -p /var/log/amper                       # jurnalele Amper pe fișier (copia de siguranță, actualizările)
[ "${1:-}" = "--no-restart" ] && exit 0       # imaginea (chroot): se aplică la prima pornire
if [ "$CHANGED" -eq 1 ]; then
  systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
  systemctl restart systemd-journald 2>/dev/null || true
  journalctl --flush 2>/dev/null || true      # mută pe card ce era până acum în RAM
fi
exit 0

#!/bin/bash
# =====================================================================
#  Kiosk: aplicația Amper pe tot ecranul monitorului HDMI legat la Pi
#  (cage + Chromium, serviciu systemd pe tty1, pornește singur la boot)
#
#  Rulați de pe Pi, după install.sh:
#     sudo bash ~/amper/deploy/kiosk.sh              # instalare / actualizare
#     sudo bash ~/amper/deploy/kiosk.sh --remove     # înapoi la consola text pe tty1
#
#  Altă adresă:  sudo KIOSK_URL=http://localhost:8080 bash ~/amper/deploy/kiosk.sh
# =====================================================================
set -euo pipefail

URL="${KIOSK_URL:-http://localhost:8080}"
KUSER=kiosk
UNIT=/etc/systemd/system/amper-kiosk.service
LAUNCHER=/usr/local/bin/amper-kiosk-browser

[ "$(id -u)" -eq 0 ] || { echo "Rulați cu sudo:  sudo bash $0"; exit 1; }
say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

if [ "${1:-}" = "--remove" ]; then
  say "Opresc kiosk-ul și revin la consola text pe tty1"
  systemctl disable --now amper-kiosk 2>/dev/null || true
  rm -f "$UNIT" "$LAUNCHER"
  systemctl daemon-reload
  systemctl enable getty@tty1 >/dev/null 2>&1
  systemctl set-default multi-user.target
  systemctl start getty@tty1
  echo "Gata. Utilizatorul '$KUSER' și pachetele cage/chromium au rămas instalate."
  exit 0
fi
[ -z "${1:-}" ] || { echo "Opțiune necunoscută: $1"; exit 1; }

say "Instalez cage (compozitor Wayland pentru o singură aplicație) și Chromium"
apt-get update -qq
apt-get install -y -qq cage chromium >/dev/null

say "Creez utilizatorul '$KUSER' (rulează doar browserul)"
id "$KUSER" >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin "$KUSER"
usermod -aG video,render,input "$KUSER"

# Așteaptă serverul Amper (max. 90 s), apoi pornește Chromium pe tot ecranul.
cat > "$LAUNCHER" <<EOF
#!/bin/sh
URL="$URL"
i=0
until python3 -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "\$URL" 2>/dev/null; do
  i=\$((i+1)); [ \$i -ge 45 ] && break; sleep 2
done
exec chromium --kiosk --ozone-platform=wayland --noerrdialogs --disable-infobars \\
  --no-first-run --disable-session-crashed-bubble --disable-features=Translate \\
  --password-store=basic --check-for-update-interval=31536000 "\$URL"
EOF
chmod +x "$LAUNCHER"

say "Instalez serviciul amper-kiosk pe tty1"
cat > "$UNIT" <<EOF
[Unit]
Description=Amper kiosk (cage + Chromium pe HDMI)
After=systemd-user-sessions.service plymouth-quit-wait.service getty@tty1.service amper.service
Wants=amper.service
Conflicts=getty@tty1.service
StartLimitIntervalSec=0

[Service]
# fără monitor legat nu pornește; reîncearcă la 30 s, deci pornește singur când e legat monitorul
ExecStartPre=/bin/sh -c 'grep -qsx connected /sys/class/drm/card*-HDMI-A-*/status'
User=$KUSER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
ExecStart=/usr/bin/cage -d -s -- $LAUNCHER
Restart=always
RestartSec=30

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl disable getty@tty1 >/dev/null 2>&1 || true
systemctl enable amper-kiosk >/dev/null 2>&1
systemctl set-default graphical.target
systemctl restart amper-kiosk

say "Gata!"
systemctl --no-pager --lines=0 status amper-kiosk | head -3 || true
cat <<EOF

  Monitorul HDMI afișează $URL (pornește singur la fiecare alimentare).
  Jurnal:        journalctl -u amper-kiosk -f
  Repornire:     sudo systemctl restart amper-kiosk
  Dezinstalare:  sudo bash $0 --remove

EOF
exit 0

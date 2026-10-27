#!/bin/bash
# =====================================================================
#  Instalare amper pe Raspberry Pi OS (Lite, 64-bit)
#
#  Rulați de pe Pi:
#     sudo bash /boot/firmware/amper/deploy/install.sh               # instalare normală
#     sudo bash /boot/firmware/amper/deploy/install.sh --tailscale   # + acces de oriunde
#     sudo bash /boot/firmware/amper/deploy/install.sh --demo        # date simulate (fără hardware)
#     sudo bash ~/amper/deploy/install.sh --restore amper-date-AAAA-LL-ZZ.tar.gz
#                       # pe un card nou: pune la loc config, istoric, Daly, Tailscale din copia din Drive
#
#  Se poate rula de mai multe ori (actualizare): păstrează config-ul și baza de date.
# =====================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/amper
ETC=/etc/amper
DATA=/var/lib/amper
TAILSCALE=0
DEMO=0
RESTORE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale) TAILSCALE=1 ;;
    --demo) DEMO=1 ;;
    --restore) RESTORE="$(readlink -f "${2:?lipsește fișierul copiei}")"; shift ;;
    *) echo "Opțiune necunoscută: $1"; exit 1 ;;
  esac
  shift
done
[ -z "$RESTORE" ] || [ -f "$RESTORE" ] || { echo "Nu găsesc copia: $RESTORE"; exit 1; }

[ "$(id -u)" -eq 0 ] || { echo "Rulați cu sudo:  sudo bash $0"; exit 1; }
say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

say "Instalez pachetele necesare (Python, aiohttp, pyserial, yaml, Bluetooth)"
apt-get update -qq
apt-get install -y -qq python3 python3-aiohttp python3-serial python3-yaml python3-cryptography sqlite3 >/dev/null
# Bluetooth pentru modulele Daly (opțional: dacă nu se poate instala, merge doar pe cablu)
apt-get install -y -qq bluez python3-bleak >/dev/null || echo "  (Bluetooth indisponibil: modulele Daly merg doar pe cablu)"
# copia de siguranță (Google Drive) și extinderea cardului după restaurarea unei clone
apt-get install -y -qq rclone pigz rsync cloud-guest-utils >/dev/null || echo "  (lipsesc unelte pentru copia de siguranță)"

say "Creez utilizatorul de sistem 'amper' (cu acces la porturile seriale)"
id amper >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin amper
usermod -aG dialout amper

# Programul stă în /opt/amper-<versiune>, iar /opt/amper e o legătură către versiunea folosită
# (așa o schimbă și actualizările din aplicație, cu revenire la cea anterioară).
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/amper/__init__.py")"
[ -n "$VER" ] || { echo "Nu găsesc versiunea în $SRC/amper/__init__.py"; exit 1; }
say "Copiez programul (versiunea $VER) în $APP-$VER"
mkdir -p "$ETC" "$DATA"
if [ "$(readlink -f "$SRC")" != "$(readlink -f "$APP-$VER")" ]; then
  rm -rf "$APP-$VER.new"
  mkdir -p "$APP-$VER.new"
  cp -r "$SRC/amper" "$SRC/web" "$SRC/deploy" "$APP-$VER.new/"
  for f in config.example.yaml CHANGELOG.md LICENSE README.md; do [ -f "$SRC/$f" ] && cp "$SRC/$f" "$APP-$VER.new/"; done
  find "$APP-$VER.new" -name "__pycache__" -type d -prune -exec rm -rf {} +
  rm -rf "$APP-$VER" && mv "$APP-$VER.new" "$APP-$VER"
fi
[ -d "$APP" ] && [ ! -L "$APP" ] && rm -rf "$APP"          # instalare veche: folder în loc de legătură
ln -sfn "$APP-$VER" "$APP.new" && mv -T "$APP.new" "$APP"

# ---------------- restaurare dintr-o copie (opțional) ----------------
if [ -n "$RESTORE" ]; then
  say "Pun la loc datele din copia $(basename "$RESTORE")"
  systemctl stop amper 2>/dev/null || true
  T="$(mktemp -d)"
  tar -xzf "$RESTORE" -C "$T"
  cat "$T/data-copiei.txt" 2>/dev/null || true
  cp -a "$T/etc/." "$ETC/"
  rm -f "$DATA"/amper.db-wal "$DATA"/amper.db-shm
  cp -a "$T/var-lib-amper/." "$DATA/"
  if [ -n "$(ls -A "$T/var-lib-tailscale" 2>/dev/null)" ]; then
    command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
    systemctl stop tailscaled 2>/dev/null || true
    mkdir -p /var/lib/tailscale && cp -a "$T/var-lib-tailscale/." /var/lib/tailscale/
    systemctl enable --now tailscaled >/dev/null 2>&1 || true
    echo "  Tailscale: aceeași identitate ca înainte (nu porniți și vechiul card în același timp)."
  fi
  rm -rf "$T"
fi
chown -R amper:amper "$DATA"

cat > /usr/local/bin/amper <<'EOF'
#!/bin/sh
PYTHONPATH=/opt/amper exec python3 -m amper "$@"
EOF
chmod +x /usr/local/bin/amper

# ---------------- configurare ----------------
NEW_CONFIG=0
if [ ! -f "$ETC/config.yaml" ]; then
  NEW_CONFIG=1
  if [ -f "$SRC/config.yaml" ]; then
    cp "$SRC/config.yaml" "$ETC/config.yaml"          # config pregătit de dvs. pe card
  else
    cp "$SRC/config.example.yaml" "$ETC/config.yaml"
  fi
  TOPIC="baterii-$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  sed -i "s/REPLACE-TOPIC/$TOPIC/" "$ETC/config.yaml"
fi

if [ "$DEMO" -eq 0 ] && grep -q "by-id/REPLACE-" "$ETC/config.yaml"; then
  say "Caut dispozitivele pe porturile USB (poate dura ~1 minut)"
  systemctl stop amper 2>/dev/null || true
  for svc in solar-assistant solarassistant; do systemctl stop "$svc" 2>/dev/null || true; done
  amper autoconfig -c "$ETC/config.yaml" || true
fi
# parola comună a hotspotului (scrisă în instrucțiuni); instalările vechi își păstrează parola lor
WIFI_PW="amper-setup"
if ! grep -q "^wifi_setup:" "$ETC/config.yaml"; then
  printf '\n# Configurarea WiFi de pe telefon (hotspot pornit doar când nu e internet), vezi README\nwifi_setup:\n  enabled: true\n  ssid: Amper-Setup\n  password: %s\n  offline_minutes: 2\n' "$WIFI_PW" >> "$ETC/config.yaml"
fi
sed -i "s/REPLACE-WIFI-SETUP/$WIFI_PW/" "$ETC/config.yaml"
chown root:amper "$ETC/config.yaml"
chmod 640 "$ETC/config.yaml"
[ -f "$ETC/rclone.conf" ] && chown root:root "$ETC/rclone.conf" && chmod 600 "$ETC/rclone.conf"

# ---------------- serviciu ----------------
say "Instalez serviciul (pornește singur la fiecare alimentare)"
EXEC="/usr/bin/python3 -m amper run -c $ETC/config.yaml"
[ "$DEMO" -eq 1 ] && EXEC="/usr/bin/python3 -m amper simulate --hours 48 --db $DATA/demo.db --keep"
sed "s#@EXEC@#$EXEC#" "$SRC/deploy/amper.service" > /etc/systemd/system/amper.service
systemctl daemon-reload
systemctl enable amper >/dev/null 2>&1
systemctl restart amper

cp "$SRC/deploy/amper-backup.service" "$SRC/deploy/amper-backup.timer" "$SRC/deploy/amper-grow-root.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable amper-grow-root.service >/dev/null 2>&1
# copia în Google Drive e opțională: pornește doar dacă există /etc/amper/rclone.conf
if [ -f "$ETC/rclone.conf" ]; then
  say "Copia de siguranță în fiecare noapte la 00:00 (Google Drive)"
  systemctl enable --now amper-backup.timer >/dev/null 2>&1
else
  systemctl disable --now amper-backup.timer >/dev/null 2>&1 || true
fi

say "Actualizările din aplicație (Setări → Actualizări; caută singur o dată pe săptămână, nu instalează singur)"
cp "$SRC/deploy/amper-update.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable amper-update >/dev/null 2>&1
systemctl restart amper-update

say "Hotspotul de configurare WiFi (pornește doar când Pi-ul nu are internet)"
rfkill unblock wifi 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
cp "$SRC/deploy/amper-wifi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable amper-wifi >/dev/null 2>&1
systemctl restart amper-wifi

# ---------------- Tailscale ----------------
# Pachetul se instalează mereu (fără logare): accesul de oriunde se pornește din aplicație (Setări → Acces de
# oriunde). Cu --tailscale, logarea se face și de aici, din linia de comandă.
if ! command -v tailscale >/dev/null; then
  say "Instalez Tailscale (pentru accesul de oriunde; se pornește din aplicație)"
  curl -fsSL https://tailscale.com/install.sh | sh || echo "  (Tailscale nu s-a putut instala acum; accesul de oriunde nu va fi disponibil)"
fi
if [ "$TAILSCALE" -eq 1 ] && command -v tailscale >/dev/null; then
  echo "Deschideți linkul afișat mai jos (de pe telefon sau PC) și autentificați-vă:"
  tailscale up --hostname "$(hostname)" || true
fi

sleep 3
HOST="$(hostname)"
IP="$(hostname -I | awk '{print $1}')"
TOPIC_NOW="$(grep -E '^\s*topic:' "$ETC/config.yaml" | head -1 | awk '{print $2}')"
say "Gata!"
systemctl --no-pager --lines=0 status amper | head -3 || true
cat <<EOF

  Aplicația:      http://$HOST.local:8080      (sau http://$IP:8080)
  Notificări:     instalați aplicația "ntfy" pe telefon și abonați-vă la topicul:
                      $TOPIC_NOW
  Configurare:    sudo nano $ETC/config.yaml   apoi   sudo systemctl restart amper
  Jurnal:         journalctl -u amper -f
  Scanare porturi: sudo systemctl stop amper && amper scan && sudo systemctl start amper
  WiFi fără internet: rețeaua "$(awk '/^wifi_setup:/{f=1} f&&/ssid:/{print $2; exit}' "$ETC/config.yaml")", parola: $(awk '/^wifi_setup:/{f=1} f&&/password:/{print $2; exit}' "$ETC/config.yaml")
                  (apoi pagina se deschide singură, sau http://10.42.0.1)

EOF
[ "$NEW_CONFIG" -eq 1 ] && echo "  Verificați în config capacitățile (capacity_ah) ale modulelor — din ele se calculează SOC-ul total."
grep -q "by-id/REPLACE-" "$ETC/config.yaml" && [ "$DEMO" -eq 0 ] && \
  echo "  ATENȚIE: unele dispozitive nu au fost găsite. Vedeți README, secțiunea 'Dacă un dispozitiv nu e găsit'."
exit 0

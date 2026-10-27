#!/bin/bash
# =====================================================================
#  Construiește imaginea SD Amper (generică, pentru oricine) din Raspberry Pi OS Lite 64-bit oficial.
#
#    sudo bash tools/build-image.sh                 # → dist/amper-<versiune>.img.xz + .sha256
#    IMAGE_URL=... sudo bash tools/build-image.sh   # altă imagine de bază (implicit: ultima Lite 64-bit)
#    COUNTRY=RO                                     # țara WiFi (implicit RO)
#
#  Rulează pe Linux (sau WSL) ca root: ideal pe arm64 (Pi, GitHub `ubuntu-24.04-arm`), altfel cu
#  qemu-user-static (binfmt). Cere: curl xz parted(sfdisk) e2fsprogs losetup.
#
#  De ce chroot peste imaginea oficială și nu pi-gen: Amper e un program peste Raspberry Pi OS, nu un alt
#  sistem; în chroot rulează exact deploy/install.sh (aceeași cale ca o instalare de mână, deci testată), iar
#  construirea durează minute, nu o oră; pi-gen ar însemna să întreținem propriile etape ale sistemului.
#
#  Ce NU e în imagine (se face la prima pornire, deploy/firstboot.sh + serviciul amper): machine-id, cheile
#  SSH, topicul ntfy, cheile VAPID, cheia cookie-ului, baza de date, starea Tailscale. SSH e oprit.
#  Prima pornire: Raspberry Pi OS extinde singur partiția pe tot cardul (și amper-grow-root.service).
# =====================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_URL="${IMAGE_URL:-https://downloads.raspberrypi.com/raspios_lite_arm64_latest}"
COUNTRY="${COUNTRY:-RO}"
OUT="${OUT:-$SRC/dist}"
WORK="${WORK:-$SRC/build}"
EXTRA_MB="${EXTRA_MB:-1536}"          # loc pentru pachete în timpul construirii (se taie la final)

[ "$(id -u)" -eq 0 ] || { echo "Rulați cu sudo"; exit 1; }
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/amper/__init__.py")"
say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
mkdir -p "$OUT" "$WORK"
IMG="$WORK/amper-$VER.img"
ROOT="$WORK/root"
LOOP=""

cleanup() {
  set +e
  for m in boot/firmware dev/pts dev proc sys run; do mountpoint -q "$ROOT/$m" && umount -l "$ROOT/$m"; done
  mountpoint -q "$ROOT" && umount -l "$ROOT"
  [ -n "$LOOP" ] && losetup -d "$LOOP"
}
trap cleanup EXIT

# ---------------- imaginea de bază ----------------
BASE="$WORK/base.img.xz"
if [ ! -s "$BASE" ]; then
  say "Descarc Raspberry Pi OS Lite 64-bit ($IMAGE_URL)"
  curl -fL --retry 3 -o "$BASE.part" -w '%{url_effective}\n' "$IMAGE_URL" | tee "$WORK/base.url"
  mv "$BASE.part" "$BASE"
  # suma oficială (lângă imagine, „<nume>.sha256”), dacă există
  if curl -fsL -o "$WORK/base.sha256" "$(cat "$WORK/base.url").sha256"; then
    echo "$(awk '{print $1}' "$WORK/base.sha256")  $BASE" | sha256sum -c -
  fi
fi
say "Despachetez imaginea de bază"
xz -dc "$BASE" > "$IMG"
truncate -s "+${EXTRA_MB}M" "$IMG"
echo ", +" | sfdisk -q --no-reread -N 2 "$IMG"          # partiția 2 (sistemul) până la capăt; PARTUUID rămâne

LOOP="$(losetup -Pf --show "$IMG")"
partprobe "$LOOP" 2>/dev/null || true
e2fsck -fy "${LOOP}p2" >/dev/null || [ $? -le 1 ]
resize2fs "${LOOP}p2" >/dev/null

mkdir -p "$ROOT"
mount "${LOOP}p2" "$ROOT"
mkdir -p "$ROOT/boot/firmware"
mount "${LOOP}p1" "$ROOT/boot/firmware"
for m in dev dev/pts proc sys; do mount --bind "/$m" "$ROOT/$m"; done
mount -t tmpfs tmpfs "$ROOT/run"          # nu /run al gazdei: systemctl ar vorbi cu systemd-ul ei
in_root() { chroot "$ROOT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
  DEBIAN_FRONTEND=noninteractive "$@"; }
. "$ROOT/etc/os-release"
say "Baza: $PRETTY_NAME"

if [ "$(uname -m)" != "aarch64" ]; then
  [ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ] || { echo "Pe $(uname -m) e nevoie de qemu-user-static (binfmt)"; exit 1; }
fi

# DNS în chroot; serviciile nu pornesc în timpul instalării pachetelor
RC=/run/systemd/resolve/resolv.conf; [ -s "$RC" ] || RC=/etc/resolv.conf
cp -L "$RC" "$ROOT/etc/resolv.conf.amper"
mv "$ROOT/etc/resolv.conf" "$ROOT/etc/resolv.conf.orig" 2>/dev/null || true
mv "$ROOT/etc/resolv.conf.amper" "$ROOT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d" && chmod +x "$ROOT/usr/sbin/policy-rc.d"

# ---------------- Tailscale (depozitul oficial; fără logare) ----------------
say "Adaug Tailscale ($VERSION_CODENAME)"
curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.noarmor.gpg" \
  -o "$ROOT/usr/share/keyrings/tailscale-archive-keyring.gpg"
curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.tailscale-keyring.list" \
  -o "$ROOT/etc/apt/sources.list.d/tailscale.list"
in_root apt-get update -qq
in_root apt-get install -y -qq tailscale >/dev/null

# ---------------- Amper (exact ca o instalare de mână) ----------------
say "Instalez Amper $VER"
rm -rf "$ROOT/tmp/amper-src" && mkdir -p "$ROOT/tmp/amper-src"
cp -r "$SRC/amper" "$SRC/web" "$SRC/deploy" "$ROOT/tmp/amper-src/"
for f in config.example.yaml CHANGELOG.md LICENSE README.md; do cp "$SRC/$f" "$ROOT/tmp/amper-src/"; done
in_root bash /tmp/amper-src/deploy/install.sh --image

# ---------------- sistemul ----------------
say "Pregătesc sistemul (nume, SSH oprit, utilizator intern, WiFi $COUNTRY)"
echo amper > "$ROOT/etc/hostname"
sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tamper/' "$ROOT/etc/hosts"
grep -q "^127.0.1.1" "$ROOT/etc/hosts" || printf '127.0.1.1\tamper\n' >> "$ROOT/etc/hosts"
in_root systemctl disable ssh.service ssh.socket >/dev/null 2>&1 || true
# fără întrebarea „creează un utilizator” la prima pornire: imaginea nu are cont de logare cu parolă
in_root systemctl disable userconfig.service >/dev/null 2>&1 || true
in_root systemctl mask userconfig.service >/dev/null 2>&1 || true
rm -f "$ROOT/etc/ssh/sshd_config.d/rename_user.conf"
for u in pi; do in_root id "$u" >/dev/null 2>&1 && in_root usermod -L "$u"; done
in_root passwd -l root >/dev/null
# țara WiFi (fără ea Raspberry Pi OS ține WiFi-ul blocat, iar hotspotul nu pornește)
CMD="$ROOT/boot/firmware/cmdline.txt"
grep -q "ieee80211_regdom" "$CMD" || sed -i "1s/\$/ cfg80211.ieee80211_regdom=$COUNTRY/" "$CMD"
in_root raspi-config nonint do_wifi_country "$COUNTRY" >/dev/null 2>&1 || true

# cloud-init (Trixie): implicit poate crea un utilizator la prima pornire; Amper nu are cont de logare
if [ -f "$ROOT/boot/firmware/user-data" ]; then
  cat > "$ROOT/boot/firmware/user-data" <<'CI'
#cloud-config
# Amper: fără utilizator de logare (SSH e oprit); totul se configurează din aplicație.
users: []
ssh_pwauth: false
CI
fi
for f in user-data meta-data network-config; do
  [ -f "$ROOT/boot/firmware/$f" ] && { echo "--- boot/firmware/$f"; grep -v '^\s*$' "$ROOT/boot/firmware/$f" | head -30; }
done
echo "--- cmdline.txt: $(cat "$CMD")"

# ---------------- curățenie: nimic unic sau personal în imagine ----------------
say "Curăț (chei, machine-id, jurnale, cache)"
in_root apt-get clean
rm -rf "$ROOT"/var/lib/apt/lists/* "$ROOT/tmp/amper-src" "$ROOT/usr/sbin/policy-rc.d"
rm -f "$ROOT"/etc/ssh/ssh_host_*
: > "$ROOT/etc/machine-id"
rm -f "$ROOT/var/lib/dbus/machine-id"
rm -rf "$ROOT"/var/lib/amper/* "$ROOT"/var/lib/tailscale/* "$ROOT"/var/log/journal/* "$ROOT"/root/.bash_history
find "$ROOT/var/log" -type f -exec truncate -s 0 {} +
find "$ROOT/opt" -name "__pycache__" -type d -prune -exec rm -rf {} +
mv "$ROOT/etc/resolv.conf.orig" "$ROOT/etc/resolv.conf" 2>/dev/null || rm -f "$ROOT/etc/resolv.conf"

# verificări: ce trebuie să fie și ce nu trebuie să fie
say "Verific imaginea"
fail() { echo "::error::$*"; ls -la "$ROOT/etc/systemd/system" "$ROOT/etc/systemd/system/multi-user.target.wants" || true; exit 1; }
[ -L "$ROOT/opt/amper" ] && [ -f "$ROOT/opt/amper-$VER/amper/__init__.py" ] || fail "programul lipsește"
for u in amper amper-update amper-wifi amper-firstboot amper-grow-root amper-password-reset; do
  [ -L "$ROOT/etc/systemd/system/multi-user.target.wants/$u.service" ] || fail "$u.service nu e activat"
done
for f in multi-user.target.wants/ssh.service sockets.target.wants/ssh.socket; do
  [ -L "$ROOT/etc/systemd/system/$f" ] && fail "SSH e pornit ($f)"
done
[ -f "$ROOT/etc/amper/.firstboot" ] || fail "lipsește marcajul primei porniri"
grep -q "REPLACE-TOPIC" "$ROOT/etc/amper/config.yaml" || fail "topicul ntfy e deja generat"
in_root python3 -c "import yaml,sys; c=yaml.safe_load(open('/etc/amper/config.yaml')); sys.exit(c['devices']!=[])" \
  || fail "imaginea are dispozitive în config"
in_root python3 -c "import sys; sys.path.insert(0,'/opt/amper'); import amper.web, amper.update, amper.wifi_setup, amper.drivers.daly" \
  || fail "programul nu se încarcă"
[ -x "$ROOT/usr/bin/tailscale" ] || fail "lipsește Tailscale"
[ ! -f "$ROOT/boot/firmware/user-data" ] || grep -q "^users: \[\]" "$ROOT/boot/firmware/user-data" || fail "cloud-init creează un utilizator"
[ -z "$(ls -A "$ROOT/var/lib/amper")" ] && [ -z "$(ls -A "$ROOT/var/lib/tailscale" 2>/dev/null)" ] || fail "date în imagine"
ls "$ROOT"/etc/ssh/ssh_host_* >/dev/null 2>&1 && fail "chei SSH în imagine"
[ -s "$ROOT/etc/machine-id" ] && fail "machine-id în imagine"
echo "  ok"

# ---------------- micșorare și compresie ----------------
say "Micșorez și comprim"
for m in boot/firmware dev/pts dev proc sys run; do umount "$ROOT/$m"; done
umount "$ROOT"
e2fsck -fy "${LOOP}p2" >/dev/null || [ $? -le 1 ]
resize2fs -M "${LOOP}p2" >/dev/null 2>&1
BS="$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block size/{gsub(/ /,"",$2); print $2}')"
BC="$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block count/{gsub(/ /,"",$2); print $2}')"
START="$(sfdisk -d "$IMG" | awk -F'[=,]' '/img2 /{gsub(/ /,"",$2); print $2}')"
losetup -d "$LOOP"; LOOP=""
SECTORS=$(( BC * BS / 512 ))
echo ", $SECTORS" | sfdisk -q --no-reread -N 2 "$IMG"
truncate -s $(( (START + SECTORS) * 512 )) "$IMG"
NAME="amper-$VER.img.xz"
xz -T0 -6 -c "$IMG" > "$OUT/$NAME"
(cd "$OUT" && sha256sum "$NAME" > "$NAME.sha256")
rm -f "$IMG"
say "Gata: $OUT/$NAME ($(du -h "$OUT/$NAME" | cut -f1)), baza: $PRETTY_NAME"

#!/bin/bash
# =====================================================================
#  Copie de siguranță în Google Drive, folderul „back-up”:
#     back-up/back-up/  datele: config, istoricul (baza de date), legăturile Daly, Tailscale
#                       (amper-date-AAAA-LL-ZZ.tar.gz, se păstrează ultimele 14)
#     back-up/clona/    imaginea întregului card (amper-clona-AAAA-LL-ZZ.img.gz), O DATĂ PE SĂPTĂMÂNĂ
#                       (noaptea de duminică spre luni); după ce noua clonă e urcată, cea veche se șterge
#                       DEFINITIV (fără Coș, ca să nu ocupe spațiu). Se scrie pe un card nou cu Raspberry Pi
#                       Imager („Use custom”) și pornește ca acesta.
#
#  Rulat în fiecare noapte la 00:00 de amper-backup.timer (datele zilnic, clona lunea). Manual:
#     sudo bash /opt/amper/deploy/backup.sh            # ca noaptea: datele + clona dacă e luni (sau dacă lipsește)
#     sudo bash /opt/amper/deploy/backup.sh date       # doar datele
#     sudo bash /opt/amper/deploy/backup.sh clona      # doar clona, acum
#  Legătura cu Drive-ul: /etc/amper/rclone.conf (rclone, acces doar la fișierele create de el).
#  Opțională: install.sh pornește amper-backup.timer doar dacă există /etc/amper/rclone.conf.
# =====================================================================
set -uo pipefail

WHAT="${1:-tot}"
RCLONE="rclone --config /etc/amper/rclone.conf"
REMOTE="${AMPER_BACKUP_REMOTE:-gdrive:back-up}"   # remote rclone + folder (opțional în /etc/amper/backup.env)
KEEP_DATE=14
KEEP_CLONA=1                 # doar cea mai nouă; cea veche se șterge definitiv după ce noua e urcată
CLONA_DAY=1                  # ziua clonei (date +%u: 1 = luni, adică la 00:00 în noaptea de duminică spre luni)
WORK=/var/tmp/amper-backup
DAY="$(date +%Y-%m-%d)"
FAILED=()

[ "$(id -u)" -eq 0 ] || { echo "Rulați cu sudo"; exit 1; }
log() { echo "$(date '+%F %T') $*"; }
notify() { PYTHONPATH=/opt/amper python3 -m amper notify --title "Amper: copie de siguranță" --priority high "$1" || true; }

# păstrează doar ultimele N fișiere dintr-un folder din Drive (numele conțin data, deci ordinea alfabetică e cronologică)
# (al treilea argument „definitiv” = fără Coș)
prune() {
  local dir="$1" keep="$2" trash=true
  [ "${3:-}" = definitiv ] && trash=false
  $RCLONE lsf "$REMOTE/$dir" --files-only 2>/dev/null | sort | head -n -"$keep" | while read -r f; do
    $RCLONE deletefile --drive-use-trash="$trash" "$REMOTE/$dir/$f" && log "șters vechi${3:+ ($3)}: $dir/$f"
  done
}

# ---------------- datele ----------------
backup_date() {
  local t="$WORK/date" out="$WORK/amper-date-$DAY.tar.gz"
  rm -rf "$t"; mkdir -p "$t/etc" "$t/var-lib-amper" "$t/var-lib-tailscale"
  cp -a /etc/amper/. "$t/etc/"
  sqlite3 /var/lib/amper/amper.db ".backup '$t/var-lib-amper/amper.db'" || return 1   # copie corectă și în timpul scrierii
  for f in daly-auto.json vapid.json settings.yaml cookie.key; do   # legăturile Daly, cheile notificărilor, setările din aplicație
    [ -f "/var/lib/amper/$f" ] && cp -a "/var/lib/amper/$f" "$t/var-lib-amper/"
  done
  [ -d /var/lib/tailscale ] && cp -a /var/lib/tailscale/. "$t/var-lib-tailscale/"
  date '+%F %T' > "$t/data-copiei.txt"
  hostname >> "$t/data-copiei.txt"
  tar -czf "$out" -C "$t" . || return 1
  $RCLONE copyto "$out" "$REMOTE/back-up/$(basename "$out")" || return 1
  log "date: încărcat $(du -h "$out" | cut -f1) → back-up/back-up/$(basename "$out")"
  rm -rf "$t" "$out"
  prune back-up "$KEEP_DATE"
}

# ---------------- clona cardului ----------------
# Imagine nouă, cât spațiul folosit + rezervă, cu aceleași partiții și același identificator de disc
# (PARTUUID), deci pornește pe orice card de cel puțin mărimea ei; la prima pornire partiția se extinde
# singură pe tot cardul (amper-grow-root.service).
backup_clona() (                # subshell: curățenia (trap EXIT) rămâne locală
  local img="$WORK/amper-clona-$DAY.img" mnt="$WORK/mnt" disk boot_start boot_size label used_k root_mb loop
  disk="/dev/$(lsblk -no pkname "$(findmnt -no SOURCE /)")"
  label="$(sfdisk --dump "$disk" | awk '/^label-id:/ {print $2}')"
  boot_start="$(sfdisk --dump "$disk" | awk -F'[=, ]+' '/start=/ {print $4; exit}')"
  boot_size="$(sfdisk --dump "$disk" | awk -F'[=, ]+' '/start=/ {print $6; exit}')"
  used_k="$(df --output=used -k / | tail -1)"
  root_mb=$(( used_k * 13 / 10 / 1024 + 1024 ))                 # folosit + 30% + 1 GB
  local need_k=$(( used_k + 2 * 1024 * 1024 ))
  [ "$(df --output=avail -k /var/tmp | tail -1)" -gt "$need_k" ] || { log "clona: spațiu insuficient pe card"; return 1; }

  rm -f "$img"; mkdir -p "$mnt"
  truncate -s $(( (boot_start + boot_size) * 512 + root_mb * 1024 * 1024 )) "$img"
  printf 'label: dos\nlabel-id: %s\nunit: sectors\n\nstart=%s, size=%s, type=c\nstart=%s, type=83\n' \
    "$label" "$boot_start" "$boot_size" "$(( boot_start + boot_size ))" | sfdisk -q "$img" || return 1
  loop="$(losetup -fP --show "$img")" || return 1
  trap 'umount -R "$mnt" 2>/dev/null; losetup -d "$loop" 2>/dev/null; rm -f "$img"' EXIT
  mkfs.vfat -F 32 -n bootfs "${loop}p1" >/dev/null && mkfs.ext4 -q -F -L rootfs "${loop}p2" || return 1
  mount "${loop}p2" "$mnt" && mkdir -p "$mnt/boot/firmware" && mount "${loop}p1" "$mnt/boot/firmware" || return 1

  rsync -aHAXx --numeric-ids \
    --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' --exclude='/run/*' --exclude='/tmp/*' \
    --exclude='/mnt/*' --exclude='/media/*' --exclude='/var/tmp/*' --exclude='/lost+found' \
    --exclude='/var/cache/apt/archives/*.deb' --exclude='/var/swap' \
    --exclude='/var/lib/amper/amper.db*' / "$mnt/" || [ $? -eq 24 ] || return 1     # 24 = fișiere dispărute între timp
  rsync -rt --modify-window=2 /boot/firmware/ "$mnt/boot/firmware/" || return 1
  sqlite3 /var/lib/amper/amper.db ".backup '$mnt/var/lib/amper/amper.db'" && chown amper:amper "$mnt/var/lib/amper/amper.db"
  sync
  umount -R "$mnt" && losetup -d "$loop"
  trap 'rm -f "$img"' EXIT

  log "clona: imagine $(( $(stat -c %s "$img") / 1024 / 1024 )) MB, comprim și încarc…"
  pigz -1 -c "$img" | $RCLONE rcat "$REMOTE/clona/$(basename "$img").gz" || return 1
  log "clona: încărcat → back-up/clona/$(basename "$img").gz"
  prune clona "$KEEP_CLONA" definitiv
)

mkdir -p "$WORK"
[ -f /etc/amper/rclone.conf ] || { notify "Copia NU s-a făcut: Google Drive nu e configurat (/etc/amper/rclone.conf)."; exit 1; }
if [ "$WHAT" = tot ] || [ "$WHAT" = date ]; then
  log "copie date…"; backup_date || FAILED+=("datele")
fi
DO_CLONA=0
[ "$WHAT" = clona ] && DO_CLONA=1
if [ "$WHAT" = tot ]; then
  if [ "$(date +%u)" = "$CLONA_DAY" ]; then
    DO_CLONA=1
  elif [ -z "$($RCLONE lsf "$REMOTE/clona" --files-only 2>/dev/null)" ]; then
    DO_CLONA=1; log "nu există încă nicio clonă în Drive: o fac acum"
  fi
fi
if [ "$DO_CLONA" -eq 1 ]; then
  log "clona cardului…"; backup_clona || FAILED+=("clona cardului")
fi
rm -rf "$WORK"
if [ ${#FAILED[@]} -gt 0 ]; then
  notify "Copia de siguranță din $DAY a EȘUAT pentru: ${FAILED[*]}. Vezi: journalctl -u amper-backup"
  exit 1
fi
log "gata"

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
#
#  Jurnalul fiecărei rulări (mesajele și erorile comenzilor rclone/rsync/pigz/sfdisk…) rămâne și în
#  /var/log/amper/backup.log (ultimele ~2000 de rânduri); la eșec, notificarea spune pasul și eroarea.
# =====================================================================
set -uo pipefail

WHAT="${1:-tot}"
# --low-level-retries: o cădere scurtă a internetului (router 4G) reia bucata întreruptă, nu tot fișierul
RCLONE="rclone --config /etc/amper/rclone.conf --low-level-retries 30"
REMOTE="${AMPER_BACKUP_REMOTE:-gdrive:back-up}"   # remote rclone + folder (opțional în /etc/amper/backup.env)
KEEP_DATE=14
KEEP_CLONA=1                 # doar cea mai nouă; cea veche se șterge definitiv după ce noua e urcată
CLONA_DAY=1                  # ziua clonei (date +%u: 1 = luni, adică la 00:00 în noaptea de duminică spre luni)
UPLOAD_TRIES="${AMPER_BACKUP_TRIES:-3}"   # încercări pentru încărcarea în Drive, la UPLOAD_WAIT secunde una de alta
UPLOAD_WAIT="${AMPER_BACKUP_RETRY_WAIT:-300}"
WORK=/var/tmp/amper-backup
LOGDIR="${AMPER_LOG_DIR:-/var/log/amper}"
LOGF="$LOGDIR/backup.log"
LOG_KEEP=2000                # rânduri păstrate în backup.log
DAY="$(date +%Y-%m-%d)"
FAILED=()

[ "$(id -u)" -eq 0 ] || { echo "Rulați cu sudo"; exit 1; }
mkdir -p "$LOGDIR" "$WORK"
# rotația: păstrează doar ultimele LOG_KEEP rânduri (înainte ca fișierul să fie ținut deschis mai jos)
if [ -f "$LOGF" ] && [ "$(wc -l < "$LOGF")" -gt "$LOG_KEEP" ]; then
  tail -n "$LOG_KEEP" "$LOGF" > "$LOGF.tmp" && mv "$LOGF.tmp" "$LOGF"
fi
ERRF="$(mktemp)"             # erorile ultimei comenzi
REASONF="$(mktemp)"          # motivul ultimului eșec (scris și din subshell-ul clonei)
trap 'rm -f "$ERRF" "$REASONF"' EXIT
exec 2> >(tee -a "$LOGF" >&2)          # orice eroare ajunge și în fișier

log() { local l; l="$(date '+%F %T') $*"; echo "$l"; echo "$l" >> "$LOGF"; }
notify() {
  # ca utilizatorul amper (proprietarul bazei de date, unde se salvează notificarea pentru tabul Alerte)
  runuser -u amper -- env PYTHONPATH=/opt/amper python3 -m amper notify --source backup \
    --title "Amper: copie de siguranță" --priority high "$1" 2>&1 | tail -n 3 >> "$LOGF" || true
}

# fail "pas" [cod] — notează motivul: pasul + ultima linie din erorile comenzii (sau codul de ieșire)
fail() {
  local why
  why="$(grep -v '^[[:space:]]*$' "$ERRF" 2>/dev/null | tail -n 1 \
        | sed -E 's#^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9:]+ [A-Z]+ ?: ?##' | cut -c1-240)"   # fără data din fața erorii rclone
  [ -z "$why" ] && [ -n "${2:-}" ] && why="cod de ieșire $2"
  echo "$1 a eșuat${why:+: $why}" > "$REASONF"
  log "EȘUAT — $(cat "$REASONF")"
  return 1
}
# run "pas" comandă… — rulează comanda; erorile ei intră în jurnal; la eșec întoarce 1 cu motivul notat
run() {
  local what="$1" rc; shift
  "$@" 2>"$ERRF"; rc=$?
  [ -s "$ERRF" ] && sed 's/^/    /' "$ERRF" >&2          # stderr merge și în fișier (exec de mai sus)
  [ "$rc" -eq 0 ] && return 0
  fail "$what" "$rc"
}
# retry "pas" comandă… — ca run, cu UPLOAD_TRIES încercări (internetul 4G poate cădea câteva minute)
retry() {
  local what="$1" i; shift
  for i in $(seq 1 "$UPLOAD_TRIES"); do
    run "$what" "$@" && return 0
    [ "$i" -lt "$UPLOAD_TRIES" ] && { log "reîncerc peste $UPLOAD_WAIT s (încercarea $((i + 1)) din $UPLOAD_TRIES)"; sleep "$UPLOAD_WAIT"; }
  done
  return 1
}

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
  run "date: copierea config-ului" cp -a /etc/amper/. "$t/etc/" || return 1
  # copie corectă și în timpul scrierii
  run "date: copia bazei de date" sqlite3 /var/lib/amper/amper.db ".backup '$t/var-lib-amper/amper.db'" || return 1
  for f in daly-auto.json daly-excluded.json vapid.json settings.yaml cookie.key; do   # legăturile Daly și modulele excluse, cheile notificărilor, setările din aplicație
    [ -f "/var/lib/amper/$f" ] && cp -a "/var/lib/amper/$f" "$t/var-lib-amper/"
  done
  [ -d /var/lib/tailscale ] && cp -a /var/lib/tailscale/. "$t/var-lib-tailscale/"
  date '+%F %T' > "$t/data-copiei.txt"
  hostname >> "$t/data-copiei.txt"
  run "date: arhivarea" tar -czf "$out" -C "$t" . || return 1
  retry "date: încărcarea în Drive" $RCLONE copyto "$out" "$REMOTE/back-up/$(basename "$out")" || return 1
  log "date: încărcat $(du -h "$out" | cut -f1) → back-up/back-up/$(basename "$out")"
  rm -rf "$t" "$out"
  prune back-up "$KEEP_DATE"
}

# ---------------- clona cardului ----------------
# Imagine nouă, cât spațiul folosit + rezervă, cu aceleași partiții și același identificator de disc
# (PARTUUID), deci pornește pe orice card de cel puțin mărimea ei; la prima pornire partiția se extinde
# singură pe tot cardul (amper-grow-root.service).
backup_clona() (                # subshell: curățenia (trap EXIT) rămâne locală; motivul eșecului merge prin $REASONF
  local img="$WORK/amper-clona-$DAY.img" mnt="$WORK/mnt" disk boot_start boot_size label used_k root_mb loop dump
  disk="/dev/$(lsblk -no pkname "$(findmnt -no SOURCE /)")"
  dump="$(sfdisk --dump "$disk" 2>"$ERRF")" || { fail "clona: citirea partițiilor cardului"; exit 1; }
  label="$(awk '/^label-id:/ {print $2}' <<<"$dump")"
  boot_start="$(awk -F'[=, ]+' '/start=/ {print $4; exit}' <<<"$dump")"
  boot_size="$(awk -F'[=, ]+' '/start=/ {print $6; exit}' <<<"$dump")"
  used_k="$(df --output=used -k / | tail -1)"
  root_mb=$(( used_k * 13 / 10 / 1024 + 1024 ))                 # folosit + 30% + 1 GB
  local need_k=$(( used_k + 2 * 1024 * 1024 ))
  local avail_k; avail_k="$(df --output=avail -k /var/tmp | tail -1)"
  [ "$avail_k" -gt "$need_k" ] || { : > "$ERRF"; fail "clona: verificarea spațiului ($(( avail_k / 1024 )) MB liberi pe card, trebuie $(( need_k / 1024 )) MB)"; exit 1; }

  rm -f "$img"; mkdir -p "$mnt"
  truncate -s $(( (boot_start + boot_size) * 512 + root_mb * 1024 * 1024 )) "$img"
  printf 'label: dos\nlabel-id: %s\nunit: sectors\n\nstart=%s, size=%s, type=c\nstart=%s, type=83\n' \
    "$label" "$boot_start" "$boot_size" "$(( boot_start + boot_size ))" > "$WORK/parts.txt"
  run "clona: partițiile imaginii (sfdisk)" sfdisk -q "$img" < "$WORK/parts.txt" || exit 1
  loop="$(losetup -fP --show "$img" 2>"$ERRF")" || { fail "clona: atașarea imaginii (losetup)"; exit 1; }
  trap 'umount -R "$mnt" 2>/dev/null; losetup -d "$loop" 2>/dev/null; rm -f "$img"' EXIT
  run "clona: formatarea partiției de pornire" mkfs.vfat -F 32 -n bootfs "${loop}p1" || exit 1
  run "clona: formatarea partiției sistemului" mkfs.ext4 -q -F -L rootfs "${loop}p2" || exit 1
  run "clona: montarea imaginii" mount "${loop}p2" "$mnt" || exit 1
  mkdir -p "$mnt/boot/firmware"
  run "clona: montarea partiției de pornire" mount "${loop}p1" "$mnt/boot/firmware" || exit 1

  copy_root() {
    rsync -aHAXx --numeric-ids \
      --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' --exclude='/run/*' --exclude='/tmp/*' \
      --exclude='/mnt/*' --exclude='/media/*' --exclude='/var/tmp/*' --exclude='/lost+found' \
      --exclude='/var/cache/apt/archives/*.deb' --exclude='/var/swap' \
      --exclude='/var/lib/amper/amper.db*' / "$mnt/" || [ $? -eq 24 ]     # 24 = fișiere dispărute între timp
  }
  run "clona: copierea sistemului (rsync)" copy_root || exit 1
  run "clona: copierea partiției de pornire (rsync)" rsync -rt --modify-window=2 /boot/firmware/ "$mnt/boot/firmware/" || exit 1
  run "clona: copia bazei de date" sqlite3 /var/lib/amper/amper.db ".backup '$mnt/var/lib/amper/amper.db'" || exit 1
  chown amper:amper "$mnt/var/lib/amper/amper.db"
  sync
  run "clona: demontarea imaginii" umount -R "$mnt" || exit 1
  losetup -d "$loop"
  trap 'rm -f "$img"' EXIT

  log "clona: imagine $(( $(stat -c %s "$img") / 1024 / 1024 )) MB, comprim și încarc…"
  upload() { pigz -1 -c "$img" | $RCLONE rcat "$REMOTE/clona/$(basename "$img").gz"; }
  retry "clona: încărcarea în Drive" upload || exit 1
  log "clona: încărcat → back-up/clona/$(basename "$img").gz"
  prune clona "$KEEP_CLONA" definitiv
)

# un rând de început pe rulare, ca rulările să se deosebească în fișier
log "===== copia de siguranță ($WHAT) ====="
[ -f /etc/amper/rclone.conf ] || { log "Google Drive nu e configurat"; notify "Copia NU s-a făcut: Google Drive nu e configurat (/etc/amper/rclone.conf)."; exit 1; }
REASONS=()
if [ "$WHAT" = tot ] || [ "$WHAT" = date ]; then
  log "copie date…"; : > "$REASONF"
  backup_date || { FAILED+=("datele"); REASONS+=("$(cat "$REASONF")"); }
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
  log "clona cardului…"; : > "$REASONF"
  backup_clona || { FAILED+=("clona cardului"); REASONS+=("$(cat "$REASONF")"); }
fi
rm -rf "$WORK"
if [ ${#FAILED[@]} -gt 0 ]; then
  why="$(printf '%s; ' "${REASONS[@]}")"; why="${why%; }"
  notify "Copia de siguranță din $DAY a EȘUAT. ${why:-motiv necunoscut}. Jurnalul: $LOGF"
  exit 1
fi
log "gata"

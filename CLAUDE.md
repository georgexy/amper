# CLAUDE.md — context pentru continuarea proiectului

> Repo pentru distribuție (vezi `BRIEF-amper-distributie.md`). **Nimic personal în fișierele urmărite de git**:
> IP-uri, nume tailnet, e-mailuri, topic ntfy, parole, MAC-uri, rețele WiFi, utilizatori Linux stau în `LOCAL.md`
> (ignorat de git, doar pe laptopul autorului). „Instalația de referință” mai jos = instalația autorului.

## Ce este
`amper` înlocuiește SolarAssistant pe un Raspberry Pi 4 (Raspberry Pi OS Lite 64-bit) al unei instalații off-grid.
Utilizatorul (autorul proiectului, inginer mecanic, vorbește română — răspunde în română) vrea în primul rând să vadă SOC-ul și
starea fiecărui modul în timp real și să fie alertat pe telefon când un BMS oprește încărcarea fără să semnaleze
(i s-a întâmplat cu un Daly și a rămas fără curent).

## Hardware la locație
- 3 module LiFePO4 48 V (16S), **105 Ah** fiecare, cu **BMS Daly**, fiecare pe câte un cablu **USB-UART** propriu (UART nu se
  poate pune în paralel) sau prin modulul Bluetooth al BMS-ului (toate trei au nume „DL-…”). Încă neaduse la locație.
- **3 pachete Seplos V2** în paralel pe același **RS485**, adresele **1, 2, 3**, la **19200** baud, prin adaptor
  USB **CH340** (`usb-1a86_USB_Serial-if00-port0`). Confirmat pe hardware 2026-10-25: răspund la CID2 0x42.
  Măsurat: o citire (0x42 + 0x44) ≈ 0,19 s; **fără pauză între tranzacții, pachetul întrebat imediat după altul nu
  răspunde** (50 ms ajung) → `SeplosDriver.bus_gap = 0.1` (`_Bus.last_end`). Growatt: o citire ≈ 0,33 s.
  Driverele de pe același port își împart portul (`drivers/base.py`, `get_bus`). BMS-ul nu are ecran.
- Invertor **Growatt SPF 5000 ES** (off-grid), Modbus RTU pe portul USB/RS232, prin adaptor **Exar XR21V1410**
  (`usb-04e2_1410-if00-port0`) — confirmat că răspunde la 9600, adresa 1. A avut o problemă: citirile PV rămân
  „înghețate” după deconectarea panourilor. Utilizatorul plănuiește să-l înlocuiască cu un **Deye 8 kW monofazat** —
  atunci va fi nevoie de un driver Deye nou (Modbus, registre holding).
- Posibil în viitor: acumulator Ecobat PowerCube P15A (BMS Daren Hi-Tec, RS485/CAN, protocol Pylon/Victron/Growatt).

## Arhitectură
- `amper/drivers/` — câte un driver pe tip (`daly.py`, `seplos.py`, `growatt_spf.py`, `simulator.py`); `read()` e
  blocant și rulează în thread. Convenție: curentul/puterea bateriei **pozitive = încărcare**.
- `engine.py` — buclă de citire pe dispozitiv (`poll_interval` 2 s), stare curentă, salvare în SQLite la 10 s,
  integrare energie zilnică. Starea se trimite pe WebSocket imediat după fiecare citire reușită (`_push`), nu doar la tick.
- `alerts.py` — reguli + histerezis (o condiție trebuie să persiste `hold` s; se închide după 180 s de absență).
  Regula cheie: `not_charging` (banca se încarcă, modulul are I ≈ 0, SOC < 97%, 10 min).
- Ceasul: Pi-ul n-are RTC; la boot ora e cea din `/var/lib/systemd/timesync/clock`, apoi NTP o face să sară
  (2026-10-25: +4 min → alerte false `comm_lost`). `Engine.check_clock()` (la fiecare tick) detectează saltul
  (diferența time.time − monotonic > 5 s) și mută `started`, `last_ok` și momentele alertelor cu același salt.
- `notify.py` — ntfy (JSON publish) și Telegram, cu coadă de reîncercare.
- `db.py` — `pack_samples` / `inverter_samples` (brute, 14 zile) + `*_5m` (agregate permanente), `energy_daily`, `alerts`.
- `web.py` (aiohttp) + `web/` — PWA în vanilla JS, fără CDN (fonturi Barlow locale), WebSocket `/api/ws`.
  Parola (`web.password`) protejează `/api/*` cu un cookie; cererile de pe 127.0.0.1/::1 (kiosk-ul) sunt scutite.
- Teme (`app.js`, `LOOKS`: `cadrane`, `flux`, `carduri`), toate întunecate (tema luminoasă a fost scoasă): fiecare
  are `html()` (schelet, desenat o dată) și `update(root, homeData())`. Alegerea e în localStorage (`look`), din
  Setări (rotița); `?tema=<nume>` în adresă o impune (pentru kiosk). Bateria deschide ecranul `view-packs`.
- Dispozitive „conectate”: `DeviceState.connected` = a răspuns de la pornire sau are citiri în DB în ultimele
  `raw_retention_days` (`Database.seen_devices`). Cele neconectate nu se afișează (`app.js`, `connected()`) și nu
  dau `comm_lost`.
- `deploy/install.sh` — instalare/actualizare idempotentă, cu `autoconfig` (scanare porturi → completează config).
- `deploy/kiosk.sh` — kiosk pe HDMI: utilizator `kiosk`, serviciul `amper-kiosk` (cage + Chromium `--kiosk` la
  `http://localhost:8080`, PAM pe tty1, `Conflicts=getty@tty1`), `graphical.target`; `--remove` revine la consolă.
  Lansatorul `/usr/local/bin/amper-kiosk-browser` așteaptă serverul (max. 90 s) înainte de Chromium.

## Ce e verificat și ce NU
Verificat: teste unitare pe cadrele de protocol (Daly checksum, cadrul publicat Seplos `~20004642E00200FD37`,
lungimea răspunsului Seplos de 75 de octeți, CRC Modbus), alertele în simulare, instalarea pe Ubuntu (systemd simulat).

**De verificat pe hardware** (încă nu s-a rulat pe dispozitivele reale):
1. Daly: adresa gazdei (0x40 vs 0x80), semnul curentului (`invert_current`), BMS-ul care adoarme, Bluetooth pe același UART.
2. Seplos: baudrate (19200/9600); în `decode_status` (CID2 0x44), poziția octetului de stare a comutatoarelor
   (`custom[6]`, bit0 = descărcare, bit1 = încărcare) e după documentația V2 — de confirmat cu afișajul BMS-ului.
3. Growatt: semnul lui `Bat_Watt` 77–78 (`battery_power_sign`). `GET /api/debug/inverter` arată registrele brute.

## Daly: cablu, Bluetooth, detectare automată (adăugat 2026-10-25, netestat pe module reale)
- `daly.py`: `DalyProtocol` (comenzi/decodare) + `DalyDriver` (UART, `port:`) + `DalyBleDriver` (`ble:` MAC/nume,
  adresa gazdei implicită 0x80). `ble.py`: bleak (python3-bleak) într-o buclă asyncio proprie; serviciul 0xFFF0,
  notificări 0xFFF1, scriere 0xFFF2 — UUID-uri DE VERIFICAT pe modulele reale (există fallback pe orice
  notify+write din 0xFFF0). Modulele Daly „noi” cu protocol Modbus prin BLE NU sunt suportate.
- `daly_auto.py`: Daly cu `port: auto` sau `REPLACE-...` caută în fundal: porturi USB nefolosite de alte
  dispozitive, apoi BLE cu nume `DL-`/`DALY` (după RSSI); legăturile în `/var/lib/amper/daly-auto.json`.
  Engine-ul trimite notificare la prima conectare a unui dispozitiv. `POST /api/daly/search` = „Caută acum”.
- Pe Pi: Bluetooth era blocat din rfkill; `amper.service` face `rfkill unblock bluetooth` + `bluetoothctl power on`
  la pornire (ExecStartPre cu „+”). WiFi rămâne blocat (Pi-ul e pe Ethernet). Politica D-Bus BlueZ permite oricărui
  utilizator, deci `amper` nu are nevoie de grupul bluetooth. La 2026-10-25 nu era niciun dispozitiv BLE în raza Pi-ului.
- Atenție la editarea fișierelor cu Python pe Windows: `open(p, "w")` scrie CRLF — a stricat `install.sh` o dată.
  Folosește `newline="\n"` sau convertește după.

## WiFi și mutarea Pi-ului (2026-10-25)
- Cardul va fi mutat într-un Pi nou, la locația modulelor Daly, unde e doar WiFi (fără cablu); monitorul vine luna
  următoare. WiFi-ul era blocat din rfkill → deblocat (și `amper.service` face `rfkill unblock wifi` la pornire).
- `amper/wifi_setup.py` + `amper-wifi.service` (root): fără internet `wifi_setup.offline_minutes` (2) → hotspot NM
  „Amper-Setup” (mode ap, ipv4 shared, 10.42.0.1), DNS-ul hotspotului trimite totul la 10.42.0.1
  (`/etc/NetworkManager/dnsmasq-shared.d/amper-portal.conf`), pagina pe portul 80 cu rețelele scanate înainte.
  Conectarea salvează profilul `wifi-<SSID>` (prioritate 10); la eșec îl șterge și repornește hotspotul.
  `--test-ap N` = test forțat N secunde (testat pe Pi, cu cablul băgat: rețea, pagină, DNS, oprire curată).
  NETESTAT: conectarea efectivă a unui telefon și a Pi-ului la o rețea aleasă din pagină.
- Kiosk: `ExecStartPre` verifică `/sys/class/drm/card*-HDMI-A-*/status`; fără monitor reîncearcă la 30 s.

## Copia de siguranță (2026-10-25)
- `deploy/backup.sh` (rulat ca root de `amper-backup.timer`, zilnic 00:00, Persistent): datele (`sqlite3 .backup`,
  /etc/amper, daly-auto.json, /var/lib/tailscale) → `gdrive:back-up/back-up` (14); clona = imagine nouă cât spațiul
  folosit +30% +1 GB, aceleași partiții și același label-id (PARTUUID), rsync din sistemul viu, DB prin `.backup`,
  `pigz | rclone rcat` → `gdrive:back-up/clona`, săptămânal (luni 00:00, sau dacă nu există nicio clonă), se păstrează 1, cea veche ștearsă definitiv (`--drive-use-trash=false`, cerut de utilizator). `amper-grow-root.service` extinde rădăcina la pornire.
- rclone: `/etc/amper/rclone.conf` (root 600), remote `gdrive`, scope `drive.file`. Autorizat de utilizator prin
  link Google + adresa 127.0.0.1:53682 de după redirect, trimisă la `rclone config create` care aștepta pe Pi.
- `install.sh --restore <tar.gz>` pune datele la loc pe o instalare nouă. `amper notify "mesaj"` = notificare din scripturi.

## Planificat: Pi-ul ca „BMS al băncii” (de pornit după ce Daly sunt conectate)
Aprobat de utilizator pe 2026-10-25, încă neînceput. Pi-ul coordonează banca mixtă prin invertor:
(1) pachet care nu mai acceptă încărcare → scade curentul maxim de încărcare (holding 34); (2) celulă aproape de
limită în orice pachet → scade curentul la final; (3) decizii după SOC-ul real, nu după tensiune; (4) reset SOC Daly
la 100% la bancă plină. Etapa 1: doar Modbus, puține scrieri (uzura memoriei invertorului), întâi „mod observare”.
Etapa 2: emulare BMS (Pylontech?) pe portul BMS al invertorului — adaptor nou + manualul SPF 5000 ES.
Reguli: nu se ating protecțiile BMS, limitele invertorului rămân plasa de siguranță, watchdog, activare doar de utilizator.

## Alerte proprii + cifra de pe Alerte (2026-10-26)
- Tabela `user_alerts`; `alerts.METRICS` (SOC bancă, SOC minim modul, tensiune, celule min/max, temperatură module,
  PV, consum, încărcare, rețea, temperatură invertor); `evaluate_user` cu histerezis (marjă per metrică), durată
  minimă (`hold_s`), nivel info/warning/critical, `notify_clear`. API `/api/user-alerts` (GET/POST, POST/DELETE /<id>).
  În aplicație: Alerte → „Alertele mele” → „+ Adaugă alertă” cu șabloane. Nivelul info nu apare ca banner.
- Cifra roșie de pe Alerte = alertele active nevăzute (văzute = deschis tabul Alerte; în localStorage, per telefon).

## Setări → Info (2026-10-26)
- `amper/sysinfo.py` (ca utilizatorul amper; grupul video pentru `vcgencmd get_throttled`) → `GET /api/system`.
- Schimbarea WiFi: `amper-wifi` (root) are un API local 127.0.0.1:8091 cu token în `/run/amper-wifi/token`
  (grup amper): `/scan`, `/status`, `POST /connect`. `WifiSetup.switch`: profil nou `wifi-<SSID>` prioritate 20;
  dacă nu se conectează sau nu are internet în 40 s → șterge profilul și reactivează rețeaua veche.
  Web: `/api/wifi/scan|status`, `POST /api/wifi/connect` (cere parola+cookie, ca setările invertorului).
- Lecție (2026-10-26): o rețea WiFi aleasă și-a pierdut internetul imediat după schimbare. De atunci: fără internet `offline_minutes` → `try_known_networks` încearcă pe rând rețelele salvate din zonă
  (cea curentă ultima); cea cu internet devine prioritate 20, restul 10; hotspotul pornește doar dacă niciuna nu merge.
- Utilizatorul NU vrea conturi externe (healthchecks.io refuzat); `notify.heartbeat_url` rămâne gol.

## Prezență / pene (2026-10-26)
- După o pană de curent, routerul (4G) a pornit fără internet ~30 min; amper-wifi a pornit hotspotul și a revenit
  singur pe WiFi când a revenit internetul.
- `amper/presence.py` (pornit din `Engine.run`): „🔌 a pornit” (uptime < 5 min sau date lipsă > 5 min, cu intervalul
  din `db.last_sample_ts`), „🌐 a revenit online” (întrerupere > 2 min), semnal de viață GET la 60 s către
  `notify.heartbeat_url` (opțional; utilizatorul nu vrea conturi externe, deci gol).

## Actualizare automată a aplicației (2026-10-26)
`web.py` calculează `app_version` (amprenta fișierelor din web/, cache 30 s) și o pune în fiecare snapshot; `app.js`
(`checkVersion`) se reîncarcă singur când se schimbă — nu în timpul unei editări, cel mult o dată pe minut.
Ecranul principal ascunde „comm_lost” (doar notificare + Alerte) și arată doar pachetele care comunică (`packs()`).
Alertele deschise supraviețuiesc repornirii (coloana `alerts.key`, `AlertManager.restore`): înainte, fiecare
repornire le închidea și le retrimitea pe telefon — utilizatorul a primit aceleași alerte de mai multe ori pe 2026-10-26.

## Notificări în aplicație — Web Push (2026-10-25)
- `amper/webpush.py`: RFC 8291 (aes128gcm) + VAPID ES256, cu python3-cryptography; cheile în
  `/var/lib/amper/vapid.json` (și în backup); abonamentele în tabela `push_subs` (404/410 → șterse).
  API: `GET /api/push/key`, `POST /api/push/subscribe|unsubscribe`; `sw.js` afișează notificarea la `push`.
- Apple respinge VAPID cu `sub` inventat (`https://amper.local` → 403 BadJwtToken); `sub` = `web.public_url`
  (pus în config pe Pi) sau numele Tailscale al Pi-ului (`webpush.public_url`). Cu adresa reală, Apple acceptă.
- `Notifier._deliver` trimite pe fiecare canal (webpush, ntfy, telegram) și reîncearcă doar canalele eșuate.
- HTTPS: utilizatorul a activat „HTTPS Certificates” în Tailscale; pe Pi `tailscale serve --bg --https=443
  http://127.0.0.1:8080` → https://amper.<tailnet>.ts.net (config-ul serve stă în starea tailscaled).
  Cererile prin proxy au `X-Forwarded-For`/`Tailscale-User-Login` → `local()` le tratează ca externe (cer parola).
- ntfy (topic aleator `baterii-<hex>`, generat de install.sh) rămâne ca rezervă.

## Defecte și avertismente (2026-10-25)
- Growatt: după V0.17, registrul de intrare 40 = codul defectului (`FAULTS`), 41/42 = biții de avertisment
  (`WARNINGS`, cu flag dacă se notifică; 0x8000 „PV insuficient” doar informativ). Înainte se citeau greșit 42/43.
- Seplos: `ALARM_EVENTS` = custom[0..5] din 0x44, după documentația V2 (DE VERIFICAT), împărțite în
  `protections` (critic, regula `bms_protection`) și `alarms` (avertisment, `bms_alarm`).
- Daly: `split_faults` — „(nivel 1)” = avertisment, restul (nivel 2, hardware) = protecție.

## Daly pe hardware (2026-10-26) — instalația de referință, Pi pe WiFi
- Toate 3 găsite automat prin Bluetooth (protocolul vechi, adresa gazdei 0x80), nume `DL-<12 hex>`
  (legăturile în `/var/lib/amper/daly-auto.json`). Semnul curentului e corect
  (negativ la descărcare). Daly 2 NU e legat la bancă (utilizatorul nu are cablu) — dă SOC 0/0 cicluri/MOSFET 0 sau
  dispare din Bluetooth (probabil BMS adormit).
- Daly 1 răspundea la jumătate din citiri cu starea „goală” (SOC 0, rămas 0, 0 cicluri, MOSFET-uri 0) → alerte false.
  `DalyProtocol._check_glitch` respinge o astfel de citire dacă vine la <10 min după una bună.
- BLE: răspunsuri întârziate de la comanda anterioară → `exchange(..., done=)` așteaptă cadrele comenzii cerute.
  La oprirea serviciului modulele rămâneau „conectate” în BlueZ și nu se mai anunțau → SIGTERM face oprire curată
  (închide legăturile) și `_resolve` face `bluetoothctl disconnect` + caută iar.
- GRAV, reparat: citirile BLE blocate ocupau firele comune `asyncio.to_thread` → se oprea citirea tuturor
  dispozitivelor, inclusiv a invertorului. Acum fiecare dispozitiv are `ThreadPoolExecutor(max_workers=1)` propriu și
  nu pornește o citire nouă peste una în curs (`Engine.poll_device`).
- Seplos: adaptorul CH340 nu mai e legat la Pi după mutare.
- Parametri Daly citiți 2026-10-26 (`GET /api/daly/<id>/params`, comenzile 0x50–0x63, doar citire), interpretare:
  0x50 105 Ah, 3,2 V nominal; 0x51 16 celule; 0x59 celulă 3,65/3,60 V (sus) și 2,30/2,80 V (jos);
  0x5A pachet 58,4/58,0 V și 36,8/48,0 V; 0x5B curent ±180/±100 A (Daly 3: ±144/±100); 0x5C temperaturi
  încărcare 55/65 °C sus, -35/-40 °C jos (protecția la frig practic dezactivată!), descărcare 65/70 și -35/-40;
  0x5D SOC 100/102 % și 20/10 %; 0x5E 500 și 20 (mV?), 10/15 °C (neclar); 0x5F echilibrare de la 3,20 V, la
  diferență 50 mV; 0x60 scurtcircuit 1500 A (Daly 3: 1200 A); 0x53 somn după 3600 s; firmware 31_2306 / 31_2207,
  hardware „BMS-GD2…”. Nimic schimbat în BMS-uri.
- Setări BMS în aplicație (Bateriile → ⚙ pe fiecare Daly): `amper/daly_params.py` (`FIELDS`, limite LiFePO4 16S),
  `GET/POST /api/daly/<id>/settings` (scriere: parolă+cookie, jurnal `settings_log`, notificare). Scrierea = comanda
  de citire − 0x40 cu blocul întreg de 8 octeți, apoi citire înapoi și comparare — regula protocolului, NEVERIFICATĂ
  — utilizatorul a confirmat pe 2026-10-26 că scrierea FUNCȚIONEAZĂ pe BMS-ul real. Rolul
  avertisment/protecție al fiecărui prag se calculează din valorile perechii (ordinea diferă între blocuri).

## Setările invertorului (Growatt)
- Protocolul de referință: „OffGrid Modbus RS485&RS232 RTU Protocol V0.17” (în Google Drive-ul utilizatorului,
  folderul Amper/Growatt docs). Cere ≥850 ms între comenzi (`GrowattSPFDriver.bus_gap = 0.85`) și blocuri de cel mult
  45 de registre aliniate la 45 (`BLOCKS = (0,45),(45,45)`; înainte erau 2×40 la 50 ms).
- `growatt_spf.SETTINGS` = setările modificabile (cerute de utilizator: toate din tabelul lui + orele și ceasul), cu
  limitele din protocol; `inverter_settings.py` = descriere, scriere (funcția 06) cu citire înapoi, ceas (funcția 16).
  API: `GET/POST /api/inverter/settings`, `POST /api/inverter/clock`; scrierea cere parola și cookie chiar și de pe
  localhost; jurnal în tabela `settings_log`, notificare la fiecare schimbare.
- Valori citite pe 2026-10-25: UTI First, PV+UTI, intrare AC GEN, tip USE, 75 A / 35 A, bulk/float 54,5 V, pe rețea
  sub 46,0 V, înapoi peste 54,0 V, oprire 42,0 V, 230 V / 50 Hz, buzzer pornit. Ceasul invertorului e cu 1 h înainte
  (n-a trecut la ora de iarnă). **Nicio setare n-a fost scrisă în invertor de Claude** — decizia e a utilizatorului.

## Actualizări semnate din GitHub (0.2.0, 2026-10-27)
- `amper/update.py` = serviciul `amper-update` (root): API local 127.0.0.1:8092, token `/run/amper-update/token`
  (`GET /status`, `POST /check|install|rollback`); stare în `/var/lib/amper-update/state.json`. Caută singur la
  `update.check_days` (7) zile, notifică o dată pe versiune, NU instalează singur.
- Pe Pi: `/opt/amper` = legătură → `/opt/amper-<ver>` (`install.sh` o creează; o instalare veche, folder, e
  convertită). Se păstrează 3 versiuni în /opt și 3 copii în `/var/lib/amper/pre-update/<ver>/`
  (DB prin API-ul sqlite3 backup, /etc/amper, /var/lib/amper/*.json).
- Instalare: manifest + `.sig` (Ed25519, cheile din `amper/update_key.py`) → spațiu → arhivă (sha256) → stop amper →
  copia datelor → despachetare sigură → legătura → `deploy/post-update.sh` (unități systemd noi) → restart →
  `/api/health` (fără parolă) trebuie să raporteze versiunea nouă în 60 s, altfel revenire cod + date. După
  succes `amper-update` iese și systemd îl repornește cu codul nou. Revenirea la cerere păstrează datele
  (schimbările DB/config sunt DOAR adăugiri, deci versiunea veche le citește).
- Web: `/api/update/status|check`, `POST /api/update/install|rollback` cer parola reintrodusă în corpul cererii
  (merge și de pe HDMI; de pe telefon middleware-ul cere și cookie). `app_version` include `__version__`.
- Lansare: `tools/release.py` (keygen/build/verify/notes) + `.github/workflows/release.yml` (workflow_dispatch sau
  tag `v*`; versiunea din `amper/__init__.py`, notele din `CHANGELOG.md`; `-beta` → pre-release).
  `tests.yml` rulează testele la push/PR. Cheia publică e în `amper/update_key.py` (2026-10-27); privata e doar
  secretul repo-ului `AMPER_SIGNING_KEY` (pus de utilizator) și copia lui personală.

## Lucru pe Pi
- Datele de acces la Pi-ul autorului (utilizator, IP-uri, Tailscale): în `LOCAL.md`.
- Serviciu: `systemctl status amper`, jurnal `journalctl -u amper -f`, config `/etc/amper/config.yaml`.
- Capacitate Seplos (instalația de referință): 85 Ah în config; un pachet are în BMS capacitatea nominală 100 Ah
  (`rated_capacity_ah`), deci SOC-ul lui calculat de BMS e pe 100 Ah. Driverul nu are comandă de scriere a
  parametrilor Seplos (CID2 0x47/0x49 neimplementate/neverificate), așa că nu s-a scris nimic în BMS.
- Parola aplicației e în `/etc/amper/config.yaml` (`web.password`); nu o trece în fișierele proiectului.
- Înainte de `amper scan` / `amper read <id>`, oprește serviciul (portul serial e exclusiv).
- Actualizare cod (până la actualizările din GitHub): copiezi folderul `amper` pe Pi, apoi
  `sudo bash ~/amper/deploy/install.sh` (păstrează config-ul și baza de date; autoconfig doar pentru porturile REPLACE).
- Copia în Google Drive (`deploy/backup.sh`) e opțională: timerul pornește doar dacă există `/etc/amper/rclone.conf`;
  folderul se schimbă cu `AMPER_BACKUP_REMOTE` în `/etc/amper/backup.env` (implicit `gdrive:back-up`).
- Teste: `python -m pytest`; demo: `python -m amper simulate --hours 48`.
- Preferință utilizator: nu face modificări nesolicitate. Propune-le și aplică doar ce cere.

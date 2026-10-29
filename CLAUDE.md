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
  Notele din manual (porturi, pini, DIP, 9600 bps, ipoteza PACE/Pylon): `docs/powercube-p15a.md`; driverul: `pace.py`.

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
  Parola (`web.password` sau `web.password_hash`, vezi `auth.py`) protejează `/api/*` cu un cookie; cererile de pe
  127.0.0.1/::1 (kiosk-ul) sunt scutite de cookie, dar scrierile cer parola din nou.
- Teme (`app.js`, `LOOKS`: `cadrane`, `flux`, `carduri`), toate întunecate (tema luminoasă a fost scoasă): fiecare
  are `html()` (schelet, desenat o dată) și `update(root, homeData())`. Alegerea e în localStorage (`look`), din
  Setări (rotița); `?tema=<nume>` în adresă o impune (pentru kiosk). Bateria deschide ecranul `view-packs`.
- Dispozitive „conectate”: `DeviceState.connected` = a răspuns în ultimele `connected_days` (5) zile, la pornire
  după DB (`Database.last_seen`), apoi după `seen_at`. Cele neconectate nu se afișează (`app.js`, `connected()`) și
  nu dau `comm_lost`. `Engine.expire_devices` (la fiecare tick): după 5 zile fără răspuns → deconectat automat
  (`alerts.drop_device`, o notificare), rămâne în config și revine singur dacă răspunde (cerut 2026-10-28, după
  alertele Seplos rămase din 26.10 cu adaptorul scos). `check_clock` mută și `seen_at`.
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
- 2026-10-28 (1.0.0-beta.7): la 0x5F octeții 4..7 se schimbă singuri între citiri (Daly 1: `…00014b32` →
  `…700c7232`); `write_field` compară acum doar octeții câmpului (restul → doar avertisment în jurnal). Scrierea
  bal_start 3,40 V / bal_diff 20 mV s-a aplicat pe 3 Daly (firmware 31_2306, 31_2207, 11_2506).

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

## Cum lucrăm (stabilit cu utilizatorul 2026-10-27)
- Utilizatorul cere modificările; Claude le face, le verifică (teste + simulator/browser), face PR-ul, îl include
  (merge) când testele trec și **lansează singur** versiunea: întâi **beta** (`x.y.z-beta.N`, pre-release), pe care
  utilizatorul o testează pe Pi-ul lui (canalul beta, Setări → Actualizări); când spune că merge, aceeași versiune se
  lansează ca stabilă (`x.y.z`) — abia atunci o primesc cunoscuții. Lansare: workflow-ul `release.yml`
  (`actions_run_trigger`, `run_workflow`, ref `main`); verificare: `Updater.check()` real pe GitHub.
- Utilizatorul nu lucrează cu git/PR; explică-i pe scurt, în română, ce s-a schimbat.
- Lansare urgentă (`release.yml` cu input `urgent: true` → `release.py build --urgent` → `"urgent": true` în
  manifestul semnat): Pi-urile o instalează SINGURE. Doar pentru reparații care nu pot aștepta, doar stabile testate
  pe beta, și tot doar după „publică”. Dacă după o urgentă vine repede altă versiune, marcheaz-o și pe ea urgentă
  (un Pi offline la momentul urgentei vede doar ultima versiune).
- Numerotare (stabilit 2026-10-27): după 0.3.0-beta.10 urmează 1.0.0-beta.N; **1.0.0** stabilă (prima pentru
  cunoscuți, cu imaginea SD) abia după testul complet al imaginii pe un card + Pi de rezervă. 0.3.0 stabilă nu se mai lansează.
- **NU publica (merge + release) până nu spune explicit „publică”** (cerut 2026-10-27, după ce beta.5 a ieșit prea
  devreme). Lucrul se adună pe ramură; CHANGELOG are secțiunea „— în pregătire” până la publicare.

## Ghid de configurare + securitate (0.3.0, 2026-10-27)
- Setările din aplicație stau în `/var/lib/amper/settings.yaml` (scris de serviciul amper, 600), aplicat peste
  `/etc/amper/config.yaml` (`config.overlay_path/read_overlay/save_overlay`; listele, ex. `devices`, se înlocuiesc).
  Chei: `site_name`, `setup_done`, `web.password_hash`, `devices`. Intră în copia pre-update și în backup.sh.
- `config.setup_needed`: ghidul apare doar fără parolă și fără `setup_done` (instalările existente nu-l văd).
  `amper simulate --ghid` îl arată în demo. API `/api/setup*` (nume, parolă, căutare în fundal cu
  `setup_scan.scan`, `devices` → `build_devices`, `finish` → repornire prin SIGTERM dacă s-au schimbat dispozitivele).
- `amper/auth.py`: scrypt; cookie = HMAC(`cookie.key`, amprenta parolei), `HttpOnly`, `SameSite=Strict`, `Secure`
  pe https; blocare 5 greșeli/IP (30 s, dublu, max 1 h) + global 20/15 min; tabelele `login_log`, `login_devices`;
  notificări la blocare, dispozitiv nou, parolă schimbată. IP-ul real prin proxy = primul `X-Forwarded-For`.
- Scrierile (BMS, invertor, WiFi, actualizare) cer `app_password` în corp (`web.confirm`); în pagină `securePost`
  + dialogul `#pwask`, parola ținută 5 min în memorie. Antete: CSP, X-Frame-Options, nosniff, Referrer-Policy.
- Roluri admin/vizualizare: NEFĂCUTE (opționale în brief).
- `web.local()` (scutirea kiosk-ului de parolă) e falsă la orice antet de proxy, inclusiv `Tailscale-Funnel-Request`
  și `X-Forwarded-*` — condiție pentru Funnel (adresa publică, telefoane fără Tailscale).

## Actualizare în doi pași + acces de oriunde (0.3.0-beta.4, 2026-10-27)
- `Updater.download()` → arhiva verificată în `state_dir/staged` (+ `state.staged`); `install()` o reverifică.
  Progres: `_fetch(progress=)`, `_check_sha`; job `{step, progress}`. UI: „Descarcă și verifică” → bară → „Instalează
  acum”; în timpul instalării, ecranul `#upov` (cerc), reîncărcare când `/api/health` dă versiunea nouă.
- Ecranul negru după actualizare (raportat de utilizator): cât Amper repornește, `tailscale serve` dă 502 cu
  pagină goală; `sw.js` o afișa și o salva în cache. Acum răspunsurile !ok nu se afișează/salvează (v16).
- `amper/remote.py` (în amper-update, root; API `/remote`, `/remote/login`, `/remote/funnel`): `tailscale up
  --hostname=amper` și `tailscale funnel --bg --https=443 http://127.0.0.1:<port>`, rulate în fundal; linkurile
  de logare/aprobare (orice `https://login.tailscale.com/...`) apar în aplicație. Oprit = `funnel reset` + `serve`.
  Web: `/api/remote*` (funnel cere `app_password`), actualizează `webpush.subject` cu adresa Tailscale.
  install.sh instalează mereu pachetul Tailscale (fără logare). NEVERIFICAT pe un cont Tailscale nou.

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

## Imaginea SD (3.3, 0.3.0-beta.8, 2026-10-27)
- `tools/build-image.sh` (root): Raspberry Pi OS Lite 64-bit oficial → chroot (nativ pe arm64, altfel qemu-user-static)
  → Tailscale din depozitul oficial → `deploy/install.sh --image` (nu pornește nimic, fără autoconfig, `devices: []`,
  topicul rămâne REPLACE-TOPIC, marcaj `/etc/amper/.firstboot`) → hostname `amper`, SSH oprit, `userconfig` mascat,
  `pi`/root blocați, țara WiFi (`COUNTRY`, implicit RO) în cmdline.txt → curățenie (machine-id gol, fără chei SSH,
  /var/lib/amper și /var/lib/tailscale goale) → verificări → `resize2fs -M` + sfdisk (PARTUUID păstrat) → `.img.xz`.
  Ales chroot, nu pi-gen: rulează exact install.sh, durează minute.
- Baza (2026-10): Raspberry Pi OS 13 Trixie, cu cloud-init: `/boot/firmware/user-data` e înlocuit cu `users: []`
  (fără cont de logare). Imaginea comprimată ≈ 640 MB. cmdline.txt are `resize` (extindere la prima pornire).
- README.md = ghidul simplu pentru cunoscuți (imaginea); instalarea manuală și detaliile în `docs/instalare-avansata.md`.
- `deploy/firstboot.sh` + `amper-firstboot.service` (înaintea amper/amper-wifi/amper-update): topicul ntfy, cheile SSH,
  machine-id. Cheile VAPID, cookie.key și DB le face amper la pornire.
- `.github/workflows/image.yml`: pe `ubuntu-24.04-arm`; la PR doar construiește + verifică (Artifacts 3 zile);
  „Run workflow” o adaugă la versiunea publicată v<ver>. NEVERIFICAT: pornirea imaginii pe un Pi real.

## Parola uitată (0.3.0-beta.8, 2026-10-27)
- `amper/password_reset.py` + `amper-password-reset.service` (root, la fiecare pornire, înaintea amper): fișier
  `resetare-parola*` (orice extensie/majuscule) în /boot/firmware → overlay `web.password=""`, `password_hash=""`,
  `password_reset=<ts>` (settings.yaml dat înapoi lui amper), fișierul șters. `post-update.sh` instalează și activează
  unitatea pe Pi-urile existente.
- `web.py`: `reset_pending()` → `/api/info.password_reset`, notificare la pornire; cât e în așteptare, cererile prin
  proxy (Tailscale serve/Funnel) la /api/* primesc 403 `reset_local`. Parola nouă (`/api/setup/password`) șterge
  `password_reset`. `app.js`: `openSetup(false, "password", true)` = doar ecranul „Parolă nouă”; `showResetLocal`.
- Utilizatorul NU vrea (deocamdată) butonul de feedback din 3.7.

## Siguranță la actualizări: paznic, revenire de pe card, urgente (0.3.0-beta.8, 2026-10-27)
- Motiv: utilizatorul a avut „ecran negru” (Pi online în Tailscale, Amper nu răspundea; cauza neaflată, fără SSH).
- `Updater.watchdog_tick` (fir în amper-update, la 60 s, după 120 s de la pornire): `systemctl is-active amper` +
  `/api/health`; „inactive” = oprit de mână → ignorat; 5 min fără răspuns → `systemctl restart amper` (o notificare
  pe incident; după 3 reporniri, la 30 min); încă 5 min și instalarea e din ultimele 7 zile cu versiune anterioară
  păstrată → `rollback()`, `state.bad_version`, notificare, amper-update se repornește.
- `card_rollback()` la pornirea amper-update: `revenire*`/`rollback*` în /boot/firmware → șters, `rollback()`.
  `password_reset.find_files(boot_dirs, names)` e comun.
- Urgente: `state.latest.urgent`; `due()` = la 6 ore (`URGENT_EVERY`); `urgent_pending()` (mai nouă, nu
  `bad_version`) → `install()` singur în `loop`. Notificare „⬆️ Actualizare urgentă…”.
- `app.js` `showDown()`: după 5 s fără /api/info, mesaj „Amper nu răspunde…” în locul ecranului negru.
- ATENȚIE: protecțiile ajută doar Pi-urile care le au instalate (de la beta.8). Pi-ul utilizatorului pe beta.7 nu le are.

## UI 2026-10-27 (seara, beta.8)
- Istoric: `histCache` (memorie + localStorage `hist:<ore>|<ids>`) → desenat instant, apoi `/api/history/all`;
  serverul întoarce `ms` și scrie în jurnal peste 1,5 s (durata NU se afișează: utilizatorul n-o vrea).
  Măsurat local (14 zile × 7 dispozitive, 185 MB): <0,1 s pe orice interval; fără medii 5 min, 7 zile ≈ 0,9 s.
  Cauza lentorii la utilizator: conexiunea 4G (trafic limitat), aflată de el printr-un test local. Nu schimba
  graficele până nu cere.
- Dispozitive: `renderDaly` = doar cele care comunică (`connected && !stale`); restul în panoul `devoff`.
- WiFi: panoul `wifi` se deschide din Info (`#info-wifi`, „înapoi” → info); scos din Conectare.
- `sysinfo`: peer-ii `funnel-ingress-node*` / `ShareeNode` nu se mai listează.
- Panouri: `InverterReading.pv_current` / `pv2_current` = registrele de intrare 7/8 (Buck1Curr/Buck2Curr, ×0,1 A),
  CITITE din invertor (utilizatorul nu vrea valori calculate). DE CONFIRMAT pe Pi cu afișajul invertorului. Butonul `PV_I` (`data-pvinfo`) în toate temele → `#pvpop` (`pvInfoHtml`, actualizat
  la fiecare citire, se deschide deasupra dacă nu are loc deasupra barei de jos).

## Identificarea BMS-urilor Bluetooth (0.3.0-beta.9, în pregătire)
- `BleLink.name/address` (aflate la `_resolve`); `DalyBleDriver.link_desc` = proprietate „Bluetooth <nume> · <MAC>”
  (MAC-ul doar dacă nu e deja în nume); `ble_name` vine din legătura salvată (`daly-auto.json`, `name`).
  `DalyAutoDriver.link_desc` = proprietate a legăturii interne. `raw.ble_name/ble_mac` → `bleId()` pe cardurile din
  Bateriile.

## Adaugă dispozitiv (0.3.0-beta.9, în pregătire)
- „+” din Dispozitive conectate → panoul `devadd` (nu mai e ghidul). `POST /api/devices/scan {what: cable|ble}` (job
  comun `dev_job`, `GET` pentru stare): `setup_scan.scan_cable` (doar porturi libere) / `scan_ble` (toate modulele cu
  nume, fără cele din config și din `daly-auto.json`; `guess` = daly după nume; `types` = `BLE_TYPES`).
  `POST /api/devices/add {device}` → `setup_scan.add_device` (id/nume unice, `ble_name`), overlay `devices` = cele
  reale + cel nou, notificare, repornire (nu în simulator). Doar Daly e suportat; JK/JBD/ANT = `supported: False`.

## Actualizări: ecran și notificare (0.3.0-beta.10, în pregătire)
- `Notifier.send(..., url=)` → `item.url` → Web Push `url` (implicit `/?v=alerts`); `amper notify --url`.
  `Updater._notify(msg, url="/?v=update")`, titlul „Amper”; versiune nouă = „Versiune nouă detectată. Atinge pentru a
  actualiza.” `app.js`: `?v=update` și mesajul SW deschid Setări → Actualizări.
- Ecran: beta sus, `#up-check` „Caută actualizări”, `.up-new-head` (titlu + „Descarcă”), bara/instalarea înaintea notelor.

## Deconectare BMS, module excluse, egalizatoare (1.0.0-beta.1, în pregătire)
- Pe Pi-ul utilizatorului, căutarea a legat un EGALIZATOR ACTIV (BLE „DL-B77B020306D3”) ca daly2: răspunde la 0x90 cu
  tensiuni reale, SOC 0, 0 cicluri, MOSFET 0. Utilizatorul a scos modulele BLE de pe egalizatoare și va deconecta daly2.
- `daly_auto.check_bms(drv)`: 0x90 (+0x50 capacitate, 0x93 Ah rămași, 0x94 celule/cicluri); BMS = 5–100 V, SOC 0–100
  și (capacitate 1–2000 Ah sau SOC>0 și Ah rămași>0). `_probe` pune valorile în `b["check"]`; `_note_found` →
  `recent_found()` (ultimele 20, legate sau nu); respinse → `_not_bms` (golit de `trigger()`).
- Excluse: `<state>/daly-excluded.json` = [{keys: ["ble:MAC","name:DL-…"|"uart:/dev/…"], label, reason, at}];
  `exclude/is_excluded/allow_again(index|None)`; sărite la căutarea automată, în ghid și la legarea din daly-auto.json.
  În backup.sh și în copia pre-update (*.json).
- `Engine.disconnect_device(id)`: `st.gen += 1` (citirea în curs ignorată), Daly auto → `DalyAutoDriver.unbind()`;
  Daly fix (ble:/port:) → config `port: auto` + driver auto nou; altele → task anulat, scos din `devices`/config.
  `AlertManager.drop_device` (închide în DB, fără „Rezolvat”), `settings_log` („conexiune”), notificare.
- API: `POST /api/devices/<id>/disconnect` (app_password), `GET /api/devices/excluded`, `POST .../excluded/allow`,
  `GET /api/daly/found` (+ pozițiile auto, `free`), `POST /api/daly/reject` (keys sau binding din ghid; app_password).
- UI: `#sh-disc` în fișa bateriei; `devoff`: „Caută din nou” (poziție liberă) / „Deconectează”, „Găsite la căutare”
  cu „Respinge”; panoul `excl`; ghid: valorile citite, nebifat dacă `ok:false`, „Respinge”.

## BMS JK (1.0.0-beta.1, în pregătire; NETESTAT pe un JK real)
- Cerut 2026-10-27: „configurare pentru majoritatea BMS-urilor” (JK vechi/noi, Daly vechi/noi; cablu și BT). Ordinea:
  JK (prietenii utilizatorului au JK cu modul pe cablu + Bluetooth), apoi PowerCube (BMS Daren, Pylontech RS485;
  prietenul la care e Pi-ul acum vrea unul), apoi Daly nou (Modbus). Pi-ul utilizatorului e acum la un prieten cu Daly.
- `amper/drivers/jk.py`, după documentația publică esphome-jk-bms (syssi, Apache-2.0; cadrele din `tests/test_jk.py`
  sunt capturi reale de acolo). BLE: 0xFFE0/0xFFE1 (`BleLink(target, service, notify, write)`, caracteristicile alese
  după proprietăți), cerere 20 o. `AA 55 90 EB cmd …`, 0x97 → cadru 0x03 (model/hw/sw) → `protocol_for(hw)`:
  ≥11 JK02_32S, 10 JK02_24S, <10 JK04 (refuzat, „încă nesuportat”); 0x96 → cadru 0x02 (300 o., sumă la 299) →
  `decode_jk02` (offset +16/+32 la 32S; curent s32/1000, + = încărcare; erori → `JK02_ERRORS` protecții/alarme).
- Cablu (modulul RS485/UART, „NW”, 115200): `UART_READ_ALL`, `uart_frame_ok` (sumă u16 la [n]), `parse_tlv`
  (`TLV_LEN`), curent 0x84 = bitul 15 încărcare, temperaturi >100 → negative. `__main__.probe` încearcă JK după Daly.
- Tip config `jk` (`ble:` sau `port:`); `BLE_TYPES` jk suportat; `scan_ble` ghicește „jk” după nume (`JK_NAME`).
- NEFĂCUTE: JK04, JK-PB pe RS485 Modbus (seria nouă „inverter BMS”), detectarea automată JK ca la Daly (`port: auto`).

## BMS PACE / PowerCube (1.0.0-beta.2, 2026-10-28; NETESTAT pe o baterie reală)
- Cerut 2026-10-28 („Da, începe”), după notele din manual (`docs/powercube-p15a.md`, PR #15).
- `amper/drivers/pace.py`, tip config `pace` (`port`, `address` 0–15 = DIP, `baudrate` 9600, `protocol` 25/20/auto).
  Cadrul e cel Seplos (`length_field`, `frame_checksum` din `seplos.py`) cu VER variabil. Formatul răspunsurilor după
  documentația publică esphome-pace-bms (nkinnan; repo fără licență → doar faptele protocolului, cod scris de noi);
  cadrele 0x25 din `tests/test_pace.py` sunt exemplele de acolo (BMS PACE real).
- `_analog`: fără `protocol` încearcă 0x25 apoi 0x20 și ține minte (`self.ver`). 0x42: flag, pachet, celule (mV),
  temperaturi (4 celule + MOSFET + mediu), curent /100, tensiune (mV la 0x25, 10 mV la Pylon → scara aleasă după
  suma celulelor), rămas /100, UD (3 = PACE, 2/4 = Pylon, 4 = capacități pe 3 octeți în mAh, 10 = Seplos →
  `seplos.decode_telemetry`), total /100, cicluri, proiectat /100 (doar 0x25). SOC = rămas/total (BMS-ul nu-l dă).
  0x44 la 0x25: avertismente pe celule/temperaturi/curent/tensiune, protecții 1/2, stare (bit1 MOSFET încărcare,
  bit2 descărcare, bit5/3 încarcă/descarcă), defecte, echilibrare u16, avertismente 1/2 (`PROTECT1/2`, `FAULTS`,
  `WARN1/2`); la Pylon stările 1–5 (`PYLON_S1`, MOSFET în starea 2, celule defecte în 4/5). 0xC1 = modelul plăcii.
- `__main__.probe`: după Growatt, PACE 0x25 la 9600 pe adresele 0–15 (`PROBE_WAIT` 0,8 s), apoi bucla Seplos (0x20,
  19200/9600) separă Seplos de Pylon cu `pace.is_seplos`. Ghid/„Adaugă dispozitiv”: `setup_scan` (NAMES „PowerCube”,
  `protocol` păstrat), `app.js` (DEV_TYPE „PowerCube / PACE (RS485)”).
- DE VERIFICAT la PowerCube: dacă răspunde la 0x25 sau 0x20 (programul lui e „BMS_Monitor”, nu PbmsTools), adresa
  (master 0 pentru toate modulele?), semnul curentului, ordinea temperaturilor, biții de stare. Doar citire.

## Ediții Lite / Pro (1.0.0-beta.6, 2026-10-28)
- Cerut 2026-10-28: AmperLite (supraveghere) și AmperPro (cu setări), Pro va fi cu bani (încă nimic stabilit: fără
  firmă; licența rămâne PolyForm Noncommercial până atunci). Un singur program, două moduri.
- Lite = tot, mai puțin: setările BMS Daly (⚙ în Bateriile), setările și ceasul invertorului, `/api/daly/*/params`,
  `/api/debug/*`. Ghidul, adăugarea/deconectarea dispozitivelor, alertele, WiFi, actualizările: în ambele.
- `amper/edition.py`: `init_base` (o dată, `edition_base` în settings.yaml): instalare existentă (parolă, `setup_done`
  sau citiri în DB) → pro, nouă → lite; simulatorul pro (`simulate --lite`). `pi_id()` = 8 caractere din sha256
  (`/etc/machine-id`), ex. `7K3F-Q9PA`. Cod = Ed25519 (cheia actualizărilor) peste `amper-pro:v1:<ID>`, base64url;
  `pro_code` în settings.yaml. `web.py`: middleware `pro_only` (403 `pro_required`), `/api/info.edition/pi_id`,
  `POST /api/pro/activate {code, app_password}`. `app.js`: `isPro()`, `renderEdition()`, `#ed-card`, `#iv-pro/#iv-lite`.
- Coduri: `.github/workflows/pro-code.yml` (Actions → „Cod Pro” → ID; codul în rezumatul rulării — public, dar
  merge doar pe acel Pi) sau `AMPER_SIGNING_KEY=… python tools/release.py pro <ID>`.
- Aplicația de coduri (cerută 2026-10-28): `site/coduri/index.html` → https://georgexy.github.io/amper/coduri/
  (noindex, fără link de pe site). Cheia privată se lipește o dată, se verifică cu `PUBLIC_KEYS` (copiate din
  `update_key.py` — țin-le sincron!) și se ține doar în localStorage; semnează cu WebCrypto Ed25519 (PKCS#8 din seed),
  același cod ca `edition.make_code` (verificat în test cu Chromium). Evidența codurilor date: localStorage.
- 1.0.0-beta.7: „Versiunea” în `#ed-card`, mutat sub meniul Despre, scris 14 px; scoase `#version` și
  `#about-version` (cu „citire la fiecare 2 s”). `site/coduri/` are manifest + iconițe (se pune pe ecranul principal;
  pe iPhone aplicația de pe ecran are alt localStorage decât Safari → cheia se lipește din nou acolo).
- 1.0.0-beta.7: bara de jos = model „J” ales de utilizator (2026-10-28): `nav.tabs` Istoric | `.nav-now` | Alerte;
  `.nav-now` = inel SVG (r 34, `#nav-arc` dasharray = SOC bancă) + `#nav-soc`; `navState(homeData())` la fiecare
  randare: `data-st` charging (verde, fulger mare) / discharging (albastru, căsuță) / idle (gri) / fault
  (`health.cls === "fault"`, roșu, „!”). Pe alte ecrane cercul e la 78% opacitate; tabul activ = pastilă colorată.
  `body` padding jos 104 px (cercul iese ~24 px peste bară).
- Site (2026-10-29): utilizatorul NU vrea nicio legătură spre GitHub pe site (scoase: ghidul README, licența, Întrebări,
  Codul sursă, suma de control, fallback-ul butonului). Butonul de descărcare e inactiv („vine în curând”) până când
  API-ul GitHub găsește `.img.xz` în ultima versiune stabilă; atunci duce direct la fișier (descărcare, nu pagină).
- 1.0.0-beta.8 (în pregătire): în aplicație NU apare cuvântul GitHub (mesajele `UpdateError` din `update.py` spun
  „server”/„serverul de actualizări”). Actualizări: buton „Istoric versiuni” → panoul `set-uphist` (`#up-prev` +
  `#up-hist`, înapoi → update).


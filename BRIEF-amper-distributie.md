# Brief: Amper pentru alții — imagine SD, prima pornire, actualizări din GitHub

> Document de pornire pentru o conversație nouă cu Claude Code. Lipește-l (sau dă-i calea) la începutul conversației.
> Scris pe 2026-10-26, pe baza proiectului `amper` existent (…\Documents\amper).

## 0. Reguli de lucru (de la utilizator)
- Vorbește cu mine (George, inginer mecanic) **în română**.
- Cere-mi confirmarea înainte de orice modificare în setările BMS-urilor sau ale invertorului și înainte de reporniri.
- Nu face modificări nesolicitate; dacă observi ceva, spune-mi și propune.
- **Fără conturi externe** pentru utilizatorii finali în afară de Google (pentru Tailscale). Eu am cont GitHub.
- Proiectul vechi `…\Documents\amper` rămâne neatins (rulează pe Pi-ul meu). Noul proiect e o **copie
  separată**, de ex. `…\Documents\amper-public`, cu git și repo pe GitHub.

## 1. Scopul
Vreau să dau Amper cunoscuților care au un Raspberry Pi nou. Ei primesc **doar un link** de unde descarcă imaginea
și se descurcă singuri:

1. Scriu imaginea pe card (Raspberry Pi Imager → „Use custom”, fără personalizare) și pornesc Pi-ul.
2. Pi-ul apare ca rețea wireless **„Amper-Setup”**; se conectează la ea cu telefonul → se deschide singură pagina
   de configurare (captive portal).
3. Aleg WiFi-ul de acasă și scriu parola → Pi-ul se conectează la internet.
4. Li se cere **logarea cu Google în Tailscale** (contul lor, nu al meu) → Pi-ul primește adresa
   `https://amper.<tailnet>.ts.net`, deschisă public prin **Tailscale Funnel** — telefoanele NU au nevoie de Tailscale.
5. Urmează **ghidul de configurare** în aplicație: nume instalație, parolă, căutarea dispozitivelor, capacități,
   notificări.
6. Feedback-ul lor vine la mine → modificăm local → public o versiune pe GitHub → ei apasă **„Actualizează”** în aplicație.

Fiecare instalație e independentă (Pi-ul lor, invertorul lor, datele lor, parola lor) — nu există server central,
deci nu se amestecă invertoarele și nu e nevoie de conturi de utilizator pe un server.

## 2. Punctul de plecare (ce există deja în `amper` și se refolosește)
Citește întâi `CLAUDE.md` și `README.md` din proiectul vechi — conțin lecțiile de pe hardware-ul real.

| Componentă | Fișier | Stare |
|---|---|---|
| Server, bucla de citire, alerte, DB | `amper/engine.py`, `alerts.py`, `db.py` | funcțional pe Pi |
| Drivere Daly (UART + BLE, auto-detectare), Seplos V2, Growatt SPF | `amper/drivers/` | verificate pe hardware |
| Setări BMS Daly și invertor Growatt din aplicație | `daly_params.py`, `inverter_settings.py` | scrierea Daly confirmată |
| PWA (vanilla JS, 3 teme, auto-reîncărcare la versiune nouă) | `web/` | funcțional |
| Web Push (VAPID, fără cont extern) + ntfy | `webpush.py`, `notify.py` | funcțional (Android + iPhone) |
| Hotspot „Amper-Setup” + captive portal + alegere WiFi + rollback | `wifi_setup.py`, `deploy/amper-wifi.service` | hotspot testat; conectarea telefonului NETESTATĂ |
| Scanare porturi → config automat | `amper/__main__.py` (`autoconfig`), `deploy/install.sh` | funcțional |
| Kiosk HDMI, backup Google Drive, extindere rădăcină | `deploy/` | funcțional (backup-ul e specific mie) |
| Teste | `tests/` (75 trec) | `python -m pytest` |

Atenție (lecții): pe Windows, Python scrie CRLF → scrie cu `newline="\n"` (a stricat `install.sh`); BLE blocant →
fiecare dispozitiv are executor propriu; Pi-ul n-are RTC (`Engine.check_clock`).

## 3. Ce e de făcut

### 3.1 Curățenie înainte de GitHub (OBLIGATORIU, primul pas)
Repo-ul poate fi public, deci **nimic personal** în el:
- Scoate din `CLAUDE.md`/`README.md`/config/teste: IP-uri, numele tailnet-ului, e-mailuri, topicul ntfy, parola,
  MAC-urile Daly, numele rețelelor WiFi, utilizatorul Linux al Pi-ului. Păstrează-le într-un fișier local ignorat de git
  (`LOCAL.md` în `.gitignore`).
- `config.example.yaml` generic; `backup.sh` cu Google Drive devine opțional (dezactivat implicit).
- `.gitignore`: `*.db`, `config.yaml`, `vapid.json`, chei, `LOCAL.md`, imagini `.img*`.

### 3.2 Actualizări din GitHub, semnate
- Versiune în `amper/__init__.py` (`__version__`, semver) + `CHANGELOG.md` în română (afișat în aplicație).
- Lansare: la cererea mea („fă versiunea 1.3”) se creează un **GitHub Release** cu arhiva `amper-<ver>.tar.gz` +
  `manifest.json` (versiune, sha256, note) **semnat Ed25519**. Recomandat: semnare automată din **GitHub Actions**
  (cheia privată ca secret al repo-ului, la tag `v*`), ca să pot lansa și fără laptop; alternativ `release.py` pe
  laptop cu cheia doar acolo. Cheia privată nu intră niciodată în repo.
- Cheia publică e inclusă în imagine/cod. Pi-ul instalează **numai** arhive cu semnătură validă.
- Pe Pi: `amper-update.service` (root) + timer **săptămânal** care doar **verifică** `releases/latest` (nu
  instalează singur); când găsește o versiune nouă, trimite o notificare și aplicația arată „Versiune nouă: X”.
- Instalare: descarcă → verifică semnătura și sha256 → **copie de siguranță a datelor** (vezi 3.2.2) →
  despachetează în `/opt/amper-<ver>` → schimbă legătura `/opt/amper` → repornește → dacă serviciul nu răspunde la
  `/api/health` în 60 s, **revine la versiunea veche** (cod + date din copia făcută înainte).
- Comunicarea serviciului web (utilizator `amper`) cu updater-ul (root): API local cu token, ca la `amper-wifi`
  (127.0.0.1, token în `/run/...`).
- Opțional: canal „beta” (eu primesc versiunea întâi).

#### 3.2.1 Butonul „Actualizări” din Setări (cerut explicit)
Un buton separat **Setări → Actualizări**, cu un ecran propriu care arată:
- versiunea instalată și data instalării; data ultimei căutări (automate sau manuale);
- butonul **„Caută actualizări acum”** — caută imediat pe GitHub, oricând vreau, pe lângă căutarea automată
  săptămânală; arată „Ai ultima versiune” sau „Versiune nouă: X” cu notele de schimbare (în română);
- butonul **„Actualizează”** (doar când există o versiune nouă; cere parola, ca setările), cu progres
  (descărcare → verificare → copie de siguranță → instalare → repornire) și rezultat final; aplicația de pe telefon
  se reîncarcă singură după aceea (mecanismul `app_version` există deja);
- butonul **„Revino la versiunea anterioară”** (dacă ultima versiune nu-mi place);
- istoricul actualizărilor (versiune, dată, reușit / revenit) și erorile clare în română (fără internet, semnătură
  invalidă, spațiu insuficient).

#### 3.2.2 Datele utilizatorului se păstrează (OBLIGATORIU)
Pe GitHub stă doar **codul** (aplicația „goală”). Datele fiecărei instalații stau pe Pi, separat de cod, și
actualizarea nu le atinge:

| Ce | Unde stă pe Pi | La actualizare |
|---|---|---|
| Programul (codul de pe GitHub) | `/opt/amper` | se înlocuiește |
| Config: dispozitive, parolă, capacități | `/etc/amper/config.yaml` | rămâne |
| Istoric, alerte, alertele mele, abonamente la notificări (SQLite) | `/var/lib/amper/` | rămâne |
| Legături Daly găsite (`daly-auto.json`), chei de notificări (`vapid.json`) | `/var/lib/amper/` | rămâne |
| Tailscale (adresa) | `/var/lib/tailscale` | rămâne |

Reguli:
- **Aceleași căi ca în Amper-ul actual** (`/etc/amper`, `/var/lib/amper`, utilizatorul de serviciu `amper`), ca
  Pi-ul meu să treacă pe versiunea nouă păstrând tot istoricul, fără mutări. Prevede și o comandă/procedură de
  trecere de la Amper-ul vechi la cel nou, testată pe o copie a bazei mele de date.
- Modificările bazei de date sunt **doar adăugiri** (tabele/coloane noi, cu valori implicite), niciodată ștergeri
  sau redenumiri; la fel pentru config: chei noi cu valori implicite, cele vechi încă acceptate.
- **Copie de siguranță automată înainte de fiecare actualizare**: `sqlite3 .backup` + `/etc/amper` +
  `/var/lib/amper/*.json` în `/var/lib/amper/pre-update/<ver>/` (se păstrează ultimele 3). La revenire se pun la loc.
- Teste: actualizare de la versiunea N la N+1 pe o bază de date cu date reale (copiată și anonimizată) → istoricul,
  alertele și config-ul sunt identice după actualizare și după revenire.

#### 3.2.3 Cum va funcționa dezvoltarea (pentru mine)
- Dezvoltarea se face într-o sesiune Claude Code **în cloud**, pe repo-ul GitHub: Claude scrie codul, rulează
  testele și simulatorul, face un **PR**; eu mă uit și dau Merge.
- Sesiunea din cloud **nu se conectează la niciun Pi**. Pi-urile își iau singure versiunile de pe GitHub
  (butonul de mai sus).
- Testele pe hardware (driver nou, de ex. Deye; depanare la cineva) se fac dintr-o sesiune **locală pe laptop**,
  prin Tailscale (la un cunoscut, doar dacă mi-a partajat Pi-ul).
- Pi-ul meu rămâne pe Amper-ul vechi până când versiunea nouă e gata și testată; apoi trece o singură dată pe ea
  (cu păstrarea datelor, vezi 3.2.2), iar de acolo actualizez și eu din buton.

### 3.3 Imaginea SD generică
- Script de construire (rulează pe Linux/WSL sau pe un Pi): pornește de la **Raspberry Pi OS Lite 64-bit oficial**
  (Pi 3B+/4/5), instalează pachetele și Amper ca `install.sh`, **fără** config personal, apoi micșorează și comprimă
  (`.img.xz`, ~1 GB, sub limita de 2 GB a GitHub Releases).
- Variante: `pi-gen` (oficial, reproductibil) sau chroot cu qemu-user-static peste imaginea oficială. Alege și
  justifică.
- **Nimic generat în imagine**: la prima pornire se creează machine-id, cheile SSH, cheile VAPID, cheia de cookie,
  baza de date, starea Tailscale. SSH **dezactivat** implicit (se poate porni din aplicație, cu parolă).
- Utilizatorul Linux e intern (nu-l vede nimeni); fără parolă cunoscută public.
- Partiția de rădăcină se extinde la prima pornire (există `amper-grow-root.service`).

### 3.4 Prima pornire: WiFi → Tailscale (de completat `wifi_setup.py`)
Problema: după ce Pi-ul trece pe WiFi-ul de acasă, hotspotul dispare și telefonul nu știe adresa Pi-ului
(`amper.local` nu merge pe toate telefoanele Android).
Propunere (de verificat pe hardware):
1. Pe hotspot, utilizatorul alege rețeaua + parola.
2. Pi-ul se conectează de probă, verifică internetul, află **IP-ul** și cere linkul de logare Tailscale
   (`tailscale up --hostname amper-<nume> --qr` / `tailscale login`).
3. Repornește hotspotul 3 minute și afișează: „Gata! Adresa ta acasă: 192.168.x.y”, plus pașii următori.
4. Telefonul revine pe WiFi-ul de acasă, deschide adresa → ghidul continuă cu „Conectează-te cu Google” (linkul
  Tailscale). Alternativă de evaluat: AP + client simultan pe Pi (interfață virtuală), dacă e fiabil.
- După logare: `tailscale serve`/`funnel` pe 443 → `http://127.0.0.1:8080`. Tailscale cere activarea HTTPS și
  Funnel în tailnet-ul lor (un link de aprobare) — ghidul trebuie să-l arate clar.
- Pasul Tailscale se poate **sări** (aplicația merge doar acasă); se poate face și mai târziu din Setări.
- Parola hotspotului: **de decis** (comună, de ex. `amper-snoopy`, scrisă în instrucțiuni, sau rețea deschisă).

### 3.5 Ghidul de configurare (prima deschidere a aplicației)
Pași: (1) numele instalației; (2) **parola aplicației**, aleasă de el (minim 8 caractere); (3) Tailscale (sau
„sari”); (4) **căutarea dispozitivelor** — rulează autoconfig/scan, arată ce a găsit (invertor, Daly cablu/BLE,
Seplos), confirmă; (5) capacitatea fiecărui modul; (6) activarea notificărilor + notificare de test.
- Până la terminarea ghidului, aplicația nu arată nimic altceva; ghidul se poate relua din Setări.
- Config-ul se scrie în `/etc/amper/config.yaml` printr-un API local (root) sau config-ul se mută într-un loc
  scris de utilizatorul `amper`.

### 3.6 Securitate (adresa devine publică prin Funnel)
- Blocare după 5 parole greșite (pe IP + global, cu întârziere crescătoare), jurnal al încercărilor.
- Notificare „🔐 Dispozitiv nou conectat” la prima logare a unui browser nou.
- Confirmare suplimentară (parola din nou) la scrierea setărilor BMS/invertor și la actualizare.
- Opțional: roluri — **administrator** (schimbă setări) și **vizualizare** (doar vede).
- Cookie `Secure`, `HttpOnly`, `SameSite=Strict`; antete de securitate; `local()` rămâne scutit doar pentru kiosk.

### 3.7 Ajutor de la distanță și feedback
- Instrucțiuni (în aplicație) ca proprietarul să **partajeze nodul Tailscale** cu contul meu (Machine sharing) —
  doar cu acordul lui, retras oricând. Pentru SSH: buton „Pornește accesul de asistență” (SSH pornit temporar).
- Opțional: buton **„Trimite feedback”** — fără conturi externe; de evaluat: deschide un formular GitHub Issue
  precompletat (cere cont GitHub) sau trimite prin ntfy către topicul meu.

### 3.8 Documentație pentru cunoscuți
O pagină simplă (README pe GitHub / GitHub Pages) în română: ce Pi trebuie, ce adaptoare (CH340 pentru RS485,
Exar/FTDI pentru Growatt, Bluetooth Daly), cum scrii imaginea, pașii de la prima pornire, ce faci dacă nu apare
„Amper-Setup”.

## 4. Ordinea propusă
1. Copia proiectului + curățenia (3.1) + git + repo GitHub.
2. Actualizările semnate (3.2) cu ecranul Setări → Actualizări (3.2.1) și păstrarea datelor (3.2.2), cu teste
   unitare (semnătură greșită → refuz; serviciu care nu pornește → revenire cu date; date identice după actualizare).
3. Ghidul de configurare (3.5) și securitatea (3.6) — se pot testa pe Windows cu `simulate`.
4. Fluxul WiFi → Tailscale (3.4).
5. Scriptul de imagine (3.3).
6. Test complet pe un **card SD de rezervă** (ideal și un Pi de rezervă): scriere imagine → hotspot → WiFi →
   Tailscale → ghid → actualizare de la o versiune la alta → revenire forțată.

## 5. De decis cu mine
- Numele contului GitHub și dacă repo-ul e **public** (recomandat; cel privat cere token pe fiecare Pi).
- Parola hotspotului (comună sau rețea deschisă).
- Numele proiectului public (Amper?) și licența.
- Dacă vreau roluri (admin/vizualizare) din prima versiune.
- Ce hardware de test am (card de rezervă, Pi de rezervă).

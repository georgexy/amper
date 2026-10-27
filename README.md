# Amper — monitorizarea bateriilor și a invertorului pe Raspberry Pi

Înlocuiește SolarAssistant. Pe Raspberry Pi citește:

- **fiecare modul Daly**, pe câte un cablu USB-UART;
- **bateria Seplos**, pe RS485;
- **invertorul Growatt SPF 5000 ES**, pe USB/RS232.

Pentru fiecare modul vezi pe telefon SOC-ul, curentul, celulele și starea MOSFET-urilor. Primești
**notificare pe telefon când un modul nu se mai încarcă**, chiar dacă restul băncii arată normal.

---

## 1. Ce îți trebuie

- Raspberry Pi 4 (cel de acum, de la SolarAssistant).
- Un card microSD nou, de 32 GB sau mai mult, de preferat „High Endurance”. **Cardul SolarAssistant nu se atinge.**
- Câte un cablu USB-UART pentru fiecare modul Daly, un adaptor USB-RS485 pentru Seplos și cablul invertorului pe care îl folosești acum la SA.
- Pe telefon: aplicația **ntfy** (Android/iPhone), pentru alerte. Opțional **Tailscale**, pentru acces de oriunde.

## 2. Pregătirea cardului (acasă, pe PC sau Mac)

1. Instalează **Raspberry Pi Imager** de pe raspberrypi.com/software.
2. Alege: *Raspberry Pi 4* → *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)** → cardul.
3. La „Edit settings” / „Customisation” completezi:
   - hostname: **amper**
   - un utilizator și o parolă, la alegere
   - Wi-Fi-ul **de la locație** (numele rețelei și parola)
   - fusul orar Europe/Bucharest
   - la „Services” bifezi **Enable SSH** (cu parolă)
4. Scrii cardul. După ce termină, scoți cardul și îl bagi la loc în PC. Apare o partiție numită **bootfs**.
5. **Copiezi tot folderul `amper`** (cel din arhivă) în rădăcina partiției `bootfs`.
6. Opțional: în `amper/` faci o copie a fișierului `config.example.yaml`, o numești `config.yaml` și treci
   capacitățile reale (`capacity_ah`) ale modulelor. Se poate face și mai târziu, de pe Pi.

## 3. La locație

1. Oprești Pi-ul, scoți cardul SolarAssistant (pune-i o etichetă), bagi cardul nou și pornești. Cablurile USB rămân toate în Pi.
2. După 2–3 minute te conectezi prin SSH:
   - de pe laptop, în aceeași rețea Wi-Fi: `ssh <utilizator>@amper.local`
   - sau de pe telefon, cu aplicația **Termius** (host `amper.local`)
3. Rulezi **o singură comandă**:

   ```
   sudo bash /boot/firmware/amper/deploy/install.sh --tailscale
   ```

   Fără `--tailscale` merge doar în rețeaua de la locație.
   Scriptul instalează tot, **caută singur pe ce port e fiecare dispozitiv**, pornește serviciul și la final îți afișează:
   - adresa aplicației
   - **topicul ntfy** pentru notificări
   - cu `--tailscale`, un link pe care îl deschizi ca să adaugi Pi-ul în contul tău Tailscale

## 4. Aplicația pe telefon

- Deschizi în browser **http://amper.local:8080**. Cu Tailscale, de oriunde: **http://amper:8080**
  (sau adresa 100.x.y.z a Pi-ului din Tailscale).
- **iPhone (Safari):** Partajare → *Adaugă pe ecranul principal*.
  **Android (Chrome):** meniul ⋮ → *Instalează aplicația*.
- De acum apare pe telefon ca o aplicație, „Amper”.
- **Parola** aplicației se pune în `config.yaml`, la `web: password:` (apoi `sudo systemctl restart amper`).
  Fiecare telefon sau laptop o cere o singură dată. Ecranul HDMI de pe Pi nu o cere.
- Se afișează doar dispozitivele care au răspuns măcar o dată (în ultimele 14 zile). Unul trecut în config dar
  încă nelegat nu apare și nu dă alerte. Apare singur după ce răspunde prima dată. Un modul care a mers și apoi
  nu mai răspunde rămâne pe ecran, cu roșu, și primești alerta „Nu mai comunică”.

| Ecran | Ce arată |
|---|---|
| **Acum** | O singură baterie cu SOC-ul total și kWh rămași (verde = se încarcă, albastru = se descarcă, **roșu = un pachet are o problemă**), puterea panourilor, consumul și bateria. Arată diferit după tema aleasă. |
| **Bateriile** | Se deschide când atingi bateria: câte un card pe pachet, cu SOC, curent, tensiune, temperatură, cicluri, MOSFET-uri și cele 16 celule. Atingi un pachet și vezi detaliile lui. |
| **Setări BMS** (⚙ pe fiecare Daly, în ecranul Bateriile) | Capacitatea, timpul de somn, pragurile de tensiune pe celulă și pe pachet, curenții, temperaturile la încărcare și descărcare, echilibrarea — citite din BMS și modificabile (limite sigure LiFePO4, confirmare, parolă, verificare prin citire înapoi, jurnal, notificare). |
| **Invertor** | Se deschide când atingi invertorul (sau rândul cu starea lui). Arată setările citite din invertor și permite modificarea lor (vezi mai jos). |
| **Setări → Info** | Procesor, temperatură, memorie, card, alimentare (subtensiune), WiFi-ul și puterea semnalului, adrese, Tailscale și dispozitivele tale, porturile USB și cine le folosește, legăturile Bluetooth. **Schimbă rețeaua WiFi**: caută rețelele din zonă și trece Pi-ul pe alta (cere parola aplicației); dacă rețeaua nouă nu merge, Pi-ul revine singur la cea veche. |
| **Setări** (rotița din colț) | Butoanele: **Teme** (Cadrane, Flux de energie sau Carduri, toate întunecate; alegerea se păstrează pe fiecare telefon), **Notificări** (pornirea notificărilor pe telefon și testul) și **Module Daly** (starea fiecărui modul și „Caută acum”). |
| **Istoric** | Pe lângă graficele de mai sus, energia produsă și consumată pe 7 zile, 30 zile, 1 an (pe luni) sau un interval ales „de la – până la”, cu totalurile perioadei. |
| **Istoric** | Grafice pe 6 ore, 24 ore, 7 sau 30 de zile: SOC și curent pe fiecare modul, puteri, energie pe zi. Ții degetul pe grafic ca să citești valorile. |
| **Alerte** | Alertele active și istoricul lor, plus butonul pentru o notificare de test. |

### Pe monitorul legat la Pi (HDMI)

Aplicația poate rula pe tot ecranul unui monitor legat la portul HDMI al Pi-ului (kiosk: Chromium în
compozitorul `cage`, pornit singur la fiecare alimentare). Pe Pi, după `install.sh`:

```
sudo bash ~/amper/deploy/kiosk.sh
```

Pe tty1 nu mai apare consola text; te conectezi în continuare prin SSH. Înapoi la consola text:
`sudo bash ~/amper/deploy/kiosk.sh --remove`.

### Setările invertorului

Din ecranul **Invertor** se pot schimba: prioritatea ieșirii, sursa de încărcare, modul intrării AC, orele pentru
rețea, tipul bateriei, curenții de încărcare, tensiunile (bulk, float, trecere pe rețea, revenire pe baterie,
oprire), tensiunea și frecvența ieșirii, buzzer-ul și ceasul invertorului.

- Valorile permise sunt cele din protocolul Growatt V0.17; altele nu pot fi trimise.
- Setările cu risc (marcate cu **!**) cer bifarea „Am înțeles riscul”. Fiecare scriere cere confirmare.
- După scriere, Amper citește setarea înapoi și verifică dacă invertorul a aplicat-o.
- Fiecare modificare apare în „Modificări făcute” și primești notificare pe telefon.
- Modificarea cere **întotdeauna parola**, și de pe ecranul HDMI. Fără parolă setată în config, modificarea e dezactivată.

## 5. Notificări

**Direct în aplicația Amper (fără altă aplicație):**
1. Pe iPhone, în Safari, deschide **https://amper.<tailnet>.ts.net** (adresa Pi-ului din Tailscale) (Tailscale trebuie să fie pornit pe telefon)
   și intră cu parola aplicației.
2. Partajare → **Adaugă pe ecranul principal**. Deschide Amper **de pe ecranul principal**, nu din Safari.
3. Alerte → **Pornește notificările pe acest telefon** → **Permite**.
4. **Trimite o notificare de test**.

Adresa https e făcută de Tailscale (`tailscale serve`), cu certificat reînnoit automat. Fiecare telefon se abonează
separat. Pe lângă asta, alertele se trimit și prin ntfy (rezervă), ca mai jos.

**Prin aplicația ntfy (rezervă):**

1. Instalezi aplicația **ntfy** pe telefon, apeși **+** și adaugi topicul afișat la instalare (`baterii-xxxxxxxxxxxx`).
2. În aplicația Amper → Alerte → **Trimite o notificare de test**.

Ce alerte primești:

| Alertă | Când |
|---|---|
| **Modulul X NU se încarcă** | banca se încarcă, dar modulul are curent ≈ 0 timp de 10 minute (și nu e plin) |
| **BMS-ul a OPRIT încărcarea / descărcarea** | MOSFET-ul de încărcare sau de descărcare e oprit |
| **Nu mai comunică** | nicio citire de la un dispozitiv timp de 2 minute |
| SOC scăzut / critic | sub 25% / sub 12% |
| Celulă prea sus / prea jos, dezechilibru | peste 3,60 V / sub 2,90 V / diferență peste 150 mV |
| Temperatură | peste 50 °C sau încărcare sub 1 °C |
| SOC divergent | un modul diferă cu peste 15% față de celelalte timp de 30 de minute |
| **PROTECȚIE BMS** (critic) | BMS-ul raportează o protecție sau un defect: supracurent, scurtcircuit, supra/subtensiune, temperatură (Seplos: „protection”; Daly: nivel 2 și defectele de hardware) |
| Avertisment BMS | BMS-ul raportează o alarmă de nivel 1 (tensiune, temperatură, curent aproape de limită) |
| **DEFECT invertor** (critic) | codul de defect al invertorului, cu textul lui (ex. „07 — Suprasarcină”) |
| Avertisment invertor | avertismentele invertorului (baterie scăzută, suprasarcină, ventilator, BMS com error etc.); „Putere PV insuficientă” doar se afișează, nu se trimite |

Când problema dispare, primești și mesajul „✅ Rezolvat”.

Despre Pi însuși:
- **🔌 Amper a pornit**: după o repornire sau o pană de curent, cu intervalul în care n-au fost date.
- **🌐 Amper a revenit online**: când revine internetul după o întrerupere de peste 2 minute, cu intervalul.
- **Pi-ul a căzut de tot** (fără curent sau fără internet): Pi-ul nu se poate anunța singur. Pentru asta, în
  `config.yaml`, la `notify:`, se pune `heartbeat_url:` cu adresa unei verificări de la healthchecks.io. Pi-ul trimite
  un semnal la fiecare minut, iar healthchecks.io te anunță (ntfy, email) când semnalele se opresc. Pragurile se schimbă în `config.yaml`, la `alerts:`.

## 6. Comenzi utile (pe Pi)

```
journalctl -u amper -f                    # ce face programul, în timp real
sudo systemctl restart amper              # după ce modifici config-ul
sudo nano /etc/amper/config.yaml          # configurarea
sudo systemctl restart amper-kiosk        # reîncarcă ecranul HDMI (jurnal: journalctl -u amper-kiosk)

sudo systemctl stop amper                 # (portul trebuie eliberat pentru comenzile de mai jos)
amper scan                                # ce dispozitiv e pe fiecare port USB
amper read daly1                          # citește o dată un modul și afișează tot
sudo amper autoconfig -c /etc/amper/config.yaml
sudo systemctl start amper
```

Mod demonstrativ, cu date simulate: `sudo bash /boot/firmware/amper/deploy/install.sh --demo`.
Revii la normal rulând din nou comanda fără `--demo`.

## 7. Modulele Daly: cablu sau Bluetooth

Modulele Daly se leagă **singure**. Le aduci la locație, le alimentezi și fie bagi cablul USB-UART în Pi, fie lași
modulul Bluetooth al BMS-ului pornit. Amper le găsește și le afișează la prima citire. Primești notificarea
„🔌 Daly 1: conectat (Bluetooth ...)” sau „(cablu ...)”.

- Căutarea se face la 30 s în primele 10 minute după pornire, apoi la 3 minute. Un cablu USB nou se încearcă imediat.
  În aplicație: rotița → **Module Daly** → **Caută acum**.
- Bluetooth: se caută modulele cu nume Daly („DL-…”), în ordinea semnalului. Cât timp Pi-ul e conectat la un modul,
  aplicația Daly de pe telefon nu se poate conecta la el.
- Prima legătură găsită se ține minte (`/var/lib/amper/daly-auto.json`), deci Daly 1/2/3 rămân aceleași după repornire.
  Dacă vrei altă ordine, șterge fișierul și repornește serviciul.
- Poți fixa și manual legătura, în config: `port: /dev/serial/by-id/...` (cablu) sau `ble: AA:BB:CC:DD:EE:FF`
  (Bluetooth). `amper scan` arată și modulele Bluetooth din jur.

## 7b. Dacă un dispozitiv nu e găsit

- **Daly:** BMS-ul poate „adormi” când nu trece curent prin el. Pornește un consumator sau încărcarea și rulează din nou `amper scan`.
  Dacă la Daly ai modul Bluetooth pe același port UART, cablul către Pi nu primește răspuns. Ori scoți Bluetooth-ul, ori folosești un splitter.
- **Seplos:** verifică pe care port e legat (pe RS485, nu pe CAN), baudrate-ul (19200 sau 9600) și adresa din DIP switch.
- **Growatt:** folosește același cablu cu care mergea SolarAssistant.
- Vezi și `ls -l /dev/serial/by-id/`. Fiecare cablu trebuie să apară acolo.

## 8. Ce trebuie verificat la prima pornire

Codul e testat pe date simulate și pe cadre de protocol. Câteva detalii diferă însă de la un firmware la altul, așa că verifică pe hardware:

1. **Semnul curentului la Daly:** când banca se încarcă, curentul modulelor trebuie să fie **pozitiv**. Dacă e invers, adaugi `invert_current: true` la modulul respectiv.
2. **Puterea bateriei la Growatt:** „Baterie, intră” trebuie să apară când se încarcă. Dacă e invers, adaugi `battery_power_sign: 1` la invertor.
3. **Seplos, starea MOSFET-urilor:** compară în fișa modulului „MOSFET încărcare” cu afișajul bateriei.
4. Puterea PV și consumul trebuie să se potrivească cu afișajul invertorului.

Dacă ceva nu se potrivește, deschizi proiectul în Claude Code pe un calculator din aceeași rețea cu Pi-ul și îi ceri să verifice. Fișierul `CLAUDE.md` îi dă tot contextul.

## 9. Actualizarea programului

De pe laptop, din folderul în care ai dezarhivat versiunea nouă:
```
scp -r amper <utilizator>@<IP-ul Pi-ului>:~/
```
apoi pe Pi:
```
sudo bash ~/amper/deploy/install.sh
```
Configurarea și istoricul se păstrează.

## 9a. WiFi: conectarea de pe telefon (fără cablu, fără monitor)

Când Pi-ul **nu are internet** 2 minute (nici prin cablu, nici prin WiFi), pornește singur o rețea WiFi
**„Amper-Setup”**. Parola ei e în config, la `wifi_setup: password:`, și e afișată la finalul instalării.

1. Pe telefon: Setări → WiFi → **Amper-Setup** → parola.
2. Se deschide singură pagina Amper. Dacă nu se deschide, intri în browser la **http://10.42.0.1**.
3. Alegi rețeaua WiFi din listă (sau scrii numele unei rețele ascunse), scrii parola ei și apeși **Conectează**.
4. „Amper-Setup” dispare, iar Pi-ul se conectează la rețeaua aleasă. De acolo îl găsești prin Tailscale ca de obicei.
   Dacă parola a fost greșită, „Amper-Setup” reapare în ~1 minut, iar pagina spune ce s-a întâmplat.

Cât timp Pi-ul are internet, rețeaua „Amper-Setup” nu există. Bluetooth-ul nu e folosit, deci rămâne pentru
modulele Daly. Dacă nimeni nu se conectează la hotspot, la 5 minute Pi-ul încearcă din nou rețelele știute
(de exemplu după o pană a routerului). Ecranul HDMI pornește singur când e legat un monitor.

## 9b. Copia de siguranță (Google Drive, opțională) și restaurarea

Dacă există `/etc/amper/rclone.conf` (remote-ul rclone `gdrive`), Pi-ul face copii în Google Drive, în folderul
**back-up**, la 00:00 (altfel copia e dezactivată). Alt folder: `AMPER_BACKUP_REMOTE=gdrive:<folder>` în
`/etc/amper/backup.env`.

| Folder | Ce conține | Când | Câte se păstrează |
|---|---|---|---|
| `back-up/back-up` | `amper-date-AAAA-LL-ZZ.tar.gz`: config, istoric, legăturile Daly, Tailscale | în fiecare noapte | ultimele 14 |
| `back-up/clona` | `amper-clona-AAAA-LL-ZZ.img.gz`: imaginea întregului card | o dată pe săptămână, în noaptea de duminică spre luni | doar cea mai nouă: după ce clona nouă e urcată, cea veche se șterge **definitiv** (fără Coș) |

Dacă o copie nu reușește, primești notificare pe telefon. Jurnalul: `journalctl -u amper-backup`.
Manual: `sudo bash /opt/amper/deploy/backup.sh` (sau `... date` / `... clona`).

**Dacă se strică cardul:** descarci ultima clonă din Drive, o scrii pe un card nou (de cel puțin 16 GB) cu
Raspberry Pi Imager („Choose OS” → „Use custom” → fișierul `.img.gz`), bagi cardul și pornești. Totul e ca înainte:
aplicația, ecranul HDMI, Tailscale, istoricul până în noaptea copiei. Partiția se extinde singură pe tot cardul la
prima pornire. Nu porni în același timp cardul vechi și clona.

**Doar datele, pe un sistem nou:** instalezi Raspberry Pi OS, copiezi folderul `amper` și rulezi
`sudo bash ~/amper/deploy/install.sh --restore amper-date-AAAA-LL-ZZ.tar.gz`.

Legătura cu Drive-ul e în `/etc/amper/rclone.conf`. Pi-ul vede în Drive doar fișierele create de el.

## 10. Înapoi la SolarAssistant

Oprești Pi-ul, pui cardul SolarAssistant și pornești. Totul revine cum era.

---

### Pentru dezvoltare (pe orice calculator)

```
pip install aiohttp pyserial pyyaml pytest
python -m amper simulate --hours 48      # aplicația cu date simulate la http://localhost:8080
python -m pytest
```

---

### Licență

Amper este distribuit sub **PolyForm Noncommercial 1.0.0** (textul complet în `LICENSE`, în engleză). Pe scurt:

- îl poți folosi, modifica și da mai departe **gratuit**, pentru uz personal (casa ta, instalația ta, hobby),
  precum și în școli, organizații caritabile și instituții publice;
- **nu** ai voie să-l folosești pentru a câștiga bani (de exemplu să-l vinzi sau să-l incluzi într-o instalație
  vândută clienților) fără acordul autorului;
- cine îl dă mai departe trebuie să păstreze licența și rândul „Required Notice”;
- se oferă **fără nicio garanție**: setările scrise în BMS sau în invertor sunt pe răspunderea ta.

Fonturile Barlow din `web/fonts/` au licența lor, SIL Open Font License 1.1 (`web/fonts/OFL.txt`).

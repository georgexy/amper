# Amper — bateriile și invertorul tău, pe telefon

Amper rulează pe un **Raspberry Pi** legat la bateriile și la invertorul tău. Pe telefon vezi în timp real
SOC-ul, fiecare modul de baterie, celulele, panourile și consumul, plus istoricul. Primești **notificare pe
telefon** când ceva nu e în regulă, de exemplu când un BMS oprește încărcarea fără să semnaleze.

Fiecare instalație e a ta: Pi-ul tău, datele tale, parola ta. Nu există un server central și nu îți faci cont
nicăieri, în afară de logarea cu Google pentru accesul de oriunde (opțional).

---

## 1. Ce îți trebuie

| | |
|---|---|
| **Raspberry Pi** | Pi 4 sau Pi 5 (merge și Pi 3B+). Cu alimentatorul lui oficial: unul slab dă erori ciudate. |
| **Card microSD** | 32 GB sau mai mare, de preferat „High Endurance” (scrie zilnic pe el). |
| **Bateriile** | **Daly**: prin Bluetooth (modulul BMS-ului, numele „DL-…”), fără cabluri; sau câte un cablu USB-UART Daly pe modul. <br> **Seplos V2**: un adaptor **USB-RS485** (cu cip CH340, ~30 lei), pe portul RS485 al bateriei. |
| **Invertorul** | **Growatt SPF** (ex. SPF 5000 ES): un cablu USB obișnuit (A–B) în portul USB al invertorului, sau un adaptor USB-RS232 (FTDI) pe portul RS232. |
| **Telefon** | Orice telefon cu browser (iPhone, Android). |
| **Internet** | WiFi acasă/la locație. Pi-ul poate sta și pe cablu de rețea. |

Alte BMS-uri sau invertoare: deocamdată nu. Spune-mi ce ai și vedem.

## 2. Scrii cardul (pe calculator)

1. Descarcă imaginea: **[Releases](https://github.com/georgexy/amper/releases/latest)** → fișierul
   `amper-<versiune>.img.xz` (~0,6 GB). Nu trebuie dezarhivat.
2. Instalează **Raspberry Pi Imager** de pe [raspberrypi.com/software](https://www.raspberrypi.com/software/).
3. În Imager: *Choose Device* → Pi-ul tău; *Choose OS* → la final, **Use custom** → fișierul descărcat;
   *Choose Storage* → cardul.
4. Când te întreabă de personalizare („Would you like to apply OS customisation settings?”): **No**.
   Amper își face singur setările.
5. Scrie cardul, bagă-l în Pi, leagă adaptoarele (USB) și pornește Pi-ul.

## 3. Prima pornire: WiFi-ul (de pe telefon)

1. Așteaptă **3–4 minute**. Pi-ul pornește o rețea WiFi numită **„Amper-Setup 10.42.0.1”**.
2. Pe telefon: Setări → WiFi → **Amper-Setup…**, parola **`amper-setup`**.
3. Se deschide singură pagina Amper (dacă nu: deschide în browser **http://10.42.0.1**).
4. Alege WiFi-ul tău din listă, scrie parola lui și apasă **Conectează**.
5. Pi-ul se conectează la WiFi-ul tău. Timp de 3 minute apare în lista de rețele ca
   **„Amper gata 192.168.x.y”**: acela e numărul Pi-ului acasă. Nu te conecta la ea, doar notează numărul.
6. Telefonul revine singur pe WiFi-ul tău. Deschide în browser **http://192.168.x.y:8080** (numărul de mai sus).

Dacă parola WiFi a fost greșită, după ~1 minut reapare „Amper-Setup” și pagina îți spune ce s-a întâmplat.

## 4. Ghidul de configurare (în aplicație)

La prima deschidere, aplicația te duce pas cu pas prin:

1. **Numele instalației** (ex. „Casa de la munte”).
2. **Parola aplicației**, aleasă de tine (minim 8 caractere). O cere fiecare telefon nou, o singură dată.
3. **Accesul de oriunde** (opțional, se poate face și mai târziu): te loghezi cu **contul tău Google** pe pagina
   care se deschide (serviciul Tailscale), apoi aprobi accesul. Primești o adresă `https://…ts.net` care merge de
   pe orice telefon, de oriunde, fără altă aplicație.
4. **Căutarea dispozitivelor**: Amper caută singur invertorul și bateriile (pe cablu și prin Bluetooth) și îți arată
   ce a găsit.
5. **Capacitatea** fiecărui modul de baterie (Ah), din ea se calculează SOC-ul total.
6. **Notificările** pe acest telefon + o notificare de test. Telefonul le poate primi doar când aplicația e
   deschisă prin adresa `https://…` de la pasul 3 (pe `http://192.168…` browserul nu le permite).

**Pune aplicația pe ecranul telefonului:** iPhone (Safari) → Partajare → *Adaugă pe ecranul principal*;
Android (Chrome) → ⋮ → *Instalează aplicația*. Pe iPhone, notificările merg doar așa.

## 5. Mai departe

- **Actualizări:** Setări → Despre → Actualizări. Pi-ul caută singur o dată pe săptămână și te anunță; tu alegi
  când instalezi. Datele și setările rămân; dacă versiunea nouă nu pornește, Pi-ul revine singur la cea veche.
- **Dă adresa celor din casă:** Setări → Conectare → Partajează Amper.
- **Alt WiFi** (ex. ai schimbat routerul): Setări → Despre → Info → atinge rândul „WiFi”. Dacă Pi-ul rămâne fără internet,
  pornește din nou „Amper-Setup” și o iei de la pasul 3.
- **Un dispozitiv nou** (ex. ai adăugat o baterie): Setări → Dispozitive conectate → + Adaugă dispozitiv.
- **Setările BMS și ale invertorului** se pot schimba din aplicație (cu parola). Atenție: sunt pe răspunderea ta.

## 6. Dacă ceva nu merge

**Nu apare „Amper-Setup”:**
- Mai așteaptă: hotspotul pornește după ~2 minute fără internet (prima pornire durează mai mult).
- Dacă Pi-ul e legat cu cablu de rețea la router, are deja internet și nu pornește hotspotul. Deschide
  **http://amper.local:8080** sau caută în pagina routerului dispozitivul „amper” și adresa lui.
- Ledul verde al Pi-ului nu clipește deloc? Rescrie cardul (pasul 2) sau încearcă alt card.
- Alimentatorul: cu unul slab, WiFi-ul și Bluetooth-ul se poartă ciudat.

**Aplicația scrie „Amper nu răspunde”:** Pi-ul are grijă singur. Dacă Amper nu răspunde 5 minute, îl repornește;
dacă problema a venit cu o actualizare din ultima săptămână, revine singur la versiunea anterioară (datele rămân) și
te anunță pe telefon. Dacă tot nu merge:
1. Scoate alimentarea Pi-ului câteva secunde și pornește-l din nou.
2. Revenire de pe card: oprește Pi-ul, pune cardul în calculator și fă pe partiția **bootfs** un fișier text gol
   **`revenire.txt`**. Pune cardul la loc și pornește: Pi-ul revine la versiunea anterioară a aplicației.

**Am pierdut adresa de acasă:** conectează-te din nou la routerul tău și caută dispozitivul „amper”, sau încearcă
**http://amper.local:8080** (merge pe iPhone și pe calculator; pe unele telefoane Android nu).

**Un dispozitiv nu e găsit:**
- Daly prin Bluetooth: BMS-ul poate „adormi” fără curent; pornește un consumator și apasă „Caută” din nou. Cât timp
  aplicația Daly de pe telefon e conectată la modul, Pi-ul nu se poate conecta la el (și invers).
- Seplos: adaptorul pe portul **RS485** (nu CAN) al bateriei; adresele din DIP switch diferite pe fiecare pachet.
- Growatt: încearcă alt cablu USB sau alt port USB al Pi-ului.

**Ai uitat parola aplicației:**
1. Oprește Pi-ul (scoate alimentarea), scoate cardul și bagă-l în calculator. Apare o partiție numită **bootfs**.
2. Fă pe ea un fișier text gol numit **`resetare-parola.txt`** (clic dreapta → Nou → Document text).
3. Pune cardul înapoi în Pi și pornește-l. Pi-ul șterge parola și fișierul și te anunță pe telefon.
4. Deschide Amper **din rețeaua de acasă** (`http://192.168.x.y:8080` sau `http://amper.local:8080`) și alege o
   parolă nouă. Prin adresa `https://…` de oriunde nu se poate, ca nimeni din internet să nu apuce s-o aleagă el.

## 7. Ajutor și feedback

Scrie-mi direct cu ce ai observat (o captură de ecran ajută). Corecturile ajung la tine ca actualizare în
aplicație.

---

**Pentru avansați:** instalarea manuală peste Raspberry Pi OS, comenzile de pe Pi, toate alertele, copiile de
siguranță în Google Drive și dezvoltarea: [docs/instalare-avansata.md](docs/instalare-avansata.md).

**Licență:** gratuit pentru uz personal, necomercial ([PolyForm Noncommercial 1.0.0](LICENSE)), fără nicio garanție.

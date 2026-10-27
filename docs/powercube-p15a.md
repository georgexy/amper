# Ecobat PowerCube P15A — note pentru driverul Amper

> Strânse pe 2026-10-27 de sesiunea Claude locală (laptopul lui George) pentru sesiunea din cloud, care nu are acces
> la site-ul ecobatenergy.ro. Sursa principală: manualul de utilizare în română al producătorului,
> „Manual Utilizare Acumulatori Power Cube 14.33 kWh RO” (36 de pagini, versiunea fișierului A0), de pe
> `https://ecobatenergy.ro/uploads/eshop/produse_upload/` + fișa produsului de pe sun.store.
> Manualul e protejat de drepturi de autor (Ecobat Energy) — **nu-l puneți în repo**; aici sunt doar datele tehnice,
> rezumate. Traducerea manualului e automată și pe alocuri stricată („POATE SA” = CAN, „POT SĂ” = CAN-L,
> „PE” = ON, „Guruwatt” = Growatt, „soare, soare, lună” = nume de protocoale traduse greșit).

## Context
- Prietenul la care e acum Pi-ul lui George vrea un PowerCube; e următorul BMS după JK (vezi CLAUDE.md, „BMS JK”).
- În Amper: **nu există driver**. Nimic de aici nu a fost verificat pe o baterie reală.
- Producător/distribuitor: SC Ecobat Energy S.R.L., Crevedia (Dâmbovița), support@ecobatenergy.com.

## Date tehnice (un modul)
| | |
|---|---|
| Chimie | LiFePO4, 51,2 V nominal → **16S** (16 × 3,2 V; numărul de celule nu e scris explicit) |
| Capacitate | **280 Ah**, 14 336 Wh („14,33 kWh”; fișa magazinului zice 14,3 kWh) |
| Domeniu de tensiune | **42 – 57,6 V** (2,625 – 3,60 V/celulă la 16S) |
| Curent max. încărcare / descărcare | **200 A / 200 A** |
| DOD | 100 % |
| Durată de viață | 6000 de cicluri |
| Temperatură | încărcare 0…55 °C, descărcare −20…55 °C; instalare între −10 și 50 °C, doar în interior |
| Paralel | până la **6 module** (pachet de cel mult ~80 kWh) |
| Dimensiuni / greutate | 690 × 300 × 797 mm, ≈134,5 kg net |
| Protecție | IP32 în manual (IP20 pe fișa magazinului) |
| Depozitare | 51–53 V, 30–60 % SOC, reîncărcare la 3 luni |

## Panoul frontal / porturi
Pe modul: bornele + și −, întrerupător, buton ON/OFF și RST, LED-uri RUN / ON-OFF / ALM / CAPACITATE (SOC),
DIP switch de adresă (6 poziții), contacte uscate, **RS232** (ecran LCD / PC), **RS485 + CAN** (o pereche de mufe RJ45)
și **RS485-A + RS485-B** (a doua pereche, „comunicare paralelă”, doar între module).

### RS485 (spre PC sau invertor) și CAN (doar spre invertor) — mufe RJ45 8P8C verticale
Manualul numerotează cele două mufe alăturate 1–16: mufa din dreapta (CAN) = pinii 1–8, cea din stânga (RS485) = 9–16
(pinul 9 = pinul 1 al mufei, 16 = pinul 8). Pe pinii fiecărei mufe:

| Mufa RS485 | | Mufa CAN | |
|---|---|---|---|
| pin 1 și 8 (9, 16) | RS485-B | pin 4 | CAN-H |
| pin 2 și 7 (10, 15) | RS485-A | pin 5 | CAN-L |
| pin 3 și 6 (11, 14) | GND | pin 3 și 6 | GND |
| pin 4, 5 (12, 13) | NC | pin 1, 2, 7, 8 | NC |

(CAN pe 4/5 = la fel ca la Pylontech. RS485 cu A pe 2/7 și B pe 1/8 — de verificat cu multimetrul / cablul livrat.)

### Portul de paralel (RS485-A / RS485-B) — doar între module
Ambele mufe: pin 1 și 8 = RS485B-B, 2 și 7 = RS485B-A, 3 și 6 = GND, 4 și 5 = NC (manualul dă pentru RS485-A
aceeași schemă cu numerotarea 9–16). E o magistrală internă master/slave — **nu e pentru Amper**.

### Viteze implicite
- **RS232: 9600 bps** (pentru PC / „computerul superior”).
- **RS485: 9600 bps**; „echipamentul de monitorizare, ca gazdă, interoghează datele după adresă” → protocol
  cerere-răspuns, cu adresa din DIP switch.
- **CAN: 250 kbit/s** după manual (atenție: Pylontech CAN e de obicei 500 kbit/s — de verificat).

### Protocoale declarate
- RS485: protocoale de invertor (inclusiv **Growatt**), alese „prin DIP switch, la adrese diferite”.
- CAN: **Victron, Pylon, Growatt**, alese prin DIP switch.
- Manualul **nu** are tabelul de alegere a protocolului (doar cel de adrese, mai jos) — de cerut de la Ecobat sau de
  văzut pe placă / în programul PC.

## DIP switch — adresa (6 poziții)
#1–#4 = adresa în binar (#1 = bitul 0), **#5 rezervat, #6 „gazdă”** (în tabel e OFF peste tot).
- Adresa **0** (toate OFF) = **gazda/master** („Pachetul 0”); 1–15 = sclavi („Pachetul 1…15”).
- Exemple: 1 = #1 ON; 2 = #2 ON; 3 = #1+#2; 4 = #3; 5 = #1+#3; 8 = #4; 15 = #1–#4 ON.
- Textul zice „adresa se setează 1–6 și nu se repetă”, tabelul merge 0–15 (6 module max. în paralel).
- La pornire, modulele se pornesc pe rând, în ordinea adreselor.

## BMS-ul și programul PC
- BMS-ul e **Daren Hi-Tec (DR)** — programul de pe PC se numește **„DR_Application”** și în captură apare ca
  **„BMS_Monitor V1.0.0.3”** (Windows, .NET Framework 2.0, nu se instalează), conectare prin RS232 sau RS485 cu un
  adaptor USB.
- Taburi: Monitoring, Parallel (monitorizare multiplă), Parameters (Read all / Write all / Restore default /
  Import / Export XML — scrierea cere „drepturi de administrator”), Settings (System, Calibration, Wireless module,
  Basic control, Upgrade), History.
- Parametri vizibili (grupuri cu Action / Release / Delay): tensiune celulă (Over/Under DSCH, Over CHG — alarmă și
  protecție), tensiune totală, curent (Over CHG, Over DSCH 1 și 2), temperatură la încărcare și la descărcare
  (low/high alarm și prot), diferență de tensiune între celule, temperatură MOS; în Settings: număr de celule,
  contoare de protecții, cicluri, „sleep”, limitare de curent, informații BMS (versiune software, model hardware,
  „protocol obținut”).
- **Interfața „BMS_Monitor” e cea a BMS-urilor din familia PACE**, ceea ce susține ipoteza de mai jos.

## Ipoteza pentru driver (NEVERIFICATĂ)
- Pe RS485 (și probabil RS232), BMS-urile Daren/PACE folosesc protocolul ASCII de tip **Pylontech/PACE**: cadre
  `~` … `\r` cu VER, ADR, CID1 = 0x46, CID2 = comanda, LENGTH cu sumă de control, CHKSUM — **aceeași familie ca
  `amper/drivers/seplos.py`** (Seplos V2 = VER 0x20). Diferențe probabile: VER (0x25 la Pylontech, alte valori la
  PACE), comenzile (0x42 valori analogice, 0x44 alarme, 0x47/0x4F parametri/protocol) și pozițiile din date.
- Plan propus:
  1. `amper scan`: pe RS485, la 9600 (apoi 19200), încearcă VER 0x20/0x25/… și adresele 0–15 cu CID2 0x42 și
     0x4F (versiunea protocolului); la fel pe RS232 9600.
  2. Driver pornind din `seplos.py` (sau o clasă comună „PACE/Pylon”) + teste pe cadre reale capturate de la baterie.
  3. Validare cu valorile din BMS_Monitor / LED-urile de SOC (SOC, celule, curent cu semn — + = încărcare în Amper —,
     temperaturi, MOSFET-uri, alarme/protecții).
- Portul de citire: dacă bateria vorbește deja cu invertorul pe CAN, RS485-ul de lângă poate fi liber pentru Amper;
  dacă invertorul folosește RS485, Amper ar trebui să asculte pe aceeași linie sau să folosească RS232. Pe un
  sistem cu mai multe module, citirea se face de la master (adresa 0), care ar trebui să răspundă și pentru sclavi
  (de verificat).
- CAN (Pylon) spre invertor = varianta „Amper ca BMS al băncii” din CLAUDE.md, nu pentru citire.

## Atenție
- Lista de coduri de eroare 01–29 din manual (BatVoltLow, BusOverVolt, PvVoltHigh, OverloadInverter, FanFail…)
  e a **invertorului/modulului de putere** al sistemului PowerCube, nu a BMS-ului — nu e utilă pentru driverul de BMS.
- Manualul spune că parametrii interni nu se modifică fără acordul producătorului (altfel se pierde garanția):
  driverul să fie **doar citire** la început; scrierea doar la cererea explicită a utilizatorului.
- De cerut de la Ecobat (support@ecobatenergy.com): documentul protocolului RS485/RS232 al BMS-ului Daren și tabelul
  DIP pentru alegerea protocolului de invertor.

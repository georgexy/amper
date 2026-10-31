# Noutăți în Amper

Fiecare versiune are o secțiune `## <versiune> — <dată>`; textul ei apare în aplicație, la Setări → Actualizări.

## 1.0.0-beta.15 — în pregătire
- Sănătatea celulelor: celulele care ies în evidență sunt grupate pe module — un singur cartonaș pe BMS, cu numele
  modulului sus și dedesubt celulele lui, cele mai slabe primele.

## 1.0.0-beta.14 — 2026-10-30
- Tema Cadrane: bateria e acum sub cele patru cadrane, deasupra graficului zilei, și arată ca o baterie (ca în
  iconiță: se umple de jos în sus, pe segmente), cu procentul scris alb cu contur închis, ca să se citească bine.
- Teme Cadrane și Carduri: sub Panouri scrie cât s-a produs azi, iar sub Consum cât s-a consumat azi.
- Setări → **Limbă · Language**: română sau engleză, aceeași pe toate telefoanele legate la Pi. Deocamdată sunt
  traduse ecranele aplicației și, în engleză, și textele scrise pe Pi: notificările, alertele, Raport baterii și
  raportul nopții, sănătatea celulelor, stările și defectele BMS-urilor și ale invertoarelor, setările și comenzile
  BMS, mesajele de eroare, actualizările și schimbarea WiFi. Rămân în română: notele versiunilor și pagina de
  conectare WiFi a hotspotului „Amper-Setup”.

## 1.0.0-beta.13 — 2026-10-30
- **Raport baterii** (Bateriile → „Raport baterii”): la deschidere arată raportul ultimei nopți (cu ‹ › pentru
  nopțile anterioare, până la 14), iar din taburi alegi altă perioadă: 2, 3, 7, 30 de zile sau „Aleg eu” (de la – până
  la). Raportul nopții: un text despre noapte și un tabel pe module (SOC la început și la final, scădere, curent mediu,
  citiri cu 0 A, cel mai mic curent văzut) — protecții, MOSFET-uri oprite, pene, dacă fiecare BMS numără corect, dacă
  vreunul nu vede curenții mici, cum își împart modulele consumul, echilibrul celulelor, starea băncii dimineața.
  Raportul pe perioadă: tabel cu SOC minim–maxim, Ah descărcați și încărcați, cicluri, diferența maximă a celulelor și
  temperatura maximă; în text: ce probleme au fost (de câte ori, ultima dată), în câte nopți a numărat greșit sau n-a
  văzut curenții mici fiecare BMS, cum își împart consumul, de câte ori s-a încărcat banca complet și cât de jos a
  coborât, cum a evoluat diferența dintre celule, energia produsă și consumată.
- Dimineața vine o notificare scurtă cu raportul nopții; se poate opri din ecranul raportului.
- Două (sau mai multe) invertoare în paralel, de ex. două Growatt SPF 5000 pe aceeași casă și aceeași baterie, fiecare
  cu stringul lui: ecranul principal adună panourile, consumul și rețeaua, energia zilei și graficele din Istoric
  sunt sumele lor, iar butonul ⓘ de la Panouri arată tensiunea și curentul fiecărui string (String 1, String 2…).
- Ecranul invertorului are taburi: „Toate” (doar vizualizare: sumele și starea fiecăruia), „Invertor 1”, „Invertor 2”…
  (fiecare cu setările lui; numele nu mai depind de marcă) și „Setări comune”.
- „Setări comune” (când toate invertoarele sunt de același tip, ex. două Growatt SPF): fiecare setare cu valoarea din
  fiecare invertor; cele care nu sunt la fel apar „diferite”, cu valorile alături. „Modifică” scrie în toate, pe rând,
  verificând fiecare; dacă la unul nu reușește, aplicația spune exact unde. Ceasul se potrivește la toate deodată.

## 1.0.0-beta.12 — 2026-10-29
- Setări → Dispozitive conectate → „+” (Adaugă dispozitiv): la fel ca ghidul — „Configurare automată” (cablu și
  Bluetooth, rezultatele pe Invertor / BMS) sau „Configurare manuală” (ce adaugi → modelul → conexiunea), cu „‹ Înapoi”.
- BMS-urile PACE apar în aplicație ca „PACE / Daren” (nu mai scrie PowerCube).
- Invertoare noi (doar citire, încă neverificate pe un aparat real):
  - **Deye** hibrid monofazat (și Sunsynk, Sol-Ark), pe RS485 (Modbus);
  - **Easun ISolar / SMH** și celelalte Voltronic / Axpert (MPP Solar, PowMr vechi), pe RS232 (protocolul PI30);
  - **Easun SMG / SMG-II** (PowMr nou, Anenji, SRNE), pe RS485 (Modbus); Amper alege singur harta de registre.
  Se găsesc la detectarea automată și se pot alege din listă în ghid. Setările lor (ca la Growatt) vin după ce citirea
  e confirmată pe un invertor real.
- Ghid de configurare → Dispozitivele: două variante, una sub alta. „Configurare automată”: îți spune ce să legi
  și ce să pornești, caută singur și arată separat invertorul găsit și BMS-urile găsite. „Configurare manuală”: întâi
  invertorul (modelul din listă și tipul conexiunii), apoi acumulatorii (BMS); Amper caută doar ce ai ales, în câteva
  secunde. „‹ Înapoi” te duce un pas înapoi (alt model, înapoi la invertor, altă variantă). La fiecare dispozitiv găsit
  vezi valori citite din el („… — corect?”). În listă apar deja Deye și Easun (ISolar/SMH și SMG), „în curând”.
  Butonul „+ Daly (se găsește singur)” a fost scos.
- Reparat: la „Setează SOC”, butonul „Scrie în BMS” rămânea dezactivat când în câmp era SOC-ul sugerat (aplicația
  credea că e aceeași valoare ca acum). Câmpul pornește tot de la sugestie, dar se compară cu SOC-ul real.
- Spații între butoane și cardurile vecine: „Confirmă” (parola), „Instalează acum”, „Caută actualizări” și notele
  versiunii nu se mai lipesc unele de altele.

## 1.0.0-beta.11 — 2026-10-29
- Reparat: butonul „Scrie” din fereastra de modificare nu făcea nimic (setări BMS, setări invertor, „Setează SOC”)
  și scria „Scrie în invertor” și la BMS. Cauza: butonul „Activează Pro” (de la 1.0.0-beta.6) avea același nume
  intern și îi „lua” apăsarea.

## 1.0.0-beta.10 — 2026-10-29
- Comenzi pentru BMS-urile Daly (Pro; Bateriile → ⚙ → Comenzi): pornește/oprește încărcarea și descărcarea,
  setează SOC-ul (cu o sugestie după tensiunea celulelor) și repornește BMS-ul. Fiecare cere confirmare și parola,
  iar rezultatul se citește înapoi din BMS; dacă BMS-ul ține un MOSFET oprit, aplicația spune de ce.
- Siguranță: dacă restul băncii nu poate duce curentul de acum (cu rezervă de 30%) sau n-ar mai rămâne niciun
  modul, oprirea încărcării/descărcării arată avertismentul și cere o a doua confirmare — se poate face totuși
  (ex. pentru o intervenție la conexiunile unui modul). Repornirea e refuzată dacă alt BMS repornește sau restul
  băncii nu e complet pornit. Pornirea e mereu permisă.
- Alertă nouă: un MOSFET oprit din Amper și uitat așa mai mult de 30 de minute. Cât un BMS repornește din Amper,
  nu apare „nu comunică”.
- Comenzile la care un BMS nu răspunde de 2 ori la rând sunt ascunse pentru acel firmware, cu „Încearcă din nou”.
- Sănătatea celulelor: Amper urmărește fiecare celulă în momentele în care diferențele se văd (final de
  încărcare, descărcare adâncă, salturi de curent) și, o dată pe săptămână (luni), te anunță dacă o celulă rămâne
  mereu în urmă sau o ia mereu înainte, sau dacă are rezistența mai mare. Ecranul nou „Sănătatea celulelor”
  (ultimul rând din Bateriile) arată celulele care se abat, cele mai slabe primele, cu scor și evoluția pe 8 săptămâni.

## 1.0.0-beta.9 — 2026-10-29
- Tema Flux: în stânga bateriei, puterea e verde când se încarcă și roșie când se descarcă.
- Tema Flux: pe telefoanele înalte (ex. iPhone 15) bateria nu mai ajunge sub bara de jos și linia spre baterie
  nu se mai întinde pe jumătate de ecran: totul stă centrat, cu spații egale sus și jos.

## 1.0.0-beta.8 — 2026-10-29
- Actualizări: istoricul și „Revino la versiunea anterioară” s-au mutat într-un ecran separat, „Istoric versiuni”.
- Mesajele de la Actualizări spun „server” în loc de GitHub.
- Tema Flux: ecranul principal încape pe telefon fără glisare (și pe ecrane mici, ca iPhone SE). Căsuțele de jos
  au dispărut: „produs azi” e sub panouri, „consumat azi” sub consumul casei, „din rețea azi” sub rețea, iar
  autonomia rămâne sub baterie. Sub baterie nu mai apar erorile modulelor (se văd în ecranul Bateriile), iar
  textul cu energia rămasă e mai mic. Starea invertorului e lângă invertor, iar diagrama și bateria se
  întind pe toată înălțimea ecranului. Bateria stă jos: în stânga ei, dacă se încarcă sau se descarcă, cu ce putere
  și în cât timp se încarcă/descarcă complet; în dreapta, capacitatea totală și energia actuală.
- Setările BMS Daly: rolul pragurilor se ia după poziție, cum s-a dovedit pe hardware: primul din pereche e
  avertismentul (nivel 1), al doilea e protecția (nivel 2, taie). Înainte rolul se ghicea după valori și ieșea pe dos
  la valorile din fabrică. Dacă protecția e mai puțin severă decât avertismentul, ecranul o semnalează (fără să
  blocheze scrierea). La curent și temperaturi, ordinea e marcată „neconfirmat pe hardware”.
- Daly prin Bluetooth: când se pierd tensiuni de celule (ex. 10 din 16), citirea se reîncearcă o dată, apoi se
  păstrează ultimele complete (cel mult 2 minute, cu „de acum N s”). Nu se mai calculează minim/maxim/diferență din
  celule lipsă; tensiunea, curentul și SOC-ul rămân valide. La fel pe cablu.
- Alerte: când adaptorul USB al unor dispozitive e scos din Pi (portul nu mai există), apare o singură alertă
  „Adaptorul … e deconectat”, nu câte una pentru fiecare dispozitiv. Cu „Deconectează” pe fiecare dispozitiv,
  alerta dispare după ultimul.
- Protecții BMS: alerta și fișa modulului spun dacă BMS-ul chiar a oprit încărcarea/descărcarea (MOSFET oprit)
  sau doar semnalează protecția, cu MOSFET-urile încă pornite.

## 1.0.0-beta.7 — 2026-10-29
- Reparat: la setările de echilibrare ale BMS-ului Daly, aplicația spunea „BMS-ul arată altceva decât s-a trimis”
  deși valoarea se schimbase. Unele Daly modifică singure alți octeți din același bloc; acum se verifică doar
  valoarea scrisă.
- Setări → Despre: ediția, versiunea și ID-ul Pi-ului stau într-un card mic, sub butoane; textul de jos (versiunea
  și „citire la fiecare 2 s”) a fost scos.
- Bara de jos nouă: Istoric în stânga, Alerte în dreapta, iar la mijloc un cerc mare care arată SOC-ul băncii.
  Inelul se umple cât bateria și își schimbă culoarea: verde cu fulger când se încarcă, albastru cu o căsuță
  când alimentează casa, gri în repaus, roșu cu „!” la avarie. Atins, te duce pe ecranul principal.

## 1.0.0-beta.6 — 2026-10-28
- Două ediții, același program: **Amper Lite** (supraveghere: tot, mai puțin setările BMS și ale invertorului) și
  **Amper Pro** (și setările). Pi-urile care au deja Amper rămân Pro; o instalare nouă de pe card pornește Lite.
- Setări → Despre arată ediția și „ID Pi”. Pro se activează cu un cod primit pentru acel Pi (merge doar pe el și se
  verifică fără internet): lipești codul și apeși „Activează Pro”.

## 1.0.0-beta.5 — 2026-10-28
- Un BMS sau invertor care n-a mai răspuns 5 zile se deconectează singur: nu mai apare pe ecran, alertele lui
  „nu mai comunică” se închid (fără notificări „Rezolvat”) și primești o singură notificare. Rămâne în listă: dacă
  răspunde din nou (ex. ai legat la loc cablul), revine singur. Până acum aștepta 14 zile.

## 1.0.0-beta.4 — 2026-10-28
- Istoric → „Energie pe zi”: a treia bară, „Din rețea” (energia intrată în invertor din rețea sau generator, din
  puterea de intrare AC citită din invertor), și totalul ei pe intervalul ales (7 zile, 30 de zile, un an, de la–până
  la; peste două luni, pe lună).
- Pe ecranul principal, lângă „Produs azi”: „Din rețea azi”, în toate temele (Cadrane: în rândul „Azi”; Flux: o a
  patra căsuță, cele patru pe două rânduri; Carduri: în banda de sus).
- Conține și repararea din 1.0.0-beta.3 (în fereastra „Confirmă cu parola” se poate scrie din nou).

## 1.0.0-beta.3 — 2026-10-28
- Reparat: în fereastra „Confirmă cu parola” (la actualizare, setările BMS și ale invertorului, WiFi) nu se putea
  scrie parola de la tastatură. Cu câmpul gol, „Confirmă” cere acum parola, în loc să spună „Parola nu e corectă”.

## 1.0.0-beta.2 — 2026-10-28
- Baterii cu BMS PACE pe RS485 sau RS232, încă netestat pe o baterie reală: Ecobat PowerCube (BMS Daren) și multe
  baterii de rack sau de perete care vorbesc „Pylon”. Amper citește SOC-ul, celulele, temperaturile, curentul,
  ciclurile, MOSFET-urile, echilibrarea, alarmele și protecțiile; nu scrie nimic în BMS. Pe cablu e găsit singur
  (adresele 0–15 din DIP switch, versiunea protocolului se află singură); fiecare modul apare separat.

## 1.0.0-beta.1 — 2026-10-27
- Setări arată ca pe iPhone: o listă grupată, cu iconițe colorate care se mișcă puțin (clopoțelul se leagănă,
  bateria se umple, globul se rotește…), fără explicații sub butoane.
- „Deconectează” pentru orice BMS: în fișa bateriei (atingi bateria în ecranul Bateriile) și la „Dispozitive
  inactive”. Cere parola și o confirmare. Amper nu-l mai citește și nu mai dă alerte pentru el (alertele lui
  deschise se închid fără notificare), fără repornire; modificarea apare în jurnal și primești o notificare.
  Istoricul lui rămâne.
- La Daly, poziția (ex. „Daly 2”) rămâne liberă, iar căutarea automată poate găsi alt modul; cel deconectat intră
  pe lista „Module excluse” (Setări → Dispozitive conectate), de unde îl poți „Permite din nou”. La o poziție
  liberă: „Caută din nou”. Seplos și celelalte se scot din configurare.
- BMS JK (Jikong), încă netestat pe un JK real: prin Bluetooth (JK cu hardware 10.x, 24 de celule, și 11 sau mai nou,
  32 de celule; protocolul se alege singur după versiunea BMS-ului) și pe cablu, prin modulul RS485/UART al JK.
  La „Adaugă dispozitiv” → „Caută prin Bluetooth” un JK apare ca „pare JK”; pe cablu e găsit singur. JK-urile foarte
  vechi (hardware 3.x, protocolul JK04) nu sunt încă suportate.
- Egalizatoarele active (module Bluetooth tot „DL-…”) nu mai sunt luate drept BMS Daly: un modul se leagă doar dacă
  răspunde ca un BMS (capacitate validă sau SOC și Ah rămași). La „Caută acum” și în ghidul de configurare vezi ce
  s-a găsit, cu valorile citite, și poți „Respinge” un modul greșit (intră pe lista de excluse).

## 0.3.0-beta.10 — 2026-10-27
- Actualizări: comutatorul „versiunile de test (beta)” e primul, sus; butonul se numește „Caută actualizări”;
  „Descarcă” stă în dreapta titlului „Versiune nouă”, iar bara și „Instalează acum” apar imediat sub el.
- Notificarea pentru o versiune nouă e scurtă: titlul „Amper”, textul „Versiune nouă detectată. Atinge pentru a
  actualiza.” Atinsă, deschide direct Setări → Actualizări.

## 0.3.0-beta.9 — 2026-10-27
- Bateriile pe Bluetooth au pe card numele modulului (ex. „DL-40D63C2A1F2B”, același ca în aplicația Daly de pe
  telefon) și adresa, ca să știi care baterie e Daly 1, 2, 3. Apar și în Setări → Dispozitive conectate.
- „Dispozitive conectate”: butonul de adăugare e acum un „+” mic, sus în dreapta. Deschide ecranul „Adaugă
  dispozitiv”: ce e nou pe cablu (căutat singur) și „Caută prin Bluetooth”; alegi modulul, apoi ce BMS este (Daly;
  JK, JBD, ANT apar, dar nu sunt suportate încă), numele și capacitatea. Amper repornește câteva secunde și îl citește.
- Ecranul de actualizare: în loc de cerc, bateria din iconița Amper se umple pe măsură ce avansează actualizarea.

## 0.3.0-beta.8 — 2026-10-27
- Imaginea SD Amper: se scrie pe card cu Raspberry Pi Imager („Use custom”) și Pi-ul pornește direct cu hotspotul
  „Amper-Setup”, apoi ghidul de configurare. Imaginea e aceeași pentru toți; ce trebuie să fie unic (topicul de
  notificări, cheile, baza de date) se face la prima pornire. SSH e oprit, numele în rețea e „amper”.
- Hotspotul deblochează singur WiFi-ul la pornire (pe un card nou Raspberry Pi OS îl ține blocat).
- Parola uitată: pui pe card (partiția „bootfs”, din calculator) un fișier `resetare-parola.txt` și pornești Pi-ul;
  parola se șterge, primești notificare, iar aplicația cere una nouă — doar din rețeaua de acasă, nu prin adresa
  de oriunde.
- Paznic pe Pi: dacă Amper nu răspunde 5 minute, e repornit; dacă nici așa nu merge și a fost actualizat în ultima
  săptămână, Pi-ul revine singur la versiunea anterioară (cu datele) și te anunță.
- În loc de ecran negru, aplicația scrie „Amper nu răspunde” și ce se întâmplă.
- Revenire de pe card: fișierul `revenire.txt` pus pe partiția „bootfs” → la pornire, versiunea anterioară.
- Actualizări urgente: o versiune marcată „urgentă” se instalează singură pe toate Pi-urile (în câteva ore), cu
  aceleași verificări și revenire automată. Pi-ul caută versiuni noi la 6 ore (anunță tot o singură dată).
- Istoric: graficele apar imediat din ultima încărcare (ținută și pe telefon) și se actualizează în fundal.
- „Dispozitive conectate” arată doar dispozitivele care comunică acum; cele configurate dar tăcute sunt în meniul
  „Dispozitive inactive”.
- Rețeaua WiFi s-a mutat în Setări → Despre → Info: atingi rândul „WiFi” (sau „neconectat”) și se deschide
  schimbarea rețelei. Info nu mai arată nodurile tehnice „funnel-ingress-node”; aparatele tale legate la server au
  secțiunea lor.
- La „Panouri”, pe toate temele: un buton mic „i” arată tensiunea și curentul PV de acum (pe fiecare intrare, dacă
  invertorul are două), citite din invertor.
- Documentație nouă, simplă, pentru cei care primesc Amper (README): ce trebuie cumpărat, scrierea cardului,
  prima pornire, ce faci dacă nu apare „Amper-Setup”. Instalarea manuală a trecut în docs/instalare-avansata.md.

## 0.3.0-beta.7 — 2026-10-27
- Hotspotul de configurare arată adresa în nume: „Amper-Setup 10.42.0.1”; după ce Pi-ul s-a conectat la WiFi-ul
  ales, timp de 3 minute „Amper gata 192.168.x.y” — adresa de acasă se citește direct din lista de rețele.
- Instalările noi folosesc parola comună „amper-setup” pentru hotspot (cele existente își păstrează parola).
- Comutatoare ca pe iPhone (glisoare) în loc de bife: canalul beta, notificările pe acest telefon, automatizările
  (pornit/oprit), ghidul de configurare, confirmările.
- Reparat: după o actualizare, iPhone-ul putea folosi încă fișierele vechi ale aplicației (de ex. alertele își
  arătau încă detaliile). Acum pagina cere fișierele cu versiunea în adresă, iar telefonul verifică mereu dacă
  s-au schimbat.
- Același motiv făcea ca butonul „Descarcă și verifică” să nu reacționeze până la redeschiderea aplicației: pagina
  nouă cu codul vechi. Acum pagina și codul vin mereu împreună; butonul arată imediat „Pornesc descărcarea…”.
- Cale de rezervă fără aplicație (prin SSH): `sudo amper update --install` / `--rollback` / `--check`.
- Istoric mai rapid: peste 6 ore, graficele folosesc mediile la 5 minute (de ~12 ori mai puțin de citit de pe
  card), iar toate graficele vin într-o singură cerere; „Se încarcă…” până sosesc datele.
- Reparat: pe 7 și 30 de zile, cu puține zile de date, liniile nu se desenau deloc (erau rupte după fiecare punct).
- Setări → Despre → „Partajează Amper”: adresa de oriunde și cea de acasă, cu „Trimite” (WhatsApp, Mesaje…) și
  „Copiază”, ca s-o dai celor din casă.
- În aplicație, conexiunea pentru accesul de oriunde se numește „server” (ex. „Conectare la server”).
- Setări → „Dispozitive conectate” (în loc de „Module Daly”): invertorul și toate BMS-urile, grupate după tip și
  ordonate după adresă, cu legătura (Bluetooth, cablu, RS485), viteza, capacitatea și dacă comunică acum. Butonul
  „+ Adaugă dispozitiv” deschide ghidul direct la căutarea dispozitivelor.
- Setări reorganizate: meniul „Conectare” (Acces de oriunde, Partajează Amper, Rețeaua WiFi); „Ghid de configurare”
  în Despre, lângă Actualizări și Info.

## 0.3.0-beta.6 — 2026-10-27
- Setări: meniul nou „Despre” cuprinde Actualizări și Info.
- Acces de oriunde: în loc de pagină goală, „Verific starea Tailscale…” și un mesaj clar dacă Pi-ul nu răspunde;
  fiecare comandă Tailscale are o limită de 10 secunde.
- Prima pornire: după ce Pi-ul se conectează la WiFi-ul ales, „Amper-Setup” reapare 3 minute cu adresa Pi-ului
  din rețeaua de acasă (http://192.168.x.y:8080), ca telefonul s-o găsească.

## 0.3.0-beta.5 — 2026-10-27
- Alerte: doar textul scurt; detaliile (ce era în paranteză) apar când atingi alerta.
- „Alertele mele” s-au mutat în Setări → Automatizări („+ Adaugă automatizare”).

## 0.3.0-beta.4 — 2026-10-27
- Actualizare în doi pași: „Descarcă și verifică”, cu bară de progres și procente pentru descărcare și verificare,
  apoi butonul „Instalează acum”. Arhiva descărcată se verifică din nou la instalare.
- În timpul instalării apare un ecran cu un cerc care se umple pas cu pas; când versiunea nouă răspunde,
  aplicația se reîncarcă singură.
- Reparat: după actualizare, aplicația putea rămâne cu ecranul negru (pagina de eroare a Tailscale din timpul
  repornirii era afișată și ținută minte). Acum se folosește ultima versiune bună până răspunde Pi-ul.
- Acces de oriunde fără Tailscale pe telefon (Setări → Acces de oriunde, și în ghidul de configurare):
  „Conectează-te la Tailscale” (logare cu Google, contul tău) și „Pornește accesul de oriunde” (Tailscale Funnel);
  adresa https merge apoi pe orice telefon. Pornirea/oprirea cere parola și trimite notificare. Cererile venite
  prin Funnel cer întotdeauna parola.

## 0.3.0-beta.3 — 2026-10-27
- Sigla Amper (bateria cu fulger) sus, înaintea numelui.
- Notificările pe telefon arată doar textul principal; atingerea lor deschide Alerte, unde apar și detaliile
  (ce era în paranteză).
- Alertele nu mai apar ca benzi pe ecranul principal, doar la Alerte (cifra roșie de pe buton).
- Bara de jos: o capsulă mai mică, cu iconițe colorate; butonul activ se colorează și își arată numele. La fel pe
  toate temele.

## 0.3.0-beta.2 — 2026-10-27
- Modulele Daly prin Bluetooth: prima căutare se face imediat și când Amper pornește în primele 30 de secunde
  după alimentarea Pi-ului (înainte se amâna).

## 0.3.0-beta.1 — 2026-10-27
- Ghid de configurare la prima pornire: numele instalației, parola aplicației, căutarea dispozitivelor (cablu USB
  și Daly prin Bluetooth), capacitățile modulelor, notificările pe telefon. Se poate relua din Setări.
- Securitate: după 5 parole greșite de la aceeași adresă, încercările sunt încetinite și primești notificare;
  notificare „Dispozitiv nou conectat” la fiecare telefon sau calculator nou; Setări → Securitate arată
  dispozitivele și ultimele încercări de logare.
- Schimbarea setărilor bateriilor, ale invertorului, a rețelei WiFi și actualizarea cer parola din nou (ținută
  minte 5 minute) — și de pe ecranul HDMI.
- Parola se poate schimba din aplicație; se păstrează criptată (scrypt), iar schimbarea deloghează celelalte
  dispozitive.
- Setări → Actualizări: „Primește versiunile de test (beta)”.

## 0.2.0 — 2026-10-27
- Actualizări din aplicație: Setări → Actualizări arată versiunea instalată, caută versiuni noi pe GitHub
  (singur, o dată pe săptămână, sau la cerere) și le instalează cu un buton.
- Fiecare versiune e semnată digital; Pi-ul refuză orice arhivă nesemnată sau modificată.
- Înainte de fiecare actualizare se face o copie a datelor (istoric, alerte, config). Dacă versiunea nouă nu
  pornește, Pi-ul revine singur la cea veche, cu datele de dinainte.
- Buton „Revino la versiunea anterioară”.
- Copia în Google Drive devine opțională (pornește doar dacă e configurată).

## 0.1.0 — 2026-10-26
- Prima versiune: monitorizare Daly (cablu și Bluetooth), Seplos V2, Growatt SPF; alerte, notificări în aplicație,
  ntfy, hotspot „Amper-Setup”, ecran HDMI.

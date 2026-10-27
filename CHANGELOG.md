# Noutăți în Amper

Fiecare versiune are o secțiune `## <versiune> — <dată>`; textul ei apare în aplicație, la Setări → Actualizări.

## 0.3.0-beta.8 — în pregătire
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
  invertorul are două). Curentul e calculat din putere și tensiune (invertorul nu-l raportează separat).
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

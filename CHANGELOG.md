# Noutăți în Amper

Fiecare versiune are o secțiune `## <versiune> — <dată>`; textul ei apare în aplicație, la Setări → Actualizări.

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

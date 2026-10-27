# Noutăți în Amper

Fiecare versiune are o secțiune `## <versiune> — <dată>`; textul ei apare în aplicație, la Setări → Actualizări.

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

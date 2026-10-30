"""Raportul nopții: ce s-a întâmplat cu bateria de seara până dimineața, scris ca text + un tabel pe module.

Noaptea = ultima perioadă continuă fără panouri (PV sub `DARK_W`, cel puțin `MIN_NIGHT_S`), din citirile
invertoarelor; fără invertor, 21:30 → 07:00. Totul se calculează din citirile brute salvate (pack_samples la ~10 s),
deci raportul se poate face oricând, pentru orice noapte din ultimele 14 zile.

Ce verifică (aceleași întrebări ca la o analiză făcută de mână):
  - protecții, MOSFET-uri oprite, întreruperi de comunicație / de date (pene);
  - dacă SOC-ul fiecărui modul scade cât arată curentul măsurat (Ah / capacitate) — altfel BMS-ul nu numără corect;
  - citirile de 0 A cât banca se descarcă (BMS-ul nu vede curenții mici → SOC-ul lui rămâne în urmă);
  - cum își împart modulele consumul (curentul mediu), cât de apropiate sunt SOC-urile la final;
  - echilibrul celulelor (diferența max − min), comenzile/setările date seara, starea băncii dimineața.
"""
from __future__ import annotations

import logging
import statistics
import time

log = logging.getLogger(__name__)

DARK_W = 50                 # sub atât PV-ul e „noapte”
MIN_NIGHT_S = 3 * 3600
MAX_NIGHT_S = 15 * 3600
ZERO_A = 0.05               # |I| sub atât = „0 A”
ZERO_BAD = 0.20             # peste 20 % din citiri cu 0 A = BMS-ul pierde curenții mici
GAP_S = 120                 # o pauză mai lungă între citirile unui modul = n-a comunicat
SHARE_BAD = 1.3             # un modul dă cu 30 % mai mult curent decât altul
META = "night_report_last"


def _n(v, d=1) -> str:
    return f"{v:.{d}f}".replace(".", ",").replace("-", "−")


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _dur(s: float) -> str:
    m = round(s / 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def find_night(db, now: float | None = None) -> dict:
    """{start, end, ongoing, by_pv}: ultima noapte (după PV) sau 21:30 → 07:00 fără invertor."""
    now = now or time.time()
    rows = db.samples("inverter", int(now - 22 * 3600), int(now))
    if rows:
        buckets: dict[int, dict[str, list[float]]] = {}
        for r in rows:
            if r["pv_power"] is not None:
                buckets.setdefault(int(r["ts"] // 300), {}).setdefault(r["device"], []).append(r["pv_power"])
        pv = sorted((b * 300, sum(sum(v) / len(v) for v in devs.values())) for b, devs in buckets.items())
        runs, cur = [], None
        for ts, w in pv:
            if w < DARK_W:
                if cur and ts - cur[1] <= 6 * 3600:      # o pană (fără date) în mijlocul nopții nu e zi
                    cur[1] = ts + 300
                else:
                    cur = [ts, ts + 300]
                    runs.append(cur)
            else:
                cur = None
        runs = [r for r in runs if r[1] - r[0] >= MIN_NIGHT_S]
        if runs:
            start, end = runs[-1]
            ongoing = end >= pv[-1][0] + 300 and now - pv[-1][0] < 900
            end = min(end, now)
            return dict(start=max(start, end - MAX_NIGHT_S), end=end, ongoing=ongoing, by_pv=True)
    t = time.localtime(now)
    morning = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 7, 0, 0, 0, 0, -1))
    if now < morning:
        morning -= 86400
    return dict(start=morning - 9.5 * 3600, end=morning, ongoing=False, by_pv=False)


def pack_stats(rows: list[dict]) -> dict | None:
    rows = [r for r in rows if r["current"] is not None]
    if len(rows) < 10:
        return None
    socs = [r["soc"] for r in rows if r["soc"] is not None]
    cur = [r["current"] for r in rows]
    ah, gaps, prev = 0.0, [], None
    for r in rows:
        if prev is not None:
            dt = r["ts"] - prev["ts"]
            if dt > GAP_S:
                gaps.append((prev["ts"], r["ts"]))
            ah += prev["current"] * min(dt, 60) / 3600
        prev = r
    caps = [r["remaining_ah"] / (r["soc"] / 100) for r in rows
            if r["remaining_ah"] and r["soc"] and r["soc"] >= 10]
    nz = [abs(c) for c in cur if abs(c) >= ZERO_A]
    deltas = [(r["cell_delta"] * 1000, r["ts"]) for r in rows if r["cell_delta"] is not None]
    last = rows[-1]
    return dict(
        soc0=socs[0] if socs else None, soc1=socs[-1] if socs else None,
        drop=(socs[-1] - socs[0]) if socs else None, avg_a=sum(cur) / len(cur), ah=ah,
        cap=statistics.median(caps) if caps else None,
        zero=sum(1 for c in cur if abs(c) < ZERO_A) / len(cur), min_a=min(nz) if nz else None,
        delta_p10=_pct([d for d, _ in deltas], 0.1), delta_p90=_pct([d for d, _ in deltas], 0.9),
        delta_max=max(deltas) if deltas else None, gaps=gaps,
        chg_off=_off(rows, "charge_mos"), dsg_off=_off(rows, "discharge_mos"),
        cell_lo=last["cell_min"], cell_hi=last["cell_max"], voltage=last["voltage"], ts0=rows[0]["ts"],
        ts1=last["ts"])


def _pct(vals: list[float], q: float):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * len(vals)))]


def _off(rows, col) -> float:
    """Câte secunde a stat MOSFET-ul oprit (0 = mereu pornit)."""
    off, prev = 0.0, None
    for r in rows:
        if prev is not None and prev[col] == 0:
            off += min(r["ts"] - prev["ts"], 60)
        prev = r
    return off


def night_rows(db, names: dict[str, str], s: int, e: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in db.samples("pack", s, e):
        if r["device"] in names:
            out.setdefault(r["device"], []).append(r)
    return out


def counts_right(x: dict) -> bool | None:
    """Scăderea SOC-ului se potrivește cu Ah măsurați / capacitate? None = nu se poate spune (prea puțin, fără date)."""
    if x["cap"] is None or x["drop"] is None or x["zero"] >= ZERO_BAD:
        return None
    expect = x["ah"] / x["cap"] * 100
    if abs(expect) < 2 and abs(x["drop"]) < 2:
        return None
    return abs(x["drop"] - expect) <= max(3.0, 0.3 * abs(expect))


def build(db, names: dict[str, str], now: float | None = None, night: dict | None = None) -> dict:
    now = now or time.time()
    night = night or find_night(db, now)
    s, e = int(night["start"]), int(night["end"])
    packs_rows = night_rows(db, names, s, e)
    st = {d: x for d, rows in packs_rows.items() if (x := pack_stats(rows))}
    prev = {}
    for r in db.samples("pack", s - 86400, e - 86400):
        if r["device"] in st:
            prev.setdefault(r["device"], []).append(r)
    prev = {d: x for d, rows in prev.items() if (x := pack_stats(rows))}
    inv = db.samples("inverter", s, e)
    order = [d for d in names if d in st]
    nm = lambda d: names.get(d, d)          # noqa: E731

    rep = dict(start=s, end=e, ongoing=night["ongoing"], title=f"Noaptea {_hm(s)} → {_hm(e)}",
               rows=[], points=[], commands=[], headline="", period="", bank="", watch="")
    if not order:
        rep["headline"] = "Nu sunt citiri ale bateriilor în această noapte."
        return rep

    # ---- tabelul
    for d in order:
        x = st[d]
        rep["rows"].append(dict(
            device=d, name=nm(d), soc=[x["soc0"], x["soc1"]], drop=x["drop"], avg_a=round(x["avg_a"], 2),
            zero=round(x["zero"] * 100), zero_prev=round(prev[d]["zero"] * 100) if d in prev else None,
            min_a=x["min_a"], min_prev=prev[d]["min_a"] if d in prev else None))

    # ---- ce s-a întâmplat (protecții, MOSFET-uri, întreruperi)
    alerts = db.alerts_between(s, e)
    prot = [a for a in alerts if a["rule"] == "bms_protection" and s <= a["ts_start"] <= e]
    issues = []
    if prot:
        issues.append("protecții: " + "; ".join(f"{a['message']} ({_hm(a['ts_start'])})" for a in prot[:3]))
    for d in order:
        x = st[d]
        for col, what in (("dsg_off", "descărcare"), ("chg_off", "încărcare")):
            if x[col] >= 60:
                issues.append(f"MOSFET-ul de {what} al {nm(d)} a stat oprit {_dur(x[col])}")
        if x["gaps"]:
            a, b = max(x["gaps"], key=lambda g: g[1] - g[0])
            more = f" (de {len(x['gaps'])} ori)" if len(x["gaps"]) > 1 else ""
            issues.append(f"{nm(d)} n-a comunicat {_dur(b - a)} ({_hm(a)}–{_hm(b)}){more}")
    all_ts = sorted({r["ts"] for rows in packs_rows.values() for r in rows})
    holes = [(a, b) for a, b in zip(all_ts, all_ts[1:]) if b - a > 300]
    if holes:
        a, b = max(holes, key=lambda g: g[1] - g[0])
        issues = [i for i in issues if "n-a comunicat" not in i]
        issues.append(f"Pi-ul n-a înregistrat date {_dur(b - a)} ({_hm(a)}–{_hm(b)}) — probabil o pană")
    if issues:
        rep["headline"] = "Noaptea a avut probleme: " + "; ".join(issues) + "."
        rep["ok"] = False
    else:
        rep["headline"] = ("Noaptea a trecut fără probleme: nicio protecție, MOSFET-urile au rămas pornite, iar Pi-ul "
                           "a funcționat fără întrerupere.")
        rep["ok"] = True

    # ---- comenzile și setările de seară (până la 3 h înainte)
    for c in db.settings_log_between(s - 3 * 3600, e):
        if c["ok"] and c["device"] in names:
            setting = c["setting"].removeprefix("comandă: ")
            rep["commands"].append(f"{nm(c['device'])}: {setting} {c['old'] or ''} → {c['new'] or ''} ({_hm(c['ts'])})")

    # ---- perioada: consumul și PV-ul
    per_min: dict[int, dict[str, list[float]]] = {}
    for r in inv:
        if r["load_power"] is not None:
            per_min.setdefault(r["ts"] // 60, {}).setdefault(r["device"], []).append(r["load_power"])
    lv = sorted(sum(sum(v) / len(v) for v in devs.values()) for devs in per_min.values())   # invertoarele adunate
    if lv:
        rep["period"] = (f"{_hm(s)} → {_hm(e)}, consum {_pct(lv, 0.05):.0f}–{_pct(lv, 0.95):.0f} W"
                         + (", fără PV" if night["by_pv"] else ""))
    else:
        rep["period"] = f"{_hm(s)} → {_hm(e)}"

    pts = rep["points"]
    blind = {d for d in order if st[d]["zero"] >= ZERO_BAD}     # pierd curenții mici: curentul lor mediu nu e real
    # ---- 1. numără corect? (scăderea SOC-ului față de Ah măsurați / capacitate)
    good, bad = [], []
    for d in order:
        x = st[d]
        if x["cap"] is None or x["drop"] is None or d in blind:
            continue
        expect = x["ah"] / x["cap"] * 100
        if abs(expect) < 2 and abs(x["drop"]) < 2:
            continue
        if abs(x["drop"] - expect) <= max(3.0, 0.3 * abs(expect)):
            good.append(d)
        else:
            bad.append(f"**{nm(d)}**: SOC-ul s-a schimbat cu {_n(x['drop'])} %, dar curentul măsurat "
                       f"({_n(x['ah'])} Ah din {x['cap']:.0f} Ah) explică {_n(expect)} %")
    if bad:
        pts.append("SOC-ul nu urmărește curentul: " + "; ".join(bad) + ". BMS-ul fie nu vede tot curentul, fie are "
                   "capacitatea setată greșit; SOC-ul lui se va îndepărta de cel real.")
    elif good:
        who = "Toate modulele numără" if len(good) == len(order) else ", ".join(nm(d) for d in good) + " numără"
        pts.append(f"**{who} corect**: scăderea SOC-ului se potrivește cu curentul măsurat.")

    # ---- 2. curenții mici (0 A cât banca se descarcă)
    for d in order:
        x, p = st[d], prev.get(d)
        if x["zero"] >= ZERO_BAD:
            pts.append(f"**{nm(d)} nu vede curenții mici**: a arătat 0 A la {x['zero'] * 100:.0f} % din citiri, deși "
                       "banca se descărca. SOC-ul lui va rămâne în urmă.")
        elif p and p["zero"] >= ZERO_BAD:
            pts.append(f"**{nm(d)} vede acum și curenții mici**: 0 A la {x['zero'] * 100:.0f} % din citiri "
                       f"(noaptea trecută {p['zero'] * 100:.0f} %)"
                       + (f", cel mai mic curent văzut {_n(x['min_a'])} A." if x["min_a"] else "."))

    # ---- 3. SOC-urile la final + tensiunea celulelor
    ends = [(d, st[d]["soc1"]) for d in order if st[d]["soc1"] is not None]
    if len(ends) > 1:
        spread = max(v for _, v in ends) - min(v for _, v in ends)
        vals = " / ".join(_n(v) for _, v in ends) + " %"
        lo = [st[d]["cell_lo"] for d in order if st[d]["cell_lo"]]
        hi = [st[d]["cell_hi"] for d in order if st[d]["cell_hi"]]
        cells = f" Celulele sunt la {_n(min(lo), 2)}–{_n(max(hi), 2)} V." if lo and hi else ""
        if spread <= 6:
            pts.append(f"**SOC-urile sunt apropiate**: {vals}.{cells}")
        else:
            pts.append(f"**SOC-urile diferă cu {spread:.0f} puncte**: {vals}.{cells} Modulele legate în paralel au "
                       "aceeași tensiune, deci au de fapt cam același SOC; dacă diferența rămâne de la o noapte la alta, "
                       "SOC-ul arătat de unul e greșit și se poate corecta cu comanda „Setează SOC”.")

    # ---- 4. cum își împart consumul (raportat la capacitate: un modul de 280 Ah dă normal mai mult decât unul de 100 Ah)
    dis = [(d, st[d]["avg_a"], st[d]["avg_a"] / (st[d]["cap"] or 100) * 100) for d in order if st[d]["avg_a"] < -0.3 and d not in blind]
    mixed = len({round(st[d]["cap"] or 100) for d, _, _ in dis}) > 1
    if len(dis) > 1:
        hi_d, hi_a, hi_r = min(dis, key=lambda t: t[2])
        lo_d, lo_a, lo_r = max(dis, key=lambda t: t[2])
        ratio = hi_r / lo_r
        if ratio >= SHARE_BAD:
            per = " raportat la capacitate" if mixed else ""
            pts.append(f"**Modulele nu își împart consumul egal**: {nm(hi_d)} a dat ~{(ratio - 1) * 100:.0f} % mai "
                       f"mult curent{per} decât {nm(lo_d)} ({_n(hi_a)} A față de {_n(lo_a)} A), de aceea a scăzut mai mult. "
                       "Dacă diferența rămâne mare de la o noapte la alta, verifică strângerea bornelor și secțiunea "
                       f"cablurilor de la {nm(hi_d)} și {nm(lo_d)} spre bara comună.")
        else:
            pts.append("**Modulele își împart consumul aproape egal**" + (" (raportat la capacitate)" if mixed else "")
                       + ": " + ", ".join(f"{nm(d)} {_n(a)} A" for d, a, _ in dis) + ".")

    # ---- 5. echilibrul celulelor
    p10 = [st[d]["delta_p10"] for d in order if st[d]["delta_p10"] is not None]
    p90 = [st[d]["delta_p90"] for d in order if st[d]["delta_p90"] is not None]
    peaks = [(st[d]["delta_max"], d) for d in order if st[d]["delta_max"]]
    if p10 and p90:
        (mx, mts), md = max(peaks)
        normal = (f"{min(p10):.0f}–{max(p90):.0f} mV" if round(max(p90)) > round(min(p10))
                  else f"{max(p90):.0f} mV")
        peak = f" La vârfuri de consum ajunge la ~{mx:.0f} mV ({nm(md)}, la {_hm(mts)})." if mx > max(p90) + 10 else ""
        if max(p90) <= 30:
            pts.append(f"**Celulele sunt bine echilibrate**: diferența e de {normal} la consum normal.{peak}")
        elif max(p90) <= 60:
            pts.append(f"**Celulele sunt ușor dezechilibrate**: diferența e de {normal} la consum normal.{peak}")
        else:
            pts.append(f"**Celulele sunt dezechilibrate**: diferența e de {normal} la consum normal.{peak} Vezi mai "
                       "jos care celulă rămâne în urmă.")

    # ---- banca acum
    lst = {}
    for r in inv[::-1]:
        lst.setdefault(r["device"], r)
    socs = [(st[d]["soc1"], st[d]["cap"] or 100) for d in order if st[d]["soc1"] is not None]
    volts = [st[d]["voltage"] for d in order if st[d]["voltage"]]
    bank = []
    if socs:
        bank.append(f"~{sum(v * c for v, c in socs) / sum(c for _, c in socs):.0f} %")       # după capacitate
    if volts:
        bank.append(f"{_n(statistics.mean(volts))} V")
    pv_now = sum(r["pv_power"] or 0 for r in lst.values()) if lst else None
    load_now = sum(r["load_power"] or 0 for r in lst.values()) if lst else None
    text = ", ".join(bank)
    if pv_now and load_now is not None:
        text += (f", iar PV-ul acoperă consumul ({pv_now:.0f} W)" if pv_now >= load_now
                 else f", PV {pv_now:.0f} W la un consum de {load_now:.0f} W")
    active = [a for a in alerts if a["ts_end"] is None]
    text += ". " + ("Alerte active: " + "; ".join(f"„{a['message']}”" for a in active[:3]) + "."
                    if active else "Nicio alertă activă.")
    rep["bank"] = (f"Banca la {_hm(e)}: " if not night["ongoing"] else "Banca acum: ") + text

    # ---- de urmărit: o încărcare completă în ultimele 7 zile?
    full = db.samples_max_since(int(now - 7 * 86400))
    if not any((v.get("soc") or 0) >= 99 or (v.get("cell_max") or 0) >= 3.45 for d, v in full.items() if d in order):
        rep["watch"] = ("Rămâne de urmărit o încărcare completă (n-a fost niciuna în ultimele 7 zile), unde se vede cum "
                        "se comportă celulele sus și dacă echilibrarea lucrează.")
    return rep


def summary(rep: dict) -> str:
    """O frază pentru notificare."""
    first = rep["headline"].split(":")[0]
    alerts = [p for p in rep["points"] if p.startswith("**") and any(
        w in p for w in ("nu vede", "nu urmărește", "nu își împart", "diferă", "dezechilibrate"))]
    tail = f" {len(alerts)} lucru{'ri' if len(alerts) != 1 else ''} de urmărit." if alerts else " Totul arată bine."
    return first + "." + tail


def due(db, now: float | None = None) -> dict | None:
    """Noaptea de raportat dimineața (o singură dată pe zi, după ce noaptea s-a terminat), altfel None."""
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if db.get_meta(META) == day or time.localtime(now).tm_hour < 6:
        return None
    night = find_night(db, now)
    if night["ongoing"] or now - night["end"] > 6 * 3600:
        return None
    if not night["by_pv"] and time.localtime(now).tm_hour < 8:
        return None
    return night


def morning(db, notifier, names: dict[str, str], now: float | None = None) -> dict | None:
    """Din maintenance_loop: dimineața, după noapte, o notificare cu rezumatul (dacă e pornită)."""
    now = now or time.time()
    if db.get_meta("night_report_off") == "1":
        return None
    night = due(db, now)
    if not night:
        return None
    db.set_meta(META, time.strftime("%Y-%m-%d", time.localtime(now)))
    rep = build(db, names, now, night)
    if not rep["rows"]:
        return rep
    notifier.send(f"🌙 {summary(rep)}", title="Amper: raportul nopții", priority="low", url="/?v=night")
    log.info("raportul nopții trimis (%s)", rep["title"])
    return rep

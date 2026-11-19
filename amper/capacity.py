"""Capacitatea reală a fiecărui modul, în Ah, măsurată de Amper (cerută 2026-11-19).

BMS-ul spune cât i s-a scris în setări (ex. 105 Ah), nu cât mai ține bateria. Singura măsurătoare cinstită e o
descărcare de la plin până aproape de gol, cu curentul adunat pe drum (Ah):

1. Plin (ancora de sus): celula maximă ≥ `FULL_CELL` (3,44 V), sau SOC ≥ 99,5 % cu celula maximă ≥ 3,40 V.
   Măsurătoarea pornește de la ultima citire „plină” dinaintea descărcării.
2. Adunăm Ah scoși minus Ah băgați (încărcările parțiale de pe drum se scad), pe mediile la 5 minute + citirile brute.
   O pauză în date mai mare de `MAX_GAP` strică socoteala → măsurătoarea se abandonează.
3. Aproape gol (ancora de jos), cu SOC-ul rămas estimat DIN TENSIUNE, nu din BMS:
   - „cot”: celula minimă ≤ `KNEE_CELL` (2,95 V) sub sarcină moderată → ~2 % rămas;
   - „repaus”: curent mic (≤ 5 % din capacitate) și media celulelor ≤ `REST_MAX` (3,21 V) → SOC din curba de
     repaus LiFePO4 (`OCV`; sub ~15 % curba e destul de abruptă ca să conteze).
   Peste 3,21 V curba e aproape plată (3,26–3,33 V pentru 20–90 %), deci de acolo nu se poate spune nimic.
4. Capacitatea = Ah scoși / (1 − SOC rămas). Se păstrează doar descărcările de cel puțin `MIN_DEPTH` din capacitatea
   din BMS și rezultatele plauzibile (40–130 % din ea). Dintr-o descărcare se păstrează punctul cel mai de jos.

Un modul al cărui BMS nu vede curenții mici (prea multe citiri cu 0 A în timp ce banca lucrează) dă Ah greșiți:
descărcarea lui se respinge.
"""
from __future__ import annotations

import statistics
import time

from .night_report import reports_since

FULL_CELL = 3.44
FULL_SOC = 99.5
KNEE_CELL = 2.95
REST_MAX = 3.21
REST_C = 0.05             # „repaus” = curent sub 5 % din capacitate (5 A la 105 Ah)
MAX_GAP = 15 * 60
MIN_DEPTH = 0.5
ZERO_A = 0.05
ZERO_BAD = 0.2
META = "capacity_scan"    # până unde s-au analizat datele
OVERLAP = 3 * 86400       # o descărcare începută înainte de ultima analiză se reia de la capăt
# curba de repaus LiFePO4 (V pe celulă → SOC %), doar partea de jos, unde e destul de abruptă
OCV = [(2.80, 0.5), (3.00, 2.0), (3.10, 5.0), (3.15, 7.5), (3.20, 11.0), (3.21, 12.5)]
PLAUSIBLE = (0.4, 1.3)


def ocv_soc(v: float) -> float:
    if v <= OCV[0][0]:
        return OCV[0][1]
    for (v0, s0), (v1, s1) in zip(OCV, OCV[1:]):
        if v <= v1:
            return s0 + (s1 - s0) * (v - v0) / (v1 - v0)
    return OCV[-1][1]


def scan_device(rows: list[dict], rated: float, cells: int) -> list[dict]:
    """Măsurătorile dintr-un șir de citiri ale unui modul (în ordinea timpului, cu `dt`)."""
    out: list[dict] = []
    ep = None                 # descărcarea în curs: {t_full, ah, best, zero, n}
    prev_ts = None

    def close():
        if ep and ep["best"] and ep["n"] and ep["zero"] / ep["n"] < ZERO_BAD:
            out.append(ep["best"])

    for r in rows:
        cur, cmax, cmin, volt = r.get("current"), r.get("cell_max"), r.get("cell_min"), r.get("voltage")
        if cur is None:
            continue
        gap = prev_ts is not None and r["ts"] - prev_ts > MAX_GAP
        prev_ts = r["ts"]
        full = (cmax is not None and cmax >= FULL_CELL) or \
            (r.get("soc") is not None and r["soc"] >= FULL_SOC and cmax is not None and cmax >= 3.40)
        if full:
            close()
            ep = dict(t_full=r["ts"], ah=0.0, best=None, zero=0, n=0)
            continue
        if ep is None:
            continue
        if gap:
            close()
            ep = None
            continue
        ep["ah"] -= cur * min(r["dt"], MAX_GAP) / 3600       # + = scos din modul
        ep["n"] += 1
        ep["zero"] += abs(cur) < ZERO_A
        soc_low, method = None, None
        if cmin is not None and cmin <= KNEE_CELL and abs(cur) <= 0.5 * rated:
            soc_low, method = 2.0, "knee"
        elif volt and cells and abs(cur) <= REST_C * rated and volt / cells <= REST_MAX:
            soc_low, method = ocv_soc(volt / cells), "rest"
        if soc_low is None or ep["ah"] < MIN_DEPTH * rated:
            continue
        cap = ep["ah"] / (1 - soc_low / 100)
        if not PLAUSIBLE[0] * rated <= cap <= PLAUSIBLE[1] * rated:
            continue
        if ep["best"] is None or soc_low <= ep["best"]["soc_low"]:
            ep["best"] = dict(t_full=ep["t_full"], ts=r["ts"], ah=round(ep["ah"], 1), soc_low=round(soc_low, 1),
                              cap=round(cap, 1), method=method)
    close()
    return out


def scan(db, packs: dict[str, dict], now: float | None = None) -> int:
    """Analizează datele noi (de la ultima analiză, cu `OVERLAP` în urmă) și salvează măsurătorile. Câte a găsit."""
    now = int(now or time.time())
    last = int(float(db.get_meta(META) or 0))
    start = max(last - OVERLAP, reports_since(db))
    rows = db.pack_series(start, now, cols="ts, device, current, soc, voltage, cell_min, cell_max")
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["device"], []).append(r)
    found = 0
    for dev, p in packs.items():
        if not p.get("rated"):
            continue
        for ev in scan_device(sorted(by.get(dev, []), key=lambda r: r["ts"]), float(p["rated"]), p.get("cells") or 16):
            db.save_capacity(dict(ev, device=dev))
            found += 1
    db.set_meta(META, str(now))
    return found


def analyze(db, packs: dict[str, dict], now: float | None = None) -> dict:
    """Pe modul: ultima măsurătoare, media ultimelor 3, pe luni, procentul din capacitatea din BMS; plus cât de
    aproape a fost de o măsurătoare de la ultima încărcare completă (dacă n-are încă niciuna)."""
    now = now or time.time()
    since = reports_since(db)
    evs = [e for e in db.capacity_events() if e["t_full"] >= since]
    mods = []
    for dev, p in packs.items():
        mine = [e for e in evs if e["device"] == dev]
        rated = p.get("rated")
        m = dict(id=dev, name=p.get("name", dev), rated=rated, count=len(mine), last=mine[-1] if mine else None)
        if mine:
            recent = [e["cap"] for e in mine[-3:]]
            m["cap"] = round(statistics.median(recent), 1)
            m["pct"] = round(m["cap"] / rated * 100) if rated else None
            months: dict[str, list] = {}
            for e in mine:
                months.setdefault(time.strftime("%Y-%m", time.localtime(e["ts"])), []).append(e["cap"])
            m["months"] = [dict(month=k, cap=round(statistics.median(v), 1), n=len(v)) for k, v in sorted(months.items())]
            m["spread"] = round(max(recent) - min(recent), 1) if len(recent) > 1 else None
        mods.append(m)
    return dict(modules=mods, since=since or None, rules=dict(full_cell=FULL_CELL, rest_max=REST_MAX,
                                                               knee=KNEE_CELL, min_depth=int(MIN_DEPTH * 100)))


def progress(db, packs: dict[str, dict], now: float | None = None) -> dict[str, dict]:
    """Pentru modulele fără măsurători: ultima încărcare completă și cât de jos au coborât de atunci (din citirile
    brute, ultimele 14 zile)."""
    now = int(now or time.time())
    rows = db.pack_series(max(now - 14 * 86400, reports_since(db)), now,
                          cols="ts, device, current, soc, voltage, cell_min, cell_max")
    out: dict[str, dict] = {}
    for dev, p in packs.items():
        mine = sorted((r for r in rows if r["device"] == dev), key=lambda r: r["ts"])
        cells = p.get("cells") or 16
        last_full = None
        for r in mine:
            if (r.get("cell_max") or 0) >= FULL_CELL or ((r.get("soc") or 0) >= FULL_SOC and (r.get("cell_max") or 0) >= 3.40):
                last_full = r["ts"]
        low = None
        if last_full:
            after = [r for r in mine if r["ts"] > last_full and r.get("voltage")]
            if after:
                lo = min(after, key=lambda r: r["voltage"])
                low = dict(ts=lo["ts"], cell=round(lo["voltage"] / cells, 3), soc=lo.get("soc"))
        out[dev] = dict(last_full=last_full, low=low)
    return out

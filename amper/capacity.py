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

import json
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


REPORT_META = "capacity_report"   # raportul gata calculat (ecranul „Capacitatea reală” doar îl afișează)


def report(db, packs: dict[str, dict], now: float | None = None) -> dict:
    """Tot ce arată ecranul, calculat în fundal: datele noi analizate, măsurătorile, cât de aproape e de una,
    comparația cu celelalte module. Salvat în meta, ca să fie gata și după o repornire."""
    now = now or time.time()
    scan(db, packs, now)
    res = analyze(db, packs, now)
    prog = progress(db, packs, now)
    learn = learned(db, packs, now)
    for m in res["modules"]:
        m["progress"] = prog.get(m["id"])
        m["learned"] = learn.get(m["id"])
    res["at"] = int(now)
    res["packs"] = sorted(packs)
    db.set_meta(REPORT_META, json.dumps(res))
    return res


def cached_report(db) -> dict | None:
    try:
        return json.loads(db.get_meta(REPORT_META) or "null")
    except ValueError:
        return None


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


# ---------------- comparația dintre module (în fiecare noapte) ----------------
REL_NIGHTS_DAYS = 14         # citirile brute (raw_retention_days)
REL_MIN_AH = 3.0             # o noapte cu mai puțin de atât descărcat pe modul nu spune nimic
REL_MIN_NIGHTS = 3


def relative(db, packs: dict[str, dict], now: float | None = None) -> dict[str, dict]:
    """Modulele sunt în paralel, deci au aceeași tensiune și coboară cam la fel în SOC: Ah-ii dați de fiecare într-o
    noapte sunt proporționali cu capacitatea lui reală. Ca să se poată compara și module de mărimi diferite (Daly
    105 Ah cu Seplos 280 Ah), Ah-ii se împart la capacitatea din BMS: `rel` = (Ah / capacitatea din BMS) al modulului
    față de media modulelor (mediana nopților); 1,0 = exact cât ar trebui, 0,8 = 20 % mai puțin decât ceilalți.
    Nopțile în care un modul n-a văzut curenții mici (prea multe citiri cu 0 A) nu intră."""
    from . import battery_report, night_report as nr
    now = now or time.time()
    start = max(now - REL_NIGHTS_DAYS * 86400, reports_since(db))
    packs = {d: p for d, p in packs.items() if p.get("rated")}
    ratios: dict[str, list] = {d: [] for d in packs}
    for n in battery_report.nights_in(db, start, now):
        rows = nr.night_rows(db, {d: p.get("name", d) for d, p in packs.items()}, int(n["start"]), int(n["end"]))
        stats = {d: nr.pack_stats(rows.get(d, [])) for d in packs}
        good = {d: -x["ah"] / packs[d]["rated"] for d, x in stats.items()
                if x and x["zero"] < nr.ZERO_BAD and -x["ah"] >= REL_MIN_AH}
        if len(good) < 2 or len(good) < len(packs):
            continue
        mean = sum(good.values()) / len(good)
        for d, ah in good.items():
            ratios[d].append(ah / mean)
    return {d: dict(rel=round(statistics.median(v), 3) if len(v) >= REL_MIN_NIGHTS else None, nights=len(v))
            for d, v in ratios.items()}


def learned(db, packs: dict[str, dict], now: float | None = None) -> dict[str, dict]:
    """Capacitatea folosită de SOC-ul calculat de Amper, pe modul: {cap, kind, rel, nights}.
    measured = descărcare adâncă / test (mediana ultimelor 3); relative = un alt modul măsurat × raportul dintre ele;
    altfel nimic (rămâne cea din BMS / config)."""
    a = {m["id"]: m for m in analyze(db, packs, now)["modules"]}
    rel = relative(db, packs, now)
    out = {}
    refs = [(d, m) for d, m in a.items() if m.get("cap") and (rel.get(d) or {}).get("rel")]
    ref = max(refs, key=lambda x: x[1]["count"]) if refs else None
    for d in packs:
        r = rel.get(d) or {}
        o = dict(rel=r.get("rel"), nights=r.get("nights", 0), cap=None, kind=None)
        if a.get(d, {}).get("cap"):
            o.update(cap=a[d]["cap"], kind="measured")
        elif ref and r.get("rel") and packs[d].get("rated") and packs[ref[0]].get("rated"):
            # capacitatea reală ∝ capacitatea din BMS × rel; scara o dă modulul măsurat
            o.update(cap=round(ref[1]["cap"] * (packs[d]["rated"] * r["rel"]) /
                               (packs[ref[0]]["rated"] * rel[ref[0]]["rel"]), 1), kind="relative")
        out[d] = o
    return out


# ---------------- testul de capacitate (ghidat, o noapte) ----------------
TEST_META = "capacity_test"
TEST_FULL_WITHIN = 3 * 3600          # toate modulele încărcate complet în ultimele 3 ore
TEST_MAX_S = 36 * 3600
TEST_THRESHOLDS = (3.10, 3.05, 3.00, 2.95)
TEST_HOLD = 3                        # citiri la rând sub prag (nu un vârf de consum)


def test_state(db) -> dict | None:
    try:
        return json.loads(db.get_meta(TEST_META) or "null")
    except ValueError:
        return None


def test_start(db, est, packs: dict[str, dict], threshold: float, now: float | None = None) -> dict:
    now = now or time.time()
    if threshold not in TEST_THRESHOLDS:
        raise ValueError("Prag necunoscut.")
    st = test_state(db)
    if st and st.get("state") == "running":
        raise ValueError("Un test e deja în curs.")
    late = [p.get("name", d) for d, p in packs.items()
            if not (est.st.get(d) or {}).get("anchored") or now - ((est.st.get(d) or {}).get("full_ts") or 0) > TEST_FULL_WITHIN]
    if late:
        raise ValueError("Testul pornește doar cu bateria plină: " + ", ".join(late) +
                         " n-au fost încărcate complet în ultimele 3 ore.")
    st = dict(state="running", started=now, threshold=threshold, low_count=0, result=None, reason=None,
              full_ts={d: est.st[d]["full_ts"] for d in packs})
    db.set_meta(TEST_META, json.dumps(st))
    return st


def test_stop(db, reason: str = "oprit din aplicație", now: float | None = None) -> dict | None:
    st = test_state(db)
    if st and st.get("state") == "running":
        st.update(state="cancelled", reason=reason, ended=now or time.time())
        db.set_meta(TEST_META, json.dumps(st))
    return st


def test_tick(db, est, packs: dict[str, dict], readings: dict[str, object], notifier=None,
              now: float | None = None) -> dict | None:
    """Din tick-ul motorului: oprește testul la prag (și salvează capacitățile) sau îl anulează dacă bateria s-a
    încărcat complet între timp / a trecut prea mult."""
    st = test_state(db)
    if not st or st.get("state") != "running":
        return None
    now = now or time.time()
    for d in packs:
        s = est.st.get(d) or {}
        if s.get("full_ts") and st["full_ts"].get(d) and s["full_ts"] > st["full_ts"][d] + 60:
            st.update(state="cancelled", reason="bateria s-a încărcat complet în timpul testului", ended=now)
            db.set_meta(TEST_META, json.dumps(st))
            return st
    if now - st["started"] > TEST_MAX_S:
        st.update(state="cancelled", reason="a trecut prea mult timp (36 de ore)", ended=now)
        db.set_meta(TEST_META, json.dumps(st))
        return st
    lows = [min(r.cells) for r in readings.values() if getattr(r, "cells", None)]
    before = st.get("low_count", 0)
    st["low_count"] = before + 1 if lows and min(lows) <= st["threshold"] else 0
    if st["low_count"] < TEST_HOLD:
        if st["low_count"] != before:                 # scriem pe card doar când se schimbă ceva
            db.set_meta(TEST_META, json.dumps(st))
        return st
    # gata: Ah scoși de la plin, pe fiecare modul; SOC-ul rămas după tensiunea celulelor (sub sarcină)
    res = {}
    for d, p in packs.items():
        s, r = est.st.get(d) or {}, readings.get(d)
        if not s or r is None or not getattr(r, "cells", None) or not p.get("rated"):
            continue
        low = min(ocv_soc(min(r.cells)), 8.0)
        cap = s["ah_out"] / (1 - low / 100)
        res[d] = dict(ah=round(s["ah_out"], 1), soc_low=round(low, 1), cap=round(cap, 1),
                      pct=round(cap / p["rated"] * 100))
        if PLAUSIBLE[0] * p["rated"] <= cap <= PLAUSIBLE[1] * p["rated"]:
            db.save_capacity(dict(device=d, t_full=int(st["full_ts"][d]), ts=int(now), ah=res[d]["ah"],
                                  soc_low=res[d]["soc_low"], cap=res[d]["cap"], method="test"))
    st.update(state="done", ended=now, result=res)
    db.set_meta(TEST_META, json.dumps(st))
    if notifier is not None:
        parts = "; ".join(f"{packs[d].get('name', d)} {v['cap']:.0f} Ah ({v['pct']} %)" for d, v in res.items())
        thr = f"{st['threshold']:.2f}".replace(".", ",")
        notifier.send(f"🔋 Testul de capacitate s-a terminat (celulele au ajuns la {thr} V): {parts}. "
                      "Pornește acum încărcarea (generatorul).",
                      title="Amper: test de capacitate", priority="high", url="/?v=capacity")
    return st

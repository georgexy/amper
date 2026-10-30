"""Raportul bateriilor pe o perioadă aleasă (2 zile, o săptămână, o lună…): text + un tabel pe module.

Surse: mediile la 5 minute (toată istoria) pentru Ah, SOC min/max, cicluri, temperaturi; citirile brute (ultimele
`raw_retention_days`, 14 zile) pentru diferența maximă a celulelor, MOSFET-uri, pauze de comunicație și pene; fiecare
noapte din perioadă (tot din citirile brute) analizată ca în raportul nopții (`night_report`), apoi numărată: în câte
nopți a numărat corect fiecare BMS, în câte n-a văzut curenții mici, cum și-au împărțit modulele consumul.
"""
from __future__ import annotations

import statistics
import time

from . import night_report as nr

MAX_DAYS = 400
MAX_NIGHTS = 31


def _day(ts: float) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def _date(ts: float) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def nights_in(db, start: float, end: float) -> list[dict]:
    """Nopțile încheiate din perioadă: câte una pe fiecare dimineață (după PV, ca la raportul nopții)."""
    out, seen = [], set()
    t = time.localtime(start)
    noon = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))
    while noon <= end + 12 * 3600 and len(out) < MAX_NIGHTS:
        n = nr.find_night(db, min(noon, end))
        key = int(n["end"]) // 3600
        if not n["ongoing"] and n["start"] >= start - 3600 and n["end"] <= end + 60 and key not in seen:
            seen.add(key)
            out.append(n)
        noon += 86400
    return out


def build(db, names: dict[str, str], start: float, end: float, inverters: list[str] | None = None,
          now: float | None = None) -> dict:
    now = now or time.time()
    end = min(end, now)
    start = max(start, end - MAX_DAYS * 86400)
    s, e = int(start), int(end)
    days = max(1, round((e - s) / 86400))
    nm = lambda d: names.get(d, d)          # noqa: E731
    rep = dict(start=s, end=e, title=f"{_date(s)} → {_date(e)}", rows=[], points=[], commands=[], headline="",
               period="", watch="", ok=True, detail_from=None, nights=0)

    # ---------- totaluri din mediile la 5 minute
    agg: dict[str, dict] = {}
    for r in db.pack_series(s, e):
        if r["device"] not in names:
            continue
        a = agg.setdefault(r["device"], dict(chg=0.0, dis=0.0, soc_min=None, soc_max=None, caps=[], temp=None,
                                             full_days=set(), dmax=None))
        c, dt = r["current"], r["dt"]
        if c is not None:
            if c > 0:
                a["chg"] += c * dt / 3600
            else:
                a["dis"] -= c * dt / 3600
        if r["soc"] is not None:
            if a["soc_min"] is None or r["soc"] < a["soc_min"][0]:
                a["soc_min"] = (r["soc"], r["ts"])
            if a["soc_max"] is None or r["soc"] > a["soc_max"][0]:
                a["soc_max"] = (r["soc"], r["ts"])
            if r["soc"] >= 99:
                a["full_days"].add(time.strftime("%Y-%m-%d", time.localtime(r["ts"])))
            if r["remaining_ah"] and r["soc"] >= 10:
                a["caps"].append(r["remaining_ah"] / (r["soc"] / 100))
        if r["temp_max"] is not None:
            a["temp"] = max(a["temp"] or r["temp_max"], r["temp_max"])
        if r["cell_delta"] is not None:
            a["dmax"] = max(a["dmax"] or 0, r["cell_delta"])
    order = [d for d in names if d in agg]
    if not order:
        rep["headline"] = "Nu sunt citiri ale bateriilor în această perioadă."
        return rep

    # ---------- detaliile din citirile brute (ultimele 14 zile)
    det = db.pack_detail(s, e)
    rep["detail_from"] = det["from"]
    for d in order:
        dd = det["devices"].get(d)
        if dd and dd["dmax"] is not None:
            agg[d]["dmax"], agg[d]["dmax_ts"] = dd["dmax"], dd["dmax_ts"]

    for d in order:
        a = agg[d]
        cap = statistics.median(a["caps"]) if a["caps"] else None
        a["cap"] = cap
        rep["rows"].append(dict(
            device=d, name=nm(d), soc_min=a["soc_min"][0] if a["soc_min"] else None,
            soc_max=a["soc_max"][0] if a["soc_max"] else None, dis_ah=round(a["dis"], 1), chg_ah=round(a["chg"], 1),
            cycles=round(a["dis"] / cap, 1) if cap else None,
            dmax=round(a["dmax"] * 1000) if a["dmax"] is not None else None,
            temp=round(a["temp"]) if a["temp"] is not None else None))

    # ---------- ce s-a întâmplat
    issues = []
    alerts = db.alerts_between(s, e)
    prot: dict[str, list[int]] = {}
    for al in alerts:
        if al["rule"] == "bms_protection" and s <= al["ts_start"] <= e:
            prot.setdefault(al["message"], []).append(al["ts_start"])
    for msg, ts in sorted(prot.items(), key=lambda kv: -len(kv[1])):
        issues.append(f"{msg} — {len(ts)} {'dată' if len(ts) == 1 else 'ori'}, ultima {_day(max(ts))}")
    for d in order:
        dd = det["devices"].get(d)
        if not dd:
            continue
        for col, what in (("dsg_off", "descărcare"), ("chg_off", "încărcare")):
            if (dd[col] or 0) >= 60:
                issues.append(f"MOSFET-ul de {what} al {nm(d)} a stat oprit în total {nr._dur(dd[col])}")
        if dd["gaps"] and dd["gap_at"]:
            a_, b_ = dd["gap_at"]
            issues.append(f"{nm(d)} n-a comunicat de {dd['gaps']} ori, cel mai mult {nr._dur(b_ - a_)} ({_day(a_)})")
    holes = det["holes"]
    if holes:
        a_, b_ = max(holes, key=lambda h: h[1] - h[0])
        issues = [i for i in issues if "n-a comunicat" not in i]
        issues.append(f"Pi-ul n-a înregistrat date de {len(holes)} {'dată' if len(holes) == 1 else 'ori'} "
                      f"(cel mai mult {nr._dur(b_ - a_)}, {_day(a_)}) — probabil pene")
    if issues:
        rep["ok"] = False
        rep["headline"] = f"În aceste {days} zile au fost probleme: " + "; ".join(issues) + "."
    else:
        rep["headline"] = (f"În aceste {days} zile nu au fost probleme: nicio protecție, MOSFET-urile au rămas pornite, "
                           "iar Pi-ul a funcționat fără întrerupere.")

    for c in db.settings_log_between(s, e):
        if c["ok"] and c["device"] in names:
            rep["commands"].append(f"{nm(c['device'])}: {c['setting'].removeprefix('comandă: ')} "
                                   f"{c['old'] or ''} → {c['new'] or ''} ({_day(c['ts'])})")

    # ---------- nopțile din perioadă, analizate pe rând
    per_night = []
    for n in nights_in(db, s, e):
        rows = nr.night_rows(db, names, int(n["start"]), int(n["end"]))
        st = {d: x for d, r in rows.items() if (x := nr.pack_stats(r))}
        if st:
            per_night.append((n, st))
    rep["nights"] = len(per_night)
    pts = rep["points"]
    if per_night:
        # 1. numără corect?
        right, wrong = {}, {}
        for _, st in per_night:
            for d, x in st.items():
                ok = nr.counts_right(x)
                if ok is not None:
                    (right if ok else wrong)[d] = (right if ok else wrong).get(d, 0) + 1
        bad = [d for d in order if wrong.get(d)]
        if bad:
            pts.append("**SOC-ul nu urmărește mereu curentul**: " + "; ".join(
                f"{nm(d)} a numărat greșit în {wrong[d]} din {wrong[d] + right.get(d, 0)} nopți" for d in bad)
                + ". În acele nopți SOC-ul a scăzut altfel decât arată curentul măsurat.")
        elif right:
            pts.append(f"**Toate modulele au numărat corect** în toate nopțile verificate ({len(per_night)}).")
        # 2. curenții mici
        for d in order:
            zn = [n for n, st in per_night if d in st and st[d]["zero"] >= nr.ZERO_BAD]
            if zn:
                pts.append(f"**{nm(d)} n-a văzut curenții mici în {len(zn)} din {len(per_night)} nopți** (0 A la peste "
                           f"{nr.ZERO_BAD * 100:.0f} % din citiri; ultima noapte așa: {_date(zn[-1]['end'])}).")
        # 3. cum își împart consumul (media nopților, raportat la capacitate)
        norm: dict[str, list[float]] = {}
        for _, st in per_night:
            for d, x in st.items():
                if x["zero"] < nr.ZERO_BAD and x["avg_a"] < -0.3:
                    norm.setdefault(d, []).append(x["avg_a"] / (x["cap"] or agg[d]["cap"] or 100) * 100)
        avg = {d: statistics.mean(v) for d, v in norm.items()}
        if len(avg) > 1:
            hi = min(avg, key=avg.get)
            lo = max(avg, key=avg.get)
            ratio = avg[hi] / avg[lo]
            if ratio >= nr.SHARE_BAD:
                pts.append(f"**Modulele nu își împart consumul egal**: noaptea, {nm(hi)} a dat în medie "
                           f"~{(ratio - 1) * 100:.0f} % mai mult curent (raportat la capacitate) decât {nm(lo)}. "
                           f"Verifică strângerea bornelor și secțiunea cablurilor de la {nm(hi)} și {nm(lo)}.")
            else:
                pts.append("**Modulele își împart consumul aproape egal** (media nopților, raportat la capacitate).")
        # 5. celulele: diferența la consum normal, la început și la sfârșit
        p90 = [max(x["delta_p90"] for x in st.values() if x["delta_p90"] is not None)
               for _, st in per_night if any(x["delta_p90"] is not None for x in st.values())]
        if len(p90) >= 4:
            k = len(p90) // 3 or 1
            first, last = statistics.mean(p90[:k]), statistics.mean(p90[-k:])
            if last - first >= 10:
                pts.append(f"**Diferența dintre celule a crescut**: la consum normal, de la ~{first:.0f} mV la "
                           f"~{last:.0f} mV. Vezi mai jos, la Sănătatea celulelor, care celulă rămâne în urmă.")
            elif first - last >= 10:
                pts.append(f"**Diferența dintre celule a scăzut**: la consum normal, de la ~{first:.0f} mV la "
                           f"~{last:.0f} mV — echilibrarea lucrează.")
            else:
                pts.append(f"**Diferența dintre celule a rămas aceeași**: ~{statistics.mean(p90):.0f} mV la consum "
                           "normal.")

    # 4. încărcări complete și cât de jos a coborât
    full_all = set.intersection(*(agg[d]["full_days"] for d in order)) if order else set()
    low = min((agg[d]["soc_min"] + (d,) for d in order if agg[d]["soc_min"]), default=None)
    text = (f"**Banca s-a încărcat complet în {len(full_all)} din {days} zile**" if full_all
            else f"**Banca nu s-a încărcat complet în aceste {days} zile**")
    if low:
        text += f"; cel mai jos a coborât {nm(low[2])}, la {nr._n(low[0])} % ({_day(low[1])})"
    pts.append(text + ".")
    worst = max((agg[d]["dmax"], d) for d in order if agg[d]["dmax"] is not None) if any(
        agg[d]["dmax"] is not None for d in order) else None
    if worst:
        at = agg[worst[1]].get("dmax_ts")
        pts.append(f"Cea mai mare diferență între celule: **{worst[0] * 1000:.0f} mV** la {nm(worst[1])}"
                   + (f" ({_day(at)})." if at else "."))

    # 6. energia
    if inverters:
        tot = dict(pv_kwh=0.0, load_kwh=0.0, ac_in_kwh=0.0)
        for inv in inverters:
            for r in db.energy_range(inv, time.strftime("%Y-%m-%d", time.localtime(s)),
                                     time.strftime("%Y-%m-%d", time.localtime(e))):
                for k in tot:
                    tot[k] += r.get(k) or 0
        if any(tot.values()):
            rep["period"] = (f"Panourile au produs {nr._n(tot['pv_kwh'])} kWh, casa a consumat "
                             f"{nr._n(tot['load_kwh'])} kWh" + (f", din rețea {nr._n(tot['ac_in_kwh'])} kWh"
                                                              if tot["ac_in_kwh"] >= 0.1 else "") + ".")
    if not full_all:
        rep["watch"] = ("Rămâne de urmărit o încărcare completă, unde se vede cum se comportă celulele sus și dacă "
                        "echilibrarea lucrează.")
    if det["from"] and det["from"] > s + 3600:
        rep["note"] = (f"Citirile detaliate (la 10 secunde) se păstrează 14 zile: nopțile, MOSFET-urile, pauzele și "
                       f"diferența maximă a celulelor sunt analizate doar de la {_date(det['from'])}; restul din "
                       "mediile la 5 minute.")
    elif not det["from"]:
        rep["note"] = ("Pentru această perioadă există doar mediile la 5 minute: nopțile, MOSFET-urile și pauzele "
                       "nu se mai pot analiza.")
    return rep

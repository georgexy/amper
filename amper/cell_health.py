"""Sănătatea celulelor: urmărește fiecare celulă și anunță o dată pe săptămână dacă una rămâne mereu în urmă sau o ia
mereu înainte (semnul timpuriu al unei celule care se degradează).

La LiFePO4 diferențele dintre celule se văd doar la capete: în zona de mijloc (~3,25–3,35 V) curba e plată și toate
celulele arată la fel, oricât de diferite ar fi. De aceea se salvează doar momentele care spun ceva:
  - „chg”: final de încărcare (se încarcă și o celulă a trecut de KNEE_HI): cine o ia înainte = capacitate mai mică;
  - „dis”: descărcare adâncă (o celulă sub KNEE_LO): cine rămâne în urmă = cea mai slabă;
  - „ir”: salt de curent (≥ IR_STEP_A în câteva secunde): căderea de tensiune a fiecărei celule ÷ saltul de curent
    = rezistența internă (în mΩ, cu tot cu legăturile — utilă la comparat celulele între ele, nu ca valoare absolută).
Un „episod” chg/dis (condiția ținută, cu pauze sub EPISODE_GAP) dă un singur rând: momentul cel mai extrem.
Rândurile se țin în tabela `cell_events` (abaterile față de media modulului, în mV, sau rezistențele, în mΩ).
"""
from __future__ import annotations

import json
import logging
import statistics
import time

log = logging.getLogger("amper.cell_health")

KNEE_HI = 3.40          # V — peste el, la încărcare, celulele se despart
KNEE_LO = 3.10          # V — sub el, la descărcare, celulele se despart
CHG_MIN_A = 1.0         # A — „se încarcă”
EPISODE_GAP = 3600      # s — pauză după care începe un episod nou
IR_STEP_A = 15.0        # A — salt minim de curent pentru rezistență
IR_MAX_DT = 30          # s — cele două citiri trebuie să fie apropiate
IR_EVERY = 300          # s — cel mult o măsurătoare de rezistență la 5 min, pe modul
WINDOW_DAYS = 28        # analiza: ultimele 4 săptămâni
TREND_WEEKS = 8
MIN_EVENTS = 3          # sub atât, metrica nu contează (date insuficiente)
KEEP_DAYS = 120
WEEK = 7 * 86400


class Tracker:
    """Primește citirile modulelor (din Engine) și salvează momentele utile."""

    def __init__(self, db):
        self.db = db
        self.episode: dict[tuple[str, str], dict] = {}     # (device, kind) → {id, last, extreme}
        self.prev: dict[str, tuple[float, float, list[float]]] = {}   # device → (ts, current, cells)
        self.last_ir: dict[str, float] = {}

    def observe(self, device: str, r, now: float | None = None) -> None:
        cells = list(r.cells or [])
        if len(cells) < 4 or (r.raw or {}).get("cells_age") is not None:
            return                                   # celule lipsă sau vechi: nu se folosesc
        now = now or r.ts or time.time()
        cur = r.current or 0.0
        avg = sum(cells) / len(cells)
        dev = [round((v - avg) * 1000, 1) for v in cells]
        if cur > CHG_MIN_A and max(cells) >= KNEE_HI:
            self._episode(device, "chg", now, dev, max(cells))
        if min(cells) <= KNEE_LO and cur <= CHG_MIN_A:
            self._episode(device, "dis", now, dev, -min(cells))
        prev = self.prev.get(device)
        if prev and len(prev[2]) == len(cells) and 0 < now - prev[0] <= IR_MAX_DT \
                and abs(cur - prev[1]) >= IR_STEP_A and now - self.last_ir.get(device, 0) >= IR_EVERY:
            di = cur - prev[1]
            ir = [round((v - p) / di * 1000, 3) for v, p in zip(cells, prev[2])]   # mΩ; + = rezistență
            if all(x > 0 for x in ir):
                self.db.add_cell_event(int(now), device, "ir", json.dumps(ir))
                self.last_ir[device] = now
        self.prev[device] = (now, cur, cells)

    def _episode(self, device: str, kind: str, now: float, dev: list[float], extreme: float) -> None:
        ep = self.episode.get((device, kind))
        if ep and now - ep["last"] <= EPISODE_GAP:
            ep["last"] = now
            if extreme > ep["extreme"]:              # păstrăm momentul cel mai extrem al episodului
                ep["extreme"] = extreme
                self.db.update_cell_event(ep["id"], int(now), json.dumps(dev))
            return
        rid = self.db.add_cell_event(int(now), device, kind, json.dumps(dev))
        self.episode[(device, kind)] = {"id": rid, "last": now, "extreme": extreme}


# ---------------- analiza ----------------
def _num(v: float, d: int = 0) -> str:
    return f"{v:.{d}f}".replace(".", ",")


def _de(n: int) -> str:
    """„28 de descărcări”, dar „10 descărcări” (româna cere „de” de la 20 în sus, mai puțin la 101–119 etc.)."""
    return f"{n} de" if n >= 20 and not 1 <= n % 100 <= 19 else str(n)


def analyze(db, names: dict[str, str], now: float | None = None) -> dict:
    """Pe fiecare celulă: abaterea mediană la final de încărcare și la descărcare (mV), de câte ori a fost cea mai sus /
    cea mai jos, rezistența față de celelalte (%), scorul 0–100 și evoluția pe săptămâni. Celulele cu scor sub 90,
    cele mai slabe primele, în `cells`; restul numărate în `ok_count`. `modules` = câte momente are fiecare modul
    (`ready` = are destule pentru măsurătoare, altfel celulele lui nu intră încă în analiză)."""
    now = now or time.time()
    since = now - TREND_WEEKS * WEEK
    rows = db.cell_events(int(since))
    by: dict[str, dict[str, list]] = {}
    for r in rows:
        by.setdefault(r["device"], {}).setdefault(r["kind"], []).append((r["ts"], json.loads(r["vals"])))
    out, ok_scores, totals, mods = [], [], {"chg": 0, "dis": 0, "ir": 0}, {}
    win = now - WINDOW_DAYS * 86400
    for dev, kinds in by.items():
        n = max((len(v) for evs in kinds.values() for _, v in evs), default=0)
        recent = {k: [v for t, v in evs if t >= win and len(v) == n] for k, evs in kinds.items()}
        for k in totals:
            totals[k] += len(recent.get(k, []))
        chg, dis, ir = recent.get("chg", []), recent.get("dis", []), recent.get("ir", [])
        mods[dev] = dict(device=dev, name=names.get(dev, dev), cells=n, chg=len(chg), dis=len(dis), ir=len(ir),
                         ready=max(len(chg), len(dis), len(ir)) >= MIN_EVENTS)
        ir_med = statistics.median([statistics.median(e) for e in ir]) if len(ir) >= MIN_EVENTS else None
        for i in range(n):
            c = dict(device=dev, name=names.get(dev, dev), cell=i + 1, chg=None, dis=None, chg_top=None,
                     dis_low=None, ir_rel=None)
            if len(chg) >= MIN_EVENTS:
                c["chg"] = statistics.median(e[i] for e in chg)
                c["chg_top"] = [sum(1 for e in chg if e[i] == max(e)), len(chg)]
            if len(dis) >= MIN_EVENTS:
                c["dis"] = statistics.median(e[i] for e in dis)
                c["dis_low"] = [sum(1 for e in dis if e[i] == min(e)), len(dis)]
            if ir_med:
                c["ir_rel"] = round(statistics.median(e[i] for e in ir) / ir_med * 100 - 100)
            c["score"] = score(c)
            c["level"] = 2 if c["score"] < 70 else 1 if c["score"] < 90 else 0
            c["trend"] = trend(kinds, i, n, now)
            if c["level"]:
                out.append(c)
            elif c["chg"] is not None or c["dis"] is not None or c["ir_rel"] is not None:
                ok_scores.append(c["score"])
    out.sort(key=lambda c: c["score"])
    for c in out:
        c["text"] = describe(c)
    order = list(names)
    return dict(cells=out, ok_count=len(ok_scores),
                modules=sorted(mods.values(), key=lambda m: order.index(m["device"]) if m["device"] in order else 99), ok_range=[min(ok_scores), max(ok_scores)] if ok_scores else None,
                events=totals, window_days=WINDOW_DAYS, ts=int(now),
                enough=totals["chg"] >= MIN_EVENTS or totals["dis"] >= MIN_EVENTS)


def score(c: dict) -> int:
    """100 = la fel ca celelalte. Abaterea (mV, peste 5 mV) și rezistența mai mare (%, peste 5%) scad scorul."""
    dev = max(abs(c["chg"] or 0), abs(c["dis"] or 0))
    pen = max(0.0, dev - 5) * 0.6 + max(0.0, (c["ir_rel"] or 0) - 5) * 0.4
    for hit in (c["chg_top"], c["dis_low"]):
        if hit and hit[1] >= MIN_EVENTS and hit[0] / hit[1] >= 0.7 and dev >= 10:
            pen += 5                                  # mereu aceeași celulă la capăt: semn mai sigur
    return max(0, min(100, round(100 - pen)))


def trend(kinds: dict, i: int, n: int, now: float) -> list[float | None]:
    """Abaterea (mV, în valoare absolută) a celulei în fiecare din ultimele TREND_WEEKS săptămâni."""
    out = []
    for w in range(TREND_WEEKS, 0, -1):
        lo, hi = now - w * WEEK, now - (w - 1) * WEEK
        vals = [abs(v[i]) for k in ("chg", "dis") for t, v in kinds.get(k, []) if lo <= t < hi and len(v) == n]
        out.append(round(statistics.median(vals), 1) if vals else None)
    return out


def describe(c: dict) -> str:
    """„Daly 2 · celula 7 rămâne în urmă: cea mai jos la 9 din ultimele 10 descărcări, cu 46 mV sub medie…”"""
    parts = []
    behind = (c["dis"] or 0) <= -abs(c["chg"] or 0)
    if c["dis"] is not None and c["dis"] < -5:
        low = c["dis_low"]
        parts.append((f"cea mai jos la {low[0]} din ultimele {_de(low[1])} descărcări, " if low and low[0] else "la descărcare ")
                     + f"cu {_num(-c['dis'])} mV sub medie")
    if c["chg"] is not None and c["chg"] > 5:
        top = c["chg_top"]
        parts.append((f"cea mai sus la {top[0]} din ultimele {_de(top[1])} încărcări complete, " if top and top[0]
                      else "la final de încărcare ") + f"cu {_num(c['chg'])} mV peste medie")
    tr = [x for x in c["trend"] if x is not None]
    if len(tr) >= 4 and tr[-1] - tr[0] >= 10:
        parts.append(f"diferența a crescut de la {_num(tr[0])} la {_num(tr[-1])} mV")
    if c["ir_rel"] is not None and c["ir_rel"] >= 15:
        parts.append(f"rezistența internă e cu {c['ir_rel']}% mai mare decât a celorlalte")
    what = "rămâne în urmă" if behind else "o ia înainte" if (c["chg"] or 0) > 5 else "se abate de la celelalte"
    return f"{c['name']} · celula {c['cell']} {what}" + (": " + "; ".join(parts) if parts else "") + "."


# ---------------- notificarea săptămânală ----------------
META = "cell_health_last"


def weekly_due(db, now: float | None = None) -> bool:
    """O dată pe săptămână, luni după ora 9 (ora Pi-ului)."""
    now = now or time.time()
    last = float(db.get_meta(META) or 0)
    t = time.localtime(now)
    return now - last >= 6 * 86400 and t.tm_wday == 0 and t.tm_hour >= 9


def weekly(db, notifier, names: dict[str, str], now: float | None = None) -> dict:
    """Analiza săptămânală: notifică doar dacă o celulă e de urmărit, sau ușor diferită dar mereu aceeași la capăt."""
    now = now or time.time()
    res = analyze(db, names, now)
    db.set_meta(META, str(int(now)))
    db.purge_cell_events(int(now - KEEP_DAYS * 86400))
    worth = [c for c in res["cells"] if c["level"] == 2 or any(
        h and h[1] >= MIN_EVENTS and h[0] / h[1] >= 0.7 for h in (c["chg_top"], c["dis_low"]))]
    if worth:
        icon = "🔴" if worth[0]["level"] == 2 else "🟠"
        more = f" (și încă {len(worth) - 1})" if len(worth) > 1 else ""
        notifier.send(f"{icon} Sănătatea celulelor: {worth[0]['text']}{more}", title="Amper: celule",
                      priority="default", url="/?v=cells")
        log.info("sănătatea celulelor: %d celule de urmărit", len(worth))
    return res

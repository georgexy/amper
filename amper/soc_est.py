"""SOC-ul calculat de Amper, pe fiecare modul (cerut 2026-11-22, după ce un Daly a coborât de la 99 % la 0 % fără
curent: celulele rămăseseră la 3,30 V, ca la celelalte module).

Două lucruri separate:
- `ah_out` = Ah ieșiți de la ultima încărcare completă (net: ce intră se scade). Se numără din curentul raportat de BMS
  la fiecare citire și NU depinde de capacitate. „Plin” se recunoaște după tensiune, nu după SOC-ul BMS-ului: celula
  cea mai încărcată ≥ `FULL_CELL` (3,44 V), sau media celulelor ≥ `FULL_AVG` (3,40 V) cu curent mic (coada
  încărcării). La plin, `ah_out` = 0.
- capacitatea (Ah): cea învățată de `capacity.learned` (descărcări adânci, testul de capacitate, comparația dintre
  module), altfel cea din BMS / config.

SOC Amper = 100 − ah_out / capacitate × 100. Jos se corectează după tensiune (cot / repaus sub 3,21 V, ca în
capacity.py). Până la prima încărcare completă (modul nou, Pi repornit fără stare salvată) pornește de la SOC-ul
BMS-ului și e marcat `anchored = False`.

Verificarea BMS-ului (`mismatch`): SOC-ul din BMS se schimbă mult mai mult decât arată Ah-ii măsurați (fereastră de
2 ore), sau nu se potrivește cu tensiunea în repaus (≤ 5 % cu celulele ≥ 3,27 V; ≥ 97 % cu celulele ≤ 3,30 V).
"""
from __future__ import annotations

import json
import time

from .capacity import KNEE_CELL, REST_C, REST_MAX, ocv_soc

FULL_CELL = 3.44
FULL_AVG = 3.40
TAIL_C = 0.05            # „coada” încărcării: curent sub 5 % din capacitate
GAP_S = 120              # peste atât între citiri, curentul nu se mai adună (lipsă de date)
META = "soc_est"
SAVE_EVERY = 300
WINDOW_S = 2 * 3600      # fereastra în care se compară SOC-ul BMS-ului cu Ah-ii măsurați
HIST_EVERY = 300
COUNT_DIFF = 15          # puncte de SOC peste care BMS-ul „numără altfel” decât curentul
SHOW_DIFF = 8            # diferența BMS ↔ Amper de la care se afișează și cealaltă cifră
MISMATCH_KEEP = 6 * 3600


class SocEstimator:
    def __init__(self, db=None, clock=time.time):
        self.db = db
        self.clock = clock
        self.st: dict[str, dict] = {}
        self.caps: dict[str, dict] = {}          # dev → {cap, kind, rel, nights}
        self.saved_at = 0.0
        if db is not None:
            try:
                for dev, s in json.loads(db.get_meta(META) or "{}").items():
                    self.st[dev] = {**s, "hist": [], "last_ts": None}
            except ValueError:
                pass

    # ---------------- capacitatea ----------------
    def set_caps(self, caps: dict[str, dict]) -> None:
        self.caps = caps or {}

    def cap(self, dev: str, rated: float | None) -> tuple[float | None, str]:
        c = self.caps.get(dev) or {}
        if c.get("cap"):
            return float(c["cap"]), c.get("kind", "measured")
        return (float(rated), "rated") if rated else (None, "none")

    # ---------------- citirile ----------------
    def observe(self, dev: str, r, rated: float | None, now: float | None = None) -> None:
        now = now or self.clock()
        cap, _kind = self.cap(dev, rated)
        if not cap:
            return
        cells = [c for c in (getattr(r, "cells", None) or []) if c]
        cmax = max(cells) if cells else None
        cmin = min(cells) if cells else None
        avg = sum(cells) / len(cells) if len(cells) >= 4 else None
        cur, soc_bms = r.current, r.soc
        s = self.st.get(dev)
        if s is None:                                     # prima citire: pornim de la BMS (dacă e plauzibil)
            start = soc_bms if soc_bms is not None and 0 <= soc_bms <= 100 else 50.0
            s = self.st[dev] = dict(ah_out=cap * (1 - start / 100), anchored=False, full_ts=None, last_ts=None,
                                    hist=[], mismatch=None)
        last = s.get("last_ts")
        if cur is not None and last is not None and 0 < now - last <= GAP_S:
            s["ah_out"] -= cur * (now - last) / 3600       # + = încărcare → ah_out scade
        elif last is not None and now - last > GAP_S:
            s["gap"] = True
        s["last_ts"] = now
        # plin: după tensiune (SOC-ul BMS-ului poate fi greșit tocmai de aceea)
        full = (cmax is not None and cmax >= FULL_CELL and (cur is None or cur > -1)) or \
            (avg is not None and avg >= FULL_AVG and cur is not None and 0 <= cur <= TAIL_C * cap)
        if full:
            s.update(ah_out=0.0, anchored=True, full_ts=now, gap=False)
        else:
            s["ah_out"] = min(max(s["ah_out"], 0.0), cap)
            low = None
            if cmin is not None and cmin <= KNEE_CELL:
                low = 2.0
            elif avg is not None and cur is not None and abs(cur) <= REST_C * cap and avg <= REST_MAX:
                low = ocv_soc(avg)
            if low is not None and abs((100 - s["ah_out"] / cap * 100) - low) > 5:
                s["ah_out"] = cap * (1 - low / 100)       # jos, tensiunea e mai sigură decât numărătoarea
        self._check(dev, s, cap, soc_bms, avg, cur, now)

    def _check(self, dev, s, cap, soc_bms, avg, cur, now) -> None:
        """SOC-ul din BMS se potrivește cu curentul măsurat și cu tensiunea?"""
        mine = 100 - s["ah_out"] / cap * 100
        hist = s.setdefault("hist", [])
        if soc_bms is not None and (not hist or now - hist[-1][0] >= HIST_EVERY):
            hist.append((now, soc_bms, mine))
            while hist and now - hist[0][0] > WINDOW_S:
                hist.pop(0)
        why = None
        if soc_bms is not None and hist and now - hist[0][0] >= WINDOW_S * 0.75:
            t0, b0, m0 = hist[0]
            db_, dm = soc_bms - b0, mine - m0
            if abs(db_ - dm) > COUNT_DIFF:
                why = (f"BMS-ul și-a schimbat SOC-ul cu {db_:+.0f} % în {int((now - t0) // 60)} min, "
                       f"dar curentul măsurat arată {dm:+.0f} %")
        rest = cur is not None and abs(cur) <= 0.1 * cap
        if why is None and soc_bms is not None and avg is not None and rest:
            if soc_bms <= 5 and avg >= 3.27:
                why = f"BMS-ul arată {soc_bms:.0f} %, dar celulele au {avg:.2f} V (nu e gol)".replace(".", ",")
            elif soc_bms >= 97 and avg <= 3.30 and abs(cur) <= TAIL_C * cap:
                why = f"BMS-ul arată {soc_bms:.0f} %, dar celulele au doar {avg:.2f} V (nu e plin)".replace(".", ",")
        if why and soc_bms is not None and abs(soc_bms - mine) > COUNT_DIFF:
            s["mismatch"] = {"since": (s.get("mismatch") or {}).get("since", now), "at": now, "why": why}
        elif s.get("mismatch") and (now - s["mismatch"]["at"] > MISMATCH_KEEP
                                    or (soc_bms is not None and abs(soc_bms - mine) <= SHOW_DIFF)):
            s["mismatch"] = None

    # ---------------- rezultatul ----------------
    def soc(self, dev: str, rated: float | None = None) -> float | None:
        s = self.st.get(dev)
        cap, _ = self.cap(dev, rated)
        if not s or not cap:
            return None
        return round(max(0.0, min(100.0, 100 - s["ah_out"] / cap * 100)), 1)

    def info(self, dev: str, rated: float | None, soc_bms: float | None) -> dict | None:
        s = self.st.get(dev)
        cap, kind = self.cap(dev, rated)
        if not s or not cap:
            return None
        c = self.caps.get(dev) or {}
        mine = self.soc(dev, rated)
        return dict(soc=mine, soc_bms=soc_bms, ah_out=round(s["ah_out"], 1), cap=round(cap, 1), cap_kind=kind,
                    rel=c.get("rel"), anchored=bool(s.get("anchored")), full_ts=s.get("full_ts"),
                    gap=bool(s.get("gap")), mismatch=(s.get("mismatch") or {}).get("why"),
                    differs=soc_bms is not None and mine is not None and abs(soc_bms - mine) > SHOW_DIFF)

    def save(self, force: bool = False) -> None:
        now = self.clock()
        if self.db is None or (not force and now - self.saved_at < SAVE_EVERY):
            return
        self.saved_at = now
        keep = {d: {k: s.get(k) for k in ("ah_out", "anchored", "full_ts", "gap")} for d, s in self.st.items()}
        self.db.set_meta(META, json.dumps(keep))

    def forget(self, dev: str) -> None:
        self.st.pop(dev, None)

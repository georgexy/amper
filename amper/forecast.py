"""Prognoza pentru următoarele 2 zile: cât produc panourile, cât se consumă, unde ajunge bateria și dacă trebuie
pornit generatorul (cerută 2026-11-19).

- Vremea: Open-Meteo (fără cont, fără cheie). Pi-ul trimite doar coordonatele localității alese de utilizator.
  `shortwave_radiation` (W/m², media orei care se termină la ora din listă), 14 zile în urmă + 3 înainte.
- Cât produc panourile la o anumită radiație NU se cere utilizatorului: Pi-ul învață din ultimele 14 zile
  (`calibrate`): raportul PV măsurat / radiație, doar în orele cu soare și bateria sub 95 % (cu bateria plină,
  invertorul taie PV-ul, deci raportul ar ieși prea mic). Mediana rapoartelor.
- Consumul: media fiecărei ore din zi în ultimele 14 zile (`load_profile`).
- Simularea (`project`): oră cu oră, de acum 48 de ore înainte, plecând de la energia din bancă (kWh, pe tensiunea
  nominală, ca pe ecranul principal). Consumul trece prin invertor (randament `INV_EFF`), surplusul se încarcă în
  bancă (`CHG_EFF`) până la plin.
- Notificarea (`check`): o dată pe zi, dacă bateria ar coborî sub pragul ales în următoarele 24 de ore.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = "https://api.open-meteo.com/v1/forecast"
GEO = "https://geocoding-api.open-meteo.com/v1/search"
META = "forecast"                   # setările: localitatea, pragul, notificarea (JSON în tabela meta)
CACHE = "forecast_weather"          # ultima prognoză descărcată
NOTIFIED = "forecast_notified"      # ziua ultimei notificări
REFRESH_S = 2 * 3600                # vremea se cere din nou după 2 ore
STALE_S = 24 * 3600                 # peste atât, prognoza veche nu mai e folosită
PAST_DAYS = 14
HORIZON_H = 48
MIN_SUN = 150                       # W/m²: ore cu soare destul pentru calibrare
FULL_SOC = 95                       # peste atât, PV-ul poate fi tăiat de invertor → ora nu intră în calibrare
MIN_CAL_HOURS = 12                  # sub atâtea ore bune, nu avem încă o estimare a panourilor
INV_EFF = 0.92                      # consumul casei, văzut din baterie (pierderile invertorului)
CHG_EFF = 0.97
DEFAULTS = {"place": None, "lat": None, "lon": None, "min_soc": 20, "notify": True}


def _fetch(url: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "amper"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("reason") or "eroare")
    return data


# ---------------- setări ----------------
def settings(db) -> dict:
    try:
        s = json.loads(db.get_meta(META) or "{}")
    except ValueError:
        s = {}
    return {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}


def save_settings(db, upd: dict) -> dict:
    s = settings(db)
    if "lat" in upd or "lon" in upd:
        lat, lon = float(upd.get("lat")), float(upd.get("lon"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordonate greșite.")
        s.update(lat=round(lat, 3), lon=round(lon, 3), place=str(upd.get("place") or "")[:80] or None)
    if "min_soc" in upd:
        v = int(upd["min_soc"])
        if not 5 <= v <= 80:
            raise ValueError("Pragul trebuie să fie între 5 și 80 %.")
        s["min_soc"] = v
    if "notify" in upd:
        s["notify"] = bool(upd["notify"])
    db.set_meta(META, json.dumps(s))
    return s


def places(query: str, fetch=_fetch, lang: str = "ro") -> list[dict]:
    """Căutarea localității (Open-Meteo geocoding): primele 6 potriviri."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    d = fetch(f"{GEO}?{urllib.parse.urlencode({'name': q, 'count': 6, 'language': lang, 'format': 'json'})}")
    out = []
    for r in d.get("results") or []:
        region = ", ".join(x for x in (r.get("admin1"), r.get("country")) if x)
        out.append(dict(name=r.get("name"), region=region, lat=r.get("latitude"), lon=r.get("longitude")))
    return out


# ---------------- vremea ----------------
def weather(db, s: dict, fetch=_fetch, now: float | None = None, allow_fetch: bool = True) -> dict:
    """Radiația pe ore {ora (secunde, sfârșitul orei): W/m²}, din cache dacă e proaspătă. Ridică eroare doar dacă
    nu există nici o prognoză folosibilă. `allow_fetch=False`: doar din cache (graficele, casa 3D)."""
    now = now or time.time()
    try:
        c = json.loads(db.get_meta(CACHE) or "{}")
    except ValueError:
        c = {}
    same = c.get("lat") == s["lat"] and c.get("lon") == s["lon"]
    if same and now - c.get("at", 0) < REFRESH_S:
        return c
    if not allow_fetch:
        if same and now - c.get("at", 0) < STALE_S:
            return c
        raise RuntimeError("fără prognoză descărcată")
    q = dict(latitude=s["lat"], longitude=s["lon"], hourly="shortwave_radiation,cloud_cover",
             past_days=PAST_DAYS, forecast_days=3, timezone="GMT", timeformat="unixtime")
    try:
        d = fetch(f"{API}?{urllib.parse.urlencode(q)}")
        h = d["hourly"]
        c = dict(lat=s["lat"], lon=s["lon"], at=now, time=h["time"], rad=h["shortwave_radiation"],
                 cloud=h.get("cloud_cover") or [None] * len(h["time"]))
        db.set_meta(CACHE, json.dumps(c))
        return c
    except Exception as e:
        log.warning("prognoza: vremea nu s-a putut descărca (%s)", e)
        if same and now - c.get("at", 0) < STALE_S:
            return dict(c, stale=str(e))
        raise RuntimeError(f"Prognoza meteo nu s-a putut descărca ({e}).")


def _by_hour(w: dict) -> dict[int, tuple]:
    """{începutul orei: (radiație, nori)}. Open-Meteo dă media orei care SE TERMINĂ la ora din listă."""
    return {int(t) - 3600: (r, c) for t, r, c in zip(w["time"], w["rad"], w["cloud"]) if r is not None}


# ---------------- ce am învățat din ultimele zile ----------------
def calibrate(hist: dict[int, dict], rad: dict[int, tuple]) -> dict:
    """W de PV la 1 W/m²: mediana orelor cu soare și bateria nu plină. Până sunt destule (`MIN_CAL_HOURS`), o
    estimare aproximativă din toate orele cu soare (percentila 90: cu bateria plină PV-ul e tăiat, deci prea mic),
    marcată `approx`. Plus cel mai mare PV văzut (W)."""
    good, every = [], []
    for h, o in hist.items():
        r = rad.get(h, (None,))[0]
        if r is None or r < MIN_SUN or o.get("pv") is None:
            continue
        every.append(o["pv"] / r)
        if o.get("soc") is None or o["soc"] < FULL_SOC:
            good.append(o["pv"] / r)
    pv_max = max((o.get("pv") or 0 for o in hist.values()), default=0)
    if len(good) >= MIN_CAL_HOURS:
        return dict(k=statistics.median(good), hours=len(good), pv_max=pv_max, approx=False)
    if len(every) >= MIN_CAL_HOURS // 2:
        every.sort()
        return dict(k=every[int(0.9 * (len(every) - 1))], hours=len(good), pv_max=pv_max, approx=True)
    return dict(k=None, hours=len(good), pv_max=pv_max, approx=False)


def load_profile(hist: dict[int, dict]) -> dict:
    """Consumul mediu (W) pe fiecare oră din zi (ora locală) și câte zile de date sunt."""
    by: dict[int, list] = {}
    days = set()
    for h, o in hist.items():
        if o.get("load") is None:
            continue
        lt = time.localtime(h)
        by.setdefault(lt.tm_hour, []).append(o["load"])
        days.add((lt.tm_year, lt.tm_yday))
    if not by:
        return dict(w={}, days=0, avg=None)
    avg = statistics.mean(v for vals in by.values() for v in vals)
    return dict(w={hr: statistics.mean(v) for hr, v in by.items()}, days=len(days), avg=avg)


# ---------------- simularea ----------------
def project(now: float, kwh: float, cap_kwh: float, cal: dict, prof: dict, rad: dict[int, tuple],
            hours: int = HORIZON_H) -> list[dict]:
    """Ora cu ora: energia din bancă (kWh) și SOC-ul. Primul punct = acum, apoi sfârșitul fiecărei ore."""
    e = max(0.0, min(kwh, cap_kwh))
    soc = lambda: round(e / cap_kwh * 100, 1) if cap_kwh else None
    pts = [dict(ts=int(now), pv=None, load=None, cloud=None, kwh=round(e, 3), soc=soc())]
    pv_cap = cal["pv_max"] * 1.15 if cal["pv_max"] else None
    h0 = int(now // 3600 * 3600)
    for i in range(hours):
        start, end = h0 + i * 3600, h0 + (i + 1) * 3600
        frac = (end - max(now, start)) / 3600
        r, cloud = rad.get(start, (None, None))
        pv = (cal["k"] * r) if (cal["k"] is not None and r is not None) else 0.0
        if pv_cap:
            pv = min(pv, pv_cap)
        load = prof["w"].get(time.localtime(start).tm_hour, prof["avg"] or 0.0)
        net = (pv - load / INV_EFF) * frac / 1000          # kWh în ora asta (sau partea rămasă din ea)
        e = min(cap_kwh, e + net * CHG_EFF) if net > 0 else max(0.0, e + net)
        pts.append(dict(ts=end, pv=round(pv), load=round(load), cloud=cloud, kwh=round(e, 3), soc=soc()))
    return pts


def _day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def summarize(now: float, pts: list[dict], min_soc: float, cap_kwh: float) -> dict:
    """Pe zile (azi, mâine, poimâine) + sfatul pentru generator."""
    days: dict[str, dict] = {}
    prev = now
    for p in pts:
        hours = (p["ts"] - prev) / 3600
        prev = p["ts"]
        d = days.setdefault(_day_key(p["ts"] - 1), dict(pv_kwh=0.0, load_kwh=0.0, min_soc=None, min_at=None,
                                                         end_soc=None))
        if p["pv"] is not None:
            d["pv_kwh"] += p["pv"] * hours / 1000
            d["load_kwh"] += p["load"] * hours / 1000
        if p["soc"] is not None and (d["min_soc"] is None or p["soc"] < d["min_soc"]):
            d.update(min_soc=p["soc"], min_at=p["ts"])
        d["end_soc"] = p["soc"]
    out_days = []
    for k, d in sorted(days.items())[:3]:
        out_days.append(dict(day=k, pv_kwh=round(d["pv_kwh"], 1), load_kwh=round(d["load_kwh"], 1),
                             min_soc=d["min_soc"], min_at=d["min_at"], end_soc=d["end_soc"]))
    low = next((p for p in pts if p["soc"] is not None and p["soc"] < min_soc), None)
    lowest = min((p for p in pts if p["soc"] is not None), key=lambda p: p["soc"], default=None)
    need = 0.0
    if low and cap_kwh:
        need = max(min_soc / 100 * cap_kwh - p["kwh"] for p in pts)
    return dict(days=out_days, low=low, lowest=lowest, need_kwh=round(max(0.0, need), 1))


def _when(ts: float, now: float) -> tuple[str, str]:
    """(„azi” / „mâine” / „poimâine” / data, „HH:MM”)."""
    day = lambda x: time.mktime(time.strptime(_day_key(x), "%Y-%m-%d"))
    word = {0: "azi", 1: "mâine", 2: "poimâine"}.get(round((day(ts) - day(now)) / 86400),
                                                     time.strftime("%d.%m", time.localtime(ts)))
    return word, time.strftime("%H:%M", time.localtime(ts))


def _kwh(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def advice(sm: dict, min_soc: float, now: float, pts: list[dict]) -> dict:
    low, lowest = sm["low"], sm["lowest"]
    if low and low is pts[0]:
        return dict(level="need", at=low["ts"], text=f"Bateria e sub {min_soc:.0f} % acum. Pornește generatorul: ca să "
                                                     f"rămână peste prag îi lipsesc cam {_kwh(sm['need_kwh'])} kWh.")
    if low:
        w, hm = _when(low["ts"], now)
        sw, shm = _when(max(now, low["ts"] - 3600), now)
        go = "acum" if low["ts"] - 3600 <= now else f"până la ora {shm}" + (f" {sw}" if sw != "azi" else "")
        return dict(level="need", at=low["ts"],
                    text=f"Bateria ajunge sub {min_soc:.0f} % {w} la {hm}. Pornește generatorul {go}: "
                         f"îi lipsesc cam {_kwh(sm['need_kwh'])} kWh.")
    if lowest:
        w, hm = _when(lowest["ts"], now)
        return dict(level="ok", at=lowest["ts"], text=f"Nu e nevoie de generator în următoarele 48 de ore. "
                                                      f"Cel mai jos: {lowest['soc']:.0f} %, {w} la {hm}.")
    return dict(level="none", text="Nu sunt date despre baterie.", at=None)


# ---------------- tot ----------------
def build(db, bank: dict, fetch=_fetch, now: float | None = None) -> dict:
    now = now or time.time()
    s = settings(db)
    out = dict(settings=s, ready=False)
    if s["lat"] is None:
        out["missing"] = "location"
        return out
    try:
        w = weather(db, s, fetch, now)
    except RuntimeError as e:
        out["error"] = str(e)
        return out
    rad = _by_hour(w)
    hist = db.hourly(int(now - PAST_DAYS * 86400), int(now // 3600 * 3600))
    cal = calibrate(hist, rad)
    prof = load_profile(hist)
    out.update(calibration=dict(hours=cal["hours"], k=round(cal["k"], 2) if cal["k"] else None,
                                pv_max=round(cal["pv_max"]), approx=cal["approx"]), profile_days=prof["days"],
               weather_at=w.get("at"), weather_stale=w.get("stale"))
    if not bank or bank.get("capacity_kwh") is None or bank.get("kwh") is None:
        out["missing"] = "battery"
        return out
    if prof["avg"] is None:
        out["missing"] = "history"
        return out
    pts = project(now, bank["kwh"], bank["capacity_kwh"], cal, prof, rad)
    sm = summarize(now, pts, s["min_soc"], bank["capacity_kwh"])
    out.update(ready=True, learning=cal["k"] is None or cal["approx"], no_pv=cal["k"] is None, points=pts, days=sm["days"],
               advice=advice(sm, s["min_soc"], now, pts), need_kwh=sm["need_kwh"],
               soc_now=bank.get("soc"), capacity_kwh=bank["capacity_kwh"])
    return out


def check(db, notifier, bank: dict, fetch=_fetch, now: float | None = None) -> dict | None:
    """Din maintenance_loop (la oră): vremea ținută la zi (graficele și casa 3D o citesc din cache) și o notificare
    pe zi dacă bateria ar coborî sub prag în următoarele 24 de ore."""
    now = now or time.time()
    s = settings(db)
    if s["lat"] is None:
        return None
    try:
        weather(db, s, fetch, now)
    except RuntimeError:
        return None
    if not s["notify"]:
        return None
    if db.get_meta(NOTIFIED) == _day_key(now):
        return None
    rep = build(db, bank, fetch, now)
    a = rep.get("advice") or {}
    if not rep.get("ready") or rep.get("no_pv") or a.get("level") != "need" or a["at"] - now > 24 * 3600:
        return None
    db.set_meta(NOTIFIED, _day_key(now))
    notifier.send("⛅ " + a["text"], title="Amper: prognoză", priority="high", url="/?v=forecast")
    return rep


# ---------------- preconizarea pe grafice și vremea pentru casa 3D (doar din cache, fără internet) ----------------
_MODEL: dict = {}


def pv_model(db, now: float | None = None) -> tuple[dict, dict] | None:
    """(calibrarea, radiația pe ore) din prognoza salvată; ținută în memorie cât nu se schimbă ora sau prognoza."""
    now = now or time.time()
    s = settings(db)
    if s["lat"] is None:
        return None
    try:
        w = weather(db, s, now=now, allow_fetch=False)
    except RuntimeError:
        return None
    key = (id(db), w.get("at"), int(now // 3600))
    if _MODEL.get("key") != key:
        rad = _by_hour(w)
        hist = db.hourly(int(now - PAST_DAYS * 86400), int(now // 3600 * 3600))
        _MODEL.update(key=key, val=(calibrate(hist, rad), rad))
    return _MODEL["val"]


def _pv(cal: dict, r: float) -> float:
    pv = cal["k"] * r
    return min(pv, cal["pv_max"] * 1.15) if cal["pv_max"] else pv


def pv_series(db, start: float, end: float, now: float | None = None) -> dict | None:
    """Producția preconizată pe ore între `start` și `end` (poate fi și în viitor): [[mijlocul orei, W], …]."""
    m = pv_model(db, now)
    if not m or m[0]["k"] is None:
        return None
    cal, rad = m
    pts = [[h + 1800, round(_pv(cal, r))] for h, (r, _c) in sorted(rad.items())
           if start - 3600 <= h <= end and r is not None]
    return dict(points=pts, approx=cal["approx"])


def pv_days(db, day_from: str, day_to: str, now: float | None = None) -> dict | None:
    """kWh preconizați pe zile (AAAA-LL-ZZ), doar zilele acoperite în întregime de prognoză."""
    m = pv_model(db, now)
    if not m or m[0]["k"] is None:
        return None
    cal, rad = m
    days: dict[str, list] = {}
    for h, (r, _c) in rad.items():
        d = _day_key(h)
        if day_from <= d <= day_to and r is not None:
            days.setdefault(d, []).append(_pv(cal, r))
    return dict(days={d: round(sum(v) / 1000, 1) for d, v in days.items() if len(v) >= 23},
                approx=cal["approx"])


def sky_now(db, now: float | None = None) -> dict:
    """Norii de acum (%) și radiația, pentru casa 3D. Fără localitate sau prognoză: {}."""
    now = now or time.time()
    s = settings(db)
    if s["lat"] is None:
        return {}
    try:
        w = weather(db, s, now=now, allow_fetch=False)
    except RuntimeError:
        return {}
    r, c = _by_hour(w).get(int(now // 3600 * 3600), (None, None))
    return dict(cloud=c, rad=r)

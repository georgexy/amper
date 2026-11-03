"""Parametrii BMS-urilor Seplos V2: citire (CID2 0x47), descriere pentru aplicație și scriere (0x49) doar în câmpurile
confirmate.

Citirea: verificată pe hardware 2026-11-03 (3 pachete V2, 19200 baud), doar cu comenzi de citire. Indexul valorii din
răspunsul 0x47 = numărul rândului („Num”) din BatteryMonitor V16.4 (protocol 2.0), ecranul „Parameter Manage”.
Câmpurile 0–11 și 58 sunt confirmate cu programul (8 = pornirea echilibrării: schimbat în BatteryMonitor, Amper a
citit valoarea nouă). Restul sunt PROBABILE: se afișează cu „neconfirmat” și nu se pot scrie.
BatteryMonitor scrie „V”, dar valorile sunt în mV (celule) și 10 mV (pachet); aici se afișează în V.

Scrierea (0x49): NEVERIFICATĂ pe hardware, iar formatul INFO pentru un singur parametru nu e cunoscut. Ipoteza folosită
(`write_info`): adresa, indexul parametrului (1 octet), valoarea (u16 big-endian). De aceea scrierea e oprită implicit
(`seplos.allow_write: false` în config) și, chiar pornită: doar câmpuri confirmate, în limite LiFePO4 16S, cu parola
aplicației; se citește 0x47, se scrie 0x49, se citește iar 0x47 și se compară TOATE valorile (doar câmpul cerut are
voie să se schimbe). Primul test pe hardware: doar cu utilizatorul de față, un câmp (ex. 8), un pachet, ±10 mV și înapoi.
"""
from __future__ import annotations

import time

from .drivers.base import DriverError

WRITE_CID2 = 0x49

F = dict
CELL = dict(unit="V", scale=0.001)        # mV
PACK = dict(unit="V", scale=0.01)         # 10 mV

FIELDS = [
    F(idx=0, group="Tensiune celulă", label="Alarmă tensiune mare", **CELL, min=3.40, max=3.65, confirmed=True),
    F(idx=1, group="Tensiune celulă", label="Revenire din alarma de tensiune mare", **CELL, min=3.30, max=3.60,
      confirmed=True),
    F(idx=2, group="Tensiune celulă", label="Alarmă tensiune mică", **CELL, min=2.80, max=3.10, confirmed=True),
    F(idx=3, group="Tensiune celulă", label="Revenire din alarma de tensiune mică", **CELL, min=2.90, max=3.30,
      confirmed=True),
    F(idx=4, group="Tensiune celulă", label="Protecție la supratensiune", **CELL, min=3.45, max=3.70, confirmed=True,
      risky=True),
    F(idx=5, group="Tensiune celulă", label="Revenire din protecția la supratensiune", **CELL, min=3.30, max=3.60,
      confirmed=True, risky=True),
    F(idx=6, group="Tensiune celulă", label="Protecție la subtensiune", **CELL, min=2.50, max=3.00, confirmed=True,
      risky=True),
    F(idx=7, group="Tensiune celulă", label="Revenire din protecția la subtensiune", **CELL, min=2.80, max=3.30,
      confirmed=True, risky=True),
    F(idx=8, group="Echilibrare", label="Pornire echilibrare", **CELL, min=3.20, max=3.50, confirmed=True,
      note="La LiFePO4, între 3,30 și 3,40 V curba e plată: echilibrarea acolo poate dezechilibra."),
    F(idx=9, group="Tensiune celulă", label="Celulă descărcată (Battery low voltage)", **CELL, min=1.00, max=2.80,
      confirmed=True, risky=True),
    F(idx=10, group="Tensiune pachet", label="Alarmă tensiune mare", **PACK, min=54.0, max=58.4, confirmed=True),
    F(idx=11, group="Tensiune pachet", label="Revenire din alarma de tensiune mare", **PACK, min=52.0, max=57.6,
      confirmed=True),
    F(idx=12, group="Tensiune pachet", label="Alarmă tensiune mică", **PACK),
    F(idx=13, group="Tensiune pachet", label="Revenire din alarma de tensiune mică", **PACK),
    F(idx=14, group="Tensiune pachet", label="Protecție la supratensiune", **PACK),
    F(idx=15, group="Tensiune pachet", label="Revenire din protecția la supratensiune", **PACK),
    F(idx=16, group="Tensiune pachet", label="Protecție la subtensiune", **PACK),
    F(idx=17, group="Tensiune pachet", label="Revenire din protecția la subtensiune", **PACK),
    F(idx=18, group="Alte valori", label="Parametrul 18", unit="", scale=1),
    F(idx=19, group="Alte valori", label="Parametrul 19", unit="", scale=1),
    *[F(idx=i, group="Temperaturi", label=f"Parametrul {i}", unit="°C", scale=0.1, offset=-273.1) for i in range(20, 50)],
    *[F(idx=i, group="Curenți", label=f"Parametrul {i}", unit="A", scale=0.01, signed=True) for i in range(50, 57)],
    F(idx=57, group="Alte valori", label="Parametrul 57", unit="", scale=1),
    F(idx=58, group="Baterie", label="Capacitate nominală", unit="Ah", scale=0.01, min=10, max=400, confirmed=True,
      risky=True, note="Din ea se calculează SOC-ul BMS-ului. Trebuie să fie capacitatea reală a pachetului."),
    F(idx=59, group="Alte valori", label="Parametrul 59", unit="", scale=1),
]
BY_IDX = {f["idx"]: f for f in FIELDS}
CONFIRMED = {f["idx"] for f in FIELDS if f.get("confirmed")}


def decimals(f: dict) -> int:
    s = f["scale"]
    return 3 if s == 0.001 else 2 if s == 0.01 else 1 if s == 0.1 else 0


def raw_to_value(f: dict, raw: int) -> float:
    if f.get("signed") and raw & 0x8000:
        raw -= 0x10000
    return round(raw * f["scale"] + f.get("offset", 0.0), decimals(f))


def value_to_raw(f: dict, value: float) -> int:
    return int(round((value - f.get("offset", 0.0)) / f["scale"]))


def fmt(f: dict, v) -> str:
    if v is None:
        return "–"
    s = f"{v:.{decimals(f)}f}".replace(".", ",")
    return f"{s} {f['unit']}".strip()


def describe(params: dict) -> list[dict]:
    """Toate valorile citite, cu `confirmed` (doar acelea se pot modifica)."""
    vals = params.get("values") or []
    out = []
    for f in FIELDS:
        raw = vals[f["idx"]] if f["idx"] < len(vals) else None
        v = raw_to_value(f, raw) if raw is not None else None
        out.append(dict(key=str(f["idx"]), idx=f["idx"], group=f["group"], label=f["label"], value=v, raw=raw,
                        text=fmt(f, v), unit=f["unit"], min=f.get("min"), max=f.get("max"), step=f["scale"],
                        confirmed=bool(f.get("confirmed")), locked=not f.get("confirmed"), risky=bool(f.get("risky")),
                        note=f.get("note", ""), role="", role_text="", alert=""))
    return out


def write_info(address: int, idx: int, raw: int) -> str:
    """INFO pentru 0x49 — IPOTEZĂ, NEVERIFICATĂ pe hardware: adresa, indexul (1 octet), valoarea (u16 big-endian)."""
    return f"{address:02X}{idx:02X}{raw:04X}"


def write_field(drv, key, value, allow: bool) -> dict:
    """Citește 0x47, scrie 0x49, citește iar 0x47 și compară toate valorile: doar câmpul cerut are voie să difere."""
    if not allow:
        raise PermissionError("Scrierea parametrilor Seplos e oprită (seplos.allow_write în config).")
    try:
        f = BY_IDX[int(key)]
    except (TypeError, ValueError, KeyError):
        raise ValueError(f"setare necunoscută: {key}") from None
    if not f.get("confirmed"):
        raise ValueError(f"{f['group']}, {f['label']}: câmp neconfirmat, nu se poate modifica")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{f['label']}: valoare invalidă") from None
    if not f["min"] - 1e-9 <= value <= f["max"] + 1e-9:
        raise ValueError(f"{f['group']}, {f['label']}: trebuie între {fmt(f, f['min'])} și {fmt(f, f['max'])}")
    raw = value_to_raw(f, value)
    before = drv.read_params()["values"]
    old = before[f["idx"]]
    label = f"{f['group']}, {f['label']}"
    res = dict(label=label, old=fmt(f, raw_to_value(f, old)), new=fmt(f, raw_to_value(f, raw)), changed=raw != old)
    if not res["changed"]:
        return res
    drv._query(WRITE_CID2, write_info(drv.address, f["idx"], raw))
    time.sleep(0.5)
    after = drv.read_params()["values"]
    if after[f["idx"]] != raw:
        if after[f["idx"]] == old:
            raise DriverError(f"{label}: BMS-ul nu a aplicat modificarea (a rămas {res['old']})")
        raise DriverError(f"{label}: BMS-ul arată altceva decât s-a trimis "
                          f"({fmt(f, raw_to_value(f, after[f['idx']]))}) — verificați în BatteryMonitor")
    other = [i for i, (a, b) in enumerate(zip(before, after)) if a != b and i != f["idx"]]
    if other or len(after) != len(before):
        raise DriverError(f"{label}: s-au schimbat și alți parametri ({', '.join(map(str, other))}) — verificați "
                          "în BatteryMonitor")
    return res

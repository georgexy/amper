"""Setările BMS-urilor Daly: citire, descriere pentru aplicație și scriere verificată prin citire înapoi.

Citirea: comenzile 0x50–0x63 (verificate pe hardware 2026-10-26). Fiecare comandă întoarce un bloc de 8 octeți.
Scrierea: comanda de citire minus 0x40 (ex. 0x59 → 0x19), cu blocul întreg de 8 octeți — după regula protocolului
Daly UART/485, NEVERIFICATĂ încă pe aceste BMS-uri. De aceea:
  - se citește blocul proaspăt, se schimbă doar câmpul cerut, restul octeților rămân exact cum erau;
  - după scriere, blocul se citește înapoi și trebuie să fie identic cu cel trimis, altfel se raportează eroare;
  - valorile sunt limitate la intervale sigure pentru LiFePO4 16S.

Fiecare prag apare în pereche, iar rolul e dat de POZIȚIA în bloc, nu de valoare: ordinea e
[sus nivel 1, sus nivel 2, jos nivel 1, jos nivel 2]; nivel 1 = avertisment, nivel 2 = protecție (taie MOSFET-ul).
Dovedit pe hardware 2026-10-29 la 0x59: cu cell_lo_b = 2,95 V, Daly 3 a raportat „Tensiune celulă mică (nivel 2)” și
a tăiat descărcarea la 2,93–2,95 V. Înainte rolul se calcula după valori („cel mai sever = protecție”), iar valorile
din fabrică „inversate” (sus 3,65/3,60, jos 2,30/2,80) îl arătau pe dos. La 0x5A (pachet) ordinea e aceeași
(același tip de bloc); la 0x5B (curent) și 0x5C (temperaturi) regula e NECONFIRMATĂ pe hardware (`CONFIRMED`).
Dacă protecția e mai puțin severă decât avertismentul, `describe` o semnalează (`alert`), fără să blocheze scrierea.
"""
from __future__ import annotations

import logging
import time

from .drivers.base import DriverError
from .drivers.daly import build_request

log = logging.getLogger("amper.daly_params")

# key, cmd, offset, size, tip, scară, unitate, min, max, grup, etichetă, pereche, sens („sus” = mai mare e mai sever)
F = dict
FIELDS = [
    F(key="capacity", cmd=0x50, off=0, size=4, kind="u", scale=0.001, unit="Ah", min=10, max=1000,
      group="Baterie", label="Capacitate nominală", risky=True,
      note="Din ea se calculează SOC-ul BMS-ului. Trebuie să fie capacitatea reală a modulului."),
    F(key="sleep", cmd=0x53, off=5, size=2, kind="u", scale=1, unit="s", min=60, max=65535,
      group="Baterie", label="Somn după (fără curent)",
      note="După acest timp fără curent BMS-ul adoarme și Bluetooth-ul nu mai răspunde. 65535 = maxim."),
    F(key="cell_hi_a", cmd=0x59, off=0, size=2, kind="u", scale=0.001, unit="V", min=3.40, max=3.75,
      group="Tensiune celulă", label="Maximă", pair="cell_hi", hi=True, risky=True),
    F(key="cell_hi_b", cmd=0x59, off=2, size=2, kind="u", scale=0.001, unit="V", min=3.40, max=3.75,
      group="Tensiune celulă", label="Maximă", pair="cell_hi", hi=True, risky=True),
    F(key="cell_lo_a", cmd=0x59, off=4, size=2, kind="u", scale=0.001, unit="V", min=2.00, max=3.10,
      group="Tensiune celulă", label="Minimă", pair="cell_lo", hi=False, risky=True),
    F(key="cell_lo_b", cmd=0x59, off=6, size=2, kind="u", scale=0.001, unit="V", min=2.00, max=3.10,
      group="Tensiune celulă", label="Minimă", pair="cell_lo", hi=False, risky=True),
    F(key="pack_hi_a", cmd=0x5A, off=0, size=2, kind="u", scale=0.1, unit="V", min=54.0, max=60.0,
      group="Tensiune pachet", label="Maximă", pair="pack_hi", hi=True, risky=True),
    F(key="pack_hi_b", cmd=0x5A, off=2, size=2, kind="u", scale=0.1, unit="V", min=54.0, max=60.0,
      group="Tensiune pachet", label="Maximă", pair="pack_hi", hi=True, risky=True),
    F(key="pack_lo_a", cmd=0x5A, off=4, size=2, kind="u", scale=0.1, unit="V", min=32.0, max=50.0,
      group="Tensiune pachet", label="Minimă", pair="pack_lo", hi=False, risky=True),
    F(key="pack_lo_b", cmd=0x5A, off=6, size=2, kind="u", scale=0.1, unit="V", min=32.0, max=50.0,
      group="Tensiune pachet", label="Minimă", pair="pack_lo", hi=False, risky=True),
    F(key="dis_a", cmd=0x5B, off=0, size=2, kind="cur_dis", scale=0.1, unit="A", min=5, max=400,
      group="Curent", label="Descărcare", pair="dis", hi=True, risky=True),
    F(key="dis_b", cmd=0x5B, off=2, size=2, kind="cur_dis", scale=0.1, unit="A", min=5, max=400,
      group="Curent", label="Descărcare", pair="dis", hi=True, risky=True),
    F(key="chg_a", cmd=0x5B, off=4, size=2, kind="cur_chg", scale=0.1, unit="A", min=5, max=400,
      group="Curent", label="Încărcare", pair="chg", hi=True, risky=True),
    F(key="chg_b", cmd=0x5B, off=6, size=2, kind="cur_chg", scale=0.1, unit="A", min=5, max=400,
      group="Curent", label="Încărcare", pair="chg", hi=True, risky=True),
    F(key="tc_hi_a", cmd=0x5C, off=0, size=1, kind="temp", scale=1, unit="°C", min=30, max=80,
      group="Temperatură la încărcare", label="Maximă", pair="tc_hi", hi=True),
    F(key="tc_hi_b", cmd=0x5C, off=1, size=1, kind="temp", scale=1, unit="°C", min=30, max=80,
      group="Temperatură la încărcare", label="Maximă", pair="tc_hi", hi=True),
    F(key="tc_lo_a", cmd=0x5C, off=2, size=1, kind="temp", scale=1, unit="°C", min=-40, max=15,
      group="Temperatură la încărcare", label="Minimă", pair="tc_lo", hi=False, risky=True,
      note="LiFePO4 nu are voie să fie încărcat sub 0 °C."),
    F(key="tc_lo_b", cmd=0x5C, off=3, size=1, kind="temp", scale=1, unit="°C", min=-40, max=15,
      group="Temperatură la încărcare", label="Minimă", pair="tc_lo", hi=False, risky=True,
      note="LiFePO4 nu are voie să fie încărcat sub 0 °C."),
    F(key="td_hi_a", cmd=0x5C, off=4, size=1, kind="temp", scale=1, unit="°C", min=30, max=85,
      group="Temperatură la descărcare", label="Maximă", pair="td_hi", hi=True),
    F(key="td_hi_b", cmd=0x5C, off=5, size=1, kind="temp", scale=1, unit="°C", min=30, max=85,
      group="Temperatură la descărcare", label="Maximă", pair="td_hi", hi=True),
    F(key="td_lo_a", cmd=0x5C, off=6, size=1, kind="temp", scale=1, unit="°C", min=-40, max=10,
      group="Temperatură la descărcare", label="Minimă", pair="td_lo", hi=False),
    F(key="td_lo_b", cmd=0x5C, off=7, size=1, kind="temp", scale=1, unit="°C", min=-40, max=10,
      group="Temperatură la descărcare", label="Minimă", pair="td_lo", hi=False),
    F(key="bal_start", cmd=0x5F, off=0, size=2, kind="u", scale=0.001, unit="V", min=3.00, max=3.60,
      group="Echilibrare", label="Pornește peste",
      note="La LiFePO4, între 3,2 și 3,35 V curba e plată: echilibrarea acolo poate dezechilibra."),
    F(key="bal_diff", cmd=0x5F, off=2, size=2, kind="u", scale=1, unit="mV", min=5, max=200,
      group="Echilibrare", label="La o diferență de cel puțin"),
]
BY_KEY = {f["key"]: f for f in FIELDS}
# rolul după poziție e dovedit pe hardware doar la tensiuni (0x59 celulă, 0x5A pachet)
CONFIRMED = {0x59, 0x5A}
ROLE_TEXT = {1: "Avertisment (nivel 1)", 2: "Protecție (nivel 2, taie)"}


def level(f: dict) -> int | None:
    """Nivelul pragului după poziție: primul din pereche (…_a) = 1, al doilea (…_b) = 2."""
    return (1 if f["key"].endswith("_a") else 2) if f.get("pair") else None
READ_CMDS = sorted({f["cmd"] for f in FIELDS})
WRITE_OFFSET = 0x40


def raw_get(block: bytes, f: dict) -> int:
    return int.from_bytes(block[f["off"]:f["off"] + f["size"]], "big")


def raw_to_value(f: dict, raw: int) -> float:
    if f["kind"] == "temp":
        return raw - 40
    if f["kind"] == "cur_dis":
        return round((30000 - raw) * f["scale"], 1)
    if f["kind"] == "cur_chg":
        return round((raw - 30000) * f["scale"], 1)
    return round(raw * f["scale"], 3)


def value_to_raw(f: dict, value: float) -> int:
    if not (f["min"] - 1e-9 <= value <= f["max"] + 1e-9):
        raise ValueError(f"{f['group']}, {f['label']}: trebuie între {f['min']:g} și {f['max']:g} {f['unit']}")
    if f["kind"] == "temp":
        return int(round(value)) + 40
    if f["kind"] == "cur_dis":
        return 30000 - int(round(value / f["scale"]))
    if f["kind"] == "cur_chg":
        return 30000 + int(round(value / f["scale"]))
    return int(round(value / f["scale"]))


def fmt(f: dict, v: float) -> str:
    d = 3 if f["scale"] == 0.001 and f["unit"] == "V" else 1 if f["scale"] == 0.1 else 0
    if f["unit"] == "Ah":
        d = 1
    return (f"{v:.{d}f}" if d else f"{v:g}").replace(".", ",") + f" {f['unit']}"


def describe(blocks: dict[int, bytes]) -> list[dict]:
    """Câmpurile cu valorile citite; pentru perechi, rolul după poziție (nivel 1 = avertisment, nivel 2 = protecție)
    și `alert` pe protecție dacă e mai puțin severă decât avertismentul (BMS-ul ar tăia înainte să avertizeze)."""
    vals = {}
    for f in FIELDS:
        b = blocks.get(f["cmd"])
        vals[f["key"]] = raw_to_value(f, raw_get(b, f)) if b else None
    out = []
    for f in FIELDS:
        v, lv = vals[f["key"]], level(f)
        sure = f["cmd"] in CONFIRMED
        item = dict(key=f["key"], group=f["group"], label=f["label"], value=v,
                    text=fmt(f, v) if v is not None else "–", unit=f["unit"], min=f["min"], max=f["max"],
                    step=f["scale"] if f["kind"] not in ("temp",) else 1, risky=bool(f.get("risky")),
                    note=f.get("note", ""), role="", level=lv, role_text="", confirmed=sure, alert="")
        if lv:
            item["role"] = "protecție" if lv == 2 else "avertisment"
            item["role_text"] = ROLE_TEXT[lv] + ("" if sure else " — neconfirmat pe hardware")
            warn = BY_KEY[f["pair"] + "_a"]
            wv = vals[warn["key"]]
            if lv == 2 and v is not None and wv is not None and (v < wv if f["hi"] else v > wv):
                item["alert"] = (f"Protecția ({fmt(f, v)}) e mai puțin severă decât avertismentul ({fmt(warn, wv)}): "
                                 "BMS-ul taie înainte să avertizeze." + ("" if sure else " Ordinea pragurilor la "
                                 "acest grup e neconfirmată pe hardware."))
        out.append(item)
    return out


def read_blocks(drv) -> dict[int, bytes]:
    out = {}
    for cmd in READ_CMDS:
        try:
            out[cmd] = drv._query(cmd)[0]
        except DriverError:
            pass
    return out


def write_field(drv, key: str, value) -> dict:
    """Scrie un câmp: citește blocul, schimbă doar câmpul, îl trimite, apoi îl citește înapoi și compară DOAR
    octeții câmpului. La 0x5F (echilibrare) octeții 4..7 se schimbă singuri între două citiri, fără nicio scriere
    (văzut pe hardware 2026-10-28, firmware 31_2306): compararea întregului bloc dădea eroare deși valoarea se
    aplicase. Se trimite totuși înapoi blocul întreg, cu octeții citiți atunci și pentru câmpurile nedocumentate —
    pe hardware n-a stricat nimic, dar e NEVERIFICAT ce reprezintă acei octeți."""
    f = BY_KEY.get(key)
    if f is None:
        raise ValueError(f"setare necunoscută: {key}")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{f['label']}: valoare invalidă") from None
    raw = value_to_raw(f, value)
    old_block = drv._query(f["cmd"])[0]
    old = raw_to_value(f, raw_get(old_block, f))
    lv = level(f)
    res = dict(label=f"{f['group']}, {f['label']}" + (f" — {ROLE_TEXT[lv].lower()}" if lv else ""), old=fmt(f, old), new=fmt(f, raw_to_value(f, raw)),
               changed=raw != raw_get(old_block, f))
    if not res["changed"]:
        return res
    new_block = bytearray(old_block)
    new_block[f["off"]:f["off"] + f["size"]] = raw.to_bytes(f["size"], "big")
    drv._exchange(build_request(drv.address, f["cmd"] - WRITE_OFFSET, bytes(new_block)), 13)
    time.sleep(0.5)
    back = drv._query(f["cmd"])[0]
    got = raw_get(back, f)
    if got != raw:
        now = raw_to_value(f, got)
        if got == raw_get(old_block, f):
            raise DriverError(f"{res['label']}: BMS-ul nu a aplicat modificarea (a rămas {fmt(f, now)})")
        raise DriverError(f"{res['label']}: BMS-ul arată altceva decât s-a trimis ({fmt(f, now)}) — verificați în "
                          "aplicația Daly")
    if back != bytes(new_block):
        log.warning("Daly 0x%02X: după scrierea „%s” s-au schimbat și alți octeți: %s → %s (câmpul e corect)",
                    f["cmd"], key, bytes(new_block).hex(), back.hex())
    return res

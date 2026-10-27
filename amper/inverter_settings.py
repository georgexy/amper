"""Setările invertorului: citire, descriere pentru aplicație, scriere verificată (citire înapoi) și jurnal.

Funcțiile de aici sunt blocante (vorbesc pe portul serial) și rulează în thread.
Driverul trebuie să aibă read_settings(), read_holding(regs), write_register(reg, val), write_registers(start, vals).
"""
from __future__ import annotations

import time

from .drivers.base import DriverError
from .drivers.growatt_spf import (REG_BATTERY_TYPE, REG_CLOCK, SETTINGS, SETTINGS_BY_KEY, check_value,
                                  setting_spec)


def _fmt(sp: dict, raw: int | None) -> str:
    if raw is None:
        return "–"
    if sp["kind"] == "enum":
        return sp["options"].get(raw, f"cod {raw}")
    v = raw * sp["scale"]
    return (f"{v:.1f}" if sp["scale"] < 1 else f"{v:g}").replace(".", ",") + f" {sp['unit']}"


def describe(regs: dict[int, int]) -> list[dict]:
    bt = regs.get(REG_BATTERY_TYPE)
    out = []
    for d in SETTINGS:
        sp = setting_spec(d, bt)
        raw = regs.get(d["reg"])
        item = dict(key=d["key"], reg=d["reg"], group=d["group"], label=d["label"], kind=sp["kind"], raw=raw,
                    text=_fmt(sp, raw), risky=bool(d.get("risky")), note=d.get("note", ""))
        if sp["kind"] == "enum":
            item["options"] = [[k, v] for k, v in sp["options"].items()]
        else:
            item.update(min=sp["min"] * sp["scale"], max=sp["max"] * sp["scale"], step=sp["scale"], unit=sp["unit"],
                        value=None if raw is None else round(raw * sp["scale"], 3))
        out.append(item)
    return out


def clock(regs: dict[int, int]) -> str | None:
    c = [regs.get(REG_CLOCK + i) for i in range(6)]
    if None in c:
        return None
    year = c[0] + 2000 if c[0] < 100 else c[0]       # protocolul zice „offset 2000”; firmware-ul tău trimite anul întreg
    return f"{year:04d}-{c[1]:02d}-{c[2]:02d} {c[3]:02d}:{c[4]:02d}:{c[5]:02d}"


def to_raw(key: str, value, battery_type: int | None) -> int:
    d = SETTINGS_BY_KEY.get(key)
    if d is None:
        raise ValueError(f"setare necunoscută: {key}")
    sp = setting_spec(d, battery_type)
    try:
        raw = int(value) if sp["kind"] == "enum" else round(float(value) / sp["scale"])
    except (TypeError, ValueError):
        raise ValueError(f"{d['label']}: valoare invalidă") from None
    check_value(d, raw, battery_type)
    return raw


def write_setting(driver, key: str, value) -> dict:
    """Scrie o setare și o verifică citind-o înapoi. Întoarce {label, old, new, regs}."""
    regs = driver.read_settings()
    d = SETTINGS_BY_KEY[key] if key in SETTINGS_BY_KEY else None
    if d is None:
        raise ValueError(f"setare necunoscută: {key}")
    bt = regs.get(REG_BATTERY_TYPE)
    raw = to_raw(key, value, bt)
    sp = setting_spec(d, bt)
    old = regs.get(d["reg"])
    res = dict(label=d["label"], old=_fmt(sp, old), new=_fmt(sp, raw), changed=old != raw)
    if old != raw:
        driver.write_register(d["reg"], raw)
        after = driver.read_holding([d["reg"]]).get(d["reg"])
        if after != raw:
            raise DriverError(f"{d['label']}: comanda a fost trimisă, dar invertorul arată tot {_fmt(sp, after)}")
        regs = driver.read_settings()
    res["regs"] = regs
    return res


def sync_clock(driver, now: float | None = None) -> dict:
    """Pune ceasul invertorului după ceasul Pi-ului (ora locală)."""
    regs = driver.read_settings()
    before = clock(regs)
    year_raw = regs.get(REG_CLOCK)
    lt = time.localtime(now or time.time())
    year = lt.tm_year if (year_raw or 0) >= 100 else lt.tm_year - 2000   # același format ca cel citit
    driver.write_registers(REG_CLOCK, [year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec])
    regs = driver.read_settings()
    after = clock(regs)
    if not after or after[:13] != time.strftime("%Y-%m-%d %H", lt):
        raise DriverError(f"Ceas: comanda a fost trimisă, dar invertorul arată {after}")
    return dict(label="Ceasul invertorului", old=before or "–", new=after, changed=True, regs=regs)

"""Căutarea dispozitivelor pentru ghidul de configurare: porturile USB-serial libere + modulele Daly Bluetooth.

Rulează în serviciul amper (utilizatorul amper, grupul dialout), într-un fir separat. Porturile folosite deja de
dispozitivele din config nu se ating (sunt deschise de citiri); apar în rezultat ca „deja configurate”.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

NAMES = {"daly": "Daly", "jk": "JK", "seplos": "Seplos", "growatt_spf": "Invertor Growatt SPF"}


def used_ports(cfg: dict) -> dict[str, str]:
    return {d["port"]: d.get("name", d["id"]) for d in cfg.get("devices", [])
            if str(d.get("port", "")).startswith("/dev/")}


# Tipurile de BMS care se pot alege pentru un modul Bluetooth (ecranul „Adaugă dispozitiv”).
# supported=False: apar în listă, dar nu se pot adăuga încă (nu există driver).
BLE_TYPES = [
    {"type": "daly", "label": "Daly", "supported": True},
    {"type": "jk", "label": "JK BMS (Jikong)", "supported": True},
    {"type": "jbd", "label": "JBD / Xiaoxiang", "supported": False},
    {"type": "ant", "label": "ANT BMS", "supported": False},
]


def used_ble(cfg: dict) -> set[str]:
    """Adresele/numele Bluetooth folosite deja: din config și din legăturile găsite automat (daly-auto.json)."""
    out = {str(d["ble"]).upper() for d in cfg.get("devices", []) if d.get("ble")}
    try:
        from .drivers.daly_auto import load_bindings
        for b in load_bindings().values():
            for k in ("ble", "name"):
                if b.get(k):
                    out.add(str(b[k]).upper())
    except Exception:
        pass
    return out


def scan_cable(cfg: dict, progress=lambda msg: None) -> dict:
    """Doar porturile USB libere (cele folosite de dispozitivele din config nu se ating și nu apar)."""
    res = scan(cfg, progress, ble_seconds=0)
    return {"found": res["found"], "ports": [p for p in res["ports"] if not p["result"].startswith("folosit de")]}


def scan_ble(cfg: dict, seconds: float = 10) -> dict:
    """Toate modulele Bluetooth din jur care au nume, fără cele legate deja. Un modul conectat la telefon
    (sau la Amper) nu se anunță, deci nu apare."""
    from .drivers import ble
    used = used_ble(cfg)
    found = []
    for d in ble.scan(seconds):
        if not d["name"] or d["address"].upper() in used or d["name"].upper() in used:
            continue
        from .drivers.jk import JK_NAME
        found.append({"ble": d["address"], "name": d["name"], "rssi": d["rssi"],
                      "guess": "daly" if ble.is_daly_name(d["name"]) else "jk" if JK_NAME.match(d["name"]) else None})
    return {"found": found, "types": BLE_TYPES}


def scan(cfg: dict, progress=lambda msg: None, ble_seconds: float = 8) -> dict:
    from .__main__ import list_ports, probe
    used = used_ports(cfg)
    found, ports = [], []
    for p in list_ports():
        short = p.rsplit("/", 1)[-1]
        if p in used:
            ports.append({"port": p, "short": short, "result": f"folosit de {used[p]}"})
            continue
        progress(f"Încerc portul {short}…")
        try:
            res = probe(p)
        except Exception as e:                      # port care dispare, permisiuni
            log.warning("căutare %s: %s", p, e)
            res = []
        ports.append({"port": p, "short": short, "result": ", ".join(r.split(" ", 1)[0] for r in res) or "nimic"})
        for r in res:
            typ, extra = r.split(" ", 1)
            item = {"type": typ, "port": p, "short": short, **json.loads(extra), "link": "cablu"}
            item["label"] = NAMES.get(typ, typ) + (f" (adresa {item['address']})" if typ == "seplos" else "")
            found.append(item)
    ble_note = ""
    if not ble_seconds:
        return {"found": found, "ports": ports, "ble_note": ble_note}
    progress("Caut modulele Daly prin Bluetooth (8 s)…")
    try:
        from .drivers import ble, daly_auto
        mods = [d for d in ble.scan(ble_seconds) if ble.is_daly_name(d["name"])
                and not daly_auto.is_excluded({"type": "ble", "ble": d["address"], "name": d["name"]})]
        for i, d in enumerate(mods, 1):
            # citit, ca să se vadă dacă e BMS (un egalizator activ are tot nume „DL-…” și SOC 0)
            progress(f"Citesc modulul {d['name']} ({i} din {len(mods)})…")
            b = {"type": "ble", "ble": d["address"], "name": d["name"]}
            try:
                ok = daly_auto._probe({"id": "cautare", "name": d["name"], "type": "daly"}, b)
            except Exception as e:                   # nu răspunde: îl arătăm, nebifat
                log.warning("citire %s: %s", d["name"], e)
                ok = False
            found.append({"type": "daly", "ble": d["address"], "name": d["name"], "rssi": d["rssi"],
                          "link": "Bluetooth", "label": f"Daly {d['name']}", "check": b.get("check") or {},
                          "ok": ok, "keys": sorted(daly_auto.link_keys(b)), "binding": {"type": "ble", "ble": d["address"],
                                                                                        "name": d["name"]}})
    except Exception as e:                           # fără adaptor, fără bleak
        ble_note = f"Bluetooth indisponibil ({e.__class__.__name__})"
    return {"found": found, "ports": ports, "ble_note": ble_note}


def add_device(existing: list[dict], c: dict) -> dict:
    """Un dispozitiv nou, adăugat la cele existente: id și nume care nu se repetă."""
    typ = c.get("type")
    if typ == "daly" and c.get("ble") is None and not str(c.get("port", "")).startswith("/dev/"):
        raise ValueError("Alege un modul Bluetooth sau un port.")
    if typ not in NAMES:
        t = next((t for t in BLE_TYPES if t["type"] == typ), None)
        raise ValueError(f"{t['label'] if t else typ}: nu e suportat încă.")
    dev = build_devices([c])[0]
    ids, names = {d["id"] for d in existing}, {d.get("name") for d in existing}
    base = {"daly": "daly", "jk": "jk", "seplos": "seplos", "growatt_spf": "inverter"}[typ]
    n = sum(1 for d in existing if d.get("type") == typ) + 1
    while f"{base}{n}" in ids:
        n += 1
    dev["id"] = f"{base}{n}"
    if not str(c.get("name") or "").strip():
        dev["name"] = f"{NAMES[typ]} {n}"
        while dev["name"] in names:
            n += 1
            dev["name"] = f"{NAMES[typ]} {n}"
    if typ in ("daly", "jk") and c.get("ble") and c.get("ble_name"):
        dev["ble_name"] = str(c["ble_name"])[:40]
    return dev


def build_devices(chosen: list[dict]) -> list[dict]:
    """Din alegerile din ghid: intrările `devices` pentru config (id-uri și nume stabile, în ordine)."""
    out, count = [], {}
    total = {t: sum(1 for c in chosen if c.get("type") == t) for t in ("daly", "jk", "seplos", "growatt_spf")}
    for c in chosen:
        typ = c.get("type")
        if typ not in total:
            raise ValueError(f"tip necunoscut: {typ}")
        count[typ] = count.get(typ, 0) + 1
        n = count[typ]
        base = {"daly": "daly", "jk": "jk", "seplos": "seplos", "growatt_spf": "inverter"}[typ]
        dev = {"id": f"{base}{n}" if typ != "growatt_spf" or total[typ] > 1 else base,
               "name": str(c.get("name") or "").strip()[:40] or
               (f"{NAMES[typ]} {n}" if typ != "growatt_spf" or total[typ] > 1 else NAMES[typ]),
               "type": typ}
        if typ == "daly":
            if c.get("ble"):
                dev["ble"] = str(c["ble"])
            elif str(c.get("port", "")).startswith("/dev/"):
                dev["port"] = c["port"]
                if c.get("address") is not None:
                    dev["address"] = int(c["address"])
            else:
                dev["port"] = "auto"
        elif typ == "jk":                                   # JK: Bluetooth sau modulul RS485/UART (115200)
            if c.get("ble"):
                dev["ble"] = str(c["ble"])
            elif str(c.get("port", "")).startswith("/dev/"):
                dev["port"] = c["port"]
                dev["baudrate"] = int(c.get("baudrate") or 115200)
            else:
                raise ValueError(f"{dev['name']}: alege modulul Bluetooth sau portul")
        else:
            if not str(c.get("port", "")).startswith("/dev/"):
                raise ValueError(f"{dev['name']}: lipsește portul")
            dev["port"] = c["port"]
            dev["baudrate"] = int(c.get("baudrate") or (19200 if typ == "seplos" else 9600))
            dev["address"] = int(c.get("address") if c.get("address") is not None else (0 if typ == "seplos" else 1))
        if typ in ("daly", "jk", "seplos"):
            cap = float(c.get("capacity_ah") or 0)
            if not 1 <= cap <= 2000:
                raise ValueError(f"{dev['name']}: capacitatea trebuie să fie între 1 și 2000 Ah")
            dev["capacity_ah"] = cap
        out.append(dev)
    return out

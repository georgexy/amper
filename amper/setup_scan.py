"""Căutarea dispozitivelor pentru ghidul de configurare: porturile USB-serial libere + modulele Daly Bluetooth.

Rulează în serviciul amper (utilizatorul amper, grupul dialout), într-un fir separat. Porturile folosite deja de
dispozitivele din config nu se ating (sunt deschise de citiri); apar în rezultat ca „deja configurate”.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

NAMES = {"daly": "Daly", "jk": "JK", "seplos": "Seplos", "pace": "PACE", "growatt_spf": "Invertor Growatt SPF",
         "deye": "Invertor Deye", "voltronic": "Invertor Easun / Voltronic", "srne": "Invertor Easun SMG / SRNE"}
INVERTERS = {"growatt_spf", "deye", "voltronic", "srne"}
BASE = {"daly": "daly", "jk": "jk", "seplos": "seplos", "pace": "pace", **{t: "inverter" for t in INVERTERS}}


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


def live_check(item: dict) -> dict:
    """Citește o dată dispozitivul găsit pe cablu, ca utilizatorul să vadă valori pe care le poate verifica
    („Growatt · ieșire 230 V · baterie 52,8 V — corect?”). Gol dacă citirea nu merge."""
    from .drivers import make_driver
    from .models import PackReading
    conf = {"id": "verificare", "name": item.get("label", ""), "type": item["type"], "port": item["port"],
            **{k: item[k] for k in ("baudrate", "address", "protocol", "map") if item.get(k) is not None}}
    drv = None
    try:
        drv = make_driver(conf, [conf])
        r = drv.read()
    except Exception as e:
        log.warning("verificare %s: %s", item.get("label"), e)
        return {}
    finally:
        if drv is not None:
            try:
                drv.close()
            except Exception:
                pass
    if isinstance(r, PackReading):
        return {k: v for k, v in dict(voltage=r.voltage, soc=r.soc, cells=len(r.cells or []) or None,
                                      capacity_ah=r.capacity_ah, cycles=r.cycles).items() if v is not None}
    return {k: v for k, v in dict(out_v=getattr(r, "output_voltage", None), bat_v=getattr(r, "battery_voltage", None),
                                  pv_w=getattr(r, "pv_power", None), load_w=getattr(r, "load_power", None)).items()
            if v is not None}


def scan(cfg: dict, progress=lambda msg: None, ble_seconds: float = 8, model: str | None = None,
         link: str | None = None, address: int | None = None) -> dict:
    """Detectarea automată (fără `model`) sau doar modelul ales din listă (`catalog.py`): numai protocolul lui, pe
    porturile libere (`link` = cable) sau prin Bluetooth (`link` = ble); `address` = adresa din DIP (Seplos/PACE)."""
    from .__main__ import list_ports, probe
    from .catalog import BY_ID
    only = None
    if model:
        entry = BY_ID.get(model)
        if not entry or not entry["supported"]:
            raise ValueError("Modelul ales nu e suportat încă.")
        only = {entry["driver"]}
        if link == "ble":
            return scan_ble_model(cfg, entry, progress, ble_seconds or 10)
        ble_seconds = 0
    used = used_ports(cfg)
    found, ports = [], []
    for p in list_ports():
        short = p.rsplit("/", 1)[-1]
        if p in used:
            ports.append({"port": p, "short": short, "result": f"folosit de {used[p]}"})
            continue
        progress(f"Încerc portul {short}…")
        try:
            res = probe(p, only, [address] if address is not None else None)
        except Exception as e:                      # port care dispare, permisiuni
            log.warning("căutare %s: %s", p, e)
            res = []
        ports.append({"port": p, "short": short, "result": ", ".join(r.split(" ", 1)[0] for r in res) or "nimic"})
        for r in res:
            typ, extra = r.split(" ", 1)
            item = {"type": typ, "port": p, "short": short, **json.loads(extra), "link": "cablu"}
            item["label"] = NAMES.get(typ, typ) + (f" (adresa {item['address']})" if typ in ("seplos", "pace") else "")
            progress(f"Citesc {item['label']}…")
            item["check"] = live_check(item)
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


def scan_ble_model(cfg: dict, entry: dict, progress, seconds: float) -> dict:
    """Bluetooth, pentru modelul ales: doar modulele cu numele potrivit (Daly „DL-…”, JK „JK-…”), citite o dată."""
    from .drivers import ble, daly_auto
    from .drivers.jk import JK_NAME
    progress(f"Caut modulele {entry['brand']} prin Bluetooth ({seconds:g} s)…")
    try:
        mods = ble.scan(seconds)
    except Exception as e:
        return {"found": [], "ports": [], "ble_note": f"Bluetooth indisponibil ({e.__class__.__name__})"}
    used = used_ble(cfg)
    match = ble.is_daly_name if entry["driver"] == "daly" else (lambda n: bool(JK_NAME.match(n or "")))
    mods = [d for d in mods if d["name"] and match(d["name"]) and d["address"].upper() not in used
            and d["name"].upper() not in used]
    found = []
    for i, d in enumerate(mods, 1):
        progress(f"Citesc modulul {d['name']} ({i} din {len(mods)})…")
        item = {"type": entry["driver"], "ble": d["address"], "name": d["name"], "rssi": d["rssi"], "link": "Bluetooth",
                "label": f"{entry['brand']} {d['name']}"}
        if entry["driver"] == "daly":
            b = {"type": "ble", "ble": d["address"], "name": d["name"]}
            try:
                item["ok"] = daly_auto._probe({"id": "cautare", "name": d["name"], "type": "daly"}, b)
            except Exception as e:
                log.warning("citire %s: %s", d["name"], e)
                item["ok"] = False
            item.update(check=b.get("check") or {}, keys=sorted(daly_auto.link_keys(b)), binding=b)
        found.append(item)
    note = "" if mods else f"N-am găsit niciun modul {entry['brand']} prin Bluetooth în apropiere."
    return {"found": found, "ports": [], "ble_note": note}


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
    base = BASE[typ]
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
    total = {t: sum(1 for c in chosen if c.get("type") == t) for t in BASE}
    n_inv = sum(1 for c in chosen if c.get("type") in INVERTERS)
    for c in chosen:
        typ = c.get("type")
        if typ not in total:
            raise ValueError(f"tip necunoscut: {typ}")
        count[typ] = count.get(typ, 0) + 1
        n = count[typ]
        base = BASE[typ]
        single = typ in INVERTERS and n_inv == 1                 # un singur invertor: id „inverter”, fără număr
        dev = {"id": base if single else f"{base}{n}",
               "name": str(c.get("name") or "").strip()[:40] or (NAMES[typ] if single else f"{NAMES[typ]} {n}"),
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
            dev["baudrate"] = int(c.get("baudrate") or (19200 if typ == "seplos" else 2400 if typ == "voltronic" else 9600))
            if typ == "srne" and c.get("map") in ("smg", "srne"):
                dev["map"] = c["map"]
            dev["address"] = int(c.get("address") if c.get("address") is not None else (0 if typ in ("seplos", "pace") else 1))
            if typ == "pace" and c.get("protocol") in (20, 25, "20", "25"):
                dev["protocol"] = int(c["protocol"])
        if typ in ("daly", "jk", "seplos", "pace"):
            cap = float(c.get("capacity_ah") or 0)
            if not 1 <= cap <= 2000:
                raise ValueError(f"{dev['name']}: capacitatea trebuie să fie între 1 și 2000 Ah")
            dev["capacity_ah"] = cap
        out.append(dev)
    return out

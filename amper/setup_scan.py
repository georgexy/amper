"""Căutarea dispozitivelor pentru ghidul de configurare: porturile USB-serial libere + modulele Daly Bluetooth.

Rulează în serviciul amper (utilizatorul amper, grupul dialout), într-un fir separat. Porturile folosite deja de
dispozitivele din config nu se ating (sunt deschise de citiri); apar în rezultat ca „deja configurate”.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

NAMES = {"daly": "Daly", "seplos": "Seplos", "growatt_spf": "Invertor Growatt SPF"}


def used_ports(cfg: dict) -> dict[str, str]:
    return {d["port"]: d.get("name", d["id"]) for d in cfg.get("devices", [])
            if str(d.get("port", "")).startswith("/dev/")}


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
    progress("Caut modulele Daly prin Bluetooth (8 s)…")
    ble_note = ""
    try:
        from .drivers import ble
        for d in ble.scan(ble_seconds):
            if ble.is_daly_name(d["name"]):
                found.append({"type": "daly", "ble": d["address"], "name": d["name"], "rssi": d["rssi"],
                              "link": "Bluetooth", "label": f"Daly {d['name']}"})
    except Exception as e:                           # fără adaptor, fără bleak
        ble_note = f"Bluetooth indisponibil ({e.__class__.__name__})"
    return {"found": found, "ports": ports, "ble_note": ble_note}


def build_devices(chosen: list[dict]) -> list[dict]:
    """Din alegerile din ghid: intrările `devices` pentru config (id-uri și nume stabile, în ordine)."""
    out, count = [], {}
    total = {t: sum(1 for c in chosen if c.get("type") == t) for t in ("daly", "seplos", "growatt_spf")}
    for c in chosen:
        typ = c.get("type")
        if typ not in total:
            raise ValueError(f"tip necunoscut: {typ}")
        count[typ] = count.get(typ, 0) + 1
        n = count[typ]
        base = {"daly": "daly", "seplos": "seplos", "growatt_spf": "inverter"}[typ]
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
        else:
            if not str(c.get("port", "")).startswith("/dev/"):
                raise ValueError(f"{dev['name']}: lipsește portul")
            dev["port"] = c["port"]
            dev["baudrate"] = int(c.get("baudrate") or (19200 if typ == "seplos" else 9600))
            dev["address"] = int(c.get("address") if c.get("address") is not None else (0 if typ == "seplos" else 1))
        if typ in ("daly", "seplos"):
            cap = float(c.get("capacity_ah") or 0)
            if not 1 <= cap <= 2000:
                raise ValueError(f"{dev['name']}: capacitatea trebuie să fie între 1 și 2000 Ah")
            dev["capacity_ah"] = cap
        out.append(dev)
    return out

"""Daly cu detectare automată: modulul se leagă singur când apare, pe cablu USB sau prin Bluetooth.

Un dispozitiv Daly fără legătură în config (`port: auto`, sau încă `REPLACE-...`) caută, în fundal:
  - pe cablu: adaptoarele USB-serial noi din /dev/serial/by-id, nefolosite de alte dispozitive;
  - prin Bluetooth: modulele cu nume Daly (DL-..., DALY...), de la cel cu semnalul cel mai bun.
Primul modul care răspunde la protocolul Daly e legat de dispozitiv și ținut minte în
<directorul bazei de date>/daly-auto.json, ca după repornire să rămână aceeași ordine (Daly 1, 2, 3).
Fișierul se poate șterge ca legăturile să fie refăcute.

Un modul e legat doar dacă răspunde ca un BMS (`check_bms`): tensiune plauzibilă și capacitatea din 0x50, sau SOC și
Ah rămași nenuli. Egalizatoarele active au tot module Bluetooth „DL-…” și răspund la 0x90 cu SOC 0 — nu se leagă.
Modulele deconectate sau respinse din aplicație intră în <director>/daly-excluded.json și nu se mai leagă singure
(Setări → Dispozitive conectate → Module excluse → „Permite din nou”).

Căutarea e rară ca să nu încarce Pi-ul: la 30 s în primele 10 minute, apoi la 3 minute; adaptoarele USB noi
se încearcă imediat ce apar (verificare la 15 s). `trigger()` pornește o căutare imediat (butonul din aplicație).
"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading
import time

from .base import Driver, DriverError

log = logging.getLogger(__name__)

STATE_DIR = "/var/lib/amper"
_lock = threading.Lock()              # o singură căutare odată, pentru toate modulele Daly
_claimed: dict[str, str] = {}         # legătură ("uart:/dev/..." / "ble:AA:BB..") → id dispozitiv
_wake = threading.Condition()
_wake_gen = [0]
_ble_cache: dict = {"t": None, "items": []}     # t = None: încă nescanat (monotonic poate fi < 30 s după pornire)
_found: list[dict] = []               # ultimele module încercate (legate sau nu), cu valorile citite — pentru aplicație
_not_bms: set[str] = set()            # încercate și respinse (nu par BMS): nu se reîncearcă până la „Caută acum”


def recent_found() -> list[dict]:
    return list(_found)


def _note_found(b: dict, ok: bool, dev_id: str | None) -> None:
    item = {"label": b.get("name") or str(b.get("port", "")).rsplit("/", 1)[-1], "link": "Bluetooth" if b["type"] == "ble"
            else "cablu", "keys": sorted(link_keys(b)), "check": b.get("check") or {}, "ok": ok, "device": dev_id,
            "at": int(time.time()), "binding": {k: v for k, v in b.items() if k != "check"}}
    _found[:] = [x for x in _found if x["keys"] != item["keys"]][-19:] + [item]
    if not ok:
        _not_bms.update(item["keys"])


def set_state_dir(path: str) -> None:
    global STATE_DIR
    STATE_DIR = path


def _bind_file() -> str:
    return os.path.join(STATE_DIR, "daly-auto.json")


def _excl_file() -> str:
    return os.path.join(STATE_DIR, "daly-excluded.json")


def load_excluded() -> list[dict]:
    """Modulele pe care căutarea automată nu le mai leagă: [{key, label, at, reason}]."""
    try:
        with open(_excl_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_excluded(items: list[dict]) -> None:
    try:
        with open(_excl_file(), "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.warning("nu pot salva %s: %s", _excl_file(), e)


def link_keys(b: dict) -> set[str]:
    """Cheile după care se recunoaște o legătură: portul USB, adresa și numele Bluetooth."""
    if b.get("type") == "uart" or (b.get("port") and not b.get("ble")):
        return {f"uart:{b['port']}"} if b.get("port") else set()
    out = set()
    if b.get("ble"):
        out.add(f"ble:{str(b['ble']).upper()}")
    if b.get("name"):
        out.add(f"name:{str(b['name']).upper()}")
    return out


def is_excluded(b: dict) -> bool:
    keys = link_keys(b)
    return any(k in keys for x in load_excluded() for k in x.get("keys", []))


def exclude(b: dict, label: str, reason: str) -> None:
    items = [x for x in load_excluded() if not set(x.get("keys", [])) & link_keys(b)]
    items.append({"keys": sorted(link_keys(b)), "label": label, "reason": reason, "at": int(time.time())})
    _save_excluded(items)


def allow_again(index: int | None = None) -> list[dict]:
    """„Permite din nou”: scoate un modul (după poziția din listă) sau pe toate; căutarea poate să-l lege iar."""
    items = load_excluded()
    items = [] if index is None else [x for i, x in enumerate(items) if i != index]
    _save_excluded(items)
    trigger()
    return items


def load_bindings() -> dict:
    try:
        with open(_bind_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_binding(dev_id: str, b: dict | None) -> None:
    data = load_bindings()
    if b is None:
        data.pop(dev_id, None)
    else:
        data[dev_id] = b
    try:
        with open(_bind_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        log.warning("nu pot salva %s: %s", _bind_file(), e)


def trigger() -> None:
    """Caută acum (nu aștepta următoarea rundă)."""
    with _wake:
        _wake_gen[0] += 1
        _ble_cache["t"] = None
        _not_bms.clear()
        _wake.notify_all()


def is_auto(cfg: dict) -> bool:
    if cfg.get("type") != "daly" or cfg.get("ble"):
        return False
    port = str(cfg.get("port") or "auto")
    return port == "auto" or "REPLACE-" in port


def list_serial_ports() -> list[str]:
    return sorted(glob.glob("/dev/serial/by-id/*"))


def _key(b: dict) -> str:
    return f"uart:{b['port']}" if b["type"] == "uart" else f"ble:{b['ble']}"


def _make(cfg: dict, b: dict):
    from .daly import DalyBleDriver, DalyDriver
    c = {**cfg, "address": b.get("address", cfg.get("address"))}
    if c["address"] is None:
        c.pop("address")
    if b["type"] == "uart":
        return DalyDriver({**c, "port": b["port"]})
    return DalyBleDriver({**c, "ble": b["ble"], "ble_name": b.get("name")})


def check_bms(drv) -> tuple[bool, dict]:
    """Răspunde ca un BMS (nu ca un egalizator activ, care are tot modul Bluetooth „DL-…”)? Întoarce (da/nu, valorile
    citite: tensiune, SOC, capacitate, Ah rămași, celule). Ridică DriverError dacă nu răspunde deloc la 0x90."""
    from .daly import u16
    d = drv._query(0x90)[0]
    info = {"voltage": u16(d, 0) / 10, "soc": u16(d, 6) / 10}
    for cmd, fn in ((0x50, lambda x: {"capacity_ah": int.from_bytes(x[0:4], "big") / 1000}),
                    (0x93, lambda x: {"remaining_ah": int.from_bytes(x[4:8], "big") / 1000}),
                    (0x94, lambda x: {"cells": x[0], "cycles": u16(x, 5)})):
        try:
            info.update(fn(drv._query(cmd)[0]))
        except (DriverError, OSError):
            pass
    v, soc, cap = info["voltage"], info["soc"], info.get("capacity_ah") or 0
    ok = (5 <= v <= 100 and 0 <= soc <= 100
          and (1 <= cap <= 2000 or (soc > 0 and (info.get("remaining_ah") or 0) > 0)))
    return ok, info


def _probe(cfg: dict, b: dict) -> bool:
    """E un BMS Daly pe legătura asta? Încearcă ambele adrese de gazdă (0x40 și 0x80); valorile citite rămân în
    b["check"] (le arată aplicația, ca un modul greșit să poată fi respins)."""
    for addr in ((0x40, 0x80) if b["type"] == "uart" else (0x80, 0x40)):
        drv = _make(cfg, {**b, "address": addr})
        try:
            ok, info = check_bms(drv)
        except (DriverError, OSError):
            continue
        finally:
            drv.close()
        b["check"] = info
        if not ok:
            log.info("%s nu pare BMS (SOC %.0f%%, capacitate %s Ah): nu-l leg", b.get("name") or b.get("port"),
                     info["soc"], info.get("capacity_ah"))
            return False
        b["address"] = addr
        return True
    return False


class DalyAutoDriver(Driver):
    kind = "pack"

    def __init__(self, cfg: dict, all_cfgs: list[dict] | None = None):
        super().__init__(cfg)
        self.all_cfgs = all_cfgs or [cfg]
        self.inner = None
        self.binding = None
        self.state = "caut"
        self.started = time.monotonic()
        self._thread = None
        self._stop = threading.Event()
        self._tried_ports: set[str] = set()
        b = load_bindings().get(self.id)
        if b and not is_excluded(b):
            self._bind(b, save=False)

    @property
    def link_desc(self) -> str:
        return self.inner.link_desc if self.inner else ""

    # ---------- legare ----------
    def _bind(self, b: dict, save: bool = True) -> None:
        with _lock:
            other = _claimed.get(_key(b))
            if other and other != self.id:
                return
            _claimed[_key(b)] = self.id
        self.inner = _make(self.cfg, b)
        self.state = "legat"
        if save:
            _save_binding(self.id, b)
        self.binding = b
        log.info("%s: legat la %s (adresa gazdei 0x%02X)", self.id, self.link_desc, b.get("address", 0))

    def unbind(self) -> dict | None:
        """Deconectare din aplicație: uită legătura (și din daly-auto.json); poziția rămâne liberă și căutarea
        continuă, fără modulele excluse. Întoarce legătura veche."""
        b = getattr(self, "binding", None)
        with _lock:
            for k in [k for k, v in _claimed.items() if v == self.id]:
                _claimed.pop(k)
        if self.inner is not None:
            self.inner.close()
        self.inner, self.binding, self.state = None, None, "caut"
        _save_binding(self.id, None)
        self.started = time.monotonic()                   # caută des, ca la pornire
        return b

    def _used_ports(self) -> set[str]:
        return {str(d.get("port")) for d in self.all_cfgs if d.get("port") and not is_auto(d)}

    def _used_ble(self) -> set[str]:
        return {str(d["ble"]).upper() for d in self.all_cfgs if d.get("ble")}

    def _search_once(self) -> bool:
        with _lock:
            claimed = set(_claimed)
        # 1) cablu: adaptoare USB nefolosite (fiecare încercat o dată, apoi doar dacă reapare)
        present = set(list_serial_ports())
        self._tried_ports &= present
        for port in sorted(present - self._used_ports() - self._tried_ports):
            b = {"type": "uart", "port": port}
            if _key(b) in claimed or is_excluded(b):
                continue
            self._tried_ports.add(port)
            ok = _probe(self.cfg, b)
            if b.get("check"):
                _note_found(b, ok, self.id if ok else None)
            if ok:
                self._bind(b)
                return True
        # 2) Bluetooth: rezultatul scanării e comun tuturor modulelor (30 s)
        from . import ble
        now = time.monotonic()
        if _ble_cache["t"] is None or now - _ble_cache["t"] > 30:
            try:
                found = [d for d in ble.scan(8) if ble.is_daly_name(d["name"])]
                _ble_cache["items"] = sorted(found, key=lambda d: -(d.get("rssi") or -999))   # semnalul cel mai bun întâi
            except DriverError as e:
                _ble_cache["items"] = []
                self.state = f"caut (Bluetooth: {e})"
            _ble_cache["t"] = time.monotonic()
        for d in _ble_cache["items"]:
            b = {"type": "ble", "ble": d["address"], "name": d["name"]}
            if _key(b) in claimed or d["address"].upper() in self._used_ble() or is_excluded(b):
                continue
            if link_keys(b) & _not_bms:
                continue                                   # deja încercat: nu e BMS (egalizator activ)
            ok = _probe(self.cfg, b)
            if b.get("check"):
                _note_found(b, ok, self.id if ok else None)
            if ok:
                self._bind(b)
                return True
        return False

    def _search_loop(self) -> None:
        while not self._stop.is_set() and self.inner is None:
            try:
                with _search_gate:
                    if self.inner is None:
                        self._search_once()
            except Exception:
                log.exception("%s: eroare la căutare", self.id)
            if self.inner is not None:
                break
            slow = time.monotonic() - self.started > 600
            deadline = time.monotonic() + (180 if slow else 30)
            ports = set(list_serial_ports())
            gen = _wake_gen[0]
            while time.monotonic() < deadline and not self._stop.is_set():
                with _wake:
                    _wake.wait(15)
                if _wake_gen[0] != gen:                     # „Caută acum” din aplicație
                    break
                if set(list_serial_ports()) - ports:        # a apărut un adaptor USB nou
                    break

    # ---------- interfața driverului ----------
    def read(self):
        if self.inner is not None:
            return self.inner.read()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._search_loop, name=f"caut-{self.id}", daemon=True)
            self._thread.start()
        raise DriverError(f"{self.name}: încă negăsit — {self.state} (cablu USB sau Bluetooth)")

    def close(self) -> None:
        self._stop.set()
        if self.inner is not None:
            self.inner.close()


_search_gate = threading.Lock()      # căutările modulelor Daly se fac pe rând

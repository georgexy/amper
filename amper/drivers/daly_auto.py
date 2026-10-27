"""Daly cu detectare automată: modulul se leagă singur când apare, pe cablu USB sau prin Bluetooth.

Un dispozitiv Daly fără legătură în config (`port: auto`, sau încă `REPLACE-...`) caută, în fundal:
  - pe cablu: adaptoarele USB-serial noi din /dev/serial/by-id, nefolosite de alte dispozitive;
  - prin Bluetooth: modulele cu nume Daly (DL-..., DALY...), de la cel cu semnalul cel mai bun.
Primul modul care răspunde la protocolul Daly e legat de dispozitiv și ținut minte în
<directorul bazei de date>/daly-auto.json, ca după repornire să rămână aceeași ordine (Daly 1, 2, 3).
Fișierul se poate șterge ca legăturile să fie refăcute.

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


def set_state_dir(path: str) -> None:
    global STATE_DIR
    STATE_DIR = path


def _bind_file() -> str:
    return os.path.join(STATE_DIR, "daly-auto.json")


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


def _probe(cfg: dict, b: dict) -> bool:
    """Răspunde un BMS Daly pe legătura asta? Încearcă ambele adrese de gazdă (0x40 și 0x80)."""
    for addr in ((0x40, 0x80) if b["type"] == "uart" else (0x80, 0x40)):
        drv = _make(cfg, {**b, "address": addr})
        try:
            drv._query(0x90)
            b["address"] = addr
            return True
        except (DriverError, OSError):
            continue
        finally:
            drv.close()
    return False


class DalyAutoDriver(Driver):
    kind = "pack"

    def __init__(self, cfg: dict, all_cfgs: list[dict] | None = None):
        super().__init__(cfg)
        self.all_cfgs = all_cfgs or [cfg]
        self.inner = None
        self.state = "caut"
        self.started = time.monotonic()
        self._thread = None
        self._stop = threading.Event()
        self._tried_ports: set[str] = set()
        b = load_bindings().get(self.id)
        if b:
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
        log.info("%s: legat la %s (adresa gazdei 0x%02X)", self.id, self.link_desc, b.get("address", 0))

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
            if _key(b) in claimed:
                continue
            self._tried_ports.add(port)
            if _probe(self.cfg, b):
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
            if _key(b) in claimed or d["address"].upper() in self._used_ble():
                continue
            if _probe(self.cfg, b):
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

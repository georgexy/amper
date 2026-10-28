"""Driver pentru BMS-urile PACE (și clonele lor) pe RS485/RS232: Ecobat PowerCube (BMS Daren), Pylon și multe
baterii de rack/perete — protocolul ASCII „paceic”, aceeași familie ca Seplos (vezi seplos.py și
docs/powercube-p15a.md).

Cadru: '~' VER ADR CID1(46) CID2 LENGTH INFO CHKSUM '\\r' (hex ASCII), ca la Seplos; diferă VER și conținutul:
  VER 0x25 = PACE „nou” (programul PbmsTools): 0x42 valori, 0x44 stări, 0xC1 versiunea hardware.
  VER 0x20 = „Pylon” (și Seplos V2, recunoscut după octetul 10 din 0x42 și citit ca în seplos.py).
Formatul răspunsurilor după documentația publică a proiectului esphome-pace-bms (nkinnan); cadrele din
tests/test_pace.py sunt exemplele de acolo. NETESTAT pe un PowerCube real. Driverul doar CITEȘTE.
"""
from __future__ import annotations

import time

from ..models import PackReading
from . import seplos
from .base import DriverError, SerialDriver
from .seplos import _Cursor, frame_checksum, length_field

VERSIONS = (0x25, 0x20)          # ordinea încercărilor când `protocol` nu e dat
PROBE_WAIT = 0.8                 # s, la căutarea pe porturi (amper scan, ghidul)


def build_request(ver: int, address: int, cid2: int, info: str | None = None) -> bytes:
    info = f"{address:02X}" if info is None else info
    body = f"{ver:02X}{address:02X}46{cid2:02X}{length_field(len(info))}{info}"
    return f"~{body}{frame_checksum(body)}\r".encode("ascii")


def parse_response(raw: bytes) -> tuple[int, int, bytes]:
    """(VER, ADR, INFO) dintr-un răspuns valid; altfel DriverError."""
    text = raw.decode("ascii", errors="replace").strip()
    start = text.find("~")
    if start < 0:
        raise DriverError("PACE: răspuns fără '~'")
    text = text[start + 1:]
    if len(text) < 16:
        raise DriverError(f"PACE: răspuns prea scurt ({len(text)} caractere)")
    body, chk = text[:-4], text[-4:]
    if frame_checksum(body) != chk.upper():
        raise DriverError("PACE: checksum greșit")
    rtn = int(body[6:8], 16)
    if rtn != 0:
        raise DriverError(f"PACE: BMS-ul a răspuns cu codul de eroare RTN=0x{rtn:02X}")
    if length_field(int(body[8:12], 16) & 0x0FFF) != body[8:12].upper():
        raise DriverError("PACE: câmpul de lungime e greșit")
    lenid = int(body[8:12], 16) & 0x0FFF
    info = body[12:12 + lenid]
    if len(info) != lenid:
        raise DriverError("PACE: lungime INFO greșită")
    return int(body[0:2], 16), int(body[2:4], 16), bytes.fromhex(info)


def _ud_offset(info: bytes) -> int | None:
    """Poziția octetului „definit de utilizator” din răspunsul 0x42 (după curent, tensiune, Ah rămași)."""
    try:
        n_cells = info[2]
        i = 3 + 2 * n_cells
        n_temps = info[i]
        return i + 1 + 2 * n_temps + 6
    except IndexError:
        return None


def is_seplos(info: bytes) -> bool:
    """Seplos V2 răspunde la VER 0x20 cu 10 câmpuri proprii (SOC, SOH...), Pylon cu 2 sau 4."""
    i = _ud_offset(info)
    return i is not None and i < len(info) and info[i] == 0x0A


def _temps(r: PackReading, temps: list[float]) -> None:
    # PACE: 4 senzori pe celule, apoi MOSFET-ul și mediul (6); unele modele au mai mulți
    r.temps = temps[:4] if len(temps) >= 6 else temps
    if len(temps) >= 6:
        r.raw["temp_mosfet"] = temps[4]
        r.raw["temp_ambient"] = temps[5]


def decode_analog(info: bytes, r: PackReading, ver: int) -> None:
    """Răspunsul 0x42 (valorile): celule, temperaturi, curent, tensiune, capacitate, cicluri."""
    if ver == 0x20 and is_seplos(info):
        seplos.decode_telemetry(info, r)
        r.raw["variant"] = "seplos"
        return
    c = _Cursor(info)
    c.u8()                                      # DATA FLAG (0)
    r.raw["pack"] = c.u8()
    n_cells = c.u8()
    if not 1 <= n_cells <= 32:
        raise DriverError(f"PACE: număr de celule nepotrivit ({n_cells})")
    r.cells = [c.u16() / 1000 for _ in range(n_cells)]
    if not any(r.cells):
        raise DriverError("PACE: toate celulele la 0 V (răspuns invalid)")
    _temps(r, [(c.u16() - 2731) / 10 for _ in range(c.u8())])
    r.current = c.s16() / 100
    v = c.u16()
    # PACE 0x25 dă tensiunea în mV, Pylon în 10 mV: aleg scara apropiată de suma celulelor
    total = sum(r.cells)
    r.voltage = min((v / 1000, v / 100), key=lambda x: abs(x - total))
    rem = c.u16() / 100
    ud = c.u8() if c.remaining() else None
    r.raw["variant"] = f"0x{ver:02X}/{ud}"
    full = design = None
    if c.remaining() >= 4:
        full = c.u16() / 100
        r.cycles = c.u16()
    if ud == 4 and c.remaining() >= 6:          # Pylon cu capacitate mare: rămas/total pe 3 octeți, în mAh
        rem = int.from_bytes(info[c.i:c.i + 3], "big") / 1000
        full = int.from_bytes(info[c.i + 3:c.i + 6], "big") / 1000
        c.i += 6
    elif ver == 0x25 and c.remaining() >= 2:
        design = c.u16() / 100
    r.remaining_ah = rem
    if full:
        r.capacity_ah = full
        r.soc = round(max(0.0, min(100.0, rem / full * 100)), 1)
    if design:
        r.raw["rated_capacity_ah"] = design
        if full:
            r.soh = round(min(100.0, full / design * 100), 1)
    r.state = "charging" if r.current > 0.2 else "discharging" if r.current < -0.2 else "idle"


# Biții din răspunsul 0x44, VER 0x25 (după documentația PACE publicată de esphome-pace-bms).
PROTECT1 = ["Celulă: protecție la supratensiune", "Celulă: protecție la subtensiune",
            "Protecție la supratensiune totală", "Protecție la subtensiune totală",
            "Încărcare: protecție la supracurent", "Descărcare: protecție la supracurent",
            "Protecție la scurtcircuit", "Încărcător: protecție la tensiune mare"]
PROTECT2 = ["Încărcare: protecție la supratemperatură", "Descărcare: protecție la supratemperatură",
            "Încărcare: protecție la temperatură prea mică", "Descărcare: protecție la temperatură prea mică",
            "MOSFET: protecție la supratemperatură", "Mediu: protecție la supratemperatură",
            "Mediu: protecție la temperatură prea mică", None]          # bitul 7 = încărcată complet (informativ)
FAULTS = ["Comutatorul de încărcare (MOSFET) defect", "Comutatorul de descărcare (MOSFET) defect",
          "Senzor de temperatură (NTC) defect", "Defect de comunicație", "Celulă defectă",
          "Defect la măsurarea tensiunii", "Defect CCB", "Încălzitor defect"]
WARN1 = ["Celulă: tensiune mare", "Celulă: tensiune mică", "Tensiune totală mare", "Tensiune totală mică",
         "Încărcare: curent mare", "Descărcare: curent mare", None, None]
WARN2 = ["Încărcare: temperatură mare", "Descărcare: temperatură mare", "Încărcare: temperatură mică",
         "Descărcare: temperatură mică", "Mediu: temperatură mare", "Mediu: temperatură mică",
         "MOSFET: temperatură mare", "Capacitate rămasă mică"]
# VER 0x20, varianta Pylon: stările 1..3 (4 și 5 = celulele 1–16 defecte)
PYLON_S1 = ["Protecție la supratensiune totală", "Celulă: protecție la subtensiune",
            "Încărcare: protecție la supracurent", None, "Descărcare: protecție la supracurent",
            "Descărcare: protecție de temperatură", "Încărcare: protecție de temperatură",
            "Protecție la subtensiune totală"]


def _bits(val: int, texts: list, out: list[str]) -> None:
    for b, t in enumerate(texts):
        if t and val & (1 << b):
            out.append(t)


def _warn(v: int, what: str, out: list[str]) -> None:
    if v == 1:
        out.append(f"{what} prea mică")
    elif v == 2:
        out.append(f"{what} prea mare")
    elif v:
        out.append(f"{what}: defect (0x{v:02X})")


def decode_status(info: bytes, r: PackReading, ver: int) -> None:
    """Răspunsul 0x44: alarme, protecții, MOSFET-uri, echilibrare."""
    if r.raw.get("variant") == "seplos":
        seplos.decode_status(info, r)
        return
    c = _Cursor(info)
    c.u8()                                      # DATA FLAG
    c.u8()                                      # pachetul
    for i in range(c.u8()):
        _warn(c.u8(), f"Celula {i + 1}: tensiune", r.alarms)
    for i in range(c.u8()):
        _warn(c.u8(), f"Senzor temperatură {i + 1}", r.alarms)
    _warn(c.u8(), "Curent de încărcare", r.alarms)
    _warn(c.u8(), "Tensiune totală", r.alarms)
    _warn(c.u8(), "Curent de descărcare", r.alarms)
    if ver == 0x25:
        p1, p2, sf, cf, ff = (c.u8() for _ in range(5))
        bal = c.u16()
        w1, w2 = c.u8(), c.u8()
        _bits(p1, PROTECT1, r.protections)
        _bits(p2, PROTECT2, r.protections)
        _bits(ff, FAULTS, r.protections)
        _bits(w1, WARN1, r.alarms)
        _bits(w2, WARN2, r.alarms)
        r.charge_mos = bool(sf & 0x02)
        r.discharge_mos = bool(sf & 0x04)
        if sf & 0x20:
            r.state = "charging"
        elif sf & 0x08:
            r.state = "discharging"
        r.balancing = bool(bal)
        r.raw.update(status=sf, config=cf, balance_cells=[i + 1 for i in range(16) if bal & (1 << i)],
                     full=bool(p2 & 0x80), heater=bool(sf & 0x80))
        if sf & 0x10:
            r.protections.append("Polaritate inversată la borne")
    else:                                       # VER 0x20, Pylon
        s = [c.u8() if c.remaining() else 0 for _ in range(5)]
        _bits(s[0], PYLON_S1, r.protections)
        r.discharge_mos = bool(s[1] & 0x04)
        r.charge_mos = bool(s[1] & 0x02)
        if s[2] & 0x80:
            r.state = "charging"
        elif s[2] & 0x40:
            r.state = "discharging"
        r.raw.update(status=s, full=bool(s[2] & 0x08), heater=bool(s[2] & 0x20))
        for i in range(16):
            if (s[3] if i < 8 else s[4]) & (1 << (i % 8)):
                r.protections.append(f"Celula {i + 1}: defect (sub 1 V sau peste 4,2 V)")


class PaceDriver(SerialDriver):
    """`type: pace`, `port:` (RS485 sau RS232), `address:` (DIP-ul bateriei, 0 = master), `baudrate:` (9600),
    `protocol:` 25 / 20 / auto (implicit: se încearcă 0x25, apoi 0x20)."""
    kind = "pack"
    default_baudrate = 9600
    timeout = 0.5
    bus_gap = 0.1                               # ca la Seplos: pachetele de pe același RS485, cu o mică pauză
    answer_wait = 1.5                           # s, cât aștept răspunsul (la căutare: mai puțin)

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.address = int(cfg.get("address", 0))
        p = str(cfg.get("protocol", "auto")).lower().replace("0x", "")
        self.versions = (int(p, 16),) if p in ("20", "25") else VERSIONS
        self.ver: int | None = self.versions[0] if len(self.versions) == 1 else None
        self.hw: str | None = None

    def _query(self, ver: int, cid2: int, info: str | None = None) -> bytes:
        def reader(ser):
            buf = b""
            deadline = time.monotonic() + self.answer_wait
            while time.monotonic() < deadline:
                chunk = ser.read(max(1, getattr(ser, "in_waiting", 0)))
                if chunk:
                    buf += chunk
                    if b"~" in buf and b"\r" in buf[buf.index(b"~"):]:
                        break
            return buf

        raw = self.transact(build_request(ver, self.address, cid2, info), reader)
        if not raw:
            raise DriverError(f"PACE {self.id}: fără răspuns (verificați adresa din DIP, viteza și cablul RS485)")
        _, _, data = parse_response(raw[:raw.index(b"\r") + 1] if b"\r" in raw else raw)
        return data

    def _analog(self) -> tuple[int, bytes]:
        if self.ver is not None:
            return self.ver, self._query(self.ver, 0x42)
        err = None
        for ver in self.versions:               # prima citire: aflu versiunea protocolului
            try:
                data = self._query(ver, 0x42)
            except DriverError as e:
                err = e
                continue
            self.ver = ver
            return ver, data
        raise err or DriverError(f"PACE {self.id}: fără răspuns")

    def read(self) -> PackReading:
        r = PackReading(device_id=self.id, ts=time.time())
        ver, data = self._analog()
        decode_analog(data, r, ver)
        r.raw["protocol"] = f"0x{ver:02X}"
        if ver == 0x25 and self.hw is None:      # o singură dată: modelul plăcii (ex. „P16S100A-…”)
            try:
                self.hw = self._query(ver, 0xC1, "").decode("ascii", "replace").strip(" \x00")
            except DriverError:
                self.hw = ""
        if self.hw:
            r.raw["hw"] = self.hw
        if self.cfg.get("capacity_ah"):
            if r.remaining_ah is not None and not r.soc and r.capacity_ah is None:
                r.soc = round(min(100.0, r.remaining_ah / float(self.cfg["capacity_ah"]) * 100), 1)
            r.capacity_ah = float(self.cfg["capacity_ah"])
        try:
            decode_status(self._query(ver, 0x44), r, ver)
        except (DriverError, IndexError):
            pass
        return r

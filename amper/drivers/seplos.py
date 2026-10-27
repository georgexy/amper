"""Driver Seplos BMS V1/V2 — protocol ASCII-hex (familia "PACE"/Pylon v2.0) pe RS485.

Cadru: '~' VER(20) ADR CID1(46) CID2 LENGTH INFO CHKSUM '\\r'   (toate în hex ASCII)
  CID2 0x42 = telemetrie (tensiuni, curent, SOC...), 0x44 = alarme/stări
  LENGTH = LCHKSUM(4 biți) + LENID(12 biți = nr. caractere INFO)
  CHKSUM = complementul față de 2 al sumei codurilor ASCII (16 biți)

Exemplu cerere telemetrie pachet 0:  ~20004642E00200FD37\\r
"""
from __future__ import annotations

import time

from ..models import PackReading
from .base import DriverError, SerialDriver


def length_field(info_len: int) -> str:
    lenid = info_len & 0x0FFF
    s = (lenid & 0xF) + ((lenid >> 4) & 0xF) + ((lenid >> 8) & 0xF)
    lchk = (~s + 1) & 0xF
    return f"{(lchk << 12) | lenid:04X}"


def frame_checksum(body: str) -> str:
    s = sum(body.encode("ascii"))
    return f"{(~s + 1) & 0xFFFF:04X}"


def build_request(address: int, cid2: int, info: str | None = None) -> bytes:
    info = f"{address:02X}" if info is None else info
    body = f"20{address:02X}46{cid2:02X}{length_field(len(info))}{info}"
    return f"~{body}{frame_checksum(body)}\r".encode("ascii")


def parse_response(raw: bytes) -> bytes:
    """Validează un răspuns și întoarce câmpul INFO ca octeți."""
    text = raw.decode("ascii", errors="replace").strip()
    start = text.find("~")
    if start < 0:
        raise DriverError("Seplos: răspuns fără '~'")
    text = text[start + 1:]
    if len(text) < 16:
        raise DriverError(f"Seplos: răspuns prea scurt ({len(text)} caractere)")
    body, chk = text[:-4], text[-4:]
    if frame_checksum(body) != chk.upper():
        raise DriverError("Seplos: checksum greșit")
    rtn = int(body[6:8], 16)
    if rtn != 0:
        raise DriverError(f"Seplos: BMS a răspuns cu cod de eroare RTN=0x{rtn:02X}")
    lenid = int(body[8:12], 16) & 0x0FFF
    info = body[12:12 + lenid]
    if len(info) != lenid:
        raise DriverError("Seplos: lungime INFO greșită")
    return bytes.fromhex(info)


class _Cursor:
    def __init__(self, b: bytes):
        self.b, self.i = b, 0

    def u8(self) -> int:
        v = self.b[self.i]
        self.i += 1
        return v

    def u16(self) -> int:
        v = (self.b[self.i] << 8) | self.b[self.i + 1]
        self.i += 2
        return v

    def s16(self) -> int:
        v = self.u16()
        return v - 0x10000 if v & 0x8000 else v

    def remaining(self) -> int:
        return len(self.b) - self.i


def decode_telemetry(info: bytes, r: PackReading) -> None:
    c = _Cursor(info)
    c.u8()                          # DATA FLAG
    r.raw["pack"] = c.u8()          # nr. pachet
    n_cells = c.u8()
    r.cells = [c.u16() / 1000 for _ in range(n_cells)]
    n_temps = c.u8()
    temps = [(c.u16() - 2731) / 10 for _ in range(n_temps)]
    # Seplos: primele 4 = celule, apoi ambient, apoi MOSFET (dacă există)
    r.temps = temps[:4] if n_temps >= 4 else temps
    if n_temps >= 6:
        r.raw["temp_ambient"] = temps[4]
        r.raw["temp_mosfet"] = temps[5]
    r.current = c.s16() / 100
    r.voltage = c.u16() / 100
    r.remaining_ah = c.u16() / 100
    c.u8()                          # nr. câmpuri custom (10)
    r.capacity_ah = c.u16() / 100
    r.soc = c.u16() / 10
    r.raw["rated_capacity_ah"] = c.u16() / 100
    r.cycles = c.u16()
    r.soh = c.u16() / 10
    if c.remaining() >= 2:
        r.raw["port_voltage"] = c.u16() / 100
    r.state = "charging" if r.current > 0.2 else "discharging" if r.current < -0.2 else "idle"


# Octeții „alarm event” din răspunsul 0x44 (câmpurile custom), după documentația Seplos V2 — DE VERIFICAT pe hardware.
# custom[i] → biții 0..7: (text, nivel); nivel "p" = protecție/defect (critic), "a" = alarmă (avertisment),
# None = doar informativ (nu se raportează).
ALARM_EVENTS = {
    0: [("Senzor de tensiune defect", "p"), ("Senzor de temperatură defect", "p"), ("Senzor de curent defect", "p"),
        ("Comutator defect", "p"), ("Defect: diferență de tensiune între celule", "p"),
        ("Comutatorul de încărcare defect", "p"), ("Comutatorul de descărcare defect", "p"),
        ("Comutatorul de limitare a curentului defect", "p")],
    1: [("Celulă: tensiune mare", "a"), ("Celulă: protecție la supratensiune", "p"),
        ("Celulă: tensiune mică", "a"), ("Celulă: protecție la subtensiune", "p"),
        ("Tensiune totală mare", "a"), ("Protecție la supratensiune totală", "p"),
        ("Tensiune totală mică", "a"), ("Protecție la subtensiune totală", "p")],
    2: [("Încărcare: temperatură mare", "a"), ("Încărcare: protecție la supratemperatură", "p"),
        ("Încărcare: temperatură mică", "a"), ("Încărcare: protecție la temperatură prea mică", "p"),
        ("Descărcare: temperatură mare", "a"), ("Descărcare: protecție la supratemperatură", "p"),
        ("Descărcare: temperatură mică", "a"), ("Descărcare: protecție la temperatură prea mică", "p")],
    3: [("Mediu: temperatură mare", "a"), ("Mediu: protecție la supratemperatură", "p"),
        ("Mediu: temperatură mică", "a"), ("Mediu: protecție la temperatură prea mică", "p"),
        ("Electronica de putere: protecție la supratemperatură", "p"), ("Electronica de putere: temperatură mare", "a"),
        ("Încălzire celule pornită", None), (None, None)],
    4: [("Încărcare: curent mare", "a"), ("Încărcare: protecție la supracurent", "p"),
        ("Descărcare: curent mare", "a"), ("Descărcare: protecție la supracurent", "p"),
        ("Protecție la supracurent de scurtă durată", "p"), ("Protecție la scurtcircuit la ieșire", "p"),
        ("Blocat după supracurent de scurtă durată", "p"), ("Blocat după scurtcircuit la ieșire", "p")],
    5: [("Protecție la tensiune mare de încărcare", "p"), ("Așteaptă reîncărcarea intermitentă", None),
        ("Capacitate rămasă mică", "a"), ("Protecție: capacitate rămasă prea mică", "p"),
        ("Încărcare interzisă: celulă cu tensiune prea mică", "p"), ("Protecție la inversarea polarității la ieșire", "p"),
        ("Defect de conexiune la ieșire", "p"), (None, None)],
}


def decode_status(info: bytes, r: PackReading) -> None:
    """Stări și alarme (CID2 0x44). Poziția octeților de după listele de alarme
    e conform documentației Seplos V2 — DE VERIFICAT pe hardware."""
    c = _Cursor(info)
    c.u8()                          # DATA FLAG
    c.u8()                          # nr. pachet
    n_cells = c.u8()
    cell_alarms = [c.u8() for _ in range(n_cells)]
    n_temps = c.u8()
    temp_alarms = [c.u8() for _ in range(n_temps)]
    cur_alarm = c.u8()
    volt_alarm = c.u8()
    n_custom = c.u8()
    custom = [c.u8() for _ in range(min(n_custom, c.remaining()))]
    r.raw["status_custom"] = custom
    for i, a in enumerate(cell_alarms):
        if a == 1:
            r.alarms.append(f"Celula {i + 1}: tensiune mică")
        elif a == 2:
            r.alarms.append(f"Celula {i + 1}: tensiune mare")
    for i, a in enumerate(temp_alarms):
        if a in (1, 2):
            r.alarms.append(f"Senzor temperatură {i + 1}: {'prea mică' if a == 1 else 'prea mare'}")
    if cur_alarm in (1, 2):
        r.alarms.append("Alarmă curent")
    if volt_alarm == 1:
        r.alarms.append("Tensiune totală mică")
    elif volt_alarm == 2:
        r.alarms.append("Tensiune totală mare")
    for i, bits in ALARM_EVENTS.items():
        if i < len(custom):
            for b, (text, level) in enumerate(bits):
                if text and level and custom[i] & (1 << b):
                    (r.protections if level == "p" else r.alarms).append(text)
    # octetul de stare comutatoare: după "alarm event 1..6"
    if len(custom) > 6:
        sw = custom[6]
        r.discharge_mos = bool(sw & 0x01)
        r.charge_mos = bool(sw & 0x02)
        r.raw["switch_byte"] = sw
    if len(custom) > 8:
        r.balancing = bool(custom[7] or custom[8])


class SeplosDriver(SerialDriver):
    kind = "pack"
    default_baudrate = 19200
    timeout = 0.5
    # pe hardware (3 pachete V2, 19200): fără pauză, pachetul întrebat imediat după altul nu răspunde; 50 ms ajung
    bus_gap = 0.1

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.address = int(cfg.get("address", 0))

    def _query(self, cid2: int) -> bytes:
        def reader(ser):
            buf = b""
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                # cât e deja sosit (minim 1 octet): read(256) ar aștepta tot timeout-ul după un răspuns mai scurt
                chunk = ser.read(max(1, getattr(ser, "in_waiting", 0)))
                if chunk:
                    buf += chunk
                    if b"~" in buf and b"\r" in buf[buf.index(b"~"):]:
                        break
            return buf

        raw = self.transact(build_request(self.address, cid2), reader)
        if not raw:
            raise DriverError(f"Seplos {self.id}: fără răspuns (verificați baudrate și adresa)")
        return parse_response(raw[:raw.index(b"\r") + 1] if b"\r" in raw else raw)

    def read(self) -> PackReading:
        r = PackReading(device_id=self.id, ts=time.time())
        decode_telemetry(self._query(0x42), r)
        if self.cfg.get("capacity_ah"):
            r.capacity_ah = float(self.cfg["capacity_ah"])
        try:
            decode_status(self._query(0x44), r)
        except (DriverError, IndexError):
            pass
        return r

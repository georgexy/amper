"""BMS JK (Jikong): prin Bluetooth (modulul din BMS) sau pe cablu (modulul RS485/UART al JK, protocolul „NW”).

Formatul cadrelor e cel documentat public (proiectul esphome-jk-bms); cadrele din teste sunt capturi reale publicate
acolo. NETESTAT încă pe un JK legat la Amper.

Bluetooth — serviciul 0xFFE0, caracteristica 0xFFE1 (scriere și notificări):
  cerere de 20 de octeți: AA 55 90 EB <comandă> <lungime> <valoare, 4 octeți LE> 0×9 <suma octeților 0–18>
    0x97 = informații despre dispozitiv → cadru de tip 0x03 (model, versiunea hardware și software)
    0x96 = starea → cadre de tip 0x02 (celule, tensiune, curent, SOC…), trimise apoi cam o dată pe secundă
  răspuns: 55 AA EB 90 <tip> <contor> …, 300 de octeți (unele modele 320), suma octeților 0–298 în octetul 299.
  Protocolul după versiunea hardware: 10.x → JK02 (24 de celule), 11 și peste → JK02_32S (32 de celule; aceleași
  câmpuri, mutate cu 16/32 de octeți), sub 10 → JK04 (valori „float”, nesuportat încă).

Cablu — modulul RS485/UART al JK (portul GPS), 115200 baud:
  cerere „citește tot”: 4E 57 00 13 00 00 00 00 06 03 00 00 00 00 00 00 68 00 00 01 29
  răspuns: 4E 57 <lungime 2> … <date TLV de la octetul 11> … 68 00 00 <sumă 2>; TLV: 0x79 celule (lungime, apoi
  câte 3 octeți: nr, mV), 0x80–0x82 temperaturi, 0x83 tensiune (0,01 V), 0x84 curent (0,01 A, bitul 15 = încărcare),
  0x85 SOC, 0x87 cicluri, 0x8B avertismente, 0x8C stare (bit0 încărcare, bit1 descărcare, bit2 echilibrare),
  0xAA capacitate (Ah), 0xC0 versiunea protocolului.
"""
from __future__ import annotations

import logging
import re
import struct
import time

from ..models import PackReading
from .base import Driver, DriverError, SerialDriver

log = logging.getLogger(__name__)

JK_NAME = re.compile(r"^(JK[-_ ]|JK$|JK\d)", re.I)

# ---------------------------------------------------------------- erori (JK02, Bluetooth)
# (text, protecție?) — None = doar informativ (nu se raportează)
JK02_ERRORS = [
    ("rezistența firelor de echilibrare", False),          # bit 0
    ("temperatură MOSFET prea mare", True),
    ("numărul de celule diferă de setări", False),
    None,
    None,                                                   # bit 4: „baterie încărcată complet” (normal)
    ("supratensiune pachet", True),
    ("supracurent la încărcare", True),
    ("scurtcircuit la încărcare", True),
    ("temperatură prea mare la încărcare", True),           # bit 8
    ("temperatură prea mică la încărcare", True),
    ("eroare de comunicare a coprocesorului", True),
    ("subtensiune celulă", True),
    ("subtensiune pachet", True),                           # bit 12
    ("supracurent la descărcare", True),
    ("scurtcircuit la descărcare", True),
    ("temperatură prea mare la descărcare", True),
    ("MOSFET de încărcare defect", True),                   # bit 16
    ("MOSFET de descărcare defect", True),
    None,                                                   # GPS deconectat
    None,                                                   # schimbă parola
    ("pornirea descărcării a eșuat", True),                 # bit 20
    ("temperatura bateriei prea mare", True),
    ("senzor de temperatură defect", False),
    ("modulul de preîncărcare defect", False),
    ("revenirea după scurtcircuit a eșuat", True),          # bit 24
    ("supracurent la descărcare (nivel II)", True),
    ("supracurent la descărcare (nivel III)", True),
    ("temperatură prea mică la descărcare", False),
    ("blocat de la distanță (GPS)", False),                 # bit 28
]

# avertismentele pe cablu (TLV 0x8B)
UART_WARNINGS = [
    ("capacitate scăzută", False), ("temperatură MOSFET prea mare", True), ("supratensiune la încărcare", True),
    ("subtensiune la descărcare", True), ("temperatura bateriei prea mare", True), ("supracurent la încărcare", True),
    ("supracurent la descărcare", True), ("diferență prea mare între celule", False),
    ("temperatură prea mare în cutie", True), ("temperatura bateriei prea mică", True), ("supratensiune celulă", True),
    ("subtensiune celulă", True), ("protecție 309_A", True), ("protecție 309_B", True),
]


def split_bits(bits: int, table: list) -> tuple[list[str], list[str]]:
    alarms, prot = [], []
    for i, item in enumerate(table):
        if item and bits & (1 << i):
            (prot if item[1] else alarms).append(item[0])
    return alarms, prot


def _state(current) -> str | None:
    if current is None:
        return None
    return "charging" if current > 0.5 else "discharging" if current < -0.5 else "idle"


# ---------------------------------------------------------------- Bluetooth
FRAME_LEN = 300
PREAMBLE = b"\x55\xAA\xEB\x90"
CMD_CELL_INFO = 0x96
CMD_DEVICE_INFO = 0x97


def ble_command(cmd: int, value: int = 0, length: int = 0) -> bytes:
    f = bytearray(20)
    f[0:4] = b"\xAA\x55\x90\xEB"
    f[4], f[5] = cmd, length
    f[6:10] = struct.pack("<I", value)
    f[19] = sum(f[:19]) & 0xFF
    return bytes(f)


def ble_frames(buf: bytes) -> list[bytes]:
    """Cadrele complete și corecte (suma) din ce a sosit pe Bluetooth."""
    out, i = [], buf.find(PREAMBLE)
    while i >= 0 and i + FRAME_LEN <= len(buf):
        f = buf[i:i + FRAME_LEN]
        if sum(f[:FRAME_LEN - 1]) & 0xFF == f[FRAME_LEN - 1]:
            out.append(f)
            i = buf.find(PREAMBLE, i + FRAME_LEN)
        else:
            i = buf.find(PREAMBLE, i + 1)
    return out


def _has(ftype: int):
    return lambda buf: any(f[4] == ftype for f in ble_frames(bytes(buf)))


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def device_info(frame: bytes) -> dict:
    return {"model": _cstr(frame[6:22]), "hw": _cstr(frame[22:30]), "sw": _cstr(frame[30:38]),
            "name": _cstr(frame[46:62])}


def protocol_for(hw: str) -> str:
    m = re.match(r"\s*(\d+)", hw or "")
    major = int(m.group(1)) if m else 11
    return "JK02_32S" if major >= 11 else "JK02_24S" if major >= 10 else "JK04"


def decode_jk02(frame: bytes, protocol: str, dev_id: str, capacity_ah=None) -> PackReading:
    """Cadrul de stare (tip 0x02) al protocoalelor JK02_24S / JK02_32S."""
    u16 = lambda i: struct.unpack_from("<H", frame, i)[0]            # noqa: E731
    s16 = lambda i: struct.unpack_from("<h", frame, i)[0]            # noqa: E731
    u32 = lambda i: struct.unpack_from("<I", frame, i)[0]            # noqa: E731
    s32 = lambda i: struct.unpack_from("<i", frame, i)[0]            # noqa: E731
    big = protocol == "JK02_32S"
    o = 16 if big else 0                    # celule și rezistențe: 32 în loc de 24
    n = 32 if big else 24
    cells = [u16(6 + 2 * i) / 1000 for i in range(n)]
    o2 = o * 2                              # câmpurile de după rezistențe
    r = PackReading(device_id=dev_id, ts=time.time())
    r.cells = [c for c in cells if c > 0]
    r.voltage = u32(118 + o2) / 1000
    r.current = s32(126 + o2) / 1000         # JK: pozitiv = încărcare, ca la noi
    r.temps = [s16(130 + o2) / 10, s16(132 + o2) / 10]
    r.raw["mos_temp"] = s16((112 if big else 134) + o2) / 10
    errors = u32(134 + o2) if big else u16(136 + o2)
    r.alarms, r.protections = split_bits(errors, JK02_ERRORS)
    r.raw["errors"] = errors
    r.balancing = frame[140 + o2] != 0
    r.soc = frame[141 + o2]
    r.remaining_ah = u32(142 + o2) / 1000
    r.capacity_ah = capacity_ah or (u32(146 + o2) / 1000) or None
    r.cycles = u32(150 + o2)
    r.soh = frame[158 + o2]
    r.charge_mos = bool(frame[166 + o2])
    r.discharge_mos = bool(frame[167 + o2])
    r.state = _state(r.current)
    return r


class JkBleDriver(Driver):
    """JK prin Bluetooth; `ble:` = adresa MAC sau numele (ex. JK-B2A24S15P)."""
    kind = "pack"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from .ble import JK_CHAR, JK_SERVICE, BleLink
        self.link = BleLink(str(cfg["ble"]), JK_SERVICE, JK_CHAR, JK_CHAR)
        self.ble_name = cfg.get("ble_name")
        self.protocol = cfg.get("protocol")           # se poate fixa în config, altfel se află din versiunea hardware
        self.info: dict = {}

    @property
    def link_desc(self) -> str:
        name = getattr(self.link, "name", None) or self.ble_name
        mac = getattr(self.link, "address", None)
        if name and mac and mac.replace(":", "") not in name.upper():
            return f"Bluetooth {name} · {mac}"
        return f"Bluetooth {name or mac or self.link.target}"

    def _frame(self, cmd: int, ftype: int, timeout: float) -> bytes:
        buf = self.link.exchange(ble_command(cmd), FRAME_LEN, timeout=timeout, done=_has(ftype))
        f = next((f for f in ble_frames(buf) if f[4] == ftype), None)
        if f is None:
            raise DriverError(f"JK {self.id}: fără răspuns ({len(buf)} octeți primiți prin Bluetooth)")
        return f

    def read(self) -> PackReading:
        if not self.protocol:
            self.info = device_info(self._frame(CMD_DEVICE_INFO, 0x03, 5.0))
            self.protocol = protocol_for(self.info["hw"])
            log.info("%s: JK %s, hardware %s, software %s → %s", self.id, self.info["model"], self.info["hw"],
                     self.info["sw"], self.protocol)
        if self.protocol == "JK04":
            raise DriverError(f"JK {self.id}: model foarte vechi (protocol JK04, hardware {self.info.get('hw')}): "
                              "încă nesuportat")
        r = decode_jk02(self._frame(CMD_CELL_INFO, 0x02, 6.0), self.protocol, self.id, self.cfg.get("capacity_ah"))
        r.raw.update(link=self.link_desc, protocol=self.protocol, **{k: v for k, v in self.info.items() if v})
        r.raw["ble_name"] = getattr(self.link, "name", None) or self.ble_name
        r.raw["ble_mac"] = getattr(self.link, "address", None)
        return r

    def close(self) -> None:
        self.link.close()


# ---------------------------------------------------------------- cablu (modulul RS485/UART, protocolul „NW”)
UART_READ_ALL = bytes.fromhex("4E5700130000000006030000000000006800000129")
TLV_LEN = {0x80: 2, 0x81: 2, 0x82: 2, 0x83: 2, 0x84: 2, 0x85: 1, 0x86: 1, 0x87: 2, 0x89: 4, 0x8A: 2, 0x8B: 2,
           0x8C: 2, 0x8E: 2, 0x8F: 2, 0x90: 2, 0x91: 2, 0x92: 2, 0x93: 2, 0x94: 2, 0x95: 2, 0x96: 2, 0x97: 2,
           0x98: 2, 0x99: 2, 0x9A: 2, 0x9B: 2, 0x9C: 2, 0x9D: 1, 0x9E: 2, 0x9F: 2, 0xA0: 2, 0xA1: 2, 0xA2: 2,
           0xA3: 2, 0xA4: 2, 0xA5: 2, 0xA6: 2, 0xA7: 2, 0xA8: 2, 0xA9: 1, 0xAA: 4, 0xAB: 1, 0xAC: 1, 0xAD: 2,
           0xAE: 1, 0xAF: 1, 0xB0: 2, 0xB1: 1, 0xB2: 10, 0xB3: 1, 0xB4: 8, 0xB5: 4, 0xB6: 4, 0xB7: 15, 0xB8: 1,
           0xB9: 4, 0xBA: 24, 0xC0: 1}


def uart_frame_ok(raw: bytes) -> bytes:
    """Verifică un răspuns „NW” și întoarce datele TLV."""
    if len(raw) < 13 or raw[:2] != b"NW":
        raise DriverError("JK: răspuns invalid pe cablu")
    n = struct.unpack_from(">H", raw, 2)[0]
    if len(raw) < n + 2:
        raise DriverError(f"JK: răspuns incomplet ({len(raw)} din {n + 2} octeți)")
    if sum(raw[:n]) & 0xFFFF != struct.unpack_from(">H", raw, n)[0]:
        raise DriverError("JK: suma de control greșită")
    return raw[11:n - 3]


def parse_tlv(data: bytes) -> dict:
    out, i = {}, 0
    while i < len(data):
        tag = data[i]
        if tag == 0x79:
            ln = data[i + 1]
            out[tag] = data[i + 2:i + 2 + ln]
            i += 2 + ln
        elif tag in TLV_LEN:
            out[tag] = data[i + 1:i + 1 + TLV_LEN[tag]]
            i += 1 + TLV_LEN[tag]
        else:
            break                               # sfârșitul datelor (numărul înregistrării) sau câmp necunoscut
    return out


def decode_uart(data: bytes, dev_id: str, capacity_ah=None) -> PackReading:
    t = parse_tlv(data)
    u16 = lambda tag: struct.unpack(">H", t[tag])[0] if tag in t else None      # noqa: E731
    temp = lambda v: None if v is None else (-(v - 100) if v > 100 else v)       # noqa: E731
    r = PackReading(device_id=dev_id, ts=time.time())
    cells = t.get(0x79, b"")
    r.cells = [struct.unpack_from(">H", cells, k + 1)[0] / 1000 for k in range(0, len(cells) - 2, 3)]
    r.temps = [x for x in (temp(u16(0x81)), temp(u16(0x82))) if x is not None]
    r.raw["mos_temp"] = temp(u16(0x80))
    if u16(0x83) is not None:
        r.voltage = u16(0x83) / 100
    raw_i = u16(0x84)
    if raw_i is not None:                       # bitul 15 = încărcare, restul = valoarea (0,01 A)
        r.current = (raw_i & 0x7FFF) / 100 * (1 if raw_i & 0x8000 else -1)
    if 0x85 in t:
        r.soc = t[0x85][0]
    r.cycles = u16(0x87)
    cap = struct.unpack(">I", t[0xAA])[0] if 0xAA in t else None
    r.capacity_ah = capacity_ah or cap or None
    if r.capacity_ah and r.soc is not None:
        r.remaining_ah = round(r.capacity_ah * r.soc / 100, 1)
    st = u16(0x8C)
    if st is not None:
        r.charge_mos, r.discharge_mos, r.balancing = bool(st & 1), bool(st & 2), bool(st & 4)
    w = u16(0x8B)
    if w:
        r.alarms, r.protections = split_bits(w, UART_WARNINGS)
    r.raw["protocol_version"] = t[0xC0][0] if 0xC0 in t else None
    r.state = _state(r.current)
    return r


class JkUartDriver(SerialDriver):
    """JK pe cablu, prin modulul RS485/UART al JK (protocolul „NW”)."""
    kind = "pack"
    default_baudrate = 115200
    timeout = 1.0
    bus_gap = 0.1

    @property
    def link_desc(self) -> str:
        return f"cablu {self.port.rsplit('/', 1)[-1]}"

    def _reader(self, ser) -> bytes:
        head = ser.read(4)
        if len(head) < 4 or head[:2] != b"NW":
            raise DriverError(f"JK {self.id}: fără răspuns pe {self.port}")
        n = struct.unpack_from(">H", head, 2)[0]
        rest = ser.read(n + 2 - 4)
        return head + rest

    def read(self) -> PackReading:
        raw = self.transact(UART_READ_ALL, self._reader)
        r = decode_uart(uart_frame_ok(raw), self.id, self.cfg.get("capacity_ah"))
        r.raw["link"] = self.link_desc
        return r

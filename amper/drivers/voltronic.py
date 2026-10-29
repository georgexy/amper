"""Invertoarele Voltronic / Axpert (protocolul PI30): Easun ISolar și SMH, MPP Solar, PowMr (seriile vechi), Mecer...

Comenzi text pe portul RS232 / USB-serial, 2400 8N1: „QPIGS” + CRC (XMODEM, 2 octeți) + CR; răspunsul „(… CRC CR”.
  - QPI    → „(PI30” (versiunea protocolului; folosită la recunoaștere);
  - QPIGS  → valorile de acum (rețea, ieșire, baterie, panouri, temperatură);
  - QMOD   → modul de lucru (L rețea, B baterie, S așteptare, F defect…);
  - QPIWS  → biții de avertisment / defect.
DOAR CITIRE. NEVERIFICAT pe un invertor real. Portul USB al multora dintre ele apare ca „HID” (nu ca port serial):
acela nu e suportat încă — se folosește cablul RS232 (cu adaptor USB–RS232).
"""
from __future__ import annotations

import time

from ..models import InverterReading
from .base import DriverError, SerialDriver

MODES = {"P": "Pornire", "S": "Așteptare", "L": "Din rețea", "B": "Din baterie", "F": "Defect", "H": "Economie",
         "D": "Oprit", "Y": "Bypass", "C": "Încărcare", "E": "ECO"}
# QPIWS: poziția bitului (de la stânga) → text; „defect” = oprește invertorul
WARNINGS = {1: ("Defect invertor", True), 2: ("Tensiune magistrală mare", True), 3: ("Tensiune magistrală mică", True),
            4: ("Pornire lentă eșuată", True), 6: ("Scurtcircuit la ieșire", True), 7: ("Tensiune invertor mică", True),
            8: ("Tensiune invertor mare", True), 9: ("Temperatură mare", False), 10: ("Ventilator blocat", False),
            11: ("Tensiune baterie mare", False), 12: ("Baterie descărcată", False), 14: ("Baterie sub limita de oprire", True),
            16: ("Suprasarcină", False), 17: ("Eroare EEPROM", False), 18: ("Supracurent invertor", True),
            19: ("Pornire invertor eșuată", True), 20: ("Tensiune DC ieșire", True), 21: ("Baterie deschisă", False),
            22: ("Curent senzor", True), 23: ("Baterie scurtcircuitată", True), 24: ("Limitare de putere", False),
            25: ("Tensiune PV mare", False), 26: ("Supracurent MPPT", False), 27: ("Avertisment supracurent MPPT", False),
            28: ("Baterie prea descărcată", False)}


def crc_xmodem(data: bytes) -> bytes:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    hi, lo = crc >> 8, crc & 0xFF
    # protocolul evită octeții „(”, CR și LF în CRC: îi mărește cu 1
    hi, lo = [(x + 1) if x in (0x28, 0x0D, 0x0A) else x for x in (hi, lo)]
    return bytes([hi, lo])


def command(cmd: str) -> bytes:
    body = cmd.encode()
    return body + crc_xmodem(body) + b"\r"


def parse_answer(buf: bytes, cmd: str) -> str:
    """„(230.0 50.0 …<CRC>\\r” → „230.0 50.0 …”; NAK sau CRC greșit → eroare."""
    end = buf.find(b"\r")
    if not buf.startswith(b"(") or end < 3:
        raise DriverError(f"Voltronic: răspuns neașteptat la {cmd}" if buf else f"Voltronic: fără răspuns la {cmd}")
    body, crc = buf[:end - 2], buf[end - 2:end]
    if crc != crc_xmodem(body):
        raise DriverError(f"Voltronic: CRC greșit la {cmd}")
    text = body[1:].decode("ascii", "replace")
    if text.startswith("NAK"):
        raise DriverError(f"Voltronic: comanda {cmd} nu e acceptată (NAK)")
    return text


def decode_qpigs(text: str, r: InverterReading) -> None:
    f = text.split()

    def num(i):
        try:
            return float(f[i])
        except (IndexError, ValueError):
            return None
    r.ac_in_voltage, r.output_voltage, r.output_freq = num(0), num(2), num(3)
    r.load_power, r.load_percent = num(5), num(6)
    r.battery_voltage, r.battery_soc, r.inverter_temp = num(8), num(10), num(11)
    chg, dis = num(9) or 0, num(15) or 0
    if r.battery_voltage is not None:
        r.battery_power = round((chg - dis) * r.battery_voltage)                 # + = încărcare
    r.pv_current, r.pv_voltage = num(12), num(13)
    pv_w = num(19)
    if pv_w is None and r.pv_current is not None and r.pv_voltage is not None:
        pv_w = r.pv_current * r.pv_voltage
    r.pv_power = pv_w
    status = f[16] if len(f) > 16 else ""
    r.raw.update(qpigs=text, status_bits=status, bus_voltage=num(7), apparent_power=num(4), grid_freq=num(1))
    # rețeaua: puterea din rețea nu e raportată separat; pe rețea (bitul „AC charging” sau mod L) consumul vine din ea
    r.ac_in_power = None


def decode_qpiws(bits: str, r: InverterReading) -> None:
    r.warnings, faults = [], []
    for pos, (text, fault) in WARNINGS.items():
        if pos < len(bits) and bits[pos] == "1":
            (faults if fault else r.warnings).append(text)
    r.fault_code = 1 if faults else 0
    r.fault_text = "; ".join(faults) or None


class VoltronicDriver(SerialDriver):
    kind = "inverter"
    default_baudrate = 2400
    timeout = 0.5
    bus_gap = 0.2

    def ask(self, cmd: str, wait: float = 1.5) -> str:
        def reader(ser):
            buf = b""
            deadline = time.monotonic() + wait
            while b"\r" not in buf and time.monotonic() < deadline:
                chunk = ser.read(64)
                if chunk:
                    buf += chunk
            return buf
        return parse_answer(self.transact(command(cmd), reader), cmd)

    def identify(self) -> str:
        return self.ask("QPI", 1.0)

    def read(self) -> InverterReading:
        r = InverterReading(device_id=self.id, ts=time.time())
        decode_qpigs(self.ask("QPIGS"), r)
        try:
            mode = self.ask("QMOD")[:1]
            r.status = MODES.get(mode, mode)
            r.status_code = ord(mode) if mode else None
            if mode == "L" and r.load_power is not None:
                r.ac_in_power = r.load_power          # pe rețea consumul vine din ea (încărcarea poate fi din PV)
            elif mode in ("B", "S"):
                r.ac_in_power = 0.0
        except DriverError:
            pass
        try:
            decode_qpiws(self.ask("QPIWS"), r)
        except DriverError:
            pass
        return r

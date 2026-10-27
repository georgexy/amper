"""Driver Daly BMS — protocolul UART/RS485 (cadre binare de 13 octeți), pe cablu sau prin Bluetooth.

Cerere:  A5 <adresă> <cmd> 08 00x8 <checksum>
Răspuns: A5 01 <cmd> 08 <8 octeți date> <checksum>
checksum = suma tuturor octeților anteriori & 0xFF

Adresa gazdei: 0x40 pe UART (implicit), 0x80 pe unele RS485/Bluetooth.
Curent: (raw - 30000) / 10 A, peste 30000 = încărcare.

Legătura: DalyDriver = cablu USB-UART (`port:`), DalyBleDriver = modulul Bluetooth al BMS-ului (`ble:`),
aceleași cadre. Pentru detectarea automată vezi daly_auto.py.
"""
from __future__ import annotations

import logging
import time

from ..models import PackReading
from .base import Driver, DriverError, SerialDriver

log = logging.getLogger(__name__)

FRAME_LEN = 13

# Biții din comanda 0x98 (coduri de defect) — conform protocolului Daly UART/485
FAULT_BITS = [
    ["Tensiune celulă mare (nivel 1)", "Tensiune celulă mare (nivel 2)",
     "Tensiune celulă mică (nivel 1)", "Tensiune celulă mică (nivel 2)",
     "Tensiune totală mare (nivel 1)", "Tensiune totală mare (nivel 2)",
     "Tensiune totală mică (nivel 1)", "Tensiune totală mică (nivel 2)"],
    ["Temperatură mare la încărcare (nivel 1)", "Temperatură mare la încărcare (nivel 2)",
     "Temperatură mică la încărcare (nivel 1)", "Temperatură mică la încărcare (nivel 2)",
     "Temperatură mare la descărcare (nivel 1)", "Temperatură mare la descărcare (nivel 2)",
     "Temperatură mică la descărcare (nivel 1)", "Temperatură mică la descărcare (nivel 2)"],
    ["Supracurent la încărcare (nivel 1)", "Supracurent la încărcare (nivel 2)",
     "Supracurent la descărcare (nivel 1)", "Supracurent la descărcare (nivel 2)",
     "SOC mare (nivel 1)", "SOC mare (nivel 2)", "SOC mic (nivel 1)", "SOC mic (nivel 2)"],
    ["Diferență tensiune celule (nivel 1)", "Diferență tensiune celule (nivel 2)",
     "Diferență temperatură (nivel 1)", "Diferență temperatură (nivel 2)", None, None, None, None],
    ["Temperatură mare MOSFET încărcare", "Temperatură mare MOSFET descărcare",
     "Senzor temperatură MOSFET încărcare defect", "Senzor temperatură MOSFET descărcare defect",
     "MOSFET încărcare lipit", "MOSFET descărcare lipit",
     "MOSFET încărcare întrerupt", "MOSFET descărcare întrerupt"],
    ["Cip AFE defect", "Pierdere citire tensiune celulă", "Senzor temperatură celulă defect",
     "Eroare EEPROM", "Eroare RTC", "Precharge eșuat", "Eroare comunicație", "Eroare comunicație internă"],
    ["Modul curent defect", "Detecție tensiune totală defectă", "Protecție scurtcircuit",
     "Încărcare interzisă la tensiune mică", None, None, None, None],
]


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def build_request(address: int, cmd: int, payload: bytes = bytes(8)) -> bytes:
    body = bytes([0xA5, address, cmd, 0x08]) + payload[:8].ljust(8, b"\x00")
    return body + bytes([checksum(body)])


def parse_frames(buf: bytes, cmd: int) -> list[bytes]:
    """Extrage cadrele valide de răspuns pentru comanda `cmd`; întoarce lista de câte 8 octeți date."""
    out = []
    i = 0
    while i + FRAME_LEN <= len(buf):
        if buf[i] == 0xA5 and buf[i + 2] == cmd and buf[i + 3] == 0x08:
            frame = buf[i:i + FRAME_LEN]
            if checksum(frame[:12]) == frame[12]:
                out.append(frame[4:12])
                i += FRAME_LEN
                continue
        i += 1
    return out


def u16(b: bytes, i: int) -> int:
    return (b[i] << 8) | b[i + 1]


def decode_faults(data: bytes) -> list[str]:
    out = []
    for byte_i, names in enumerate(FAULT_BITS):
        if byte_i >= len(data):
            break
        for bit, name in enumerate(names):
            if name and data[byte_i] & (1 << bit):
                out.append(name)
    return out


def split_faults(names: list[str]) -> tuple[list[str], list[str]]:
    """(avertismente, protecții): „nivel 1” = avertisment; „nivel 2” și defectele de hardware = protecție (critic)."""
    warn = [n for n in names if "(nivel 1)" in n]
    return warn, [n for n in names if n not in warn]


class DalyProtocol:
    """Comenzile și decodarea Daly; legătura (cablu sau Bluetooth) o dă `_exchange(request, expected)`."""
    kind = "pack"
    link_desc = ""

    def _init_daly(self, cfg: dict, default_address: int) -> None:
        self.address = int(cfg.get("address", default_address))
        self.invert_current = bool(cfg.get("invert_current", False))
        self.cell_count = None
        self.temp_count = None

    def _exchange(self, request: bytes, expected: int) -> bytes:
        raise NotImplementedError

    def _query(self, cmd: int, frames: int = 1) -> list[bytes]:
        buf = self._exchange(build_request(self.address, cmd), FRAME_LEN * frames)
        data = parse_frames(buf, cmd)
        if not data:
            raise DriverError(f"Daly {self.id}: fără răspuns la comanda 0x{cmd:02X} "
                              f"({len(buf)} octeți primiți)")
        return data

    def read(self) -> PackReading:
        r = PackReading(device_id=self.id, ts=time.time(), capacity_ah=self.cfg.get("capacity_ah"))

        d = self._query(0x90)[0]
        r.voltage = u16(d, 0) / 10
        cur = (u16(d, 4) - 30000) / 10
        r.current = -cur if self.invert_current else cur
        r.soc = u16(d, 6) / 10

        d = self._query(0x93)[0]
        r.state = {0: "idle", 1: "charging", 2: "discharging"}.get(d[0], str(d[0]))
        r.charge_mos = bool(d[1])
        r.discharge_mos = bool(d[2])
        r.raw["bms_life"] = d[3]
        r.remaining_ah = int.from_bytes(d[4:8], "big") / 1000

        d = self._query(0x94)[0]
        self.cell_count = d[0] or self.cell_count
        self.temp_count = d[1]
        r.raw["charger_connected"] = bool(d[2])
        r.raw["load_connected"] = bool(d[3])
        r.cycles = u16(d, 5)

        # tensiuni pe celule: 3 celule / cadru
        if self.cell_count:
            n_frames = (self.cell_count + 2) // 3
            cells: dict[int, float] = {}
            for fr in self._query(0x95, n_frames):
                idx = fr[0]
                for k in range(3):
                    cell_no = (idx - 1) * 3 + k
                    if 1 <= idx and cell_no < self.cell_count:
                        cells[cell_no] = u16(fr, 1 + 2 * k) / 1000
            r.cells = [cells[i] for i in sorted(cells)]

        # temperaturi: 7 senzori / cadru
        if self.temp_count:
            n_frames = (self.temp_count + 6) // 7
            temps: dict[int, float] = {}
            for fr in self._query(0x96, n_frames):
                idx = fr[0]
                for k in range(7):
                    t_no = (idx - 1) * 7 + k
                    if 1 <= idx and t_no < self.temp_count:
                        temps[t_no] = fr[1 + k] - 40
            r.temps = [temps[i] for i in sorted(temps)]
        else:
            d = self._query(0x92)[0]
            r.temps = [d[0] - 40, d[2] - 40]

        try:
            d = self._query(0x97)[0]
            r.balancing = any(d[:6])
        except DriverError:
            pass
        try:
            d = self._query(0x98)[0]
            r.alarms, r.protections = split_faults(decode_faults(d[:7]))
        except DriverError:
            pass

        if r.capacity_ah is None and r.remaining_ah is not None and r.soc:
            r.capacity_ah = round(r.remaining_ah / (r.soc / 100), 1)
        if self.link_desc:
            r.raw["link"] = self.link_desc
        self._check_glitch(r)
        return r

    # Comenzile de CITIRE a parametrilor (protocolul Daly UART/485). Doar citire — nicio comandă de scriere aici.
    PARAM_COMMANDS = list(range(0x50, 0x64))

    def read_params(self) -> dict[str, str]:
        """Răspunsurile brute (hex, 8 octeți) la comenzile de citire a parametrilor; lipsă = nu răspunde."""
        out = {}
        for cmd in self.PARAM_COMMANDS:
            try:
                out[f"{cmd:02X}"] = self._query(cmd)[0].hex()
            except DriverError:
                out[f"{cmd:02X}"] = None
        return out

    # Văzut pe hardware (2026-10-26, prin Bluetooth): un Daly bun răspunde uneori cu starea „goală”
    # (SOC 0, rămas 0, 0 cicluri, ambele MOSFET-uri 0) deși celulele sunt normale, alternând cu citiri corecte.
    # O astfel de citire, imediat după una bună (SOC > 5% cu cel mult 10 minute înainte), e o eroare de transmisie:
    # o respingem, ca să nu dea alerte false. Un BMS care raportează mereu așa (neconfigurat) e lăsat cum e.
    def _check_glitch(self, r: PackReading) -> None:
        empty = (r.soc == 0 and not r.remaining_ah and not r.cycles and r.charge_mos is False
                 and r.discharge_mos is False and (r.voltage or 0) > 30)
        last = getattr(self, "_last_good", None)
        if empty and last and r.ts - last[1] < 600 and last[0] > 5:
            n = getattr(self, "_glitches", 0) + 1
            self._glitches = n
            if n in (1, 10) or n % 100 == 0:
                log.warning("%s: citire Daly goală după una bună (SOC %.1f%%), ignorată (#%d)", self.id, last[0], n)
            raise DriverError(f"Daly {self.id}: citire incompletă (SOC 0 / MOSFET-uri 0), ignorată")
        if not empty:
            self._last_good = (r.soc or 0, r.ts)


class DalyDriver(DalyProtocol, SerialDriver):
    """Daly pe cablul USB-UART."""
    default_baudrate = 9600
    timeout = 0.3

    def __init__(self, cfg: dict):
        SerialDriver.__init__(self, cfg)
        self._init_daly(cfg, 0x40)
        self.link_desc = f"cablu {self.port.rsplit('/', 1)[-1]}"

    def _exchange(self, request: bytes, expected: int) -> bytes:
        frames = max(1, expected // FRAME_LEN)

        def reader(ser):
            buf = b""
            deadline = time.monotonic() + 0.4 + 0.05 * frames
            while len(buf) < expected and time.monotonic() < deadline:
                chunk = ser.read(expected - len(buf))
                if chunk:
                    buf += chunk
            return buf

        return self.transact(request, reader)


class DalyBleDriver(DalyProtocol, Driver):
    """Daly prin modulul Bluetooth (BLE) al BMS-ului; `ble:` = adresa MAC sau numele (ex. DL-40D63C2xxxxx)."""

    def __init__(self, cfg: dict):
        Driver.__init__(self, cfg)
        self._init_daly(cfg, 0x80)
        from .ble import BleLink
        self.link = BleLink(str(cfg["ble"]))
        self.link_desc = f"Bluetooth {cfg['ble']}"

    def _exchange(self, request: bytes, expected: int) -> bytes:
        # prin Bluetooth, răspunsurile întârziate ale comenzii anterioare pot sosi primele:
        # așteptăm până avem cadrele comenzii cerute, nu doar numărul de octeți
        cmd, frames = request[2], max(1, expected // FRAME_LEN)
        return self.link.exchange(request, expected, timeout=2.0 + 0.1 * frames,
                                  done=lambda buf: len(parse_frames(bytes(buf), cmd)) >= frames)

    def close(self) -> None:
        self.link.close()

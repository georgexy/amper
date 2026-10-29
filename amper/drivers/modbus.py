"""Modbus RTU comun pentru invertoarele noi (Deye, SRNE / SMG): citirea registrelor holding (funcția 03)."""
from __future__ import annotations

import time

from .base import DriverError, SerialDriver
from .growatt_spf import build_read, crc16


def parse(resp: bytes, address: int, count: int, function: int, name: str) -> list[int]:
    if len(resp) >= 5 and resp[1] == (function | 0x80):
        raise DriverError(f"{name}: excepție Modbus cod {resp[2]}")
    need = 5 + 2 * count
    if len(resp) < need:
        raise DriverError(f"{name}: răspuns incomplet ({len(resp)}/{need} octeți)" if resp else f"{name}: fără răspuns")
    resp = resp[:need]
    if resp[0] != address or resp[1] != function or resp[2] != 2 * count:
        raise DriverError(f"{name}: antet răspuns neașteptat")
    if crc16(resp[:-2]) != (resp[-2] | (resp[-1] << 8)):
        raise DriverError(f"{name}: CRC greșit")
    data = resp[3:-2]
    return [(data[i] << 8) | data[i + 1] for i in range(0, len(data), 2)]


def s16(v: int | None) -> int | None:
    return None if v is None else (v - 0x10000 if v & 0x8000 else v)


class ModbusDriver(SerialDriver):
    """Driver Modbus RTU: `read_regs(start, count)` cu funcția 03 (holding), adresa din config (implicit 1)."""
    kind = "inverter"
    default_baudrate = 9600
    timeout = 0.5
    bus_gap = 0.1
    name = "Modbus"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.address = int(cfg.get("address", 1))

    def read_regs(self, start: int, count: int, function: int = 0x03) -> list[int]:
        need = 5 + 2 * count

        def reader(ser):
            buf = b""
            deadline = time.monotonic() + 1.0
            while len(buf) < need and time.monotonic() < deadline:
                chunk = ser.read(need - len(buf))
                if chunk:
                    buf += chunk
                    if len(buf) >= 5 and buf[1] & 0x80:
                        break
            return buf

        resp = self.transact(build_read(self.address, start, count, function), reader)
        return parse(resp, self.address, count, function, f"{self.name} {self.id}")

    def read_map(self, blocks) -> dict[int, int]:
        regs: dict[int, int] = {}
        for start, count in blocks:
            for i, v in enumerate(self.read_regs(start, count)):
                regs[start + i] = v
        return regs

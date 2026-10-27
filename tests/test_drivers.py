"""Teste pentru drivere — cu un port serial fals care imită dispozitivele reale."""
import struct

import pytest

from amper.drivers import daly, growatt_spf, seplos
from amper.drivers.base import DriverError


class FakeSerial:
    def __init__(self, responder):
        self.responder = responder
        self.out = b""

    def reset_input_buffer(self):
        self.out = b""

    def write(self, data):
        self.out += self.responder(bytes(data))

    def flush(self):
        pass

    def read(self, n=1):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def close(self):
        pass


# ---------------- Daly ----------------
def daly_frame(cmd, data):
    body = bytes([0xA5, 0x01, cmd, 0x08]) + bytes(data).ljust(8, b"\x00")
    return body + bytes([sum(body) & 0xFF])


CELLS = [3.301, 3.305, 3.299, 3.310, 3.302, 3.300, 3.298, 3.304,
         3.306, 3.301, 3.303, 3.297, 3.300, 3.302, 3.305, 3.299]


def daly_responder(req):
    assert req[0] == 0xA5 and req[3] == 0x08 and req[12] == sum(req[:12]) & 0xFF
    cmd = req[2]
    if cmd == 0x90:   # 52.8 V, +12.5 A (încărcare), 64.3 %
        return daly_frame(0x90, struct.pack(">HHHH", 528, 0, 30125, 643))
    if cmd == 0x93:   # charging, MOS încărcare OFF, descărcare ON, 64300 mAh
        return daly_frame(0x93, bytes([1, 0, 1, 7]) + struct.pack(">I", 64300))
    if cmd == 0x94:   # 16 celule, 2 senzori, ..., 321 cicluri
        return daly_frame(0x94, bytes([16, 2, 1, 0, 0]) + struct.pack(">H", 321))
    if cmd == 0x95:
        out = b""
        for f in range(6):
            vals = CELLS[f * 3:f * 3 + 3]
            data = bytes([f + 1]) + b"".join(struct.pack(">H", round(v * 1000)) for v in vals)
            out += daly_frame(0x95, data)
        return out
    if cmd == 0x96:
        return daly_frame(0x96, bytes([1, 40 + 21, 40 + 23]))
    if cmd == 0x97:
        return daly_frame(0x97, bytes(8))
    if cmd == 0x98:   # bit0 octet4 = temperatură mare MOSFET încărcare
        return daly_frame(0x98, bytes([0, 0, 0, 0, 0x01, 0, 0]))
    return b""


def test_daly_request_checksum():
    req = daly.build_request(0x40, 0x90)
    assert req.hex() == "a540900800000000000000007d"


def test_daly_full_read():
    drv = daly.DalyDriver({"id": "d1", "type": "daly", "port": "x", "capacity_ah": 100})
    drv._ser = FakeSerial(daly_responder)
    r = drv.read()
    assert r.voltage == 52.8
    assert r.current == 12.5
    assert r.soc == 64.3
    assert r.charge_mos is False and r.discharge_mos is True
    assert r.remaining_ah == 64.3
    assert r.cycles == 321
    assert r.cells == CELLS
    assert r.temps == [21, 23]
    assert r.alarms == []                                       # defect de hardware = protecție, nu avertisment
    assert r.protections == ["Temperatură mare MOSFET încărcare"]
    assert r.state == "charging"


def test_daly_no_response():
    drv = daly.DalyDriver({"id": "d1", "type": "daly", "port": "x"})
    drv._ser = FakeSerial(lambda req: b"")
    with pytest.raises(DriverError):
        drv.read()


def test_daly_ignores_garbage():
    frames = daly.parse_frames(b"\x00\xff" + daly_frame(0x90, b"\x01\x02") + b"\x11", 0x90)
    assert frames == [bytes([1, 2, 0, 0, 0, 0, 0, 0])]


# ---------------- Seplos ----------------
def test_seplos_request_matches_known_frame():
    # cadru publicat (esphome-seplos-bms): telemetrie pachet 0
    assert seplos.build_request(0, 0x42) == b"~20004642E00200FD37\r"


def seplos_response(info: bytes) -> bytes:
    info_hex = info.hex().upper()
    body = f"20004600{seplos.length_field(len(info_hex))}{info_hex}"
    return f"~{body}{seplos.frame_checksum(body)}\r".encode()


def seplos_telemetry_info():
    cells = [3287, 3305, 3316, 3286] * 4
    temps = [2731 + 215, 2731 + 220, 2731 + 218, 2731 + 219, 2731 + 200, 2731 + 250]
    b = bytes([0x00, 0x01, 16]) + b"".join(struct.pack(">H", c) for c in cells)
    b += bytes([6]) + b"".join(struct.pack(">H", t) for t in temps)
    b += struct.pack(">h", -1523)      # -15.23 A
    b += struct.pack(">H", 5283)       # 52.83 V
    b += struct.pack(">H", 18000)      # 180.00 Ah rămași
    b += bytes([10])
    b += struct.pack(">H", 28000)      # 280 Ah
    b += struct.pack(">H", 643)        # 64.3 %
    b += struct.pack(">H", 28000)
    b += struct.pack(">H", 87)         # cicluri
    b += struct.pack(">H", 1000)       # SOH 100 %
    b += struct.pack(">H", 5280)       # port voltage
    b += bytes(8)
    return b


def test_seplos_length_matches_published_sample():
    # răspunsul publicat începe cu ~2000460010 96 ... => INFO de 0x096 = 150 caractere = 75 octeți
    assert len(seplos_telemetry_info()) == 75
    assert seplos.length_field(150) == "1096"


def test_seplos_telemetry_decode():
    drv = seplos.SeplosDriver({"id": "s", "type": "seplos", "port": "x", "capacity_ah": 280})

    def responder(req):
        if req.startswith(b"~20004642"):
            return seplos_response(seplos_telemetry_info())
        return b""   # fără răspuns la 0x44 → ignorat

    drv._ser = FakeSerial(responder)
    r = drv.read()
    assert r.cells[0] == 3.287 and len(r.cells) == 16
    assert r.temps == [21.5, 22.0, 21.8, 21.9]
    assert r.raw["temp_mosfet"] == 25.0
    assert r.current == -15.23
    assert r.voltage == 52.83
    assert r.remaining_ah == 180.0
    assert r.soc == 64.3
    assert r.cycles == 87 and r.soh == 100.0
    assert r.state == "discharging"


def test_seplos_bad_checksum():
    raw = bytearray(seplos_response(seplos_telemetry_info()))
    raw[-3] = ord("0") if raw[-3] != ord("0") else ord("1")
    with pytest.raises(DriverError):
        seplos.parse_response(bytes(raw))


# ---------------- Growatt ----------------
def test_modbus_crc_known_frame():
    # exemplu clasic Modbus: 01 03 00 00 00 0A -> CRC C5 CD
    assert growatt_spf.crc16(bytes.fromhex("01030000000A")) == 0xCDC5


def growatt_responder(regs):
    def resp(req):
        assert growatt_spf.crc16(req[:-2]) == req[-2] | (req[-1] << 8)
        start = (req[2] << 8) | req[3]
        count = (req[4] << 8) | req[5]
        data = b"".join(struct.pack(">H", regs.get(start + i, 0)) for i in range(count))
        pdu = bytes([1, 4, len(data)]) + data
        c = growatt_spf.crc16(pdu)
        return pdu + bytes([c & 0xFF, c >> 8])
    return resp


def test_growatt_decode():
    regs = {0: 5, 1: 2853, 3: 0, 4: 25000, 9: 0, 10: 8500, 17: 5320, 18: 71,
            22: 2300, 23: 5000, 25: 345, 27: 170, 42: 0, 43: 0}
    bw = (-16000) & 0xFFFFFFFF          # -1600 W în registru = încărcare (semn protocol)
    regs[77], regs[78] = bw >> 16, bw & 0xFFFF
    drv = growatt_spf.GrowattSPFDriver({"id": "inv", "type": "growatt_spf", "port": "x"})
    drv._ser = FakeSerial(growatt_responder(regs))
    r = drv.read()
    assert r.status_code == 5 and r.status == "Încărcare din PV"
    assert r.pv_voltage == 285.3
    assert r.pv_power == 2500.0
    assert r.pv_current == 8.76          # 2500 W ÷ 285,3 V
    assert r.pv2_current == 0.0          # PV2 fără tensiune
    assert r.load_power == 850.0
    assert r.battery_voltage == 53.2
    assert r.battery_soc == 71
    assert r.battery_power == 1600.0     # convenția noastră: + = încărcare
    assert r.inverter_temp == 34.5


def test_growatt_exception_response():
    pdu = bytes([1, 0x84, 2])
    c = growatt_spf.crc16(pdu)
    with pytest.raises(DriverError):
        growatt_spf.parse_read(pdu + bytes([c & 0xFF, c >> 8]), 1, 10)

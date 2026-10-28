"""BMS PACE (PowerCube / BMS Daren, Pylon...): protocolul „paceic” 0x25 și 0x20.

Cadrele 0x25 sunt exemplele publicate în documentația proiectului esphome-pace-bms (nkinnan), luate de la un BMS
PACE real (Jakiper, P16S100A). Varianta Pylon (0x20) e construită după aceeași descriere (nu e o captură).
"""
import struct

import pytest

from amper.drivers import make_driver, pace, seplos
from amper.drivers.base import DriverError
from amper.models import PackReading
from test_drivers import FakeSerial, seplos_telemetry_info

ANALOG_REQ_25 = b"~25014642E00201FD30\r"
ANALOG_25 = (b"~25014600F07A0001100CC70CC80CC70CC70CC70CC50CC60CC70CC70CC60CC70CC60CC60CC70CC60CC7060B9B0B990B990B990B"
             b"B30BBCFF1FCCCD12D303286A008C2710E1E4\r")
STATUS_REQ_25 = b"~25014644E00201FD2E\r"
STATUS_25 = (b"~25014600004C000110000000000000000000000000000000000600000000000000000000000E000000000000EF3A\r")
HW_REQ_25 = b"~250146C10000FD9A\r"
HW_25 = b"~25014600602850313653313030412D313831322D312E30302000F58E\r"


def test_requests_match_published_frames():
    assert pace.build_request(0x25, 1, 0x42) == ANALOG_REQ_25
    assert pace.build_request(0x25, 1, 0x44) == STATUS_REQ_25
    assert pace.build_request(0x25, 1, 0xC1, "") == HW_REQ_25


def test_parse_checks_frame():
    ver, adr, info = pace.parse_response(ANALOG_25)
    assert (ver, adr, len(info)) == (0x25, 1, 0x7A // 2)
    bad = bytearray(ANALOG_25)
    bad[20] = ord("D")
    with pytest.raises(DriverError):
        pace.parse_response(bytes(bad))
    with pytest.raises(DriverError):                      # RTN ≠ 0
        pace.parse_response(b"~250146020000FDAD\r")


def responder_25(req):
    return {ANALOG_REQ_25: ANALOG_25, STATUS_REQ_25: STATUS_25, HW_REQ_25: HW_25}.get(req, b"")


def drv(**kw):
    d = make_driver({"id": "pc1", "type": "pace", "port": "x", "address": 1, **kw})
    d.answer_wait = 0.05
    return d


def test_read_v25():
    d = drv()
    d._ser = FakeSerial(responder_25)
    r = d.read()
    assert r.raw["protocol"] == "0x25" and d.ver == 0x25
    assert len(r.cells) == 16 and r.cells[0] == 3.271 and min(r.cells) == 3.269
    assert r.temps == [24.0, 23.8, 23.8, 23.8]
    assert r.raw["temp_mosfet"] == 26.4 and r.raw["temp_ambient"] == 27.3
    assert r.current == -2.25 and r.state == "discharging"
    assert r.voltage == 52.429                            # 0x25: mV
    assert r.remaining_ah == 48.19 and r.capacity_ah == 103.46
    assert r.soc == 46.6 and r.cycles == 140
    assert r.raw["rated_capacity_ah"] == 100.0 and r.soh == 100.0
    assert r.raw["hw"] == "P16S100A-1812-1.00"
    # starea: MOSFET-urile pornite (0x06), fără alarme
    assert r.charge_mos is True and r.discharge_mos is True
    assert r.alarms == [] and r.protections == [] and r.balancing is False


def test_status_bits_v25():
    r = PackReading(device_id="x", ts=0)
    info = bytes([0, 1, 2, 0, 2, 2, 0, 0, 0, 0, 0,     # celula 2 tensiune mare
                  0b01000001, 0b00000100, 0b00101010, 0, 0b00000001, 0x00, 0x05, 0b00000001, 0b10000000])
    pace.decode_status(info, r, 0x25)
    assert "Celula 2: tensiune prea mare" in r.alarms
    assert "Celulă: protecție la supratensiune" in r.protections and "Protecție la scurtcircuit" in r.protections
    assert "Încărcare: protecție la temperatură prea mică" in r.protections
    assert "Comutatorul de încărcare (MOSFET) defect" in r.protections
    assert "Celulă: tensiune mare" in r.alarms and "Capacitate rămasă mică" in r.alarms
    assert r.charge_mos is True and r.discharge_mos is False and r.state == "charging"
    assert r.balancing is True and r.raw["balance_cells"] == [1, 3]


def pylon_info(ud=2):
    cells = [3300] * 15 + [3310]
    b = bytes([0x00, 0x00, 16]) + b"".join(struct.pack(">H", c) for c in cells)
    b += bytes([5]) + b"".join(struct.pack(">H", 2731 + 200 + i) for i in range(5))
    b += struct.pack(">h", 1250)                          # +12,50 A
    b += struct.pack(">H", 5281)                          # 52,81 V (10 mV)
    if ud == 2:
        b += struct.pack(">H", 14000) + bytes([2]) + struct.pack(">H", 28000) + struct.pack(">H", 12)
    else:                                                 # 280 Ah nu încape pe 2 octeți în mAh: 3 octeți
        b += b"\xff\xff" + bytes([4]) + b"\xff\xff" + struct.pack(">H", 12)
        b += (210000).to_bytes(3, "big") + (280000).to_bytes(3, "big")
    return b


def frame(ver, adr, info: bytes) -> bytes:
    h = info.hex().upper()
    body = f"{ver:02X}{adr:02X}4600{pace.length_field(len(h))}{h}"
    return f"~{body}{pace.frame_checksum(body)}\r".encode()


def test_pylon_v20_autodetect():
    status = bytes([0, 0, 16]) + bytes(16) + bytes([5]) + bytes(5) + bytes([0, 0, 0]) + bytes([0b10, 0b110, 0x88, 0x01, 0])

    def resp(req):
        if req.startswith(b"~25"):
            return b""                                    # nu știe 0x25
        if req[7:9] == b"42":
            return frame(0x20, 0, pylon_info())
        if req[7:9] == b"44":
            return frame(0x20, 0, status)
        return b""

    d = make_driver({"id": "p", "type": "pace", "port": "x"})
    d.answer_wait = 0.05
    d._ser = FakeSerial(resp)
    r = d.read()
    assert d.ver == 0x20 and r.raw["protocol"] == "0x20"
    assert r.voltage == 52.81 and r.current == 12.5 and r.state == "charging"
    assert r.remaining_ah == 140.0 and r.capacity_ah == 280.0 and r.soc == 50.0 and r.cycles == 12
    assert r.temps == [20.0, 20.1, 20.2, 20.3, 20.4]
    assert "Celulă: protecție la subtensiune" in r.protections
    assert "Celula 1: defect (sub 1 V sau peste 4,2 V)" in r.protections
    assert r.charge_mos is True and r.discharge_mos is True and r.raw["full"] is True


def test_pylon_large_capacity():
    r = PackReading(device_id="x", ts=0)
    pace.decode_analog(pylon_info(ud=4), r, 0x20)
    assert r.remaining_ah == 210.0 and r.capacity_ah == 280.0 and r.soc == 75.0


def test_seplos_answer_on_v20_is_read_as_seplos():
    info = seplos_telemetry_info()
    assert pace.is_seplos(info) and not pace.is_seplos(pylon_info())
    r = PackReading(device_id="x", ts=0)
    pace.decode_analog(info, r, 0x20)
    assert r.raw["variant"] == "seplos" and r.soc == 64.3 and r.voltage == 52.83


def test_fixed_protocol_and_capacity_from_config():
    d = drv(protocol="25", capacity_ah=280)
    assert d.versions == (0x25,) and d.ver == 0x25
    d._ser = FakeSerial(responder_25)
    r = d.read()
    assert r.capacity_ah == 280.0 and r.soc == 46.6       # SOC-ul rămâne cel calculat de BMS


def test_no_answer():
    d = drv()
    d._ser = FakeSerial(lambda req: b"")
    with pytest.raises(DriverError):
        d.read()
    assert d.ver is None                                  # data viitoare încearcă iar ambele versiuni


def test_zero_cells_rejected():
    info = bytes([0, 1, 2, 0, 0, 0, 0, 0]) + bytes(20)
    with pytest.raises(DriverError):
        pace.decode_analog(info, PackReading(device_id="x", ts=0), 0x25)


def test_seplos_module_unchanged():
    assert seplos.build_request(0, 0x42) == b"~20004642E00200FD37\r"


def test_scan_finds_pace_v25(monkeypatch):
    """`amper scan` / ghidul: o baterie PACE 0x25 la adresa 1 e găsită ca `pace` (nu ca Seplos)."""
    import serial
    from amper import __main__ as cli
    from amper.drivers import base

    def responder(req):
        if req.startswith(b"~2501") and req[7:9] == b"42":
            return ANALOG_25
        return b""

    monkeypatch.setattr(serial, "Serial", lambda *a, **k: FakeSerial(responder))
    monkeypatch.setattr(pace, "PROBE_WAIT", 0.02)
    monkeypatch.setattr(base, "_BUSES", {})
    assert cli.probe("/dev/fake-pace") == ['pace {"baudrate": 9600, "address": 1, "protocol": 25}']

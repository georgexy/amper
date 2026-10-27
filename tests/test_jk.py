"""BMS JK: Bluetooth (JK02 cu 24 / 32 de celule, alegerea protocolului) și cablu (modulul RS485/UART, „NW”).

Cadrele Bluetooth sunt capturi reale de la BMS-uri JK, publicate în documentația proiectului esphome-jk-bms
(syssi/esphome-jk-bms, Apache-2.0): starea unui JK-B2A24S15P și cadrele „informații despre dispozitiv” ale unui
JK-B2A16S (hardware 3.0), JK-B2A24S15P (10.XW) și JK_PB2A16S15P (14.XA).
"""
import struct

import pytest

from amper.drivers import jk, make_driver
from amper.drivers.base import DriverError

CELL_24S = bytes.fromhex(
    "55aaeb90028cff0c010d010dff0c010d010dff0c010d010d010d010dff0c010d010d010d010d000000000000000000000000"
    "00000000ffff0000000d000000009d0196018c01870184018401830184018501810183018601820182018301850100000000"
    "00000000000000000000000000000000000003d000000000000000000000be00bf00d2000000000000548e0b0100683c0100"
    "000000003d04000064007904ca0310000101aa06000000000000000000000000070001000000d50200000000aed63b400000"
    "000058aafdff0000000100020000ece64f000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000cd")

INFO = {
    "JK04": bytes.fromhex(
        "55aaeb9003e74a4b2d42324131365300000000000000332e300000000000332e332e30000000108e320213000000424d5300"
        "0000000000000000000000003132333400000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000a9"),
    "JK02_24S": bytes.fromhex(
        "55aaeb90039f4a4b2d4232413234533135500000000031302e585700000031302e303700000040af0100060000004a4b2d42"
        "3241323453313550000000003132333400000000000000000000000032323034303700003230323136303230393600303030"
        "3000496e70757420557365726461746100003132333435360000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000065"),
    "JK02_32S": bytes.fromhex(
        "55aaeb9003c94a4b5f5042324131365331355000000031342e584100000031342e323000000054e601009c0000004a4b5f50"
        "4232413136533135500000003132333400000000000000000000000032333131313800003330393235373231333400303030"
        "3000496e707574205573657264617461000031323334353700000000000000000000496e7075742055736572646174610000"
        "feffffffafe9010200000000901f00000000c0d8e7fe1f00000100000000000000000104cf03000000000000000000000000"
        "0000df07000000000000000000000000000001cf0300000000000000000000000000000b0001000000000000000009000000"
        "0b0000000000000000000000805100000a5001000000000000000000000000000000000000fe9fe9fe03000000000000001b"),
}


def test_ble_command_checksum():
    c = jk.ble_command(0x96)
    assert len(c) == 20 and c[:6] == bytes([0xAA, 0x55, 0x90, 0xEB, 0x96, 0]) and c[19] == sum(c[:19]) & 0xFF


def test_device_info_selects_protocol():
    got = {k: (jk.device_info(f)["model"], jk.protocol_for(jk.device_info(f)["hw"])) for k, f in INFO.items()}
    assert got == {"JK04": ("JK-B2A16S", "JK04"), "JK02_24S": ("JK-B2A24S15P", "JK02_24S"),
                   "JK02_32S": ("JK_PB2A16S15P", "JK02_32S")}


def test_real_24s_cell_frame():
    # cadrul vine pe bucăți, cu gunoi înainte (ex. restul cadrului anterior)
    assert jk.ble_frames(b"\x01\x02" + CELL_24S[:150]) == []
    assert jk.ble_frames(b"\x01\x02" + CELL_24S + b"\x55\xAA") == [CELL_24S]
    r = jk.decode_jk02(CELL_24S, "JK02_24S", "jk1")
    assert len(r.cells) == 16 and r.cells[:2] == [3.327, 3.329]
    assert (r.voltage, r.current, r.soc, r.remaining_ah, r.capacity_ah) == (53.251, 0.0, 84, 68.494, 81.0)
    assert r.temps == [19.0, 19.1] and r.raw["mos_temp"] == 21.0
    assert r.charge_mos and r.discharge_mos and not r.balancing and r.soh == 100 and r.state == "idle"
    assert not r.alarms and not r.protections


def to_32s(f24: bytes) -> bytes:
    """Același conținut în formatul JK02_32S: celulele și rezistențele au 32 de locuri, câmpurile se mută."""
    f = bytearray(300)
    f[0:6] = f24[0:6]
    f[6:54] = f24[6:54]                     # 24 de celule (restul de 8: 0)
    f[70:74] = f24[54:58]                   # masca celulelor
    f[80:128] = f24[64:112]                 # rezistențele
    f[144:270] = f24[112:238]               # câmpurile de după rezistențe (+32)
    f[299] = sum(f[:299]) & 0xFF
    return bytes(f)


def test_32s_layout_and_errors():
    f = bytearray(to_32s(CELL_24S))
    struct.pack_into("<i", f, 126 + 32, -12345)               # descărcare 12,345 A
    struct.pack_into("<I", f, 134 + 32, (1 << 6) | (1 << 22) | (1 << 4))   # supracurent, senzor, plin (ignorat)
    f[299] = sum(f[:299]) & 0xFF
    r = jk.decode_jk02(bytes(f), "JK02_32S", "jk1", capacity_ah=100)
    assert len(r.cells) == 16 and r.voltage == 53.251 and r.current == -12.345 and r.state == "discharging"
    assert r.soc == 84 and r.capacity_ah == 100
    assert r.protections == ["supracurent la încărcare"] and r.alarms == ["senzor de temperatură defect"]


class FakeJkLink:
    """Modulul Bluetooth JK: 0x97 → cadrul de informații, 0x96 → cadrul de stare (pe bucăți, ca prin BLE)."""

    def __init__(self, info):
        self.info, self.sent, self.name, self.address, self.target = info, [], "JK-B2A24S15P", "C8:47:8C:00:00:01", "x"

    def exchange(self, request, expected, timeout=1.5, done=None):
        self.sent.append(request[4])
        out = {0x97: self.info, 0x96: CELL_24S}[request[4]]
        assert done(out)
        return out

    def close(self):
        pass


def test_ble_driver(monkeypatch):
    import amper.drivers.ble as ble
    monkeypatch.setattr(ble, "BleLink", lambda *a, **k: FakeJkLink(INFO["JK02_24S"]))
    drv = make_driver({"id": "jk1", "type": "jk", "ble": "C8:47:8C:00:00:01", "capacity_ah": 280})
    assert isinstance(drv, jk.JkBleDriver)
    r = drv.read()
    r2 = drv.read()
    assert drv.link.sent == [0x97, 0x96, 0x96]                # informațiile doar o dată
    assert r.soc == 84 and r2.capacity_ah == 280 and r.raw["protocol"] == "JK02_24S" and r.raw["model"] == "JK-B2A24S15P"
    assert r.raw["link"] == "Bluetooth JK-B2A24S15P · C8:47:8C:00:00:01"


def test_ble_driver_refuses_jk04(monkeypatch):
    import amper.drivers.ble as ble
    monkeypatch.setattr(ble, "BleLink", lambda *a, **k: FakeJkLink(INFO["JK04"]))
    with pytest.raises(DriverError, match="JK04"):
        make_driver({"id": "jk1", "type": "jk", "ble": "x"}).read()


# ---------------------------------------------------------------- cablu (protocolul „NW”)
def nw_response(tlv: bytes) -> bytes:
    body = b"NW" + b"\x00\x00" + bytes(4) + bytes([0x06, 0x00, 0x01]) + tlv + bytes(4) + b"\x68\x00\x00"
    n = len(body)
    raw = bytearray(body[:2] + struct.pack(">H", n) + body[4:])
    return bytes(raw) + struct.pack(">H", sum(raw) & 0xFFFF)


# exemplul din documentația protocolului: 14 celule, 53,59 V, 2,08 A la încărcare, 15 %, 4 cicluri, 560 Ah
CELLS_14 = [3.821, 3.834, 3.831, 3.820, 3.832, 3.834, 3.825, 3.832, 3.811, 3.834, 3.825, 3.835, 3.835, 3.826]
TLV = (bytes([0x79, 42]) + b"".join(bytes([i + 1]) + struct.pack(">H", round(v * 1000)) for i, v in enumerate(CELLS_14))
       + bytes.fromhex("80001D 81001E 820065 8314EF 8480D0 850F 8602 870004 8900000000 8A000E 8B0083 8C0007")
       + bytes.fromhex("AA00000230 C001"))


def test_uart_request_and_decode():
    assert jk.UART_READ_ALL[-2:] == struct.pack(">H", sum(jk.UART_READ_ALL[:19]))
    r = jk.decode_uart(jk.uart_frame_ok(nw_response(TLV)), "jk1")
    assert r.cells == CELLS_14 and r.voltage == 53.59 and r.current == 2.08 and r.soc == 15 and r.cycles == 4
    assert r.capacity_ah == 560 and r.remaining_ah == 84.0 and r.temps == [30, -1] and r.raw["mos_temp"] == 29
    assert r.charge_mos and r.discharge_mos and r.balancing and r.state == "charging"
    assert r.protections == ["temperatură MOSFET prea mare"]                      # 0x0083: biții 0, 1, 7
    assert r.alarms == ["capacitate scăzută", "diferență prea mare între celule"]
    with pytest.raises(DriverError, match="suma"):
        jk.uart_frame_ok(nw_response(TLV)[:-1] + b"\x00")


class FakeSerial:
    def __init__(self, resp):
        self.resp, self.buf, self.written = resp, b"", b""

    def reset_input_buffer(self):
        pass

    def write(self, b):
        self.written += b
        self.buf = self.resp

    def flush(self):
        pass

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass


def test_uart_driver():
    drv = make_driver({"id": "jk1", "type": "jk", "port": "/dev/ttyJK", "capacity_ah": 280})
    assert isinstance(drv, jk.JkUartDriver) and drv.baudrate == 115200
    drv._ser = FakeSerial(nw_response(TLV))
    r = drv.read()
    assert drv._ser.written == jk.UART_READ_ALL and r.capacity_ah == 280 and r.raw["link"] == "cablu ttyJK"

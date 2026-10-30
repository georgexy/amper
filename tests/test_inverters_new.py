"""Driverele noi de invertor (Deye, Voltronic/Axpert PI30, SMG/SRNE), cu un port serial simulat. Hărțile și cadrele
sunt cele din documentația publică; pe un invertor real încă NEVERIFICATE."""
import pytest

from amper.drivers import deye, srne, voltronic
from amper.drivers.base import DriverError
from amper.drivers.growatt_spf import crc16


class ModbusPort:
    """Port serial fals: răspunde la cererile Modbus 03 din `regs` (registrele lipsă → excepție 02)."""

    def __init__(self, regs, address=1):
        self.regs, self.address, self.out, self.baudrate, self.timeout = regs, address, b"", 9600, 0.5

    def reset_input_buffer(self):
        self.out = b""

    def flush(self):
        pass

    def write(self, req):
        addr, fn, start, count = req[0], req[1], (req[2] << 8) | req[3], (req[4] << 8) | req[5]
        if addr != self.address:
            self.out = b""
            return
        if any(start + i not in self.regs for i in range(count)):
            body = bytes([addr, fn | 0x80, 2])
        else:
            body = bytes([addr, fn, 2 * count]) + b"".join(
                (self.regs[start + i] & 0xFFFF).to_bytes(2, "big") for i in range(count))
        c = crc16(body)
        self.out = body + bytes([c & 0xFF, c >> 8])

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def close(self):
        pass


def with_port(drv, port):
    drv._ser = port
    return drv


# ---------------- Deye ----------------
def deye_regs():
    regs = {i: 0 for i in list(range(0, 8)) + [59] + list(range(70, 113)) + list(range(150, 194))}
    regs[0] = 3                                                  # hibrid monofazat
    for i, pair in enumerate(["21", "05", "12", "34", "56"]):    # seria 2105123456
        regs[3 + i] = (ord(pair[0]) << 8) | ord(pair[1])
    regs.update({59: 2, 108: 125, 84: 98, 76: 31, 109: 3605, 110: 82, 111: 0, 112: 0, 150: 2312, 154: 2301,
                 169: (-150) & 0xFFFF, 178: 410, 183: 5284, 184: 64, 186: 2950, 187: 0, 190: (-1200) & 0xFFFF,
                 193: 5001, 90: 1405, 91: 1382, 182: 1225})
    return regs


def test_deye_reading():
    d = with_port(deye.DeyeDriver({"id": "inv", "type": "deye", "port": "/dev/x"}), ModbusPort(deye_regs()))
    assert deye.looks_like_deye(d.identify())
    r = d.read()
    assert r.status == "Funcționare normală" and r.fault_code == 0
    assert (r.pv_voltage, r.pv_current, r.pv_power) == (360.5, 8.2, 2950)
    assert (r.ac_in_voltage, r.output_voltage, r.output_freq) == (231.2, 230.1, 50.01)
    assert r.ac_in_power == 0.0                                  # −150 W = trimite în rețea, nu ia din ea
    assert (r.load_power, r.battery_voltage, r.battery_soc) == (410, 52.84, 64)
    assert r.battery_power == 1200                               # Deye: −1200 W = se încarcă → Amper: +1200
    assert (r.inverter_temp, r.dcdc_temp) == (38.2, 40.5)
    assert r.raw["serial"] == "2105123456" and r.raw["today_pv_kwh"] == 12.5


def test_deye_not_confused_with_other_modbus():
    regs = {i: 0 for i in range(8)}
    regs[0] = 1                                                  # alt aparat Modbus (ex. Growatt: registrul 0 = pornit)
    assert not deye.looks_like_deye(regs)


# ---------------- Voltronic / Axpert PI30 ----------------
def test_voltronic_crc_matches_published_commands():
    assert voltronic.command("QPIGS") == b"QPIGS\xb7\xa9\r"
    assert voltronic.command("QMOD") == b"QMOD\x49\xc1\r"
    assert voltronic.command("QPIWS") == b"QPIWS\xb4\xda\r"
    assert voltronic.command("QPI") == b"QPI\xbe\xac\r"


class PI30Port(ModbusPort):
    ANSWERS = {"QPI": "PI30", "QMOD": "B",
               "QPIGS": "230.0 50.0 230.0 50.0 0460 0410 009 380 52.80 012 064 0035 0008 125.0 52.90 00000 00010110 "
                        "00 00 01000 010",
               "QPIWS": "00000000000000000000000000000000"}

    def __init__(self, faults=()):
        super().__init__({})
        self.faults = faults

    def write(self, req):
        cmd = req[:-3].decode()
        text = self.ANSWERS[cmd]
        if cmd == "QPIWS":
            text = "".join("1" if i in self.faults else "0" for i in range(32))
        body = b"(" + text.encode()
        self.out = body + voltronic.crc_xmodem(body) + b"\r"


def test_voltronic_reading():
    d = with_port(voltronic.VoltronicDriver({"id": "inv", "type": "voltronic", "port": "/dev/x"}), PI30Port())
    assert d.identify() == "PI30"
    r = d.read()
    assert (r.ac_in_voltage, r.output_voltage, r.output_freq, r.load_power, r.load_percent) == (230.0, 230.0, 50.0, 410, 9)
    assert (r.battery_voltage, r.battery_soc, r.inverter_temp) == (52.8, 64, 35)
    assert r.battery_power == round(12 * 52.8)                   # 12 A încărcare, 0 descărcare
    assert (r.pv_current, r.pv_voltage, r.pv_power) == (8, 125.0, 1000)
    assert r.status == "Din baterie" and r.ac_in_power == 0.0 and r.fault_code == 0


def test_voltronic_faults_and_bad_crc():
    r = with_port(voltronic.VoltronicDriver({"id": "inv", "type": "voltronic", "port": "/dev/x"}), PI30Port(faults={6, 12})).read()
    assert r.fault_code == 1 and "Scurtcircuit la ieșire" in r.fault_text and "Baterie descărcată" in r.warnings
    with pytest.raises(DriverError, match="CRC"):
        voltronic.parse_answer(b"(PI30\x00\x00\r", "QPI")
    with pytest.raises(DriverError, match="NAK"):
        body = b"(NAK"
        voltronic.parse_answer(body + voltronic.crc_xmodem(body) + b"\r", "QPIGS")


# ---------------- SMG / SRNE ----------------
def smg_regs():
    regs = {i: 0 for i in range(201, 231)}
    regs.update({201: 3, 202: 0, 204: 0, 209: 2299, 211: 5000, 213: 520, 215: 531, 217: (-640) & 0xFFFF,
                 219: 1802, 220: 61, 223: 1100, 225: 11, 226: 41, 227: 39, 229: 72})
    return regs


def srne_regs():
    regs = {i: 0 for i in list(range(0x0100, 0x010A)) + list(range(0x0210, 0x0222))}
    regs.update({0x0100: 81, 0x0101: 534, 0x0102: 152, 0x0107: 1655, 0x0108: 72, 0x0109: 1190, 0x0210: 5,
                 0x0213: 0, 0x0216: 2302, 0x0218: 5001, 0x021B: 330, 0x021F: 7, 0x0220: 412, 0x0221: 385})
    return regs


def test_smg_map_detected_and_decoded():
    d = with_port(srne.SrneDriver({"id": "inv", "type": "srne", "port": "/dev/x"}), ModbusPort(smg_regs()))
    r = d.read()
    assert d.map == "smg" and r.status == "Din baterie (off-grid)"
    assert (r.output_voltage, r.output_freq, r.load_power, r.load_percent) == (229.9, 50.0, 520, 11)
    assert (r.battery_voltage, r.battery_power, r.battery_soc) == (53.1, -640, 72)
    assert (r.pv_voltage, r.pv_current, r.pv_power, r.inverter_temp) == (180.2, 6.1, 1100, 39)


def test_srne_map_detected_and_decoded():
    d = with_port(srne.SrneDriver({"id": "inv", "type": "srne", "port": "/dev/x"}), ModbusPort(srne_regs()))
    r = d.read()
    assert d.map == "srne" and r.status == "Din baterie"
    assert (r.battery_soc, r.battery_voltage, r.battery_power) == (81, 53.4, round(15.2 * 53.4))
    assert (r.pv_voltage, r.pv_current, r.pv_power) == (165.5, 7.2, 1190)
    assert (r.output_voltage, r.load_power, r.inverter_temp) == (230.2, 330, 38.5)


def test_srne_nothing_plausible():
    d = with_port(srne.SrneDriver({"id": "inv", "type": "srne", "port": "/dev/x"}), ModbusPort({}))
    with pytest.raises(DriverError, match="nicio hartă"):
        d.read()


def test_new_types_in_config_and_setup():
    from amper import setup_scan
    from amper.config import KNOWN_TYPES
    assert {"deye", "voltronic", "srne"} <= KNOWN_TYPES
    devs = setup_scan.build_devices([{"type": "voltronic", "port": "/dev/ttyUSB0"},
                                     {"type": "daly", "ble": "AA:BB", "capacity_ah": 105}])
    assert devs[0] == {"id": "inverter", "name": "Invertor Easun / Voltronic", "type": "voltronic",
                       "port": "/dev/ttyUSB0", "baudrate": 2400, "address": 1}
    smg = setup_scan.build_devices([{"type": "srne", "port": "/dev/ttyUSB1", "map": "smg"}])[0]
    assert smg["map"] == "smg" and smg["baudrate"] == 9600


def test_parallel_inverters_energy_is_summed():
    from amper.web import sum_energy
    a = [{"day": "2026-10-29", "device": "inverter1", "pv_kwh": 6.5, "load_kwh": 4.0, "ac_in_kwh": 0.0},
         {"day": "2026-10-28", "device": "inverter1", "pv_kwh": 5.0, "load_kwh": 3.5, "ac_in_kwh": 1.0}]
    b = [{"day": "2026-10-29", "device": "inverter2", "pv_kwh": 6.1, "load_kwh": 3.0, "ac_in_kwh": 0.5}]
    rows = sum_energy([a, b])
    assert [r["day"] for r in rows] == ["2026-10-29", "2026-10-28"]
    assert rows[0]["pv_kwh"] == 12.6 and rows[0]["load_kwh"] == 7.0 and rows[0]["ac_in_kwh"] == 0.5
    assert rows[1]["pv_kwh"] == 5.0
    assert sum_energy([a]) is a


def test_two_inverters_get_numbered_ids():
    from amper import setup_scan
    devs = setup_scan.build_devices([{"type": "growatt_spf", "port": "/dev/ttyUSB0"},
                                     {"type": "growatt_spf", "port": "/dev/ttyUSB1"}])
    assert [(d["id"], d["name"]) for d in devs] == [("inverter1", "Invertor Growatt SPF 1"),
                                                    ("inverter2", "Invertor Growatt SPF 2")]

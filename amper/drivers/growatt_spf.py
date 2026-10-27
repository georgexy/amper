"""Driver Growatt SPF (5000 ES și familia off-grid SPF) — Modbus RTU, registre de intrare (funcția 04).

Comunicație: 9600 8N1, adresa 1, pe portul USB/RS232 al invertorului.
Harta registrelor: "OffGrid Modbus RS485&RS232 RTU Protocol" V0.17 (2022-06-22).
Protocolul cere: minim 850 ms între comenzi; cel mult 45 de registre pe citire, în blocuri aliniate la 45
(0–44, 45–89, 90–134...). Setările (registre holding, funcția 03 citire / 06 scriere) sunt în SETTINGS.
Registrele marcate VERIFY trebuie confirmate pe invertorul real (unele firmware-uri diferă).
"""
from __future__ import annotations

import time

from ..models import InverterReading
from .base import DriverError, SerialDriver

STATUS = {
    0: "Standby", 1: "—", 2: "Descărcare (din baterie)", 3: "Defect", 4: "Actualizare firmware",
    5: "Încărcare din PV", 6: "Încărcare din AC", 7: "Încărcare PV + AC",
    8: "Încărcare PV + AC și bypass", 9: "Încărcare PV și bypass", 10: "Încărcare AC și bypass",
    11: "Bypass", 12: "Încărcare PV și descărcare",
}


# protocolul V0.17, tabelul &*1: codul de defect (registrul de intrare 40); numărul e cel afișat de invertor
FAULTS = {
    1: "Ventilator blocat", 2: "Supratemperatură", 3: "Tensiune baterie prea mare", 4: "Baterie descărcată (subtensiune)",
    5: "Scurtcircuit la ieșire", 6: "Tensiune de ieșire prea mare", 7: "Suprasarcină",
    8: "Tensiune magistrală DC prea mare", 9: "Pornirea magistralei DC a eșuat", 11: "Releu principal defect",
    51: "Supracurent", 52: "Tensiune magistrală DC prea mică", 53: "Pornirea invertorului a eșuat",
    56: "Supracurent IGBT", 58: "Tensiune de ieșire prea mică", 60: "Putere negativă prea mare",
    61: "Tensiune PV prea mare", 62: "Eroare de comunicație internă", 80: "Eroare comunicație CAN",
    81: "Unitatea principală pierdută (paralel)",
}
# tabelul &*8: biții de avertisment; registrul 41 = biții 0–15, registrul 42 = biții 16–31.
# (text, se trimite pe telefon?) — între paranteze, codul afișat de invertor
WARNINGS = {
    0x0001: ("Ventilator blocat (01)", True), 0x0002: ("Baterie supraîncărcată (03)", True),
    0x0004: ("Tensiune baterie scăzută (04)", True), 0x0008: ("Suprasarcină (07)", True),
    0x0010: ("Putere de ieșire redusă (10)", True), 0x0020: ("Încărcarea solară oprită: baterie prea descărcată (12)", True),
    0x0040: ("Încărcarea solară oprită: tensiune PV prea mare (13)", True),
    0x0080: ("Încărcarea solară oprită: suprasarcină (14)", True),
    0x0100: ("Rețea diferită între invertoarele în paralel (15)", True), 0x0200: ("Eroare de fază la intrare (16)", True),
    0x0400: ("Lipsă fază la ieșire (17)", True), 0x0800: ("Supratemperatură (02)", True),
    0x1000: ("Curent Buck prea mare (18)", True), 0x2000: ("Baterie deconectată (19)", True),
    0x4000: ("Eroare comunicație cu BMS-ul (20)", True), 0x8000: ("Putere PV insuficientă (21)", False),
    0x10000: ("Fără baterie, paralel dezactivat (22)", True), 0x20000: ("Versiuni diferite în paralel (23)", True),
    0x80000: ("Capacități diferite în paralel (25)", True), 0x100000: ("Unitatea principală pierdută (81)", True),
    0x200000: ("BMS: supratensiune celulă (34)", True), 0x400000: ("BMS: supratensiune totală (36)", True),
    0x800000: ("BMS: supracurent la descărcare (38)", True), 0x1000000: ("BMS: supracurent la încărcare (39)", True),
    0x2000000: ("BMS: supratemperatură (43)", True), 0x4000000: ("Tensiune baterie inconsistentă (63)", True),
}


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_read(address: int, start: int, count: int, function: int = 0x04) -> bytes:
    pdu = bytes([address, function, start >> 8, start & 0xFF, count >> 8, count & 0xFF])
    c = crc16(pdu)
    return pdu + bytes([c & 0xFF, c >> 8])


def build_write(address: int, reg: int, value: int) -> bytes:
    pdu = bytes([address, 0x06, reg >> 8, reg & 0xFF, value >> 8, value & 0xFF])
    c = crc16(pdu)
    return pdu + bytes([c & 0xFF, c >> 8])


def parse_read(resp: bytes, address: int, count: int, function: int = 0x04) -> list[int]:
    if len(resp) >= 5 and resp[1] == (function | 0x80):
        raise DriverError(f"Growatt: excepție Modbus cod {resp[2]}")
    need = 5 + 2 * count
    if len(resp) < need:
        raise DriverError(f"Growatt: răspuns incomplet ({len(resp)}/{need} octeți)")
    resp = resp[:need]
    if resp[0] != address or resp[1] != function or resp[2] != 2 * count:
        raise DriverError("Growatt: antet răspuns neașteptat")
    if crc16(resp[:-2]) != (resp[-2] | (resp[-1] << 8)):
        raise DriverError("Growatt: CRC greșit")
    data = resp[3:-2]
    return [(data[i] << 8) | data[i + 1] for i in range(0, len(data), 2)]


def decode(regs: dict[int, int], r: InverterReading, battery_sign: int = -1) -> None:
    def g(i):
        return regs.get(i)

    def u32(i):
        if g(i) is None or g(i + 1) is None:
            return None
        return (g(i) << 16) | g(i + 1)

    def s32(i):
        v = u32(i)
        return None if v is None else (v - (1 << 32) if v & 0x80000000 else v)

    def sc(v, k):
        return None if v is None else round(v * k, 3)

    r.status_code = g(0)
    r.status = STATUS.get(g(0), f"cod {g(0)}")
    r.pv_voltage = sc(g(1), 0.1)
    r.pv2_voltage = sc(g(2), 0.1)
    p1, p2 = u32(3), u32(5)
    r.pv_power = sc((p1 or 0) + (p2 or 0), 0.1) if p1 is not None else None
    # curentul PV, citit din invertor: registrele 7/8 (Buck1Curr/Buck2Curr, 0,1 A) — curentul fiecărui MPPT
    r.pv_current = sc(g(7), 0.1)
    r.pv2_current = sc(g(8), 0.1)
    r.load_power = sc(u32(9), 0.1)
    r.battery_voltage = sc(g(17), 0.01)
    r.battery_soc = g(18)
    r.ac_in_voltage = sc(g(20), 0.1)
    r.output_voltage = sc(g(22), 0.1)
    r.output_freq = sc(g(23), 0.01)
    r.inverter_temp = sc(g(25), 0.1)
    r.dcdc_temp = sc(g(26), 0.1)
    r.load_percent = sc(g(27), 0.1)
    r.ac_in_power = sc(u32(36), 0.1)
    # protocolul V0.17: 40 = codul defectului (tabelul &*1), 41/42 = biții de avertisment (&*8), 43 = valoare
    r.fault_code = g(40)
    if r.fault_code:
        r.fault_text = FAULTS.get(r.fault_code, f"cod {r.fault_code}")
    if g(41) is not None:
        bits = g(41) | ((g(42) or 0) << 16)
        r.warning_code = bits
        for bit, (text, notify) in WARNINGS.items():
            if bits & bit:
                (r.warnings if notify else r.notices).append(text)
        r.raw["warning_value"] = g(43)
    # VERIFY: Bat_Watt (77-78, int32 cu semn). În protocol: pozitiv = descărcare.
    bw = s32(77)
    if bw is not None:
        r.battery_power = round(bw * 0.1 * battery_sign, 1)
    r.raw.update(ac_charge_power=sc(u32(13), 0.1), bus_voltage=sc(g(19), 0.1),
                 pv1_power=sc(p1, 0.1), pv2_power=sc(p2, 0.1),
                 epv_today_kwh=sc(((u32(48) or 0) + (u32(52) or 0)), 0.1))


# ---------------- setări (registre holding) ----------------
# kind: "enum" (options: valoare → text), "num" (min/max în unități brute, scale → unitatea afișată) sau
# "threshold": prag de tensiune, în V — sau în % când tipul bateriei e Litiu (3). Limitele sunt din protocolul V0.17.
# risky: setare care poate opri alimentarea sau afecta bateria/aparatele (aplicația cere confirmare în plus).
SETTINGS = [
    dict(key="output_priority", reg=1, group="Funcționare", label="Prioritate ieșire", kind="enum",
         options={0: "Baterie întâi (SBU)", 1: "PV întâi", 2: "Rețea întâi (UTI)", 3: "PV și rețea"}),
    dict(key="charge_source", reg=2, group="Funcționare", label="Sursa de încărcare", kind="enum",
         options={0: "PV întâi", 1: "PV și rețea", 2: "Doar PV"}),
    dict(key="ac_input_mode", reg=8, group="Funcționare", label="Mod intrare AC", kind="enum",
         options={0: "APL (90–280 V)", 1: "UPS (170–280 V)", 2: "Generator (GEN)"}),
    dict(key="uti_out_start", reg=3, group="Funcționare", label="Ieșire din rețea: de la ora", kind="num",
         min=0, max=23, scale=1, unit="h"),
    dict(key="uti_out_end", reg=4, group="Funcționare", label="Ieșire din rețea: până la ora", kind="num",
         min=0, max=23, scale=1, unit="h"),
    dict(key="uti_chg_start", reg=5, group="Funcționare", label="Încărcare din rețea: de la ora", kind="num",
         min=0, max=23, scale=1, unit="h"),
    dict(key="uti_chg_end", reg=6, group="Funcționare", label="Încărcare din rețea: până la ora", kind="num",
         min=0, max=23, scale=1, unit="h"),
    dict(key="battery_type", reg=39, group="Baterie", label="Tip baterie", kind="enum", risky=True,
         options={0: "AGM", 1: "FLD (acid lichid)", 2: "USE (definit de utilizator)", 3: "Litiu", 4: "USE2"},
         note="Se poate schimba doar cu invertorul în standby. Schimbă și unitatea pragurilor (V sau %)."),
    dict(key="max_charge_current", reg=34, group="Baterie", label="Curent maxim de încărcare", kind="num",
         min=0, max=400, scale=1, unit="A"),
    dict(key="ac_charge_current", reg=38, group="Baterie", label="Curent de încărcare din rețea", kind="num",
         min=0, max=100, scale=1, unit="A"),
    dict(key="bulk_voltage", reg=35, group="Baterie", label="Tensiune bulk (absorbție)", kind="num",
         min=500, max=640, scale=0.1, unit="V", risky=True),
    dict(key="float_voltage", reg=36, group="Baterie", label="Tensiune float", kind="num",
         min=500, max=560, scale=0.1, unit="V", risky=True),
    dict(key="bat_low_to_uti", reg=37, group="Baterie", label="Trece pe rețea sub", kind="threshold", pct_scale=1),
    dict(key="ac_to_bat", reg=95, group="Baterie", label="Revine pe baterie peste", kind="threshold", pct_scale=1),
    dict(key="bat_low_cutoff", reg=82, group="Baterie", label="Oprire la baterie descărcată", kind="threshold",
         pct_scale=0.1, risky=True, note="Sub acest prag invertorul oprește ieșirea."),
    dict(key="output_voltage", reg=18, group="Ieșire", label="Tensiune ieșire", kind="enum", risky=True,
         options={0: "208 V", 1: "230 V", 2: "240 V", 3: "220 V", 4: "100 V", 5: "110 V", 6: "120 V"},
         note="O tensiune greșită poate strica aparatele din casă."),
    dict(key="output_freq", reg=19, group="Ieșire", label="Frecvență ieșire", kind="enum", risky=True,
         options={0: "50 Hz", 1: "60 Hz"}, note="În România rețeaua e de 50 Hz."),
    dict(key="buzzer", reg=22, group="Ieșire", label="Buzzer (avertizare sonoră)", kind="enum",
         options={0: "Oprit", 1: "Pornit"}),
]
SETTINGS_BY_KEY = {d["key"]: d for d in SETTINGS}
REG_BATTERY_TYPE = 39
REG_CLOCK = 45                     # 45..50: an, lună, zi, oră, minut, secundă
LITHIUM = 3


def setting_spec(d: dict, battery_type: int | None) -> dict:
    """Limitele efective ale unei setări (pragurile depind de tipul bateriei)."""
    if d["kind"] != "threshold":
        return d
    if battery_type == LITHIUM:
        lo, hi = (5, 100) if d["pct_scale"] == 1 else (50, 1000)
        return {**d, "kind": "num", "min": lo, "max": hi, "scale": d["pct_scale"], "unit": "%"}
    return {**d, "kind": "num", "min": 200, "max": 640, "scale": 0.1, "unit": "V"}


def check_value(d: dict, raw: int, battery_type: int | None) -> None:
    sp = setting_spec(d, battery_type)
    if sp["kind"] == "enum":
        if raw not in sp["options"]:
            raise ValueError(f"{sp['label']}: valoare nepermisă ({raw})")
    elif not (sp["min"] <= raw <= sp["max"]):
        raise ValueError(f"{sp['label']}: trebuie între {sp['min'] * sp['scale']:g} și "
                         f"{sp['max'] * sp['scale']:g} {sp['unit']}")


class GrowattSPFDriver(SerialDriver):
    kind = "inverter"
    default_baudrate = 9600
    timeout = 0.6
    bus_gap = 0.85                 # protocol: minim 850 ms între comenzi
    BLOCKS = [(0, 45), (45, 45)]   # registrele de intrare 0–89, în blocuri aliniate la 45

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.address = int(cfg.get("address", 1))
        # -1: registrul Bat_Watt e pozitiv la descărcare (protocol); +1 dacă firmware-ul e invers
        self.battery_sign = int(cfg.get("battery_power_sign", -1))

    def _read_block(self, start: int, count: int, function: int = 0x04) -> list[int]:
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
        if not resp:
            raise DriverError(f"Growatt {self.id}: fără răspuns")
        return parse_read(resp, self.address, count, function)

    def read_holding(self, regs) -> dict[int, int]:
        """Citește registrele holding cerute, în blocurile de 45 cerute de protocol."""
        out: dict[int, int] = {}
        for b in sorted({r // 45 for r in regs}):
            for i, v in enumerate(self._read_block(b * 45, 45, 0x03)):
                out[b * 45 + i] = v
        return out

    def read_settings(self) -> dict[int, int]:
        return self.read_holding([d["reg"] for d in SETTINGS] + list(range(REG_CLOCK, REG_CLOCK + 6)))

    def write_registers(self, start: int, values: list[int]) -> None:
        """Funcția 16 (mai multe registre deodată, ex. ceasul); răspunsul: adresa, 0x10, start, număr."""
        n = len(values)
        pdu = bytes([self.address, 0x10, start >> 8, start & 0xFF, n >> 8, n & 0xFF, 2 * n])
        pdu += b"".join(bytes([v >> 8, v & 0xFF]) for v in values)
        c = crc16(pdu)
        req = pdu + bytes([c & 0xFF, c >> 8])

        def reader(ser):
            buf = b""
            deadline = time.monotonic() + 1.5
            while len(buf) < 8 and time.monotonic() < deadline:
                chunk = ser.read(8 - len(buf))
                if chunk:
                    buf += chunk
                    if len(buf) >= 5 and buf[1] & 0x80:
                        break
            return buf

        resp = self.transact(req, reader)
        if len(resp) >= 5 and resp[1] == 0x90:
            raise DriverError(f"Growatt: invertorul a refuzat scrierea (excepție Modbus cod {resp[2]})")
        if len(resp) < 8 or resp[:6] != req[:6] or crc16(resp[:6]) != (resp[6] | (resp[7] << 8)):
            raise DriverError("Growatt: răspuns neașteptat la scriere" if resp else "Growatt: fără răspuns la scriere")

    def write_register(self, reg: int, value: int) -> None:
        """Funcția 06; răspunsul corect e ecoul cererii."""
        req = build_write(self.address, reg, value)

        def reader(ser):
            buf = b""
            deadline = time.monotonic() + 1.5
            while len(buf) < 8 and time.monotonic() < deadline:
                chunk = ser.read(8 - len(buf))
                if chunk:
                    buf += chunk
                    if len(buf) >= 5 and buf[1] & 0x80:
                        break
            return buf

        resp = self.transact(req, reader)
        if len(resp) >= 5 and resp[1] == 0x86:
            raise DriverError(f"Growatt: invertorul a refuzat scrierea (excepție Modbus cod {resp[2]})")
        if resp[:8] != req:
            raise DriverError("Growatt: răspuns neașteptat la scriere" if resp else "Growatt: fără răspuns la scriere")

    def read(self) -> InverterReading:
        regs: dict[int, int] = {}
        for start, count in self.BLOCKS:
            for i, v in enumerate(self._read_block(start, count)):
                regs[start + i] = v
        r = InverterReading(device_id=self.id, ts=time.time())
        decode(regs, r, self.battery_sign)
        r.raw["regs"] = regs
        return r

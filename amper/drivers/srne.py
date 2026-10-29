"""Invertoarele off-grid „SMG” și SRNE: Easun SMG / SMG-II, PowMr (seriile noi), Anenji, SRNE HF/HES...

Modbus RTU pe RS485, 9600 8N1, adresa 1, registre holding (funcția 03). Există două hărți de registre în aceste
aparate, iar Amper o alege singur la prima citire (`map` în config o impune):
  - „smg”  (SMG-II, PowMr, Anenji): 201 mod de lucru, 202–204 rețea, 205–208 invertor, 209–214 ieșire,
            215–217 baterie, 219–224 panouri, 225 încărcare %, 226/227 temperaturi, 229 SOC;
  - „srne” (SRNE HF/HES și clone): 0x0100 SOC, 0x0101 tensiune, 0x0102 curent baterie, 0x0107–0x0109 PV,
            0x0210 stare, 0x0213/0x0215 rețea, 0x0216/0x0218 ieșire, 0x021B consum, 0x021F încărcare %,
            0x0220/0x0221 temperaturi.
Hărțile sunt după documentația publică (proiectele open-source pentru aceste invertoare). DOAR CITIRE. NEVERIFICAT pe
un invertor real — de confirmat: harta corectă pentru modelul tău și semnul curentului / puterii bateriei.
"""
from __future__ import annotations

import time

from ..models import InverterReading
from .base import DriverError
from .modbus import ModbusDriver, s16

SMG_MODES = {0: "Pornire", 1: "Așteptare", 2: "Din rețea", 3: "Din baterie (off-grid)", 4: "Bypass", 5: "Încărcare",
             6: "Defect"}
SRNE_STATES = {0: "Pornire", 1: "Așteptare", 2: "Inițializare", 3: "Pornire lentă", 4: "Din rețea", 5: "Din baterie",
               6: "Baterie → rețea", 7: "Rețea → baterie", 8: "Activare baterie", 9: "Oprit de utilizator", 10: "Defect"}


def plausible_smg(regs: dict[int, int]) -> bool:
    """Tensiunea bateriei (215, ×0,1) între 10 și 70 V și modul de lucru cunoscut."""
    v = regs.get(215)
    return v is not None and 100 <= v <= 700 and regs.get(201) in SMG_MODES


def plausible_srne(regs: dict[int, int]) -> bool:
    v, soc = regs.get(0x0101), regs.get(0x0100)
    return v is not None and 100 <= v <= 700 and soc is not None and soc <= 100


def decode_smg(regs: dict[int, int], r: InverterReading, sign: int = 1) -> None:
    g = regs.get

    def sc(i, k, signed=False):
        v = g(i)
        return None if v is None else round((s16(v) if signed else v) * k, 3)
    r.status_code = g(201)
    r.status = SMG_MODES.get(r.status_code, f"mod {r.status_code}") if r.status_code is not None else None
    r.ac_in_voltage = sc(202, 0.1)
    grid = sc(204, 1, signed=True)
    r.ac_in_power = None if grid is None else max(0.0, grid)
    r.output_voltage, r.output_freq = sc(209, 0.1), sc(211, 0.01)
    r.load_power, r.load_percent = sc(213, 1, signed=True), g(225)
    r.battery_voltage = sc(215, 0.1)
    bp = sc(217, 1, signed=True)
    r.battery_power = None if bp is None else bp * sign
    r.pv_voltage, r.pv_current, r.pv_power = sc(219, 0.1), sc(220, 0.1), sc(223, 1)
    r.dcdc_temp, r.inverter_temp = sc(226, 1, signed=True), sc(227, 1, signed=True)
    r.battery_soc = g(229)
    r.fault_code = 1 if r.status_code == 6 else 0
    r.fault_text = "Invertorul raportează defect" if r.status_code == 6 else None


def decode_srne(regs: dict[int, int], r: InverterReading, sign: int = 1) -> None:
    g = regs.get

    def sc(i, k, signed=False):
        v = g(i)
        return None if v is None else round((s16(v) if signed else v) * k, 3)
    r.status_code = g(0x0210)
    r.status = SRNE_STATES.get(r.status_code, f"stare {r.status_code}") if r.status_code is not None else None
    r.battery_soc, r.battery_voltage = g(0x0100), sc(0x0101, 0.1)
    cur = sc(0x0102, 0.1, signed=True)
    if cur is not None and r.battery_voltage is not None:
        r.battery_power = round(cur * r.battery_voltage * sign)
    r.pv_voltage, r.pv_current, r.pv_power = sc(0x0107, 0.1), sc(0x0108, 0.1), sc(0x0109, 1)
    r.ac_in_voltage = sc(0x0213, 0.1)
    r.output_voltage, r.output_freq = sc(0x0216, 0.1), sc(0x0218, 0.01)
    r.load_power, r.load_percent = sc(0x021B, 1), g(0x021F)
    r.dcdc_temp, r.inverter_temp = sc(0x0220, 0.1, signed=True), sc(0x0221, 0.1, signed=True)
    r.fault_code = 1 if r.status_code == 10 else 0
    r.fault_text = "Invertorul raportează defect" if r.status_code == 10 else None


SMG_BLOCKS = [(201, 30)]
SRNE_BLOCKS = [(0x0100, 10), (0x0210, 18)]


class SrneDriver(ModbusDriver):
    name = "SMG/SRNE"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.map = cfg.get("map") if cfg.get("map") in ("smg", "srne") else None
        self.battery_sign = int(cfg.get("battery_power_sign", 1))

    def detect(self) -> str:
        """Care hartă răspunde cu valori plauzibile: întâi SMG, apoi SRNE."""
        for name, blocks, ok in (("smg", SMG_BLOCKS, plausible_smg), ("srne", SRNE_BLOCKS, plausible_srne)):
            try:
                if ok(self.read_map(blocks)):
                    return name
            except DriverError:
                continue
        raise DriverError(f"SMG/SRNE {self.id}: nicio hartă de registre nu răspunde")

    def read(self) -> InverterReading:
        if self.map is None:
            self.map = self.detect()
        regs = self.read_map(SMG_BLOCKS if self.map == "smg" else SRNE_BLOCKS)
        r = InverterReading(device_id=self.id, ts=time.time())
        (decode_smg if self.map == "smg" else decode_srne)(regs, r, self.battery_sign)
        r.raw["map"] = self.map
        r.raw["regs"] = regs
        return r

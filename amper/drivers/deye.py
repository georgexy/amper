"""Invertoarele hibride Deye monofazate (SUN-…K-SG01LP1 / SG03LP1 / SG05LP1) — același protocol au și Sunsynk și Sol-Ark.

Modbus RTU pe portul RS485 („Modbus”) al invertorului, 9600 8N1, adresa 1, registre holding (funcția 03). Harta după
documentația publică a protocolului Deye (aceeași folosită de proiectele open-source pentru Sunsynk / Solarman).
DOAR CITIRE. NEVERIFICAT pe un Deye real — de confirmat la primul invertor: semnul puterii bateriei (190) și al
rețelei (169), temperaturile (×0,1 cu decalaj 1000) și care registru e puterea consumului (178) pe modelul tău.
"""
from __future__ import annotations

import time

from ..models import InverterReading
from .modbus import ModbusDriver, s16

# 0: tipul aparatului (2 string, 3 hibrid monofazat, 4 micro, 5 hibrid trifazat LV, 6 trifazat HV), 3–7: seria (ASCII)
BLOCKS = [(0, 8), (59, 1), (70, 43), (150, 44)]
DEVICE_TYPES = {2: "string", 3: "hibrid monofazat", 4: "microinvertor", 5: "hibrid trifazat (LV)", 6: "hibrid trifazat (HV)"}
STATE = {0: "Așteptare", 1: "Autotest", 2: "Funcționare normală", 3: "Alarmă", 4: "Defect"}


def serial_number(regs: dict[int, int]) -> str:
    raw = b"".join(bytes([regs.get(i, 0) >> 8, regs.get(i, 0) & 0xFF]) for i in range(3, 8))
    return "".join(chr(c) for c in raw if 32 < c < 127)


def looks_like_deye(regs: dict[int, int]) -> bool:
    """Răspunsul la registrele 0–7 e al unui Deye: tip cunoscut și o serie de cel puțin 6 caractere."""
    return regs.get(0) in DEVICE_TYPES and len(serial_number(regs)) >= 6


def decode(regs: dict[int, int], r: InverterReading, battery_sign: int = -1, grid_sign: int = 1) -> None:
    g = regs.get

    def sc(i, k, signed=False):
        v = g(i)
        if v is None:
            return None
        return round((s16(v) if signed else v) * k, 3)

    def temp(i):
        v = g(i)
        return None if v is None else round((v - 1000) * 0.1, 1)

    r.status_code = g(59)
    r.status = STATE.get(r.status_code, f"stare {r.status_code}") if r.status_code is not None else None
    r.pv_voltage, r.pv_current = sc(109, 0.1), sc(110, 0.1)
    r.pv2_voltage, r.pv2_current = sc(111, 0.1), sc(112, 0.1)
    pv1, pv2 = g(186), g(187)
    r.pv_power = None if pv1 is None and pv2 is None else (pv1 or 0) + (pv2 or 0)
    r.ac_in_voltage = sc(150, 0.1)
    grid = sc(169, 1, signed=True)
    r.ac_in_power = None if grid is None else max(0.0, grid * grid_sign)        # doar ce vine din rețea
    r.output_voltage = sc(154, 0.1)
    r.output_freq = sc(193, 0.01)
    r.load_power = sc(178, 1, signed=True)
    r.inverter_temp, r.dcdc_temp = temp(91), temp(90)
    r.battery_voltage = sc(183, 0.01)
    r.battery_soc = g(184)
    bp = sc(190, 1, signed=True)
    r.battery_power = None if bp is None else bp * battery_sign                # Deye: + = descărcare
    r.raw.update(device_type=DEVICE_TYPES.get(g(0)), serial=serial_number(regs), battery_temp=temp(182),
                 today_pv_kwh=sc(108, 0.1), today_load_kwh=sc(84, 0.1), today_grid_kwh=sc(76, 0.1),
                 today_batt_charge_kwh=sc(70, 0.1), today_batt_discharge_kwh=sc(71, 0.1))
    r.fault_code = 0 if r.status_code != 4 else 1
    r.fault_text = "Invertorul raportează defect" if r.status_code == 4 else None


class DeyeDriver(ModbusDriver):
    name = "Deye"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.battery_sign = int(cfg.get("battery_power_sign", -1))
        self.grid_sign = int(cfg.get("grid_power_sign", 1))

    def identify(self) -> dict[int, int]:
        return self.read_map([(0, 8)])

    def read(self) -> InverterReading:
        regs = self.read_map(BLOCKS)
        r = InverterReading(device_id=self.id, ts=time.time())
        decode(regs, r, self.battery_sign, self.grid_sign)
        r.raw["regs"] = regs
        return r

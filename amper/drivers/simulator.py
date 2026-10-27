"""Simulator: o instalație off-grid virtuală (PV + consum + mai multe module) pentru teste fără hardware.

Opțiuni pe dispozitive simulate (în config):
  fault_after_s: 300    → după 300 s, BMS-ul acestui modul oprește încărcarea (MOSFET încărcare OFF)
  start_soc: 60
"""
from __future__ import annotations

import math
import random
import time

from ..models import InverterReading, PackReading
from .base import Driver

LFP_OCV = [(0, 2.80), (5, 3.10), (10, 3.20), (20, 3.25), (40, 3.28), (60, 3.30),
           (80, 3.33), (90, 3.36), (97, 3.40), (100, 3.55)]


def ocv(soc: float) -> float:
    soc = max(0.0, min(100.0, soc))
    for (s0, v0), (s1, v1) in zip(LFP_OCV, LFP_OCV[1:]):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return LFP_OCV[-1][1]


class SimPack:
    def __init__(self, cfg: dict, rnd: random.Random):
        self.id = cfg["id"]
        self.kind = cfg["type"]
        self.cap = float(cfg.get("capacity_ah", 100))
        self.soc = float(cfg.get("start_soc", rnd.uniform(55, 75)))
        self.fault_after = cfg.get("fault_after_s")
        self.cell_offsets = [rnd.gauss(0, 0.006) for _ in range(16)]
        self.current = 0.0
        self.charge_mos = True
        self.discharge_mos = True
        self.cycles = rnd.randint(150, 600)
        self.temp = rnd.uniform(16, 22)

    def voltage(self) -> float:
        return 16 * ocv(self.soc) + self.current * 0.004


class SimWorld:
    """Lumea simulată comună tuturor dispozitivelor simulate (ca valorile să fie coerente)."""
    _instance = None

    def __init__(self, device_cfgs: list[dict], start_t: float | None = None, seed: int = 7):
        self.rnd = random.Random(seed)
        self.packs = {d["id"]: SimPack(d, self.rnd) for d in device_cfgs
                      if d["type"] in ("sim_daly", "sim_seplos")}
        inv = [d for d in device_cfgs if d["type"] == "sim_growatt"]
        self.pv_peak = float(inv[0].get("pv_peak_w", 4400)) if inv else 4400
        self.t0 = self.t = start_t or time.time()
        self.pv = self.load = self.bat_power = 0.0
        self.cloud = 1.0

    @classmethod
    def get(cls, device_cfgs=None) -> "SimWorld":
        if cls._instance is None:
            cls._instance = SimWorld(device_cfgs or [])
        return cls._instance

    def _pv(self, t: float) -> float:
        lt = time.localtime(t)
        h = lt.tm_hour + lt.tm_min / 60 + lt.tm_sec / 3600
        day = math.sin(math.pi * (h - 7) / 11) if 7 < h < 18 else 0.0
        self.cloud += self.rnd.gauss(0, 0.03)
        self.cloud = min(1.0, max(0.35, self.cloud))
        return max(0.0, day * self.pv_peak * 0.82 * self.cloud)

    def _load(self, t: float) -> float:
        lt = time.localtime(t)
        h = lt.tm_hour + lt.tm_min / 60
        base = 280 + 420 * math.exp(-((h - 20) ** 2) / 4) + 300 * math.exp(-((h - 8) ** 2) / 1.5)
        spike = 1800 if self.rnd.random() < 0.02 else 0
        return base + self.rnd.uniform(-40, 60) + spike

    def step(self, t: float | None = None) -> None:
        t = time.time() if t is None else t
        while self.t < t:
            dt = min(10.0, t - self.t)
            self.t += dt
            self._advance(dt)

    def _advance(self, dt: float) -> None:
        self.pv = self._pv(self.t)
        self.load = self._load(self.t)
        surplus = self.pv - self.load
        packs = list(self.packs.values())
        for p in packs:
            if p.fault_after is not None and self.t - self.t0 > float(p.fault_after):
                p.charge_mos = False
        if surplus >= 0:
            able = [p for p in packs if p.charge_mos and p.soc < 99.8]
        else:
            able = [p for p in packs if p.discharge_mos and p.soc > 3]
        total_cap = sum(p.cap for p in able) or 1
        if not able and surplus > 0:
            self.pv = self.load            # bancă plină: invertorul limitează PV
            surplus = 0
        bank_v = sum(p.voltage() for p in packs) / max(len(packs), 1)
        self.bat_power = surplus
        for p in packs:
            if p in able:
                share = surplus * p.cap / total_cap
                # încărcare tapered peste 95 %
                if share > 0 and p.soc > 95:
                    share *= max(0.05, (100 - p.soc) / 5)
                p.current = share / bank_v
            else:
                p.current = 0.0
            p.soc = max(0.0, min(100.0, p.soc + p.current * dt / 3600 / p.cap * 100))
            p.temp += (18 + abs(p.current) * 0.08 - p.temp) * dt / 900

    # ------- citiri -------
    def pack_reading(self, pid: str) -> PackReading:
        p = self.packs[pid]
        v_cell = p.voltage() / 16
        cells = [round(v_cell + o + self.rnd.gauss(0, 0.002), 3) for o in p.cell_offsets]
        state = "charging" if p.current > 0.2 else "discharging" if p.current < -0.2 else "idle"
        return PackReading(
            device_id=pid, ts=self.t, voltage=round(sum(cells), 2), current=round(p.current, 1),
            soc=round(p.soc, 1), remaining_ah=round(p.cap * p.soc / 100, 1), capacity_ah=p.cap,
            cycles=p.cycles, soh=98.0 if p.kind == "sim_seplos" else None, cells=cells,
            temps=[round(p.temp + self.rnd.gauss(0, 0.2), 1) for _ in range(2 if p.kind == "sim_daly" else 4)],
            charge_mos=p.charge_mos, discharge_mos=p.discharge_mos, balancing=max(cells) - min(cells) > 0.02,
            state=state, alarms=[])

    def inverter_reading(self, iid: str) -> InverterReading:
        packs = list(self.packs.values())
        soc = sum(p.soc * p.cap for p in packs) / max(sum(p.cap for p in packs), 1)
        bv = sum(p.voltage() for p in packs) / max(len(packs), 1)
        if self.pv > 50 and self.bat_power > 50:
            code = 5
        elif self.pv > 50:
            code = 12
        else:
            code = 2
        return InverterReading(
            device_id=iid, ts=self.t, status_code=code, status=f"{code}", pv_power=round(self.pv, 0),
            pv_voltage=round(280 + self.rnd.uniform(-8, 8), 1) if self.pv > 0 else 0.0,
            load_power=round(self.load, 0), load_percent=round(self.load / 50, 1),
            battery_voltage=round(bv, 2), battery_power=round(self.bat_power, 0), battery_soc=round(soc),
            ac_in_power=0.0, output_voltage=230.0, output_freq=50.0, inverter_temp=round(32 + self.load / 300, 1),
            dcdc_temp=30.0, fault_code=0, warning_code=0)


class SimPackDriver(Driver):
    kind = "pack"

    def __init__(self, cfg: dict, world: SimWorld):
        super().__init__(cfg)
        self.world = world

    def read(self) -> PackReading:
        self.world.step()
        return self.world.pack_reading(self.id)


# setările unui SPF 5000 ES real (citite pe 2026-10-25), ca registre holding
SIM_HOLDING = {1: 2, 2: 1, 3: 0, 4: 0, 5: 0, 6: 0, 8: 2, 18: 1, 19: 0, 22: 1, 34: 75, 35: 545, 36: 545,
               37: 460, 38: 35, 39: 2, 82: 420, 95: 540}


class SimInverterDriver(Driver):
    kind = "inverter"

    def __init__(self, cfg: dict, world: SimWorld):
        super().__init__(cfg)
        self.world = world
        self.holding = dict(SIM_HOLDING)

    # setări (aceeași interfață ca GrowattSPFDriver)
    def read_holding(self, regs) -> dict[int, int]:
        out = {}
        for b in sorted({r // 45 for r in regs}):
            out.update({i: self.holding.get(i, 0) for i in range(b * 45, b * 45 + 45)})
        lt = time.localtime()
        for i, v in enumerate([lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec]):
            if 45 + i in out:
                out[45 + i] = self.holding.get(45 + i, v)
        return out

    def read_settings(self) -> dict[int, int]:
        return self.read_holding([0, 45, 90])

    def write_register(self, reg: int, value: int) -> None:
        self.holding[reg] = value

    def write_registers(self, start: int, values: list[int]) -> None:
        for i, v in enumerate(values):
            self.holding[start + i] = v

    def read(self) -> InverterReading:
        self.world.step()
        r = self.world.inverter_reading(self.id)
        from .growatt_spf import STATUS
        r.status = STATUS.get(r.status_code, r.status)
        return r

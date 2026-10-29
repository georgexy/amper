"""Registrul de drivere: tip din config → clasă."""
from __future__ import annotations

from .base import Driver, DriverError
from .daly import DalyBleDriver, DalyDriver
from .daly_auto import DalyAutoDriver, is_auto
from .growatt_spf import GrowattSPFDriver
from .deye import DeyeDriver
from .srne import SrneDriver
from .voltronic import VoltronicDriver
from .jk import JkBleDriver, JkUartDriver
from .pace import PaceDriver
from .seplos import SeplosDriver
from .simulator import SimInverterDriver, SimPackDriver, SimWorld

REAL = {"daly": DalyDriver, "seplos": SeplosDriver, "pace": PaceDriver, "growatt_spf": GrowattSPFDriver,
        "deye": DeyeDriver, "voltronic": VoltronicDriver, "srne": SrneDriver}


def make_driver(cfg: dict, all_cfgs: list[dict] | None = None) -> Driver:
    t = cfg["type"]
    if t == "daly":
        if is_auto(cfg):
            return DalyAutoDriver(cfg, all_cfgs)
        return DalyBleDriver(cfg) if cfg.get("ble") else DalyDriver(cfg)
    if t == "jk":
        return JkBleDriver(cfg) if cfg.get("ble") else JkUartDriver(cfg)
    if t in REAL:
        return REAL[t](cfg)
    world = SimWorld.get(all_cfgs or [cfg])
    if t in ("sim_daly", "sim_seplos"):
        return SimPackDriver(cfg, world)
    if t == "sim_growatt":
        return SimInverterDriver(cfg, world)
    raise ValueError(f"tip de dispozitiv necunoscut: {t}")


__all__ = ["Driver", "DriverError", "make_driver", "SimWorld"]

"""Modelele de date comune pentru toate driverele."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PackReading:
    """O citire de la un BMS (un modul / o baterie).

    Convenție semn curent: POZITIV = încărcare, NEGATIV = descărcare.
    """
    device_id: str
    ts: float
    voltage: Optional[float] = None          # V
    current: Optional[float] = None          # A (+ încărcare, - descărcare)
    soc: Optional[float] = None              # %
    remaining_ah: Optional[float] = None     # Ah
    capacity_ah: Optional[float] = None      # Ah (capacitate totală / nominală)
    cycles: Optional[int] = None
    soh: Optional[float] = None              # %
    cells: list[float] = field(default_factory=list)   # V, pe fiecare celulă
    temps: list[float] = field(default_factory=list)   # °C
    charge_mos: Optional[bool] = None        # True = încărcarea permisă
    discharge_mos: Optional[bool] = None     # True = descărcarea permisă
    balancing: Optional[bool] = None
    state: Optional[str] = None              # "charging" / "discharging" / "idle"
    alarms: list[str] = field(default_factory=list)        # avertismente BMS (alarmă, nivel 1)
    protections: list[str] = field(default_factory=list)   # protecții / defecte BMS (nivel 2, critice)
    raw: dict = field(default_factory=dict)  # date brute utile la depanare

    @property
    def power(self) -> Optional[float]:
        if self.voltage is None or self.current is None:
            return None
        return self.voltage * self.current

    @property
    def cell_min(self) -> Optional[float]:
        return min(self.cells) if self.cells else None

    @property
    def cell_max(self) -> Optional[float]:
        return max(self.cells) if self.cells else None

    @property
    def cell_delta(self) -> Optional[float]:
        return (max(self.cells) - min(self.cells)) if self.cells else None

    @property
    def temp_max(self) -> Optional[float]:
        return max(self.temps) if self.temps else None

    @property
    def temp_min(self) -> Optional[float]:
        return min(self.temps) if self.temps else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            kind="pack",
            power=_r(self.power, 1),
            cell_min=self.cell_min,
            cell_max=self.cell_max,
            cell_delta=_r(self.cell_delta, 3),
            temp_max=self.temp_max,
            temp_min=self.temp_min,
        )
        return d


@dataclass
class InverterReading:
    """O citire de la invertor.

    Convenție semn putere baterie: POZITIV = încărcare, NEGATIV = descărcare.
    """
    device_id: str
    ts: float
    status: Optional[str] = None
    status_code: Optional[int] = None
    pv_power: Optional[float] = None         # W (total)
    pv_voltage: Optional[float] = None       # V (PV1)
    pv_current: Optional[float] = None       # A (PV1), citit din invertor (Growatt: Buck1Curr)
    pv2_voltage: Optional[float] = None
    pv2_current: Optional[float] = None      # A (PV2)
    load_power: Optional[float] = None       # W (ieșire AC)
    load_percent: Optional[float] = None     # %
    battery_voltage: Optional[float] = None  # V
    battery_power: Optional[float] = None    # W (+ încărcare)
    battery_soc: Optional[float] = None      # % (raportat de invertor)
    ac_in_power: Optional[float] = None      # W (rețea/generator)
    ac_in_voltage: Optional[float] = None
    output_voltage: Optional[float] = None
    output_freq: Optional[float] = None
    inverter_temp: Optional[float] = None
    dcdc_temp: Optional[float] = None
    fault_code: Optional[int] = None         # 0 = fără defect
    fault_text: Optional[str] = None
    warning_code: Optional[int] = None       # biții de avertisment (registrele 41 + 42)
    warnings: list[str] = field(default_factory=list)   # avertismente care se trimit pe telefon
    notices: list[str] = field(default_factory=list)    # informative (ex. PV insuficient), doar afișate
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = "inverter"
        return d


def _r(v, n):
    return None if v is None else round(v, n)

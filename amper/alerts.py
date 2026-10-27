"""Motorul de alerte.

Fiecare regulă produce, la fiecare evaluare, o listă de condiții (cheie, nivel, mesaj).
O alertă se declanșează doar după ce condiția persistă `hold` secunde și se închide
(cu notificare de revenire) când condiția dispare.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field


def ro(v: float, d: int = 1) -> str:
    """Număr formatat românește (virgulă zecimală)."""
    return f"{v:.{d}f}".replace(".", ",")


@dataclass
class Condition:
    key: str                 # unic: regulă + dispozitiv
    rule: str
    device: str | None
    level: str               # "info", "warning" sau "critical"
    message: str
    hold: float = 0          # secunde de persistență înainte de alertă
    notify_clear: bool = True   # trimite și „Rezolvat” când condiția trece


@dataclass
class ActiveAlert:
    cond: Condition
    first_seen: float
    fired: bool = False
    db_id: int | None = None
    fired_at: float | None = None
    extra: dict = field(default_factory=dict)


def evaluate(snapshot: dict, cfg: dict, now: float) -> list[Condition]:
    """Calculează condițiile active din starea curentă (snapshot-ul motorului)."""
    a = cfg
    out: list[Condition] = []
    devices = snapshot["devices"]
    packs = {d: v for d, v in devices.items() if v["kind"] == "pack"}
    fresh = {d: v for d, v in packs.items() if v.get("reading") and not v.get("stale")}
    names = {d: v["name"] for d, v in devices.items()}

    # --- comunicație pierdută ---
    for d, v in devices.items():
        if not v.get("connected", True):      # nelegat încă (n-a răspuns niciodată)
            continue
        last = v.get("last_ok")
        age = now - last if last else now - snapshot["started"]
        if age > a["comm_lost_seconds"]:
            out.append(Condition(f"comm:{d}", "comm_lost", d, "critical",
                                 f"{names[d]}: nu mai comunică de {int(age // 60)} min"
                                 + (f" ({v['error']})" if v.get("error") else "")))

    bank = snapshot.get("bank", {})
    bank_charging = (bank.get("current") or 0) > a["bank_charging_amps"]
    inv = next((v["reading"] for v in devices.values()
                if v["kind"] == "inverter" and v.get("reading") and not v.get("stale")), None)
    if inv and inv.get("battery_power") is not None and inv.get("battery_voltage"):
        bank_charging = bank_charging or inv["battery_power"] / inv["battery_voltage"] > a["bank_charging_amps"]

    for d, v in fresh.items():
        r = v["reading"]
        n = names[d]
        soc = r.get("soc")
        cur = r.get("current") or 0

        # --- modulul NU se încarcă, deși banca se încarcă (problema principală) ---
        if bank_charging and soc is not None and soc < a["full_soc"] and cur < a["pack_idle_amps"]:
            out.append(Condition(f"notchg:{d}", "not_charging", d, "critical",
                                 f"{n} NU se încarcă (curent {ro(cur)} A, SOC {soc:.0f}%) "
                                 f"în timp ce restul băncii se încarcă", hold=a["not_charging_minutes"] * 60))

        # --- MOSFET încărcare / descărcare oprit ---
        if r.get("charge_mos") is False and (soc is None or soc < a["full_soc"]):
            out.append(Condition(f"chgmos:{d}", "charge_mos_off", d, "critical",
                                 f"{n}: BMS-ul a OPRIT încărcarea (MOSFET încărcare OFF)", hold=60))
        if r.get("discharge_mos") is False and (soc is None or soc > 5):
            out.append(Condition(f"dsgmos:{d}", "discharge_mos_off", d, "critical",
                                 f"{n}: BMS-ul a OPRIT descărcarea (MOSFET descărcare OFF)", hold=60))

        # --- SOC scăzut ---
        if soc is not None:
            if soc <= a["low_soc_critical"]:
                out.append(Condition(f"soc:{d}", "low_soc", d, "critical", f"{n}: SOC critic {soc:.0f}%", hold=60))
            elif soc <= a["low_soc_warning"]:
                out.append(Condition(f"soc:{d}", "low_soc", d, "warning", f"{n}: SOC scăzut {soc:.0f}%", hold=120))

        # --- celule ---
        if r.get("cell_max") is not None and r["cell_max"] >= a["cell_high_volts"]:
            out.append(Condition(f"cellhi:{d}", "cell_high", d, "warning",
                                 f"{n}: celulă la {ro(r['cell_max'], 3)} V", hold=30))
        if r.get("cell_min") is not None and r["cell_min"] <= a["cell_low_volts"]:
            out.append(Condition(f"celllo:{d}", "cell_low", d, "critical",
                                 f"{n}: celulă la {ro(r['cell_min'], 3)} V", hold=30))
        if r.get("cell_delta") is not None and r["cell_delta"] >= a["cell_delta_volts"]:
            out.append(Condition(f"delta:{d}", "cell_delta", d, "warning",
                                 f"{n}: dezechilibru celule {r['cell_delta'] * 1000:.0f} mV", hold=300))

        # --- temperaturi ---
        if r.get("temp_max") is not None and r["temp_max"] >= a["temp_high_c"]:
            out.append(Condition(f"temphi:{d}", "temp_high", d, "critical",
                                 f"{n}: temperatură {r['temp_max']:.0f} °C", hold=60))
        if r.get("temp_min") is not None and r["temp_min"] <= a["temp_low_charge_c"] and cur > 1:
            out.append(Condition(f"templo:{d}", "temp_low_charge", d, "critical",
                                 f"{n}: se încarcă la {r['temp_min']:.0f} °C (prea rece pentru LiFePO4)", hold=60))

        # --- raportate de BMS: protecții/defecte (critice) și alarme (avertismente) ---
        if r.get("protections"):
            out.append(Condition(f"bmsprot:{d}", "bms_protection", d, "critical",
                                 f"{n}: PROTECȚIE BMS — " + "; ".join(r["protections"][:4]), hold=10))
        if r.get("alarms"):
            out.append(Condition(f"bms:{d}", "bms_alarm", d, "warning",
                                 f"{n}: avertisment BMS — " + "; ".join(r["alarms"][:4]), hold=30))

    # --- SOC divergent față de celelalte module ---
    socs = {d: v["reading"]["soc"] for d, v in fresh.items() if v["reading"].get("soc") is not None}
    if len(socs) >= 3:
        for d, s in socs.items():
            others = [x for k, x in socs.items() if k != d]
            med = statistics.median(others)
            if abs(s - med) >= a["soc_divergence_percent"]:
                out.append(Condition(f"div:{d}", "soc_divergence", d, "warning",
                                     f"{names[d]}: SOC {s:.0f}% față de {med:.0f}% la celelalte module",
                                     hold=a["soc_divergence_minutes"] * 60))

    # --- invertor ---
    if inv and inv.get("fault_code"):
        out.append(Condition("invfault", "inverter_fault", inv["device_id"], "critical",
                             f"Invertor: DEFECT {inv['fault_code']:02d} — {inv.get('fault_text') or 'necunoscut'}", hold=10))
    if inv and inv.get("warnings"):
        out.append(Condition("invwarn", "inverter_warning", inv["device_id"], "warning",
                             "Invertor: avertisment — " + "; ".join(inv["warnings"][:4]), hold=60))
    return out


# ---------------- alerte proprii, setate de utilizator în aplicație ----------------
def _fresh_packs(snapshot: dict) -> list[dict]:
    return [v["reading"] for v in snapshot["devices"].values()
            if v["kind"] == "pack" and v.get("reading") and not v.get("stale")]


def _inverter(snapshot: dict) -> dict:
    return next((v["reading"] for v in snapshot["devices"].values()
                 if v["kind"] == "inverter" and v.get("reading") and not v.get("stale")), None) or {}


def _agg(fn, vals):
    vals = [v for v in vals if v is not None]
    return fn(vals) if vals else None


# metrica: (denumire, unitate, marjă de histerezis, funcție snapshot → valoare)
METRICS = {
    "bank_soc": ("SOC-ul băncii", "%", 2, lambda s: (s.get("bank") or {}).get("soc")),
    "pack_soc_min": ("SOC-ul celui mai descărcat modul", "%", 2,
                     lambda s: _agg(min, [p.get("soc") for p in _fresh_packs(s)])),
    "bank_voltage": ("Tensiunea băncii", "V", 0.3, lambda s: (s.get("bank") or {}).get("voltage")),
    "cell_max": ("Celula cea mai încărcată", "V", 0.02, lambda s: _agg(max, [p.get("cell_max") for p in _fresh_packs(s)])),
    "cell_min": ("Celula cea mai descărcată", "V", 0.02, lambda s: _agg(min, [p.get("cell_min") for p in _fresh_packs(s)])),
    "pack_temp_max": ("Temperatura maximă la module", "°C", 2,
                      lambda s: _agg(max, [p.get("temp_max") for p in _fresh_packs(s)])),
    "pv_power": ("Puterea panourilor", "W", 100, lambda s: _inverter(s).get("pv_power")),
    "load_power": ("Consumul casei", "W", 100, lambda s: _inverter(s).get("load_power")),
    "charge_power": ("Puterea de încărcare a bateriei", "W", 100, lambda s: _inverter(s).get("battery_power")),
    "grid_voltage": ("Tensiunea rețelei / generatorului", "V", 10, lambda s: _inverter(s).get("ac_in_voltage")),
    "inverter_temp": ("Temperatura invertorului", "°C", 2, lambda s: _inverter(s).get("inverter_temp")),
}


def _num(v: float, unit: str) -> str:
    d = 2 if unit == "V" and abs(v) < 10 else 1 if unit in ("V", "°C") else 0
    return f"{v:.{d}f}".replace(".", ",")


def evaluate_user(rules: list[dict], snapshot: dict, active: set[str]) -> list[Condition]:
    """Condițiile alertelor proprii. Odată pornită, o alertă rămâne activă până trece pragul cu marja de histerezis
    (ex. „sub 25%” se închide abia peste 27%), ca să nu oscileze în jurul pragului."""
    out = []
    for r in rules:
        if not r.get("enabled") or r.get("metric") not in METRICS:
            continue
        name, unit, margin, get = METRICS[r["metric"]]
        v = get(snapshot)
        if v is None:
            continue
        key = f"user:{r['id']}"
        thr = float(r["value"])
        below = r["op"] == "<"
        on = key in active
        hit = (v < (thr + margin if on else thr)) if below else (v > (thr - margin if on else thr))
        if hit:
            what = r.get("name") or f"{name} {'sub' if below else 'peste'} {_num(thr, unit)} {unit}"
            out.append(Condition(key, "user", None, r.get("level") or "warning",
                                 f"{what} (acum {_num(v, unit)} {unit})", hold=float(r.get("hold_s") or 0),
                                 notify_clear=bool(r.get("notify_clear", 1))))
    return out


class AlertManager:
    def __init__(self, cfg: dict, db, notifier):
        self.cfg = cfg
        self.db = db
        self.notifier = notifier
        self.active: dict[str, ActiveAlert] = {}

    def process(self, snapshot: dict, now: float | None = None) -> None:
        now = now or time.time()
        conds = {c.key: c for c in evaluate(snapshot, self.cfg, now)}
        fired = {k for k, a in self.active.items() if a.fired}
        conds.update({c.key: c for c in evaluate_user(self.user_rules(), snapshot, fired)})

        for key, c in conds.items():
            act = self.active.get(key)
            if act is None:
                act = self.active[key] = ActiveAlert(c, first_seen=now)
            act.cond = c                       # actualizează mesajul cu valorile curente
            act.extra["last_seen"] = now
            if not act.fired and now - act.first_seen >= c.hold:
                act.fired, act.fired_at = True, now
                act.db_id = self.db.open_alert(int(now), c.device, c.rule, c.level, c.message, key=c.key)
                icon = {"critical": "🔴 ", "warning": "🟠 ", "info": "🔔 "}.get(c.level, "🟠 ")
                self.notifier.send(icon + c.message, title="Amper: alertă",
                                   priority="high" if c.level == "critical" else "default")

        # histerezis: un nor trecător sau o citire ratată nu resetează / nu închide alerta
        for key in list(self.active):
            if key in conds:
                continue
            act = self.active[key]
            gone_for = now - act.extra.get("last_seen", act.first_seen)
            if gone_for < (self.CLEAR_AFTER if act.fired else self.PENDING_GAP):
                continue
            self.active.pop(key)
            if act.fired:
                self.db.close_alert(act.db_id, int(now))
                if act.cond.notify_clear and self._user_notify_clear(key):
                    self.notifier.send(f"✅ Rezolvat: {act.cond.message}", title="Amper: rezolvat", priority="low")

    # alertele proprii (tabela user_alerts), citite din baza de date la fiecare 10 s
    _rules_cache: tuple[float, list] = (0.0, [])

    def user_rules(self) -> list[dict]:
        t, rules = self._rules_cache
        if time.monotonic() - t > 10 and hasattr(self.db, "user_alerts"):
            rules = self.db.user_alerts()
            self._rules_cache = (time.monotonic(), rules)
        return rules

    def reload_rules(self) -> None:
        self._rules_cache = (0.0, [])

    def _user_notify_clear(self, key: str) -> bool:
        if not key.startswith("user:"):
            return True
        r = next((r for r in self.user_rules() if f"user:{r['id']}" == key), None)
        return bool(r["notify_clear"]) if r else False

    def restore(self, rows: list[dict], now: float) -> list[dict]:
        """Preia alertele rămase deschise de la rularea anterioară, fără să le mai trimită o dată pe telefon.
        Dacă problema nu mai există, se închid după CLEAR_AFTER (cu „Rezolvat”). Întoarce rândurile fără cheie."""
        old = []
        for r in rows:
            if not r.get("key"):
                old.append(r)
                continue
            c = Condition(r["key"], r["rule"], r["device"], r["level"], r["message"])
            self.active[r["key"]] = ActiveAlert(c, first_seen=r["ts_start"], fired=True, db_id=r["id"],
                                                fired_at=r["ts_start"], extra={"last_seen": now})
        return old

    def drop_device(self, device: str, now: float | None = None) -> int:
        """Dispozitiv deconectat din aplicație: alertele lui se închid în tăcere (fără „✅ Rezolvat”)."""
        now = now or time.time()
        n = 0
        for key in [k for k, a in self.active.items() if a.cond.device == device]:
            act = self.active.pop(key)
            if act.fired and act.db_id:
                self.db.close_alert(act.db_id, int(now))
            n += 1
        return n

    def shift_clock(self, delta: float) -> None:
        """Ceasul sistemului a sărit cu `delta` s: duratele alertelor în curs rămân cele reale."""
        for act in self.active.values():
            act.first_seen += delta
            if act.fired_at is not None:
                act.fired_at += delta
            if "last_seen" in act.extra:
                act.extra["last_seen"] += delta

    PENDING_GAP = 120    # s — întreruperi tolerate înainte ca o alertă să se declanșeze
    CLEAR_AFTER = 180     # s — cât trebuie să lipsească o condiție ca alerta să fie considerată rezolvată

    def active_list(self) -> list[dict]:
        return [dict(key=a.cond.key, rule=a.cond.rule, device=a.cond.device, level=a.cond.level,
                     message=a.cond.message, since=a.fired_at) for a in self.active.values() if a.fired]

"""Defecte, protecții și avertismente: invertor (registrele 40–42), Seplos (alarm events), Daly (nivel 1/2) și alertele."""
from amper.alerts import evaluate
from amper.config import DEFAULTS
from amper.drivers import daly, growatt_spf, seplos
from amper.models import InverterReading, PackReading


def inv_regs(over):
    return {**{i: 0 for i in range(90)}, **over}


def test_growatt_fault_and_warning_registers_v017():
    r = InverterReading(device_id="inv", ts=0)
    growatt_spf.decode(inv_regs({40: 7, 41: 0x0004 | 0x8000, 42: 0x0040}), r)
    assert r.fault_code == 7 and r.fault_text == "Suprasarcină"
    assert r.warnings == ["Tensiune baterie scăzută (04)", "BMS: supratensiune totală (36)"]
    assert r.notices == ["Putere PV insuficientă (21)"]          # informativ, nu se trimite pe telefon
    r2 = InverterReading(device_id="inv", ts=0)
    growatt_spf.decode(inv_regs({42: 0x1234, 43: 99}), r2)       # vechea citire (42 ca defect) ar fi dat „defect”
    assert r2.fault_code == 0 and r2.fault_text is None


def seplos_status(custom):
    n_cells, n_temps = 16, 6
    b = bytes([0, 1, n_cells]) + bytes(n_cells) + bytes([n_temps]) + bytes(n_temps) + bytes([0, 0, len(custom)]) + bytes(custom)
    r = PackReading(device_id="s1", ts=0)
    seplos.decode_status(b, r)
    return r


def test_seplos_alarm_events():
    custom = [0] * 20
    custom[1] = 0b0000_0011          # celulă: tensiune mare (alarmă) + protecție la supratensiune
    custom[4] = 0b0010_0000          # protecție la scurtcircuit la ieșire
    custom[5] = 0b0000_0010          # „așteaptă reîncărcarea” — doar informativ
    custom[6] = 3                    # ambele MOSFET-uri pornite
    r = seplos_status(custom)
    assert r.alarms == ["Celulă: tensiune mare"]
    assert r.protections == ["Celulă: protecție la supratensiune", "Protecție la scurtcircuit la ieșire"]
    assert r.charge_mos and r.discharge_mos


def test_seplos_real_status_bytes_are_clean():
    # octeții citiți pe 2026-10-25 de la cele 3 pachete reale (fără probleme)
    r = seplos_status([0, 0, 0, 0, 0, 0, 3, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1])
    assert r.alarms == [] and r.protections == []


def test_daly_levels():
    data = bytes([0b0000_0011, 0, 0, 0, 0, 0, 0])   # celulă mare nivel 1 + nivel 2
    warn, prot = daly.split_faults(daly.decode_faults(data))
    assert warn == ["Tensiune celulă mare (nivel 1)"] and prot == ["Tensiune celulă mare (nivel 2)"]


def snapshot(pack=None, inv=None):
    devs = {}
    if pack:
        devs["s1"] = dict(kind="pack", name="Seplos 1", reading=pack, stale=False, last_ok=1000, connected=True)
    if inv:
        devs["inv"] = dict(kind="inverter", name="Growatt", reading=inv, stale=False, last_ok=1000, connected=True)
    return {"devices": devs, "bank": {}, "started": 0}


def test_alert_levels():
    cfg = DEFAULTS["alerts"]
    pack = dict(soc=80, current=0, alarms=["Celulă: tensiune mare"], protections=["Protecție la scurtcircuit la ieșire"])
    inv = dict(device_id="inv", fault_code=2, fault_text="Supratemperatură", warnings=["Suprasarcină (07)"],
               notices=["Putere PV insuficientă (21)"])
    conds = {c.rule: c for c in evaluate(snapshot(pack, inv), cfg, 1000)}
    assert conds["bms_protection"].level == "critical" and "scurtcircuit" in conds["bms_protection"].message
    assert conds["bms_alarm"].level == "warning"
    assert conds["inverter_fault"].level == "critical" and "DEFECT 02 — Supratemperatură" in conds["inverter_fault"].message
    assert conds["inverter_warning"].level == "warning" and "Suprasarcină" in conds["inverter_warning"].message
    assert "PV insuficientă" not in "".join(c.message for c in conds.values())

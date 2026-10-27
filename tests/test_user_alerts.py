"""Alertele proprii: prag, histerezis, durată minimă, „anunță și când trece”."""
from amper.alerts import AlertManager, evaluate_user
from amper.config import DEFAULTS
from amper.db import Database
from amper.notify import Notifier


def snap(soc, load=500):
    return {"devices": {"inv": dict(kind="inverter", name="Growatt", reading=dict(load_power=load), stale=False,
                                    last_ok=10**12, connected=True)},
            "bank": {"soc": soc, "voltage": 52.0}, "started": 0}


def rule(**kw):
    r = dict(id=1, name=None, metric="bank_soc", op="<", value=25, hold_s=0, level="warning", notify_clear=1, enabled=1)
    r.update(kw)
    return r


def test_threshold_and_hysteresis():
    rules = [rule()]
    assert evaluate_user(rules, snap(30), set()) == []
    c = evaluate_user(rules, snap(24), set())[0]
    assert c.key == "user:1" and "SOC-ul băncii sub 25 %" in c.message and "24" in c.message
    assert evaluate_user(rules, snap(26), {"user:1"})                 # pornită: ține până peste 27%
    assert evaluate_user(rules, snap(27.5), {"user:1"}) == []
    assert evaluate_user([rule(enabled=0)], snap(10), set()) == []


def test_full_battery_info_without_resolved_message():
    db = Database(":memory:")
    db.add_user_alert("Bateria s-a încărcat complet", "bank_soc", ">", 99, 0, "info", False)
    n = Notifier({})
    am = AlertManager(DEFAULTS["alerts"], db, n)
    am.process(snap(99.5), now=1000)
    assert n.sent[-1]["message"] == "🔔 Bateria s-a încărcat complet (acum 100 %)"   # 99,5 rotunjit
    am.process(snap(90), now=1500)                                                # a trecut de mult
    assert not any("Rezolvat" in m["message"] for m in n.sent)
    assert am.active_list() == []


def test_hold_time_and_resolved():
    db = Database(":memory:")
    db.add_user_alert(None, "load_power", ">", 3000, 120, "info", True)
    n = Notifier({})
    am = AlertManager(DEFAULTS["alerts"], db, n)
    am.process(snap(80, load=3500), now=1000)
    am.process(snap(80, load=3500), now=1060)
    assert n.sent == []                                                         # sub 2 minute: încă nimic
    am.process(snap(80, load=3500), now=1130)
    assert "Consumul casei peste 3000 W" in n.sent[-1]["message"]
    am.process(snap(80, load=800), now=1400)
    assert n.sent[-1]["message"].startswith("✅ Rezolvat")

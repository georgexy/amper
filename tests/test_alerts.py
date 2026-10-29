"""Teste pentru alerte, cu lumea simulată — în special scenariul: un modul Daly încetează să se încarce."""
import time

from amper.config import DEFAULTS, _merge
from amper.db import Database
from amper.engine import Engine
from amper.notify import Notifier


def make_engine(fault_after=None):
    from amper.drivers.simulator import SimWorld
    SimWorld._instance = None
    devs = [
        {"id": "daly1", "name": "Daly 1", "type": "sim_daly", "capacity_ah": 100, "start_soc": 60},
        {"id": "daly2", "name": "Daly 2", "type": "sim_daly", "capacity_ah": 100, "start_soc": 60,
         **({"fault_after_s": fault_after} if fault_after is not None else {})},
        {"id": "daly3", "name": "Daly 3", "type": "sim_daly", "capacity_ah": 100, "start_soc": 60},
        {"id": "inv", "name": "Growatt", "type": "sim_growatt"},
    ]
    cfg = _merge(DEFAULTS, {"database": ":memory:", "devices": devs})
    return Engine(cfg, Database(":memory:"), Notifier({}))


def run_sim(eng, start, seconds, step=10):
    """Rulează simularea 'în timp accelerat', ca motorul real (citire → stare → alerte)."""
    world = next(iter(eng.devices.values())).driver.world
    world.t0 = world.t = start
    t = start
    while t < start + seconds:
        t += step
        world.step(t)
        for st in eng.devices.values():
            r = world.pack_reading(st.cfg["id"]) if st.driver.kind == "pack" else world.inverter_reading(st.cfg["id"])
            st.reading, st.last_ok, st.connected = r, t, True
        eng.started = start
        eng.alerts.process(eng.snapshot(now=t), now=t)
    return t


def noon(day_offset=0):
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, 11, 0, 0, 0, 0, -1))


def test_pack_stops_charging_triggers_alert():
    eng = make_engine(fault_after=600)
    run_sim(eng, noon(), 3600)
    rules = {a["rule"]: a for a in eng.alerts.active_list()}
    assert "charge_mos_off" in rules and rules["charge_mos_off"]["device"] == "daly2"
    assert "not_charging" in rules and rules["not_charging"]["device"] == "daly2"
    msgs = " ".join(n["message"] for n in eng.notifier.sent)
    assert "Daly 2 NU se încarcă" in msgs
    # ceilalți nu au alerte de încărcare
    assert all(a["device"] == "daly2" for a in eng.alerts.active_list() if a["rule"] in ("not_charging", "charge_mos_off"))


def test_healthy_system_no_charging_alerts():
    eng = make_engine()
    run_sim(eng, noon(), 3600)
    assert not [a for a in eng.alerts.active_list() if a["rule"] in ("not_charging", "charge_mos_off", "comm_lost")]


def test_comm_lost_and_recovery():
    eng = make_engine()
    t = run_sim(eng, noon(), 60)
    st = eng.devices["daly3"]
    # 5 minute fără citiri de la daly3
    for k in range(30):
        t += 10
        for s in eng.devices.values():
            if s is not st:
                s.last_ok = t
        eng.alerts.process(eng.snapshot(now=t), now=t)
    assert any(a["rule"] == "comm_lost" and a["device"] == "daly3" for a in eng.alerts.active_list())
    for k in range(20):          # revine comunicația; alerta se închide după histerezis
        t += 10
        for s in eng.devices.values():
            s.last_ok = t
        eng.alerts.process(eng.snapshot(now=t), now=t)
    assert not any(a["rule"] == "comm_lost" for a in eng.alerts.active_list())
    assert any(n["message"].startswith("✅ Rezolvat") for n in eng.notifier.sent)


def test_never_connected_device_is_hidden_and_silent():
    eng = make_engine()
    t = run_sim(eng, noon(), 60)
    st = eng.devices["daly3"]
    st.connected, st.last_ok, st.reading = False, None, None     # cablul n-a fost legat niciodată
    for k in range(30):
        t += 10
        for s in eng.devices.values():
            if s is not st:
                s.last_ok = t
        snap = eng.snapshot(now=t)
        eng.alerts.process(snap, now=t)
    assert snap["devices"]["daly3"]["connected"] is False
    assert not any(a["device"] == "daly3" for a in eng.alerts.active_list())


def test_clock_jump_at_boot_is_not_comm_lost(monkeypatch):
    eng = make_engine()
    now = time.time()
    eng.started = now - 60
    for st in eng.devices.values():
        st.reading = None
        st.last_ok, st.connected = now - 3, True
    eng.alerts.process(eng.snapshot(now=now), now=now)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 240)   # vine ora corectă: ceasul sare 4 min înainte
    assert 235 < eng.check_clock() < 245
    t = time.time()
    eng.alerts.process(eng.snapshot(now=t), now=t)
    assert not eng.alerts.active, eng.alerts.active
    assert eng.check_clock() == 0.0


def test_connected_from_history():
    db = Database(":memory:")
    from amper.models import PackReading
    db.insert_pack(PackReading(device_id="daly1", ts=time.time(), soc=50))
    assert db.seen_devices(int(time.time()) - 3600) == {"daly1"}
    assert db.seen_devices(int(time.time()) + 10) == set()


def test_store_and_history():
    eng = make_engine()
    world = next(iter(eng.devices.values())).driver.world
    start = noon(-1)
    world.t0 = world.t = start
    for i in range(360):
        t = start + i * 10
        world.step(t)
        for st in eng.devices.values():
            st.reading = world.pack_reading(st.cfg["id"]) if st.driver.kind == "pack" else world.inverter_reading(st.cfg["id"])
            st.last_ok = t
            if st.driver.kind == "inverter":
                eng._integrate_energy(st.reading, now=t)
        eng.store_once(now=t)
    h = eng.db.history("pack", "daly1", int(start), int(start + 3600), points=60)
    assert 50 <= len(h) <= 70 and h[0]["soc"] is not None
    e = eng.db.energy("inv")
    assert e and e[0]["pv_kwh"] > 0.5
    eng.db.rollup(14, now=start + 3600 + 600)
    n = eng.db.conn.execute("SELECT COUNT(*) FROM pack_5m WHERE device='daly1'").fetchone()[0]
    assert n == 12


def test_open_alerts_survive_restart_without_new_notification(tmp_path):
    from amper.config import DEFAULTS, _merge
    from amper.drivers.simulator import SimWorld
    path = str(tmp_path / "a.db")

    def engine():
        SimWorld._instance = None
        cfg = _merge(DEFAULTS, {"database": path, "devices": [{"id": "d1", "name": "Daly 1", "type": "sim_daly"}]})
        return Engine(cfg, Database(path), Notifier({}))
    e1 = engine()
    t = time.time()
    e1.started = t - 300                                   # dispozitivul (știut) n-a răspuns de 5 minute
    e1.devices["d1"].connected = True
    e1.alerts.process(e1.snapshot(now=t), now=t)
    assert [a["rule"] for a in e1.alerts.active_list()] == ["comm_lost"]
    assert len(e1.notifier.sent) == 1
    e2 = engine()                                          # repornire: alerta rămâne, fără notificare nouă
    e2.devices["d1"].connected = True
    assert [a["rule"] for a in e2.alerts.active_list()] == ["comm_lost"]
    t2 = e2.started + 150
    e2.alerts.process(e2.snapshot(now=t2), now=t2)
    assert e2.notifier.sent == [] and len(e2.alerts.active_list()) == 1
    assert len(e2.db.open_alerts()) == 1


def test_device_silent_5_days_disconnects_itself(tmp_path):
    """Un dispozitiv fără răspuns 5 zile (`connected_days`) se deconectează singur: alertele lui se închid în
    tăcere, nu se mai afișează și nu mai dă `comm_lost`; dacă răspunde din nou, revine (cerut 2026-10-28)."""
    from amper.config import DEFAULTS, _merge
    from amper.drivers.simulator import SimWorld
    from amper.models import PackReading
    path = str(tmp_path / "c.db")
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": path, "devices": [{"id": "s1", "name": "Seplos 1", "type": "sim_seplos"},
                                                          {"id": "s2", "name": "Seplos 2", "type": "sim_seplos"}]})
    db = Database(path)
    now = time.time()
    db.insert_pack(PackReading(device_id="s1", ts=now, soc=50), ts=int(now - 4 * 86400))   # acum 4 zile
    db.insert_pack(PackReading(device_id="s2", ts=now, soc=50), ts=int(now - 6 * 86400))   # acum 6 zile
    eng = Engine(cfg, db, Notifier({}))
    assert eng.devices["s1"].connected and not eng.devices["s2"].connected
    eng.started = now - 300                                                   # pornit de 5 min, fără răspuns
    eng.alerts.process(eng.snapshot(now=now), now=now)
    assert [a["device"] for a in eng.alerts.active_list()] == ["s1"]          # comm_lost doar la cel conectat
    eng.notifier.sent.clear()
    assert eng.expire_devices(now=now) == []
    later = now + 1.5 * 86400                                                 # s1: 5,5 zile fără răspuns
    assert eng.expire_devices(now=later) == ["s1"]
    assert not eng.devices["s1"].connected and eng.alerts.active_list() == []
    assert db.open_alerts() == []                                             # închisă în DB
    assert len(eng.notifier.sent) == 1 and "deconectat automat" in eng.notifier.sent[0]["message"]
    eng.alerts.process(eng.snapshot(now=later), now=later)
    assert eng.alerts.active_list() == []                                     # nu mai dă comm_lost
    assert eng.expire_devices(now=later + 86400) == []                        # o singură dată


def test_clock_jump_does_not_disconnect_a_live_device():
    eng = make_engine()
    st = eng.devices["daly1"]
    st.connected, st.last_ok = True, time.time() - 10
    st.seen_at = st.last_ok
    eng._clock_offset -= 7 * 86400                                            # ceasul sare 7 zile înainte
    eng.check_clock()
    assert eng.expire_devices() == [] and st.connected


def lose(eng, t, ids, steps=30):
    """`steps` × 10 s fără citiri de la `ids`; celelalte răspund."""
    for _ in range(steps):
        t += 10
        for s in eng.devices.values():
            if s.cfg["id"] not in ids:
                s.last_ok = t
        eng.alerts.process(eng.snapshot(now=t), now=t)
    return t


def test_unplugged_adapter_gives_one_alert_per_port():
    eng = make_engine()
    t = run_sim(eng, noon(), 60)
    for d in ("daly1", "daly2"):
        eng.devices[d].cfg["port"] = "/dev/null"                       # portul există: alerte pe dispozitiv
    t = lose(eng, t, {"daly1", "daly2", "daly3"})
    assert {a["key"] for a in eng.alerts.active_list()} >= {"comm:daly1", "comm:daly2", "comm:daly3"}
    for d in ("daly1", "daly2"):
        eng.devices[d].cfg["port"] = "/dev/amper-test-lipsa"            # adaptorul scos
    sent = len(eng.notifier.sent)
    t = lose(eng, t, {"daly1", "daly2", "daly3"})
    comm = {a["key"]: a for a in eng.alerts.active_list() if a["rule"] == "comm_lost"}
    assert set(comm) == {"comm:port:/dev/amper-test-lipsa", "comm:daly3"}    # una pe adaptor + daly3 (fără port)
    assert "Adaptorul amper-test-lipsa e deconectat" in comm["comm:port:/dev/amper-test-lipsa"]["message"]
    assert "Daly 1, Daly 2" in comm["comm:port:/dev/amper-test-lipsa"]["message"]
    new = [n["message"] for n in eng.notifier.sent[sent:]]
    assert not any("Rezolvat" in m for m in new) and sum("Adaptorul" in m for m in new) == 1
    # „Deconectează” pe fiecare: alerta pe adaptor rămâne cât mai are dispozitive, apoi se închide în tăcere
    eng.alerts.drop_device("daly1", t)
    assert "comm:port:/dev/amper-test-lipsa" in {a["key"] for a in eng.alerts.active_list()}
    eng.alerts.drop_device("daly2", t)
    assert "comm:port:/dev/amper-test-lipsa" not in {a["key"] for a in eng.alerts.active_list()}


def test_protection_says_if_mosfet_really_cut():
    from amper.alerts import mos_note
    assert "oprit descărcarea" in mos_note({"charge_mos": True, "discharge_mos": False})
    assert "doar semnal" in mos_note({"charge_mos": True, "discharge_mos": True})
    assert mos_note({}) == ""

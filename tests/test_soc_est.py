"""SOC-ul calculat de Amper (soc_est.py) și testul de capacitate (capacity.test_*), cu cazul real din 22.11.2026:
un Daly a coborât de la 99 % la 0 % în câteva ore cu curentul 0 A, deși celulele rămăseseră la 3,30 V."""
import json

import pytest

from amper import capacity
from amper.config import DEFAULTS, _merge
from amper.db import Database
from amper.engine import Engine
from amper.models import PackReading
from amper.notify import Notifier
from amper.soc_est import SocEstimator

T0 = 1_795_300_000
CAP = 105.0


def rd(t, soc, cur, cell, n=16, spread=0.004):
    cells = [round(cell + spread * ((i % 3) - 1), 3) for i in range(n)]
    return PackReading(device_id="d", ts=t, voltage=round(sum(cells), 2), current=cur, soc=soc, cells=cells)


def test_full_anchor_and_counting():
    est = SocEstimator()
    t = T0
    est.observe("d", rd(t, 99.8, 1.0, 3.43, spread=0.03), CAP, now=t)        # celula max 3,46 V → plin
    assert est.soc("d", CAP) == 100 and est.st["d"]["anchored"]
    for _ in range(360):                                                       # 2 h la −10,5 A = 21 Ah
        t += 20
        est.observe("d", rd(t, 80, -10.5, 3.32), CAP, now=t)
    assert est.soc("d", CAP) == pytest.approx(80, abs=0.5)
    assert est.info("d", CAP, 80)["ah_out"] == pytest.approx(21, abs=0.2)


def test_bms_soc_falling_without_current_is_flagged_and_ignored():
    """Ca daly3 pe 22.11: SOC-ul BMS-ului scade 99 → 0 cu 0 A; al lui Amper rămâne pe loc."""
    est = SocEstimator()
    t = T0
    est.observe("d", rd(t, 99.8, 0.5, 3.43, spread=0.03), CAP, now=t)
    soc_bms = 99.8
    for i in range(int(3 * 3600 / 20)):
        t += 20
        soc_bms = max(0.0, soc_bms - 0.12)                                   # ~22 %/h, fără curent
        est.observe("d", rd(t, soc_bms, 0.0, 3.34), CAP, now=t)
    info = est.info("d", CAP, soc_bms)
    assert info["soc"] == 100.0 and info["differs"]
    assert info["mismatch"] and "curentul măsurat" in info["mismatch"]


def test_zero_percent_at_3_30_volts_is_impossible():
    est = SocEstimator()
    t = T0
    est.observe("d", rd(t, 55, -2.0, 3.30), CAP, now=t)              # pornește de la BMS (55 %)
    est.st["d"]["ah_out"] = CAP * 0.45
    for _ in range(5):
        t += 20
        est.observe("d", rd(t, 0.0, -1.0, 3.30), CAP, now=t)
    info = est.info("d", CAP, 0.0)
    assert info["mismatch"] and "nu e gol" in info["mismatch"] and info["soc"] > 50


def test_data_gap_is_not_counted_and_low_voltage_corrects():
    est = SocEstimator()
    t = T0
    est.observe("d", rd(t, 99.8, 1.0, 3.43, spread=0.03), CAP, now=t)
    t += 3600                                                                  # o oră fără date
    est.observe("d", rd(t, 90, -20.0, 3.33), CAP, now=t)
    assert est.soc("d", CAP) == 100 and est.info("d", CAP, 90)["gap"]
    t += 20
    est.observe("d", rd(t, 40, -2.0, 3.15), CAP, now=t)                      # repaus la 3,15 V → ~7,5 %
    assert est.soc("d", CAP) == pytest.approx(capacity.ocv_soc(3.15), abs=1)


def test_state_survives_restart(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    est = SocEstimator(db)
    est.observe("d", rd(T0, 99.8, 1.0, 3.43, spread=0.03), CAP, now=T0)
    est.st["d"]["ah_out"] = 30.0
    est.save(force=True)
    again = SocEstimator(db)
    assert again.soc("d", CAP) == pytest.approx(100 - 30 / CAP * 100, abs=0.1) and again.st["d"]["anchored"]


def test_learned_capacity_is_used():
    est = SocEstimator()
    est.set_caps({"d": {"cap": 84.0, "kind": "measured"}})
    est.observe("d", rd(T0, 99.8, 1.0, 3.43, spread=0.03), CAP, now=T0)
    est.st["d"]["ah_out"] = 42.0
    assert est.soc("d", CAP) == 50.0 and est.info("d", CAP, 60)["cap_kind"] == "measured"


def make_engine():
    from amper.drivers.simulator import SimWorld
    SimWorld._instance = None
    devs = [{"id": f"daly{i}", "name": f"Daly {i}", "type": "sim_daly", "capacity_ah": 100, "start_soc": 60}
            for i in (1, 2, 3)]
    cfg = _merge(DEFAULTS, {"database": ":memory:", "devices": devs})
    return Engine(cfg, Database(":memory:"), Notifier({}))


def test_snapshot_switches_between_amper_and_bms_soc():
    eng = make_engine()
    for k, st in eng.devices.items():
        st.reading = PackReading(device_id=k, ts=T0, voltage=53.0, current=-2.0, soc=0.0 if k == "daly3" else 55.0,
                                 remaining_ah=0, capacity_ah=100, cells=[3.31] * 16)
        st.last_ok = st.seen_at = T0
        st.connected = True
        eng.soc.st[k] = dict(ah_out=45.0, anchored=True, full_ts=T0 - 3600, last_ts=T0, hist=[], mismatch=None)
    snap = eng.snapshot(now=T0 + 1)
    d3 = snap["devices"]["daly3"]
    assert snap["soc_source"] == "amper" and d3["reading"]["soc"] == 55.0 and d3["soc_bms"] == 0.0
    assert snap["bank"]["soc"] == pytest.approx(55, abs=0.1)
    eng.set_soc_source("bms")
    snap = eng.snapshot(now=T0 + 1)
    assert snap["devices"]["daly3"]["reading"]["soc"] == 0.0 and snap["bank"]["soc"] < 40
    assert eng.db.get_meta("soc_source") == "bms"


def test_capacity_test_runs_to_the_threshold(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    est = SocEstimator(db)
    packs = {"a": dict(name="A", rated=CAP, cells=16), "b": dict(name="B", rated=CAP, cells=16)}
    t = T0
    for d in packs:
        est.observe(d, rd(t, 100, 1.0, 3.43, spread=0.03), CAP, now=t)
    with pytest.raises(ValueError, match="Prag"):
        capacity.test_start(db, est, packs, 2.5, now=t)
    st = capacity.test_start(db, est, packs, 3.0, now=t)
    assert st["state"] == "running"
    with pytest.raises(ValueError, match="deja"):
        capacity.test_start(db, est, packs, 3.0, now=t)
    sent = []

    class N:
        def send(self, msg, **k):
            sent.append(msg)
    # A dă 95 Ah, B 80 Ah până la prag (curenți diferiți, aceeași tensiune: sunt în paralel)
    for i in range(int(10 * 3600 / 30)):
        t += 30
        cell = 3.30 if i < 1150 else 2.99
        reads = {"a": rd(t, 50, -9.5, cell), "b": rd(t, 50, -8.0, cell)}
        for d, r in reads.items():
            est.observe(d, r, CAP, now=t)
        st = capacity.test_tick(db, est, packs, reads, N(), now=t)
        if st["state"] != "running":
            break
    assert st["state"] == "done" and sent and "Testul de capacitate s-a terminat" in sent[0]
    res = st["result"]
    assert res["a"]["cap"] > res["b"]["cap"] and 85 <= res["a"]["cap"] <= 105
    assert {e["method"] for e in db.capacity_events()} == {"test"}
    assert json.loads(db.get_meta(capacity.TEST_META))["state"] == "done"


def test_capacity_test_needs_a_full_bank_and_cancels_on_recharge(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    est = SocEstimator(db)
    packs = {"a": dict(name="A", rated=CAP, cells=16)}
    est.observe("a", rd(T0, 60, -2.0, 3.30), CAP, now=T0)
    with pytest.raises(ValueError, match="plină"):
        capacity.test_start(db, est, packs, 3.0, now=T0)
    est.observe("a", rd(T0 + 10, 100, 1.0, 3.43, spread=0.03), CAP, now=T0 + 10)
    capacity.test_start(db, est, packs, 3.0, now=T0 + 20)
    est.observe("a", rd(T0 + 7200, 100, 1.0, 3.43, spread=0.03), CAP, now=T0 + 7200)   # s-a încărcat din nou
    st = capacity.test_tick(db, est, packs, {"a": rd(T0 + 7200, 100, 1.0, 3.43)}, None, now=T0 + 7200)
    assert st["state"] == "cancelled" and "încărcat complet" in st["reason"]


def test_relative_capacity_from_nights(tmp_path, monkeypatch):
    """În paralel, modulele dau Ah proporțional cu capacitatea: B dă constant 80 % din A → B are ~80 % din A;
    cu A măsurat (95 Ah), B primește ~76 Ah, iar C (care vede 0 A prea des) nu intră în comparație."""
    from amper import battery_report, night_report as nr
    db = Database(str(tmp_path / "a.db"))
    packs = {"a": dict(name="A", rated=CAP), "b": dict(name="B", rated=CAP)}
    nights = [dict(start=T0 + i * 86400, end=T0 + i * 86400 + 8 * 3600, ongoing=False) for i in range(5)]
    monkeypatch.setattr(battery_report, "nights_in", lambda db_, s, e: nights)

    def rows(db_, names, s, e):
        out = {}
        for d, amps in (("a", -5.0), ("b", -4.0)):
            out[d] = [dict(ts=s + k * 60, current=amps, soc=50, remaining_ah=50, cell_delta=0.01, cell_min=3.3,
                           cell_max=3.31, voltage=53, charge_mos=1, discharge_mos=1) for k in range(480)]
        return out
    monkeypatch.setattr(nr, "night_rows", rows)
    rel = capacity.relative(db, packs, now=T0 + 6 * 86400)
    assert rel["a"]["rel"] == pytest.approx(1.111, abs=0.01) and rel["b"]["rel"] == pytest.approx(0.889, abs=0.01)
    db.save_capacity(dict(device="a", t_full=T0, ts=T0 + 100, ah=90.0, soc_low=5.0, cap=95.0, method="test"))
    learned = capacity.learned(db, packs, now=T0 + 6 * 86400)
    assert learned["a"]["kind"] == "measured" and learned["b"]["kind"] == "relative"
    assert learned["b"]["cap"] == pytest.approx(95 * 0.8, abs=0.5)

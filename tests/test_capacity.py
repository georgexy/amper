"""Capacitatea reală (capacity.py): descărcare de la plin până aproape de gol → Ah."""
import time

import pytest

from amper import capacity
from amper.db import Database

RATED = 105.0


def series(true_cap=95.0, start=1_700_000_000, load_a=10.0, end_cell=3.18, rest_end=True, step=300):
    """Plin (3,45 V), apoi descărcare cu `load_a` până la `end_cell` pe celulă (curba simplificată), apoi repaus."""
    rows, t, ah = [], start, 0.0
    rows.append(dict(ts=t, dt=step, current=1.0, soc=100, voltage=55.2, cell_min=3.43, cell_max=3.46))
    while True:
        t += step
        ah += load_a * step / 3600
        left = 1 - ah / true_cap
        cell = 3.30 if left > 0.2 else 3.21 + (left - 0.125) * 1.2 if left > 0.125 else 2.9 + left * 2.48
        rows.append(dict(ts=t, dt=step, current=-load_a, soc=max(0, 100 - ah / RATED * 100), voltage=cell * 16,
                         cell_min=cell - 0.01, cell_max=cell + 0.01))
        if cell <= end_cell:
            break
    if rest_end:
        for _ in range(4):
            t += step
            rows.append(dict(ts=t, dt=step, current=-1.0, soc=rows[-1]["soc"], voltage=cell * 16,
                             cell_min=cell - 0.01, cell_max=cell + 0.01))
    return rows, ah


def test_rest_at_the_bottom_gives_the_capacity():
    rows, ah = series(true_cap=95.0, end_cell=3.17)
    ev = capacity.scan_device(rows, RATED, 16)
    assert len(ev) == 1
    e = ev[0]
    assert e["method"] == "rest" and 5 < e["soc_low"] < 12.5
    assert e["cap"] == pytest.approx(95.0, rel=0.05)
    assert e["ah"] < e["cap"]


def test_shallow_discharge_is_not_a_measurement():
    rows, _ = series(end_cell=3.29, rest_end=True)          # se oprește în zona plată
    assert capacity.scan_device(rows, RATED, 16) == []


def test_knee_under_load():
    rows, _ = series(true_cap=100.0, end_cell=2.94, rest_end=False)
    ev = capacity.scan_device(rows, RATED, 16)
    assert ev and ev[0]["method"] in ("knee", "rest") and ev[0]["cap"] == pytest.approx(100, rel=0.06)


def test_gap_in_data_breaks_the_count():
    rows, _ = series(end_cell=3.17)
    for r in rows[len(rows) // 2:]:
        r["ts"] += 3600                                       # o oră fără date la jumătatea descărcării
    assert capacity.scan_device(rows, RATED, 16) == []


def test_bms_that_misses_small_currents_is_rejected():
    rows, _ = series(end_cell=3.17)
    for r in rows[1:len(rows) // 3]:
        r["current"] = 0.0
    assert capacity.scan_device(rows, RATED, 16) == []


def test_scan_saves_and_analyze_reports(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    now = time.time()
    t0 = int(now - 5 * 86400) // 300 * 300
    for cap in (96.0, 94.0):
        rows, _ = series(true_cap=cap, start=t0, end_cell=3.17)
        for r in rows:
            db.conn.execute("INSERT INTO pack_5m (ts, device, current, soc, voltage, cell_min, cell_max) "
                            "VALUES (:ts, 'p1', :current, :soc, :voltage, :cell_min, :cell_max)", r)
        t0 = rows[-1]["ts"] + 86400 // 300 * 300
    packs = {"p1": dict(name="Daly 1", rated=RATED, cells=16), "p2": dict(name="Daly 2", rated=RATED, cells=16)}
    assert capacity.scan(db, packs, now) == 2
    assert capacity.scan(db, packs, now + 3600) == 0 or len(db.capacity_events()) == 2      # fără dubluri
    res = capacity.analyze(db, packs, now)
    m1, m2 = res["modules"]
    assert m1["count"] == 2 and m1["cap"] == pytest.approx(95, rel=0.05) and 85 <= m1["pct"] <= 95
    assert m1["months"] and m2["count"] == 0 and "cap" not in m2
    prog = capacity.progress(db, packs, now)
    assert prog["p1"]["last_full"] and prog["p1"]["low"]["cell"] < 3.2 and prog["p2"]["last_full"] is None

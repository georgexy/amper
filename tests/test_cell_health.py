"""Sănătatea celulelor: momentele salvate (final de încărcare, descărcare adâncă, salturi de curent), analiza pe
celule (abatere, de câte ori e la capăt, rezistență, scor, evoluție) și notificarea săptămânală."""
import json
import random
import statistics
import time

from amper import cell_health as ch
from amper.db import Database
from amper.models import PackReading
from amper.notify import Notifier

DAY = 86400
T0 = time.mktime((2026, 9, 7, 9, 30, 0, 0, 0, -1))           # o zi de luni


def reading(ts, cells, current, **raw):
    return PackReading(device_id="d", ts=ts, cells=cells, current=current, raw=raw)


def cycle(tr, dev, t, weak=None, grow=1.0, base_r=0.9):
    """O zi: încărcare până la capăt, descărcare adâncă și un salt de curent. Celula `weak` (index) are capacitate
    mai mică: o ia înainte la încărcare și rămâne în urmă la descărcare, cu o abatere care crește cu `grow`."""
    n = 16
    for k in range(3):                                    # final de încărcare (3 citiri, un singur episod)
        cells = [3.40 + 0.002 * k] * n
        if weak is not None:
            cells[weak] += 0.020 * grow
        tr.observe(dev, reading(t + k * 60, cells, 20.0), now=t + k * 60)
    t2 = t + 8 * 3600
    for k in range(3):                                    # descărcare adâncă
        cells = [3.08 - 0.003 * k] * n
        if weak is not None:
            cells[weak] -= 0.040 * grow
        tr.observe(dev, reading(t2 + k * 60, cells, -30.0), now=t2 + k * 60)
    t3 = t + 4 * 3600                                     # consumul sare între −5 A și −45 A timp de un minut
    for k in range(10):
        i = -5.0 if k % 3 == 0 else -45.0 if k % 3 == 1 else -25.0
        cells = [3.33 + i * base_r / 1000] * n
        if weak is not None:
            cells[weak] = 3.33 + i * base_r * 1.45 / 1000
        tr.observe(dev, reading(t3 + 6 * k, cells, i), now=t3 + 6 * k)


def fill(db, weeks=8, weak=6):
    tr = ch.Tracker(db)
    for d in range(weeks * 7):
        t = T0 + d * DAY
        cycle(tr, "daly2", t, weak=weak, grow=0.4 + 0.6 * d / (weeks * 7))
        cycle(tr, "daly1", t)
    return T0 + weeks * 7 * DAY


def test_episode_keeps_only_the_most_extreme_moment():
    db = Database(":memory:")
    tr = ch.Tracker(db)
    cycle(tr, "daly1", T0, weak=3)
    rows = db.cell_events(0)
    assert [r["kind"] for r in rows].count("chg") == 1 and [r["kind"] for r in rows].count("dis") == 1
    assert [r["kind"] for r in rows].count("ir") == 1
    # celule lipsă sau vechi (Daly prin Bluetooth): nu se folosesc
    tr.observe("daly1", reading(T0 + 9 * DAY, [3.45] * 10, 20.0, cells_age=30), now=T0 + 9 * DAY)
    assert len(db.cell_events(0)) == 3


def test_weak_cell_found_ranked_and_described():
    db = Database(":memory:")
    now = fill(db)
    res = ch.analyze(db, {"daly1": "Daly 1", "daly2": "Daly 2"}, now)
    assert res["enough"] and res["events"]["dis"] >= 20
    worst = res["cells"][0]
    assert (worst["device"], worst["cell"]) == ("daly2", 7)
    assert worst["level"] == 2 and worst["score"] < 70
    assert worst["dis_low"][0] == worst["dis_low"][1]                 # mereu cea mai jos
    assert worst["ir_rel"] >= 40
    assert worst["text"].startswith("Daly 2 · celula 7 rămâne în urmă: cea mai jos la")
    assert "rezistența internă e cu" in worst["text"] and "a crescut" in worst["text"]
    assert len(res["cells"]) == 1 and res["ok_count"] == 31            # celelalte 31 în regulă


def test_weekly_notifies_only_when_needed():
    db = Database(":memory:")
    now = fill(db)
    n = Notifier({})
    assert ch.weekly_due(db, now)
    ch.weekly(db, n, {"daly2": "Daly 2"}, now)
    msgs = [m["message"] for m in n.sent]
    assert len(msgs) == 1 and msgs[0].startswith("🔴 Sănătatea celulelor: Daly 2 · celula 7 rămâne în urmă")
    assert n.sent[0]["url"] == "/?v=cells"                           # atinsă: deschide ecranul celulelor
    assert not ch.weekly_due(db, now + 3600)                          # o dată pe săptămână
    healthy = Database(":memory:")
    tr = ch.Tracker(healthy)
    for d in range(28):
        cycle(tr, "daly1", T0 + d * DAY)
    n2 = Notifier({})
    ch.weekly(healthy, n2, {}, T0 + 28 * DAY)
    assert n2.sent == []                                              # totul în regulă: nicio notificare


def test_not_enough_data():
    db = Database(":memory:")
    tr = ch.Tracker(db)
    cycle(tr, "daly1", T0, weak=2)
    res = ch.analyze(db, {}, T0 + DAY)
    assert not res["enough"] and res["cells"] == []


# ---------------- acuratețe (1.0.0-beta.16, cerut 2026-11-01) ----------------

def test_resistance_by_regression_over_many_readings():
    """Rezistența = panta tensiune–curent din toată fereastra, nu din două citiri: zgomotul de ±2 mV se mediază."""
    db = Database(":memory:")
    tr = ch.Tracker(db)
    rnd = random.Random(1)
    for k in range(30):
        i = rnd.uniform(-45, -5)
        cells = [3.30 + i * 0.9 / 1000 + rnd.uniform(-0.002, 0.002) for _ in range(16)]
        cells[4] = 3.30 + i * 1.8 / 1000 + rnd.uniform(-0.002, 0.002)
        tr.observe("d", reading(T0 + 4 * k, cells, i), now=T0 + 4 * k)
    ir = [r for r in db.cell_events(0) if r["kind"] == "ir"]
    assert len(ir) == 1
    v = json.loads(ir[0]["vals"])
    assert abs(v[4] / statistics.median(v) - 2.0) < 0.25          # dublă, cu tot zgomotul
    assert json.loads(ir[0]["meta"])["r2"] >= ch.IR_MIN_R2


def test_no_resistance_without_enough_current_change():
    db = Database(":memory:")
    tr = ch.Tracker(db)
    for k in range(30):
        i = -20.0 - (k % 2) * 6                                    # variază doar 6 A
        tr.observe("d", reading(T0 + 4 * k, [3.30 + i * 0.9 / 1000] * 16, i), now=T0 + 4 * k)
    assert not db.cell_events(0)


def test_episode_averages_the_most_extreme_readings():
    db = Database(":memory:")
    tr = ch.Tracker(db)
    for k in range(20):                                           # o citire „ciudată”, restul cu celula 3 +20 mV
        cells = [3.40 + 0.005 * k] * 16
        cells[3] += 0.080 if k == 19 else 0.020
        tr.observe("d", reading(T0 + 30 * k, cells, 20.0), now=T0 + 30 * k)
    rows = db.cell_events(0)
    assert len(rows) == 1 and json.loads(rows[0]["meta"])["n"] == ch.EPISODE_TOP
    dev3 = json.loads(rows[0]["vals"])[3]
    assert 25 < dev3 < 40                                         # media celor 5: nu 75 mV din citirea ciudată


def test_balancing_readings_are_avoided_when_possible():
    db = Database(":memory:")
    tr = ch.Tracker(db)
    for k in range(12):
        bal = k % 2 == 0
        cells = [3.40 + 0.003 * k] * 16
        cells[2] += 0.005 if bal else 0.025                       # echilibrarea ascunde abaterea
        r = reading(T0 + 30 * k, cells, 20.0)
        r.balancing = bal
        tr.observe("d", r, now=T0 + 30 * k)
    row = db.cell_events(0)[0]
    assert json.loads(row["meta"])["bal"] == 0 and json.loads(row["vals"])[2] > 20


def test_noisy_cell_gets_low_confidence_and_no_notification():
    db = Database(":memory:")
    rnd = random.Random(3)
    now = T0 + 10 * DAY
    for d in range(4):                                            # 4 descărcări; celula 5 sare de la −60 la +40 mV
        vals = [0.0] * 16
        vals[5] = [-60, 40, -55, 35][d]
        db.add_cell_event(int(now - (d + 1) * DAY), "d", "dis", json.dumps(vals))
    res = ch.analyze(db, {"d": "Daly 1"}, now)
    c5 = next((c for c in res["cells"] if c["cell"] == 6), None)
    assert c5 is None or c5["confidence"] == "low"
    db2 = Database(":memory:")
    fill(db2)
    worst = ch.analyze(db2, {"daly1": "Daly 1", "daly2": "Daly 2"}, T0 + 56 * DAY)["cells"][0]
    assert worst["confidence"] == "high"

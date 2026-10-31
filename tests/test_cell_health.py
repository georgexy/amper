"""Sănătatea celulelor: momentele salvate (final de încărcare, descărcare adâncă, salturi de curent), analiza pe
celule (abatere, de câte ori e la capăt, rezistență, scor, evoluție) și notificarea săptămânală."""
import json
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
    t3 = t + 4 * 3600                                     # salt de curent −5 A → −45 A în 2 s
    before = [3.30] * n
    after = [v - 40 * base_r / 1000 for v in before]
    if weak is not None:
        after[weak] = before[weak] - 40 * base_r * 1.45 / 1000
    tr.observe(dev, reading(t3, before, -5.0), now=t3)
    tr.observe(dev, reading(t3 + 2, after, -45.0), now=t3 + 2)


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
    assert [(m["name"], m["cells"], m["ready"]) for m in res["modules"]] == [("Daly 1", 16, True), ("Daly 2", 16, True)]


def test_module_without_enough_moments_is_listed_apart():
    """Un modul cu doar 2 salturi de curent nu intră în analiză și nu e numărat „în regulă” (cerut 2026-10-31)."""
    db = Database(":memory:")
    now = fill(db)
    for k in range(2):
        db.add_cell_event(int(now - (k + 1) * 3600), "daly3", "ir", json.dumps([1.0] * 16))
    res = ch.analyze(db, {"daly1": "Daly 1", "daly2": "Daly 2", "daly3": "Daly 3"}, now)
    d3 = res["modules"][-1]
    assert (d3["name"], d3["ir"], d3["ready"]) == ("Daly 3", 2, False)
    assert res["ok_count"] == 31 and all(c["device"] != "daly3" for c in res["cells"])


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

import time

from amper import battery_report
from amper.db import Database

from test_night_report import DAY0, NAMES, fill


def report(db, days=3, **kw):
    db.rollup(14, now=DAY0 + 86400 + 4 * 3600)
    end = DAY0 + 86400 - 2.5 * 3600                     # 09:30, după ultima noapte
    return battery_report.build(db, NAMES, end - days * 86400, end, ["inv"], now=end, **kw)


def test_period_report_table_and_counts():
    db = Database(":memory:")
    fill(db, d1_amps=1.4, nights=3)
    rep = report(db)
    assert rep["ok"] and rep["headline"].startswith("În aceste 3 zile nu au fost probleme")
    assert [r["name"] for r in rep["rows"]] == ["Daly 1", "Daly 2", "Daly 3"]
    r1 = rep["rows"][0]
    assert r1["dis_ah"] > 30 and r1["chg_ah"] > 0 and r1["cycles"] > 0.3 and r1["dmax"] == 20
    assert rep["nights"] >= 2
    text = " ".join(rep["points"])
    assert "Toate modulele au numărat corect" in text and "aproape egal" in text
    assert "Banca nu s-a încărcat complet în aceste 3 zile" in text and "cel mai jos a coborât" in text


def test_period_report_counts_bad_nights():
    db = Database(":memory:")
    fill(db, d3_zero=0.5, nights=3)
    db.open_alert(int(DAY0 - 6 * 3600), "daly1", "bms_protection", "critical", "Daly 1: celulă mică (nivel 2)")
    rep = report(db)
    assert not rep["ok"] and "Daly 1: celulă mică (nivel 2) — 1 dată" in rep["headline"]
    text = " ".join(rep["points"])
    assert "Daly 3 n-a văzut curenții mici în" in text
    assert "Daly 1 a dat în medie ~46 % mai mult curent" in text


def test_period_without_data_and_bad_range():
    db = Database(":memory:")
    rep = battery_report.build(db, NAMES, time.time() - 86400, time.time())
    assert not rep["rows"] and "Nu sunt citiri" in rep["headline"]

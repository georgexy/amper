"""Pi mutat la altă instalație (cerut 2026-11-04): „Resetează rapoartele” și „Resetează sănătatea celulelor”."""
import json

from amper import battery_report, cell_health, night_report
from amper.db import Database
from test_night_report import NAMES, fill, morning_now
from test_security_setup import run


def test_reports_ignore_data_before_reset():
    db = Database(":memory:")
    fill(db, d1_amps=1.4)
    now = morning_now()
    assert night_report.build(db, NAMES, now)["rows"]
    db.set_meta(night_report.SINCE, str(int(now - 3600)))           # resetat după noapte
    rep = night_report.build(db, NAMES, now)
    assert rep["rows"] == [] and rep["headline"].startswith("Nu sunt citiri")
    per = battery_report.build(db, NAMES, now - 3 * 86400, now, now=now)
    assert per["start"] == int(now - 3600)


def test_cell_health_reset_clears_everything():
    db = Database(":memory:")
    tr = cell_health.Tracker(db)
    db.add_cell_event(1000, "d", "dis", json.dumps([0.0] * 16))
    tr.last_ir["d"] = 5.0
    cell_health.reset(db, tr)
    assert db.cell_events(0) == [] and tr.last_ir == {}


def test_reset_endpoints_need_the_password(tmp_path):
    async def steps(c, eng, sent):
        db = eng.db
        db.add_cell_event(1000, "d", "dis", json.dumps([0.0] * 16))
        r1 = await c.post("/api/cells/health/reset", json={})
        r2 = await c.post("/api/reports/reset", json={})
        ok1 = await c.post("/api/cells/health/reset", json={"app_password": "parola123"})
        ok2 = await c.post("/api/reports/reset", json={"app_password": "parola123"})
        return r1.status, r2.status, ok1.status, ok2.status, db.cell_events(0), db.get_meta(night_report.SINCE)
    cfg = {"web": {"password": "parola123"}, "settings_file": str(tmp_path / "s.yaml")}
    r1, r2, ok1, ok2, events, since = run(cfg, steps, tmp_path)
    assert r1 == r2 == 403 and ok1 == ok2 == 200
    assert events == [] and int(since) > 0

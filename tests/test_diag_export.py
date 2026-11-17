"""Exportul pentru diagnostic: arhiva .zip cu citiri, evenimente, jurnale și stare, fără date secrete."""
import asyncio
import csv
import io
import time
import zipfile

from amper import diag_export
from amper.config import DEFAULTS, _merge
from amper.db import Database
from amper.models import InverterReading, PackReading
from amper.notify import Notifier


def _engine(**cfg_extra):
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "devices": [{"id": "daly1", "type": "sim_daly", "name": "Daly 1"},
                                                                {"id": "inv", "type": "sim_growatt"}], **cfg_extra})
    return Engine(cfg, Database(":memory:"), Notifier({}))


def _fill(eng, t0):
    for i in range(30):
        eng.db.insert_pack(PackReading(device_id="daly1", ts=t0 + i * 10, voltage=53.1, current=-4.2, soc=80,
                                       cells=[3.31, 3.32, 3.30]))
        eng.db.insert_inverter(InverterReading(device_id="inv", ts=t0 + i * 10, pv_power=500, load_power=300))
    # un gol de 20 de minute
    eng.db.insert_pack(PackReading(device_id="daly1", ts=t0 + 29 * 10 + 1200, voltage=53.0, soc=79, cells=[3.3]))
    eng.notifier.send("Copia de siguranță a eșuat.", title="Amper", priority="high")
    eng.db.log_setting(t0 + 5, "daly1", "cell_hi", "3.55", "3.60", True, None, "app")


def _zip(eng, start, end, **kw):
    buf = io.BytesIO()
    fake_journal = lambda *a, **k: type("R", (), {"stdout": "2026-11-17T10:00 amper: pornit; ntfy topic baterii-abc123 "
                                                            "amper.tail1234.ts.net laptop.other999.ts.net george@example.com Wifi-Acasa", "stderr": ""})()
    summary = diag_export.build(eng, start, end, buf, system={"network": {"wifi": {"ssid": "Wifi-Acasa"}}},
                                run=fake_journal, **kw)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue())), summary


def test_archive_contents_and_counts():
    t0 = int(time.time()) - 3600
    eng = _engine()
    _fill(eng, t0)
    z, summary = _zip(eng, t0 - 60, t0 + 3700)
    names = set(z.namelist())
    assert {"CITESTE-MA.txt", "date/baterii.csv", "date/invertor.csv", "evenimente/alerte.csv",
            "evenimente/notificari.csv", "evenimente/setari_schimbate.csv", "stare/acum.json",
            "stare/config.json", "stare/pi.json", "jurnale/sistem.txt"} <= names
    rows = list(csv.DictReader(io.StringIO(z.read("date/baterii.csv").decode())))
    assert len(rows) == 31 and rows[0]["device"] == "daly1" and rows[0]["celule_V"] == "3.310;3.320;3.300"
    assert rows[0]["ora"] and rows[0]["current"] == "-4.2"
    assert summary["counts"]["citiri baterii"] == 31 and summary["counts"]["citiri invertor"] == 30
    readme = z.read("CITESTE-MA.txt").decode()
    assert "Goluri" in readme and "daly1:" in readme and "(20 min)" in readme
    notes = z.read("evenimente/notificari.csv").decode()
    assert "Copia de siguranță a eșuat." in notes
    assert "cell_hi" in z.read("evenimente/setari_schimbate.csv").decode()


def test_no_secrets_in_archive():
    t0 = int(time.time()) - 3600
    eng = _engine(web={"password": "parola-mea-123", "public_url": "https://amper.tail1234.ts.net"},
                  notify={"ntfy": {"enabled": True, "topic": "baterii-abc123"},
                          "telegram": {"enabled": True, "bot_token": "999:SECRETBOT", "chat_id": "424242"}})
    _fill(eng, t0)
    z, _ = _zip(eng, t0 - 60, t0 + 600)
    blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    for secret in ("parola-mea-123", "baterii-abc123", "SECRETBOT", "424242", "tail1234", "george@example.com",
                   "Wifi-Acasa"):
        assert secret not in blob, secret
    assert diag_export.HIDDEN in z.read("stare/config.json").decode()
    assert "<tailnet>" in z.read("jurnale/sistem.txt").decode() and "other999" not in blob


def test_old_period_includes_5min_averages():
    eng = _engine()
    now = time.time()
    z, _ = _zip(eng, int(now - 20 * 86400), int(now - 15 * 86400), now=now)
    assert "date/baterii_medii_5min.csv" in z.namelist()


def test_api_export_validates_and_downloads():
    from aiohttp.test_utils import TestClient, TestServer

    from amper.web import make_app
    eng = _engine()
    t0 = int(time.time()) - 1800
    _fill(eng, t0)

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            bad = (await c.get("/api/export?start=x")).status
            short = (await c.get(f"/api/export?start={t0}&end={t0 + 10}")).status
            long = (await c.get(f"/api/export?start={t0 - 30 * 86400}&end={t0}")).status
            r = await c.get(f"/api/export?start={t0 - 60}&end={t0 + 3000}")
            return bad, short, long, r.status, r.headers.get("Content-Disposition", ""), await r.read()
    bad, short, long, status, disp, body = asyncio.run(go())
    assert (bad, short, long, status) == (400, 400, 400, 200)
    assert "amper-diagnostic-" in disp and disp.endswith('.zip"')
    assert "date/baterii.csv" in zipfile.ZipFile(io.BytesIO(body)).namelist()

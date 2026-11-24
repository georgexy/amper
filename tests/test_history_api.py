"""Istoricul: compact (doar coloanele desenate), comprimat și ținut gata în fundal."""
import asyncio
import time

from amper.db import Database
from amper.notify import Notifier
from amper.web import compact_rows, hist_ttl


def test_compact_rows_round_and_keep_order():
    rows = [{"t": 10, "soc": 55.123456, "current": -1.23456, "pv_power": 1234.56}]
    assert compact_rows(rows, ("soc", "current", "soc_amper")) == [[10, 55.12, -1.23, None]]
    assert compact_rows(rows, ("pv_power",)) == [[10, 1235]]
    assert hist_ttl(6) < hist_ttl(24) < hist_ttl(168) < hist_ttl(720)


def test_history_all_compact_gzip_and_cached():
    from aiohttp.test_utils import TestClient, TestServer

    from amper.config import DEFAULTS, _merge
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.web import make_app
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:",
                            "devices": [{"id": "inv", "type": "sim_growatt"}, {"id": "p1", "type": "sim_daly"}]})
    db = Database(":memory:")
    eng = Engine(cfg, db, Notifier({}))
    now = int(time.time())
    for i in range(200):
        db.conn.execute("INSERT INTO pack_samples (ts, device, soc, current, voltage) VALUES (?, 'p1', ?, ?, 53.1)",
                        (now - 3600 + i * 18, 50 + i / 100, -3.14159))
        db.conn.execute("INSERT INTO inverter_samples (ts, device, pv_power, load_power, ac_in_power) "
                        "VALUES (?, 'inv', 1500.4, 400.6, 0)", (now - 3600 + i * 18,))

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            url = "/api/history/all?devices=p1,inv&hours=6&points=400"
            r1 = await c.get(url, headers={"Accept-Encoding": "gzip"})
            a = await r1.json()
            b = await (await c.get(url)).json()
            return r1.headers.get("Content-Encoding"), a, b
    enc, a, b = asyncio.run(go())
    p1, inv = a["series"]["p1"], a["series"]["inv"]
    assert enc == "gzip"
    assert p1["cols"] == ["soc", "soc_amper", "current"] and inv["cols"] == ["pv_power", "load_power", "ac_in_power"]
    assert p1["data"] and p1["data"][0][3] == -3.14 and inv["data"][0][1] == 1500
    assert b == a                                         # a doua cerere: rezultatul ținut gata

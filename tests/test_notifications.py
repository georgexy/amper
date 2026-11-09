"""Alerte → „Toate notificările”: fiecare notificare se salvează (Notifier.send și `amper notify`) și se vede prin API."""
import asyncio

import pytest
import yaml

from amper.db import Database
from amper.notify import Notifier


def test_send_saves_even_without_channels():
    db = Database(":memory:")
    n = Notifier({})
    n.db = db
    assert not n.enabled                                   # niciun canal configurat: tot se salvează
    n.send("Daly 1 conectat automat.", title="Amper", priority="low", url="/?v=alerts")
    n.send("A doua", title="Test")
    rows = db.notifications()
    assert [r["message"] for r in rows] == ["A doua", "Daly 1 conectat automat."]      # cele mai noi întâi
    assert rows[1]["priority"] == "low" and rows[1]["url"] == "/?v=alerts" and rows[1]["source"] == "amper"
    assert rows[0]["channels"] == ""


def test_delivered_channels_recorded():
    db = Database(":memory:")
    n = Notifier({"ntfy": {"enabled": True, "topic": "t-abc"}})
    n.db = db
    sent = []

    async def ok(http, item):
        sent.append(item["message"])
    n._ntfy = ok
    n.send("Copia de siguranță a eșuat.", priority="high")
    item = n.queue[0]
    asyncio.run(n._deliver(None, item))
    assert sent == ["Copia de siguranță a eșuat."]
    assert db.notifications()[0]["channels"] == "ntfy"


def test_send_translated_text_is_saved():
    from amper import i18n
    db = Database(":memory:")
    n = Notifier({})
    n.db = db
    i18n.set_lang("en")
    try:
        n.send("Amper actualizat la 1.2.3.", title="Amper")
    finally:
        i18n.set_lang("ro")
    assert db.notifications()[0]["message"] == "Amper updated to 1.2.3."   # cum a plecat pe telefon


def test_db_error_does_not_block_sending():
    class Broken:
        def add_notification(self, *a, **k):
            raise RuntimeError("disc plin")
    n = Notifier({"ntfy": {"enabled": True, "topic": "t-abc"}})
    n.db = Broken()
    n.send("mesaj")
    assert len(n.queue) == 1


def test_paging_and_purge():
    db = Database(":memory:")
    for i in range(7):
        db.add_notification(1_000 + i, "T", f"m{i}")
    first = db.notifications(limit=3)
    assert [r["message"] for r in first] == ["m6", "m5", "m4"]
    nxt = db.notifications(limit=3, before=first[-1]["id"])
    assert [r["message"] for r in nxt] == ["m3", "m2", "m1"]
    db.purge_notifications(1_003)
    assert [r["message"] for r in db.notifications()] == ["m6", "m5", "m4", "m3"]


def _cfg(tmp_path, db_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"database": str(db_path), "devices": [],
                                 "notify": {"ntfy": {"enabled": False}, "telegram": {"enabled": False}}}))
    return p


def test_cli_notify_saves_without_service(tmp_path):
    """`python3 -m amper notify` (copia de siguranță, actualizările): serviciul amper poate fi oprit."""
    from amper.__main__ import main
    db_path = tmp_path / "amper.db"
    Database(str(db_path)).close()                         # baza există (o face serviciul amper la prima pornire)
    cfg = _cfg(tmp_path, db_path)
    with pytest.raises(SystemExit) as e:                   # niciun canal: iese cu mesaj, dar notificarea rămâne
        main(["notify", "--source", "backup", "--title", "Amper: copie de siguranță", "--priority", "high",
              "--url", "/?v=alerts", "-c", str(cfg), "Copia de siguranță din 2026-11-09 a EȘUAT."])
    assert "nu sunt configurate" in str(e.value.code)
    rows = Database(str(db_path)).notifications()
    assert len(rows) == 1
    r = rows[0]
    assert (r["source"], r["priority"], r["title"], r["url"]) == ("backup", "high", "Amper: copie de siguranță", "/?v=alerts")
    assert r["message"] == "Copia de siguranță din 2026-11-09 a EȘUAT."


def test_cli_notify_concurrent_with_service(tmp_path):
    """Serviciul ține baza deschisă (WAL) cât timp comanda scrie în ea."""
    from amper.__main__ import main
    db_path = tmp_path / "amper.db"
    service = Database(str(db_path))
    service.add_notification(1, "Amper", "de la serviciu")
    cfg = _cfg(tmp_path, db_path)
    with pytest.raises(SystemExit):
        main(["notify", "--source", "update", "-c", str(cfg), "Versiune nouă detectată."])
    assert [r["message"] for r in service.notifications()] == ["Versiune nouă detectată.", "de la serviciu"]


def test_cli_notify_without_database_does_not_create_it(tmp_path):
    from amper.__main__ import main
    db_path = tmp_path / "lipsa" / "amper.db"
    cfg = _cfg(tmp_path, db_path)
    with pytest.raises(SystemExit):
        main(["notify", "-c", str(cfg), "mesaj"])
    assert not db_path.exists()                            # n-o creează ca root în locul serviciului


def test_api_notifications_paged_and_protected():
    from aiohttp.test_utils import TestClient, TestServer

    from amper.config import DEFAULTS, _merge
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.web import make_app
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "web": {"password": "pw"},
                            "devices": [{"id": "inv", "type": "sim_growatt"}]})
    eng = Engine(cfg, Database(":memory:"), Notifier({}))
    for i in range(5):
        eng.notifier.send(f"n{i}", title="Amper")          # Engine leagă Notifier de baza lui

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            proxied = {"X-Forwarded-For": "100.70.1.2"}    # de pe telefon: cere parola
            denied = (await c.get("/api/notifications", headers=proxied)).status
            a = await (await c.get("/api/notifications?limit=2")).json()
            b = await (await c.get(f"/api/notifications?limit=2&before={a['items'][-1]['id']}")).json()
            bad = (await c.get("/api/notifications?limit=x")).status
            return denied, a, b, bad
    denied, a, b, bad = asyncio.run(go())
    assert denied == 401 and bad == 400
    assert [x["message"] for x in a["items"]] == ["n4", "n3"] and a["more"]
    assert [x["message"] for x in b["items"]] == ["n2", "n1"]

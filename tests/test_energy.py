"""Energia pe zile pentru un interval ales în aplicație."""
from amper.db import Database


def test_energy_range_inclusive():
    db = Database(":memory:")
    for day, pv in [("2026-10-01", 3.0), ("2026-10-15", 4.5), ("2026-10-31", 2.0), ("2026-11-01", 9.9)]:
        db.add_energy(day, "inv", pv_kwh=pv, load_kwh=1.0)
    rows = db.energy_range("inv", "2026-10-01", "2026-10-31")
    assert [r["day"] for r in rows] == ["2026-10-01", "2026-10-15", "2026-10-31"]
    assert sum(r["pv_kwh"] for r in rows) == 9.5
    assert db.energy_range("alt", "2026-10-01", "2026-10-31") == []


def test_history_over_6h_uses_5min_averages_plus_recent_raw():
    """Graficele pe 24 h: mediile la 5 minute (rapid de citit) + ultimele minute din citirile detaliate,
    fără goluri, fără dubluri, cu aceleași valori ca din citirile detaliate."""
    from amper.db import Database, PACK_COLS
    db = Database(":memory:")
    now = 1_900_000_000 - 1_900_000_000 % 300 + 123
    start = now - 26 * 3600
    for ts in range(start, now, 10):
        db.conn.execute(f"INSERT INTO pack_samples (ts, device, {', '.join(PACK_COLS)}) "
                        f"VALUES (?, 'd', {', '.join('?' * len(PACK_COLS))})",
                        [ts, 50 + (ts - start) / 3600] + [1.0] * (len(PACK_COLS) - 1))
    db.rollup(14, now - 2000)                  # mediile rămân în urmă (se calculează o dată pe oră)
    rows = db.history("pack", "d", now - 24 * 3600, now, 400, 14)
    ts = [r["t"] for r in rows]
    assert ts == sorted(set(ts)) and all(b - a == 300 for a, b in zip(ts, ts[1:]))     # continuu, din 5 în 5 min
    assert now - ts[-1] < 300 and ts[0] - (now - 24 * 3600) < 300                       # de la început până acum
    for r in rows:                             # valoarea = media citirilor din intervalul de 5 minute
        assert abs(r["voltage"] - (50 + (r["t"] + 145 - start) / 3600)) < 0.05
    six = db.history("pack", "d", now - 6 * 3600, now, 400, 14)
    assert len(six) > 300                     # 6 ore: citirile detaliate, mai multe puncte

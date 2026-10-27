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

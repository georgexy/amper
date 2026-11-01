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


# ---------------- energia stocată (kWh): tensiunea nominală, nu cea de moment (cerut 2026-11-01) ----------------
from types import SimpleNamespace

from amper.engine import DeviceState, Engine
from amper.models import PackReading


def _bank(volt, soc, cells=16, packs=3, cfg_extra=None):
    devs = {}
    for i in range(packs):
        cfg = {"id": f"daly{i}", "type": "daly", "capacity_ah": 105, **(cfg_extra or {})}
        st = DeviceState(cfg, SimpleNamespace(kind="pack"))
        st.reading = PackReading(device_id=cfg["id"], ts=0, voltage=volt, current=0.0, soc=soc,
                                 remaining_ah=105 * soc / 100, cells=[volt / cells] * cells)
        st.last_ok = 1.0
        devs[cfg["id"]] = st.to_dict(2.0, 60)
    return Engine._bank(devs)


def test_bank_capacity_uses_nominal_voltage():
    """3 × 105 Ah, 16 celule: 16,13 kWh și la 55 V (înainte ieșea 17,3 kWh)."""
    full = _bank(55.0, 100)
    assert full["capacity_kwh"] == 16.13 and full["kwh"] == 16.13


def test_remaining_energy_does_not_follow_the_voltage():
    assert _bank(53.0, 50)["kwh"] == _bank(49.5, 50)["kwh"] == 8.06


def test_nominal_voltage_from_config_and_fallback():
    assert _bank(27.0, 100, cells=8, packs=1)["capacity_kwh"] == round(105 * 25.6 / 1000, 2)   # 8 celule → 25,6 V
    assert _bank(55.0, 100, packs=1, cfg_extra={"nominal_voltage": 48})["capacity_kwh"] == 5.04
    st = DeviceState({"id": "x", "type": "daly"}, SimpleNamespace(kind="pack"))
    assert st.nominal_voltage() == 51.2                                              # nimic cunoscut

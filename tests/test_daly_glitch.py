"""Citirile Daly „goale” (SOC 0, MOSFET-uri 0) venite între citiri bune — văzute pe hardware prin Bluetooth."""
import pytest

from amper.drivers import daly
from amper.drivers.base import DriverError
from amper.models import PackReading


def reading(ts, soc, rem, cycles, mos):
    return PackReading(device_id="d", ts=ts, voltage=53.0, soc=soc, remaining_ah=rem, cycles=cycles,
                       charge_mos=mos, discharge_mos=mos)


def drv():
    d = daly.DalyProtocol()
    d.id = "daly1"
    return d


def test_empty_reading_after_good_one_is_rejected():
    d = drv()
    d._check_glitch(reading(0, 98.7, 103.6, 331, True))
    with pytest.raises(DriverError, match="incompletă"):
        d._check_glitch(reading(5, 0, 0, 0, False))
    d._check_glitch(reading(10, 98.7, 103.6, 331, True))        # următoarea bună trece


def test_bms_that_always_reports_empty_is_shown_as_is():
    d = drv()
    for t in range(0, 50, 5):
        d._check_glitch(reading(t, 0, 0, 0, False))              # ex. BMS neconfigurat / modul nelegat


def test_hanging_device_does_not_block_others():
    """Un dispozitiv blocat (ex. Bluetooth care nu răspunde) nu are voie să oprească citirea celorlalte."""
    import asyncio
    import threading
    import time as _t

    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.engine import Engine
    from amper.drivers.simulator import SimWorld
    from amper.notify import Notifier
    SimWorld._instance = None
    devs = [{"id": f"b{i}", "type": "sim_daly"} for i in range(12)] + [{"id": "inv", "type": "sim_growatt"}]
    eng = Engine(_merge(DEFAULTS, {"database": ":memory:", "poll_interval": 0.2, "devices": devs}),
                 Database(":memory:"), Notifier({}))
    stop = threading.Event()
    calls = {}
    for i in range(12):                                  # 12 dispozitive care se blochează la citire
        drv = eng.devices[f"b{i}"].driver

        def hang(drv=drv):
            calls[drv.id] = calls.get(drv.id, 0) + 1
            stop.wait(5)
            raise DriverError("blocat")
        drv.read = hang

    async def go():
        tasks = [asyncio.create_task(eng.poll_device(st)) for st in eng.devices.values()]
        await asyncio.sleep(2.5)
        for t in tasks:
            t.cancel()
    t0 = _t.monotonic()
    asyncio.run(go())
    stop.set()
    assert eng.devices["inv"].last_ok and eng.devices["inv"].last_ok > _t.time() - 2   # invertorul se citește
    assert max(calls.values()) == 1                       # nicio citire nouă peste una încă blocată
    assert _t.monotonic() - t0 < 10


def test_old_good_reading_does_not_hide_real_change():
    d = drv()
    d._check_glitch(reading(0, 98.7, 103.6, 331, True))
    d._check_glitch(reading(700, 0, 0, 0, False))                # peste 10 minute: se acceptă

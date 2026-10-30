"""Setările invertorului: cadre Modbus de scriere, limite, verificare prin citire înapoi, API cu parolă."""
import asyncio
import struct

import pytest

from amper import inverter_settings as invset
from amper.drivers import growatt_spf
from amper.drivers.base import DriverError
from amper.drivers.simulator import SIM_HOLDING, SimInverterDriver


class FakeModbus:
    """Port serial fals care se comportă ca un invertor Growatt (funcțiile 03, 04, 06, 16)."""

    def __init__(self, holding, apply_writes=True, refuse=False):
        self.holding, self.apply, self.refuse = dict(holding), apply_writes, refuse
        self.out = b""
        self.writes = []

    def reset_input_buffer(self):
        self.out = b""

    def write(self, req):
        assert growatt_spf.crc16(req[:-2]) == req[-2] | (req[-1] << 8)
        fn = req[1]
        if fn in (3, 4):
            start, count = struct.unpack(">HH", req[2:6])
            data = b"".join(struct.pack(">H", self.holding.get(start + i, 0) if fn == 3 else 0) for i in range(count))
            pdu = bytes([req[0], fn, len(data)]) + data
        elif fn == 6:
            if self.refuse:
                pdu = bytes([req[0], 0x86, 3])
            else:
                reg, val = struct.unpack(">HH", req[2:6])
                self.writes.append((reg, val))
                if self.apply:
                    self.holding[reg] = val
                self.out = req
                return
        elif fn == 16:
            start, n = struct.unpack(">HH", req[2:6])
            for i in range(n):
                self.holding[start + i] = struct.unpack(">H", req[7 + 2 * i:9 + 2 * i])[0]
            pdu = req[:6]
        c = growatt_spf.crc16(pdu)
        self.out = pdu + bytes([c & 0xFF, c >> 8])

    def flush(self):
        pass

    def read(self, n=1):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def close(self):
        pass


def growatt(fake):
    drv = growatt_spf.GrowattSPFDriver({"id": "inv", "type": "growatt_spf", "port": f"fake-{id(fake)}"})
    drv.bus_gap = 0
    drv._ser = fake
    return drv


def test_write_frame():
    # 01 06 0022 004B: registrul 34 (curent maxim) = 75 A
    f = growatt_spf.build_write(1, 34, 75)
    assert f[:6] == bytes.fromhex("01060022004B")
    assert growatt_spf.crc16(f[:-2]) == f[-2] | (f[-1] << 8)


def test_limits_follow_battery_type():
    d = growatt_spf.SETTINGS_BY_KEY["bat_low_to_uti"]
    assert growatt_spf.setting_spec(d, 2)["unit"] == "V"
    assert growatt_spf.setting_spec(d, growatt_spf.LITHIUM)["unit"] == "%"
    assert invset.to_raw("bulk_voltage", 54.5, 2) == 545
    assert invset.to_raw("bat_low_to_uti", "46", 2) == 460
    assert invset.to_raw("bat_low_to_uti", 30, 3) == 30
    with pytest.raises(ValueError):
        invset.to_raw("bulk_voltage", 70, 2)             # peste 64 V
    with pytest.raises(ValueError):
        invset.to_raw("output_priority", 7, 2)           # nu există
    with pytest.raises(ValueError):
        invset.to_raw("nu_exista", 1, 2)


def test_describe_real_values():
    items = {x["key"]: x for x in invset.describe({**SIM_HOLDING, 45: 2026, 46: 10, 47: 25, 48: 19, 49: 3, 50: 44})}
    assert items["output_priority"]["text"] == "Rețea întâi (UTI)"
    assert items["charge_source"]["text"] == "PV și rețea"
    assert items["ac_input_mode"]["text"] == "Generator (GEN)"
    assert items["max_charge_current"]["text"] == "75 A"
    assert items["bulk_voltage"]["text"] == "54,5 V"
    assert items["bat_low_cutoff"]["text"] == "42,0 V"
    assert items["ac_to_bat"]["text"] == "54,0 V"
    assert items["output_voltage"]["text"] == "230 V" and items["output_voltage"]["risky"]
    assert invset.clock({45: 2026, 46: 10, 47: 25, 48: 19, 49: 3, 50: 44}) == "2026-10-25 19:03:44"


def test_write_setting_verified_on_real_driver_protocol():
    fake = FakeModbus(SIM_HOLDING)
    res = invset.write_setting(growatt(fake), "max_charge_current", 60)
    assert res["changed"] and res["old"] == "75 A" and res["new"] == "60 A"
    assert fake.writes == [(34, 60)] and fake.holding[34] == 60


def test_write_same_value_sends_nothing():
    fake = FakeModbus(SIM_HOLDING)
    res = invset.write_setting(growatt(fake), "max_charge_current", 75)
    assert not res["changed"] and fake.writes == []


def test_write_not_applied_is_detected():
    fake = FakeModbus(SIM_HOLDING, apply_writes=False)
    with pytest.raises(DriverError, match="arată tot"):
        invset.write_setting(growatt(fake), "buzzer", 0)


def test_write_refused_by_inverter():
    with pytest.raises(DriverError, match="refuzat"):
        invset.write_setting(growatt(FakeModbus(SIM_HOLDING, refuse=True)), "buzzer", 0)


def test_invalid_value_never_reaches_inverter():
    fake = FakeModbus(SIM_HOLDING)
    with pytest.raises(ValueError):
        invset.write_setting(growatt(fake), "float_voltage", 60)    # float maxim 56 V
    assert fake.writes == []


def test_clock_sync():
    import time
    fake = FakeModbus({**SIM_HOLDING, 45: 2026, 46: 10, 47: 25, 48: 19, 49: 3, 50: 44})   # cu o oră înainte
    now = time.time()
    res = invset.sync_clock(growatt(fake), now=now)
    lt = time.localtime(now)
    assert [fake.holding[45 + i] for i in range(4)] == [lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour]
    assert res["old"] == "2026-10-25 19:03:44"


# ---------------- API ----------------
def run_api(password, steps, devices=None):
    from aiohttp.test_utils import TestClient, TestServer

    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.engine import Engine
    from amper.notify import Notifier
    from amper.drivers.simulator import SimWorld
    from amper.web import make_app

    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "web": {"password": password},
                            "devices": devices or [{"id": "inv", "name": "Growatt", "type": "sim_growatt"}]})
    eng = Engine(cfg, Database(":memory:"), Notifier({}))

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            return await steps(c, eng)
    return asyncio.run(go())


def test_api_write_requires_password_set():
    async def steps(c, eng):
        r = await c.post("/api/inverter/settings", json={"key": "buzzer", "value": 0})
        return r.status
    assert run_api("", steps) == 403


def test_api_write_requires_password_again_even_from_localhost():
    async def steps(c, eng):
        read = await c.get("/api/inverter/settings")              # citirea merge de pe localhost (kiosk)
        no = await c.post("/api/inverter/settings", json={"key": "buzzer", "value": 0})
        wrong = await c.post("/api/inverter/settings", json={"key": "buzzer", "value": 0, "app_password": "x"})
        bad = await c.post("/api/inverter/settings", json={"key": "bulk_voltage", "value": 99, "app_password": "pw"})
        ok = await c.post("/api/inverter/settings", json={"key": "buzzer", "value": 0, "app_password": "pw"})
        j = await ok.json()
        log = eng.db.settings_log("inv")
        return read.status, no.status, wrong.status, bad.status, ok.status, j, log, eng.notifier
    read, no, wrong, bad, ok, j, log, notifier = run_api("pw", steps)
    assert read == 200 and no == 403 and wrong == 403 and bad == 400 and ok == 200
    assert j["changed"] and j["old"] == "Pornit" and j["new"] == "Oprit"
    assert log[0]["ok"] == 1 and log[0]["new"] == "Oprit"
    assert any("Buzzer" in n["message"] for n in notifier.sent)


TWO = [{"id": "inv", "name": "Growatt", "type": "sim_growatt"}, {"id": "inv2", "name": "Growatt", "type": "sim_growatt"}]


def test_common_settings_show_differences_and_write_to_all():
    async def steps(c, eng):
        eng.devices["inv2"].driver.holding[35] = 540              # bulk diferit în al doilea invertor
        before = await (await c.get("/api/inverter/settings?device=_common")).json()
        w = await c.post("/api/inverter/settings", json={"key": "bulk_voltage", "value": 55.0, "device": "_common",
                                                          "app_password": "pw"})
        j = await w.json()
        one = await c.post("/api/inverter/settings", json={"key": "buzzer", "value": 0, "device": "inv2",
                                                            "app_password": "pw"})
        return before, w.status, j, one.status, eng
    before, st, j, one, eng = run_api("pw", steps, TWO)
    bulk = next(x for x in before["settings"] if x["key"] == "bulk_voltage")
    assert before["common"] and not bulk["same"] and bulk["text"] == "diferite"
    assert [v["name"] for v in bulk["values"]] == ["Invertor 1", "Invertor 2"]
    assert next(x for x in before["settings"] if x["key"] == "buzzer")["same"]
    assert st == 200 and j["ok"] and len(j["results"]) == 2
    assert eng.devices["inv"].driver.holding[35] == eng.devices["inv2"].driver.holding[35] == 550
    assert next(x for x in j["settings"] if x["key"] == "bulk_voltage")["same"]
    assert one == 200 and eng.devices["inv"].driver.holding[22] == 1 and eng.devices["inv2"].driver.holding[22] == 0
    msgs = [n["message"] for n in eng.notifier.sent]
    assert any(m.startswith("⚙️ Invertor 1:") for m in msgs) and any(m.startswith("⚙️ Invertor 2:") for m in msgs)


def test_common_write_reports_partial_failure():
    from amper.drivers.base import DriverError

    async def steps(c, eng):
        def broken(reg, value):
            raise DriverError("fără răspuns")
        eng.devices["inv2"].driver.write_register = broken
        w = await c.post("/api/inverter/settings", json={"key": "bulk_voltage", "value": 55.0, "device": "_common",
                                                          "app_password": "pw"})
        return w.status, await w.json(), eng
    st, j, eng = run_api("pw", steps, TWO)
    assert st == 502 and j["partial"]
    assert "Scris în Invertor 1" in j["error"] and "EȘUAT la Invertor 2" in j["error"]
    assert eng.devices["inv"].driver.holding[35] == 550
    assert eng.db.settings_log("inv2")[0]["ok"] == 0


def test_common_settings_need_same_type():
    async def steps(c, eng):
        return (await c.get("/api/inverter/settings?device=_common")).status
    assert run_api("pw", steps, [TWO[0]]) == 404

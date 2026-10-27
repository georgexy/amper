"""Daly prin Bluetooth și detectarea automată (cablu USB nou sau modul Bluetooth), cu legături false."""
import time

import pytest

from amper.config import ConfigError, validate
from amper.drivers import daly, daly_auto, make_driver
from amper.drivers.base import DriverError
from tests.test_drivers import daly_responder


class FakeBleLink:
    """Imită modulul Bluetooth Daly: primește cererea, răspunde cu aceleași cadre ca pe cablu."""

    def __init__(self, target, alive=True):
        self.target, self.alive, self.sent = target, alive, []

    def exchange(self, request, expected, timeout=1.5, done=None):
        self.sent.append(request)
        if not self.alive:
            return b""
        return daly_responder(request)

    def close(self):
        pass


@pytest.fixture
def fake_ble(monkeypatch):
    links = {}

    def make(target):
        links.setdefault(target, FakeBleLink(target))
        return links[target]
    import amper.drivers.ble as ble
    monkeypatch.setattr(ble, "BleLink", make)
    return links


def test_daly_over_bluetooth(fake_ble):
    drv = make_driver({"id": "daly1", "type": "daly", "ble": "AA:BB:CC:DD:EE:01"})
    assert isinstance(drv, daly.DalyBleDriver)
    r = drv.read()
    assert r.voltage == 52.8 and r.current == 12.5 and r.soc == 64.3 and len(r.cells) == 16
    assert r.raw["link"] == "Bluetooth AA:BB:CC:DD:EE:01"
    assert fake_ble["AA:BB:CC:DD:EE:01"].sent[0][1] == 0x80        # adresa gazdei implicită pe Bluetooth


def test_config_daly_without_port_is_auto():
    cfg = {"devices": [{"id": "daly1", "type": "daly"}, {"id": "daly2", "type": "daly", "ble": "DL-40D63C"}]}
    validate(cfg)
    assert cfg["devices"][0]["port"] == "auto"
    assert daly_auto.is_auto(cfg["devices"][0]) and not daly_auto.is_auto(cfg["devices"][1])
    assert daly_auto.is_auto({"type": "daly", "port": "/dev/serial/by-id/REPLACE-daly1"})
    with pytest.raises(ConfigError):
        validate({"devices": [{"id": "s", "type": "seplos"}]})


@pytest.fixture
def auto_env(tmp_path, monkeypatch, fake_ble):
    daly_auto.set_state_dir(str(tmp_path))
    daly_auto._claimed.clear()
    daly_auto._ble_cache.update(t=None, items=[])
    ports = []
    ble_devices = []
    monkeypatch.setattr(daly_auto, "list_serial_ports", lambda: list(ports))
    import amper.drivers.ble as ble
    monkeypatch.setattr(ble, "scan", lambda seconds=8: list(ble_devices))
    return dict(ports=ports, ble=ble_devices, dir=tmp_path)


def auto_devices():
    cfgs = [{"id": f"daly{i}", "name": f"Daly {i}", "type": "daly", "port": f"/dev/serial/by-id/REPLACE-daly{i}"}
            for i in (1, 2, 3)]
    cfgs.append({"id": "inv", "type": "growatt_spf", "port": "/dev/serial/by-id/usb-exar"})
    return cfgs, [make_driver(c, cfgs) for c in cfgs[:3]]


def test_auto_binds_bluetooth_modules_in_signal_order(auto_env):
    auto_env["ble"] += [{"address": "AA:00:00:00:00:02", "name": "DL-40D63C2B", "rssi": -60},
                        {"address": "AA:00:00:00:00:01", "name": "DL-40D63C2A", "rssi": -50},
                        {"address": "AA:00:00:00:00:09", "name": "Telefon", "rssi": -40}]
    _, drvs = auto_devices()
    assert drvs[0]._search_once() and drvs[1]._search_once()
    assert not drvs[2]._search_once()                     # doar două module Daly în jur
    assert [d.inner.link_desc for d in drvs[:2]] == ["Bluetooth DL-40D63C2A · AA:00:00:00:00:01",
                                                          "Bluetooth DL-40D63C2B · AA:00:00:00:00:02"]
    assert drvs[0].read().soc == 64.3
    # după repornire, legăturile rămân aceleași (din daly-auto.json)
    daly_auto._claimed.clear()
    _, again = auto_devices()
    assert again[0].inner.link_desc == "Bluetooth DL-40D63C2A · AA:00:00:00:00:01"   # numele, ținut minte
    assert again[1].inner.link_desc == "Bluetooth DL-40D63C2B · AA:00:00:00:00:02"
    assert drvs[0].read().raw["ble_name"] == "DL-40D63C2A"


def test_auto_bluetooth_right_after_boot(auto_env, monkeypatch):
    """Pi-ul (sau mașina de test) pornit de mai puțin de 30 s: prima căutare Bluetooth se face oricum."""
    monkeypatch.setattr(daly_auto.time, "monotonic", lambda: 12.0)
    auto_env["ble"].append({"address": "AA:00:00:00:00:01", "name": "DL-40D63C2A", "rssi": -50})
    _, drvs = auto_devices()
    assert drvs[0]._search_once()


def test_auto_ignores_ports_of_other_devices_and_binds_new_usb(auto_env, monkeypatch):
    auto_env["ports"] += ["/dev/serial/by-id/usb-exar", "/dev/serial/by-id/usb-ftdi-new"]
    probed = []

    def probe(cfg, b):
        probed.append(b.get("port") or b.get("ble"))
        if b["type"] == "uart" and b["port"].endswith("ftdi-new"):
            b["address"] = 0x40
            return True
        return False
    monkeypatch.setattr(daly_auto, "_probe", probe)
    _, drvs = auto_devices()
    assert drvs[0]._search_once()
    assert drvs[0].inner.link_desc == "cablu usb-ftdi-new"
    assert "/dev/serial/by-id/usb-exar" not in probed            # portul invertorului nu e atins
    assert not drvs[1]._search_once()                           # portul e deja luat de daly1


def test_unbound_auto_reports_searching(auto_env):
    _, drvs = auto_devices()
    with pytest.raises(DriverError, match="încă negăsit"):
        drvs[0].read()
    drvs[0].close()
    time.sleep(0.05)


# ---------------------------------------------------------------- egalizatoare, deconectare, module excluse
import struct  # noqa: E402

from tests.test_drivers import daly_frame  # noqa: E402


def equalizer_responder(req):
    """Egalizator activ cu modul Bluetooth „DL-…”: tensiuni reale, dar SOC 0, fără capacitate, 0 cicluri."""
    cmd = req[2]
    if cmd == 0x90:
        return daly_frame(0x90, struct.pack(">HHHH", 531, 0, 30000, 0))
    if cmd == 0x93:
        return daly_frame(0x93, bytes(8))
    if cmd == 0x94:
        return daly_frame(0x94, bytes([16, 0, 0, 0, 0, 0, 0]))
    return b""


@pytest.fixture
def equalizer(fake_ble, monkeypatch):
    import amper.drivers.ble as ble
    real = ble.BleLink

    def make(target):
        link = real(target)
        if target.startswith("EE:"):
            link.exchange = lambda request, expected, timeout=1.5, done=None: equalizer_responder(request)
        return link
    monkeypatch.setattr(ble, "BleLink", make)


def test_equalizer_is_not_bound_as_bms(auto_env, equalizer):
    auto_env["ble"] += [{"address": "EE:00:00:00:00:01", "name": "DL-B77B020306D3", "rssi": -40},
                        {"address": "AA:00:00:00:00:01", "name": "DL-40D63C2A", "rssi": -60}]
    _, drvs = auto_devices()
    assert drvs[0]._search_once()
    assert drvs[0].binding["ble"] == "AA:00:00:00:00:01"          # egalizatorul (semnal mai bun) a fost sărit
    assert not drvs[1]._search_once()
    found = {x["label"]: x for x in daly_auto.recent_found()}
    assert not found["DL-B77B020306D3"]["ok"] and found["DL-B77B020306D3"]["check"]["soc"] == 0
    assert found["DL-40D63C2A"]["ok"] and found["DL-40D63C2A"]["device"] == "daly1"


def test_disconnect_frees_position_and_excludes(auto_env, tmp_path):
    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.engine import Engine
    from amper.notify import Notifier
    auto_env["ble"] += [{"address": "AA:00:00:00:00:01", "name": "DL-40D63C2A", "rssi": -50},
                        {"address": "AA:00:00:00:00:02", "name": "DL-40D63C2B", "rssi": -60}]
    cfg = _merge(DEFAULTS, {"database": str(tmp_path / "amper.db"), "devices": [
        {"id": "daly1", "name": "Daly 1", "type": "daly", "port": "auto", "capacity_ah": 105},
        {"id": "seplos1", "name": "Seplos 1", "type": "seplos", "port": "/dev/ttyX", "address": 1, "capacity_ah": 100}]})
    eng = Engine(cfg, Database(cfg["database"]), Notifier({}))
    daly_auto.set_state_dir(str(auto_env["dir"]))
    st = eng.devices["daly1"]
    assert st.driver._search_once() and st.driver.binding["ble"] == "AA:00:00:00:00:01"
    st.connected = True
    # o alertă deschisă a modulului se închide în tăcere
    from amper.alerts import ActiveAlert, Condition
    c = Condition("comm_lost:daly1", "comm_lost", "daly1", "warning", "Daly 1 nu mai comunică")
    eng.alerts.active[c.key] = ActiveAlert(c, first_seen=0, fired=True, db_id=eng.db.open_alert(0, "daly1", "comm_lost", "warning", c.message, key=c.key))
    res = eng.disconnect_device("daly1")
    assert res["action"] == "freed" and res["devices"] is None
    assert not st.connected and st.driver.inner is None and "daly1" not in daly_auto.load_bindings()
    assert not eng.alerts.active and not eng.db.open_alerts()
    assert not any("Rezolvat" in m["message"] for m in eng.notifier.sent)
    assert any("Daly 1 a fost deconectat" in m["message"] for m in eng.notifier.sent)
    assert eng.db.settings_log("daly1")[0]["setting"] == "conexiune"
    assert daly_auto.load_excluded()[0]["keys"] == ["ble:AA:00:00:00:00:01", "name:DL-40D63C2A"]
    # căutarea leagă alt modul pe poziția liberă, nu pe cel exclus
    assert st.driver._search_once() and st.driver.binding["ble"] == "AA:00:00:00:00:02"
    daly_auto.allow_again(0)
    assert daly_auto.load_excluded() == []
    # Seplos (fără căutare automată): scos din config, nu mai e citit
    res = eng.disconnect_device("seplos1")
    assert res["action"] == "removed" and "seplos1" not in eng.devices and [d["id"] for d in res["devices"]] == ["daly1"]

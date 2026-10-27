"""Completarea automată a porturilor în config, păstrând comentariile."""
import shutil
from pathlib import Path
from types import SimpleNamespace

import amper.__main__ as cli
from amper.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def test_autoconfig_fills_ports(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    ports = {"/dev/serial/by-id/usb-A": "daly {\"address\": 64}",
             "/dev/serial/by-id/usb-B": "growatt_spf {\"baudrate\": 9600}",
             "/dev/serial/by-id/usb-C": "daly {\"address\": 64}",
             "/dev/serial/by-id/usb-D": None}
    monkeypatch.setattr(cli, "list_ports", lambda: list(ports))
    monkeypatch.setattr(cli, "probe", lambda p: [ports[p]] if ports[p] else [])
    cli.cmd_autoconfig(SimpleNamespace(config=str(cfg)))
    text = cfg.read_text()
    assert "Câte zile se păstrează" in text                       # comentariile rămân
    c = load_config(cfg)
    by_id = {d["id"]: d["port"] for d in c["devices"]}
    assert by_id["daly1"] == "/dev/serial/by-id/usb-A"
    assert by_id["daly2"] == "/dev/serial/by-id/usb-C"
    assert by_id["daly3"] == "auto"                                # negăsit: rămâne pe căutare automată
    assert by_id["inverter"] == "/dev/serial/by-id/usb-B"
    assert by_id["seplos"].endswith("REPLACE-seplos")


def test_autoconfig_multiple_seplos(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    ports = {"/dev/serial/by-id/usb-exar": ['growatt_spf {"baudrate": 9600}'],
             "/dev/serial/by-id/usb-ch340": [f'seplos {{"baudrate": 19200, "address": {a}}}' for a in (1, 2, 3)]}
    monkeypatch.setattr(cli, "list_ports", lambda: list(ports))
    monkeypatch.setattr(cli, "probe", lambda p: ports[p])
    cli.cmd_autoconfig(SimpleNamespace(config=str(cfg)))
    c = load_config(cfg)
    sep = [d for d in c["devices"] if d["type"] == "seplos"]
    assert [d["id"] for d in sep] == ["seplos1", "seplos2", "seplos3"]
    assert [d["address"] for d in sep] == [1, 2, 3]
    assert all(d["port"] == "/dev/serial/by-id/usb-ch340" and d["baudrate"] == 19200 for d in sep)
    assert next(d for d in c["devices"] if d["id"] == "inverter")["port"] == "/dev/serial/by-id/usb-exar"
    assert [d["id"] for d in c["devices"]][:3] == ["daly1", "daly2", "daly3"]
    assert "Alerte" in cfg.read_text()


def test_shared_bus_serializes_packs():
    """Trei pachete Seplos pe același port folosesc același obiect serial."""
    from amper.drivers.seplos import SeplosDriver
    ds = [SeplosDriver({"id": f"s{a}", "type": "seplos", "port": "/dev/fake-shared", "address": a}) for a in (1, 2, 3)]
    assert ds[0]._bus is ds[1]._bus is ds[2]._bus
    assert [d.address for d in ds] == [1, 2, 3]

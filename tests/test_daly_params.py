"""Setările Daly: decodarea blocurilor citite de pe hardware și scrierea verificată (doar cu BMS simulat)."""
import pytest

from amper import daly_params as dp
from amper.drivers.base import DriverError
from amper.drivers.daly import FRAME_LEN, checksum

# blocuri citite de la Daly 1 pe 2026-10-26
REAL = {0x50: "00019a2800000c80", 0x53: "00011708100e1019", 0x59: "0e420e1008fc0af0", 0x5A: "02480244017001e0",
        0x5B: "6e2871487c387918", 0x5C: "5f690500696e0500", 0x5F: "0c80003200000000"}


def blocks():
    return {k: bytes.fromhex(v) for k, v in REAL.items()}


def test_decode_real_values():
    d = {x["key"]: x for x in dp.describe(blocks())}
    assert d["capacity"]["value"] == 105.0 and d["sleep"]["value"] == 3600
    assert (d["cell_hi_a"]["value"], d["cell_hi_b"]["value"]) == (3.65, 3.6)
    assert d["cell_hi_a"]["role"] == "protecție" and d["cell_hi_b"]["role"] == "avertisment"
    assert (d["cell_lo_a"]["value"], d["cell_lo_b"]["value"]) == (2.3, 2.8) and d["cell_lo_a"]["role"] == "protecție"
    assert (d["pack_hi_a"]["value"], d["pack_lo_b"]["value"]) == (58.4, 48.0)
    assert (d["dis_a"]["value"], d["dis_b"]["value"], d["chg_a"]["value"], d["chg_b"]["value"]) == (180, 100, 180, 100)
    assert (d["tc_hi_a"]["value"], d["tc_hi_b"]["value"], d["tc_lo_a"]["value"], d["tc_lo_b"]["value"]) == (55, 65, -35, -40)
    assert d["tc_hi_b"]["role"] == "protecție" and d["tc_lo_b"]["role"] == "protecție"
    assert (d["bal_start"]["value"], d["bal_diff"]["value"]) == (3.2, 50)


class FakeDaly:
    """BMS simulat: comenzile de citire întorc blocul; scrierea (cmd - 0x40) îl înlocuiește dacă `applies`."""

    def __init__(self, applies=True):
        self.blocks, self.applies, self.address, self.sent = blocks(), applies, 0x80, []

    def _query(self, cmd, frames=1):
        return [self.blocks[cmd]]

    def _exchange(self, request, expected):
        self.sent.append(request)
        assert len(request) == FRAME_LEN and request[12] == checksum(request[:12])
        if self.applies:
            self.blocks[request[2] + 0x40] = bytes(request[4:12])
        return b""


def test_write_changes_only_that_field(monkeypatch):
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    bms = FakeDaly()
    res = dp.write_field(bms, "tc_lo_b", 0)
    assert res["changed"] and res["old"] == "-40 °C" and res["new"] == "0 °C"
    assert bms.sent[0][2] == 0x1C                                           # scrierea = citirea 0x5C - 0x40
    assert bms.blocks[0x5C].hex() == "5f69052869" + "6e0500"                # doar octetul 3 s-a schimbat (0x28 = 0 °C)


def test_write_current_and_voltage_encoding(monkeypatch):
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    bms = FakeDaly()
    dp.write_field(bms, "chg_a", 105)
    assert bms.blocks[0x5B][4:6].hex() == f"{30000 + 1050:04x}"
    dp.write_field(bms, "cell_lo_a", 2.5)
    assert bms.blocks[0x59][4:6].hex() == f"{2500:04x}"


def test_not_applied_is_reported(monkeypatch):
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    with pytest.raises(DriverError, match="nu a aplicat"):
        dp.write_field(FakeDaly(applies=False), "sleep", 65535)


def test_limits_protect(monkeypatch):
    with pytest.raises(ValueError):
        dp.write_field(FakeDaly(), "cell_hi_a", 4.2)          # peste 3,75 V: refuzat, nimic trimis
    bms = FakeDaly()
    with pytest.raises(ValueError):
        dp.write_field(bms, "tc_lo_a", 20)
    assert bms.sent == []

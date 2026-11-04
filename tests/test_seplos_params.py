"""Parametrii Seplos V2 (CID2 0x47), cu cadrele citite pe hardware 2026-11-03 (doar comenzi de citire)."""
import pytest

from amper import seplos_params as sp
from amper.drivers.base import DriverError
from amper.drivers.seplos import build_request, decode_params, parse_response

S1_47 = ("~200146008152013C0DAC0D480B540C1C0E420D480A8C0C1C0D0205DC15E01518122012C01680151810E012C0189C17D40C9D0C7F0ABD"
         "0ADB0CCF0C9D0A450AA90CB10C7F0A450AC70CCF0C9D0A130AA90AA90B0D0C9D0C7F0AA90AC70D010CCF0A450AA90E2D0DFB0E910DFB2710"
         "251CD6FCD7C42AF8D5089E5807D0213413881B321E140A0A100A0A1E3C0505010A0A1EF0300F0560506409000D0008FFFFFF3FBF9FAF07"
         "313130312D4C42303320B2C5\r")
S3_47 = ("~200346008152033C0DAC0D480B540C1C0E420D480A8C0C1C0D0205DC15E01518122012C01680151810E012C0189C17D40C9F0C810ABF"
         "0ADD0CD10C9F0A470AAB0CB30C810A470AC90CD10C9F0A150AAB0AAB0B0F0D030CD10AAB0AC90D530D350A470AAB0E2F0DFD0E930DFD2710"
         "251CD6FCD7C42AF8D5089E5807D0271013881B321E140A0A100A0A1E3C0505010A0A1EF0300F0560506409000D0008FFFFFF3FBF9FAF07"
         "313130312D4E59303120B2F4\r")
S1_51 = "~20014600C040313130312D4C42303320100643414E3A504E475F4459455F4C7578705F544242F028\r"
S1_4F = "~200146000000FDB3\r"


def params(frame):
    return decode_params(parse_response(frame.encode()))


def test_request_is_like_the_telemetry_one():
    assert build_request(1, 0x47).startswith(b"~20014647E00201")       # INFO = adresa, ca la 0x42


def test_frames_have_valid_checksums():
    for f in (S1_47, S3_47, S1_51, S1_4F):
        parse_response(f.encode())                     # ar ridica DriverError la sumă greșită
    assert b"1101-LB03" in parse_response(S1_51.encode()) and parse_response(S1_4F.encode()) == b""


def test_decode_seplos_1_and_3():
    p1, p3 = params(S1_47), params(S3_47)
    assert (p1["address"], p1["count"], len(p1["values"])) == (1, 60, 60)
    assert p1["values"][0] == 3500 and p1["values"][8] == 3330
    assert p1["values"][58] == 8500 and p3["values"][58] == 10000
    assert p1["model"] == "1101-LB03" and p3["model"] == "1101-NY01"
    assert p1["extra"].startswith("1b321e14")


def test_describe_shows_volts_and_locks_unconfirmed():
    d = {x["idx"]: x for x in sp.describe(params(S1_47))}
    assert d[0]["text"] == "3,500 V" and d[8]["text"] == "3,330 V" and d[10]["text"] == "56,00 V"
    assert d[58]["text"] == "85,00 Ah" and d[20]["text"] == "49,8 °C" and d[52]["text"] == "-105,00 A"
    assert all(not d[i]["locked"] for i in [*range(12), 58])
    assert all(d[i]["locked"] for i in d if i not in [*range(12), 58])


class FakeSeplos:
    """Răspunde la 0x47 cu cadrul real; 0x49 aplică (sau nu) valoarea, după `apply`."""
    address = 1

    def __init__(self, apply=True, side=None):
        self.values, self.apply, self.side, self.sent = params(S1_47)["values"], apply, side, []

    def read_params(self):
        return dict(values=list(self.values), model="1101-LB03")

    def _query(self, cid2, info=None):
        self.sent.append((cid2, info))
        idx, raw = int(info[2:4], 16), int(info[4:8], 16)
        if self.apply:
            self.values[idx] = raw
        if self.side is not None:
            self.values[self.side] += 1
        return b""


def test_write_only_when_allowed_and_only_confirmed_fields():
    drv = FakeSeplos()
    with pytest.raises(PermissionError):
        sp.write_field(drv, 8, 3.34, allow=False)
    with pytest.raises(ValueError, match="neconfirmat"):
        sp.write_field(drv, 14, 57.0, allow=True)
    with pytest.raises(ValueError, match="trebuie între"):
        sp.write_field(drv, 8, 3.80, allow=True)
    assert drv.sent == []                              # nimic trimis BMS-ului


def test_write_reads_back_and_compares_all_values(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    drv = FakeSeplos()
    res = sp.write_field(drv, 8, 3.34, allow=True)
    assert res == dict(label="Echilibrare, Pornire echilibrare", old="3,330 V", new="3,340 V", changed=True)
    assert drv.sent == [(0x49, "01080D0C")]
    with pytest.raises(DriverError, match="nu a aplicat"):
        sp.write_field(FakeSeplos(apply=False), 8, 3.34, allow=True)
    with pytest.raises(DriverError, match="alți parametri"):
        sp.write_field(FakeSeplos(side=30), 8, 3.34, allow=True)

"""Citiri Daly cu celule lipsă (cadre 0x95 pierdute prin Bluetooth, văzut pe hardware 2026-10-29)."""
from amper.drivers import daly


def frame(cmd, data):
    body = bytes([0xA5, 0x01, cmd, 0x08]) + bytes(data)
    return body + bytes([daly.checksum(body)])


def cell_frames(mv):
    """Cadrele 0x95: câte 3 celule, numerotate de la 1."""
    out = []
    for i in range(0, len(mv), 3):
        chunk = list(mv[i:i + 3]) + [0] * (3 - len(mv[i:i + 3]))
        out.append(frame(0x95, [i // 3 + 1] + [b for v in chunk for b in v.to_bytes(2, "big")] + [0]))
    return out


class FakeLink:
    """Legătura BLE simulată: răspunde la fiecare comandă; la 0x95 dă cadrele din `cell_plan` (pe rând)."""
    target, name, address = "DL-TEST", "DL-TEST", None

    def __init__(self, cell_plan):
        self.cell_plan, self.asked95 = list(cell_plan), 0

    def exchange(self, request, expected, timeout=2.0, done=None):
        cmd = request[2]
        if cmd == 0x95:
            self.asked95 += 1
            return b"".join(self.cell_plan.pop(0) if self.cell_plan else [])
        data = {0x90: [0x02, 0x10, 0, 0, 0x74, 0x68, 0x02, 0x3A],          # 52,8 V, -20,0 A, SOC 57,0 %
                0x93: [2, 1, 1, 7, 0, 0, 0xEA, 0x60],                       # descarcă, MOSFET-uri pornite, 60 Ah
                0x94: [16, 1, 0, 1, 0, 0, 110, 0],                          # 16 celule, 1 senzor, 110 cicluri
                0x96: [1, 60, 0, 0, 0, 0, 0, 0], 0x97: [0] * 8, 0x98: [0] * 8}[cmd]
        return frame(cmd, data)


def driver(plan):
    d = daly.DalyBleDriver.__new__(daly.DalyBleDriver)
    d.id, d.cfg, d.ble_name = "daly3", {}, "DL-TEST"
    d._init_daly({}, 0x80)
    d.link = FakeLink(plan)
    return d


FULL = [3300 + i for i in range(16)]


def test_complete_reading_sets_cells():
    r = driver([cell_frames(FULL)]).read()
    assert len(r.cells) == 16 and r.cell_min == 3.3 and r.cell_max == 3.315 and "cells_age" not in r.raw


def test_missing_frames_keep_last_complete_cells_marked_old(monkeypatch):
    part = cell_frames(FULL)[:4]                                           # 12 celule din 16
    d = driver([cell_frames(FULL), part, part])
    t = [1000.0]
    monkeypatch.setattr(daly.time, "time", lambda: t[0])
    d.read()
    t[0] += 30
    r = d.read()
    assert d.link.asked95 == 3                                             # o reîncercare la cadre lipsă
    assert r.cells == [v / 1000 for v in FULL] and r.raw["cells_age"] == 30 and r.raw["cells_missing"] == "12/16"
    assert r.voltage == 52.8 and r.current == -20.0 and r.soc == 57.0   # restul citirii rămâne valid


def test_retry_completes_the_reading():
    d = driver([cell_frames(FULL)[:3], cell_frames(FULL)[3:]])
    r = d.read()
    assert len(r.cells) == 16 and "cells_missing" not in r.raw


def test_incomplete_without_recent_complete_gives_no_cell_stats(monkeypatch):
    part = cell_frames(FULL)[:4]
    d = driver([cell_frames(FULL), part, part, part, part])
    t = [1000.0]
    monkeypatch.setattr(daly.time, "time", lambda: t[0])
    d.read()
    t[0] += d.CELLS_KEEP_S + 1                                             # ultimele complete sunt prea vechi
    r = d.read()
    assert r.cells == [] and r.cell_min is None and r.cell_delta is None and r.soc == 57.0
    r = driver([part, part]).read()                                        # nicio citire completă până acum
    assert r.cells == [] and r.raw["cells_missing"] == "12/16"

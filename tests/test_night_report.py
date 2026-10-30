import time

from amper import night_report
from amper.db import Database
from amper.models import InverterReading, PackReading
from amper.notify import Notifier

NAMES = {"daly1": "Daly 1", "daly2": "Daly 2", "daly3": "Daly 3"}
# o noapte ca cea din 2026-10-30: soare până la 18:00, apoi 14 h fără PV, dimineața iar soare
DAY0 = time.mktime((2026, 10, 29, 12, 0, 0, 0, 0, -1))


def fill(db, *, d3_zero=0.0, d1_amps=1.9, gap=None, mos_off=None, d2_soc_bias=0.0, nights=2):
    """Citiri la 30 s: 3 Daly de 105 Ah; Daly 1 dă mai mult curent; Daly 3 pierde curenții mici (`d3_zero`)."""
    socs = {"daly1": 80.0, "daly2": 80.0 + d2_soc_bias, "daly3": 80.0}
    amps = {"daly1": d1_amps, "daly2": 1.3, "daly3": 1.5}
    t, end = DAY0 - (nights - 1) * 86400, DAY0 + 86400 + 4 * 3600
    i = 0
    while t < end:
        hour = time.localtime(t).tm_hour
        dark = hour >= 18 or hour < 8
        if not (gap and gap[0] <= t < gap[1]):
            db.insert_inverter(InverterReading(device_id="inv", ts=t, pv_power=0 if dark else 900,
                                               load_power=300 if dark else 400), ts=t)
            for d in NAMES:
                a = -amps[d] if dark else 2.0
                socs[d] = max(5, min(100, socs[d] + a * 30 / 3600 / 105 * 100))
                shown = 0.0 if d == "daly3" and dark and (i % 100) < d3_zero * 100 else a
                off = bool(mos_off and d == mos_off[0] and mos_off[1] <= t < mos_off[2])
                cells = [3.26 + (0.02 if c == 5 else 0) for c in range(16)]
                db.insert_pack(PackReading(device_id=d, ts=t, voltage=52.2, current=shown, soc=round(socs[d], 1),
                                           remaining_ah=socs[d] * 1.05, cells=cells, charge_mos=True,
                                           discharge_mos=not off), ts=t)
        t += 30
        i += 1


def morning_now():
    return DAY0 + 86400 - 2.5 * 3600                  # a doua zi, 09:30 (după ce PV-ul a pornit la 08:00)


def test_finds_the_night_from_pv():
    db = Database(":memory:")
    fill(db)
    n = night_report.find_night(db, morning_now())
    assert n["by_pv"] and not n["ongoing"]
    assert time.localtime(n["start"]).tm_hour == 18 and time.localtime(n["end"]).tm_hour == 8


def test_quiet_night_report_text_and_table():
    db = Database(":memory:")
    fill(db, d1_amps=1.4)
    rep = night_report.build(db, NAMES, morning_now())
    assert rep["ok"] and rep["headline"].startswith("Noaptea a trecut fără probleme")
    assert [r["name"] for r in rep["rows"]] == ["Daly 1", "Daly 2", "Daly 3"]
    row = rep["rows"][0]
    assert row["drop"] < -15 and row["zero"] == 0 and row["min_a"] == 1.4
    text = " ".join(rep["points"])
    assert "numără corect" in text and "aproape egal" in text and "bine echilibrate" in text
    assert "consum 300–300 W, fără PV" in rep["period"]
    assert rep["bank"].startswith("Banca la 08:00") and "Nicio alertă activă" in rep["bank"]


def test_problems_are_explained():
    db = Database(":memory:")
    night = DAY0 + 86400 - 4 * 3600                      # 08:00 a doua zi; noaptea = 18:00 → 08:00
    fill(db, d3_zero=0.5, gap=(night - 6 * 3600, night - 5.5 * 3600),
         mos_off=("daly2", night - 3 * 3600, night - 2.5 * 3600))
    db.open_alert(int(night - 4 * 3600), "daly1", "bms_protection", "critical", "Daly 1: celulă mică (nivel 2)")
    db.log_setting(int(night - 10.5 * 3600), "daly3", "comandă: SOC", "76,9 %", "43 %", True, None, "x")
    rep = night_report.build(db, NAMES, morning_now())
    assert not rep["ok"]
    assert "Daly 1: celulă mică" in rep["headline"]
    assert "MOSFET-ul de descărcare al Daly 2 a stat oprit 30 min" in rep["headline"]
    assert "Pi-ul n-a înregistrat date 30 min" in rep["headline"]
    assert rep["commands"] == ["Daly 3: SOC 76,9 % → 43 % (21:30)"]
    text = " ".join(rep["points"])
    assert "**Daly 3 nu vede curenții mici**: a arătat 0 A la 5" in text
    assert "SOC-ul nu urmărește curentul" not in text           # Daly 3 nu intră în comparațiile cu curentul
    assert "Daly 1 a dat ~46 % mai mult curent decât Daly 2" in text and "decât Daly 3" not in text
    assert "strângerea bornelor" in text
    assert night_report.summary(rep).startswith("Noaptea a avut probleme.")


def test_fixed_meter_is_noticed_against_previous_night():
    db = Database(":memory:")
    fill(db)
    rep = night_report.build(db, NAMES, morning_now())
    # noaptea trecută nu avea date de 0 A, deci niciun „vede acum”
    assert not any("vede acum" in p for p in rep["points"])
    db2 = Database(":memory:")
    fill(db2, d3_zero=0.6)                               # ambele nopți cu 0 A
    rep2 = night_report.build(db2, NAMES, morning_now())
    assert rep2["rows"][2]["zero"] >= 50 and rep2["rows"][2]["zero_prev"] >= 50


def test_morning_notification_once_per_day_and_can_be_turned_off():
    db = Database(":memory:")
    fill(db)
    n = Notifier({})
    assert night_report.morning(db, n, NAMES, morning_now() - 2.5 * 3600) is None   # 07:00: încă noapte
    assert night_report.morning(db, n, NAMES, morning_now()) is not None
    assert night_report.morning(db, n, NAMES, morning_now() + 600) is None           # doar o dată pe zi
    assert n.sent[-1]["message"].startswith("🌙 Noaptea a trecut fără probleme")
    assert n.sent[-1]["url"] == "/?v=night"
    db.set_meta(night_report.META, "")
    db.set_meta("night_report_off", "1")
    assert night_report.morning(db, n, NAMES, morning_now()) is None

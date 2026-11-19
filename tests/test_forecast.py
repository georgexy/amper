"""Prognoza (forecast.py): calibrarea panourilor din istoric, consumul pe ore, simularea bateriei, sfatul."""
import json
import math
import time

import pytest

from amper import forecast
from amper.db import Database

K = 5.0                    # panourile „adevărate”: 5 W PV la 1 W/m² (≈ 5 kWp)
DAY = 86400


def sun(ts: int, peak: float = 800) -> float:
    """Radiația medie a orei care începe la `ts` (ora locală 7–17 cu soare)."""
    h = time.localtime(ts).tm_hour + 0.5
    return max(0.0, peak * math.sin(math.pi * (h - 7) / 10)) if 7 <= h <= 17 else 0.0


def make_db(tmp_path, now, soc=50.0, load=500.0, peak_past=800):
    db = Database(str(tmp_path / "a.db"))
    h0 = int(now // 3600 * 3600)
    for t in range(h0 - 14 * DAY, h0, 300):
        hour = t // 3600 * 3600
        pv = K * sun(hour, peak_past)
        db.conn.execute("INSERT INTO inverter_5m (ts, device, pv_power, load_power) VALUES (?,?,?,?)",
                        (t, "inv", pv, load))
        db.conn.execute("INSERT INTO pack_5m (ts, device, soc) VALUES (?,?,?)", (t, "p1", soc))
    return db


def fake_fetch(now, peak_future=800, peak_past=800, calls=None):
    h0 = int(now // 3600 * 3600)

    def fetch(url):
        if calls is not None:
            calls.append(url)
        assert "timeformat=unixtime" in url and "past_days=14" in url
        times = list(range(h0 - 14 * DAY + 3600, h0 + 3 * DAY, 3600))       # sfârșitul fiecărei ore
        rad = [sun(t - 3600, peak_past if t <= h0 else peak_future) for t in times]
        return {"hourly": {"time": times, "shortwave_radiation": rad, "cloud_cover": [10] * len(times)}}
    return fetch


NOW = time.mktime(time.strptime("2026-11-19 20:00", "%Y-%m-%d %H:%M"))


def test_needs_a_location_first(tmp_path):
    db = make_db(tmp_path, NOW)
    rep = forecast.build(db, {"kwh": 8, "capacity_kwh": 16, "soc": 50}, fake_fetch(NOW), NOW)
    assert rep["missing"] == "location" and not rep["ready"]


def test_learns_the_panels_and_says_no_generator(tmp_path):
    db = make_db(tmp_path, NOW)
    forecast.save_settings(db, {"lat": 45.65, "lon": 25.6, "place": "Brașov"})
    rep = forecast.build(db, {"kwh": 10, "capacity_kwh": 16, "soc": 62}, fake_fetch(NOW), NOW)
    assert rep["ready"] and not rep["learning"]
    assert rep["calibration"]["k"] == pytest.approx(K, rel=0.01)
    assert rep["advice"]["level"] == "ok" and "Nu e nevoie de generator" in rep["advice"]["text"]
    tomorrow = rep["days"][1]
    assert tomorrow["load_kwh"] == pytest.approx(12.0, abs=0.1)            # 500 W × 24 h
    assert tomorrow["pv_kwh"] == pytest.approx(rep["days"][1]["pv_kwh"]) and tomorrow["pv_kwh"] > 20
    assert len(rep["points"]) == 49 and rep["points"][0]["soc"] == pytest.approx(62.5)


def test_cloudy_days_ask_for_the_generator(tmp_path):
    db = make_db(tmp_path, NOW)
    forecast.save_settings(db, {"lat": 45.65, "lon": 25.6, "min_soc": 20})
    rep = forecast.build(db, {"kwh": 6, "capacity_kwh": 16, "soc": 37}, fake_fetch(NOW, peak_future=60), NOW)
    a = rep["advice"]
    assert a["level"] == "need" and "Pornește generatorul" in a["text"] and "sub 20 %" in a["text"]
    low = next(p for p in rep["points"] if p["soc"] < 20)
    assert a["at"] == low["ts"] and rep["need_kwh"] > 0


def test_full_battery_hours_are_left_out_of_calibration(tmp_path):
    """Cu bateria plină invertorul taie PV-ul: orele acelea nu trag estimarea în jos."""
    db = make_db(tmp_path, NOW, soc=99)
    forecast.save_settings(db, {"lat": 45.65, "lon": 25.6})
    rep = forecast.build(db, {"kwh": 15, "capacity_kwh": 16, "soc": 94}, fake_fetch(NOW), NOW)
    assert rep["learning"] and rep["calibration"]["hours"] == 0
    assert rep["calibration"]["approx"] and rep["calibration"]["k"] == pytest.approx(K, rel=0.01)     # aproximativ


def test_weather_is_cached_and_survives_an_outage(tmp_path):
    db = make_db(tmp_path, NOW)
    s = forecast.save_settings(db, {"lat": 45.65, "lon": 25.6})
    calls = []
    forecast.weather(db, s, fake_fetch(NOW, calls=calls), NOW)
    forecast.weather(db, s, fake_fetch(NOW, calls=calls), NOW + 600)
    assert len(calls) == 1

    def down(url):
        raise RuntimeError("Daily API request limit exceeded")
    w = forecast.weather(db, s, down, NOW + 3 * 3600)
    assert w["stale"] and w["time"]
    with pytest.raises(RuntimeError, match="nu s-a putut descărca"):
        forecast.weather(db, s, down, NOW + 2 * DAY)


def test_settings_are_checked(tmp_path):
    db = make_db(tmp_path, NOW)
    with pytest.raises(ValueError):
        forecast.save_settings(db, {"min_soc": 95})
    with pytest.raises(ValueError):
        forecast.save_settings(db, {"lat": 120, "lon": 0})
    s = forecast.save_settings(db, {"min_soc": 30, "notify": False})
    assert s["min_soc"] == 30 and s["notify"] is False and json.loads(db.get_meta(forecast.META))["min_soc"] == 30


def test_one_notification_per_day(tmp_path):
    db = make_db(tmp_path, NOW)
    forecast.save_settings(db, {"lat": 45.65, "lon": 25.6})
    sent = []

    class N:
        def send(self, msg, **k):
            sent.append((msg, k))
    bank = {"kwh": 5, "capacity_kwh": 16, "soc": 31}
    assert forecast.check(db, N(), bank, fake_fetch(NOW, peak_future=40), NOW)
    assert forecast.check(db, N(), bank, fake_fetch(NOW, peak_future=40), NOW + 3600) is None
    assert len(sent) == 1 and sent[0][0].startswith("⛅ Bateria ajunge sub 20 %") and sent[0][1]["url"] == "/?v=forecast"


def test_places_from_geocoding():
    def fetch(url):
        assert "name=Bra%C8%99ov" in url
        return {"results": [{"name": "Brașov", "latitude": 45.65, "longitude": 25.61, "admin1": "Județul Brașov",
                             "country": "România"}]}
    assert forecast.places("Brașov", fetch) == [dict(name="Brașov", region="Județul Brașov, România", lat=45.65,
                                                     lon=25.61)]
    assert forecast.places("B", fetch) == []


def test_chart_forecast_and_sky_come_from_the_cache_only(tmp_path):
    db = make_db(tmp_path, NOW)
    assert forecast.pv_series(db, NOW - DAY, NOW + 12 * 3600, NOW) is None          # fără localitate
    assert forecast.sky_now(db, NOW) == {}
    s = forecast.save_settings(db, {"lat": 45.65, "lon": 25.6})
    assert forecast.pv_series(db, NOW - DAY, NOW, NOW) is None                         # nimic descărcat încă
    forecast.weather(db, s, fake_fetch(NOW), NOW)
    ser = forecast.pv_series(db, NOW - DAY, NOW + 12 * 3600, NOW)
    ts = [p[0] for p in ser["points"]]
    assert min(ts) >= NOW - DAY - 3600 and max(ts) > NOW and not ser["approx"]
    noon = max(ser["points"], key=lambda p: p[1])
    assert noon[1] == pytest.approx(K * 800 * math.sin(math.pi * 5.5 / 10) / 1, rel=0.05) or noon[1] > 3500
    tomorrow = time.strftime("%Y-%m-%d", time.localtime(NOW + DAY))
    days = forecast.pv_days(db, "2026-11-10", tomorrow, NOW)["days"]
    assert tomorrow in days and days[tomorrow] > 20
    assert forecast.sky_now(db, NOW) == {"cloud": 10, "rad": 0.0}

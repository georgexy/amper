"""Notificările de pornire și de revenire online."""
import time

from amper.presence import Presence


def p():
    return Presence(engine=None, cfg={})


def test_startup_after_power_cut():
    now = time.mktime((2026, 10, 26, 18, 10, 0, 0, 0, -1))
    msg = p().startup_message(now, last_sample=now - 38 * 60, uptime=60)
    assert msg.startswith("🔌 Pi-ul a pornit") and "17:32" in msg and "18:10" in msg and "38 min" in msg
    assert "pană de curent" in msg


def test_quick_service_restart_is_silent():
    now = time.time()
    assert p().startup_message(now, last_sample=now - 20, uptime=5000) is None


def test_back_online_after_outage():
    x = p()
    t0 = time.mktime((2026, 10, 26, 17, 32, 0, 0, 0, -1))
    assert x.on_check(True, t0 - 30) is None
    assert x.on_check(False, t0) is None
    assert x.on_check(False, t0 + 600) is None
    msg = x.on_check(True, t0 + 38 * 60)
    assert msg.startswith("🌐 Amper a revenit online") and "17:32" in msg and "38 min" in msg
    assert x.on_check(True, t0 + 39 * 60) is None


def test_short_blip_is_silent():
    x = p()
    x.on_check(False, 0)
    assert x.on_check(True, 60) is None

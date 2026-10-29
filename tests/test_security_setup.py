"""Securitate (parolă, blocare, dispozitiv nou, antete) și ghidul de configurare."""
import asyncio

import pytest
import yaml

from amper import auth as A
from amper.config import DEFAULTS, _merge, load_config, setup_needed


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, msg, title="", priority="default"):
        self.sent.append(msg)


class Clock:
    def __init__(self):
        self.t = 1_900_000_000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- parola
def test_hash_and_plain_compat():
    h = A.hash_password("parola-buna")
    assert A.verify_hash("parola-buna", h) and not A.verify_hash("alta", h)
    a = A.Auth({"web": {"password": "veche"}})                 # instalările vechi: parola în clar în config
    assert a.enabled and a.check("veche") and not a.check("VECHE")
    a2 = A.Auth({"web": {"password": "veche", "password_hash": h}})
    assert a2.check("parola-buna") and not a2.check("veche")  # hash-ul are prioritate
    assert a.token != a2.token
    assert not A.Auth({"web": {"password": ""}}).enabled


def test_lockout_per_ip_and_global():
    n, clk = Notes(), Clock()
    a = A.Auth({"web": {"password": "corecta1"}}, notifier=n, clock=clk)
    for _ in range(4):
        assert a.attempt("1.1.1.1", "gresit") == (False, 0)
    assert a.attempt("1.1.1.1", "gresit") == (False, 30)          # a 5-a: blocat 30 s
    assert a.attempt("1.1.1.1", "corecta1") == (False, 30)        # nici parola bună nu trece cât e blocat
    assert a.attempt("2.2.2.2", "corecta1") == (True, 0)          # altă adresă: merge
    clk.t += 31
    assert a.attempt("1.1.1.1", "gresit") == (False, 60)          # dublu
    clk.t += 61
    assert a.attempt("1.1.1.1", "corecta1") == (True, 0)          # reușita resetează
    assert a.attempt("1.1.1.1", "gresit") == (False, 0)
    assert len(n.sent) == 1 and "5 parole greșite" in n.sent[0]
    # global: 20 de greșeli în 15 min, de la adrese diferite → toți blocați 5 min
    for i in range(20):
        a.attempt(f"9.9.9.{i}", "gresit")
    assert a.blocked_for("3.3.3.3") == 300
    assert any("15 minute" in m for m in n.sent)


def test_new_browser_notified_once(tmp_path):
    from amper.db import Database
    n = Notes()
    a = A.Auth({"web": {"password": "corecta1"}}, db=Database(":memory:"), notifier=n, state_dir=str(tmp_path))
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Mobile Safari/604.1"
    dev = a.seen_browser(None, "5.5.5.5", ua)
    assert a.seen_browser(dev, "5.5.5.6", ua) == dev
    assert n.sent == ["🔐 Dispozitiv nou conectat la Amper: iPhone · Safari, de la 5.5.5.5."]
    assert (tmp_path / "cookie.key").stat().st_mode & 0o777 == 0o600


def test_new_password_logs_everyone_out(tmp_path):
    cfg = {"database": str(tmp_path / "amper.db"), "web": {"password": "veche123"}}
    a = A.Auth(cfg, state_dir=str(tmp_path))
    old = a.token
    a.set_password("noua-parola")
    assert not a.valid_cookie(old) and a.check("noua-parola") and not a.check("veche123")
    over = yaml.safe_load((tmp_path / "settings.yaml").read_text())
    assert over["web"]["password_hash"].startswith("scrypt$") and "noua-parola" not in (tmp_path / "settings.yaml").read_text()
    with pytest.raises(ValueError):
        a.set_password("scurta")


# ---------------------------------------------------------------- config + ghid
def write_cfg(tmp_path, extra=""):
    p = tmp_path / "config.yaml"
    p.write_text(f"database: {tmp_path}/amper.db\n{extra}")
    return str(p)


def test_overlay_and_setup_needed(tmp_path):
    cfg = load_config(write_cfg(tmp_path))
    assert setup_needed(cfg)
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump({
        "site_name": "Casa", "setup_done": True, "web": {"password_hash": A.hash_password("parola-buna")},
        "devices": [{"id": "daly1", "type": "daly", "port": "auto", "capacity_ah": 105}]}))
    cfg = load_config(write_cfg(tmp_path, "web:\n  port: 8081\ndevices:\n  - {id: x, type: sim_daly}\n"))
    assert not setup_needed(cfg) and cfg["site_name"] == "Casa" and cfg["web"]["port"] == 8081
    assert [d["id"] for d in cfg["devices"]] == ["daly1"]               # lista din aplicație o înlocuiește
    # o instalare existentă (parola în /etc/amper/config.yaml) nu vede ghidul
    (tmp_path / "settings.yaml").unlink()
    assert not setup_needed(load_config(write_cfg(tmp_path, "web:\n  password: x\n")))


def test_build_devices():
    from amper.setup_scan import build_devices
    devs = build_devices([
        {"type": "daly", "ble": "AA:BB", "capacity_ah": 105},
        {"type": "daly", "port": "/dev/serial/by-id/usb-x", "address": 128, "capacity_ah": 105},
        {"type": "daly", "capacity_ah": 100},
        {"type": "seplos", "port": "/dev/ttyUSB0", "baudrate": 19200, "address": 1, "capacity_ah": 85},
        {"type": "seplos", "port": "/dev/ttyUSB0", "baudrate": 19200, "address": 2, "capacity_ah": 85},
        {"type": "growatt_spf", "port": "/dev/ttyUSB1", "baudrate": 9600, "address": 1}])
    assert [d["id"] for d in devs] == ["daly1", "daly2", "daly3", "seplos1", "seplos2", "inverter"]
    assert devs[0]["ble"] == "AA:BB" and devs[1]["address"] == 128 and devs[2]["port"] == "auto"
    assert devs[5]["name"] == "Invertor Growatt SPF" and "capacity_ah" not in devs[5]
    with pytest.raises(ValueError, match="capacitatea"):
        build_devices([{"type": "daly", "capacity_ah": 0}])
    with pytest.raises(ValueError, match="portul"):
        build_devices([{"type": "growatt_spf"}])


# ---------------------------------------------------------------- API
def run(cfg_extra, steps, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from amper.db import Database
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.web import make_app
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": str(tmp_path / "amper.db"),
                            "devices": [{"id": "inv", "name": "Growatt", "type": "sim_growatt"}], **cfg_extra})
    from amper.notify import Notifier
    eng = Engine(cfg, Database(cfg["database"]), Notifier({}))

    class Sent:                                    # mesajele trimise, ca listă de texte
        @property
        def sent(self):
            return [m["message"] for m in eng.notifier.sent]

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            return await steps(c, eng, Sent())
    return asyncio.run(go())


PHONE = {"X-Forwarded-For": "8.8.8.8", "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120"}


def test_setup_wizard_flow(tmp_path):
    async def steps(c, eng, n):
        info = await (await c.get("/api/info")).json()
        assert info["setup_needed"] and not info["auth"]
        assert (await c.post("/api/setup/finish")).status == 400        # fără parolă nu se poate termina
        await c.post("/api/setup/name", json={"name": "Casa de la munte"})
        bad = await c.post("/api/setup/password", json={"password": "scurt", "repeat": "scurt"}, headers=PHONE)
        diff = await c.post("/api/setup/password", json={"password": "parola-buna", "repeat": "alta-parola"}, headers=PHONE)
        ok = await c.post("/api/setup/password", json={"password": "parola-buna", "repeat": "parola-buna"}, headers=PHONE)
        cookie = ok.headers.getall("Set-Cookie")
        # de acum, de pe telefon fără cookie: refuzat; cu cookie-ul primit: merge
        status = (await c.get("/api/setup", headers={**PHONE, "Cookie": ""})).status
        c.session.cookie_jar.clear()
        nocookie = (await c.get("/api/setup", headers=PHONE)).status
        await c.post("/api/login", json={"password": "parola-buna"}, headers=PHONE)
        withcookie = (await c.get("/api/setup", headers=PHONE)).status
        dev = await c.post("/api/setup/devices", headers=PHONE, json={"devices": [
            {"type": "daly", "capacity_ah": 105}, {"type": "growatt_spf", "port": "/dev/ttyUSB0"}]})
        fin = await (await c.post("/api/setup/finish", headers=PHONE)).json()
        # schimbarea parolei cere parola veche
        nold = await c.post("/api/setup/password", headers=PHONE,
                            json={"password": "alta-parola", "repeat": "alta-parola", "old": "gresita"})
        return bad.status, diff.status, ok.status, cookie, status, nocookie, withcookie, dev.status, fin, nold.status, n.sent
    bad, diff, ok, cookie, _, nocookie, withcookie, dev, fin, nold, sent = run({}, steps, tmp_path)
    assert bad == 400 and diff == 400 and ok == 200
    assert any("amper=" in h and "HttpOnly" in h and "SameSite=Strict" in h for h in cookie)
    assert nocookie == 401 and withcookie == 200 and dev == 200 and nold == 403
    assert fin == {"ok": True, "restart": False}              # simulator: fără repornire
    over = yaml.safe_load((tmp_path / "settings.yaml").read_text())
    assert over["site_name"] == "Casa de la munte" and over["setup_done"] is True
    assert [d["id"] for d in over["devices"]] == ["daly1", "inverter"]
    assert any("Parola aplicației a fost schimbată" in m for m in sent)
    assert not any("Dispozitiv nou" in m for m in sent[:1])   # cel care a pus parola nu e anunțat ca „nou”
    cfg = load_config(write_cfg(tmp_path))
    assert not setup_needed(cfg) and cfg["devices"][0]["capacity_ah"] == 105


def test_login_lockout_headers_and_new_device(tmp_path):
    async def steps(c, eng, n):
        codes = [(await c.post("/api/login", json={"password": "x"}, headers=PHONE)).status for _ in range(5)]
        blocked = await c.post("/api/login", json={"password": "pw-corecta"}, headers=PHONE)
        other = await c.post("/api/login", json={"password": "pw-corecta"},
                             headers={"X-Forwarded-For": "7.7.7.7", "User-Agent": "Mozilla/5.0 (iPhone) Safari"})
        page = await c.get("/")
        sec = await (await c.get("/api/security", headers={"X-Forwarded-For": "7.7.7.7"})).json()
        return codes, blocked.status, (await blocked.json()), other.status, dict(page.headers), sec, n.sent
    codes, blocked, bj, other, hdr, sec, sent = run({"web": {"password": "pw-corecta"}}, steps, tmp_path)
    assert codes == [401, 401, 401, 401, 429] and blocked == 429 and bj["wait"] > 0 and other == 200
    assert hdr["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in hdr["Content-Security-Policy"]
    assert hdr["X-Content-Type-Options"] == "nosniff"
    assert any("Dispozitiv nou conectat la Amper: iPhone · Safari, de la 7.7.7.7" in m for m in sent)
    assert any("5 parole greșite la rând de la 8.8.8.8" in m for m in sent)
    assert sec["log"][0]["ok"] == 1 and sum(1 for r in sec["log"] if not r["ok"]) == 6
    assert len(sec["devices"]) == 1 and sec["devices"][0]["this"]


def test_update_channel_saved(tmp_path):
    from test_update import Env
    e = Env(tmp_path)
    u = e.updater()
    assert u.set_channel("beta")["channel"] == "beta"
    assert e.updater().channel == "beta"                      # păstrat după repornire


# ---------------------------------------------------------------- parola uitată (fișierul de pe card)
def test_password_reset_from_card(tmp_path):
    from amper.password_reset import reset
    boot = tmp_path / "boot"
    boot.mkdir()
    cfg_path = write_cfg(tmp_path, "web:\n  password: veche-din-config\n")
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump({"setup_done": True,
                                                            "web": {"password_hash": A.hash_password("parola-buna")}}))
    assert not reset(cfg_path, boot_dirs=[str(boot)], owner=None)          # fără fișier: nimic
    (boot / "Resetare-Parola.txt.txt").write_text("")                     # Windows ascunde „.txt”
    assert reset(cfg_path, boot_dirs=[str(boot)], owner=None)
    assert not list(boot.iterdir())
    cfg = load_config(cfg_path)
    assert not A.Auth(cfg).enabled and cfg["password_reset"] and not setup_needed(cfg)


def test_password_reset_only_from_home_network(tmp_path):
    async def steps(c, eng, n):
        info = await (await c.get("/api/info")).json()
        remote = await c.get("/api/info", headers={**PHONE, "Tailscale-Funnel-Request": "?1"})
        remote_set = await c.post("/api/setup/password", json={"password": "hacker-pw1", "repeat": "hacker-pw1"},
                                  headers=PHONE)
        home = {"User-Agent": PHONE["User-Agent"]}                         # din rețeaua de acasă: fără proxy
        ok = await c.post("/api/setup/password", json={"password": "parola-noua", "repeat": "parola-noua"}, headers=home)
        after = await (await c.get("/api/info")).json()
        return info, remote.status, (await remote.json()), remote_set.status, ok.status, after, n.sent
    info, rs, rj, rset, ok, after, sent = run({"password_reset": 1, "setup_done": True}, steps, tmp_path)
    assert info["password_reset"] and not info["setup_needed"]
    assert rs == 403 and rj["reset_local"] and rset == 403 and ok == 200
    assert not after["password_reset"] and after["auth"]
    assert not (yaml.safe_load((tmp_path / "settings.yaml").read_text()) or {}).get("password_reset")
    assert any("ștearsă cu fișierul de pe card" in m for m in sent)


# ---------------------------------------------------------------- Adaugă dispozitiv (Setări → Dispozitive conectate → +)
def test_add_device_ids_and_unsupported():
    from amper.setup_scan import add_device
    existing = [{"id": "daly1", "name": "Daly 1", "type": "daly", "ble": "AA"}, {"id": "daly3", "name": "Daly 3", "type": "daly"},
                {"id": "inverter", "name": "Invertor", "type": "growatt_spf", "port": "/dev/x"}]
    d = add_device(existing, {"type": "daly", "ble": "BB:00", "ble_name": "DL-BB00", "capacity_ah": 105})
    assert d["id"] == "daly4" and d["name"] == "Daly 4" and d["ble"] == "BB:00" and d["ble_name"] == "DL-BB00"
    s = add_device(existing, {"type": "seplos", "port": "/dev/ttyUSB1", "address": 2, "capacity_ah": 100, "name": "Raft"})
    assert s["id"] == "seplos1" and s["name"] == "Raft" and s["baudrate"] == 19200
    with pytest.raises(ValueError, match="JBD.*nu e suportat"):
        add_device(existing, {"type": "jbd", "ble": "CC", "capacity_ah": 100})
    j = add_device(existing, {"type": "jk", "ble": "CC:00", "ble_name": "JK_B2A8S20P", "capacity_ah": 280})
    assert j["id"] == "jk1" and j["name"] == "JK 1" and j["ble"] == "CC:00" and j["ble_name"] == "JK_B2A8S20P"
    u = add_device(existing, {"type": "jk", "port": "/dev/ttyUSB2", "capacity_ah": 100})
    assert u["port"] == "/dev/ttyUSB2" and u["baudrate"] == 115200
    with pytest.raises(ValueError, match="capacitatea"):
        add_device(existing, {"type": "daly", "ble": "CC"})


def test_scan_ble_hides_used_modules(monkeypatch, tmp_path):
    from amper import setup_scan
    from amper.drivers import ble, daly_auto
    monkeypatch.setattr(ble, "scan", lambda s: [
        {"address": "AA:00", "name": "DL-AA00", "rssi": -50},       # în config
        {"address": "BB:00", "name": "DL-BB00", "rssi": -55},       # găsit automat (daly-auto.json)
        {"address": "CC:00", "name": "JK_B2A8S20P", "rssi": -60},
        {"address": "DD:00", "name": "", "rssi": -40}])            # fără nume: nu apare
    monkeypatch.setattr(daly_auto, "load_bindings", lambda: {"daly2": {"type": "ble", "ble": "BB:00", "name": "DL-BB00"}})
    r = setup_scan.scan_ble({"devices": [{"id": "daly1", "type": "daly", "ble": "aa:00"}]})
    assert [f["ble"] for f in r["found"]] == ["CC:00"] and r["found"][0]["guess"] == "jk"
    assert any(t["type"] == "daly" and t["supported"] for t in r["types"])


def test_api_add_device(tmp_path):
    async def steps(c, eng, n):
        bad = await c.post("/api/devices/add", json={"device": {"type": "jbd", "ble": "CC", "capacity_ah": 50}})
        ok = await c.post("/api/devices/add", json={"device": {"type": "daly", "ble": "CC:00", "ble_name": "DL-CC00",
                                                                 "capacity_ah": 105}})
        return bad.status, await ok.json(), n.sent
    bad, ok, sent = run({"devices": [{"id": "inv", "name": "Growatt", "type": "sim_growatt"},
                                     {"id": "daly1", "name": "Daly 1", "type": "daly", "ble": "AA:00", "capacity_ah": 105}]},
                        steps, tmp_path)
    assert bad == 400 and ok["device"]["id"] == "daly2" and ok["restart"] is False      # simulator: fără repornire
    over = yaml.safe_load((tmp_path / "settings.yaml").read_text())
    assert [d["id"] for d in over["devices"]] == ["daly1", "daly2"] and over["devices"][1]["ble_name"] == "DL-CC00"
    assert any("Dispozitiv adăugat" in m for m in sent)


def test_api_disconnect_needs_password_and_excluded_list(tmp_path):
    from amper.drivers import daly_auto
    daly_auto.set_state_dir(str(tmp_path))
    daly_auto.exclude({"type": "ble", "ble": "EE:01", "name": "DL-B77B"}, "daly2: DL-B77B", "respins din aplicație")

    async def steps(c, eng, n):
        await c.post("/api/login", json={"password": "pw-corecta"})
        no = await c.post("/api/devices/seplos1/disconnect", json={})
        bad = await c.post("/api/devices/seplos1/disconnect", json={"app_password": "gresit"})
        ok = await c.post("/api/devices/seplos1/disconnect", json={"app_password": "pw-corecta"})
        inv = await c.post("/api/devices/inv/disconnect", json={"app_password": "pw-corecta"})   # invertorul: nu e BMS
        ex = await (await c.get("/api/devices/excluded")).json()
        allow = await (await c.post("/api/devices/excluded/allow", json={"index": 0})).json()
        return no.status, bad.status, ok.status, await ok.json(), inv.status, ex, allow, list(eng.devices)
    no, bad, ok, okj, inv, ex, allow, devs = run({"web": {"password": "pw-corecta"}, "devices": [
        {"id": "inv", "name": "Growatt", "type": "sim_growatt"},
        {"id": "seplos1", "name": "Seplos 1", "type": "seplos", "port": "/dev/ttyX", "address": 1, "capacity_ah": 100}]},
        steps, tmp_path)
    assert no == 403 and bad == 403 and ok == 200 and okj["action"] == "removed" and inv == 404
    assert devs == ["inv"] and ex["items"][0]["label"] == "daly2: DL-B77B" and allow["items"] == []
    over = yaml.safe_load((tmp_path / "settings.yaml").read_text())
    assert [d["id"] for d in over["devices"]] == ["inv"]


def test_page_ids_are_unique():
    """Două elemente cu același id: `$("#…")` îl găsește doar pe primul, iar celălalt buton nu mai face nimic
    (așa s-a stricat „Scrie” din fereastra de modificare, din cauza butonului „Activează Pro”, până la 1.0.0-beta.11)."""
    import re
    from collections import Counter
    from pathlib import Path
    html = (Path(__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    dup = [k for k, n in Counter(re.findall(r'\bid="([^"]+)"', html)).items() if n > 1]
    assert dup == []

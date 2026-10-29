"""Comenzile Daly (repornire, MOSFET-uri, SOC): cadrele exacte, verificarea după comandă, preverificările de siguranță
și marcarea „nesuportată”. Doar cu BMS simulat — pe hardware se testează cu utilizatorul, pas cu pas."""
import time

import pytest

from amper import daly_actions as da
from amper.db import Database
from amper.drivers.base import DriverError
from amper.drivers.daly import FRAME_LEN, build_request, checksum, parse_frames


def frame(cmd, data):
    body = bytes([0xA5, 0x01, cmd, 0x08]) + bytes(data)
    return body + bytes([checksum(body)])


# ---------------- cadrele exacte ----------------
NOW = time.mktime((2026, 10, 29, 14, 5, 9, 0, 0, -1))      # ora locală a Pi-ului


@pytest.mark.parametrize("addr", [0x40, 0x80])
def test_exact_frames(addr):
    def hexs(key, value=None):
        return da.request(addr, key, value, NOW).hex()
    head = f"a5{addr:02x}"
    assert hexs("restart") == build_request(addr, 0x00, bytes(8)).hex() == head + "0008" + "00" * 8 + f"{(0xA5 + addr + 8) & 0xFF:02x}"
    assert hexs("dsg_on") == build_request(addr, 0xD9, bytes([1, 0, 0, 0, 0, 0, 0, 0])).hex()
    assert hexs("dsg_off") == build_request(addr, 0xD9, bytes(8)).hex()
    assert hexs("chg_on") == build_request(addr, 0xDA, bytes([1, 0, 0, 0, 0, 0, 0, 0])).hex()
    assert hexs("chg_off") == build_request(addr, 0xDA, bytes(8)).hex()
    # 0x21: 26-10-29 14:05:09, SOC 42,5 % = 425 = 0x01A9
    assert hexs("set_soc", 42.5)[:8] == head + "2108"
    assert hexs("set_soc", 42.5)[8:24] == "1a0a1d0e050901a9"
    for k in ("restart", "dsg_on", "chg_off"):
        req = bytes.fromhex(hexs(k))
        assert len(req) == FRAME_LEN and req[12] == checksum(req[:12])
    with pytest.raises(ValueError):
        da.request(addr, "set_soc", 101)


# ---------------- BMS simulat ----------------
class FakeDaly:
    """BMS cu protocolul clasic: `mode` = apply | ignore (răspunde, nu aplică) | silent (nu răspunde, nu aplică)."""

    def __init__(self, mode="apply", address=0x80, protect=False):
        self.mode, self.address, self.protect = mode, address, protect
        self.chg = self.dsg = True
        self.soc, self.cycles, self.cap_mah = 600, 110, 105000
        self.down_until, self.t = 0.0, 0.0
        self.sent = []

    # timpul simulat (sleep-ul din teste îl avansează)
    def sleep(self, s):
        self.t += s

    def clock(self):
        return self.t

    def _exchange(self, request, expected):
        self.sent.append(request)
        cmd, data = request[2], request[4:12]
        if self.t < self.down_until:
            raise DriverError("Bluetooth: nu răspunde (repornește)")
        if cmd == 0x00:
            if self.mode == "apply":
                self.down_until = self.t + 5              # repornire: 5 s fără răspuns, fără cadru înapoi
            return b""
        if cmd in (0xD9, 0xDA, 0x21):
            if self.mode == "silent":
                return b""
            if self.mode == "apply":
                if cmd == 0xD9:
                    self.dsg = bool(data[0]) and not self.protect
                elif cmd == 0xDA:
                    self.chg = bool(data[0])
                else:
                    self.soc = (data[6] << 8) | data[7]
            return frame(cmd, [1 if self.mode == "apply" else 0] + [0] * 7)
        resp = {0x90: [0x02, 0x10, 0, 0, 0x75, 0x30, self.soc >> 8, self.soc & 0xFF],
                0x93: [0, int(self.chg), int(self.dsg), 0] + list((60000).to_bytes(4, "big")),
                0x94: [16, 1, 0, 1, 0] + list(self.cycles.to_bytes(2, "big")) + [0],
                0x50: list(self.cap_mah.to_bytes(4, "big")) + [0, 0, 0x0C, 0x80],
                0x98: [0b1000 if self.protect else 0] + [0] * 7,           # bitul 3 = celulă mică (nivel 2)
                0x5B: list((30000 - 1800).to_bytes(2, "big")) + [0x71, 0x48]
                      + list((30000 + 1800).to_bytes(2, "big")) + [0x79, 0x18],
                0x62: [1] + list(b"31_2306"),
                }.get(cmd)
        return frame(cmd, resp) if resp else b""

    def _query(self, cmd, frames=1):
        data = parse_frames(self._exchange(build_request(self.address, cmd), FRAME_LEN * frames), cmd)
        if not data:
            raise DriverError(f"fără răspuns la 0x{cmd:02X}")
        return data


def run(bms, key, value=None):
    return da.execute(bms, key, value, sleep=bms.sleep, clock=bms.clock, now=NOW)


def test_mosfet_off_and_on_verified():
    bms = FakeDaly()
    res = run(bms, "dsg_off")
    assert res["ok"] and (res["old"], res["new"]) == ("pornit", "oprit") and bms.dsg is False
    assert run(bms, "dsg_on")["ok"] and bms.dsg
    assert run(bms, "chg_off")["ok"] and not bms.chg


def test_mosfet_kept_off_by_protection_says_why():
    bms = FakeDaly(protect=True)
    bms.dsg = False
    res = run(bms, "dsg_on")
    assert not res["ok"] and res["error"] == "BMS-ul ține descărcarea oprită: Tensiune celulă mică (nivel 2)"
    assert not res["support_fail"]                          # comanda e suportată, BMS-ul o refuză pe bună dreptate


def test_not_applied_and_no_answer():
    res = run(FakeDaly(mode="ignore"), "dsg_off")
    assert not res["ok"] and "nu a oprit descărcarea" in res["error"] and res["support_fail"]
    res = run(FakeDaly(mode="silent"), "chg_off")
    assert not res["ok"] and "nu a răspuns" in res["error"] and res["support_fail"]


def test_set_soc_within_half_percent():
    bms = FakeDaly()
    res = run(bms, "set_soc", 42.5)
    assert res["ok"] and res["old"] == "60,0 %" and res["new"] == "42,5 %"
    res = run(FakeDaly(mode="ignore"), "set_soc", 42.5)
    assert not res["ok"] and "refuzat" in res["error"]


def test_restart_waits_and_checks_identity():
    bms = FakeDaly()
    res = run(bms, "restart")
    assert res["ok"] and res["seconds"] >= 5 and res["old"] == res["new"] == "110 cicluri, 105 Ah"


def test_restart_reset_to_factory_is_critical():
    bms = FakeDaly()
    orig = bms._exchange

    def reset(req, exp):
        out = orig(req, exp)
        if req[2] == 0x00:
            bms.cycles, bms.cap_mah = 0, 30000              # „din fabrică”
        return out
    bms._exchange = reset
    res = run(bms, "restart")
    assert not res["ok"] and res["critical"] and "setările din fabrică" in res["error"]


def test_restart_never_comes_back():
    bms = FakeDaly()
    bms.down_until = 1e9
    bms.mode = "apply"
    orig = bms._exchange
    calls = {"n": 0}

    def dead(req, exp):
        calls["n"] += 1
        if calls["n"] <= 2:                                 # identitatea citită înainte, apoi nu mai răspunde
            saved, bms.down_until = bms.down_until, 0
            try:
                return orig(req, exp)
            finally:
                bms.down_until = saved
        raise DriverError("nu răspunde")
    bms._exchange = dead
    res = run(bms, "restart")
    assert not res["ok"] and res["critical"] and "60 s" in res["error"]


# ---------------- preverificările de siguranță ----------------
def snap(load_w=3000, dsg=(True, True, True), chg=(True, True, True), bank_current=-57.7):
    devs = {f"daly{i + 1}": dict(name=f"Daly {i + 1}", kind="pack", connected=True, stale=False,
                                 reading=dict(discharge_mos=dsg[i], charge_mos=chg[i]))
            for i in range(3)}
    devs["inv"] = dict(name="Growatt", kind="inverter", stale=False,
                       reading=dict(load_power=load_w, battery_voltage=52.0, battery_power=None))
    return dict(devices=devs, bank=dict(voltage=52.0, current=bank_current))


LIM = {d: {"dis": 100, "chg": 100} for d in ("daly1", "daly2", "daly3")}


def test_discharge_off_allowed_when_others_can_carry():
    assert da.precheck("dsg_off", "daly1", snap(), LIM, set()) is None    # 3 kW ≈ 58 A ×1,3 = 75 A ≤ 200 A


def test_discharge_off_refused_if_one_left_too_weak_or_none():
    weak = {d: {"dis": 50, "chg": 50} for d in LIM}
    why = da.precheck("dsg_off", "daly1", snap(dsg=(True, True, False)), weak, set())
    assert why.startswith("Ar rămâne un singur modul pentru 3,0 kW")
    why = da.precheck("dsg_off", "daly1", snap(dsg=(True, False, False)), LIM, set())
    assert "zero module" in why


def test_charge_off_uses_charge_limits():
    s = snap(load_w=200, bank_current=80)
    assert da.precheck("chg_off", "daly1", s, LIM, set()) is None          # 80 A ×1,3 = 104 ≤ 200
    weak = {d: {"dis": 100, "chg": 40} for d in LIM}
    assert "pentru" in da.precheck("chg_off", "daly1", s, weak, set())


def test_turning_on_is_always_allowed_and_restart_rules():
    s = snap(dsg=(False, False, False))
    assert da.precheck("dsg_on", "daly1", s, {}, set()) is None
    assert da.precheck("set_soc", "daly1", s, {}, set()) is None
    assert "Alt BMS repornește" in da.precheck("restart", "daly1", snap(), LIM, {"daly2"})
    assert "Daly 3" in da.precheck("restart", "daly1", snap(chg=(True, True, False)), LIM, set())
    assert da.precheck("restart", "daly1", snap(), LIM, set()) is None


# ---------------- „nesuportată” după 2 eșecuri ----------------
def test_unsupported_after_two_failures_and_retry():
    db = Database(":memory:")
    sup = da.Support(db)
    bms = FakeDaly(mode="silent")
    key = da.support_key(bms)
    assert key == "31_2306|0x80"
    assert not sup.record(key, "chg_off", run(bms, "chg_off"))
    assert "chg_off" not in sup.unsupported(key)
    assert sup.record(key, "chg_off", run(bms, "chg_off"))                # al doilea eșec la rând
    assert sup.unsupported(key) == {"chg_off"}
    assert da.Support(db).unsupported(key) == {"chg_off"}                  # ținut minte în baza de date
    sup.reset(key, "chg_off")
    assert sup.unsupported(key) == set()
    # un succes între eșecuri resetează numărătoarea
    sup.record(key, "dsg_off", {"ok": False, "support_fail": True})
    sup.record(key, "dsg_off", {"ok": True})
    sup.record(key, "dsg_off", {"ok": False, "support_fail": True})
    assert sup.unsupported(key) == set()


def test_soc_suggestion_from_cells():
    s = da.soc_from_voltage([3.30] * 16, current=0.3)
    assert s["soc"] == 60 and s["rest"]
    assert da.soc_from_voltage([3.30] * 16, current=-20)["rest"] is False
    assert da.soc_from_voltage([]) is None


# ---------------- API + alertele (cu serverul web real) ----------------
def web_run(tmp_path, steps, fake):
    from test_security_setup import run as srun

    async def wrapped(c, eng, n):
        st = eng.devices["daly1"]
        fake.kind = "pack"
        fake.read = lambda: (_ for _ in ()).throw(DriverError("test"))
        st.driver = fake
        st.connected = True
        return await steps(c, eng, n)
    return srun({"edition": "pro", "web": {"password": "parola-buna"},
                 "devices": [{"id": "daly1", "name": "Daly 1", "type": "daly", "port": "/dev/amper-test"},
                             {"id": "inv", "name": "Growatt", "type": "sim_growatt"}]}, wrapped, tmp_path)


def test_api_action_logs_notifies_and_marks_forced(tmp_path, monkeypatch):
    monkeypatch.setattr(da.time, "sleep", lambda s: None)
    fake = FakeDaly()

    async def steps(c, eng, n):
        lst = await (await c.get("/api/daly/daly1/actions")).json()
        bad = await c.post("/api/daly/daly1/action", json={"action": "dsg_off", "app_password": "gresita"})
        # singurul modul: oprirea descărcării e refuzată pe server (invertorul s-ar opri)
        refused = await c.post("/api/daly/daly1/action", json={"action": "dsg_off", "app_password": "parola-buna"})
        # … dar se poate opri totuși, cu `force` (ex. intervenție la conexiunile modulului)
        ok = await c.post("/api/daly/daly1/action", json={"action": "dsg_off", "app_password": "parola-buna",
                                                           "force": True})
        log = eng.db.settings_log("daly1")
        return (lst, bad.status, (refused.status, await refused.json()), ok.status, await ok.json(), log,
                dict(eng.devices["daly1"].forced), n.sent)
    lst, bad, refused, ok, body, log, forced, sent = web_run(tmp_path, steps, fake)
    assert {a["key"] for a in lst["actions"]} == set(da.BY_KEY) and lst["firmware"] == "31_2306|0x80"
    assert next(a for a in lst["actions"] if a["key"] == "dsg_off")["refused"].startswith("Ar rămâne zero module")
    assert refused[0] == 409 and refused[1]["refused"] and refused[1]["can_force"]
    assert bad == 403 and ok == 200 and body["ok"] and fake.dsg is False
    assert log[0]["setting"].startswith("comandă: Oprește descărcarea (forțat, deși: Ar rămâne zero module")
    assert log[0]["ok"] == 1
    assert "dsg" in forced and any("⚙️ Daly 1: Oprește descărcarea" in m for m in sent)


def test_forced_off_alert_and_no_comm_lost_while_restarting():
    from amper.alerts import evaluate
    from amper.config import DEFAULTS
    a = dict(DEFAULTS["alerts"])
    now = 10_000.0
    dev = dict(name="Daly 1", kind="pack", connected=True, stale=True, last_ok=now - 600, restarting=True,
               reading=dict(discharge_mos=False, charge_mos=True), forced={"dsg": now - 31 * 60})
    conds = evaluate(dict(devices={"daly1": dev}, started=0, bank={}), a, now)
    rules = {c.rule: c for c in conds}
    assert "comm_lost" not in rules                                    # repornește din Amper: nu „nu comunică”
    assert rules["mos_forced_off"].message == "Daly 1: descărcarea e oprită din Amper de 31 min"
    assert rules["mos_forced_off"].level == "warning"
    dev["forced"] = {"dsg": now - 10 * 60}
    assert "mos_forced_off" not in {c.rule for c in evaluate(dict(devices={"daly1": dev}, started=0, bank={}), a, now)}


def test_restart_cannot_be_forced(tmp_path, monkeypatch):
    monkeypatch.setattr(da, "precheck", lambda *a, **k: "Alt BMS repornește acum. Câte unul odată.")

    async def steps(c, eng, n):
        r = await c.post("/api/daly/daly1/action", json={"action": "restart", "app_password": "parola-buna",
                                                          "force": True})
        return r.status, await r.json()
    status, body = web_run(tmp_path, steps, FakeDaly())
    assert status == 409 and not body["can_force"]

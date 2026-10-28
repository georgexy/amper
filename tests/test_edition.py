"""Edițiile Amper Lite / Pro: ID-ul Pi-ului, codul de activare semnat, ediția de bază, blocarea rutelor Pro."""
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from amper import edition
from amper.config import DEFAULTS, _merge, read_overlay
from amper.db import Database
from amper.models import PackReading
from test_security_setup import run


def keypair():
    k = Ed25519PrivateKey.generate()
    pub = base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return k, pub


def test_pi_id_from_machine_id(tmp_path):
    m = tmp_path / "machine-id"
    m.write_text("0123456789abcdef0123456789abcdef\n")
    a = edition.pi_id((str(m),))
    assert a == edition.pi_id((str(tmp_path / "lipsă"), str(m)))          # stabil, din primul fișier găsit
    assert len(a) == 9 and a[4] == "-" and all(c in edition.ALPHABET for c in a.replace("-", ""))
    m.write_text("fedcba9876543210fedcba9876543210\n")
    assert edition.pi_id((str(m),)) != a
    assert edition.norm_id(" 7k3f q9pa ") == "7K3F-Q9PA"


def test_code_only_for_its_pi_and_only_from_the_author():
    k, pub = keypair()
    code = edition.make_code(k, "7K3F-Q9PA")
    assert edition.code_ok(code, "7k3fq9pa", [pub])                       # ID scris oricum
    assert edition.code_ok(f"  {code[:40]}\n{code[40:]} ", "7K3F-Q9PA", [pub])   # lipit cu spații / rânduri
    assert not edition.code_ok(code, "7K3F-Q9PB", [pub])                  # alt Pi
    assert not edition.code_ok(code[:-2] + ("A" if code[-2] != "A" else "B") + code[-1], "7K3F-Q9PA", [pub])
    other, _ = keypair()
    assert not edition.code_ok(edition.make_code(other, "7K3F-Q9PA"), "7K3F-Q9PA", [pub])   # altă cheie
    assert not edition.code_ok(code, "7K3F-Q9PA")                         # cheia reală a autorului: nu e a testului
    assert not edition.code_ok("", "7K3F-Q9PA", [pub]) and not edition.code_ok("!!!", "7K3F-Q9PA", [pub])


def cfg_for(tmp_path, **extra):
    return _merge(DEFAULTS, {"database": str(tmp_path / "amper.db"),
                             "devices": [{"id": "d1", "type": "daly", "port": "auto"}], **extra})


def test_new_install_is_lite_existing_stays_pro(tmp_path):
    cfg = cfg_for(tmp_path / "nou")
    (tmp_path / "nou").mkdir()
    assert edition.init_base(cfg, Database(cfg["database"])) == "lite"   # imaginea SD: prima pornire
    assert read_overlay(cfg)["edition_base"] == "lite"
    cfg["web"]["password"] = "pusă-mai-târziu"
    assert edition.init_base(cfg, Database(cfg["database"])) == "lite"   # hotărât o singură dată
    assert edition.current(cfg, "7K3F-Q9PA") == "lite"

    (tmp_path / "parola").mkdir()
    old = cfg_for(tmp_path / "parola", web={"password": "veche"})
    assert edition.init_base(old, Database(old["database"])) == "pro"    # Pi folosit deja (parolă)
    assert edition.current(old) == "pro"

    (tmp_path / "date").mkdir()
    data = cfg_for(tmp_path / "date")
    db = Database(data["database"])
    db.insert_pack(PackReading(device_id="d1", ts=0, soc=50), ts=1)
    assert edition.init_base(data, db) == "pro"                           # Pi folosit deja (citiri)

    sim = _merge(DEFAULTS, {"database": ":memory:", "devices": [{"id": "s", "type": "sim_daly"}]})
    assert edition.init_base(dict(sim), Database(":memory:")) == "pro"   # simulatorul: Pro
    assert edition.init_base({**sim, "edition": "lite"}, Database(":memory:")) == "lite"


def test_lite_blocks_pro_routes_and_code_unlocks(tmp_path, monkeypatch):
    k, pub = keypair()
    monkeypatch.setattr(edition, "pi_id", lambda paths=None: "7K3F-Q9PA")
    real_ok = edition.code_ok
    monkeypatch.setattr(edition, "code_ok", lambda c, p, keys=None: real_ok(c, p, [pub]))
    code = edition.make_code(k, "7K3F-Q9PA")

    async def steps(c, eng, n):
        info = await (await c.get("/api/info")).json()
        blocked = [(await c.get(u)).status for u in ("/api/inverter/settings", "/api/daly/x/settings",
                                                      "/api/daly/x/params", "/api/debug/inv")]
        clock = await c.post("/api/inverter/clock", json={})
        live = (await c.get("/api/live")).status                         # restul merge
        bad = await c.post("/api/pro/activate", json={"code": edition.make_code(keypair()[0], "7K3F-Q9PA"),
                                                       "app_password": "parola-buna"})
        nopw = await c.post("/api/pro/activate", json={"code": code, "app_password": "gresita"})
        ok = await c.post("/api/pro/activate", json={"code": code, "app_password": "parola-buna"})
        after = await (await c.get("/api/info")).json()
        dbg = (await c.get("/api/debug/inv")).status
        return (info, blocked, (await clock.json()), live, bad.status, nopw.status, ok.status, after, dbg,
                read_overlay(eng.cfg).get("pro_code"), n.sent)

    info, blocked, clock, live, bad, nopw, ok, after, dbg, saved, sent = run(
        {"edition": "lite", "web": {"password": "parola-buna"}}, steps, tmp_path)
    assert info["edition"] == "lite" and info["pi_id"] == "7K3F-Q9PA"
    assert blocked == [403] * 4 and clock["pro_required"] and live == 200
    assert bad == 400 and nopw == 403 and ok == 200
    assert after["edition"] == "pro" and dbg == 200 and saved == code
    assert any("Amper Pro a fost activat" in m for m in sent)


def test_activation_survives_restart(tmp_path, monkeypatch):
    k, pub = keypair()
    monkeypatch.setattr(edition, "pi_id", lambda paths=None: "7K3F-Q9PA")
    real_ok = edition.code_ok
    monkeypatch.setattr(edition, "code_ok", lambda c, p, keys=None: real_ok(c, p, [pub]))
    cfg = cfg_for(tmp_path, edition_base="lite", pro_code=edition.make_code(k, "7K3F-Q9PA"))
    assert edition.current(cfg) == "pro"
    assert edition.current({**cfg, "pro_code": edition.make_code(k, "AAAA-BBBB")}) == "lite"

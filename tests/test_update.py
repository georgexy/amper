"""Actualizările semnate: semnătură, sha256, copia datelor, revenirea automată, API-ul web."""
import asyncio
import base64
import io
import urllib.error
import json
import os
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amper import update as up

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import release  # noqa: E402

REPO = "georgexy/amper"
API = f"https://api.github.com/repos/{REPO}/releases"


def keypair():
    k = Ed25519PrivateKey.generate()
    priv = base64.b64encode(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                            serialization.NoEncryption())).decode()
    return priv, release.public_b64(k)


def fake_source(tmp: Path, ver: str) -> Path:
    """Un „repo” minimal cu versiunea dată."""
    src = tmp / f"src-{ver}"
    (src / "amper").mkdir(parents=True)
    (src / "amper" / "__init__.py").write_text(f'__version__ = "{ver}"\n')
    (src / "web").mkdir()
    (src / "web" / "index.html").write_text(f"<p>{ver}</p>")
    (src / "deploy").mkdir()
    (src / "CHANGELOG.md").write_text(f"# Noutăți\n\n## {ver} — 2026-11-01\n- ceva nou în {ver}\n\n## 0.0.1\n- vechi\n")
    return src


class Env:
    """Un Pi în miniatură: /opt, /etc/amper, /var/lib/amper, GitHub simulat, systemctl simulat."""

    def __init__(self, tmp: Path, cur="0.2.0"):
        self.tmp = tmp
        self.opt = tmp / "opt"
        self.opt.mkdir()
        cur_dir = self.opt / f"amper-{cur}"
        (cur_dir / "amper").mkdir(parents=True)
        (cur_dir / "amper" / "__init__.py").write_text(f'__version__ = "{cur}"\n')
        self.link = self.opt / "amper"
        os.symlink(cur_dir, self.link)
        self.etc = tmp / "etc"
        self.etc.mkdir()
        (self.etc / "config.yaml").write_text("web:\n  password: pw\n")
        self.data = tmp / "data"
        self.data.mkdir()
        self.db = self.data / "amper.db"
        con = sqlite3.connect(self.db)
        con.execute("create table pack_samples (ts int, soc real)")
        con.executemany("insert into pack_samples values (?, ?)", [(i, 50 + i % 40) for i in range(500)])
        con.commit()
        con.close()
        (self.data / "daly-auto.json").write_text('{"daly1": "ble:DL-TEST"}')
        (self.data / "vapid.json").write_text('{"k": 1}')
        (self.data / "settings.yaml").write_text("site_name: Casa\n")
        self.files: dict[str, bytes] = {}
        self.cmds: list[tuple] = []
        self.fetched: list[str] = []
        self.healthy: dict[str, bool] = {}
        self.on_restart = None
        self.notes: list[str] = []
        self.priv, self.pub = keypair()

    def publish(self, ver: str, *, priv=None, tamper=False, prerelease=False, tar=None):
        out = self.tmp / f"dist-{ver}"
        m = release.build(out, priv or self.priv, root=fake_source(self.tmp, ver), mtime=1_800_000_000)
        base = f"https://github.com/{REPO}/releases/download/v{ver}/"
        data = (out / m["tarball"]).read_bytes()
        if tar is not None:
            data = tar
        if tamper:
            data = data[:-10] + b"x" * 10
        self.files[base + m["tarball"]] = data
        self.files[base + "manifest.json"] = (out / "manifest.json").read_bytes()
        self.files[base + "manifest.json.sig"] = (out / "manifest.json.sig").read_bytes()
        rel = {"tag_name": f"v{ver}", "draft": False, "prerelease": prerelease,
               "assets": [{"name": n, "browser_download_url": base + n}
                          for n in (m["tarball"], "manifest.json", "manifest.json.sig")]}
        if not prerelease:
            self.files[API + "/latest"] = json.dumps(rel).encode()
        lst = json.loads(self.files.get(API + "?per_page=15", b"[]"))
        self.files[API + "?per_page=15"] = json.dumps([rel] + lst).encode()
        return m

    def fetch(self, url, accept="", limit=0, progress=None):
        if url not in self.files:
            raise up.UpdateError("Nu există încă nicio versiune publicată pe GitHub.")
        data = self.files[url]
        if progress:                                   # ca descărcarea reală: pe bucăți
            self.fetched.append(url)
            for i in range(1, 5):
                progress(len(data) * i // 4, len(data))
        return data

    def run(self, *cmd):
        self.cmds.append(cmd)
        if cmd[:2] == ("systemctl", "restart") and cmd[2] == "amper" and self.on_restart:
            self.on_restart()

    now = 1_900_000_000

    def updater(self, channel="stable", keys=None, **kw):
        return up.Updater({"database": str(self.db), "update": {"repo": REPO, "channel": channel}},
                          opt_dir=str(self.opt), link=str(self.link), etc_dir=str(self.etc),
                          data_dir=str(self.data), state_dir=str(self.tmp / "state"),
                          keys=[self.pub] if keys is None else keys, fetch=self.fetch, run=self.run,
                          health=lambda v: self.healthy.get(v, True), notify=self.notes.append,
                          clock=lambda: self.now, **kw)

    def snapshot(self):
        con = sqlite3.connect(self.db)
        rows = con.execute("select * from pack_samples order by ts").fetchall()
        con.close()
        return rows, (self.etc / "config.yaml").read_text(), sorted(p.name for p in self.etc.iterdir()), \
            {p.name: p.read_text() for p in list(self.data.glob("*.json")) + list(self.data.glob("*.yaml"))}


# ---------------------------------------------------------------- versiuni
def test_version_order():
    assert up.newer("1.10.0", "1.9.9")
    assert up.newer("1.2.0", "1.2.0-beta.2")
    assert up.newer("1.2.0-beta.10", "1.2.0-beta.9")
    assert not up.newer("1.2.0", "1.2.0")
    with pytest.raises(ValueError):
        up.parse_version("1.2")


# ---------------------------------------------------------------- lansarea
def test_release_of_this_repo_signs_and_verifies(tmp_path):
    priv, pub = keypair()
    m = release.build(tmp_path, priv, mtime=1_800_000_000)
    ver = release.version()
    assert m["version"] == ver and m["notes"]
    mb = (tmp_path / "manifest.json").read_bytes()
    assert up.verify_manifest(mb, (tmp_path / "manifest.json.sig").read_text(), [pub])["sha256"] == m["sha256"]
    names = tarfile.open(tmp_path / m["tarball"]).getnames()
    assert f"amper-{ver}/amper/update.py" in names and f"amper-{ver}/deploy/install.sh" in names
    assert not any("/tests/" in n or "/tools/" in n or "__pycache__" in n for n in names)
    # reproductibilă: același conținut → aceeași arhivă
    assert release.build_tarball(ver, mtime=1_800_000_000) == release.build_tarball(ver, mtime=1_800_000_000)


def test_wrong_key_is_refused(tmp_path):
    other_priv, _ = keypair()
    e = Env(tmp_path)
    e.publish("0.3.0", priv=other_priv)
    before = e.snapshot()
    u = e.updater()
    with pytest.raises(up.UpdateError, match="Semnătura"):
        u.check()
    assert not u.install()
    assert "Semnătura" in u.job["message"] and u.job["state"] == "failed"
    assert os.path.realpath(e.link).endswith("amper-0.2.0")
    assert not any(c[:2] == ("systemctl", "stop") for c in e.cmds)          # nimic oprit
    assert e.snapshot() == before


def test_modified_manifest_is_refused(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    url = f"https://github.com/{REPO}/releases/download/v0.3.0/manifest.json"
    e.files[url] = e.files[url].replace(b'"0.3.0"', b'"0.3.0" ', 1)
    u = e.updater()
    assert not u.install()
    assert "Semnătura" in u.job["message"]


def test_tampered_tarball_is_refused(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0", tamper=True)
    u = e.updater()
    assert not u.install()
    assert "sha256" in u.job["message"]
    assert os.path.realpath(e.link).endswith("amper-0.2.0") and not (e.opt / "amper-0.3.0").exists()


def test_no_public_key_cannot_install(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    u = e.updater(keys=[])
    assert not u.install()
    assert "cheia" in u.job["message"]


def test_unsafe_paths_in_archive(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in ("amper-0.3.0/amper/__init__.py", "amper-0.3.0/../../etc/passwd"):
            ti = tarfile.TarInfo(name)
            ti.size = 3
            tf.addfile(ti, io.BytesIO(b"abc"))
    with pytest.raises(up.UpdateError, match="nepermisă"):
        up.safe_extract(buf.getvalue(), str(tmp_path / "x"), "0.3.0")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("amper-0.3.0/amper/link")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/shadow"
        tf.addfile(ti)
    with pytest.raises(up.UpdateError, match="nepermisă"):
        up.safe_extract(buf.getvalue(), str(tmp_path / "y"), "0.3.0")


# ---------------------------------------------------------------- căutarea
def test_check_notifies_once_and_channels(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    e.publish("0.4.0-beta.1", prerelease=True)
    u = e.updater()
    st = u.check()
    assert st["available"] and st["latest"]["version"] == "0.3.0" and "ceva nou în 0.3.0" in st["latest"]["notes"]
    u.check()
    assert e.notes == ["Versiune nouă detectată. Atinge pentru a actualiza."]      # o singură dată, text scurt
    assert e.updater(channel="beta").check()["latest"]["version"] == "0.4.0-beta.1"


def test_check_without_internet(tmp_path):
    e = Env(tmp_path)
    u = e.updater()
    u.fetch = lambda *a, **k: (_ for _ in ()).throw(up.UpdateError("Fără internet: nu pot ajunge la GitHub."))
    with pytest.raises(up.UpdateError):
        u.check()
    assert u.status()["check_error"].startswith("Fără internet") and u.status()["last_check"]


# ---------------------------------------------------------------- actualizarea
def test_update_ok_keeps_data(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    before = e.snapshot()
    u = e.updater()
    assert u.install(), u.job
    assert os.path.realpath(e.link) == str(e.opt / "amper-0.3.0")
    assert (e.opt / "amper-0.3.0" / "web" / "index.html").read_text() == "<p>0.3.0</p>"
    assert e.snapshot() == before                                   # istoric, config, json: neatinse
    assert (e.data / "pre-update" / "0.2.0" / "amper.db").exists()
    st = u.status()
    assert st["version"] == "0.3.0" and st["previous"]["version"] == "0.2.0" and not st["available"]
    assert st["history"][0]["result"] == "ok"
    assert ("systemctl", "stop", "amper") in e.cmds and ("systemctl", "restart", "amper") in e.cmds
    assert any("actualizat la 0.3.0" in n for n in e.notes)


def test_update_that_does_not_start_rolls_back_code_and_data(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    before = e.snapshot()
    e.healthy["0.3.0"] = False

    def broken_version_touches_data():                              # versiunea nouă strică datele, apoi cade
        con = sqlite3.connect(e.db)
        con.execute("delete from pack_samples where ts > 100")
        con.execute("create table noua (x int)")
        con.commit()
        con.close()
        (e.etc / "config.yaml").write_text("stricat")
        (e.etc / "nou.yaml").write_text("x")
        (e.data / "daly-auto.json").write_text("{}")
        (e.data / "settings.yaml").write_text("stricat: da\n")
    e.on_restart = broken_version_touches_data
    u = e.updater()
    assert not u.install()
    assert u.job["state"] == "rolled_back" and "am revenit la 0.2.0" in u.job["message"]
    assert os.path.realpath(e.link) == str(e.opt / "amper-0.2.0")
    assert not (e.opt / "amper-0.3.0").exists()
    assert e.snapshot() == before                                   # date identice cu cele de dinainte
    assert u.status()["history"][0]["result"] == "rolled_back"
    assert any("nu a pornit" in n for n in e.notes)


def test_manual_rollback_keeps_new_data(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    u = e.updater()
    assert u.install()
    con = sqlite3.connect(e.db)
    con.execute("insert into pack_samples values (9999, 77)")       # date adunate cu versiunea nouă
    con.commit()
    con.close()
    after = e.snapshot()
    u2 = e.updater()                                                # serviciul repornit după actualizare
    assert u2.status()["previous"]["version"] == "0.2.0"
    assert u2.rollback(), u2.job
    assert os.path.realpath(e.link) == str(e.opt / "amper-0.2.0")
    assert e.snapshot() == after
    st = u2.status()
    assert st["version"] == "0.2.0" and st["previous"]["version"] == "0.3.0" and st["available"]
    assert (e.data / "pre-update" / "0.3.0-revenire" / "amper.db").exists()


def test_old_layout_folder_is_migrated(tmp_path):
    """Instalarea veche: /opt/amper e folder, nu legătură."""
    e = Env(tmp_path)
    real = os.path.realpath(e.link)
    os.remove(e.link)
    os.rename(real, e.link)
    e.publish("0.3.0")
    u = e.updater()
    assert u.install(), u.job
    assert os.path.islink(e.link) and os.path.realpath(e.link) == str(e.opt / "amper-0.3.0")
    assert u.status()["previous"]["version"] == "0.2.0" and (e.opt / "amper-0.2.0" / "amper").is_dir()


def test_no_space(tmp_path, monkeypatch):
    e = Env(tmp_path)
    e.publish("0.3.0")
    u = e.updater()
    monkeypatch.setattr(u, "_free_mb", lambda p: 10)
    assert not u.install()
    assert "Spațiu insuficient" in u.job["message"] and not e.cmds


def test_backups_and_versions_are_pruned(tmp_path):
    e = Env(tmp_path)
    t = [1_900_000_000]
    for i, v in enumerate(("0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0")):
        e.publish(v)
        u = e.updater()
        assert u.install(), u.job
        for d in list(e.opt.glob("amper-*")) + list((e.data / "pre-update").glob("*")):
            os.utime(d, (t[0] + i * 10, t[0] + i * 10)) if d.name.endswith(v) else None
    assert len(list((e.data / "pre-update").iterdir())) == up.KEEP_BACKUPS
    assert sorted(p.name for p in e.opt.glob("amper-*")) == ["amper-0.5.0", "amper-0.6.0", "amper-0.7.0"]


# ---------------------------------------------------------------- API-ul web
def run_api(password, steps, monkeypatch, tmp_path):
    from aiohttp import web as aweb
    from aiohttp.test_utils import TestClient, TestServer

    from amper import web
    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.notify import Notifier

    calls = []

    async def status(req):
        assert req.headers["X-Token"] == "tok"
        return aweb.json_response({"version": "0.2.0", "job": {"state": "idle"}})

    async def action(req):
        assert req.headers["X-Token"] == "tok"
        calls.append(req.path)
        return aweb.json_response({"ok": True})

    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "web": {"password": password},
                            "devices": [{"id": "inv", "name": "Growatt", "type": "sim_growatt"}]})
    eng = Engine(cfg, Database(":memory:"), Notifier({}))
    (tmp_path / "token").write_text("tok")

    async def go():
        fake = aweb.Application()                      # serviciul amper-update simulat
        fake.router.add_get("/status", status)
        fake.router.add_post("/{a}", action)
        async with TestServer(fake) as srv:
            monkeypatch.setattr(web, "UPDATE_API", str(srv.make_url("")).rstrip("/"))
            monkeypatch.setattr(web, "UPDATE_TOKEN", str(tmp_path / "token"))
            async with TestClient(TestServer(web.make_app(eng))) as c:
                return await steps(c, calls)
    return asyncio.run(go())


def test_api_update_requires_password_again(monkeypatch, tmp_path):
    async def steps(c, calls):
        health = await (await c.get("/api/health")).json()          # fără parolă (pentru amper-update)
        st = await c.get("/api/update/status")                     # citirea: ca restul aplicației (kiosk)
        # de pe telefon (prin proxy-ul Tailscale), fără cookie: refuzat înainte de parolă
        no_login = await c.post("/api/update/install", json={"app_password": "pw"}, headers={"X-Forwarded-For": "1.2.3.4"})
        wrong = await c.post("/api/update/install", json={"app_password": "altceva"})      # ecranul HDMI
        empty = await c.post("/api/update/rollback", json={})
        await c.post("/api/login", json={"password": "pw"})
        ok = await c.post("/api/update/install", json={"app_password": "pw"}, headers={"X-Forwarded-For": "1.2.3.4"})
        back = await c.post("/api/update/rollback", json={"app_password": "pw"})
        return health, st.status, (await st.json()), no_login.status, wrong.status, empty.status, ok.status, \
            back.status, list(calls)
    health, st, stj, no_login, wrong, empty, ok, back, calls = run_api("pw", steps, monkeypatch, tmp_path)
    from amper import __version__
    assert health == {"ok": True, "version": __version__}
    assert st == 200 and stj["version"] == "0.2.0" and stj["running"] == __version__
    assert no_login == 401 and wrong == 403 and empty == 403 and ok == 200 and back == 200
    assert calls == ["/install", "/rollback"]


def test_api_update_without_app_password(monkeypatch, tmp_path):
    async def steps(c, calls):
        r = await c.post("/api/update/install", json={"app_password": ""})
        return r.status, list(calls)
    assert run_api("", steps, monkeypatch, tmp_path) == (403, [])


def test_local_api_token(tmp_path):
    import urllib.request
    e = Env(tmp_path)
    u = e.updater()
    srv = u.start_local_api(str(tmp_path / "run" / "token"), port=0)
    port = srv.server_address[1]
    tok = (tmp_path / "run" / "token").read_text()
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status")
        r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"X-Token": tok}))
        assert json.loads(r.read())["version"] == "0.2.0"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- descarcă și verifică, apoi „Instalează acum”
def test_download_then_install_now(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    before = e.snapshot()
    u = e.updater()
    seen = []
    orig = u._progress

    def spy(step, label):
        cb = orig(step, label)
        return lambda d, t: (cb(d, t), seen.append((step, u.job["progress"])))
    u._progress = spy
    assert u.download(), u.job
    assert u.job["state"] == "downloaded" and u.job["progress"] == 100
    dl = [p for s_, p in seen if s_ == "download"]
    ck = [p for s_, p in seen if s_ == "check"]
    assert len(dl) == 4 and dl == sorted(dl) and dl[0] < 50 and dl[-1] == 100      # procentul crește până la 100
    assert ck == sorted(ck) and ck[-1] == 100
    assert not e.cmds and os.path.realpath(e.link).endswith("amper-0.2.0")     # nimic instalat încă
    st = u.status()
    assert st["staged"]["version"] == "0.3.0" and st["available"]
    # „Instalează acum”: arhiva nu se mai descarcă, doar se reverifică
    n = len(e.fetched)
    assert u.install(), u.job
    assert len(e.fetched) == n and os.path.realpath(e.link).endswith("amper-0.3.0")
    assert e.snapshot() == before and u.status()["staged"] is None
    assert not (tmp_path / "state" / "staged").exists()


def test_staged_file_tampered_is_refused(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    u = e.updater()
    assert u.download()
    path = u.state["staged"]["path"]
    with open(path, "r+b") as f:                   # cineva modifică arhiva de pe card
        f.seek(100)
        f.write(b"XXXX")
    assert not u.install()
    assert "sha256" in u.job["message"] and os.path.realpath(e.link).endswith("amper-0.2.0")


def test_download_failure_keeps_everything(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0", tamper=True)
    u = e.updater()
    assert not u.download()
    assert u.job["state"] == "failed" and "sha256" in u.job["message"]
    assert u.status()["staged"] is None and not e.cmds


def test_command_line_install_fallback(tmp_path, monkeypatch, capsys):
    """`sudo amper update --install`: instalarea fără aplicație (dacă pagina nu merge)."""
    e = Env(tmp_path)
    e.publish("0.3.0")
    u = e.updater()
    monkeypatch.setattr(up, "Updater", lambda cfg: u)
    monkeypatch.setattr("amper.config.load_config", lambda path: {})
    import pytest as _pt
    with _pt.raises(SystemExit) as ex:
        up.main(["--install"])
    assert ex.value.code == 0 and os.path.realpath(e.link).endswith("amper-0.3.0")
    assert ("systemctl", "stop", "amper-update") in e.cmds and e.cmds[-1] == ("systemctl", "start", "amper-update")
    assert "0.3.0" in capsys.readouterr().out


# ---------------------------------------------------------------- paznicul, revenirea de pe card, urgente
class Svc:
    """Serviciul amper simulat pentru paznic: starea systemd și dacă răspunde la /api/health."""
    def __init__(self):
        self.state, self.up = "active", True


def test_watchdog_restarts_then_rolls_back_recent_update(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    svc = Svc()
    u = e.updater(ping=lambda: svc.up, svc_state=lambda: svc.state)
    assert u.install()
    assert u.watchdog_tick() is None                               # răspunde: nimic
    svc.up = False                                                 # 0.3.0 se blochează după o vreme
    assert u.watchdog_tick() is None                               # abia a observat
    e.now += 200
    assert u.watchdog_tick() is None                               # încă nu 5 minute
    e.now += 120
    assert u.watchdog_tick() == "restart"
    assert e.cmds[-1] == ("systemctl", "restart", "amper") and any("l-am repornit" in n for n in e.notes)
    e.now += 310
    assert u.watchdog_tick() == "rollback"                         # tot nu merge: revine la 0.2.0
    assert os.path.realpath(e.link) == str(e.opt / "amper-0.2.0")
    assert u.state["bad_version"] == "0.3.0" and any("am revenit singur la 0.2.0" in n for n in e.notes)


def test_watchdog_only_restarts_old_install_and_leaves_manual_stop(tmp_path):
    e = Env(tmp_path)
    svc = Svc()
    u = e.updater(ping=lambda: svc.up, svc_state=lambda: svc.state)
    svc.state = "inactive"                                         # oprit de mână (amper scan)
    for _ in range(3):
        assert u.watchdog_tick() is None
        e.now += 400
    svc.state, svc.up = "failed", False                           # cade, fără actualizare recentă
    acts = []
    for _ in range(4):
        acts.append(u.watchdog_tick())
        e.now += 310
    assert acts == [None, "restart", "restart", "restart"] and os.path.realpath(e.link) == str(e.opt / "amper-0.2.0")
    assert sum("l-am repornit" in n for n in e.notes) == 1         # o singură notificare pe incident
    svc.state, svc.up = "active", True
    assert u.watchdog_tick() is None and any("răspunde din nou" in n for n in e.notes)


def test_card_rollback_file(tmp_path):
    e = Env(tmp_path)
    e.publish("0.3.0")
    boot = tmp_path / "boot"
    boot.mkdir()
    u = e.updater(boot_dirs=[str(boot)])
    assert u.install()
    assert u.card_rollback() is None
    (boot / "Revenire.txt").write_text("")
    assert u.card_rollback() == "rollback"
    assert os.path.realpath(e.link) == str(e.opt / "amper-0.2.0") and not list(boot.iterdir())
    assert any("cerută de pe card" in n for n in e.notes)


def test_urgent_release_installs_by_itself(tmp_path):
    e = Env(tmp_path)
    u = e.updater()
    out = tmp_path / "dist-urg"
    m = release.build(out, e.priv, root=fake_source(tmp_path, "0.3.1"), mtime=1_800_000_000, urgent=True)
    assert m["urgent"] and up.verify_manifest((out / "manifest.json").read_bytes(),
                                              (out / "manifest.json.sig").read_text(), [e.pub])["urgent"]
    e.publish("0.3.0")
    u.check(auto=True)
    assert not u.urgent_pending() and any("Versiune nouă" in n for n in e.notes)
    e.files.clear()
    base = f"https://github.com/{REPO}/releases/download/v0.3.1/"
    for n in (m["tarball"], "manifest.json", "manifest.json.sig"):
        e.files[base + n] = (out / n).read_bytes()
    e.files[API + "/latest"] = json.dumps({"tag_name": "v0.3.1", "draft": False, "assets": [
        {"name": n, "browser_download_url": base + n} for n in (m["tarball"], "manifest.json", "manifest.json.sig")]}).encode()
    e.now += up.URGENT_EVERY
    assert u.due()                                                  # se caută la 6 ore, nu la 7 zile
    u.check(auto=True)
    assert u.urgent_pending() and any("urgentă" in n for n in e.notes)
    u.state["bad_version"] = "0.3.1"                               # a căzut deja aici: nu insistă
    assert not u.urgent_pending()

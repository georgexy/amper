"""Acces de oriunde (Tailscale + Funnel), cu un `tailscale` simulat."""
import json
import threading
import time
from types import SimpleNamespace

from amper.remote import Remote


class FakeProc:
    """Ca `tailscale up` / `tailscale funnel`: scrie linkul de aprobare, apoi așteaptă până „aprobă” utilizatorul."""

    def __init__(self, lines, rc=0):
        self.lines, self.rc, self.approved = lines, rc, threading.Event()

    @property
    def stdout(self):
        for ln in self.lines:
            yield ln
        self.approved.wait(5)

    def wait(self):
        return self.rc


class FakeTS:
    def __init__(self, state="NeedsLogin", name="", funnel=False):
        self.state, self.name, self.funnel, self.calls, self.procs = state, name, funnel, [], []

    def run(self, *a):
        self.calls.append(a)
        if a[1:3] == ("status", "--json"):
            out = {"BackendState": self.state, "Self": {"DNSName": self.name}}
        elif a[1:4] == ("funnel", "status", "--json"):
            out = {"AllowFunnel": {f"{self.name}:443": True}} if self.funnel else {}
        else:
            out = {}
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)

    def popen(self, *a):
        self.calls.append(a)
        url = ("https://login.tailscale.com/a/abc123" if a[1] == "up"
               else "https://login.tailscale.com/f/funnel?node=n1")
        p = FakeProc([f"To authenticate, visit:\n", f"\t{url}\n"])
        self.procs.append(p)
        return p


def remote(ts):
    return Remote(port=8080, run=ts.run, popen=ts.popen, which=lambda n: "/usr/bin/tailscale")


def test_not_installed():
    assert Remote(which=lambda n: None).status() == {"installed": False}


def test_login_shows_link_then_running():
    ts = FakeTS()
    r = remote(ts)
    assert r.status()["state"] == "NeedsLogin"
    job = r.login()
    assert job["state"] == "running" and job["url"] == "https://login.tailscale.com/a/abc123"
    assert ("tailscale", "up", "--hostname=amper") in ts.calls
    # utilizatorul se loghează în browser → comanda se termină
    ts.state, ts.name = "Running", "amper.tail123.ts.net."
    ts.procs[0].approved.set()
    for _ in range(50):
        if r.job["state"] != "running":
            break
        time.sleep(0.02)
    st = r.status()
    assert r.job["state"] == "ok" and st["url"] == "https://amper.tail123.ts.net" and not st["funnel"]


def test_funnel_on_waits_for_approval_then_off_goes_back_to_serve():
    ts = FakeTS(state="Running", name="amper.tail123.ts.net.")
    r = remote(ts)
    job = r.funnel(True)
    assert job["url"].startswith("https://login.tailscale.com/f/funnel")
    assert ("tailscale", "funnel", "--bg", "--https=443", "http://127.0.0.1:8080") in ts.calls
    assert r.funnel(True)["state"] == "running"               # a doua apăsare nu pornește încă o comandă
    assert sum(1 for c in ts.calls if c[1:3] == ("funnel", "--bg")) == 1
    ts.funnel = True
    ts.procs[0].approved.set()
    for _ in range(50):
        if r.job["state"] != "running":
            break
        time.sleep(0.02)
    assert r.job["state"] == "ok" and r.status()["funnel"]
    r.funnel(False)
    assert ("tailscale", "funnel", "reset") in ts.calls
    assert ("tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8080") in ts.calls


def test_failed_command_reports_error():
    ts = FakeTS(state="Running", name="amper.x.ts.net.")
    def failing(*a):
        p = FakeProc(["error: Funnel not available\n"], rc=1)
        p.approved.set()
        return p
    ts.popen = failing
    r = remote(ts)
    r.funnel(True)
    for _ in range(50):
        if r.job["state"] != "running":
            break
        time.sleep(0.02)
    assert r.job["state"] == "failed" and "Funnel not available" in r.job["message"]


def test_web_funnel_requires_password_and_updates_push_address(monkeypatch, tmp_path):
    import asyncio

    from aiohttp import web as aweb
    from aiohttp.test_utils import TestClient, TestServer

    from amper import web
    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.notify import Notifier
    calls = []

    async def remote_status(req):
        return aweb.json_response({"installed": True, "state": "Running", "url": "https://amper.t.ts.net", "funnel": False})

    async def remote_post(req):
        calls.append((req.path, await req.json() if req.can_read_body else None))
        return aweb.json_response({"installed": True, "state": "Running", "url": "https://amper.t.ts.net", "funnel": True})
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "web": {"password": "pw"},
                            "devices": [{"id": "inv", "type": "sim_growatt"}]})
    eng = Engine(cfg, Database(":memory:"), Notifier({}))
    eng.notifier.webpush = SimpleNamespace(subject="https://amper.local", enabled=False)
    (tmp_path / "token").write_text("tok")

    async def go():
        fake = aweb.Application()
        fake.router.add_get("/remote", remote_status)
        fake.router.add_post("/remote/{a}", remote_post)
        async with TestServer(fake) as srv:
            monkeypatch.setattr(web, "UPDATE_API", str(srv.make_url("")).rstrip("/"))
            monkeypatch.setattr(web, "UPDATE_TOKEN", str(tmp_path / "token"))
            async with TestClient(TestServer(web.make_app(eng))) as c:
                st = await (await c.get("/api/remote")).json()
                no = await c.post("/api/remote/funnel", json={"on": True})
                ok = await c.post("/api/remote/funnel", json={"on": True, "app_password": "pw"})
                return st, no.status, ok.status
    st, no, ok = asyncio.run(go())
    assert st["url"] == "https://amper.t.ts.net" and no == 403 and ok == 200
    assert calls == [("/remote/funnel", {"on": True})]
    assert eng.notifier.webpush.subject == "https://amper.t.ts.net"          # notificările pe iPhone: adresa reală
    assert any("Accesul de oriunde a fost pornit" in m["message"] for m in eng.notifier.sent)


def test_slow_tailscale_does_not_block_status():
    """`tailscale` care nu răspunde: starea vine oricum (fără blocaj), cu funnel oprit."""
    import subprocess

    def slow(*a):
        raise subprocess.TimeoutExpired(a, 10)
    r = Remote(run=slow, popen=lambda *a: None, which=lambda n: "/x")
    st = r.status()
    assert st["installed"] and st["state"] == "NoState" and st["funnel"] is False
    assert r.funnel(False)["state"] == "failed"

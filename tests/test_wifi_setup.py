"""Hotspotul de configurare WiFi: lista rețelelor din nmcli, pagina, decizia de pornire/oprire."""
import pytest

from amper import wifi_setup as ws


def test_parse_scan_dedup_and_escapes():
    out = "\n".join([
        "Casa:80:WPA2",
        "Casa:49:WPA2",
        "Vecin\\:2G:39:WPA1 WPA2",      # „:” din nume, scăpat de nmcli
        ":70:WPA2",                      # rețea ascunsă, fără nume
        "Cafenea:55:",                   # deschisă
        "Amper-Setup:90:WPA2",           # propriul hotspot nu apare
    ])
    nets = ws.parse_scan(out)
    assert [n["ssid"] for n in nets] == ["Casa", "Cafenea", "Vecin:2G"]
    assert nets[0]["signal"] == 80 and nets[0]["secure"]
    assert not nets[1]["secure"]


def test_page_escapes_names():
    w = ws.WifiSetup({"password": "12345678"})
    w.networks = [{"ssid": '<script>"x"', "signal": 60, "secure": True, "security": "WPA2"}]
    w.message = "Parola <greșită>"
    p = ws.page(w)
    assert "<script>" not in p and "&lt;script&gt;" in p and "Parola &lt;greșită&gt;" in p


@pytest.fixture
def setup(monkeypatch):
    w = ws.WifiSetup({"password": "12345678", "offline_minutes": 2})
    w.calls = []
    monkeypatch.setattr(w, "start_ap", lambda: (w.calls.append("start"), setattr(w, "ap_on", True)))
    monkeypatch.setattr(w, "stop_ap", lambda: (w.calls.append("stop"), setattr(w, "ap_on", False)))
    monkeypatch.setattr(w, "ap_clients", lambda: 0)
    monkeypatch.setattr(w, "try_known_networks", lambda wait=30: False)   # nicio rețea știută cu internet
    return w


def test_hotspot_only_after_offline_minutes_and_stops_when_online(setup):
    w = setup
    w.tick(True, 0)
    w.tick(False, 10)
    w.tick(False, 100)
    assert w.calls == []                     # sub 2 minute fără internet: nimic
    w.tick(False, 131)
    assert w.calls == ["start"]
    w.tick(True, 140)
    assert w.calls == ["start", "stop"]      # a revenit internetul (orice cale): hotspot oprit
    w.tick(False, 150)
    assert w.calls == ["start", "stop"]      # numărătoarea începe din nou


def test_short_outage_never_starts_hotspot(setup):
    w = setup
    for t, online in [(0, False), (60, False), (100, True), (160, False), (250, False), (270, True)]:
        w.tick(online, t)
    assert w.calls == []


def test_idle_hotspot_retries_known_networks(setup, monkeypatch):
    w = setup
    w.tick(False, 0)
    w.tick(False, 130)
    w.tick(False, 200)
    assert w.calls == ["start"]
    monkeypatch.setattr(w, "try_known_networks", lambda wait=30: True)   # între timp a revenit routerul
    w.tick(False, 500)                       # 5 minute fără nimeni la hotspot: încearcă rețelele știute
    assert w.calls == ["start", "stop"] and not w.ap_on


class FakeNM:
    """Imită nmcli: profilurile salvate și rețeaua activă."""

    def __init__(self, active="wifi-Casa", works=True):
        self.profiles = {"wifi-Casa": 10}
        self.active, self.works, self.calls = active, works, []

    def __call__(self, *args, timeout=30, check=False):
        import subprocess
        self.calls.append(args)
        out, rc = "", 0
        if args[:2] == ("-t", "-f") and "--active" in args:
            out = f"{self.active}:802-11-wireless:wlan0\n" if self.active else ""
        elif args[:2] == ("connection", "add"):
            self.profiles[args[args.index("con-name") + 1]] = 20
        elif args[:2] == ("connection", "delete"):
            self.profiles.pop(args[2], None)
        elif args[:2] == ("connection", "up"):
            if args[2] == "wifi-Casa" or self.works:
                self.active = args[2]
            else:
                rc = 4
        return subprocess.CompletedProcess(args, rc, out, "")


def test_switch_to_new_network(monkeypatch):
    nm = FakeNM()
    monkeypatch.setattr(ws, "nmcli", nm)
    monkeypatch.setattr(ws.WifiSetup, "wait_internet", staticmethod(lambda s: True))
    w = ws.WifiSetup({"password": "12345678"})
    w.switch("Vecin", "parola123")
    assert w.job["state"] == "ok" and nm.active == "wifi-Vecin" and "wifi-Vecin" in nm.profiles


def test_switch_failure_returns_to_previous_network(monkeypatch):
    nm = FakeNM(works=False)
    monkeypatch.setattr(ws, "nmcli", nm)
    monkeypatch.setattr(ws.WifiSetup, "wait_internet", staticmethod(lambda s: True))
    w = ws.WifiSetup({"password": "12345678"})
    w.switch("Vecin", "gresita12")
    assert w.job["state"] == "failed" and "Casa" in w.job["message"]
    assert nm.active == "wifi-Casa" and "wifi-Vecin" not in nm.profiles     # înapoi pe rețeaua veche


def test_switch_without_internet_also_returns(monkeypatch):
    nm = FakeNM()
    monkeypatch.setattr(ws, "nmcli", nm)
    monkeypatch.setattr(ws.WifiSetup, "wait_internet", staticmethod(lambda s: False))
    w = ws.WifiSetup({"password": "12345678"})
    w.switch("FaraNet", "parola123")
    assert w.job["state"] == "failed" and "nu are internet" in w.job["message"] and nm.active == "wifi-Casa"


def test_offline_tries_other_known_networks_before_hotspot(setup, monkeypatch):
    w = setup
    monkeypatch.setattr(w, "try_known_networks", lambda wait=30: True)
    w.tick(False, 0)
    w.tick(False, 130)
    assert w.calls == [] and not w.ap_on            # altă rețea știută avea internet: fără hotspot


class FakeNM2(FakeNM):
    """Trei rețele salvate; doar unele au internet."""

    def __init__(self, internet):
        super().__init__(active="wifi-Acasa")
        self.profiles = {"wifi-Acasa": 20, "wifi-Acasa_5G": 10, "wifi-Departe": 5}
        self.internet = internet

    def __call__(self, *args, timeout=30, check=False):
        import subprocess
        if args[:2] == ("-t", "-f") and "AUTOCONNECT-PRIORITY" in args[2]:
            self.calls.append(args)
            out = "".join(f"{n}:802-11-wireless:{p}\n" for n, p in self.profiles.items()) + "lo:loopback:0\n"
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[:2] == ("-g", "802-11-wireless.ssid"):
            return subprocess.CompletedProcess(args, 0, args[4].removeprefix("wifi-") + "\n", "")
        if args[:2] == ("connection", "modify"):
            self.calls.append(args)
            self.profiles[args[2]] = int(args[4])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("connection", "up"):
            self.calls.append(args)
            self.active = args[2]
            return subprocess.CompletedProcess(args, 0, "", "")
        return super().__call__(*args, timeout=timeout, check=check)


def test_try_known_networks_finds_the_one_with_internet(monkeypatch):
    nm = FakeNM2(internet={"wifi-Acasa_5G"})
    monkeypatch.setattr(ws, "nmcli", nm)
    w = ws.WifiSetup({"password": "12345678"})
    monkeypatch.setattr(w, "scan", lambda: [{"ssid": "Acasa"}, {"ssid": "Acasa_5G"}])     # „Departe” nu e în zonă
    monkeypatch.setattr(w, "wait_internet", lambda s: nm.active in nm.internet)
    assert w.try_known_networks()
    ups = [c[2] for c in nm.calls if c[:2] == ("connection", "up")]
    assert ups == ["wifi-Acasa_5G"]                           # cea de acum (fără internet) ultima, „Departe” deloc
    assert nm.profiles["wifi-Acasa_5G"] == 20 and nm.profiles["wifi-Acasa"] == 10


def test_try_known_networks_none_works(monkeypatch):
    nm = FakeNM2(internet=set())
    monkeypatch.setattr(ws, "nmcli", nm)
    w = ws.WifiSetup({"password": "12345678"})
    monkeypatch.setattr(w, "scan", lambda: [{"ssid": "Acasa"}, {"ssid": "Acasa_5G"}])
    monkeypatch.setattr(w, "wait_internet", lambda s: False)
    assert not w.try_known_networks()
    assert [c[2] for c in nm.calls if c[:2] == ("connection", "up")] == ["wifi-Acasa_5G", "wifi-Acasa"]


def test_after_connect_hotspot_shows_home_address_then_goes_home(setup, monkeypatch):
    """Rețeaua aleasă merge → hotspotul revine 3 minute cu adresa de acasă → „Am notat” → Pi-ul revine acasă."""
    from types import SimpleNamespace
    w = setup
    calls = []

    def nm(*a, timeout=30, check=False):
        calls.append(a)
        out = "192.168.1.57/24" if a[:2] == ("-g", "IP4.ADDRESS") else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(ws, "nmcli", nm)
    monkeypatch.setattr(ws.WifiSetup, "wait_internet", staticmethod(lambda s: True))
    monkeypatch.setattr(ws.socket, "gethostname", lambda: "amper")
    w.ap_on = True
    w.connect("Casa", "parola123")
    assert w.calls == ["stop", "start"] and w.result["ip"] == "192.168.1.57"
    page = ws.result_page(w.result, 8080)
    assert "http://192.168.1.57:8080" in page and "http://amper.local:8080" in page and "Casa" in page
    t0 = w.result["until"] - ws.ANNOUNCE_S
    w.tick(False, t0 + 60)                       # fără internet (e hotspot): nu se schimbă nimic
    assert w.calls == ["stop", "start"] and w.result
    w.result["done"] = True                      # „Am notat”
    w.tick(False, t0 + 70)
    assert w.calls == ["stop", "start", "stop"] and w.result is None
    assert ("connection", "up", "wifi-Casa") in calls


def test_home_address_shown_at_most_3_minutes(setup, monkeypatch):
    w = setup
    monkeypatch.setattr(w, "finish_announce", lambda: (w.calls.append("home"), setattr(w, "result", None)))
    w.result = {"ssid": "Casa", "ip": "192.168.1.57", "profile": "wifi-Casa", "until": 1000.0}
    w.tick(False, 999)
    assert w.calls == []
    w.tick(False, 1000)
    assert w.calls == ["home"]

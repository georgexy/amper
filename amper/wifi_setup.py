"""Configurarea WiFi de pe telefon: hotspotul „Amper-Setup”, pornit doar când Pi-ul nu are internet.

Rulează ca root (serviciul amper-wifi), separat de amper, pentru că lucrează cu NetworkManager (nmcli).

  - La fiecare 15 s verifică internetul (o conexiune TCP spre câteva servere cunoscute).
  - Dacă nu e internet `offline_minutes` minute la rând (prin cablu sau WiFi), pornește hotspotul
    (SSID și parolă din config, secțiunea wifi_setup) și pagina de configurare la http://10.42.0.1
    (orice adresă cerută de telefon duce acolo, deci pagina se deschide singură).
  - Pagina arată rețelele WiFi din jur; alegi una, scrii parola, iar Pi-ul se conectează.
  - Când revine internetul (rețeaua aleasă, cablul băgat, routerul repornit), hotspotul se oprește.
    Dacă nimeni nu e conectat la hotspot, la 5 minute îl oprește scurt și încearcă rețelele știute.
Bluetooth-ul nu e folosit deloc (rămâne pentru modulele Daly).
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("amper.wifi")

AP_CON = "amper-setup"
AP_IP = "10.42.0.1"
IFACE = "wlan0"
DNSMASQ_CONF = "/etc/NetworkManager/dnsmasq-shared.d/amper-portal.conf"
CHECK_HOSTS = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)]


def nmcli(*args, timeout: float = 30, check: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip() or f"nmcli {args[0]} a eșuat")
    return p


def has_internet(timeout: float = 4) -> bool:
    for host, port in CHECK_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def split_terse(line: str) -> list[str]:
    """Desparte o linie `nmcli -t` (câmpuri cu „:”, iar „\\:” e un „:” din valoare)."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def parse_scan(text: str) -> list[dict]:
    """Rețelele din `nmcli -t -f SSID,SIGNAL,SECURITY device wifi list`: una pe SSID, cu semnalul cel mai bun."""
    best: dict[str, dict] = {}
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) < 3 or not f[0] or f[0] == "Amper-Setup":
            continue
        ssid, sig, sec = f[0], int(f[1] or 0), f[2].strip()
        if ssid not in best or sig > best[ssid]["signal"]:
            best[ssid] = dict(ssid=ssid, signal=sig, secure=bool(sec) and sec != "--", security=sec)
    return sorted(best.values(), key=lambda d: -d["signal"])


class WifiSetup:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ssid = cfg.get("ssid") or "Amper-Setup"
        self.password = str(cfg.get("password") or "")
        self.offline_after = float(cfg.get("offline_minutes", 2)) * 60
        self.ap_on = False
        self.networks: list[dict] = []
        self.message = ""                      # rezultatul ultimei încercări, arătat pe pagină
        self.lock = threading.Lock()
        self.server = None

    # ---------- rețele ----------
    def scan(self) -> list[dict]:
        nmcli("device", "wifi", "rescan", "ifname", IFACE, timeout=20)
        time.sleep(3)
        p = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", IFACE, timeout=20)
        found = parse_scan(p.stdout)
        if found:
            self.networks = found
        return self.networks

    def ap_clients(self) -> int:
        """Câte telefoane sunt conectate la hotspot (după tabela ARP a interfeței)."""
        try:
            with open("/proc/net/arp") as f:
                return sum(1 for line in f.readlines()[1:] if line.split()[-1] == IFACE and line.split()[2] != "0x0")
        except OSError:
            return 0

    # ---------- hotspot ----------
    def start_ap(self) -> None:
        if len(self.password) < 8:
            raise RuntimeError("parola hotspotului (wifi_setup.password) trebuie să aibă cel puțin 8 caractere")
        try:
            self.scan()                         # înainte de hotspot: în modul hotspot scanarea nu merge mereu
        except Exception as e:
            log.warning("scanare WiFi: %s", e)
        os.makedirs(os.path.dirname(DNSMASQ_CONF), exist_ok=True)
        with open(DNSMASQ_CONF, "w") as f:      # orice nume → Pi-ul, ca telefonul să deschidă singur pagina
            f.write(f"address=/#/{AP_IP}\n")
        nmcli("connection", "delete", AP_CON, timeout=15)
        nmcli("connection", "add", "type", "wifi", "ifname", IFACE, "con-name", AP_CON, "autoconnect", "no",
              "ssid", self.ssid, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
              "ipv4.method", "shared", "ipv4.addresses", f"{AP_IP}/24", "ipv6.method", "disabled",
              "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", self.password, check=True)
        nmcli("connection", "up", AP_CON, timeout=40, check=True)
        self.ap_on = True
        self.start_portal()
        log.warning("fără internet: am pornit hotspotul %s (pagina: http://%s)", self.ssid, AP_IP)

    def stop_ap(self) -> None:
        nmcli("connection", "down", AP_CON, timeout=20)
        nmcli("connection", "delete", AP_CON, timeout=15)
        try:
            os.remove(DNSMASQ_CONF)
        except OSError:
            pass
        self.ap_on = False
        self.stop_portal()
        log.info("hotspotul %s e oprit", self.ssid)

    def known_networks(self) -> list[tuple[str, str, int]]:
        """Rețelele WiFi salvate: (profil, SSID, prioritate), cea mai prioritară prima."""
        out = []
        p = nmcli("-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY", "connection", "show", timeout=10)
        for f in (split_terse(x) for x in p.stdout.splitlines()):
            if len(f) >= 3 and f[1] == "802-11-wireless" and f[0] != AP_CON:
                ssid = nmcli("-g", "802-11-wireless.ssid", "connection", "show", f[0], timeout=10).stdout.strip()
                out.append((f[0], ssid or f[0].removeprefix("wifi-"), int(f[2] or 0)))
        return sorted(out, key=lambda x: -x[2])

    def try_known_networks(self, wait: float = 30) -> bool:
        """Încearcă pe rând rețelele știute aflate în zonă, până la una CU internet. Cea care merge devine prima
        (prioritate 20, celelalte 10), ca să fie aleasă și data viitoare. True dacă a revenit internetul."""
        known = self.known_networks()
        try:
            visible = {n["ssid"] for n in self.scan()}
        except Exception:
            visible = set()
        current = self.active_wifi()
        # întâi cele vizibile, în ordinea priorității; cea de acum (fără internet) ultima
        order = sorted(known, key=lambda k: (k[1] not in visible, k[0] == current, -k[2]))
        for name, ssid, _prio in order:
            if visible and ssid not in visible:
                continue
            log.info("încerc rețeaua știută „%s”", ssid)
            if nmcli("connection", "up", name, timeout=45).returncode == 0 and self.wait_internet(wait):
                for other, _s, prio in known:
                    want = 20 if other == name else 10
                    if prio != want:
                        nmcli("connection", "modify", other, "connection.autoconnect-priority", str(want), timeout=10)
                log.warning("internet găsit pe rețeaua „%s”", ssid)
                return True
        return False

    def connect(self, ssid: str, password: str) -> None:
        """Salvează rețeaua aleasă, oprește hotspotul și se conectează; la eșec repornește hotspotul."""
        with self.lock:
            name = f"wifi-{ssid}"
            nmcli("connection", "delete", name, timeout=15)
            args = ["connection", "add", "type", "wifi", "ifname", IFACE, "con-name", name, "ssid", ssid,
                    "connection.autoconnect", "yes", "connection.autoconnect-priority", "10"]
            if password:
                args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
            p = nmcli(*args)
            if p.returncode != 0:
                self.message = f"Nu am putut salva rețeaua „{ssid}”: {(p.stderr or p.stdout).strip()}"
                return
            self.stop_ap()
            up = nmcli("connection", "up", name, timeout=60)
            ok = up.returncode == 0 and self.wait_internet(40)
            if ok:
                self.message = ""
                log.warning("conectat la WiFi „%s”", ssid)
                return
            log.warning("conectarea la „%s” a eșuat: %s", ssid, (up.stderr or up.stdout).strip())
            nmcli("connection", "delete", name, timeout=15)       # parolă greșită: nu o păstrăm
            self.message = (f"Nu m-am putut conecta la „{ssid}”. Verificați parola și încercați din nou."
                            if up.returncode != 0 else f"M-am conectat la „{ssid}”, dar rețeaua nu are internet.")
            self.start_ap()

    @staticmethod
    def wait_internet(seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if has_internet():
                return True
            time.sleep(4)
        return False

    # ---------- bucla ----------
    offline_since: float | None = None
    ap_since = 0.0

    def tick(self, online: bool, now: float) -> None:
        """O verificare: pornește/oprește hotspotul după starea internetului."""
        if self.lock.locked():                  # o conectare cerută de pe pagină e în curs
            return
        if online:
            self.offline_since = None
            if self.ap_on:
                self.stop_ap()
        elif not self.ap_on:
            if self.offline_since is None:
                self.offline_since = now
            if now - self.offline_since >= self.offline_after:
                # întâi celelalte rețele știute din zonă; hotspotul doar dacă niciuna nu are internet
                with self.lock:
                    found = self.try_known_networks()
                    if not found:
                        self.start_ap()
                if found:
                    self.offline_since = None
                else:
                    self.ap_since = now
        elif now - self.ap_since > 300 and self.ap_clients() == 0:
            # nimeni la hotspot de 5 minute: poate a revenit rețeaua știută (router repornit)
            with self.lock:
                self.stop_ap()
                if not self.try_known_networks():
                    self.start_ap()
            self.ap_since = time.monotonic()

    def run(self) -> None:
        nmcli("connection", "delete", AP_CON, timeout=15)        # rămas de la o rulare întreruptă
        while True:
            try:
                self.tick(has_internet(), time.monotonic())
            except Exception:
                log.exception("eroare în bucla WiFi")
            time.sleep(15)

    # ---------- schimbarea rețelei din aplicație (Setări → Info), cu întoarcere la cea veche ----------
    job = {"state": "idle", "message": "", "ssid": ""}

    def active_wifi(self) -> str | None:
        p = nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active", timeout=10)
        for f in (split_terse(x) for x in p.stdout.splitlines()):
            if len(f) >= 3 and f[1] == "802-11-wireless" and f[2] == IFACE and f[0] != AP_CON:
                return f[0]
        return None

    def switch(self, ssid: str, password: str) -> None:
        """Trece pe altă rețea WiFi. Dacă nu merge (parolă greșită, fără internet), revine la rețeaua de dinainte."""
        with self.lock:
            prev = self.active_wifi()
            name = f"wifi-{ssid}"
            self.job = {"state": "connecting", "message": f"Mă conectez la „{ssid}”…", "ssid": ssid}
            if name == prev:
                self.job = {"state": "ok", "message": f"Pi-ul e deja pe „{ssid}”.", "ssid": ssid}
                return
            nmcli("connection", "delete", name, timeout=15)
            args = ["connection", "add", "type", "wifi", "ifname", IFACE, "con-name", name, "ssid", ssid,
                    "connection.autoconnect", "yes", "connection.autoconnect-priority", "20"]
            if password:
                args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
            if nmcli(*args).returncode != 0:
                self.job = {"state": "failed", "message": f"Nu am putut salva rețeaua „{ssid}”.", "ssid": ssid}
                return
            up = nmcli("connection", "up", name, timeout=60)
            if up.returncode == 0 and self.wait_internet(40):
                if prev:                                   # rețeaua nouă devine prima la pornire
                    nmcli("connection", "modify", prev, "connection.autoconnect-priority", "10", timeout=10)
                self.job = {"state": "ok", "message": f"Conectat la „{ssid}”, cu internet.", "ssid": ssid}
                log.warning("rețea WiFi schimbată din aplicație: %s → %s", prev, ssid)
                return
            why = "parola greșită sau semnal prea slab" if up.returncode != 0 else "rețeaua nu are internet"
            log.warning("schimbarea pe „%s” a eșuat (%s); revin la %s", ssid, why, prev)
            nmcli("connection", "delete", name, timeout=15)
            if prev:
                nmcli("connection", "up", prev, timeout=60)
            self.job = {"state": "failed", "ssid": ssid,
                        "message": f"Nu m-am putut conecta la „{ssid}” ({why}). Am revenit la „{(prev or '').removeprefix('wifi-')}”."}

    def start_local_api(self, token_path: str) -> None:
        """API local (127.0.0.1:8091) pentru serverul Amper, protejat cu un token citibil doar de grupul amper."""
        import grp
        import secrets
        token = secrets.token_hex(16)
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(token)
        try:
            gid = grp.getgrnam("amper").gr_gid
            os.chown(token_path, 0, gid)
            os.chown(os.path.dirname(token_path), 0, gid)
            os.chmod(os.path.dirname(token_path), 0o750)
        except (KeyError, OSError):
            pass
        os.chmod(token_path, 0o640)
        setup = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, data, code=200):
                b = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _auth(self) -> bool:
                if self.headers.get("X-Token") != token:
                    self._json({"error": "neautorizat"}, 403)
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path.startswith("/scan"):
                    try:
                        nets = setup.scan()
                    except Exception as e:
                        return self._json({"error": str(e)}, 500)
                    return self._json({"networks": nets, "active": setup.active_wifi(), "hotspot": setup.ap_on})
                return self._json({**setup.job, "active": setup.active_wifi(), "hotspot": setup.ap_on})

            def do_POST(self):
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                ssid, pw = str(body.get("ssid", "")).strip(), str(body.get("password", ""))
                if not ssid or (pw and not 8 <= len(pw) <= 63):
                    return self._json({"error": "Nume de rețea lipsă sau parolă de 8–63 de caractere."}, 400)
                if setup.lock.locked():
                    return self._json({"error": "O altă conectare e în curs."}, 409)
                threading.Thread(target=setup.switch, args=(ssid, pw), daemon=True).start()
                return self._json({"ok": True})

        srv = ThreadingHTTPServer(("127.0.0.1", 8091), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ---------- pagina ----------
    def start_portal(self) -> None:
        if self.server:
            return
        setup = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: str, code: int = 200):
                b = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.startswith("/rescan"):
                    try:
                        setup.scan()
                    except Exception:
                        pass
                self._send(page(setup))

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode(), keep_blank_values=True)
                ssid = (form.get("ssid") or [""])[0].strip() or (form.get("other") or [""])[0].strip()
                pw = (form.get("password") or [""])[0]
                if not ssid:
                    return self._send(page(setup, "Alegeți o rețea."))
                if pw and not 8 <= len(pw) <= 63:
                    return self._send(page(setup, "Parola WiFi trebuie să aibă între 8 și 63 de caractere."))
                self._send(done_page(ssid))
                threading.Thread(target=setup.connect, args=(ssid, pw), daemon=True).start()

        try:
            self.server = ThreadingHTTPServer((AP_IP, 80), H)
        except OSError as e:
            log.error("nu pot porni pagina pe %s:80: %s", AP_IP, e)
            return
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop_portal(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


STYLE = """body{margin:0;background:#0E1A22;color:#E7EFF3;font:16px/1.4 system-ui,-apple-system,sans-serif}
main{max-width:460px;margin:0 auto;padding:20px 16px 40px}h1{font-size:26px;margin:4px 0 2px}p{color:#8FA6B4}
label.n{display:flex;justify-content:space-between;align-items:center;gap:10px;background:#172632;border:1px solid #2A4255;
border-radius:14px;padding:13px 14px;margin:8px 0;cursor:pointer}label.n input{margin-right:10px;transform:scale(1.3)}
.s{color:#8FA6B4;font-size:13px;white-space:nowrap}input[type=password],input[type=text]{width:100%;box-sizing:border-box;
font-size:17px;padding:13px;border-radius:12px;border:1px solid #2A4255;background:#172632;color:#E7EFF3;margin-top:6px}
button{width:100%;margin-top:16px;padding:14px;border:0;border-radius:999px;background:#63B7E6;color:#04121B;font-size:17px;
font-weight:600}.err{background:#3a1d22;border-left:4px solid #F2564F;padding:10px 12px;border-radius:8px;color:#fff}
a{color:#63B7E6}"""


def page(setup: WifiSetup, error: str = "") -> str:
    err = error or setup.message
    nets = "".join(
        f'<label class="n"><span><input type="radio" name="ssid" value="{html.escape(n["ssid"], quote=True)}"'
        f'{" checked" if i == 0 else ""}>{html.escape(n["ssid"])}{" 🔒" if n["secure"] else ""}</span>'
        f'<span class="s">{bars(n["signal"])} {n["signal"]}%</span></label>'
        for i, n in enumerate(setup.networks))
    return f"""<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Amper — WiFi</title><style>{STYLE}</style></head>
<body><main><h1>Amper — conectare WiFi</h1>
<p>Pi-ul nu are internet. Alegeți rețeaua WiFi de aici și scrieți parola ei.</p>
{f'<p class="err">{html.escape(err)}</p>' if err else ""}
<form method="post">{nets or '<p>Nu am găsit nicio rețea. <a href="/rescan">Caută din nou</a></p>'}
<p style="margin-top:14px">Altă rețea (ascunsă):</p><input type="text" name="other" placeholder="numele rețelei" autocapitalize="off">
<p style="margin-top:14px">Parola WiFi:</p><input type="password" name="password" autocomplete="off">
<button type="submit">Conectează</button></form>
<p><a href="/rescan">Caută din nou rețelele</a></p></main></body></html>"""


def done_page(ssid: str) -> str:
    return f"""<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Amper — WiFi</title><style>{STYLE}</style></head>
<body><main><h1>Mă conectez la „{html.escape(ssid)}”…</h1>
<p>Rețeaua „Amper-Setup” dispare acum. Dacă totul e în regulă, în 1–2 minute Pi-ul e online și îl găsiți prin
Tailscale, ca de obicei (http://amper:8080).</p>
<p>Dacă „Amper-Setup” reapare peste un minut, conectarea nu a reușit (de obicei parola). Conectați-vă din nou la
„Amper-Setup” și încercați iar: pagina vă arată ce s-a întâmplat.</p></main></body></html>"""


def bars(signal: int) -> str:
    return "▂▄▆█"[: max(1, min(4, signal // 25 + 1))]


def main(argv=None) -> None:
    from .config import load_config
    p = argparse.ArgumentParser(prog="amper-wifi")
    p.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    p.add_argument("--test-ap", type=int, metavar="SECUNDE",
                   help="pornește hotspotul și pagina pentru un test, apoi le oprește")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config).get("wifi_setup") or {}
    w = WifiSetup(cfg)
    if args.test_ap:
        w.start_ap()
        print(f"hotspot {w.ssid} pornit, pagina la http://{AP_IP}; rețele găsite: {len(w.networks)}")
        try:
            time.sleep(args.test_ap)
        finally:
            w.stop_ap()
        return
    try:                                # API-ul pentru Setări → Info (scanare, schimbare rețea) merge oricum
        w.start_local_api("/run/amper-wifi/token")
    except OSError as e:
        log.error("API-ul local WiFi nu a pornit: %s", e)
    if not cfg.get("enabled", True) or len(w.password) < 8:
        log.warning("hotspotul de configurare e oprit: %s", "dezactivat în config" if not cfg.get("enabled", True)
                    else "lipsește parola (wifi_setup.password, minim 8 caractere)")
        while True:
            time.sleep(3600)
    w.run()


if __name__ == "__main__":
    main()

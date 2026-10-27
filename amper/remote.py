"""Acces de oriunde (în aplicație: „server”, cuvântul Tailscale nu apare utilizatorului): Tailscale (logare cu contul utilizatorului) + Funnel (adresa https publică, telefoanele nu
au nevoie de Tailscale). Rulează în serviciul amper-update (root), API-ul lui local: /remote/*.

Pașii utilizatorului, din aplicație (ghidul de configurare sau Setări → Acces de oriunde):
  1. „Conectează-te” → `tailscale up` afișează linkul de logare (contul lui Google) → îl deschide pe telefon.
  2. „Pornește accesul de oriunde” → `tailscale funnel --bg --https=443 http://127.0.0.1:<port>`; prima dată Tailscale
     cere aprobarea HTTPS/Funnel în contul lui (alt link) și comanda așteaptă până e aprobat.
  3. Adresa https://<nume>.<tailnet>.ts.net merge de pe orice telefon.
„Oprește” revine la `tailscale serve` (adresa merge doar din tailnet, ca înainte).

NEVERIFICAT pe un cont Tailscale nou: textul exact al linkurilor de aprobare (le căutăm generic, orice
https://login.tailscale.com/...) și comportamentul `funnel --bg` cât așteaptă aprobarea.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")


class Remote:
    def __init__(self, port: int = 8080, hostname: str = "amper", run=None, popen=None, which=shutil.which):
        self.port, self.hostname = port, hostname
        self.run = run or (lambda *a: subprocess.run(a, capture_output=True, text=True, timeout=10))
        self.popen = popen or (lambda *a: subprocess.Popen(a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                          text=True))
        self.which = which
        self.job: dict = {"state": "idle"}           # login / funnel în curs: linkul de deschis, mesajul
        self.lock = threading.Lock()

    # ---------- starea ----------
    def status(self) -> dict:
        if not self.which("tailscale"):
            return {"installed": False}
        st = {"installed": True, "job": self.job}
        try:
            d = json.loads(self.run("tailscale", "status", "--json").stdout or "{}")
        except (ValueError, OSError, subprocess.SubprocessError):
            d = {}
        st["state"] = d.get("BackendState") or "NoState"              # Running, NeedsLogin, Stopped, …
        me = d.get("Self") or {}
        st["name"] = (me.get("DNSName") or "").rstrip(".")
        st["url"] = f"https://{st['name']}" if st["name"] else None
        st["account"] = ((d.get("CurrentTailnet") or {}).get("Name")) or None
        st["auth_url"] = d.get("AuthURL") or None
        st["funnel"] = self.funnel_on()
        return st

    def funnel_on(self) -> bool:
        try:
            cfg = json.loads(self.run("tailscale", "funnel", "status", "--json").stdout or "{}")
        except (ValueError, OSError, subprocess.SubprocessError):
            return False
        return any(bool(v) for v in (cfg.get("AllowFunnel") or {}).values())

    # ---------- comenzi care pot aștepta o aprobare în browser ----------
    def _background(self, kind: str, args: tuple, done_msg: str, wait_url: float = 20) -> dict:
        """Pornește comanda; întoarce imediat ce apare un link de aprobare (sau ce s-a terminat)."""
        if not self.lock.acquire(blocking=False):
            return self.job
        self.job = {"state": "running", "kind": kind, "url": None, "message": "Pornesc…", "started": time.time()}
        try:
            proc = self.popen(*args)
        except OSError as e:
            self.lock.release()
            self.job = {"state": "failed", "kind": kind, "message": f"Conexiunea la server nu a pornit: {e}"}
            return self.job
        found = threading.Event()

        def reader():
            out = []
            try:
                for line in proc.stdout:
                    out.append(line)
                    m = URL_RE.search(line)
                    if m and not self.job.get("url"):
                        self.job.update(url=m.group(0).rstrip(".,)"), message="Deschide linkul și aprobă.")
                        found.set()
                rc = proc.wait()
                if rc == 0:
                    self.job.update(state="ok", url=None, message=done_msg)
                else:
                    tail = "".join(out[-3:]).strip()
                    self.job.update(state="failed", message=f"Serverul a răspuns cu o eroare: {tail[-300:]}")
                log.info("tailscale %s: %s", kind, self.job["state"])
            finally:
                found.set()
                self.lock.release()
        threading.Thread(target=reader, daemon=True).start()
        found.wait(wait_url)
        return self.job

    def login(self) -> dict:
        """Leagă Pi-ul de contul Tailscale al utilizatorului (linkul de logare îl deschide el)."""
        return self._background("login", ("tailscale", "up", f"--hostname={self.hostname}"),
                                "Pi-ul e conectat la server.")

    def funnel(self, on: bool) -> dict:
        if on:
            return self._background("funnel", ("tailscale", "funnel", "--bg", "--https=443", f"http://127.0.0.1:{self.port}"),
                                    "Accesul de oriunde e pornit.")
        # oprit: rămâne adresa https doar pentru aparatele din contul Tailscale (ca înainte)
        try:
            self.run("tailscale", "funnel", "reset")
            r = self.run("tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{self.port}")
        except (OSError, subprocess.SubprocessError) as e:
            self.job = {"state": "failed", "kind": "funnel-off", "message": f"Serverul nu răspunde: {e}"}
            return self.job
        self.job = {"state": "ok" if r.returncode == 0 else "failed", "kind": "funnel-off",
                    "message": "Accesul public e oprit; adresa merge doar pe telefoanele conectate la server."
                    if r.returncode == 0 else f"Nu am putut opri: {(r.stdout or '')[-200:]}"}
        return self.job

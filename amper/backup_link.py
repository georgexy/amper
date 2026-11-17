"""Copia de siguranță în Google Drive, legată din aplicație (Setări → Conectare → Copia de siguranță, cerut
2026-11-17). Rulează în serviciul amper-update (root): /etc/amper/rclone.conf e al lui root, iar timerul
amper-backup se pornește cu systemctl. API-ul lui local: /backup/*.

Pașii utilizatorului (aceiași ca la legarea făcută de mână, dar din aplicație):
  1. „Conectează Google Drive” → pe Pi pornește `rclone authorize drive` (acces doar la fișierele făcute de Amper,
     scope drive.file), care ascultă pe 127.0.0.1:53682. Cerem pagina /auth?state=… și primim adresa Google
     (redirecționarea), pe care aplicația o deschide pe telefon.
  2. Utilizatorul alege contul și apasă „Permite”. Google îl trimite la http://127.0.0.1:53682/?state=…&code=…, pagină
     care pe telefon NU se deschide (e adresa Pi-ului, nu a telefonului) — e normal. Copiază adresa din bara de sus
     și o lipește în aplicație.
  3. Pi-ul cere el acea adresă local, rclone primește codul și dă cheia (token) → /etc/amper/rclone.conf (600),
     timerul amper-backup (în fiecare noapte la 00:00) pornește, iar legătura se verifică cu `rclone mkdir`.
NEVERIFICAT pe Pi: textele exacte afișate de rclone (le căutăm larg) și pasul 2 pe iPhone.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

AUTH_RE = re.compile(r"http://(?:127\.0\.0\.1|localhost):53682/auth\?state=[\w-]+")
TOKEN_RE = re.compile(r"--->\s*(\{.*?\})\s*<---", re.S)
SCOPE = base64.urlsafe_b64encode(json.dumps({"scope": "drive.file"}).encode()).decode().rstrip("=")   # rclone: fără „=”
WAIT_URL = 30                    # secunde până rclone afișează linkul
WAIT_TOKEN = 60                  # secunde după cod până rclone dă cheia
LINK_TTL = 15 * 60               # cât așteaptă legarea începută (apoi se oprește)


class LinkError(Exception):
    pass


class BackupLink:
    def __init__(self, etc_dir: str = "/etc/amper", log_dir: str = "/var/log/amper", run=None, popen=None,
                 fetch=None, clock=time.time):
        self.conf = os.path.join(etc_dir, "rclone.conf")
        self.env = os.path.join(etc_dir, "backup.env")
        self.log_file = os.path.join(log_dir, "backup.log")
        self.run = run or (lambda *a, timeout=60: subprocess.run(a, capture_output=True, text=True, timeout=timeout))
        self.popen = popen or (lambda *a: subprocess.Popen(a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                          text=True))
        self.fetch = fetch or _fetch
        self.clock = clock
        self.proc = None
        self.reader = None
        self.out: list[str] = []
        self.job: dict = {"state": "idle"}
        self.lock = threading.Lock()

    # ---------- starea ----------
    def linked(self) -> bool:
        try:
            with open(self.conf, encoding="utf-8") as f:
                return "[gdrive]" in f.read()
        except OSError:
            return False

    def remote(self) -> str:
        try:
            with open(self.env, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("AMPER_BACKUP_REMOTE="):
                        return line.split("=", 1)[1].strip().strip('"') or "gdrive:back-up"
        except OSError:
            pass
        return "gdrive:back-up"

    def last_run(self) -> dict | None:
        """Ultima rulare a copiei, din /var/log/amper/backup.log: când, ce, cum s-a terminat."""
        try:
            with open(self.log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-400:]
        except OSError:
            return None
        start = max((i for i, ln in enumerate(lines) if "=====" in ln), default=None)
        if start is None:
            return None
        run = lines[start:]
        res = {"at": run[0][:19], "what": (re.search(r"\((\w+)\)", run[0]) or [None, ""])[1], "state": "running"}
        for ln in run[1:]:
            if ln[20:].startswith("gata"):
                res.update(state="ok", end=ln[:19])
            elif "EȘUAT — " in ln:
                res.update(state="failed", end=ln[:19], why=ln.split("EȘUAT — ", 1)[1].strip())
            elif "Google Drive nu e configurat" in ln:
                res.update(state="failed", why="Google Drive nu e configurat")
        return res

    def timer_on(self) -> bool:
        try:
            return self.run("systemctl", "is-enabled", "amper-backup.timer", timeout=10).stdout.strip() == "enabled"
        except Exception:
            return False

    def status(self) -> dict:
        job = dict(self.job)
        if job.get("state") == "waiting" and self.clock() - job.get("at", 0) > LINK_TTL:
            self._stop()
            job = self.job = {"state": "idle"}
        return {"linked": self.linked(), "remote": self.remote(), "timer": self.timer_on(),
                "last": self.last_run(), "job": job}

    # ---------- legarea ----------
    def start(self) -> dict:
        """Pasul 1: pornește rclone authorize și întoarce adresa Google de deschis pe telefon."""
        with self.lock:
            self._stop()
            self.out = []
            try:                                         # o încercare veche rămasă (ex. după o repornire) ține portul 53682
                self.run("pkill", "-f", "rclone authorize drive", timeout=10)
            except Exception:
                pass
            try:
                self.proc = self.popen("rclone", "authorize", "drive", SCOPE, "--auth-no-open-browser")
            except OSError as e:
                raise LinkError(f"rclone nu e instalat pe Pi ({e}).")
            self.reader = threading.Thread(target=self._read, args=(self.proc,), daemon=True)
            self.reader.start()
            end = self.clock() + WAIT_URL
            local = None
            while self.clock() < end and not local:
                m = AUTH_RE.search("".join(self.out))
                local = m.group(0) if m else None
                if not local:
                    if self.proc.poll() is not None:
                        self.reader.join(timeout=3)
                        m = AUTH_RE.search("".join(self.out))
                        local = m.group(0) if m else None
                        break
                    time.sleep(0.2)
            if not local:
                self._stop()
                raise LinkError("Pi-ul nu a putut porni legarea: " + (self._last_line() or "rclone nu a răspuns"))
            try:
                google = self.fetch(local, follow=False)
            except Exception as e:
                self._stop()
                raise LinkError(f"Pi-ul nu a putut cere adresa Google ({e}).")
            if not google.startswith("https://accounts.google.com/"):
                self._stop()
                raise LinkError("Adresa primită nu e de la Google.")
            self.job = {"state": "waiting", "url": google, "at": self.clock()}
            log.info("copia de siguranță: legarea Google Drive a început")
            return dict(self.job)

    def finish(self, pasted: str) -> dict:
        """Pasul 3: adresa lipită de utilizator (pagina care nu s-a deschis) → codul → cheia → rclone.conf."""
        pasted = (pasted or "").strip()
        u = urllib.parse.urlparse(pasted)
        q = urllib.parse.parse_qs(u.query)
        if u.hostname not in ("127.0.0.1", "localhost") or u.port != 53682 or "code" not in q or "state" not in q:
            raise LinkError("Adresa lipită nu e cea bună: trebuie să înceapă cu http://127.0.0.1:53682/ și să "
                            "conțină code=. Copiaz-o din bara de sus a paginii care nu s-a deschis.")
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                raise LinkError("Legarea a expirat. Apasă din nou „Conectează Google Drive”.")
            local = f"http://127.0.0.1:53682{u.path or '/'}?{u.query}"
            try:
                self.fetch(local, follow=True)
            except Exception as e:
                raise LinkError(f"Pi-ul nu a putut trimite codul la rclone ({e}).")
            end = self.clock() + WAIT_TOKEN
            token = None
            while self.clock() < end:
                m = TOKEN_RE.search("".join(self.out))
                if m:
                    token = m.group(1)
                    break
                if self.proc.poll() is not None:            # s-a oprit: citim tot ce a mai scris, o dată
                    self.reader.join(timeout=3)
                    m = TOKEN_RE.search("".join(self.out))
                    token = m.group(1) if m else None
                    break
                time.sleep(0.3)
            if not token:
                why = self._last_line()
                self._stop()
                self.job = {"state": "idle"}
                raise LinkError("Google nu a dat acces: " + (why or "fără răspuns") + ". Încearcă din nou.")
            try:
                json.loads(token)
            except ValueError:
                raise LinkError("Răspunsul de la Google nu e valid. Încearcă din nou.")
            self._write_conf(token)
            self._stop()
        test = self.run("rclone", "--config", self.conf, "mkdir", self.remote(), timeout=60)
        if test.returncode != 0:
            raise LinkError("Legătura s-a salvat, dar Drive-ul nu răspunde: "
                            + ((test.stderr or test.stdout or "").strip().splitlines() or ["?"])[-1])
        self.run("systemctl", "enable", "--now", "amper-backup.timer", timeout=30)
        self.job = {"state": "done", "at": self.clock()}
        log.warning("copia de siguranță: Google Drive legat din aplicație, timerul pornit")
        return self.status()

    def _write_conf(self, token: str) -> None:
        os.makedirs(os.path.dirname(self.conf), exist_ok=True)
        tmp = self.conf + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"[gdrive]\ntype = drive\nscope = drive.file\ntoken = {token}\n")
        if os.path.exists(self.conf):                       # legătura veche rămâne ca rezervă
            os.replace(self.conf, self.conf + ".old")
        os.replace(tmp, self.conf)

    def backup_now(self) -> dict:
        if not self.linked():
            raise LinkError("Google Drive nu e legat.")
        self.run("systemctl", "start", "--no-block", "amper-backup.service", timeout=30)
        return {"ok": True}

    def unlink(self) -> dict:
        self.run("systemctl", "disable", "--now", "amper-backup.timer", timeout=30)
        if os.path.exists(self.conf):
            os.replace(self.conf, self.conf + ".old")       # nu ștergem cheia definitiv: se poate pune la loc de mână
        self.job = {"state": "idle"}
        log.warning("copia de siguranță: Google Drive dezlegat din aplicație")
        return self.status()

    # ---------- intern ----------
    def _read(self, proc) -> None:
        try:
            for line in proc.stdout:
                self.out.append(line)
        except (OSError, ValueError):
            pass

    def _last_line(self) -> str:
        lines = [ln.strip() for ln in "".join(self.out).splitlines() if ln.strip()]
        if not lines:
            return ""
        # „2026/11/17 18:39:22 Fatal error: failed to get token: oauth2: …” → „failed to get token: oauth2: …”
        return re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+(?:[A-Z]+\s*:|Fatal error:|Error:)?\s*", "", lines[-1])[:200]

    def _stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _fetch(url: str, follow: bool = True) -> str:
    """Cere o pagină pe Pi. Fără `follow`, întoarce adresa la care trimite (Location)."""
    opener = urllib.request.build_opener(*([] if follow else [_NoRedirect()]),
                                         urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=20) as r:
            return r.headers.get("Location") or r.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if not follow and e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location") or ""
        raise

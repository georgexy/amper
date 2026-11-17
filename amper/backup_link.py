"""Copia de siguranță legată din aplicație (Setări → Conectare → Copia de siguranță, cerut 2026-11-17): Google
Drive, OneDrive, Dropbox, pCloud, Nextcloud / WebDAV sau un NAS din casă (SMB). Rulează în serviciul amper-update
(root): /etc/amper/rclone.conf e al lui root, iar timerul amper-backup se pornește cu systemctl. API-ul lui local:
/backup/*. Copia o face tot deploy/backup.sh, cu remote-ul din /etc/amper/backup.env (AMPER_BACKUP_REMOTE).

Serviciile cu cont (OAuth: Google, Microsoft, Dropbox, pCloud), aceiași pași ca la legarea făcută de mână:
  1. Pe Pi pornește `rclone authorize <tip>`, care ascultă pe 127.0.0.1:53682. Cerem pagina /auth?state=… fără
     redirecționare și primim adresa de logare a serviciului, pe care aplicația o deschide pe telefon.
  2. Utilizatorul alege contul și apasă „Permite”. Serviciul îl trimite la http://127.0.0.1:53682/?state=…&code=… sau
     http://localhost:53682/…, pagină care pe telefon NU se deschide (e adresa Pi-ului). Copiază adresa și o lipește.
  3. Pi-ul cere el acea adresă local, rclone primește codul și dă cheia (token) → rclone.conf (600). OneDrive: și
     discul (drive_id, din Microsoft Graph); pCloud: serverul (hostname=…, din adresa lipită: UE sau SUA).
Serviciile cu adresă + utilizator + parolă (WebDAV / Nextcloud, SMB): parola se ascunde cu `rclone obscure`.
La final: `rclone mkdir <remote>` (verifică legătura), backup.env, `systemctl enable --now amper-backup.timer`.

Verificat cu rclone 1.60.1 (cel din Raspberry Pi OS): adresele de logare ale celor 4 servicii OAuth și mesajul unui
cod greșit. NEVERIFICAT: consimțământul cu conturi reale pe iPhone, WebDAV/SMB pe servere reale.
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
WAIT_URL = 30                    # secunde până rclone afișează linkul
WAIT_TOKEN = 60                  # secunde după cod până rclone dă cheia
LINK_TTL = 15 * 60               # cât așteaptă legarea începută (apoi se oprește)


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")   # rclone: base64url fără „=”


# Serviciile. `stream`: rclone poate urca clona din mers (rcat); altfel backup.sh o comprimă întâi pe card.
# `space`: spațiul gratuit al unui cont nou, pentru textul din aplicație.
PROVIDERS = {
    "drive": dict(name="Google Drive", kind="oauth", rtype="drive", auth_args=[_b64({"scope": "drive.file"})],
                  hosts=("accounts.google.com",), extra={"scope": "drive.file"}, section="gdrive", stream=True,
                  space="15 GB"),
    "onedrive": dict(name="OneDrive", kind="oauth", rtype="onedrive", auth_args=[], hosts=("login.microsoftonline.com",
                     "login.live.com"), extra={}, section="cloud", stream=False, space="5 GB"),
    "dropbox": dict(name="Dropbox", kind="oauth", rtype="dropbox", auth_args=[], hosts=("www.dropbox.com",),
                    extra={}, section="cloud", stream=True, space="2 GB"),
    "pcloud": dict(name="pCloud", kind="oauth", rtype="pcloud", auth_args=[], hosts=("my.pcloud.com",), extra={},
                   section="cloud", stream=False, space="10 GB"),
    "webdav": dict(name="Nextcloud / WebDAV", kind="form", rtype="webdav", section="cloud", stream=False, space=None,
                   fields=["url", "user", "pass"]),
    "smb": dict(name="NAS / folder partajat (SMB)", kind="form", rtype="smb", section="cloud", stream=True, space=None,
                fields=["host", "share", "user", "pass", "domain"]),
}
TYPE_TO_ID = {p["rtype"]: k for k, p in PROVIDERS.items()}
FOLDER = "back-up"


class LinkError(Exception):
    pass


class BackupLink:
    def __init__(self, etc_dir: str = "/etc/amper", log_dir: str = "/var/log/amper", run=None, popen=None,
                 fetch=None, graph=None, clock=time.time):
        self.conf = os.path.join(etc_dir, "rclone.conf")
        self.env = os.path.join(etc_dir, "backup.env")
        self.log_file = os.path.join(log_dir, "backup.log")
        self.run = run or (lambda *a, timeout=60: subprocess.run(a, capture_output=True, text=True, timeout=timeout))
        self.popen = popen or (lambda *a: subprocess.Popen(a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                          text=True))
        self.fetch = fetch or _fetch
        self.graph = graph or _graph
        self.clock = clock
        self.proc = None
        self.reader = None
        self.out: list[str] = []
        self.job: dict = {"state": "idle"}
        self.lock = threading.Lock()

    # ---------- starea ----------
    def sections(self) -> dict[str, str]:
        """Remote-urile din rclone.conf: nume → tip."""
        out, cur = {}, None
        try:
            with open(self.conf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        cur = line[1:-1]
                        out[cur] = ""
                    elif cur and line.startswith("type") and "=" in line:
                        out[cur] = line.split("=", 1)[1].strip()
        except OSError:
            pass
        return out

    def remote(self) -> str:
        try:
            with open(self.env, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("AMPER_BACKUP_REMOTE="):
                        return line.split("=", 1)[1].strip().strip('"') or "gdrive:back-up"
        except OSError:
            pass
        return "gdrive:back-up"

    def linked(self) -> bool:
        return self.remote().split(":", 1)[0] in self.sections()

    def provider(self) -> str | None:
        rtype = self.sections().get(self.remote().split(":", 1)[0])
        return TYPE_TO_ID.get(rtype or "")

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
            elif "nu e configurat" in ln:
                res.update(state="failed", why="copia nu e legată")
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
        prov = self.provider()
        return {"linked": self.linked(), "provider": prov, "provider_name": PROVIDERS[prov]["name"] if prov else None,
                "remote": self.remote(), "timer": self.timer_on(), "last": self.last_run(), "job": job,
                "providers": [dict(id=k, name=p["name"], kind=p["kind"], space=p["space"],
                                   fields=p.get("fields", [])) for k, p in PROVIDERS.items()]}

    # ---------- legarea cu cont (OAuth) ----------
    def start(self, provider: str = "drive") -> dict:
        """Pasul 1: pornește rclone authorize și întoarce adresa de logare de deschis pe telefon."""
        p = PROVIDERS.get(provider)
        if not p or p["kind"] != "oauth":
            raise LinkError("Serviciu necunoscut.")
        with self.lock:
            self._stop()
            self.out = []
            try:                                         # o încercare veche rămasă (ex. după o repornire) ține portul 53682
                self.run("pkill", "-f", "rclone authorize", timeout=10)
            except Exception:
                pass
            try:
                self.proc = self.popen("rclone", "authorize", p["rtype"], *p["auth_args"], "--auth-no-open-browser")
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
                login = self.fetch(local.replace("localhost", "127.0.0.1"), follow=False)
            except Exception as e:
                self._stop()
                raise LinkError(f"Pi-ul nu a putut cere adresa de logare ({e}).")
            if urllib.parse.urlparse(login).hostname not in p["hosts"]:
                self._stop()
                raise LinkError("Adresa primită nu e a serviciului ales.")
            self.job = {"state": "waiting", "provider": provider, "name": p["name"], "url": login, "at": self.clock()}
            log.info("copia de siguranță: legarea %s a început", p["name"])
            return dict(self.job)

    def finish(self, pasted: str) -> dict:
        """Pasul 3: adresa lipită de utilizator (pagina care nu s-a deschis) → codul → cheia → rclone.conf."""
        pasted = (pasted or "").strip()
        u = urllib.parse.urlparse(pasted)
        q = urllib.parse.parse_qs(u.query)
        if u.hostname not in ("127.0.0.1", "localhost") or u.port != 53682 or "code" not in q or "state" not in q:
            raise LinkError("Adresa lipită nu e cea bună: trebuie să înceapă cu http://127.0.0.1:53682/ sau "
                            "http://localhost:53682/ și să conțină code=. Copiaz-o din bara de sus a paginii care nu "
                            "s-a deschis.")
        provider = self.job.get("provider") or "drive"
        p = PROVIDERS[provider]
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                raise LinkError("Legarea a expirat. Apasă din nou „Conectează”.")
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
                raise LinkError(f"{p['name']} nu a dat acces: " + (why or "fără răspuns") + ". Încearcă din nou.")
            try:
                tok = json.loads(token)
            except ValueError:
                raise LinkError("Răspunsul serviciului nu e valid. Încearcă din nou.")
            self._stop()
        opts = {"type": p["rtype"], **p["extra"], "token": token}
        if provider == "onedrive":                          # discul OneDrive al contului (rclone îl cere)
            try:
                d = self.graph(tok.get("access_token", ""))
                opts.update(drive_id=d["id"], drive_type=d.get("driveType") or "personal")
            except Exception as e:
                raise LinkError(f"OneDrive nu a spus care e discul contului ({e}). Încearcă din nou.")
        if provider == "pcloud":                            # conturile din UE sunt pe alt server
            opts["hostname"] = (q.get("hostname") or ["api.pcloud.com"])[0]
        return self._save(provider, opts, f"{p['section']}:{FOLDER}")

    # ---------- legarea cu adresă + utilizator + parolă ----------
    def link_form(self, provider: str, fields: dict) -> dict:
        p = PROVIDERS.get(provider)
        if not p or p["kind"] != "form":
            raise LinkError("Serviciu necunoscut.")
        f = {k: str(fields.get(k) or "").strip() for k in p["fields"]}
        if provider == "webdav":
            if not re.match(r"^https?://", f["url"]):
                raise LinkError("Adresa trebuie să înceapă cu https:// (sau http://).")
            vendor = "nextcloud" if "nextcloud" in f["url"].lower() or "/remote.php/dav" in f["url"] else "other"
            opts = {"type": "webdav", "url": f["url"], "vendor": vendor}
            remote = f"{p['section']}:amper-{FOLDER}"
        else:
            host = re.sub(r"^(smb:)?/+|/+$", "", f["host"])
            share = f["share"].strip("/\\")
            if not host or not share:
                raise LinkError("Scrie adresa NAS-ului (ex. 192.168.1.10) și numele folderului partajat.")
            opts = {"type": "smb", "host": host}
            if f.get("domain"):
                opts["domain"] = f["domain"]
            remote = f"{p['section']}:{share}/amper-{FOLDER}"
        if f.get("user"):
            opts["user"] = f["user"]
        if f.get("pass"):
            r = self.run("rclone", "obscure", f["pass"], timeout=20)
            if r.returncode != 0 or not r.stdout.strip():
                raise LinkError("Parola nu s-a putut salva.")
            opts["pass"] = r.stdout.strip()
        return self._save(provider, opts, remote)

    # ---------- comun ----------
    def _save(self, provider: str, opts: dict, remote: str) -> dict:
        name = remote.split(":", 1)[0]
        old = (self._read_file(self.conf), self._read_file(self.env))
        self._write(self.conf, f"[{name}]\n" + "".join(f"{k} = {v}\n" for k, v in opts.items()), 0o600)
        self._write(self.env, f"AMPER_BACKUP_REMOTE={remote}\n", 0o644)
        test = self.run("rclone", "--config", self.conf, "mkdir", remote, timeout=60)
        if test.returncode != 0:                            # legătura nu merge: punem la loc ce era
            self._restore(*old)
            raise LinkError("Legătura nu merge: " + _short((test.stderr or test.stdout or "").strip()))
        if old[0] is not None:
            self._write(self.conf + ".old", old[0], 0o600)   # legătura veche rămâne ca rezervă
        self.run("systemctl", "enable", "--now", "amper-backup.timer", timeout=30)
        self.job = {"state": "done", "at": self.clock()}
        log.warning("copia de siguranță: legată din aplicație la %s (%s)", PROVIDERS[provider]["name"], remote)
        return self.status()

    def _restore(self, conf: str | None, env: str | None) -> None:
        for path, text, mode in ((self.conf, conf, 0o600), (self.env, env, 0o644)):
            if text is None:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            else:
                self._write(path, text, mode)

    @staticmethod
    def _read_file(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    @staticmethod
    def _write(path: str, text: str, mode: int) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def backup_now(self) -> dict:
        if not self.linked():
            raise LinkError("Copia nu e legată.")
        self.run("systemctl", "start", "--no-block", "amper-backup.service", timeout=30)
        return {"ok": True}

    def unlink(self) -> dict:
        self.run("systemctl", "disable", "--now", "amper-backup.timer", timeout=30)
        if os.path.exists(self.conf):
            os.replace(self.conf, self.conf + ".old")       # nu ștergem cheia definitiv: se poate pune la loc de mână
        self.job = {"state": "idle"}
        log.warning("copia de siguranță: oprită din aplicație")
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
        return _short(lines[-1]) if lines else ""

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


def _short(line: str) -> str:
    """„2026/11/17 18:39:22 Fatal error: failed to get token: …” → „failed to get token: …” (ultimul rând)."""
    line = (line.strip().splitlines() or [""])[-1]
    return re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\s+(?:[A-Z]+\s*:|Fatal error:|Error:)?\s*", "", line)[:200]


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


def _graph(access_token: str) -> dict:
    """OneDrive: discul contului (id + tip), din Microsoft Graph."""
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me/drive",
                                 headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

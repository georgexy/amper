"""Actualizări din GitHub Releases, semnate Ed25519 (serviciul amper-update, rulează ca root).

  python3 -m amper.update [-c /etc/amper/config.yaml]        serviciul: API local + căutare automată
  python3 -m amper.update --check                            caută o dată și afișează rezultatul

Pe Pi:
  /opt/amper            legătură → /opt/amper-<versiune> (se schimbă la actualizare / revenire)
  /etc/amper            config — rămâne
  /var/lib/amper        baza de date, daly-auto.json, vapid.json — rămân
  /var/lib/amper/pre-update/<versiune>/   copia datelor făcută înainte de fiecare actualizare (ultimele 3)
  /var/lib/amper-update/state.json        versiunea instalată, ultima căutare, istoricul

Pașii unei actualizări: manifest + semnătură → arhivă (sha256) → spațiu → copia datelor → despachetare în
/opt/amper-<ver> → schimbarea legăturii → repornire → /api/health în 60 s; altfel revine la versiunea veche
(cod + date din copia făcută înainte). Căutarea automată (la 6 ore) doar anunță; o versiune marcată „urgentă” în
manifestul semnat (`release.py build --urgent`) se instalează singură, cu aceleași verificări.
Paznicul (la fiecare minut): Amper nu răspunde 5 minute → repornit; tot nu răspunde și actualizarea e din ultimele
7 zile → revenire automată la versiunea anterioară. Fișierul `revenire.txt` pus pe card (bootfs) → revenire la pornire.
API local pentru serverul web (utilizatorul amper): 127.0.0.1:8092, token în /run/amper-update/token.
"""
from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

API_PORT = 8092
TOKEN_PATH = "/run/amper-update/token"
KEEP_BACKUPS = 3          # copii pre-update păstrate
KEEP_VERSIONS = 3         # foldere /opt/amper-<ver> păstrate (curentă, anterioară și încă una)
HEALTH_TIMEOUT = 60
URGENT_EVERY = 6 * 3600   # cât de des se caută o versiune urgentă (și se anunță una nouă)
WATCH_EVERY = 60          # paznicul: cât de des întreabă Amper dacă răspunde
DOWN_RESTART = 300        # nu răspunde de atâtea secunde → repornire
RECENT_UPDATE = 7 * 86400  # o actualizare mai nouă de atât e suspectă dacă Amper nu mai pornește
CARD_ROLLBACK = ("revenire", "rollback")   # fișierul de pe card care cere revenirea
STEPS = ("verify", "space", "download", "check", "backup", "install", "restart", "health")


class UpdateError(Exception):
    """Eroare cu mesaj clar, în română, afișat în aplicație."""


# ---------------------------------------------------------------- versiuni
_VER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$")


def parse_version(v: str) -> tuple:
    m = _VER.match(str(v).strip().lstrip("v"))
    if not m:
        raise ValueError(f"versiune invalidă: {v}")
    pre = m.group(4)
    # 1.2.0-beta.1 < 1.2.0: o versiune fără sufix e mai nouă decât orice beta a ei
    pre_key = (1,) if pre is None else (0,) + tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key)


def newer(a: str, b: str) -> bool:
    """a e mai nouă decât b?"""
    return parse_version(a) > parse_version(b)


def is_beta(v: str) -> bool:
    return "-" in str(v)


def code_version(code_dir: str) -> str | None:
    """Versiunea dintr-un folder de cod (amper/__init__.py), fără să-l importe."""
    try:
        with open(os.path.join(code_dir, "amper", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


# ---------------------------------------------------------------- semnătura
def verify_manifest(manifest_bytes: bytes, sig_b64: str | bytes, keys: list[str]) -> dict:
    """Verifică semnătura Ed25519 a manifestului (exact octeții primiți) și îl întoarce ca dict."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not keys:
        raise UpdateError("Această versiune nu are cheia de verificare a actualizărilor; "
                          "actualizarea automată nu e posibilă (instalați manual o versiune nouă).")
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except ValueError:
        raise UpdateError("Semnătura versiunii este invalidă (format greșit). Actualizarea a fost refuzată.")
    for k in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, manifest_bytes)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        raise UpdateError("Semnătura versiunii este invalidă: arhiva nu vine de la autorul Amper. "
                          "Actualizarea a fost refuzată.")
    try:
        m = json.loads(manifest_bytes)
        parse_version(m["version"])
        if not re.fullmatch(r"[0-9a-f]{64}", m["sha256"]) or not re.fullmatch(r"amper-[\w.\-]+\.tar\.gz", m["tarball"]):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise UpdateError("Manifestul versiunii e incomplet.")
    return m


# ---------------------------------------------------------------- arhiva
def safe_extract(tar_bytes: bytes, dest: str, version: str) -> str:
    """Despachetează arhiva în `dest` (care nu trebuie să existe); întoarce folderul de cod.
    Acceptă numai fișiere și foldere obișnuite sub `amper-<versiune>/`."""
    prefix = f"amper-{version}"
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            name = m.name.rstrip("/")
            parts = name.split("/")
            if (name.startswith("/") or ".." in parts or parts[0] != prefix
                    or not (m.isfile() or m.isdir())):
                raise UpdateError(f"Arhiva conține o cale nepermisă ({m.name}). Actualizarea a fost refuzată.")
        tmp = dest + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for m in members:
            m.mode = (m.mode & 0o755) | (0o755 if m.isdir() else 0o644)
            m.uid = m.gid = 0
            m.uname = m.gname = "root"
        try:
            tf.extractall(tmp, members=members, filter="data")
        except TypeError:                                   # Python < 3.12 (fără filter)
            tf.extractall(tmp, members=members)
    if not os.path.isfile(os.path.join(tmp, prefix, "amper", "__init__.py")):
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError("Arhiva nu conține programul Amper.")
    if code_version(os.path.join(tmp, prefix)) != version:
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError("Versiunea din arhivă nu se potrivește cu manifestul.")
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(os.path.join(tmp, prefix), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


# ---------------------------------------------------------------- copia datelor
def _copy_tree(src: str, dst: str) -> None:
    """Copie cu drepturile și proprietarii păstrați (config-ul e root:amper 640)."""
    shutil.copytree(src, dst, copy_function=shutil.copy2, dirs_exist_ok=True, symlinks=True)
    if os.geteuid() == 0:
        for root, dirs, files in os.walk(src):
            for n in dirs + files + [""]:
                s = os.path.join(root, n)
                st = os.lstat(s)
                os.lchown(os.path.join(dst, os.path.relpath(s, src)), st.st_uid, st.st_gid)


def _chown_like(path: str, ref: str) -> None:
    if os.geteuid() == 0 and os.path.exists(ref):
        st = os.stat(ref)
        os.chown(path, st.st_uid, st.st_gid)


def _state_files(d: str) -> list[str]:
    return glob.glob(os.path.join(d, "*.json")) + [p for p in (os.path.join(d, "settings.yaml"),
                                                              os.path.join(d, "cookie.key")) if os.path.isfile(p)]


def backup_data(dest: str, db_path: str, etc_dir: str, data_dir: str, meta: dict) -> None:
    """Copia datelor înainte de actualizare: baza de date (API-ul sqlite3 de backup), config, fișierele de stare
    (*.json, settings.yaml = setările din aplicație, cookie.key)."""
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    os.chmod(dest, 0o700)
    if os.path.exists(db_path):
        src = sqlite3.connect(db_path, timeout=30)
        try:
            dst = sqlite3.connect(os.path.join(dest, "amper.db"))
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
    if os.path.isdir(etc_dir):
        _copy_tree(etc_dir, os.path.join(dest, "etc"))
    os.makedirs(os.path.join(dest, "json"))
    for f in _state_files(data_dir):
        shutil.copy2(f, os.path.join(dest, "json"))
    with open(os.path.join(dest, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({**meta, "db": os.path.exists(db_path), "time": int(time.time())}, f, ensure_ascii=False)


def restore_data(src: str, db_path: str, etc_dir: str, data_dir: str) -> None:
    """Pune la loc copia făcută de backup_data (serviciul amper trebuie să fie oprit)."""
    with open(os.path.join(src, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("db"):
        ref = data_dir if os.path.exists(data_dir) else src
        for ext in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(db_path + ext)
            except FileNotFoundError:
                pass
        shutil.copy2(os.path.join(src, "amper.db"), db_path)
        _chown_like(db_path, ref)
    if os.path.isdir(os.path.join(src, "etc")):
        if os.path.isdir(etc_dir):
            for n in os.listdir(etc_dir):                   # exact ca înainte: fișierele noi dispar
                p = os.path.join(etc_dir, n)
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
        _copy_tree(os.path.join(src, "etc"), etc_dir)
    for f in _state_files(os.path.join(src, "json")):
        d = os.path.join(data_dir, os.path.basename(f))
        shutil.copy2(f, d)
        _chown_like(d, data_dir)


# ---------------------------------------------------------------- updater-ul
class Updater:
    def __init__(self, cfg: dict, *, opt_dir="/opt", link="/opt/amper", etc_dir="/etc/amper",
                 data_dir="/var/lib/amper", state_dir="/var/lib/amper-update", keys: list[str] | None = None,
                 fetch=None, run=None, health=None, notify=None, clock=time.time, ping=None, svc_state=None,
                 boot_dirs=("/boot/firmware", "/boot")):
        u = cfg.get("update") or {}
        self.repo = u.get("repo") or "georgexy/amper"
        self.channel = u.get("channel") or "stable"
        self.check_days = float(u.get("check_days") or 7)
        self.port = int((cfg.get("web") or {}).get("port") or 8080)
        self.db_path = cfg.get("database") or os.path.join(data_dir, "amper.db")
        self.opt_dir, self.link, self.etc_dir, self.data_dir = opt_dir, link, etc_dir, data_dir
        self.state_dir = state_dir
        self.backup_root = os.path.join(data_dir, "pre-update")
        if keys is None:
            from .update_key import PUBLIC_KEYS
            keys = PUBLIC_KEYS
        self.keys = keys
        self.fetch = fetch or self._fetch
        self.run = run or self._run
        self.health = health or self._health
        self.notify = notify or self._notify
        self.clock = clock
        self.ping = ping or self._ping
        self.svc_state = svc_state or self._svc_state
        self.boot_dirs = boot_dirs
        self.wd = {"down_since": None, "restarts": 0, "notified": False}      # starea paznicului
        self.lock = threading.Lock()
        self.job: dict = {"state": "idle"}
        from .remote import Remote                    # acces de oriunde (Tailscale + Funnel), tot ca root
        self.remote = Remote(port=self.port)
        self.state = self._load_state()
        if self.state.get("channel") in ("stable", "beta"):     # ales din aplicație (Setări → Actualizări)
            self.channel = self.state["channel"]
        self._sync_installed()

    # ---------- starea ----------
    def _load_state(self) -> dict:
        try:
            with open(os.path.join(self.state_dir, "state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"history": []}

    def save(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        p = os.path.join(self.state_dir, "state.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)

    def current_dir(self) -> str:
        return os.path.realpath(self.link)

    def installed_version(self) -> str | None:
        return code_version(self.current_dir())

    def _sync_installed(self) -> None:
        """Instalare manuală (install.sh) între timp → o trecem ca versiune instalată."""
        v = self.installed_version()
        inst = self.state.get("installed") or {}
        if v and inst.get("version") != v:
            self.state["installed"] = {"version": v, "at": int(self.clock()), "dir": self.current_dir()}
            self.save()

    def status(self) -> dict:
        prev = self.state.get("previous") or {}
        prev_ok = bool(prev.get("dir") and os.path.isdir(prev["dir"]) and prev["dir"] != self.current_dir())
        latest = self.state.get("latest")
        cur = self.installed_version()
        avail = bool(latest and cur and newer(latest["version"], cur))
        return dict(installed=self.state.get("installed"), version=cur, channel=self.channel, repo=self.repo,
                    last_check=self.state.get("last_check"), last_check_auto=self.state.get("last_check_auto"),
                    check_error=self.state.get("check_error"), latest=latest, available=avail,
                    previous=prev if prev_ok else None, history=(self.state.get("history") or [])[-20:][::-1],
                    job=self.job, last_job=self.state.get("last_job"), can_install=bool(self.keys),
                    steps=list(STEPS), staged=self._staged(latest["version"]) if latest else None)

    # ---------- versiunea descărcată și verificată, care așteaptă „Instalează acum” ----------
    def _staged(self, version: str) -> dict | None:
        st = self.state.get("staged") or {}
        if st.get("version") == version and os.path.isfile(st.get("path") or ""):
            return {k: st.get(k) for k in ("version", "size", "at")}
        return None

    def _progress(self, step: str, label: str):
        def cb(done: int, total: int):
            pct = int(done * 100 / total) if total else None
            self.job.update(step=step, done=done, total=total, progress=pct,
                            message=f"{label}… {pct}%" if pct is not None else f"{label}… {done / 1e6:.1f} MB")
        return cb

    def _check_sha(self, tar: bytes, expected: str) -> None:
        h = hashlib.sha256()
        cb = self._progress("check", "Verific arhiva")
        step = max(1, len(tar) // 50)
        for i in range(0, len(tar), step):
            h.update(tar[i:i + step])
            cb(min(len(tar), i + step), len(tar))
        if h.hexdigest() != expected:
            raise UpdateError("Arhiva descărcată nu corespunde semnăturii (sha256 diferit). Actualizarea a fost refuzată.")

    def _prepare(self, cur: str | None) -> tuple[dict, bytes]:
        """Manifest + semnătură, spațiu, arhiva (din cea deja descărcată, dacă e aceeași versiune), sha256."""
        self._step("verify", "Descarc informațiile versiunii și verific semnătura…")
        rel = self._release()
        m = self._manifest(rel)
        ver = m["version"]
        self.job["version"] = ver
        self.state["latest"] = {k: m.get(k) for k in ("version", "notes", "date", "size")}
        if cur and not newer(ver, cur):
            raise UpdateError(f"Ai deja ultima versiune ({cur}).")
        need = (m.get("size") or 5e6) * 4 / 1e6 + (os.path.getsize(self.db_path) / 1e6 if os.path.exists(self.db_path) else 0) + 50
        self._step("space", "Verific spațiul liber…")
        for p in (self.opt_dir, self.data_dir):
            if self._free_mb(p) < need:
                raise UpdateError(f"Spațiu insuficient pe card (trebuie ~{need:.0f} MB liberi). Șterge ceva și încearcă din nou.")
        st = self.state.get("staged") or {}
        tar = None
        if st.get("version") == ver and st.get("sha256") == m["sha256"]:
            try:
                with open(st["path"], "rb") as f:
                    tar = f.read()
                self.job.update(step="download", progress=100, message="Arhiva e deja descărcată.")
            except OSError:
                tar = None
        if tar is None:
            self._step("download", f"Descarc Amper {ver}…")
            tar = self.fetch(rel["assets"][m["tarball"]], progress=self._progress("download", f"Descarc Amper {ver}"))
        self._check_sha(tar, m["sha256"])
        return m, tar

    def download(self) -> bool:
        """„Descarcă și verifică”: arhiva ajunge pe card, verificată; instalarea așteaptă confirmarea."""
        if not self.lock.acquire(blocking=False):
            raise UpdateError("O actualizare e deja în curs.")
        cur = self.installed_version()
        self.job = {"state": "running", "kind": "download", "from": cur, "step": "verify", "message": "Pornesc…",
                    "started": int(self.clock()), "progress": None}
        try:
            m, tar = self._prepare(cur)
            d = os.path.join(self.state_dir, "staged")
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
            path = os.path.join(d, m["tarball"])
            with open(path, "wb") as f:
                f.write(tar)
            self.state["staged"] = {"version": m["version"], "sha256": m["sha256"], "size": len(tar),
                                    "path": path, "at": int(self.clock())}
            self.save()
            self.job.update(state="downloaded", step="check", progress=100, finished=int(self.clock()),
                            message=f"Amper {m['version']} e descărcat și verificat (semnătură validă).")
            log.info("actualizare: %s descărcată și verificată", m["version"])
            return True
        except UpdateError as e:
            self.job.update(state="failed", message=str(e), finished=int(self.clock()))
            return False
        except Exception as e:
            log.exception("descărcare eșuată")
            self.job.update(state="failed", message=f"Eroare neprevăzută: {e}", finished=int(self.clock()))
            return False
        finally:
            self.lock.release()

    def set_channel(self, channel: str) -> dict:
        if channel not in ("stable", "beta"):
            raise UpdateError("Canal necunoscut.")
        self.channel = self.state["channel"] = channel
        self.state["latest"] = None                 # altă listă de versiuni: se caută din nou
        self.save()
        log.info("canal de actualizări: %s", channel)
        return self.status()

    # ---------- GitHub ----------
    def _fetch(self, url: str, accept: str = "application/octet-stream", limit: int = 50 << 20,
               progress=None) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "amper-update", "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if progress is None:
                    data = r.read(limit + 1)
                else:                                      # pe bucăți, cu procentul pentru aplicație
                    total = int(r.headers.get("Content-Length") or 0)
                    buf = bytearray()
                    while len(buf) <= limit:
                        chunk = r.read(64 * 1024)
                        if not chunk:
                            break
                        buf += chunk
                        progress(len(buf), total)
                    data = bytes(buf)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("Nu există încă nicio versiune publicată pe GitHub.")
            if e.code in (403, 429):
                raise UpdateError("GitHub a limitat cererile pentru moment. Încearcă din nou peste o oră.")
            raise UpdateError(f"GitHub a răspuns cu eroarea {e.code}. Încearcă mai târziu.")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise UpdateError("Fără internet: nu pot ajunge la GitHub. Verifică legătura Pi-ului și încearcă din nou.")
        if len(data) > limit:
            raise UpdateError("Fișierul descărcat e prea mare.")
        return data

    def _release(self) -> dict:
        """Ultima versiune publicată pe canalul ales (stable = fără beta)."""
        base = f"https://api.github.com/repos/{self.repo}/releases"
        if self.channel == "beta":
            rels = json.loads(self.fetch(base + "?per_page=15", "application/vnd.github+json"))
            rels = [r for r in rels if not r.get("draft")]
        else:
            rels = [json.loads(self.fetch(base + "/latest", "application/vnd.github+json"))]
        best = None
        for r in rels:
            assets = {a["name"]: a["browser_download_url"] for a in r.get("assets") or []}
            if "manifest.json" not in assets or "manifest.json.sig" not in assets:
                continue
            try:
                v = r["tag_name"].lstrip("v")
                parse_version(v)
            except (KeyError, ValueError):
                continue
            if best is None or newer(v, best["version"]):
                best = {"version": v, "assets": assets}
        if not best:
            raise UpdateError("Nu există încă nicio versiune publicată pe GitHub.")
        return best

    def _manifest(self, rel: dict) -> dict:
        mb = self.fetch(rel["assets"]["manifest.json"], limit=1 << 20)
        sig = self.fetch(rel["assets"]["manifest.json.sig"], limit=4096)
        m = verify_manifest(mb, sig, self.keys)
        if m["version"] != rel["version"]:
            raise UpdateError("Versiunea din manifest nu se potrivește cu cea publicată.")
        if m["tarball"] not in rel["assets"]:
            raise UpdateError("Arhiva versiunii lipsește de pe GitHub.")
        return m

    def check(self, auto: bool = False) -> dict:
        """Caută o versiune nouă; doar anunță (notificare o singură dată pe versiune)."""
        now = int(self.clock())
        self.state["last_check"], self.state["last_check_auto"] = now, auto
        try:
            rel = self._release()
            m = self._manifest(rel)
            self.state["latest"] = {k: m.get(k) for k in ("version", "notes", "date", "size", "urgent")}
            self.state["check_error"] = None
        except UpdateError as e:
            self.state["check_error"] = str(e)
            self.save()
            raise
        self.save()
        cur = self.installed_version()
        if cur and newer(m["version"], cur) and self.state.get("notified") != m["version"]:
            self.state["notified"] = m["version"]
            self.save()
            if self.urgent_pending():
                self.notify("Actualizare urgentă detectată. Se instalează singură.")
            else:
                self.notify("Versiune nouă detectată. Atinge pentru a actualiza.")
        return self.status()

    def urgent_pending(self) -> bool:
        """Ultima versiune găsită e marcată urgentă (în manifestul semnat) și nu a căzut deja aici."""
        lt = self.state.get("latest") or {}
        cur = self.installed_version()
        return bool(lt.get("urgent") and cur and newer(lt["version"], cur)
                    and lt["version"] != self.state.get("bad_version"))

    # ---------- comenzi de sistem ----------
    @staticmethod
    def _run(*cmd: str) -> None:
        subprocess.run(cmd, check=False, timeout=120, capture_output=True)

    def _health(self, expect: str) -> bool:
        url = f"http://127.0.0.1:{self.port}/api/health"
        end = time.monotonic() + HEALTH_TIMEOUT
        while time.monotonic() < end:
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    if json.loads(r.read()).get("version") == expect:
                        return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def _ping(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=10) as r:
                return bool(json.loads(r.read()).get("ok"))
        except Exception:
            return False

    @staticmethod
    def _svc_state() -> str:
        try:
            return subprocess.run(["systemctl", "is-active", "amper"], capture_output=True, text=True,
                                  timeout=20).stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _notify(msg: str, url: str = "/?v=update") -> None:
        """Notificările actualizărilor: titlul „Amper”; atinse, deschid Setări → Actualizări."""
        try:                              # ca utilizatorul amper (cheile Web Push, baza de date)
            subprocess.run(["runuser", "-u", "amper", "--", "env", "PYTHONPATH=/opt/amper", "python3", "-m",
                            "amper", "notify", "--title", "Amper", "--url", url, msg],
                           timeout=60, capture_output=True)
        except Exception as e:
            log.warning("notificarea nu a plecat: %s", e)

    def _switch(self, target: str) -> None:
        tmp = self.link + ".new"
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        os.symlink(target, tmp)
        if os.path.isdir(self.link) and not os.path.islink(self.link):
            shutil.rmtree(self.link)                           # instalare veche, fără legătură
        os.replace(tmp, self.link)

    def _step(self, step: str, msg: str) -> None:
        self.job.update(step=step, message=msg)
        log.info("actualizare: %s", msg)

    def _free_mb(self, path: str) -> float:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e6

    # ---------- actualizarea ----------
    def install(self, version: str | None = None) -> bool:
        if not self.lock.acquire(blocking=False):
            raise UpdateError("O actualizare e deja în curs.")
        cur = self.installed_version()
        self.job = {"state": "running", "kind": "update", "from": cur, "version": version, "step": "verify",
                    "message": "Pornesc…", "started": int(self.clock())}
        try:
            return self._install(cur)
        except UpdateError as e:
            self._finish("failed", str(e))
            return False
        except Exception as e:                              # neprevăzut: nimic schimbat dacă n-am ajuns la restart
            log.exception("actualizare eșuată")
            self._finish("failed", f"Eroare neprevăzută: {e}")
            return False
        finally:
            self.lock.release()

    def _install(self, cur: str | None) -> bool:
        m, tar = self._prepare(cur)
        ver = m["version"]
        new_dir = os.path.join(self.opt_dir, f"amper-{ver}")
        old_dir = self.current_dir()
        if os.path.realpath(new_dir) == old_dir:
            raise UpdateError(f"Versiunea {ver} rulează deja.")

        self._step("backup", "Opresc Amper și fac copia de siguranță a datelor…")
        self.run("systemctl", "stop", "amper")
        bdir = os.path.join(self.backup_root, cur or "necunoscuta")
        try:
            backup_data(bdir, self.db_path, self.etc_dir, self.data_dir, {"from": cur, "to": ver})
        except Exception as e:
            self.run("systemctl", "start", "amper")
            raise UpdateError(f"Copia de siguranță a datelor nu s-a putut face ({e}). Nu am schimbat nimic.")
        self._prune_backups(keep=bdir)

        self._step("install", f"Instalez Amper {ver}…")
        try:
            safe_extract(tar, new_dir, ver)
        except Exception:
            self.run("systemctl", "start", "amper")
            raise
        prev_before = self.state.get("previous")
        if os.path.isdir(old_dir) and os.path.islink(self.link):
            self.state["previous"] = {"version": cur, "dir": old_dir}
        elif os.path.isdir(old_dir):                           # instalare veche (folder): o păstrăm ca anterioară
            keep = os.path.join(self.opt_dir, f"amper-{cur or 'vechi'}")
            if not os.path.exists(keep):
                shutil.copytree(old_dir, keep, symlinks=True)
            self.state["previous"] = {"version": cur, "dir": keep}
            old_dir = keep
        self._switch(new_dir)
        post = os.path.join(new_dir, "deploy", "post-update.sh")
        if os.path.isfile(post):
            self.run("bash", post)

        self._step("restart", "Repornesc Amper…")
        self.run("systemctl", "restart", "amper")
        self._step("health", "Verific că Amper pornește…")
        if self.health(ver):
            self.state["installed"] = {"version": ver, "at": int(self.clock()), "dir": new_dir}
            self.state["staged"] = None
            shutil.rmtree(os.path.join(self.state_dir, "staged"), ignore_errors=True)
            self.run("systemctl", "restart", "amper-wifi")
            self._prune_versions()
            self._finish("ok", f"Amper {ver} e instalat și funcționează.")
            self.notify(f"✅ Amper actualizat la {ver}.")
            return True

        log.error("versiunea %s nu răspunde: revin la %s", ver, cur)
        self._step("health", f"Amper {ver} nu pornește. Revin la versiunea {cur} (cod și date)…")
        self.run("systemctl", "stop", "amper")
        self._switch(old_dir)
        restore_data(bdir, self.db_path, self.etc_dir, self.data_dir)
        self.run("systemctl", "start", "amper")
        ok = self.health(cur) if cur else True
        self.state["previous"] = prev_before
        shutil.rmtree(new_dir, ignore_errors=True)
        msg = (f"Amper {ver} nu a pornit; am revenit la {cur}, cu datele de dinainte."
               + ("" if ok else " ATENȚIE: nici versiunea veche nu răspunde încă — verifică jurnalul."))
        self._finish("rolled_back", msg)
        self.notify(f"⚠️ {msg}")
        return False

    def rollback(self) -> bool:
        """Revenire la versiunea anterioară, la cerere. Datele rămân (schimbările bazei de date sunt doar
        adăugiri, deci versiunea veche le poate citi); se face totuși o copie înainte."""
        if not self.lock.acquire(blocking=False):
            raise UpdateError("O actualizare e deja în curs.")
        cur = self.installed_version()
        prev = self.state.get("previous") or {}
        self.job = {"state": "running", "kind": "rollback", "from": cur, "version": prev.get("version"),
                    "step": "backup", "message": "", "started": int(self.clock())}
        try:
            if not prev.get("dir") or not os.path.isdir(prev["dir"]) or prev["dir"] == self.current_dir():
                raise UpdateError("Nu există o versiune anterioară păstrată pe Pi.")
            old = prev["version"] or code_version(prev["dir"])
            cur_dir = self.current_dir()
            self._step("backup", "Opresc Amper și fac copia de siguranță a datelor…")
            self.run("systemctl", "stop", "amper")
            bdir = os.path.join(self.backup_root, f"{cur}-revenire")
            backup_data(bdir, self.db_path, self.etc_dir, self.data_dir, {"from": cur, "to": old})
            self._prune_backups(keep=bdir)
            self._step("install", f"Revin la Amper {old}…")
            self._switch(prev["dir"])
            self._step("restart", "Repornesc Amper…")
            self.run("systemctl", "restart", "amper")
            self._step("health", "Verific că Amper pornește…")
            if self.health(old):
                self.state["previous"] = {"version": cur, "dir": cur_dir}
                self.state["installed"] = {"version": old, "at": int(self.clock()), "dir": prev["dir"]}
                self.run("systemctl", "restart", "amper-wifi")
                self._finish("ok", f"Am revenit la Amper {old}. Datele au rămas neatinse.")
                self.notify(f"↩️ Amper a revenit la versiunea {old}.")
                return True
            self.run("systemctl", "stop", "amper")
            self._switch(cur_dir)
            self.run("systemctl", "start", "amper")
            self._finish("failed", f"Versiunea {old} nu a pornit; am rămas pe {cur}.")
            return False
        except UpdateError as e:
            self._finish("failed", str(e))
            return False
        except Exception as e:
            log.exception("revenire eșuată")
            self._finish("failed", f"Eroare neprevăzută: {e}")
            return False
        finally:
            self.lock.release()

    def _finish(self, result: str, msg: str) -> None:
        self.job.update(state=result, message=msg, finished=int(self.clock()))
        self.state["last_job"] = dict(self.job)
        self.state.setdefault("history", []).append(
            {"kind": self.job.get("kind"), "from": self.job.get("from"), "version": self.job.get("version"),
             "at": int(self.clock()), "result": result, "message": msg})
        self.state["history"] = self.state["history"][-50:]
        self.save()
        (log.info if result == "ok" else log.warning)("actualizare: %s — %s", result, msg)

    def _prune_backups(self, keep: str) -> None:
        dirs = sorted((d for d in glob.glob(os.path.join(self.backup_root, "*")) if os.path.isdir(d)),
                      key=os.path.getmtime)
        for d in dirs[:-KEEP_BACKUPS]:
            if d != keep:
                shutil.rmtree(d, ignore_errors=True)

    def _prune_versions(self) -> None:
        keep = {self.current_dir(), (self.state.get("previous") or {}).get("dir")}
        dirs = sorted((d for d in glob.glob(os.path.join(self.opt_dir, "amper-*"))
                       if os.path.isdir(d) and not d.endswith((".tmp", ".new"))), key=os.path.getmtime)
        extra = [d for d in dirs if d not in keep]
        for d in extra[: max(0, len(dirs) - KEEP_VERSIONS)]:
            shutil.rmtree(d, ignore_errors=True)

    # ---------- paznicul: Amper nu mai răspunde ----------
    def watchdog_tick(self) -> str | None:
        """Un pas al paznicului (la fiecare minut). Întoarce ce a făcut: None, "restart" sau "rollback"."""
        if self.lock.locked():                        # actualizare în curs: Amper e oprit intenționat
            self.wd["down_since"] = None
            return None
        now = self.clock()
        st = self.svc_state()
        if st == "inactive":                          # oprit de mână (ex. pentru `amper scan`): nu ne amestecăm
            self.wd.update(down_since=None, restarts=0)
            return None
        if st == "active" and self.ping():
            if self.wd["notified"]:
                self.notify("✅ Amper răspunde din nou.")
            self.wd.update(down_since=None, restarts=0, notified=False)
            return None
        if self.wd["down_since"] is None:
            self.wd["down_since"] = now
            return None
        wait = DOWN_RESTART * (1 if self.wd["restarts"] < 3 else 6)    # după 3 reporniri, mai rar
        if now - self.wd["down_since"] < wait:
            return None
        cur = self.installed_version()
        inst, prev = self.state.get("installed") or {}, self.state.get("previous") or {}
        recent = (now - (inst.get("at") or 0) < RECENT_UPDATE and prev.get("dir") and os.path.isdir(prev["dir"])
                  and prev["dir"] != self.current_dir())
        if self.wd["restarts"] >= 1 and recent:
            log.error("paznic: Amper %s nu răspunde nici după repornire; revin la %s", cur, prev.get("version"))
            self.state["bad_version"] = cur           # nu se reinstalează singură (actualizare urgentă)
            self.save()
            if self.rollback():
                self.notify(f"⚠️ Amper {cur} nu mai răspundea; am revenit singur la {prev.get('version')}. "
                            "Datele au rămas.")
                self.wd.update(down_since=None, restarts=0, notified=False)
                return "rollback"
        log.warning("paznic: Amper nu răspunde de %d s (stare %s); îl repornesc", now - self.wd["down_since"], st)
        self.run("systemctl", "restart", "amper")
        self.wd["restarts"] += 1
        self.wd["down_since"] = now
        if not self.wd["notified"]:
            self.wd["notified"] = True
            self.notify("⚠️ Amper nu mai răspundea de 5 minute; l-am repornit.")
        return "restart"

    def watchdog(self) -> None:
        time.sleep(120)
        while True:
            try:
                if self.watchdog_tick() == "rollback":
                    self._restart_self()
            except Exception:
                log.exception("paznicul")
            time.sleep(WATCH_EVERY)

    # ---------- revenirea cerută de pe card (fișierul revenire.txt pe bootfs) ----------
    def card_rollback(self) -> str | None:
        from .password_reset import find_files
        files = find_files(self.boot_dirs, CARD_ROLLBACK)
        if not files:
            return None
        for f in files:                               # întâi șters: o singură revenire, chiar dacă ceva cade
            try:
                os.remove(f)
            except OSError as e:
                log.warning("nu pot șterge %s: %s", f, e)
        log.warning("revenire cerută de pe card (%s)", ", ".join(files))
        cur = self.installed_version()
        if self.rollback():
            self.state["bad_version"] = cur
            self.save()
            self.notify(f"↩️ Revenire cerută de pe card: Amper a revenit de la {cur} la {self.installed_version()}.")
            return "rollback"
        self.notify(f"⚠️ Revenire cerută de pe card: {self.job.get('message') or 'nu a mers'}")
        return "failed"

    # ---------- serviciul ----------
    def due(self) -> bool:
        every = min(self.check_days * 86400, URGENT_EVERY)
        return self.clock() - (self.state.get("last_check") or 0) >= every

    def loop(self) -> None:
        if self.card_rollback() == "rollback":
            self._restart_self()
        threading.Thread(target=self.watchdog, daemon=True).start()
        time.sleep(120)                              # după pornire: lasă rețeaua să se stabilească
        while True:
            if self.due() and not self.lock.locked():
                try:
                    self.check(auto=True)
                    if self.urgent_pending() and self.install():
                        self._restart_self()         # actualizare urgentă: instalată singură
                except UpdateError as e:
                    log.info("căutarea automată: %s", e)
                    self.state["last_check"] = int(self.clock()) - URGENT_EVERY + 3600
                    self.save()                      # fără internet: reîncearcă peste ~1 oră
                except Exception:
                    log.exception("căutarea automată a eșuat")
            time.sleep(3600)

    def start_local_api(self, token_path: str = TOKEN_PATH, port: int = API_PORT) -> ThreadingHTTPServer:
        import secrets
        token = secrets.token_hex(16)
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(token)
        try:
            import grp
            gid = grp.getgrnam("amper").gr_gid
            os.chown(token_path, 0, gid)
            os.chown(os.path.dirname(token_path), 0, gid)
            os.chmod(os.path.dirname(token_path), 0o750)
        except (KeyError, OSError):
            pass
        os.chmod(token_path, 0o640)
        up = self

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

            def do_GET(self):
                if self.headers.get("X-Token") != token:
                    return self._json({"error": "neautorizat"}, 403)
                if self.path.startswith("/remote"):
                    return self._json(up.remote.status())
                return self._json(up.status())

            def do_POST(self):
                if self.headers.get("X-Token") != token:
                    return self._json({"error": "neautorizat"}, 403)
                if self.path.startswith("/remote/"):
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                    except ValueError:
                        body = {}
                    if self.path.startswith("/remote/login"):
                        up.remote.login()
                    elif self.path.startswith("/remote/funnel"):
                        up.remote.funnel(bool(body.get("on")))
                    else:
                        return self._json({"error": "necunoscut"}, 404)
                    return self._json(up.remote.status())
                if self.path.startswith("/channel"):
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                        return self._json(up.set_channel(str(body.get("channel"))))
                    except (UpdateError, ValueError) as e:
                        return self._json({"error": str(e)}, 400)
                if self.path.startswith("/check"):
                    try:
                        return self._json(up.check())
                    except UpdateError as e:
                        return self._json({**up.status(), "error": str(e)})
                if up.lock.locked():
                    return self._json({"error": "O actualizare e deja în curs."}, 409)
                if self.path.startswith("/download"):
                    if not up.keys:
                        return self._json({"error": "Lipsește cheia de verificare a actualizărilor."}, 400)
                    up.job = {"state": "running", "kind": "download", "step": "verify", "message": "Pornesc…",
                              "started": int(up.clock())}
                    threading.Thread(target=up.download, daemon=True).start()
                    return self._json({"ok": True})
                if self.path.startswith("/install"):
                    if not up.keys:
                        return self._json({"error": "Lipsește cheia de verificare a actualizărilor."}, 400)
                    target = up._install_and_restart
                elif self.path.startswith("/rollback"):
                    target = up._rollback_and_restart
                else:
                    return self._json({"error": "necunoscut"}, 404)
                up.job = {"state": "running", "step": "verify", "message": "Pornesc…", "started": int(up.clock())}
                threading.Thread(target=target, daemon=True).start()
                return self._json({"ok": True})

        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _install_and_restart(self) -> None:
        if self.install():
            self._restart_self()

    def _rollback_and_restart(self) -> None:
        if self.rollback():
            self._restart_self()

    def _restart_self(self) -> None:
        """Codul s-a schimbat: serviciul se oprește, systemd îl repornește cu versiunea nouă (Restart=always).
        Așteaptă puțin, ca aplicația să apuce să citească rezultatul."""
        time.sleep(20)
        os._exit(0)


def main(argv=None) -> None:
    from .config import load_config
    p = argparse.ArgumentParser(prog="amper-update")
    p.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    p.add_argument("--check", action="store_true", help="caută o dată o versiune nouă și afișează rezultatul")
    p.add_argument("--install", action="store_true",
                   help="instalează acum ultima versiune, fără aplicație (cale de rezervă, prin SSH)")
    p.add_argument("--rollback", action="store_true", help="revine la versiunea anterioară, fără aplicație")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    up = Updater(load_config(args.config))
    if args.install or args.rollback:
        # Cale de rezervă dacă aplicația nu merge: aceleași verificări (semnătură, copia datelor, revenire automată).
        # Serviciul amper-update se oprește cât timp lucrează comanda (nu două actualizări odată).
        up.run("systemctl", "stop", "amper-update")
        try:
            if args.install:
                up.check()
                ok = up.install()
            else:
                ok = up.rollback()
        except UpdateError as e:
            ok, up.job["message"] = False, str(e)
        finally:
            up.run("systemctl", "start", "amper-update")
        print(up.job.get("message") or ("gata" if ok else "nu a mers"))
        raise SystemExit(0 if ok else 1)
    if args.check:
        try:
            print(json.dumps(up.check(), ensure_ascii=False, indent=1))
        except UpdateError as e:
            raise SystemExit(str(e))
        return
    log.info("amper-update: versiunea instalată %s, canal %s, %s", up.installed_version(), up.channel, up.repo)
    if not up.keys:
        log.warning("lipsește cheia publică (amper/update_key.py): actualizările se pot căuta, dar nu instala")
    up.start_local_api()
    up.loop()


if __name__ == "__main__":
    main()

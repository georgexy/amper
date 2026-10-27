"""Parola aplicației, cookie-ul de sesiune, blocarea după parole greșite, dispozitivele noi.

- Parola: `web.password_hash` (scrypt, setată din aplicație, în settings.yaml) sau `web.password` (text, în
  /etc/amper/config.yaml, ca la instalările vechi). Hash-ul are prioritate.
- Cookie-ul `amper` = HMAC(cheie, amprenta parolei): o parolă nouă deloghează toate browserele. Cheia
  (`cookie.key`) se generează la prima pornire, lângă baza de date.
- Blocare: după 5 parole greșite de la aceeași adresă, așteptare 30 s, apoi dublă la fiecare greșeală (max. 1 h);
  peste 20 de greșeli în 15 minute, de oriunde → toate logările de la distanță blocate 5 minute.
- Browser nou (cookie `amper_dev` necunoscut) logat cu succes → notificare „🔐 Dispozitiv nou conectat”.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

log = logging.getLogger(__name__)

MAX_FAILS = 5
BASE_LOCK = 30
MAX_LOCK = 3600
GLOBAL_FAILS, GLOBAL_WINDOW, GLOBAL_LOCK = 20, 900, 300
MIN_PASSWORD = 8


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_hash(pw: str, stored: str) -> bool:
    try:
        kind, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def short_ua(ua: str) -> str:
    """„iPhone · Safari”, „Android · Chrome”, „Windows · Edge” — pentru notificări."""
    ua = ua or ""
    dev = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                               ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux")) if k in ua), "?")
    br = next((n for k, n in (("Edg/", "Edge"), ("Firefox", "Firefox"), ("Chrome", "Chrome"),
                              ("Safari", "Safari")) if k in ua), "browser")
    return f"{dev} · {br}"


class Auth:
    def __init__(self, cfg: dict, db=None, notifier=None, state_dir: str | None = None, clock=time.time):
        self.cfg, self.db, self.notifier, self.clock = cfg, db, notifier, clock
        self.state_dir = state_dir
        self._key = self._load_key()
        self.fails: dict[str, list] = {}          # ip → [greșeli la rând, blocat până la]
        self.recent: list[float] = []             # momentele greșelilor (global)
        self.global_until = 0.0

    # ---------- parola ----------
    @property
    def web(self) -> dict:
        return self.cfg.setdefault("web", {})

    @property
    def enabled(self) -> bool:
        return bool(self.web.get("password_hash") or self.web.get("password"))

    def check(self, pw: str) -> bool:
        pw = str(pw or "")
        if self.web.get("password_hash"):
            return verify_hash(pw, self.web["password_hash"])
        plain = self.web.get("password") or ""
        return bool(plain) and hmac.compare_digest(pw.encode(), plain.encode())

    def _fingerprint(self) -> str:
        return self.web.get("password_hash") or hashlib.sha256(("amper:" + (self.web.get("password") or "")).encode()).hexdigest()

    def _load_key(self) -> bytes:
        if not self.state_dir:
            return b"amper-test-key"
        p = os.path.join(self.state_dir, "cookie.key")
        try:
            with open(p, "rb") as f:
                k = f.read()
            if len(k) >= 32:
                return k
        except FileNotFoundError:
            pass
        k = secrets.token_bytes(32)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        return k

    @property
    def token(self) -> str:
        return hmac.new(self._key, ("session:" + self._fingerprint()).encode(), "sha256").hexdigest()

    def valid_cookie(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value, self.token)

    def set_password(self, pw: str) -> str:
        """Parolă nouă (din aplicație): hash în settings.yaml; întoarce hash-ul."""
        from .config import save_overlay
        if len(pw or "") < MIN_PASSWORD:
            raise ValueError(f"Parola trebuie să aibă cel puțin {MIN_PASSWORD} caractere.")
        h = hash_password(pw)
        save_overlay(self.cfg, {"web": {"password_hash": h}})
        self.web["password_hash"] = h
        log.warning("parola aplicației a fost schimbată")
        return h

    # ---------- blocarea ----------
    def blocked_for(self, ip: str) -> int:
        """Secunde până când adresa poate încerca din nou (0 = poate)."""
        now = self.clock()
        until = max(self.global_until, (self.fails.get(ip) or [0, 0])[1])
        return max(0, int(until - now + 0.999))

    def attempt(self, ip: str, pw: str, ua: str = "", what: str = "logare") -> tuple[bool, int]:
        """Verifică parola ținând cont de blocare. Întoarce (corectă, secunde de blocare)."""
        wait = self.blocked_for(ip)
        if wait:
            self._log(ip, False, ua, f"{what}: blocat")
            return False, wait
        if self.check(pw):
            self.fails.pop(ip, None)
            self._log(ip, True, ua, what)
            return True, 0
        now = self.clock()
        n, _ = self.fails.get(ip) or [0, 0]
        n += 1
        until = now + min(MAX_LOCK, BASE_LOCK * 2 ** (n - MAX_FAILS)) if n >= MAX_FAILS else 0
        self.fails[ip] = [n, until]
        self.recent = [t for t in self.recent if now - t < GLOBAL_WINDOW] + [now]
        self._log(ip, False, ua, what)
        if len(self.recent) >= GLOBAL_FAILS and self.global_until < now:
            self.global_until = now + GLOBAL_LOCK
            self._notify(f"🔐 {len(self.recent)} parole greșite în ultimele 15 minute: logarea de la distanță "
                         f"e blocată {GLOBAL_LOCK // 60} minute.", "high")
        elif n == MAX_FAILS:
            self._notify(f"🔐 {n} parole greșite la rând de la {ip} ({short_ua(ua)}). Încercările sunt încetinite.",
                         "high")
        log.warning("parolă greșită (%s) de la %s, %d la rând", what, ip, n)
        return False, self.blocked_for(ip)

    def _log(self, ip, ok, ua, note):
        if self.db is not None:
            try:
                self.db.log_login(int(self.clock()), ip, ok, ua or "", note)
            except Exception:
                log.exception("jurnalul de logări")

    def _notify(self, msg: str, priority: str = "default") -> None:
        if self.notifier is not None:
            self.notifier.send(msg, title="Amper: securitate", priority=priority)

    # ---------- dispozitive ----------
    def seen_browser(self, dev_id: str | None, ip: str, ua: str, quiet: bool = False) -> str:
        """După o logare reușită: browserul are un id; dacă e nou, notificare. Întoarce id-ul."""
        if not dev_id or len(dev_id) != 32:
            dev_id = secrets.token_hex(16)
        if self.db is not None and self.db.login_device(dev_id, int(self.clock()), ip, ua or "") and not quiet:
            self._notify(f"🔐 Dispozitiv nou conectat la Amper: {short_ua(ua)}, de la {ip}.", "high")
        return dev_id

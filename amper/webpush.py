"""Web Push: notificări trimise direct aplicației Amper de pe telefon (fără altă aplicație).

Standardele: RFC 8291 (criptarea mesajului, aes128gcm) și RFC 8292 (VAPID: Pi-ul se identifică la serviciul de
push al telefonului — Apple, Google, Mozilla). Cheile VAPID se generează o dată, în <director date>/vapid.json.
Telefonul se abonează din aplicație (doar prin https); abonamentele stau în tabela push_subs.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)


def public_url(cfg: dict) -> str:
    """Adresa https a aplicației: web.public_url din config, altfel numele Tailscale al Pi-ului."""
    url = (cfg.get("web") or {}).get("public_url")
    if url:
        return url.rstrip("/")
    try:
        import subprocess
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10).stdout
        name = json.loads(out)["Self"]["DNSName"].rstrip(".")
        if name:
            return f"https://{name}"
    except Exception:
        pass
    return "https://amper.local"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def encrypt(payload: bytes, ua_public_b64: str, auth_b64: str, salt: bytes | None = None,
            as_private: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Criptează un mesaj pentru un abonament (RFC 8291, o singură înregistrare aes128gcm)."""
    ua_public = unb64u(ua_public_b64)
    auth = unb64u(auth_b64)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _pub_bytes(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth, ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_public)]) + as_public + ct


class Vapid:
    def __init__(self, path: str):
        self.path = path
        self.key = self._load_or_create()
        self.public_b64 = b64u(_pub_bytes(self.key.public_key()))

    def _load_or_create(self) -> ec.EllipticCurvePrivateKey:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return serialization.load_pem_private_key(d["private_pem"].encode(), password=None)
        except (OSError, ValueError, KeyError):
            pass
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"private_pem": pem}, f)
            os.chmod(self.path, 0o600)
        except OSError as e:
            log.warning("nu pot salva cheile Web Push în %s: %s", self.path, e)
        return key

    def header(self, endpoint: str, subject: str) -> str:
        u = urlsplit(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}
        signing = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." +
                   b64u(json.dumps(claims, separators=(",", ":")).encode()))
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={jwt}, k={self.public_b64}"


class WebPush:
    """Trimite notificările la toate telefoanele abonate. `db` are push_subs / add_push / delete_push."""

    def __init__(self, db, state_dir: str, subject: str = "https://amper.local"):
        self.db = db
        self.vapid = Vapid(os.path.join(state_dir, "vapid.json"))
        self.subject = subject

    @property
    def enabled(self) -> bool:
        return bool(self.db.push_subs())

    async def send(self, http, item: dict) -> None:
        payload = json.dumps({"title": item["title"], "body": item["message"], "priority": item["priority"]},
                             ensure_ascii=False).encode()
        for sub in self.db.push_subs():
            body = encrypt(payload, sub["p256dh"], sub["auth"])
            headers = {"TTL": str(24 * 3600), "Content-Encoding": "aes128gcm",
                       "Content-Type": "application/octet-stream",
                       "Urgency": "high" if item["priority"] == "high" else "normal",
                       "Authorization": self.vapid.header(sub["endpoint"], self.subject)}
            async with http.post(sub["endpoint"], data=body, headers=headers) as r:
                if r.status in (404, 410):          # telefonul a renunțat la abonament
                    self.db.delete_push(sub["endpoint"])
                    log.info("abonament Web Push expirat, șters (%s)", urlsplit(sub["endpoint"]).netloc)
                elif r.status >= 400:
                    raise RuntimeError(f"Web Push {urlsplit(sub['endpoint']).netloc}: {r.status} {await r.text()}")

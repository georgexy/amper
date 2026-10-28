"""Ediția Amper: Lite (supraveghere) sau Pro (și setările BMS și ale invertorului). Un singur program.

- Ediția de bază se hotărăște o singură dată, la prima pornire a unei versiuni care știe de ediții, și se ține în
  settings.yaml (`edition_base`): Pi-urile care aveau deja Amper (parolă sau citiri în baza de date) rămân Pro;
  o instalare nouă (imaginea SD) pornește Lite. Cerut de utilizator 2026-10-28.
- Pro se activează cu un cod: semnătura Ed25519 a textului `amper-pro:v1:<ID Pi>`, cu aceeași cheie ca
  actualizările (`tools/release.py pro <ID>` sau workflow-ul „Cod Pro”). Codul merge doar pe Pi-ul lui și se
  verifică fără internet. ID-ul Pi-ului vine din /etc/machine-id (refăcut la prima pornire a imaginii).
"""
from __future__ import annotations

import base64
import hashlib
import logging

log = logging.getLogger("amper.edition")

MACHINE_ID = ("/etc/machine-id", "/var/lib/dbus/machine-id")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # fără 0/O și 1/I, ca să se poată dicta


def pi_id(paths=MACHINE_ID) -> str:
    """ID-ul scurt al Pi-ului, ex. „7K3F-Q9PA” (8 caractere din amprenta lui machine-id)."""
    raw = ""
    for p in paths:
        try:
            with open(p, encoding="ascii") as f:
                raw = f.read().strip()
        except OSError:
            continue
        if raw:
            break
    h = int.from_bytes(hashlib.sha256(("amper:" + raw).encode()).digest()[:8], "big")
    s = "".join(ALPHABET[(h >> (5 * i)) & 31] for i in range(8))
    return f"{s[:4]}-{s[4:]}"


def norm_id(s: str) -> str:
    s = "".join(c for c in str(s).upper() if c.isalnum())
    return f"{s[:4]}-{s[4:8]}" if len(s) == 8 else s


def message(pid: str) -> bytes:
    return f"amper-pro:v1:{norm_id(pid)}".encode()


def make_code(private_key, pid: str) -> str:
    """Codul Pro pentru un Pi (private_key = Ed25519PrivateKey). Folosit de tools/release.py."""
    return base64.urlsafe_b64encode(private_key.sign(message(pid))).decode().rstrip("=")


def code_ok(code: str, pid: str, keys: list[str] | None = None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if keys is None:
        from .update_key import PUBLIC_KEYS as keys
    c = "".join(str(code or "").split())
    try:
        sig = base64.urlsafe_b64decode(c + "=" * (-len(c) % 4))
    except ValueError:
        return False
    if len(sig) != 64:
        return False
    for k in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, message(pid))
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def init_base(cfg: dict, db) -> str:
    """Ediția de bază, hotărâtă o singură dată și salvată (vezi sus). Simulatorul e Pro (sau `edition: lite`)."""
    from .config import ConfigError, password_set, save_overlay
    if cfg.get("edition_base") in ("lite", "pro"):
        return cfg["edition_base"]
    if all(d["type"].startswith("sim_") for d in cfg["devices"]) and cfg["devices"]:
        cfg["edition_base"] = cfg.get("edition") if cfg.get("edition") in ("lite", "pro") else "pro"
        return cfg["edition_base"]                  # simulatorul: nu se salvează nimic
    existing = password_set(cfg) or bool(cfg.get("setup_done")) or db.has_samples()
    base = "pro" if existing else "lite"
    cfg["edition_base"] = base
    try:
        save_overlay(cfg, {"edition_base": base})
    except (ConfigError, OSError) as e:
        log.warning("nu pot salva ediția: %s", e)
    log.info("ediția de bază: %s (%s)", base, "instalare existentă" if existing else "instalare nouă")
    return base


def current(cfg: dict, pid: str | None = None) -> str:
    if cfg.get("edition_base") == "pro":
        return "pro"
    if cfg.get("pro_code") and code_ok(cfg["pro_code"], pid or pi_id()):
        return "pro"
    return "lite"

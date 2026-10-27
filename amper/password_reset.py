"""Recuperarea parolei uitate, de pe card: scoți cardul, pui pe partiția „bootfs” (cea care se vede în Windows/Mac)
un fișier `resetare-parola.txt` (orice conținut), bagi cardul la loc și pornești Pi-ul.

Rulează ca root la fiecare pornire, înaintea serviciului amper (amper-password-reset.service). Dacă găsește
fișierul: șterge parola (în settings.yaml, peste config.yaml), pune `password_reset` și șterge fișierul. Aplicația
cere apoi o parolă nouă — doar din rețeaua de acasă, nu prin adresa publică (web.py), și te anunță pe telefon.
Cine are cardul în mână are oricum totul, deci fișierul nu slăbește securitatea.
"""
from __future__ import annotations

import argparse
import logging
import os
import pwd
import time

log = logging.getLogger("amper.password_reset")

BOOT_DIRS = ("/boot/firmware", "/boot")
NAMES = ("resetare-parola", "reset-parola", "reset-password")


def find_files(boot_dirs=BOOT_DIRS) -> list[str]:
    """Fișierele de resetare (orice extensie, orice majuscule: Windows ascunde „.txt”)."""
    out = []
    for d in boot_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.lower().split(".")[0].strip() in NAMES:            # și „resetare-parola.txt.txt”
                out.append(os.path.join(d, n))
    return out


def reset(cfg_path: str = "/etc/amper/config.yaml", boot_dirs=BOOT_DIRS, owner: str | None = "amper") -> bool:
    files = find_files(boot_dirs)
    if not files:
        return False
    from .config import load_config, overlay_path, save_overlay
    cfg = load_config(cfg_path)
    save_overlay(cfg, {"web": {"password": "", "password_hash": ""}, "password_reset": int(time.time())})
    p = overlay_path(cfg)
    if owner:
        try:
            u = pwd.getpwnam(owner)
            os.chown(p, u.pw_uid, u.pw_gid)             # settings.yaml rămâne al serviciului amper
        except (KeyError, OSError) as e:
            log.warning("nu pot da %s utilizatorului %s: %s", p, owner, e)
    for f in files:
        try:
            os.remove(f)
        except OSError as e:
            log.warning("nu pot șterge %s: %s", f, e)
    log.warning("parola aplicației a fost ștearsă (fișierul %s de pe card)", ", ".join(files))
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Șterge parola aplicației dacă pe card e fișierul resetare-parola.txt")
    ap.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("parola ștearsă" if reset(a.config) else "niciun fișier de resetare")


if __name__ == "__main__":
    main()

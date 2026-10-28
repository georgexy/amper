"""Lansarea unei versiuni Amper: arhivă + manifest semnat Ed25519 (rulat de GitHub Actions, .github/workflows/release.yml).

  python tools/release.py keygen                 cheie nouă: privata → secretul GitHub AMPER_SIGNING_KEY,
                                                 publica → amper/update_key.py
  python tools/release.py build --out dist       arhiva amper-<ver>.tar.gz, manifest.json, manifest.json.sig,
                                                 notes.md (cheia privată din variabila AMPER_SIGNING_KEY)
  python tools/release.py verify dist            verifică semnătura cu cheile din amper/update_key.py
  python tools/release.py version                afișează versiunea din amper/__init__.py
  python tools/release.py notes [versiune]       notele din CHANGELOG.md
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ce intră în arhivă (programul de pe Pi); testele și uneltele rămân doar în repo
INCLUDE = ("amper", "web", "deploy", "config.example.yaml", "CHANGELOG.md", "LICENSE", "README.md")


def version(root: Path = ROOT) -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "amper" / "__init__.py").read_text(encoding="utf-8"))
    if not m:
        sys.exit("nu găsesc __version__ în amper/__init__.py")
    return m.group(1)


def notes(ver: str, root: Path = ROOT) -> str:
    """Secțiunea `## <ver>` din CHANGELOG.md (până la următorul `## `)."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(ver)}\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m or not m.group(1).strip():
        sys.exit(f"CHANGELOG.md nu are o secțiune „## {ver}” cu note")
    return m.group(1).strip()


def _mtime(root: Path) -> int:
    """Data ultimului commit (arhivă reproductibilă); altfel acum."""
    try:
        return int(subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%ct"], text=True).strip())
    except Exception:
        return int(time.time())


def build_tarball(ver: str, root: Path = ROOT, mtime: int | None = None) -> bytes:
    mtime = _mtime(root) if mtime is None else mtime
    prefix = f"amper-{ver}"
    files: list[Path] = []
    for name in INCLUDE:
        p = root / name
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"]
        elif p.is_file():
            files.append(p)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=mtime) as gz, tarfile.open(fileobj=gz, mode="w") as tf:
        dirs = sorted({d for f in files for d in f.relative_to(root).parents if str(d) != "."})
        for d in dirs:
            ti = tarfile.TarInfo(f"{prefix}/{d.as_posix()}")
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, mtime
            tf.addfile(ti)
        for f in sorted(files):
            data = f.read_bytes()
            ti = tarfile.TarInfo(f"{prefix}/{f.relative_to(root).as_posix()}")
            ti.size, ti.mode, ti.mtime = len(data), 0o755 if f.suffix == ".sh" else 0o644, mtime
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _private_key(b64: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError:
        sys.exit("AMPER_SIGNING_KEY nu e o cheie privată validă (base64, 32 de octeți)")


def public_b64(priv) -> str:
    from cryptography.hazmat.primitives import serialization
    return base64.b64encode(priv.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw)).decode()


def build(out: Path, key_b64: str, root: Path = ROOT, mtime: int | None = None, urgent: bool = False) -> dict:
    ver = version(root)
    note = notes(ver, root)
    tar = build_tarball(ver, root, mtime)
    out.mkdir(parents=True, exist_ok=True)
    name = f"amper-{ver}.tar.gz"
    (out / name).write_bytes(tar)
    manifest = {"version": ver, "tarball": name, "sha256": hashlib.sha256(tar).hexdigest(), "size": len(tar),
                "date": time.strftime("%Y-%m-%d", time.gmtime(_mtime(root) if mtime is None else mtime)),
                "notes": note}
    if urgent:                     # anunțată ca importantă (până la 1.0.0-beta.2, Pi-urile o instalau singure)
        manifest["urgent"] = True
    mb = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
    (out / "manifest.json").write_bytes(mb)
    sig = _private_key(key_b64).sign(mb)
    (out / "manifest.json.sig").write_text(base64.b64encode(sig).decode() + "\n")
    (out / "notes.md").write_text(note + "\n", encoding="utf-8")
    return manifest


def verify(out: Path, root: Path = ROOT) -> dict:
    sys.path.insert(0, str(root))
    from amper.update import verify_manifest
    from amper.update_key import PUBLIC_KEYS
    m = verify_manifest((out / "manifest.json").read_bytes(), (out / "manifest.json.sig").read_text(), PUBLIC_KEYS)
    if hashlib.sha256((out / m["tarball"]).read_bytes()).hexdigest() != m["sha256"]:
        sys.exit("sha256 diferit")
    return m


def keygen() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    priv = base64.b64encode(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                            serialization.NoEncryption())).decode()
    print(f"""
CHEIA PRIVATĂ (secretă — n-o trimite nimănui, n-o pune în repo):

    {priv}

  → GitHub: repo-ul → Settings → Secrets and variables → Actions → New repository secret
    Name: AMPER_SIGNING_KEY    Secret: rândul de mai sus
  Păstreaz-o și într-un loc sigur (de ex. un manager de parole): fără ea nu se mai pot lansa versiuni
  pe care Pi-urile existente să le accepte.

CHEIA PUBLICĂ (nu e secretă — se pune în amper/update_key.py, PUBLIC_KEYS):

    {public_b64(k)}
""")


def main() -> None:
    p = argparse.ArgumentParser(prog="release.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen")
    sub.add_parser("version")
    n = sub.add_parser("notes")
    n.add_argument("ver", nargs="?")
    b = sub.add_parser("build")
    b.add_argument("--out", default="dist")
    b.add_argument("--urgent", action="store_true", help="actualizare importantă (doar anunțată; Pi-urile până la 1.0.0-beta.2 o instalează singure!)")
    v = sub.add_parser("verify")
    v.add_argument("out", nargs="?", default="dist")
    a = p.parse_args()
    if a.cmd == "keygen":
        keygen()
    elif a.cmd == "version":
        print(version())
    elif a.cmd == "notes":
        print(notes(a.ver or version()))
    elif a.cmd == "build":
        key = os.environ.get("AMPER_SIGNING_KEY", "")
        if not key:
            sys.exit("Lipsește AMPER_SIGNING_KEY (secretul repo-ului; vezi `python tools/release.py keygen`).")
        m = build(Path(a.out), key, urgent=a.urgent)
        print(f"Amper {m['version']}: {m['tarball']} ({m['size'] / 1e6:.2f} MB), sha256 {m['sha256']}"
              + (" — URGENTĂ" if m.get("urgent") else ""))
    elif a.cmd == "verify":
        m = verify(Path(a.out))
        print(f"semnătura e validă: Amper {m['version']}")


if __name__ == "__main__":
    main()

"""Export pentru diagnostic (Setări → Despre → Export pentru diagnostic, cerut 2026-11-17).

Un singur fișier .zip, de citit de om (Excel) și de Claude, la depanare: citirile brute pe perioada aleasă (CSV),
alertele, notificările, setările schimbate, jurnalele Pi-ului, starea de acum și un rezumat. Fără date secrete:
parolele, cheile, topicul ntfy, adresa Tailscale, numele rețelelor WiFi și adresele de e-mail sunt înlocuite cu
„<ascuns>” (în config, în jurnale și în starea de acum). Adresele Bluetooth ale BMS-urilor rămân (ajută la depanare).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import time
import zipfile

from . import __version__
from .db import INV_COLS, PACK_COLS

MAX_DAYS = 14                                     # cât țin citirile brute pe Pi (raw_retention_days, implicit)
UNITS = ("amper", "amper-update", "amper-wifi", "amper-backup", "amper-firstboot", "amper-password-reset",
         "amper-kiosk", "bluetooth", "NetworkManager", "tailscaled")
LOG_DIR = "/var/log/amper"
JOURNAL_LINES = 40000                             # cel mult atâtea rânduri din jurnal (cele mai noi)
HIDDEN = "<ascuns>"
SECRET_KEYS = re.compile(r"pass|token|secret|topic|chat_id|public_url|pro_code|key|subject|bot", re.I)
TAILNET = re.compile(r"\b[\w-]+\.[\w-]+\.ts\.net\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def iso(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts))) if ts is not None else ""


# ---------------------------------------------------------------- ce trebuie ascuns
def secrets_of(cfg: dict, extra=()) -> list[str]:
    """Valorile secrete din config (parole, chei, topicul ntfy, adresa publică) + `extra` (ex. numele WiFi)."""
    out: list[str] = []

    def walk(x, key=""):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, str(k))
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and SECRET_KEYS.search(key) and len(x) >= 4 and "REPLACE" not in x:
            out.append(x)
            m = re.match(r"https?://([^/:]+)", x)               # și numele gazdei din adrese
            if m:
                out.append(m.group(1))
    walk(cfg)
    out += [s for s in extra if s and len(s) >= 3]
    return sorted(set(out), key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, HIDDEN)
    text = TAILNET.sub("<tailnet>", text)
    return EMAIL.sub("<email>", text)


def clean_config(cfg: dict) -> dict:
    def walk(x, key=""):
        if isinstance(x, dict):
            return {k: walk(v, str(k)) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        if isinstance(x, str) and x and SECRET_KEYS.search(key) and "REPLACE" not in x:
            return HIDDEN
        return x
    return walk(cfg)


def wifi_names() -> list[str]:
    """Numele rețelelor WiFi știute de Pi (profilurile NetworkManager), ca să fie ascunse din jurnale."""
    names = []
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], capture_output=True,
                             text=True, timeout=10).stdout
        for line in out.splitlines():
            name, _, typ = line.rpartition(":")
            if "wireless" in typ:
                names += [name, name.removeprefix("wifi-")]
    except Exception:
        pass
    return [n for n in names if n and n not in ("Amper-Setup", "Hotspot")]


# ---------------------------------------------------------------- bucățile arhivei
def _csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    return buf.getvalue()


def samples_csv(db, kind: str, start: int, end: int, table: str | None = None) -> tuple[str, int, dict]:
    """Citirile din interval (brute sau mediile la 5 min) → CSV, numărul de rânduri, golurile pe dispozitiv."""
    cols = PACK_COLS if kind == "pack" else INV_COLS
    table = table or ("pack_samples" if kind == "pack" else "inverter_samples")
    extra = ", cells" if table == "pack_samples" else ""
    with db.lock:
        cur = db.conn.execute(f"SELECT ts, device, {', '.join(cols)}{extra} FROM {table} WHERE ts BETWEEN ? AND ? "
                              "ORDER BY device, ts", (start, end))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ora", "ts", "device", *cols, *(["celule_V"] if extra else [])])
        n, last, gaps = 0, {}, {}
        for r in cur:
            r = list(r)
            ts, dev = r[0], r[1]
            if extra:
                try:
                    r[-1] = ";".join(f"{v:.3f}" for v in json.loads(r[-1])) if r[-1] else ""
                except (ValueError, TypeError):
                    pass
            w.writerow([iso(ts), *("" if v is None else round(v, 4) if isinstance(v, float) else v for v in r)])
            n += 1
            if dev in last and ts - last[dev] > 300:                  # gol de peste 5 minute
                gaps.setdefault(dev, []).append((last[dev], ts))
            last[dev] = ts
    return buf.getvalue(), n, gaps


def table_csv(db, sql: str, args: tuple, ts_cols=("ts",)) -> tuple[str, int]:
    with db.lock:
        cur = db.conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    out_cols = [f"{c}_ora" for c in ts_cols if c in cols] + cols
    for r in rows:
        for c in ts_cols:
            if c in r:
                r[f"{c}_ora"] = iso(r[c]) if r[c] else ""
    return _csv(rows, out_cols), len(rows)


def journal(start: int, end: int, run=subprocess.run) -> str:
    """Jurnalul sistemului pe perioadă: serviciile Amper, Bluetooth, rețeaua, Tailscale, plus mesajele nucleului
    (USB scos/pus, subtensiune). Cere ca utilizatorul amper să fie în grupul systemd-journal."""
    args = ["journalctl", "--no-pager", "-o", "short-iso", "--since", f"@{start}", "--until", f"@{end}",
            "-n", str(JOURNAL_LINES)]
    out = []
    for name, extra in (("servicii", [a for u in UNITS for a in ("-u", u)]), ("nucleu", ["-k"])):
        try:
            r = run([*args, *extra], capture_output=True, text=True, timeout=60)
            text = r.stdout.strip() or r.stderr.strip() or "(nimic)"
        except Exception as e:
            text = f"(jurnalul nu s-a putut citi: {e})"
        out.append(f"===== {name} =====\n{text}\n")
    return "\n".join(out)


def file_tails(start: int) -> dict[str, str]:
    """Jurnalele Amper pe fișier (copia de siguranță, actualizările), rândurile din perioadă (după dată)."""
    out = {}
    day0 = time.strftime("%Y-%m-%d", time.localtime(start))
    try:
        names = sorted(os.listdir(LOG_DIR))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".log"):
            continue
        try:
            with open(os.path.join(LOG_DIR, name), encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        keep = [ln for ln in lines if ln[:10] >= day0 or not ln[:4].isdigit()]   # rândurile fără dată: continuări
        out[name] = "".join(keep[-20000:])
    return out


# ---------------------------------------------------------------- arhiva
def build(engine, start: int, end: int, out, system: dict | None = None, run=subprocess.run,
          now: float | None = None) -> dict:
    """Scrie arhiva .zip în `out` (fișier deschis binar sau cale). Întoarce rezumatul (dict)."""
    db, cfg = engine.db, engine.cfg
    now = now or time.time()
    start, end = int(start), int(min(end, now))
    secrets = secrets_of(cfg, wifi_names() + ([(system or {}).get("network", {}).get("wifi", {}).get("ssid")]
                                               if system and (system.get("network") or {}).get("wifi") else []))
    files: dict[str, str] = {}
    counts: dict[str, int] = {}

    packs, n, gaps_p = samples_csv(db, "pack", start, end)
    files["date/baterii.csv"], counts["citiri baterii"] = packs, n
    inv, n, gaps_i = samples_csv(db, "inverter", start, end)
    files["date/invertor.csv"], counts["citiri invertor"] = inv, n
    raw_from = now - int(cfg.get("raw_retention_days") or MAX_DAYS) * 86400
    if start < raw_from:                                      # mai vechi decât citirile brute: mediile la 5 minute
        files["date/baterii_medii_5min.csv"], counts["medii 5 min baterii"], _ = samples_csv(db, "pack", start, end, "pack_5m")
        files["date/invertor_medii_5min.csv"], counts["medii 5 min invertor"], _ = samples_csv(db, "inverter", start, end, "inverter_5m")

    day0, day1 = time.strftime("%Y-%m-%d", time.localtime(start)), time.strftime("%Y-%m-%d", time.localtime(end))
    for name, sql, args, tsc in (
        ("alerte", "SELECT * FROM alerts WHERE ts_start <= ? AND (ts_end IS NULL OR ts_end >= ?) ORDER BY ts_start",
         (end, start), ("ts_start", "ts_end")),
        ("notificari", "SELECT * FROM notifications WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end), ("ts",)),
        ("setari_schimbate", "SELECT * FROM settings_log WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end), ("ts",)),
        ("energie_zilnica", "SELECT * FROM energy_daily WHERE day BETWEEN ? AND ? ORDER BY day, device", (day0, day1), ()),
        ("celule_evenimente", "SELECT * FROM cell_events WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end), ("ts",)),
    ):
        try:
            files[f"evenimente/{name}.csv"], counts[name] = table_csv(db, sql, args, tsc)
        except Exception as e:                                # o tabelă lipsă (bază veche) nu strică exportul
            files[f"evenimente/{name}.txt"] = f"nu s-a putut citi: {e}\n"

    snap = engine.snapshot(now)
    files["stare/acum.json"] = redact(json.dumps(snap, ensure_ascii=False, indent=1, default=str), secrets)
    files["stare/config.json"] = redact(json.dumps(clean_config(cfg), ensure_ascii=False, indent=1, default=str), secrets)
    if system is not None:
        files["stare/pi.json"] = redact(json.dumps(system, ensure_ascii=False, indent=1, default=str), secrets)
    files["jurnale/sistem.txt"] = redact(journal(start, end, run), secrets)
    for name, text in file_tails(start).items():
        files[f"jurnale/{name}"] = redact(text, secrets)

    devs = snap.get("devices") or {}
    lines = [
        "Amper — export pentru diagnostic",
        f"Versiunea: {__version__}",
        f"Perioada: {iso(start)} → {iso(end)} ({(end - start) / 3600:.1f} ore)",
        f"Făcut la: {iso(now)}",
        "",
        "Dispozitive:",
    ]
    for d in cfg.get("devices") or []:
        st = devs.get(d["id"]) or {}
        lines.append(f"  - {d['id']} ({d.get('name', '')}, {d['type']}): "
                     f"{'comunică' if st.get('connected') and not st.get('stale') else 'NU comunică acum'}"
                     f"{', legătura: ' + str((st.get('reading') or {}).get('raw', {}).get('link')) if (st.get('reading') or {}).get('raw', {}).get('link') else ''}")
    lines += ["", "Rânduri:"] + [f"  - {k}: {v}" for k, v in counts.items()]
    # golurile: cele comune tuturor dispozitivelor (Pi-ul oprit / Amper oprit) pe un singur rând
    by_gap: dict[tuple, list] = {}
    for dev, gs in {**gaps_p, **gaps_i}.items():
        for a, b in gs:
            by_gap.setdefault((a // 60, b // 60), []).append((dev, a, b))
    all_devs = {d["id"] for d in cfg.get("devices") or []}
    lines += ["", "Goluri în citiri (peste 5 minute fără date):"]
    for (_, _), items in sorted(by_gap.items())[:60]:
        a, b = min(i[1] for i in items), max(i[2] for i in items)
        who = "toate dispozitivele" if {i[0] for i in items} >= all_devs and len(all_devs) > 1 else \
            ", ".join(sorted({i[0] for i in items}))
        lines.append(f"  - {who}: {iso(a)} → {iso(b)} ({(b - a) / 60:.0f} min)")
    if len(by_gap) > 60:
        lines.append(f"  - încă {len(by_gap) - 60} goluri")
    if not by_gap:
        lines.append("  - niciunul")
    lines += ["", "Fișiere:", "  date/        citirile (CSV; curentul + = încărcare; celule_V = tensiunile celulelor, separate cu ;)",
              "  evenimente/  alerte, notificări, setări schimbate, energia pe zile, sănătatea celulelor",
              "  stare/       starea de acum, config-ul (fără parole și chei), Pi-ul",
              "  jurnale/     jurnalul sistemului și jurnalele Amper pe perioadă",
              "", f"Ascunse: parolele, cheile, topicul ntfy, adresa Tailscale, numele rețelelor WiFi ({HIDDEN})."]
    files["CITESTE-MA.txt"] = "\n".join(lines) + "\n"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in ["CITESTE-MA.txt", *sorted(k for k in files if k != "CITESTE-MA.txt")]:
            z.writestr(name, files[name])
    return dict(start=start, end=end, counts=counts, files=sorted(files))


def filename(start: int, end: int) -> str:
    f = "%Y%m%d-%H%M"
    return f"amper-diagnostic-{time.strftime(f, time.localtime(start))}-{time.strftime(f, time.localtime(end))}.zip"

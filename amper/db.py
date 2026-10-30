"""Baza de date SQLite: eșantioane, agregate la 5 minute, energie zilnică, alerte."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import InverterReading, PackReading

PACK_COLS = ["voltage", "current", "power", "soc", "remaining_ah", "cell_min", "cell_max",
             "cell_delta", "temp_max", "charge_mos", "discharge_mos"]
INV_COLS = ["pv_power", "load_power", "battery_power", "battery_voltage", "battery_soc",
            "ac_in_power", "pv_voltage", "inverter_temp", "status_code"]
AGG_SECONDS = 300

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS pack_samples (
    ts INTEGER NOT NULL, device TEXT NOT NULL,
    {", ".join(c + " REAL" for c in PACK_COLS)}, cells TEXT);
CREATE INDEX IF NOT EXISTS ix_pack_samples ON pack_samples(device, ts);

CREATE TABLE IF NOT EXISTS pack_5m (
    ts INTEGER NOT NULL, device TEXT NOT NULL,
    {", ".join(c + " REAL" for c in PACK_COLS)}, PRIMARY KEY(device, ts));

CREATE TABLE IF NOT EXISTS inverter_samples (
    ts INTEGER NOT NULL, device TEXT NOT NULL,
    {", ".join(c + " REAL" for c in INV_COLS)});
CREATE INDEX IF NOT EXISTS ix_inverter_samples ON inverter_samples(device, ts);

CREATE TABLE IF NOT EXISTS inverter_5m (
    ts INTEGER NOT NULL, device TEXT NOT NULL,
    {", ".join(c + " REAL" for c in INV_COLS)}, PRIMARY KEY(device, ts));

CREATE TABLE IF NOT EXISTS energy_daily (
    day TEXT NOT NULL, device TEXT NOT NULL,
    pv_kwh REAL DEFAULT 0, load_kwh REAL DEFAULT 0, charge_kwh REAL DEFAULT 0,
    discharge_kwh REAL DEFAULT 0, ac_in_kwh REAL DEFAULT 0,
    PRIMARY KEY(day, device));

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_start INTEGER NOT NULL, ts_end INTEGER,
    device TEXT, rule TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts_start);

CREATE TABLE IF NOT EXISTS settings_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, device TEXT NOT NULL,
    setting TEXT NOT NULL, old TEXT, new TEXT, ok INTEGER NOT NULL, error TEXT, source TEXT);

CREATE TABLE IF NOT EXISTS user_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, metric TEXT NOT NULL, op TEXT NOT NULL, value REAL NOT NULL,
    hold_s INTEGER NOT NULL DEFAULT 0, level TEXT NOT NULL DEFAULT 'warning', notify_clear INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created INTEGER NOT NULL, ua TEXT);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- sănătatea celulelor (cell_health.py): abaterile celulelor la final de încărcare / descărcare adâncă (mV) și
-- rezistențele la salturi de curent (mΩ), ca listă JSON pe fiecare moment util
CREATE TABLE IF NOT EXISTS cell_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, device TEXT NOT NULL, kind TEXT NOT NULL, vals TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cell_events_ts ON cell_events(ts);

-- securitate: încercările de logare și browserele care s-au logat (pentru „dispozitiv nou conectat”)
CREATE TABLE IF NOT EXISTS login_log (ts INTEGER NOT NULL, ip TEXT, ok INTEGER NOT NULL, ua TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS login_devices (
    id TEXT PRIMARY KEY, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL, ip TEXT, ua TEXT);
"""


def _b(v):
    return None if v is None else (1.0 if v else 0.0)


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")   # mai puține scrieri pe card
            self.conn.executescript(SCHEMA)
            # coloana „key” (identitatea alertei, ca să rămână deschisă peste o repornire) — adăugată ulterior
            if "key" not in {r[1] for r in self.conn.execute("PRAGMA table_info(alerts)")}:
                self.conn.execute("ALTER TABLE alerts ADD COLUMN key TEXT")

    # ---------- scriere ----------
    def insert_pack(self, r: PackReading, ts: int | None = None) -> None:
        vals = [r.voltage, r.current, r.power, r.soc, r.remaining_ah, r.cell_min, r.cell_max,
                r.cell_delta, r.temp_max, _b(r.charge_mos), _b(r.discharge_mos)]
        with self.lock:
            self.conn.execute(
                f"INSERT INTO pack_samples (ts, device, {', '.join(PACK_COLS)}, cells) "
                f"VALUES (?, ?, {', '.join('?' * len(PACK_COLS))}, ?)",
                [int(ts or r.ts), r.device_id, *vals, json.dumps(r.cells) if r.cells else None])

    def insert_inverter(self, r: InverterReading, ts: int | None = None) -> None:
        vals = [getattr(r, c) for c in INV_COLS]
        with self.lock:
            self.conn.execute(
                f"INSERT INTO inverter_samples (ts, device, {', '.join(INV_COLS)}) "
                f"VALUES (?, ?, {', '.join('?' * len(INV_COLS))})",
                [int(ts or r.ts), r.device_id, *vals])

    def add_energy(self, day: str, device: str, **kwh) -> None:
        cols = [k for k in ("pv_kwh", "load_kwh", "charge_kwh", "discharge_kwh", "ac_in_kwh") if k in kwh]
        if not cols:
            return
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO energy_daily (day, device) VALUES (?, ?)", (day, device))
            self.conn.execute(
                f"UPDATE energy_daily SET {', '.join(f'{c} = {c} + ?' for c in cols)} WHERE day=? AND device=?",
                [kwh[c] for c in cols] + [day, device])

    def open_alert(self, ts: int, device: str | None, rule: str, level: str, message: str,
                   key: str | None = None) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO alerts (ts_start, device, rule, level, message, key) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, device, rule, level, message, key))
            return cur.lastrowid

    # ---------- alerte proprii (setate de utilizator) ----------
    def user_alerts(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM user_alerts ORDER BY id").fetchall()]

    def add_user_alert(self, name, metric, op, value, hold_s, level, notify_clear) -> int:
        with self.lock:
            return self.conn.execute(
                "INSERT INTO user_alerts (name, metric, op, value, hold_s, level, notify_clear, enabled, created) "
                "VALUES (?,?,?,?,?,?,?,1,?)",
                (name, metric, op, value, hold_s, level, int(notify_clear), int(time.time()))).lastrowid

    def set_user_alert_enabled(self, rule_id: int, enabled: bool) -> None:
        with self.lock:
            self.conn.execute("UPDATE user_alerts SET enabled=? WHERE id=?", (int(enabled), rule_id))

    def delete_user_alert(self, rule_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM user_alerts WHERE id=?", (rule_id,))

    def last_sample_ts(self) -> float | None:
        """Momentul ultimei citiri salvate (de la orice dispozitiv)."""
        with self.lock:
            r = self.conn.execute("SELECT max(m) FROM (SELECT max(ts) m FROM pack_samples "
                                  "UNION ALL SELECT max(ts) FROM inverter_samples)").fetchone()
        return r[0] if r and r[0] else None

    def open_alerts(self) -> list[dict]:
        """Alertele încă deschise (rămase de la rularea anterioară)."""
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM alerts WHERE ts_end IS NULL").fetchall()]

    def close_alert(self, alert_id: int, ts: int) -> None:
        with self.lock:
            self.conn.execute("UPDATE alerts SET ts_end=? WHERE id=?", (ts, alert_id))

    def close_all_open_alerts(self, ts: int) -> None:
        with self.lock:
            self.conn.execute("UPDATE alerts SET ts_end=? WHERE ts_end IS NULL", (ts,))

    def log_setting(self, ts: int, device: str, setting: str, old: str | None, new: str | None,
                    ok: bool, error: str | None = None, source: str | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings_log (ts, device, setting, old, new, ok, error, source) VALUES (?,?,?,?,?,?,?,?)",
                (ts, device, setting, old, new, int(ok), error, source))

    def add_cell_event(self, ts: int, device: str, kind: str, vals: str) -> int:
        with self.lock:
            return self.conn.execute("INSERT INTO cell_events (ts, device, kind, vals) VALUES (?,?,?,?)",
                                     (ts, device, kind, vals)).lastrowid

    def update_cell_event(self, row_id: int, ts: int, vals: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE cell_events SET ts=?, vals=? WHERE id=?", (ts, vals, row_id))

    def cell_events(self, since: int) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT ts, device, kind, vals FROM cell_events WHERE ts >= ? ORDER BY ts", (since,))]

    def purge_cell_events(self, before: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM cell_events WHERE ts < ?", (before,))

    def get_meta(self, key: str) -> str | None:
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))

    def settings_log(self, device: str, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM settings_log WHERE device=? ORDER BY id DESC LIMIT ?",
                                     (device, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------- abonamente Web Push (telefoanele care primesc notificări din aplicație) ----------
    def add_push(self, endpoint: str, p256dh: str, auth: str, ua: str | None = None) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO push_subs (endpoint, p256dh, auth, created, ua) VALUES (?,?,?,?,?)",
                              (endpoint, p256dh, auth, int(time.time()), ua))

    def delete_push(self, endpoint: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))

    def push_subs(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM push_subs ORDER BY created").fetchall()]

    def log_login(self, ts: int, ip: str, ok: bool, ua: str = "", note: str = "") -> None:
        with self.lock:
            self.conn.execute("INSERT INTO login_log VALUES (?,?,?,?,?)", (ts, ip, int(ok), ua[:200], note))
            self.conn.execute("DELETE FROM login_log WHERE ts < ?", (ts - 90 * 86400,))

    def login_log(self, limit: int = 30) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM login_log ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))]

    def login_device(self, dev_id: str, ts: int, ip: str, ua: str) -> bool:
        """Înregistrează browserul; True dacă e nou."""
        with self.lock:
            new = self.conn.execute("SELECT 1 FROM login_devices WHERE id=?", (dev_id,)).fetchone() is None
            if new:
                self.conn.execute("INSERT INTO login_devices VALUES (?,?,?,?,?)", (dev_id, ts, ts, ip, ua[:200]))
            else:
                self.conn.execute("UPDATE login_devices SET last_ts=?, ip=?, ua=? WHERE id=?",
                                  (ts, ip, ua[:200], dev_id))
            return new

    def login_devices(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM login_devices ORDER BY last_ts DESC")]

    def last_seen(self, since: int) -> dict[str, int]:
        """Ultima citire salvată de fiecare dispozitiv după `since` (ts)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT device, MAX(ts) FROM pack_samples WHERE ts >= ? GROUP BY device "
                "UNION ALL SELECT device, MAX(ts) FROM inverter_samples WHERE ts >= ? GROUP BY device",
                (since, since)).fetchall()
        out: dict[str, int] = {}
        for d, ts in rows:
            out[d] = max(out.get(d, 0), ts)
        return out

    def has_samples(self) -> bool:
        """Baza de date are citiri (Pi folosit deja, nu o instalare nouă)."""
        with self.lock:
            return bool(self.conn.execute("SELECT 1 FROM pack_samples UNION ALL SELECT 1 FROM inverter_samples "
                                          "UNION ALL SELECT 1 FROM pack_5m UNION ALL SELECT 1 FROM inverter_5m "
                                          "LIMIT 1").fetchone())

    def seen_devices(self, since: int) -> set[str]:
        """Dispozitivele care au salvat cel puțin o citire după `since`."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT device FROM pack_samples WHERE ts >= ? "
                "UNION SELECT DISTINCT device FROM inverter_samples WHERE ts >= ?", (since, since)).fetchall()
        return {r[0] for r in rows}

    # ---------- întreținere ----------
    def rollup(self, retention_days: int, now: float | None = None) -> None:
        """Calculează mediile la 5 minute pentru intervalele încheiate și șterge datele vechi."""
        now = int(now or time.time())
        cutoff = now - now % AGG_SECONDS          # doar intervale complet încheiate
        with self.lock:
            for src, dst, cols in (("pack_samples", "pack_5m", PACK_COLS),
                                   ("inverter_samples", "inverter_5m", INV_COLS)):
                row = self.conn.execute(f"SELECT MAX(ts) FROM {dst}").fetchone()
                start = (row[0] + AGG_SECONDS) if row[0] is not None else 0
                self.conn.execute(
                    f"INSERT OR REPLACE INTO {dst} (ts, device, {', '.join(cols)}) "
                    f"SELECT (ts / {AGG_SECONDS}) * {AGG_SECONDS} AS b, device, "
                    f"{', '.join(f'AVG({c})' for c in cols)} FROM {src} "
                    f"WHERE ts >= ? AND ts < ? GROUP BY b, device", (start, cutoff))
                self.conn.execute(f"DELETE FROM {src} WHERE ts < ?", (now - retention_days * 86400,))
            self.conn.execute("DELETE FROM alerts WHERE ts_end IS NOT NULL AND ts_end < ?",
                              (now - 365 * 86400,))

    # ---------- citire ----------
    RAW_MAX_SPAN = 6 * 3600     # peste atât, graficele folosesc mediile la 5 minute (mult mai puțin de citit de pe card)

    def history(self, kind: str, device: str, start: int, end: int, points: int = 300,
                raw_retention_days: int = 14) -> list[dict]:
        cols = PACK_COLS if kind == "pack" else INV_COLS
        table_raw = "pack_samples" if kind == "pack" else "inverter_samples"
        table_agg = "pack_5m" if kind == "pack" else "inverter_5m"
        span = max(end - start, 1)
        bucket = max(10, span // max(points, 1))
        avg = ", ".join(f"AVG({c}) AS {c}" for c in cols)

        def q(table, b, a, z):
            return [dict(r) for r in self.conn.execute(
                f"SELECT (ts / {b}) * {b} AS t, {avg} FROM {table} WHERE device=? AND ts BETWEEN ? AND ? "
                f"GROUP BY t ORDER BY t", (device, a, z)).fetchall()]
        with self.lock:
            if span <= self.RAW_MAX_SPAN and start >= time.time() - raw_retention_days * 86400:
                return q(table_raw, bucket, start, end)                     # ore puține: citirile detaliate
            # mediile la 5 minute (calculate o dată pe oră) + ultimele minute din citirile detaliate
            bucket = max(bucket, AGG_SECONDS) // AGG_SECONDS * AGG_SECONDS
            row = self.conn.execute(f"SELECT MAX(ts) FROM {table_agg} WHERE device=?", (device,)).fetchone()
            agg_end = min(end, (row[0] + AGG_SECONDS) if row and row[0] is not None else start)
            agg_end = max(start, agg_end - agg_end % bucket)                # tăietura pe granița unui interval
            rows = q(table_agg, bucket, start, agg_end - 1) if agg_end > start else []
            if agg_end < end:
                rows += q(table_raw, bucket, agg_end, end)
            return rows

    def energy(self, device: str, days: int = 30) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM energy_daily WHERE device=? ORDER BY day DESC LIMIT ?", (device, days)).fetchall()
        return [dict(r) for r in rows][::-1]

    def energy_range(self, device: str, start: str, end: str) -> list[dict]:
        """Energia pe zile între două date (AAAA-LL-ZZ, inclusiv)."""
        with self.lock:
            rows = self.conn.execute("SELECT * FROM energy_daily WHERE device=? AND day BETWEEN ? AND ? ORDER BY day",
                                     (device, start, end)).fetchall()
        return [dict(r) for r in rows]

    def samples(self, kind: str, start: int, end: int) -> list[dict]:
        """Citirile brute (pack / inverter) dintr-un interval, în ordinea timpului (raportul nopții)."""
        table = "pack_samples" if kind == "pack" else "inverter_samples"
        cols = PACK_COLS if kind == "pack" else INV_COLS
        with self.lock:
            rows = self.conn.execute(f"SELECT ts, device, {', '.join(cols)} FROM {table} WHERE ts BETWEEN ? AND ? "
                                     "ORDER BY ts", (start, end)).fetchall()
        return [dict(r) for r in rows]

    def samples_max_since(self, since: int) -> dict[str, dict]:
        """SOC-ul și celula maximă atinse de fiecare modul din `since` (a fost o încărcare completă?)."""
        out: dict[str, dict] = {}
        with self.lock:
            for table in ("pack_5m", "pack_samples"):
                for r in self.conn.execute(f"SELECT device, max(soc) soc, max(cell_max) cell_max FROM {table} "
                                           "WHERE ts >= ? GROUP BY device", (since,)).fetchall():
                    o = out.setdefault(r["device"], {"soc": None, "cell_max": None})
                    for k in ("soc", "cell_max"):
                        if r[k] is not None:
                            o[k] = max(o[k] or r[k], r[k])
        return out

    def alerts_between(self, start: int, end: int) -> list[dict]:
        """Alertele care au început în interval sau erau deschise în el."""
        with self.lock:
            rows = self.conn.execute("SELECT * FROM alerts WHERE ts_start <= ? AND (ts_end IS NULL OR ts_end >= ?) "
                                     "ORDER BY ts_start", (end, start)).fetchall()
        return [dict(r) for r in rows]

    def settings_log_between(self, start: int, end: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM settings_log WHERE ts BETWEEN ? AND ? ORDER BY ts",
                                     (start, end)).fetchall()
        return [dict(r) for r in rows]

    def alerts(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM alerts ORDER BY (ts_end IS NULL) DESC, ts_start DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self.lock:
            self.conn.close()

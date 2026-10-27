"""Prezența Pi-ului pe internet: notificări la pornire și la revenirea internetului, plus semnal de viață.

- La pornirea serviciului, dacă Pi-ul abia a pornit (sau datele au lipsit mult), trimite „🔌 Amper a pornit”,
  cu cât timp a lipsit (după ultima citire salvată) — util după o pană de curent.
- La fiecare 30 s verifică internetul. Când revine după o întrerupere de cel puțin `min_outage_s`, trimite
  „🌐 Amper a revenit online”, cu intervalul. (În timpul întreruperii nu are cum să anunțe: alertele așteaptă în coadă.)
- Semnal de viață (opțional, `notify.heartbeat_url`, ex. healthchecks.io): un GET la 60 s cât timp internetul merge.
  Serviciul extern te anunță el când semnalele se opresc — singurul fel de a afla că Pi-ul a căzut de tot.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time

log = logging.getLogger(__name__)

CHECK_HOSTS = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)]


def has_internet(timeout: float = 4) -> bool:
    for host, port in CHECK_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def system_uptime() -> float | None:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return None


def hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{max(1, m)} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{h // 24} zile {h % 24} h"


class Presence:
    def __init__(self, engine, cfg: dict):
        self.engine = engine
        self.heartbeat_url = (cfg.get("notify") or {}).get("heartbeat_url") or ""
        self.min_outage = 120            # s: sub atât nu anunțăm (o sughițare a routerului)
        self.offline_since: float | None = None
        self.last_hb = 0.0

    def startup_message(self, now: float, last_sample: float | None, uptime: float | None) -> str | None:
        """Mesajul de la pornire, sau None dacă a fost doar o repornire scurtă a serviciului."""
        gap = now - last_sample if last_sample else None
        booted = uptime is not None and uptime < 300
        if not booted and (gap is None or gap < 300):
            return None
        what = "Pi-ul a pornit" if booted else "Amper a pornit din nou"
        if gap:
            return f"🔌 {what}. Nu au fost date între {hhmm(last_sample)} și {hhmm(now)} ({duration(gap)})" + \
                   (" — probabil o pană de curent." if booted else ".")
        return f"🔌 {what}."

    def on_check(self, online: bool, now: float) -> str | None:
        """Starea internetului la o verificare; întoarce mesajul de revenire, dacă e cazul."""
        if not online:
            if self.offline_since is None:
                self.offline_since = now
                log.warning("fără internet")
            return None
        if self.offline_since is None:
            return None
        since, self.offline_since = self.offline_since, None
        log.info("internetul a revenit (lipsă %.0f s)", now - since)
        if now - since < self.min_outage:
            return None
        return f"🌐 Amper a revenit online. Fără internet între {hhmm(since)} și {hhmm(now)} ({duration(now - since)})."

    async def run(self) -> None:
        eng = self.engine
        msg = self.startup_message(time.time(), await asyncio.to_thread(eng.db.last_sample_ts), system_uptime())
        if msg:
            eng.notifier.send(msg, title="Amper: pornit", priority="default")
        http = None
        try:
            import aiohttp
            http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            while True:
                now = time.time()
                online = await asyncio.to_thread(has_internet)
                msg = self.on_check(online, now)
                if msg:
                    eng.notifier.send(msg, title="Amper: online", priority="default")
                if online and self.heartbeat_url and now - self.last_hb >= 60:
                    try:
                        async with http.get(self.heartbeat_url) as r:
                            r.raise_for_status()
                        self.last_hb = now
                    except Exception as e:
                        log.warning("semnal de viață nelivrat: %s", e)
                await asyncio.sleep(30)
        finally:
            if http:
                await http.close()

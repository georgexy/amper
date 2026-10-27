"""Notificări pe telefon: direct în aplicația Amper (Web Push), ntfy (push, fără cont) și Telegram (opțional).

Mesajele intră într-o coadă; dacă internetul lipsește, se reîncearcă periodic (max. 100 în coadă).
Fiecare canal se reîncearcă separat, deci un canal căzut nu dublează mesajele pe celelalte.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque

log = logging.getLogger(__name__)

PRIO = {"low": 2, "default": 3, "high": 5}


def split_detail(message: str) -> tuple[str, str]:
    """„Daly 2: BMS-ul a OPRIT încărcarea (MOSFET încărcare OFF)” → ("Daly 2: BMS-ul a OPRIT încărcarea",
    "MOSFET încărcare OFF"): pe telefon apare doar textul principal, detaliile în aplicație (Alerte).
    Doar parantezele de pe primul nivel; cele dinăuntru rămân în detalii."""
    short, details, cur, depth = [], [], [], 0
    for ch in message:
        if ch == "(":
            depth += 1
            if depth == 1:
                cur = []
                continue
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                details.append("".join(cur).strip())
                continue
        (cur if depth else short).append(ch)
    if depth:                                        # paranteză neînchisă: mesajul rămâne cum e
        return message, ""
    text = re.sub(r"\s{2,}", " ", "".join(short)).replace(" :", ":").strip()
    return (text or message), "; ".join(d for d in details if d)


class Notifier:
    def __init__(self, cfg: dict):
        self.ntfy = cfg.get("ntfy", {})
        self.tg = cfg.get("telegram", {})
        self.queue: deque = deque(maxlen=100)
        self.sent: list[dict] = []          # folosit în teste / simulare
        self._wake = asyncio.Event() if _has_loop() else None
        self.webpush = None                 # amper.webpush.WebPush, setat de Engine

    def _ntfy_on(self) -> bool:
        return bool(self.ntfy.get("enabled") and self.ntfy.get("topic") and "REPLACE" not in self.ntfy["topic"])

    def _tg_on(self) -> bool:
        return bool(self.tg.get("enabled") and self.tg.get("bot_token"))

    @property
    def enabled(self) -> bool:
        return self._ntfy_on() or self._tg_on() or bool(self.webpush and self.webpush.enabled)

    def send(self, message: str, title: str = "amper", priority: str = "default") -> None:
        log.info("NOTIFICARE [%s] %s", priority, message)
        item = {"message": message, "title": title, "priority": priority, "done": set()}
        self.sent.append(item)
        del self.sent[:-200]
        if self.enabled:
            self.queue.append(item)
            if self._wake:
                self._wake.set()

    async def run(self) -> None:
        import aiohttp
        self._wake = self._wake or asyncio.Event()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                while self.queue:
                    item = self.queue[0]
                    try:
                        await self._deliver(http, item)
                        self.queue.popleft()
                    except Exception as e:
                        log.warning("notificare nelivrată (reîncerc în 60 s): %s", e)
                        break

    async def _deliver(self, http, item: dict) -> None:
        done = item.setdefault("done", set())
        errors = []
        for name, on, fn in (("webpush", bool(self.webpush and self.webpush.enabled), self._webpush),
                             ("ntfy", self._ntfy_on(), self._ntfy), ("telegram", self._tg_on(), self._telegram)):
            if not on or name in done:
                continue
            try:
                await fn(http, item)
                done.add(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    async def _webpush(self, http, item: dict) -> None:
        await self.webpush.send(http, item)

    async def _ntfy(self, http, item: dict) -> None:
        async with http.post(self.ntfy.get("server", "https://ntfy.sh").rstrip("/"),
                             json={"topic": self.ntfy["topic"], "title": item["title"],
                                   "message": item["message"], "priority": PRIO[item["priority"]],
                                   "tags": ["battery"]}) as r:
            r.raise_for_status()

    async def _telegram(self, http, item: dict) -> None:
        url = f"https://api.telegram.org/bot{self.tg['bot_token']}/sendMessage"
        async with http.post(url, json={"chat_id": self.tg["chat_id"],
                                        "text": f"{item['title']}\n{item['message']}"}) as r:
            r.raise_for_status()


def _has_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False

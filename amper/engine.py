"""Motorul: citește dispozitivele, ține starea curentă, salvează în baza de date, rulează alertele."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .alerts import AlertManager
from .db import Database
from .drivers import DriverError, make_driver
from .models import InverterReading, PackReading
from .notify import Notifier

log = logging.getLogger(__name__)


class DeviceState:
    def __init__(self, cfg: dict, driver):
        self.cfg = cfg
        self.driver = driver
        self.reading = None
        self.last_ok: float | None = None
        self.error: str | None = None
        self.errors = 0
        # a răspuns în ultimele `connected_days` zile (acum sau în istoric); cele nelegate nu se afișează
        self.connected = False
        self.seen_at: float | None = None    # ultimul răspuns (ora sistemului), pentru deconectarea automată
        self.gen = 0          # crește la deconectare: o citire pornită înainte nu mai e luată în seamă

    def to_dict(self, now: float, stale_after: float) -> dict:
        r = self.reading.to_dict() if self.reading else None
        if r:
            r.get("raw", {}).pop("regs", None)
        return dict(id=self.cfg["id"], name=self.cfg.get("name", self.cfg["id"]), type=self.cfg["type"],
                    kind=self.driver.kind, reading=r, last_ok=self.last_ok, error=self.error,
                    connected=self.connected,
                    stale=self.last_ok is None or now - self.last_ok > stale_after,
                    capacity_ah=self.cfg.get("capacity_ah") or (r or {}).get("capacity_ah"))


class Engine:
    def __init__(self, cfg: dict, db: Database | None = None, notifier: Notifier | None = None):
        self.cfg = cfg
        self.db = db or Database(cfg["database"])
        self.notifier = notifier or Notifier(cfg["notify"])
        self.alerts = AlertManager(cfg["alerts"], self.db, self.notifier)
        self.devices: dict[str, DeviceState] = {}
        if cfg["database"] != ":memory:":
            from .drivers import daly_auto
            state_dir = os.path.dirname(os.path.abspath(cfg["database"]))
            daly_auto.set_state_dir(state_dir)
            if self.notifier.webpush is None:
                try:
                    from .webpush import WebPush, public_url
                    # Apple respinge („BadJwtToken”) o adresă de contact inventată: folosim adresa https reală
                    self.notifier.webpush = WebPush(self.db, state_dir, subject=public_url(cfg))
                except ImportError:
                    log.warning("Web Push indisponibil: lipsește python3-cryptography")
        for d in cfg["devices"]:
            self.devices[d["id"]] = DeviceState(d, make_driver(d, cfg["devices"]))
        self.started = time.time()
        seen = self.db.last_seen(int(self.started - self.connected_days() * 86400))
        for k, st in self.devices.items():
            st.connected, st.seen_at = k in seen, seen.get(k)
        # alertele deschise rămân deschise peste o repornire (nu se mai trimit o dată);
        # cele vechi, fără cheie, se închid ca înainte
        for r in self.alerts.restore(self.db.open_alerts(), self.started):
            self.db.close_alert(r["id"], int(self.started))
        self.subscribers: set[asyncio.Queue] = set()
        self._tasks: dict[str, asyncio.Task] = {}
        self._energy_t: dict[str, float] = {}
        self._energy_acc: dict[tuple, dict] = {}
        self._clock_offset = time.time() - time.monotonic()

    # ---------------- ceas ----------------
    def check_clock(self) -> float:
        """Pi-ul nu are ceas cu baterie: la pornire ora e cea salvată la ultima oprire, iar când vine ora
        de pe internet ceasul sare. Mutăm momentele memorate cu același salt, ca să nu apară alerte false."""
        offset = time.time() - time.monotonic()
        delta = offset - self._clock_offset
        self._clock_offset = offset
        if abs(delta) < 5:
            return 0.0
        log.info("ceasul sistemului s-a corectat cu %+.0f s", delta)
        self.started += delta
        for st in self.devices.values():
            if st.last_ok is not None:
                st.last_ok += delta
                st.seen_at = st.last_ok          # răspuns în rularea asta: aceeași oră greșită, același salt
            if st.reading is not None:
                st.reading.ts += delta
        for k in self._energy_t:
            self._energy_t[k] += delta
        self.alerts.shift_clock(delta)
        return delta

    # ---------------- stare ----------------
    def snapshot(self, now: float | None = None) -> dict:
        now = now or time.time()
        stale_after = max(30, self.cfg["poll_interval"] * 6)
        devs = {k: v.to_dict(now, stale_after) for k, v in self.devices.items()}
        return dict(ts=now, started=self.started, devices=devs, bank=self._bank(devs),
                    alerts=self.alerts.active_list(), notifications=self.notifier.enabled,
                    app_version=self.app_version() if self.app_version else None)

    app_version = None      # funcție setată de serverul web: versiunea fișierelor aplicației (pentru reîncărcare)

    @staticmethod
    def _bank(devs: dict) -> dict:
        packs = [d for d in devs.values() if d["kind"] == "pack" and d["reading"] and not d["stale"]]
        if not packs:
            return {}
        cap = sum(float(d["capacity_ah"] or 0) for d in packs)
        rem = sum(d["reading"].get("remaining_ah") or 0 for d in packs)
        cur = sum(d["reading"].get("current") or 0 for d in packs)
        volt = sum(d["reading"].get("voltage") or 0 for d in packs) / len(packs)
        soc = (sum((d["reading"].get("soc") or 0) * float(d["capacity_ah"] or 0) for d in packs) / cap) if cap else None
        if soc is not None:
            rem = cap * soc / 100
        hours = None
        if cur < -0.5:
            hours = rem / -cur
        elif cur > 0.5 and cap:
            hours = (cap - rem) / cur
        return dict(soc=round(soc, 1) if soc is not None else None, capacity_ah=round(cap, 1),
                    remaining_ah=round(rem, 1), current=round(cur, 1), voltage=round(volt, 2),
                    power=round(cur * volt), packs=len(packs), hours=round(hours, 1) if hours else None,
                    kwh=round(rem * volt / 1000, 2))

    # ---------------- bucle ----------------
    async def poll_device(self, st: DeviceState) -> None:
        interval = self.cfg["poll_interval"]
        # Fiecare dispozitiv are firul lui de citire, iar o citire nouă pornește doar după ce s-a terminat cea veche.
        # Altfel, un dispozitiv care nu răspunde (ex. Bluetooth) ocupa pe rând toate firele comune și bloca citirea
        # tuturor celorlalte — invertorul inclus (văzut pe hardware, 2026-10-26).
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"citire-{st.cfg['id']}")
        pending = None
        while True:
            t0 = time.monotonic()
            try:
                if pending is None:
                    gen = st.gen
                    pending = loop.run_in_executor(executor, st.driver.read)
                    # eroarea unei citiri abandonate (ex. la oprire) nu mai e raportată ca „neprelucrată”
                    pending.add_done_callback(lambda f: f.cancelled() or f.exception())
                try:
                    r = await asyncio.wait_for(asyncio.shield(pending), timeout=interval * 3 + 5)
                finally:
                    if pending.done():
                        pending = None
                if st.gen != gen:                # deconectat cât timp citirea era în curs
                    continue
                st.reading, st.last_ok, st.error, st.errors = r, time.time(), None, 0
                st.seen_at = st.last_ok
                if not st.connected:             # prima citire de la un dispozitiv nou (ex. un Daly abia legat)
                    how = getattr(st.driver, "link_desc", "")
                    log.info("%s: conectat%s", st.cfg["id"], f" ({how})" if how else "")
                    self.notifier.send(f"🔌 {st.cfg.get('name', st.cfg['id'])}: conectat" + (f" ({how})" if how else ""),
                                       title="Amper: modul nou", priority="default")
                st.connected = True
                if isinstance(r, InverterReading):
                    self._integrate_energy(r)
                self._push(self.snapshot())      # pe ecran imediat, nu la următorul tick
            except (DriverError, asyncio.TimeoutError, OSError) as e:
                st.errors += 1
                st.error = str(e) or e.__class__.__name__
                if st.errors in (1, 10) or st.errors % 100 == 0:
                    log.warning("%s: %s (eroarea #%d)", st.cfg["id"], st.error, st.errors)
            except Exception as e:     # bug în driver: nu oprim restul sistemului
                st.errors += 1
                st.error = f"eroare internă: {e}"
                log.exception("%s: eroare neașteptată", st.cfg["id"])
            await asyncio.sleep(max(0.2, interval - (time.monotonic() - t0)))

    async def tick_loop(self) -> None:
        """Evaluează alertele și trimite starea către aplicații (WebSocket)."""
        while True:
            await asyncio.sleep(self.cfg["poll_interval"])
            self.check_clock()
            self.expire_devices()
            snap = self.snapshot()
            try:
                self.alerts.process(snap)
            except Exception:
                log.exception("eroare la evaluarea alertelor")
            snap["alerts"] = self.alerts.active_list()
            self._push(snap)

    def connected_days(self) -> float:
        return float(self.cfg.get("connected_days") or 5)

    def expire_devices(self, now: float | None = None) -> list[str]:
        """Dispozitivele care n-au răspuns `connected_days` zile se deconectează singure: nu se mai afișează,
        nu mai dau `comm_lost`, iar alertele lor se închid fără „Rezolvat”. Dacă răspund din nou, revin singure
        (rămân în configurare; citirea continuă). Cerut de utilizator 2026-10-28."""
        now = now or time.time()
        limit = self.connected_days() * 86400
        out = []
        for k, st in self.devices.items():
            if not st.connected:
                continue
            seen = st.seen_at if st.seen_at is not None else self.started
            if now - seen <= limit:
                continue
            st.connected, st.reading = False, None
            self.alerts.drop_device(k, now)
            name = st.cfg.get("name", k)
            log.info("%s: deconectat automat (fără răspuns de %d zile)", k, self.connected_days())
            self.notifier.send(f"🔌 {name}: deconectat automat — n-a mai răspuns de {self.connected_days():g} zile. "
                               "Dacă răspunde din nou, revine singur.", title="Amper", priority="default")
            out.append(k)
        return out

    def _push(self, snap: dict) -> None:
        for q in list(self.subscribers):
            if q.qsize() < 3:
                q.put_nowait(snap)

    async def store_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg["store_interval"])
            try:
                self.store_once()
            except Exception:
                log.exception("eroare la scrierea în baza de date")

    def store_once(self, now: float | None = None) -> None:
        now = now or time.time()
        stale_after = max(30, self.cfg["poll_interval"] * 6)
        for st in self.devices.values():
            if st.reading is None or st.last_ok is None or now - st.last_ok > stale_after:
                continue
            if isinstance(st.reading, PackReading):
                self.db.insert_pack(st.reading, ts=int(now))
            else:
                self.db.insert_inverter(st.reading, ts=int(now))
        self._flush_energy()

    async def maintenance_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.db.rollup, self.cfg["raw_retention_days"])
            except Exception:
                log.exception("eroare la agregarea datelor")
            await asyncio.sleep(3600)

    # ---------------- energie ----------------
    def _integrate_energy(self, r: InverterReading, now: float | None = None) -> None:
        now = now or r.ts
        last = self._energy_t.get(r.device_id)
        self._energy_t[r.device_id] = now
        if last is None:
            return
        dt = now - last
        if dt <= 0 or dt > 120:
            return
        h = dt / 3600 / 1000
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        acc = self._energy_acc.setdefault((day, r.device_id), dict(pv_kwh=0.0, load_kwh=0.0, charge_kwh=0.0,
                                                                  discharge_kwh=0.0, ac_in_kwh=0.0))
        acc["pv_kwh"] += (r.pv_power or 0) * h
        acc["load_kwh"] += (r.load_power or 0) * h
        acc["ac_in_kwh"] += (r.ac_in_power or 0) * h
        bp = r.battery_power or 0
        acc["charge_kwh" if bp > 0 else "discharge_kwh"] += abs(bp) * h

    def _flush_energy(self) -> None:
        for (day, dev), acc in list(self._energy_acc.items()):
            self.db.add_energy(day, dev, **acc)
        self._energy_acc.clear()

    async def run(self) -> None:
        self._tasks = {k: asyncio.create_task(self.poll_device(st), name=f"poll:{k}") for k, st in self.devices.items()}
        tasks = list(self._tasks.values())
        tasks += [asyncio.create_task(c) for c in (self.tick_loop(), self.store_loop(),
                                                   self.maintenance_loop(), self.notifier.run())]
        if self.cfg["database"] != ":memory:":
            from .presence import Presence          # pornire / revenire online / semnal de viață
            tasks.append(asyncio.create_task(Presence(self, self.cfg).run()))
        await asyncio.gather(*tasks)

    # ---------------- deconectare din aplicație ----------------
    def disconnect_device(self, dev_id: str, reason: str = "deconectat din aplicație") -> dict:
        """Amper nu mai citește BMS-ul și nu mai dă alerte pentru el, fără repornirea serviciului.
        Daly: poziția (ex. daly2) rămâne, liberă — legătura e uitată și pusă pe lista de excluse, iar căutarea automată
        poate găsi alt modul. Celelalte (ex. Seplos): scoase din config. Istoricul rămâne în baza de date.
        Întoarce {name, link, action: freed|removed, devices: lista nouă pentru config sau None}."""
        from .drivers import daly_auto
        st = self.devices[dev_id]
        name = st.cfg.get("name", dev_id)
        link = getattr(st.driver, "link_desc", "") or ""
        raw = (st.reading.raw if st.reading is not None else {}) or {}
        new_devices = None
        st.gen += 1
        if st.cfg["type"] == "daly":
            if isinstance(st.driver, daly_auto.DalyAutoDriver):
                b = st.driver.unbind()
            else:                                          # legătură fixă în config: devine poziție liberă („auto”)
                b = ({"type": "ble", "ble": st.cfg["ble"], "name": st.cfg.get("ble_name") or raw.get("ble_name")}
                     if st.cfg.get("ble") else {"type": "uart", "port": st.cfg.get("port")})
                st.driver.close()
                keep = ("id", "name", "type", "capacity_ah", "invert_current")
                cfg = {**{k: st.cfg[k] for k in keep if k in st.cfg}, "port": "auto"}
                self.cfg["devices"] = [cfg if d["id"] == dev_id else d for d in self.cfg["devices"]]
                st.cfg, st.driver = cfg, daly_auto.DalyAutoDriver(cfg, self.cfg["devices"])
                new_devices = self.cfg["devices"]
            if b and (b.get("ble") or b.get("port")):
                daly_auto.exclude(b, f"{name}: {link or b.get('name') or b.get('ble') or b.get('port')}", reason)
            st.reading, st.last_ok, st.error, st.errors, st.connected = None, None, None, 0, False
            action = "freed"
        else:
            task = self._tasks.pop(dev_id, None)
            if task:
                task.cancel()
            st.driver.close()
            del self.devices[dev_id]
            self.cfg["devices"] = [d for d in self.cfg["devices"] if d["id"] != dev_id]
            new_devices = self.cfg["devices"]
            action = "removed"
        self.alerts.drop_device(dev_id)
        self.db.log_setting(int(time.time()), dev_id, "conexiune", link or None, reason, True, None, "aplicație")
        log.warning("%s (%s): %s", dev_id, link, reason)
        self.notifier.send(f"🔌 {name} a fost deconectat{f' ({link})' if link else ''}. Amper nu-l mai citește.",
                           title="Amper", priority="default")
        self._push(self.snapshot())
        return {"name": name, "link": link, "action": action, "devices": new_devices}

    def close(self) -> None:
        self._flush_energy()
        for st in self.devices.values():
            st.driver.close()

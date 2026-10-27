"""Bluetooth Low Energy (prin BlueZ, biblioteca `bleak`) pentru modulele Bluetooth ale BMS-urilor (Daly, JK).

Modulul Daly expune un „UART prin BLE”: serviciul 0xFFF0, caracteristica 0xFFF1 (notificări = răspunsurile BMS-ului)
și 0xFFF2 (scriere = cererile). Prin el trec aceleași cadre de 13 octeți ca pe cablu.
UUID-urile sunt cele folosite de modulele Daly cunoscute — DE VERIFICAT pe modulele reale; dacă lipsesc,
se caută în serviciul 0xFFF0 orice caracteristică de notificare + una de scriere.
JK: serviciul 0xFFE0, caracteristica 0xFFE1 (scriere și notificări; modulele noi au două caracteristici 0xFFE1, una
pentru fiecare) — `BleLink(target, service=JK_SERVICE, notify=JK_CHAR, write=JK_CHAR)`.

bleak e asincron, iar driverele rulează în thread-uri: toate operațiile BLE rulează într-o singură buclă asyncio
proprie (un thread), iar driverele așteaptă rezultatul.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading

from .base import DriverError

log = logging.getLogger(__name__)

SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
JK_SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
JK_CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DALY_NAME = re.compile(r"^(DL-|DALY)", re.I)


def _bleak():
    try:
        import bleak
        return bleak
    except ImportError:
        raise DriverError("Bluetooth indisponibil: lipsește pachetul python3-bleak "
                          "(se instalează cu deploy/install.sh)") from None


class _Loop:
    """Bucla asyncio comună pentru tot ce e Bluetooth."""
    _inst = None
    _lock = threading.Lock()

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="ble", daemon=True)
        self.thread.start()
        self.bt_lock = asyncio.Lock()          # o singură operație Bluetooth (scanare/conectare) odată

    @classmethod
    def get(cls) -> "_Loop":
        with cls._lock:
            if cls._inst is None:
                cls._inst = _Loop()
            return cls._inst

    def run(self, coro, timeout: float):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
            raise DriverError("Bluetooth: timp depășit") from None


def run(coro_fn, timeout: float):
    return _Loop.get().run(coro_fn(), timeout)


def scan(seconds: float = 8.0) -> list[dict]:
    """Dispozitivele BLE din jur: [{address, name, rssi}], cele mai puternice primele."""
    bleak = _bleak()
    lp = _Loop.get()

    async def go():
        async with lp.bt_lock:
            found = await bleak.BleakScanner.discover(timeout=seconds, return_adv=True)
        out = []
        for addr, (dev, adv) in found.items():
            out.append(dict(address=addr, name=adv.local_name or dev.name or "", rssi=adv.rssi))
        return sorted(out, key=lambda d: -(d["rssi"] or -999))

    try:
        return lp.run(go(), seconds + 10)
    except DriverError:
        raise
    except Exception as e:                      # adaptor oprit, BlueZ lipsă, fără drepturi
        raise DriverError(f"Bluetooth: scanarea a eșuat ({e.__class__.__name__}: {e})") from e


def is_daly_name(name: str) -> bool:
    return bool(DALY_NAME.match(name or ""))


class BleLink:
    """O legătură BLE persistentă către un modul; se reconectează singură la nevoie."""

    def __init__(self, target: str, service: str = SERVICE, notify: str = NOTIFY, write: str = WRITE):
        self.target = target                    # adresa MAC sau numele modulului
        self.uuids = (service, notify, write)
        self.address = target.upper() if MAC_RE.match(target) else None    # aflate la conectare
        self.name = None if MAC_RE.match(target) else target
        self.client = None
        self.write_uuid = WRITE
        self.buf = bytearray()
        self.got = None                         # asyncio.Event, creat în bucla BLE
        self.lock = threading.Lock()
        self.rssi = None

    async def _resolve(self, bleak):
        if MAC_RE.match(self.target):
            dev = await bleak.BleakScanner.find_device_by_address(self.target, timeout=8)
            if dev is None:
                # rămas „conectat” în BlueZ după o întrerupere (atunci nu se mai anunță): îl deconectăm și căutăm iar
                try:
                    p = await asyncio.create_subprocess_exec("bluetoothctl", "disconnect", self.target,
                                                             stdout=asyncio.subprocess.DEVNULL,
                                                             stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(), 10)
                except (OSError, asyncio.TimeoutError):
                    pass
                dev = await bleak.BleakScanner.find_device_by_address(self.target, timeout=8)
        else:
            dev = await bleak.BleakScanner.find_device_by_name(self.target, timeout=10)
        if dev is None:
            raise DriverError(f"Bluetooth: modulul {self.target} nu e în raza de acțiune (sau e conectat la telefon)")
        self.address = (getattr(dev, "address", None) or self.address or "").upper() or None
        self.name = getattr(dev, "name", None) or self.name
        return dev

    async def _connect(self):
        bleak = _bleak()
        lp = _Loop.get()
        async with lp.bt_lock:
            dev = await self._resolve(bleak)
            client = bleak.BleakClient(dev, disconnected_callback=lambda _c: setattr(self, "client", None))
            await client.connect(timeout=15)
        try:
            # caracteristicile alese după proprietăți (la JK, scrierea și notificările au același UUID), întâi cele
            # cu UUID-ul așteptat, apoi orice notify/write din serviciu
            service, n_uuid, w_uuid = self.uuids
            svc = client.services.get_service(service)
            chars = svc.characteristics if svc else [c for s in client.services for c in s.characteristics]

            def pick(uuid, props):
                ok = [c for c in chars if set(props) & set(c.properties)]
                return next((c for c in ok if c.uuid.lower() == uuid), None) or next(iter(ok), None)
            notify = pick(n_uuid, ("notify", "indicate"))
            write = pick(w_uuid, ("write", "write-without-response"))
            if notify is None or write is None:
                raise DriverError(f"Bluetooth: modulul nu are canalul serial al BMS-ului ({service[4:8].upper()})")
            self.got = asyncio.Event()

            def on_data(_char, data: bytearray):
                self.buf.extend(data)
                self.got.set()

            await asyncio.wait_for(client.start_notify(notify, on_data), 15)
            self.write_uuid = write
            self.client = client
            log.info("Bluetooth: conectat la %s", self.target)
        except Exception:
            await client.disconnect()
            raise

    async def _exchange(self, request: bytes, expected: int, timeout: float, done=None) -> bytes:
        if self.client is None or not self.client.is_connected:
            await self._connect()
        self.buf.clear()
        self.got.clear()
        await self.client.write_gatt_char(self.write_uuid, request, response=False)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        ready = done or (lambda b: len(b) >= expected)
        while not ready(self.buf):
            left = deadline - loop.time()
            if left <= 0:
                break
            self.got.clear()
            try:
                await asyncio.wait_for(self.got.wait(), left)
            except asyncio.TimeoutError:
                break
        return bytes(self.buf)

    def exchange(self, request: bytes, expected: int, timeout: float = 1.5, done=None) -> bytes:
        with self.lock:
            try:
                return run(lambda: self._exchange(request, expected, timeout, done), timeout + 40)
            except DriverError:
                self.close()
                raise
            except Exception as e:
                self.close()
                raise DriverError(f"Bluetooth {self.target}: {e.__class__.__name__}: {e}") from e

    def close(self) -> None:
        c, self.client = self.client, None
        if c is not None:
            try:
                run(lambda: c.disconnect(), 10)
            except Exception:
                pass

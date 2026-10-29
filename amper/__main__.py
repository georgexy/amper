"""Linia de comandă.

  amper run      [-c /etc/amper/config.yaml]     pornește monitorizarea + aplicația web
  amper simulate [--hours 48] [--port 8080]        totul cu date simulate (fără hardware)
  amper scan                                        caută pe ce port e fiecare dispozitiv
  amper autoconfig [-c config]                     completează singur porturile în config (după scan)
  amper read <id> [-c config]                       citește o dată un dispozitiv și afișează tot
  sudo amper update --check | --install | --rollback   actualizare fără aplicație (cale de rezervă)
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sys
import time

from . import __version__
from .config import DEFAULTS, _merge, load_config, validate

log = logging.getLogger("amper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def _serve(engine) -> None:
    from aiohttp import web
    from .web import make_app
    app = make_app(engine)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    w = engine.cfg["web"]
    await web.TCPSite(runner, w["host"], int(w["port"])).start()
    log.info("aplicația web: http://%s:%s", w["host"], w["port"])
    try:
        await engine.run()
    finally:
        engine.close()
        await runner.cleanup()


def cmd_run(args) -> None:
    from .engine import Engine
    cfg = load_config(args.config)
    log.info("amper %s — %d dispozitive", __version__, len(cfg["devices"]))
    import signal

    def stop(*_):                      # systemctl stop: oprire curată (închide și legăturile Bluetooth)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        asyncio.run(_serve(Engine(cfg)))
    except KeyboardInterrupt:
        log.info("oprit")


def sim_config(db_path: str, port: int, hours: float, setup: bool = False, lite: bool = False) -> dict:
    fault_after = max(60, hours * 3600 - 1800)    # Daly 2 "cade" cu 30 min înainte de pornire
    devices = [
        {"id": "daly1", "name": "Daly 1", "type": "sim_daly", "capacity_ah": 100, "start_soc": 55},
        {"id": "daly2", "name": "Daly 2", "type": "sim_daly", "capacity_ah": 100, "start_soc": 58,
         "fault_after_s": fault_after},
        {"id": "daly3", "name": "Daly 3", "type": "sim_daly", "capacity_ah": 100, "start_soc": 52},
        {"id": "seplos", "name": "Seplos", "type": "sim_seplos", "capacity_ah": 280, "start_soc": 60},
        {"id": "inverter", "name": "Growatt SPF 5000 ES", "type": "sim_growatt", "pv_peak_w": 4400},
    ]
    # ghidul de configurare apare doar la cerere (--ghid); setările din el merg lângă baza de date simulată
    cfg = _merge(DEFAULTS, {"database": db_path, "devices": devices, "web": {"port": port, "host": "0.0.0.0"},
                            "setup_done": not setup, "settings_file": db_path + ".settings.yaml",
                            "edition": "lite" if lite else "pro"})
    validate(cfg)
    return cfg


def backfill(engine, hours: float) -> None:
    """Generează istoric simulat (pentru grafice) rulând simularea în timp accelerat."""
    from .drivers.simulator import SimWorld
    world = SimWorld.get()
    now = time.time()
    start = now - hours * 3600
    world.t0 = world.t = start
    step = engine.cfg["store_interval"]
    t = start
    engine.started = start
    while t < now:
        t += step
        world.step(t)
        for st in engine.devices.values():
            r = world.pack_reading(st.cfg["id"]) if st.driver.kind == "pack" else world.inverter_reading(st.cfg["id"])
            st.reading, st.last_ok = r, t
            if st.driver.kind == "inverter":
                engine._integrate_energy(r, now=t)
        engine.alerts.process(engine.snapshot(now=t), now=t)
        engine.store_once(now=t)
    engine.db.rollup(engine.cfg["raw_retention_days"], now=now)
    engine.started = time.time()
    engine.notifier.sent.clear()


def cmd_simulate(args) -> None:
    from .engine import Engine
    db_path = args.db
    if args.keep and os.path.exists(db_path):
        args.hours = 0                     # istoricul există deja
    elif os.path.exists(db_path):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)
    cfg = sim_config(db_path, args.port, args.hours, setup=args.ghid, lite=args.lite)
    if args.ghid and os.path.exists(cfg["settings_file"]):
        os.remove(cfg["settings_file"])
    engine = Engine(cfg)
    if args.hours > 0:
        log.info("generez %.0f ore de istoric simulat...", args.hours)
        backfill(engine, args.hours)
    asyncio.run(_serve(engine))


# ------------------------- scanare porturi -------------------------
def list_ports() -> list[str]:
    ports = sorted(glob.glob("/dev/serial/by-id/*"))
    if not ports:
        ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return ports


def probe(port: str, only: set[str] | None = None, addresses=None) -> list[str]:
    """Ce dispozitive răspund pe un port. Întoarce o listă de 'tip {json cu parametri}'.
    Pe RS485 pot fi mai multe pachete Seplos / PACE (adrese diferite) — le întoarce pe toate.
    `only` = doar aceste tipuri (alegerea din listă: mai repede și fără să încerce alte protocoale);
    `addresses` = doar aceste adrese la Seplos / PACE (cea setată din DIP)."""
    want = (lambda t: only is None or t in only)
    addrs = list(addresses) if addresses is not None else list(range(16))
    from .drivers.daly import DalyDriver
    from .drivers.growatt_spf import GrowattSPFDriver
    from .drivers.seplos import SeplosDriver

    def ok(drv, fn) -> bool:
        try:
            fn(drv)
            return True
        except Exception:
            return False
        finally:
            drv.close()

    for addr in ((0x40, 0x80) if want("daly") else ()):
        if ok(DalyDriver({"id": "probe", "type": "daly", "port": port, "address": addr}), lambda d: d._query(0x90)):
            return [f"daly {json.dumps({'address': addr})}"]
    from .drivers.jk import JkUartDriver           # JK cu modulul RS485/UART (115200)
    if want("jk") and ok(JkUartDriver({"id": "probe", "type": "jk", "port": port}), lambda d: d.read()):
        return ['jk {"baudrate": 115200}']
    if want("growatt_spf") and ok(GrowattSPFDriver({"id": "probe", "type": "growatt_spf", "port": port}),
                                  lambda d: d._read_block(0, 3)):
        return ['growatt_spf {"baudrate": 9600}']
    from .drivers import pace                      # PACE (PowerCube / BMS Daren...), versiunea 0x25, adresele 0–15

    def pace25(a):
        d = pace.PaceDriver({"id": "probe", "type": "pace", "port": port, "address": a, "protocol": "25"})
        d.answer_wait = pace.PROBE_WAIT
        return d
    found = [f"pace {json.dumps({'baudrate': 9600, 'address': a, 'protocol': 25})}" for a in addrs
             if want("pace") and ok(pace25(a), lambda d: d._analog())]
    if found:
        return found
    # versiunea 0x20: Seplos V2 sau Pylon (aceeași cerere; se deosebesc după răspuns, pace.is_seplos)
    for baud in ((19200, 9600) if want("seplos") or want("pace") else ()):
        found = []
        for a in addrs:
            info = []
            if ok(SeplosDriver({"id": "probe", "type": "seplos", "port": port, "baudrate": baud, "address": a}),
                  lambda d: info.append(d._query(0x42))):
                found.append(f"seplos {json.dumps({'baudrate': baud, 'address': a})}" if pace.is_seplos(info[0])
                             else f"pace {json.dumps({'baudrate': baud, 'address': a, 'protocol': 20})}")
        found = [f for f in found if want(f.split(" ", 1)[0])]
        if found:
            return found
    return []


def cmd_scan(args) -> None:
    ports = list_ports()
    if not ports:
        print("Nu am găsit niciun adaptor USB-serial. Verificați cablurile (lsusb).")
    for p in ports:
        print(f"\n{p}")
        res = probe(p)
        if not res:
            print("   → nimic recunoscut (dispozitiv adormit, cablu greșit sau protocol necunoscut)")
        for r in res:
            print("   → " + r)
    scan_bluetooth()


def scan_bluetooth() -> None:
    """Modulele Bluetooth Daly din jur (nume DL-... / DALY...) și dacă răspund la protocolul Daly."""
    from .drivers import ble, daly_auto
    from .drivers.base import DriverError
    print("\nBluetooth (8 s)...")
    try:
        found = ble.scan(8)
    except DriverError as e:
        print(f"   → {e}")
        return
    dalys = [d for d in found if ble.is_daly_name(d["name"])]
    print(f"   {len(found)} dispozitive Bluetooth în jur, {len(dalys)} cu nume Daly")
    for d in dalys:
        b = {"type": "ble", "ble": d["address"]}
        ok = daly_auto._probe({"id": "probe", "type": "daly"}, b)
        print(f"   {d['address']}  {d['name']:<20} semnal {d['rssi']} dBm  → " +
              (f'daly {{"ble": "{d["address"]}", "address": {b["address"]}}}' if ok else
               "nu răspunde (conectat la telefon? protocol diferit?)"))


SEPLOS_BLOCK = """  - id: {id}
    name: "{name}"
    type: seplos
    port: {port}
    baudrate: {baudrate}
    address: {address}
    capacity_ah: {capacity}
"""


def cmd_autoconfig(args) -> None:
    """Completează automat porturile marcate REPLACE din config, pe baza scanării.
    Păstrează comentariile. Pachetele Seplos multiple (adrese diferite pe același RS485)
    înlocuiesc blocul Seplos neconfigurat cu câte un bloc pentru fiecare pachet."""
    import re
    path = args.config
    text = open(path, encoding="utf-8").read()
    found: dict[str, list[tuple[str, dict]]] = {}
    for p in list_ports():
        res = probe(p)
        if not res:
            print(f"  {p} → nerecunoscut")
        for r in res:
            typ, extra = r.split(" ", 1)
            found.setdefault(typ, []).append((p, json.loads(extra)))
            print(f"  {p} → {r}")
    changed = 0

    # Seplos: înlocuim blocul (blocurile) Seplos neconfigurate cu câte unul pe pachet
    seplos = found.pop("seplos", [])
    if seplos:
        block_re = re.compile(r"(?ms)^  - id: [^\n]*\n(?:(?!  - id:|\n\S).)*?type:\s*seplos\b(?:(?!  - id:|\n\S).)*\n?")
        blocks = [m for m in block_re.finditer(text) if "REPLACE-" in m.group(0)]
        if blocks:
            cap = re.search(r"capacity_ah:\s*([\d.]+)", blocks[0].group(0))
            capacity = cap.group(1) if cap else "280"
            multi = len(seplos) > 1
            new = "".join(SEPLOS_BLOCK.format(
                id=f"seplos{i + 1}" if multi else "seplos",
                name=f"Seplos {i + 1}" if multi else "Seplos",
                port=port, baudrate=ex["baudrate"], address=ex["address"], capacity=capacity)
                for i, (port, ex) in enumerate(seplos))
            first, last = blocks[0], blocks[-1]
            tail = text[last.end():]
            text = text[:first.start()] + new + ("\n" if not tail.startswith("\n") else "") + tail
            changed += len(seplos)

    for typ, items in found.items():
        placeholders = [m for m in re.finditer(r"/dev/serial/by-id/REPLACE-[\w-]+|(?<=port: )auto(?=\s)", text)
                        if _device_type_near(text, m.start()) == typ]
        # înlocuire după poziție (de la coadă), ca „auto” din comentarii să rămână neatins
        for m, (port, extra) in reversed(list(zip(placeholders, items))):
            text = text[:m.start()] + port + text[m.end():]
            changed += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    left = text.count("REPLACE-") - text.count("REPLACE-TOPIC")
    print(f"\n{changed} dispozitive completate în {path}." +
          (f" Au rămas {left} dispozitive negăsite (adormite, nelegate sau protocol diferit)." if left else ""))


def _device_type_near(text: str, pos: int) -> str | None:
    """Tipul ('type: ...') al blocului de dispozitiv în care se află poziția dată."""
    import re
    start = text.rfind("- id:", 0, pos)
    m = re.search(r"type:\s*(\w+)", text[start:pos + 200]) if start >= 0 else None
    return m.group(1) if m else None


def cmd_read(args) -> None:
    from .drivers import make_driver
    cfg = load_config(args.config)
    dev = next((d for d in cfg["devices"] if d["id"] == args.device), None)
    if not dev:
        sys.exit(f"nu există dispozitivul '{args.device}' în {args.config}")
    drv = make_driver(dev, cfg["devices"])
    try:
        r = drv.read()
        print(json.dumps(r.__dict__, indent=2, ensure_ascii=False, default=str))
    finally:
        drv.close()


def cmd_notify(args) -> None:
    """Trimite o notificare pe telefon (folosit de scripturile din deploy/, ex. copia de siguranță)."""
    import aiohttp
    from .notify import Notifier
    cfg = load_config(args.config)
    n = Notifier(cfg["notify"])
    state_dir = os.path.dirname(os.path.abspath(cfg["database"]))
    if os.path.exists(os.path.join(state_dir, "vapid.json")):      # cheile le creează serviciul amper
        from .db import Database
        from .webpush import WebPush, public_url
        n.webpush = WebPush(Database(cfg["database"]), state_dir, subject=public_url(cfg))
    if not n.enabled:
        sys.exit("notificările nu sunt configurate")

    async def go():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            await n._deliver(http, {"message": args.message, "title": args.title, "priority": args.priority,
                                    "url": args.url})
    asyncio.run(go())


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["update"]:               # sudo amper update --install: totul merge mai departe la amper.update
        from .update import main as update_main
        return update_main(argv[1:])
    p = argparse.ArgumentParser(prog="amper", description="Monitorizare baterii și invertor")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    s = sub.add_parser("simulate")
    s.add_argument("--hours", type=float, default=48)
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--db", default="/tmp/amper-sim.db")
    s.add_argument("--keep", action="store_true", help="păstrează baza de date simulată existentă")
    s.add_argument("--ghid", action="store_true", help="pornește cu ghidul de configurare (prima pornire)")
    s.add_argument("--lite", action="store_true", help="ediția Lite (fără setările BMS și ale invertorului)")
    sub.add_parser("scan")
    ac = sub.add_parser("autoconfig")
    ac.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    rd = sub.add_parser("read")
    rd.add_argument("device")
    rd.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    nt = sub.add_parser("notify")
    nt.add_argument("message")
    nt.add_argument("--title", default="Amper")
    nt.add_argument("--priority", default="default", choices=["low", "default", "high"])
    nt.add_argument("--url", help="ce deschide atingerea notificării în aplicație (ex. /?v=update)")
    nt.add_argument("-c", "--config", default="/etc/amper/config.yaml")
    sub.add_parser("update", help="actualizări fără aplicație: --check, --install, --rollback (cu sudo)")
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    {"run": cmd_run, "simulate": cmd_simulate, "scan": cmd_scan, "read": cmd_read,
     "autoconfig": cmd_autoconfig, "notify": cmd_notify}[args.cmd](args)


if __name__ == "__main__":
    main()

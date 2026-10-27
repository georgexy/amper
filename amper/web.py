"""Serverul web: API JSON, WebSocket cu date live și aplicația de telefon (PWA)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import __version__
from .drivers.base import DriverError

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent.parent / "web"
# serviciul amper-update (root): API local cu token
UPDATE_API, UPDATE_TOKEN = "http://127.0.0.1:8092", "/run/amper-update/token"


def _json(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def make_app(engine) -> web.Application:
    cfg = engine.cfg
    from .auth import Auth
    from .config import setup_needed
    state_dir = None if cfg["database"] == ":memory:" else os.path.dirname(os.path.abspath(cfg["database"]))
    auth_ = Auth(cfg, engine.db, engine.notifier, state_dir)
    engine.auth = auth_
    COOKIE_AGE = 3600 * 24 * 365

    def client_ip(request) -> str:
        """Adresa reală: prin proxy-ul Tailscale (de pe 127.0.0.1) e primul X-Forwarded-For."""
        if request.remote in ("127.0.0.1", "::1"):
            xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if xff:
                return xff
        return request.remote or "?"

    def secure(request) -> bool:
        return request.secure or request.headers.get("X-Forwarded-Proto", "") == "https"

    def set_cookies(request, resp, quiet=False):
        resp.set_cookie("amper", auth_.token, max_age=COOKIE_AGE, httponly=True, samesite="Strict",
                        secure=secure(request))
        dev = auth_.seen_browser(request.cookies.get("amper_dev"), client_ip(request),
                                 request.headers.get("User-Agent", ""), quiet=quiet)
        resp.set_cookie("amper_dev", dev, max_age=COOKIE_AGE * 10, httponly=True, samesite="Strict",
                        secure=secure(request))

    async def confirm(request, body: dict, what: str):
        """Scrierile importante (BMS, invertor, WiFi, actualizare) cer parola aplicației din nou, în
        `app_password` — și de pe ecranul HDMI. Întoarce None dacă e în regulă, altfel răspunsul de eroare."""
        if not auth_.enabled:
            return _json({"error": "Setați întâi o parolă pentru aplicație (Setări → Ghid de configurare)."}, 403)
        ok, wait = await asyncio.to_thread(auth_.attempt, client_ip(request), body.get("app_password", ""),
                                           request.headers.get("User-Agent", ""), what)
        if ok:
            return None
        if wait:
            return _json({"error": f"Prea multe parole greșite. Încearcă din nou peste {wait} s.", "wait": wait}, 429)
        await asyncio.sleep(1)
        return _json({"error": "Parola nu e corectă.", "bad_password": True}, 403)

    def local(request) -> bool:
        # ecranul HDMI (kiosk) rulează pe Pi și nu are tastatură: nu i se cere parola.
        # Cererile venite prin `tailscale serve` (https) ajung tot de la 127.0.0.1, dar cu antetele proxy-ului:
        # acelea vin de pe telefon și trebuie să ceară parola.
        if any(h in request.headers for h in ("X-Forwarded-For", "Tailscale-User-Login", "Forwarded")):
            return False
        return request.remote in ("127.0.0.1", "::1")

    @web.middleware
    async def auth(request, handler):
        if auth_.enabled and request.path.startswith("/api/") and request.path not in ("/api/login", "/api/health") \
                and not local(request):
            if not auth_.valid_cookie(request.cookies.get("amper")):
                return _json({"error": "autentificare necesară"}, 401)
        return await handler(request)

    @web.middleware
    async def headers(request, handler):
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                     "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if secure(request):
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if isinstance(resp, web.HTTPException):
            raise resp
        return resp

    @web.middleware
    async def no_cache_api(request, handler):
        resp = await handler(request)
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[headers, auth, no_cache_api])
    app["engine"] = engine

    # Versiunea aplicației = amprenta fișierelor din web/ (nume, mărime, data modificării), recalculată cel mult
    # o dată la 30 s. Telefoanele o primesc live și se reîncarcă singure când se schimbă (o actualizare pe Pi).
    ver = {"t": 0.0, "v": ""}

    def app_version() -> str:
        if time.monotonic() - ver["t"] > 30:
            h = hashlib.sha1(__version__.encode())          # și o versiune nouă a programului reîncarcă aplicația
            for p in sorted(STATIC.rglob("*")):
                if p.is_file():
                    st = p.stat()
                    h.update(f"{p.relative_to(STATIC)}:{st.st_size}:{int(st.st_mtime)}".encode())
            ver.update(t=time.monotonic(), v=h.hexdigest()[:12])
        return ver["v"]
    engine.app_version = app_version

    async def index(request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def login(request):
        body = await request.json()
        if not auth_.enabled:
            return _json({"ok": True})
        ok, wait = await asyncio.to_thread(auth_.attempt, client_ip(request), body.get("password", ""),
                                           request.headers.get("User-Agent", ""))
        if ok:
            resp = _json({"ok": True})
            set_cookies(request, resp)
            return resp
        if wait:
            return _json({"error": f"Prea multe parole greșite. Încearcă din nou peste {wait} s.", "wait": wait}, 429)
        await asyncio.sleep(1)
        return _json({"error": "parolă greșită"}, 401)

    async def health(request):
        """Pentru amper-update: serviciul a pornit și rulează versiunea așteptată."""
        return _json({"ok": True, "version": __version__})

    async def live(request):
        return _json(engine.snapshot())

    async def info(request):
        devs = [dict(id=d["id"], name=d.get("name", d["id"]), type=d["type"],
                     kind=engine.devices[d["id"]].driver.kind, capacity_ah=d.get("capacity_ah"))
                for d in cfg["devices"]]
        wp = engine.notifier.webpush
        return _json(dict(devices=devs, auth=auth_.enabled, poll_interval=cfg["poll_interval"], version=__version__,
                          site_name=cfg.get("site_name") or "", setup_needed=setup_needed(cfg),
                          public_url=wp.subject if wp else None,
                          notifications=engine.notifier.enabled,
                          simulated=any(d["type"].startswith("sim_") for d in cfg["devices"])))

    async def history(request):
        device = request.query.get("device")
        if device not in engine.devices:
            return _json({"error": "dispozitiv necunoscut"}, 400)
        hours = min(float(request.query.get("hours", 24)), 24 * 400)
        end = int(time.time())
        start = end - int(hours * 3600)
        kind = engine.devices[device].driver.kind
        rows = await asyncio.to_thread(engine.db.history, kind, device, start, end,
                                       int(request.query.get("points", 300)), cfg["raw_retention_days"])
        return _json(dict(device=device, kind=kind, start=start, end=end, rows=rows))

    async def energy(request):
        inv = [k for k, v in engine.devices.items() if v.driver.kind == "inverter"]
        if not inv:
            return _json(dict(rows=[]))
        start, end = request.query.get("from"), request.query.get("to")
        if start and end:                     # interval ales în aplicație (AAAA-LL-ZZ)
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)):
                return _json({"error": "date invalide"}, 400)
            rows = await asyncio.to_thread(engine.db.energy_range, inv[0], min(start, end), max(start, end))
        else:
            rows = await asyncio.to_thread(engine.db.energy, inv[0], int(request.query.get("days", 30)))
        return _json(dict(rows=rows))

    async def alerts(request):
        rows = await asyncio.to_thread(engine.db.alerts, int(request.query.get("limit", 100)))
        return _json(dict(active=engine.alerts.active_list(), history=rows))

    async def test_notification(request):
        if not engine.notifier.enabled:
            return _json({"ok": False, "error": "Notificările nu sunt configurate (vezi notify în config.yaml)."})
        engine.notifier.send("Notificare de test din Amper.", title="Test", priority="default")
        return _json({"ok": True})

    async def ws(request):
        sock = web.WebSocketResponse(heartbeat=30)
        await sock.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        try:
            await sock.send_str(json.dumps(engine.snapshot(), ensure_ascii=False, default=str))
            reader = asyncio.create_task(_drain(sock))
            while not sock.closed and not reader.done():
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    await sock.send_str(json.dumps(getter.result(), ensure_ascii=False, default=str))
                else:
                    getter.cancel()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            engine.subscribers.discard(q)
        return sock

    async def _drain(sock):
        async for msg in sock:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break

    # ---------------- setările invertorului ----------------
    from . import inverter_settings as invset
    write_lock = asyncio.Lock()

    def settings_driver():
        for k, st in engine.devices.items():
            if st.driver.kind == "inverter" and hasattr(st.driver, "read_settings"):
                return k, st.driver
        return None, None

    async def inverter_get(request):
        dev, drv = settings_driver()
        if not drv:
            return _json({"error": "invertorul nu are setări disponibile"}, 404)
        try:
            regs = await asyncio.to_thread(drv.read_settings)
        except (DriverError, OSError) as e:
            return _json({"error": f"nu am putut citi setările: {e}"}, 502)
        log_rows = await asyncio.to_thread(engine.db.settings_log, dev, 20)
        return _json(dict(device=dev, settings=invset.describe(regs), clock=invset.clock(regs),
                          pi_clock=time.strftime("%Y-%m-%d %H:%M:%S"), log=log_rows,
                          can_write=auth_.enabled))

    async def inverter_write(request, body, fn, what):
        # scrierea cere ÎNTOTDEAUNA parola din nou, și de pe ecranul HDMI (localhost)
        if (err := await confirm(request, body, "setare invertor")) is not None:
            return err
        dev, drv = settings_driver()
        if not drv:
            return _json({"error": "invertorul nu are setări disponibile"}, 404)
        async with write_lock:
            try:
                res = await asyncio.to_thread(fn, drv)
            except ValueError as e:
                return _json({"error": str(e)}, 400)
            except (DriverError, OSError) as e:
                await asyncio.to_thread(engine.db.log_setting, int(time.time()), dev, what, None, None, False,
                                        str(e), request.remote)
                log.warning("setare invertor %s: EȘUAT: %s", what, e)
                return _json({"error": str(e)}, 502)
        if res["changed"]:
            await asyncio.to_thread(engine.db.log_setting, int(time.time()), dev, res["label"], res["old"],
                                    res["new"], True, None, request.remote)
            log.warning("setare invertor schimbată: %s: %s → %s (de la %s)", res["label"], res["old"], res["new"],
                        request.remote)
            engine.notifier.send(f"⚙️ Invertor: {res['label']}: {res['old']} → {res['new']}",
                                 title="Amper: setare schimbată", priority="default")
        return _json(dict(ok=True, changed=res["changed"], label=res["label"], old=res["old"], new=res["new"],
                          settings=invset.describe(res["regs"]), clock=invset.clock(res["regs"])))

    async def inverter_set(request):
        body = await request.json()
        key, value = body.get("key"), body.get("value")
        return await inverter_write(request, body, lambda drv: invset.write_setting(drv, key, value), str(key))

    async def inverter_clock(request):
        body = await request.json() if request.can_read_body else {}
        return await inverter_write(request, body, invset.sync_clock, "ceas")

    # ---------------- notificări direct în aplicație (Web Push) ----------------
    async def push_key(request):
        wp = engine.notifier.webpush
        if not wp:
            return _json({"error": "Web Push indisponibil pe acest Pi"}, 404)
        return _json({"key": wp.vapid.public_b64, "count": len(engine.db.push_subs())})

    async def push_subscribe(request):
        body = await request.json()
        sub = body.get("subscription") or {}
        keys = sub.get("keys") or {}
        if not str(sub.get("endpoint", "")).startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            return _json({"error": "abonament invalid"}, 400)
        await asyncio.to_thread(engine.db.add_push, sub["endpoint"], keys["p256dh"], keys["auth"],
                                request.headers.get("User-Agent", "")[:200])
        log.info("telefon nou abonat la notificări (%s)", sub["endpoint"].split("/")[2])
        return _json({"ok": True})

    async def push_unsubscribe(request):
        body = await request.json()
        await asyncio.to_thread(engine.db.delete_push, str(body.get("endpoint", "")))
        return _json({"ok": True})

    # ---------------- Setări → Info: Pi-ul, rețeaua, porturile; schimbarea rețelei WiFi ----------------
    from . import sysinfo
    WIFI_API, WIFI_TOKEN = "http://127.0.0.1:8091", "/run/amper-wifi/token"

    async def root_api(base: str, token_path: str, what: str, method: str, path: str, body=None, timeout=40):
        """Cererile către serviciile care rulează ca root (amper-wifi, amper-update), cu token."""
        import aiohttp
        try:
            with open(token_path) as f:
                token = f.read().strip()
        except OSError:
            return {"error": f"serviciul {what} nu rulează"}, 503
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
                async with s.request(method, base + path, json=body, headers={"X-Token": token}) as r:
                    return await r.json(), r.status
        except Exception as e:
            return {"error": f"serviciul {what} nu răspunde ({e.__class__.__name__})"}, 503

    async def wifi_api(method: str, path: str, body=None):
        """Serviciul amper-wifi (root, singurul care poate schimba rețeaua)."""
        return await root_api(WIFI_API, WIFI_TOKEN, "WiFi (amper-wifi)", method, path, body)

    async def system_info(request):
        info = await asyncio.to_thread(sysinfo.gather, engine)
        info["wifi_job"], _ = await wifi_api("GET", "/status")
        return _json(info)

    async def wifi_scan(request):
        data, status = await wifi_api("GET", "/scan")
        return _json(data, status)

    async def wifi_status(request):
        data, status = await wifi_api("GET", "/status")
        return _json(data, status)

    async def wifi_connect(request):
        # schimbarea rețelei cere ÎNTOTDEAUNA parola aplicației din nou (și de pe ecranul HDMI)
        body = await request.json()
        if (err := await confirm(request, body, "schimbare WiFi")) is not None:
            return err
        data, status = await wifi_api("POST", "/connect", {"ssid": body.get("ssid"), "password": body.get("password", "")})
        if status == 200:
            log.warning("schimbare rețea WiFi cerută din aplicație: „%s” (de la %s)", body.get("ssid"), request.remote)
        return _json(data, status)

    # ---------------- actualizări (serviciul amper-update, root) ----------------
    def update_api(method: str, path: str, body=None):
        return root_api(UPDATE_API, UPDATE_TOKEN, "de actualizare (amper-update)", method, path, body, timeout=90)

    async def update_status(request):
        data, status = await update_api("GET", "/status")
        return _json({**data, "running": __version__}, status)

    async def update_check(request):
        data, status = await update_api("POST", "/check")
        return _json({**data, "running": __version__}, status)

    async def update_download(request):
        # doar descarcă și verifică (nu schimbă nimic pe Pi): ajunge logarea
        data, status = await update_api("POST", "/download")
        return _json(data, status)

    async def update_action(request):
        # actualizarea și revenirea cer parola aplicației, introdusă din nou — și de pe ecranul
        # HDMI (localhost, fără cookie); de pe telefon, middleware-ul `auth` cere în plus cookie-ul
        body = await request.json()
        if (err := await confirm(request, body, "actualizare")) is not None:
            return err
        action = request.match_info["action"]
        data, status = await update_api("POST", "/" + action)
        if status == 200:
            log.warning("%s cerută din aplicație (de la %s)",
                        "actualizare" if action == "install" else "revenire la versiunea anterioară", request.remote)
        return _json(data, status)

    # ---------------- ghidul de configurare (prima pornire; se poate relua din Setări) ----------------
    from . import setup_scan
    from .config import ConfigError, save_overlay, validate
    scan_job: dict = {"state": "idle"}

    async def setup_get(request):
        return _json(dict(needed=setup_needed(cfg), done=bool(cfg.get("setup_done")), site_name=cfg.get("site_name") or "",
                          has_password=auth_.enabled, simulated=any(d["type"].startswith("sim_") for d in cfg["devices"]),
                          devices=[{k: d.get(k) for k in ("id", "name", "type", "port", "ble", "capacity_ah",
                                                          "baudrate", "address")} for d in cfg["devices"]],
                          scan=scan_job))

    async def setup_name(request):
        body = await request.json()
        name = str(body.get("name") or "").strip()[:60]
        await asyncio.to_thread(save_overlay, cfg, {"site_name": name})
        cfg["site_name"] = name
        return _json({"ok": True, "site_name": name})

    async def setup_password(request):
        body = await request.json()
        if auth_.enabled and (err := await confirm(request, {"app_password": body.get("old", "")}, "schimbare parolă")):
            return err
        if str(body.get("password", "")) != str(body.get("repeat", "")):
            return _json({"error": "Parolele nu sunt la fel."}, 400)
        try:
            await asyncio.to_thread(auth_.set_password, str(body.get("password", "")))
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        engine.notifier.send(f"🔐 Parola aplicației a fost schimbată (de la {client_ip(request)}).",
                             title="Amper: securitate", priority="high")
        resp = _json({"ok": True})
        set_cookies(request, resp, quiet=True)          # browserul care a pus parola rămâne logat
        return resp

    async def setup_scan_start(request):
        if scan_job.get("state") == "running":
            return _json(scan_job)
        scan_job.clear()
        scan_job.update(state="running", message="Pornesc căutarea…", started=time.time())

        def progress(msg):
            scan_job["message"] = msg

        async def run():
            try:
                res = await asyncio.to_thread(setup_scan.scan, cfg, progress)
                scan_job.update(state="done", message="", **res)
            except Exception as e:
                log.exception("căutarea dispozitivelor")
                scan_job.update(state="failed", message=f"Căutarea a eșuat: {e}")
        asyncio.get_running_loop().create_task(run())
        return _json(scan_job)

    async def setup_scan_get(request):
        return _json(scan_job)

    async def setup_devices(request):
        body = await request.json()
        try:
            devs = setup_scan.build_devices(list(body.get("devices") or []))
            validate({**cfg, "devices": [dict(d) for d in devs]})
        except (ValueError, TypeError, ConfigError) as e:
            return _json({"error": str(e)}, 400)
        await asyncio.to_thread(save_overlay, cfg, {"devices": devs})
        cfg["pending_devices"] = devs                   # se aplică la repornire (finish)
        log.warning("dispozitive noi din ghidul de configurare: %s", ", ".join(d["id"] for d in devs))
        return _json({"ok": True, "devices": devs})

    def restart_soon():
        """Serviciul se oprește curat; systemd îl repornește (Restart=always) cu config-ul nou."""
        import signal
        asyncio.get_running_loop().call_later(1.5, os.kill, os.getpid(), signal.SIGTERM)

    async def setup_finish(request):
        if not auth_.enabled:
            return _json({"error": "Alege întâi o parolă pentru aplicație."}, 400)
        await asyncio.to_thread(save_overlay, cfg, {"setup_done": True})
        cfg["setup_done"] = True
        restart = bool(cfg.pop("pending_devices", None)) and not any(d["type"].startswith("sim_") for d in cfg["devices"])
        if restart:
            restart_soon()
        log.warning("ghidul de configurare terminat%s", " — repornesc cu dispozitivele noi" if restart else "")
        return _json({"ok": True, "restart": restart})

    async def security_get(request):
        rows = await asyncio.to_thread(engine.db.login_log, 30)
        devs = await asyncio.to_thread(engine.db.login_devices)
        from .auth import short_ua
        return _json(dict(log=[{**r, "ua": short_ua(r.get("ua") or "")} for r in rows],
                          devices=[{"first": d["first_ts"], "last": d["last_ts"], "ip": d["ip"],
                                    "ua": short_ua(d.get("ua") or ""), "this": d["id"] == request.cookies.get("amper_dev")}
                                   for d in devs]))

    async def update_channel(request):
        body = await request.json()
        ch = body.get("channel")
        if ch not in ("stable", "beta"):
            return _json({"error": "canal necunoscut"}, 400)
        data, status = await update_api("POST", "/channel", {"channel": ch})
        return _json(data, status)

    async def daly_params_raw(request):
        """Parametrii unui BMS Daly (doar citire), prin legătura folosită deja la citiri."""
        st = engine.devices.get(request.match_info["device"])
        drv = getattr(st.driver, "inner", None) or getattr(st, "driver", None) if st else None
        if not drv or not hasattr(drv, "read_params"):
            return _json({"error": "nu e un BMS Daly legat"}, 404)
        try:
            raw = await asyncio.to_thread(drv.read_params)
        except Exception as e:
            return _json({"error": str(e)}, 502)
        return _json({"device": request.match_info["device"], "raw": raw})

    # ---------------- setările BMS Daly (citire + scriere verificată) ----------------
    from . import daly_params
    daly_lock = asyncio.Lock()

    def daly_driver(dev_id):
        st = engine.devices.get(dev_id)
        if not st or st.cfg.get("type") != "daly":
            return None, None
        drv = getattr(st.driver, "inner", None) or st.driver
        return st, (drv if hasattr(drv, "_query") and hasattr(drv, "address") else None)

    async def daly_settings_get(request):
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        blocks = await asyncio.to_thread(daly_params.read_blocks, drv)
        if not blocks:
            return _json({"error": "BMS-ul nu răspunde acum (adormit sau departe)"}, 502)
        log_rows = await asyncio.to_thread(engine.db.settings_log, st.cfg["id"], 20)
        return _json(dict(device=st.cfg["id"], name=st.cfg.get("name"), settings=daly_params.describe(blocks),
                          log=log_rows, can_write=auth_.enabled))

    async def daly_settings_set(request):
        body = await request.json()
        if (err := await confirm(request, body, "setare BMS")) is not None:
            return err
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        key = str(body.get("key"))
        name = st.cfg.get("name", st.cfg["id"])
        async with daly_lock:
            try:
                res = await asyncio.to_thread(daly_params.write_field, drv, key, body.get("value"))
            except ValueError as e:
                return _json({"error": str(e)}, 400)
            except (DriverError, OSError) as e:
                await asyncio.to_thread(engine.db.log_setting, int(time.time()), st.cfg["id"], key, None, None,
                                        False, str(e), request.remote)
                log.warning("setare BMS %s %s: EȘUAT: %s", name, key, e)
                return _json({"error": str(e)}, 502)
        if res["changed"]:
            await asyncio.to_thread(engine.db.log_setting, int(time.time()), st.cfg["id"], res["label"], res["old"],
                                    res["new"], True, None, request.remote)
            log.warning("setare BMS schimbată: %s: %s: %s → %s (de la %s)", name, res["label"], res["old"],
                        res["new"], request.remote)
            engine.notifier.send(f"⚙️ {name}: {res['label']}: {res['old']} → {res['new']}",
                                 title="Amper: setare BMS schimbată", priority="default")
        blocks = await asyncio.to_thread(daly_params.read_blocks, drv)
        return _json(dict(ok=True, changed=res["changed"], old=res["old"], new=res["new"],
                          settings=daly_params.describe(blocks)))

    # ---------------- alerte proprii (setate de utilizator) ----------------
    from .alerts import METRICS

    async def user_alerts_list(request):
        rules = await asyncio.to_thread(engine.db.user_alerts)
        return _json(dict(rules=rules, metrics=[dict(id=k, name=v[0], unit=v[1]) for k, v in METRICS.items()]))

    async def user_alerts_add(request):
        b = await request.json()
        try:
            metric, op, value = str(b.get("metric")), str(b.get("op")), float(b.get("value"))
            hold_s = int(float(b.get("hold_min") or 0) * 60)
        except (TypeError, ValueError):
            return _json({"error": "valoare invalidă"}, 400)
        level = str(b.get("level") or "warning")
        if metric not in METRICS or op not in ("<", ">") or level not in ("info", "warning", "critical") \
                or not 0 <= hold_s <= 24 * 3600:
            return _json({"error": "alertă invalidă"}, 400)
        name = str(b.get("name") or "").strip()[:80] or None
        rid = await asyncio.to_thread(engine.db.add_user_alert, name, metric, op, value, hold_s, level,
                                      bool(b.get("notify_clear", True)))
        engine.alerts.reload_rules()
        log.info("alertă proprie adăugată #%d: %s %s %s", rid, metric, op, value)
        return _json({"ok": True, "id": rid})

    async def user_alerts_change(request):
        rid = int(request.match_info["id"])
        if request.method == "DELETE":
            await asyncio.to_thread(engine.db.delete_user_alert, rid)
        else:
            b = await request.json()
            await asyncio.to_thread(engine.db.set_user_alert_enabled, rid, bool(b.get("enabled")))
        engine.alerts.reload_rules()
        return _json({"ok": True})

    async def daly_search(request):
        from .drivers import daly_auto
        daly_auto.trigger()
        return _json({"ok": True})

    async def debug(request):
        """Date brute de la un dispozitiv — util la punerea în funcțiune."""
        st = engine.devices.get(request.match_info["device"])
        if not st:
            return _json({"error": "dispozitiv necunoscut"}, 404)
        r = st.reading
        return _json(dict(error=st.error, errors=st.errors, last_ok=st.last_ok,
                          reading=r.__dict__ if r else None))

    app.router.add_get("/", index)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/info", info)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/live", live)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/energy", energy)
    app.router.add_get("/api/alerts", alerts)
    app.router.add_post("/api/test-notification", test_notification)
    app.router.add_get("/api/debug/{device}", debug)
    app.router.add_get("/api/inverter/settings", inverter_get)
    app.router.add_post("/api/inverter/settings", inverter_set)
    app.router.add_post("/api/inverter/clock", inverter_clock)
    app.router.add_post("/api/daly/search", daly_search)
    app.router.add_get("/api/user-alerts", user_alerts_list)
    app.router.add_post("/api/user-alerts", user_alerts_add)
    app.router.add_post("/api/user-alerts/{id:\\d+}", user_alerts_change)
    app.router.add_delete("/api/user-alerts/{id:\\d+}", user_alerts_change)
    app.router.add_get("/api/daly/{device}/params", daly_params_raw)
    app.router.add_get("/api/daly/{device}/settings", daly_settings_get)
    app.router.add_post("/api/daly/{device}/settings", daly_settings_set)
    app.router.add_get("/api/system", system_info)
    app.router.add_get("/api/wifi/scan", wifi_scan)
    app.router.add_get("/api/wifi/status", wifi_status)
    app.router.add_post("/api/wifi/connect", wifi_connect)
    app.router.add_get("/api/setup", setup_get)
    app.router.add_post("/api/setup/name", setup_name)
    app.router.add_post("/api/setup/password", setup_password)
    app.router.add_post("/api/setup/scan", setup_scan_start)
    app.router.add_get("/api/setup/scan", setup_scan_get)
    app.router.add_post("/api/setup/devices", setup_devices)
    app.router.add_post("/api/setup/finish", setup_finish)
    app.router.add_get("/api/security", security_get)
    app.router.add_post("/api/update/channel", update_channel)
    app.router.add_get("/api/update/status", update_status)
    app.router.add_post("/api/update/check", update_check)
    app.router.add_post("/api/update/download", update_download)
    app.router.add_post("/api/update/{action:install|rollback}", update_action)
    app.router.add_get("/api/push/key", push_key)
    app.router.add_post("/api/push/subscribe", push_subscribe)
    app.router.add_post("/api/push/unsubscribe", push_unsubscribe)
    app.router.add_get("/api/ws", ws)
    app.router.add_static("/", STATIC, show_index=False)
    return app

"""Serverul web: API JSON, WebSocket cu date live și aplicația de telefon (PWA)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from .drivers.base import DriverError

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent.parent / "web"


def _json(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def make_app(engine) -> web.Application:
    cfg = engine.cfg
    password = (cfg.get("web") or {}).get("password") or ""
    secret = hashlib.sha256(("amper:" + password).encode()).hexdigest()
    token = hmac.new(secret.encode(), b"session", "sha256").hexdigest()

    def local(request) -> bool:
        # ecranul HDMI (kiosk) rulează pe Pi și nu are tastatură: nu i se cere parola.
        # Cererile venite prin `tailscale serve` (https) ajung tot de la 127.0.0.1, dar cu antetele proxy-ului:
        # acelea vin de pe telefon și trebuie să ceară parola.
        if any(h in request.headers for h in ("X-Forwarded-For", "Tailscale-User-Login", "Forwarded")):
            return False
        return request.remote in ("127.0.0.1", "::1")

    @web.middleware
    async def auth(request, handler):
        if password and request.path.startswith("/api/") and request.path != "/api/login" and not local(request):
            if request.cookies.get("amper") != token:
                return _json({"error": "autentificare necesară"}, 401)
        return await handler(request)

    @web.middleware
    async def no_cache_api(request, handler):
        resp = await handler(request)
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[auth, no_cache_api])
    app["engine"] = engine

    # Versiunea aplicației = amprenta fișierelor din web/ (nume, mărime, data modificării), recalculată cel mult
    # o dată la 30 s. Telefoanele o primesc live și se reîncarcă singure când se schimbă (o actualizare pe Pi).
    ver = {"t": 0.0, "v": ""}

    def app_version() -> str:
        if time.monotonic() - ver["t"] > 30:
            h = hashlib.sha1()
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
        if not password or hmac.compare_digest(str(body.get("password", "")), password):
            resp = _json({"ok": True})
            resp.set_cookie("amper", token, max_age=3600 * 24 * 365, httponly=True, samesite="Lax")
            return resp
        await asyncio.sleep(1)
        return _json({"error": "parolă greșită"}, 401)

    async def live(request):
        return _json(engine.snapshot())

    async def info(request):
        devs = [dict(id=d["id"], name=d.get("name", d["id"]), type=d["type"],
                     kind=engine.devices[d["id"]].driver.kind, capacity_ah=d.get("capacity_ah"))
                for d in cfg["devices"]]
        wp = engine.notifier.webpush
        return _json(dict(devices=devs, auth=bool(password), poll_interval=cfg["poll_interval"],
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
                          can_write=bool(password)))

    async def inverter_write(request, fn, what):
        # scrierea cere ÎNTOTDEAUNA parola, și de pe ecranul HDMI (localhost)
        if not password:
            return _json({"error": "Setați întâi o parolă pentru aplicație (web.password în config)."}, 403)
        if request.cookies.get("amper") != token:
            return _json({"error": "autentificare necesară"}, 401)
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
        return await inverter_write(request, lambda drv: invset.write_setting(drv, key, value), str(key))

    async def inverter_clock(request):
        return await inverter_write(request, invset.sync_clock, "ceas")

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

    async def wifi_api(method: str, path: str, body=None):
        """Cererile către serviciul amper-wifi (rulează ca root, singurul care poate schimba rețeaua)."""
        import aiohttp
        try:
            with open(WIFI_TOKEN) as f:
                token = f.read().strip()
        except OSError:
            return {"error": "serviciul WiFi (amper-wifi) nu rulează"}, 503
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
                async with s.request(method, WIFI_API + path, json=body, headers={"X-Token": token}) as r:
                    return await r.json(), r.status
        except Exception as e:
            return {"error": f"serviciul WiFi nu răspunde ({e.__class__.__name__})"}, 503

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
        # schimbarea rețelei cere ÎNTOTDEAUNA parola aplicației (și de pe ecranul HDMI)
        if not password:
            return _json({"error": "Setați întâi o parolă pentru aplicație (web.password în config)."}, 403)
        if request.cookies.get("amper") != token:
            return _json({"error": "autentificare necesară"}, 401)
        body = await request.json()
        data, status = await wifi_api("POST", "/connect", {"ssid": body.get("ssid"), "password": body.get("password", "")})
        if status == 200:
            log.warning("schimbare rețea WiFi cerută din aplicație: „%s” (de la %s)", body.get("ssid"), request.remote)
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
                          log=log_rows, can_write=bool(password)))

    async def daly_settings_set(request):
        if not password:
            return _json({"error": "Setați întâi o parolă pentru aplicație (web.password în config)."}, 403)
        if request.cookies.get("amper") != token:
            return _json({"error": "autentificare necesară"}, 401)
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        body = await request.json()
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
    app.router.add_get("/api/push/key", push_key)
    app.router.add_post("/api/push/subscribe", push_subscribe)
    app.router.add_post("/api/push/unsubscribe", push_unsubscribe)
    app.router.add_get("/api/ws", ws)
    app.router.add_static("/", STATIC, show_index=False)
    return app

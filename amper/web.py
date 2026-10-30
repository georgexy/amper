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
from .models import PackReading

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent.parent / "web"
# serviciul amper-update (root): API local cu token
UPDATE_API, UPDATE_TOKEN = "http://127.0.0.1:8092", "/run/amper-update/token"


def _json(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def sum_energy(parts: list[list[dict]]) -> list[dict]:
    """Energia pe zi a mai multor invertoare în paralel, adunată pe aceeași zi (ordinea primului invertor)."""
    if len(parts) == 1:
        return parts[0]
    out: dict[str, dict] = {}
    for rows in parts:
        for r in rows:
            o = out.setdefault(r["day"], {"day": r["day"], "device": "toate"})
            for k, v in r.items():
                if k not in ("day", "device") and isinstance(v, (int, float)):
                    o[k] = round(o.get(k, 0) + v, 3)
    days = [r["day"] for r in parts[0]]
    desc = len(days) > 1 and days[0] > days[-1]           # aceeași ordine ca la un singur invertor
    return [out[d] for d in sorted(out, reverse=desc)]


def make_app(engine) -> web.Application:
    cfg = engine.cfg
    from .auth import Auth
    from .config import setup_needed
    state_dir = None if cfg["database"] == ":memory:" else os.path.dirname(os.path.abspath(cfg["database"]))
    auth_ = Auth(cfg, engine.db, engine.notifier, state_dir)
    engine.auth = auth_
    from . import edition as edition_
    edition_.init_base(cfg, engine.db)
    pid = edition_.pi_id()
    ed = {"v": edition_.current(cfg, pid)}          # „lite” / „pro”; se schimbă la activarea cu cod
    # doar în Pro: setările BMS și ale invertorului și datele brute de depanare (serverul refuză, nu doar pagina)
    PRO_ONLY = re.compile(r"^/api/(inverter/(settings|clock)|daly/[^/]+/(settings|params|actions?(/retry)?)|debug/[^/]+)$")
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

    def proxied(request) -> bool:
        """Cerere venită prin `tailscale serve` / `funnel` (https, din tailnet sau din internet)."""
        return any(h in request.headers for h in ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded",
                                                   "Tailscale-User-Login", "Tailscale-Funnel-Request"))

    def reset_pending() -> bool:
        """Parola a fost ștearsă cu fișierul de pe card (password_reset.py) și încă nu s-a ales alta."""
        return bool(cfg.get("password_reset")) and not auth_.enabled

    def local(request) -> bool:
        # ecranul HDMI (kiosk) rulează pe Pi și nu are tastatură: nu i se cere parola.
        # Cererile venite prin `tailscale serve` / `tailscale funnel` (https, din tailnet sau din internet) ajung
        # tot de la 127.0.0.1, dar cu antetele proxy-ului: acelea vin de pe telefon și trebuie să ceară parola.
        if proxied(request):
            return False
        return request.remote in ("127.0.0.1", "::1")

    @web.middleware
    async def auth(request, handler):
        if reset_pending() and proxied(request) and request.path.startswith("/api/") and request.path != "/api/health":
            # fără parolă, nimeni din internet nu trebuie să poată alege una: doar din rețeaua de acasă
            return _json({"error": "Parola a fost ștearsă de pe card. Deschide Amper din rețeaua de acasă "
                                   "(adresa http://…:8080) și alege o parolă nouă.", "reset_local": True}, 403)
        if auth_.enabled and request.path.startswith("/api/") and request.path not in ("/api/login", "/api/health") \
                and not local(request):
            if not auth_.valid_cookie(request.cookies.get("amper")):
                return _json({"error": "autentificare necesară"}, 401)
        return await handler(request)

    @web.middleware
    async def pro_only(request, handler):
        if ed["v"] != "pro" and PRO_ONLY.match(request.path):
            return _json({"error": "Disponibil doar în Amper Pro (Setări → Despre → Activează Pro).",
                          "pro_required": True}, 403)
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
        elif "Cache-Control" not in resp.headers and not request.path.startswith("/fonts/"):
            # fișierele aplicației: telefonul verifică mereu dacă s-au schimbat (altfel Safari păstrează un timp
            # app.js vechi după o actualizare); fonturile nu se schimbă
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app = web.Application(middlewares=[headers, auth, pro_only, no_cache_api])
    if reset_pending():
        engine.notifier.send("🔐 Parola aplicației a fost ștearsă cu fișierul de pe card. Deschide Amper din rețeaua "
                             "de acasă și alege una nouă.", title="Amper: securitate", priority="high")
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
        # app.js / app.css cu versiunea în adresă: după o actualizare, telefonul nu poate folosi fișierele vechi
        v = app_version()
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace('href="app.css"', f'href="app.css?v={v}"').replace('src="app.js"', f'src="app.js?v={v}"')
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

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
                     kind=engine.devices[d["id"]].driver.kind, capacity_ah=d.get("capacity_ah"),
                     port=d.get("port"), ble=d.get("ble"), address=d.get("address"), baudrate=d.get("baudrate"))
                for d in cfg["devices"]]
        wp = engine.notifier.webpush
        return _json(dict(devices=devs, auth=auth_.enabled, poll_interval=cfg["poll_interval"], version=__version__,
                          port=int((cfg.get("web") or {}).get("port") or 8080),
                          site_name=cfg.get("site_name") or "", setup_needed=setup_needed(cfg),
                          password_reset=reset_pending(), edition=ed["v"], pi_id=pid,
                          public_url=wp.subject if wp else None,
                          notifications=engine.notifier.enabled,
                          simulated=any(d["type"].startswith("sim_") for d in cfg["devices"])))

    async def pro_activate(request):
        """Activează Pro cu codul primit de la autor (semnat pentru ID-ul acestui Pi). Cere parola aplicației."""
        body = await request.json()
        if (err := await confirm(request, body, "activare Pro")) is not None:
            return err
        code = "".join(str(body.get("code") or "").split())
        if not edition_.code_ok(code, pid):
            return _json({"error": f"Codul nu e valid pentru acest Pi (ID {pid}). Verifică să fie copiat întreg."}, 400)
        from .config import save_overlay
        await asyncio.to_thread(save_overlay, cfg, {"pro_code": code})
        cfg["pro_code"] = code
        ed["v"] = "pro"
        log.warning("Amper Pro activat (ID %s, de la %s)", pid, client_ip(request))
        engine.notifier.send("⭐ Amper Pro a fost activat pe acest Pi.", title="Amper", priority="default")
        return _json({"ok": True, "edition": "pro"})

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

    async def history_many(request):
        """Toate graficele din Istoric într-o singură cerere (prin internet contează numărul de cereri)."""
        ids = [d for d in request.query.get("devices", "").split(",") if d in engine.devices][:20]
        hours = min(float(request.query.get("hours", 24)), 24 * 400)
        points = min(int(request.query.get("points", 300)), 1000)
        end = int(time.time())
        start = end - int(hours * 3600)

        t0 = time.monotonic()

        def read():
            out = {}
            for d in ids:
                kind = engine.devices[d].driver.kind
                try:
                    out[d] = dict(kind=kind, rows=engine.db.history(kind, d, start, end, points, cfg["raw_retention_days"]))
                except Exception as e:                       # un dispozitiv cu probleme nu strică restul
                    log.warning("istoric %s: %s", d, e)
                    out[d] = dict(kind=kind, rows=[], error=str(e))
            return out
        series = await asyncio.to_thread(read)
        ms = int((time.monotonic() - t0) * 1000)
        if ms > 1500:
            log.warning("istoricul (%s h, %d dispozitive) a durat %.1f s", hours, len(ids), ms / 1000)
        return _json(dict(start=start, end=end, series=series, ms=ms))

    async def energy(request):
        inv = [k for k, v in engine.devices.items() if v.driver.kind == "inverter"]
        if not inv:
            return _json(dict(rows=[]))
        start, end = request.query.get("from"), request.query.get("to")
        if start and end:                     # interval ales în aplicație (AAAA-LL-ZZ)
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end)):
                return _json({"error": "date invalide"}, 400)
            parts = [await asyncio.to_thread(engine.db.energy_range, k, min(start, end), max(start, end)) for k in inv]
        else:
            parts = [await asyncio.to_thread(engine.db.energy, k, int(request.query.get("days", 30))) for k in inv]
        return _json(dict(rows=sum_energy(parts)))

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

    NO_SETTINGS = ("Pentru acest invertor Amper deocamdată doar citește valorile. Setările vor veni după ce citirea e "
                   "verificată pe un aparat real.")

    def inverter_ids() -> list[str]:
        return [k for k, st in engine.devices.items() if st.driver.kind == "inverter"]

    def inv_label(dev: str) -> str:
        """Cu mai multe invertoare: „Invertor 1”, „Invertor 2”… (ordinea din config, ca taburile din aplicație)."""
        ids = inverter_ids()
        if len(ids) > 1 and dev in ids:
            return f"Invertor {ids.index(dev) + 1}"
        return engine.devices[dev].cfg.get("name", dev) if dev in engine.devices else "Invertor"

    def settings_driver(dev_id: str | None = None):
        """Invertorul cu setări: cel cerut (mai multe în paralel: fiecare cu setările lui) sau primul."""
        for k, st in engine.devices.items():
            if st.driver.kind == "inverter" and hasattr(st.driver, "read_settings") and (not dev_id or k == dev_id):
                return k, st.driver
        return None, None

    def common_drivers():
        """„Setări comune”: toate invertoarele, doar dacă au toate setări și sunt de același tip (ex. două Growatt SPF)."""
        devs = [(k, engine.devices[k].driver) for k in inverter_ids()]
        if len(devs) < 2:
            return None, "Setările comune apar doar cu cel puțin două invertoare."
        if not all(hasattr(d, "read_settings") for _, d in devs) or len({type(d) for _, d in devs}) > 1:
            return None, ("Setările comune se pot schimba doar când toate invertoarele sunt de același tip și Amper le "
                          "știe setările (ex. Growatt SPF). Schimbă setările din tabul fiecărui invertor.")
        return devs, None

    def combine_settings(per: list[tuple[str, dict]]) -> list[dict]:
        """Setările primului invertor + valorile fiecăruia; unde diferă, `same` = False și textul „diferite”."""
        lists = [(dev, invset.describe(regs)) for dev, regs in per]
        out = []
        for i, item in enumerate(lists[0][1]):
            vals = [dict(device=dev, name=inv_label(dev), text=lst[i]["text"], raw=lst[i]["raw"]) for dev, lst in lists]
            same = len({v["raw"] for v in vals}) == 1
            x = dict(item, values=vals, same=same)
            if not same:
                x.update(text="diferite", raw=None, value=None)
            out.append(x)
        return out

    def combine_clock(per: list[tuple[str, dict]]) -> str:
        return " · ".join(f"{inv_label(dev)}: {invset.clock(regs) or '–'}" for dev, regs in per)

    async def inverter_get(request):
        want = request.query.get("device") or None
        if want == "_common":
            devs, why = common_drivers()
            if not devs:
                return _json({"error": why, "no_settings": True}, 404)
            per = []
            for dev, drv in devs:
                try:
                    per.append((dev, await asyncio.to_thread(drv.read_settings)))
                except (DriverError, OSError) as e:
                    return _json({"error": f"{inv_label(dev)}: nu am putut citi setările: {e}"}, 502)
            logs = []
            for dev, _ in devs:
                logs += [dict(r, setting=f"{inv_label(dev)} · {r['setting']}")
                         for r in await asyncio.to_thread(engine.db.settings_log, dev, 20)]
            logs.sort(key=lambda r: r["ts"], reverse=True)
            return _json(dict(device="_common", common=True, settings=combine_settings(per), clock=combine_clock(per),
                              pi_clock=time.strftime("%Y-%m-%d %H:%M:%S"), log=logs[:20], can_write=auth_.enabled))
        dev, drv = settings_driver(want)
        if not drv:
            return _json({"error": NO_SETTINGS, "no_settings": True}, 404)
        try:
            regs = await asyncio.to_thread(drv.read_settings)
        except (DriverError, OSError) as e:
            return _json({"error": f"nu am putut citi setările: {e}"}, 502)
        log_rows = await asyncio.to_thread(engine.db.settings_log, dev, 20)
        return _json(dict(device=dev, settings=invset.describe(regs), clock=invset.clock(regs),
                          pi_clock=time.strftime("%Y-%m-%d %H:%M:%S"), log=log_rows,
                          can_write=auth_.enabled))

    async def write_one(request, dev, drv, fn, what) -> dict:
        """O scriere într-un invertor: jurnal + notificare. Întoarce rezultatul sau {error} (ValueError trece mai departe)."""
        try:
            res = await asyncio.to_thread(fn, drv)
        except (DriverError, OSError) as e:
            await asyncio.to_thread(engine.db.log_setting, int(time.time()), dev, what, None, None, False,
                                    str(e), request.remote)
            log.warning("setare invertor %s (%s): EȘUAT: %s", what, dev, e)
            return {"error": str(e)}
        if res["changed"]:
            await asyncio.to_thread(engine.db.log_setting, int(time.time()), dev, res["label"], res["old"],
                                    res["new"], True, None, request.remote)
            log.warning("setare invertor schimbată (%s): %s: %s → %s (de la %s)", dev, res["label"], res["old"],
                        res["new"], request.remote)
            engine.notifier.send(f"⚙️ {inv_label(dev)}: {res['label']}: {res['old']} → {res['new']}",
                                 title="Amper: setare schimbată", priority="default")
        return res

    async def inverter_write(request, body, fn, what):
        # scrierea cere ÎNTOTDEAUNA parola din nou, și de pe ecranul HDMI (localhost)
        if (err := await confirm(request, body, "setare invertor")) is not None:
            return err
        if body.get("device") == "_common":
            devs, why = common_drivers()
            if not devs:
                return _json({"error": why, "no_settings": True}, 404)
        else:
            dev, drv = settings_driver(body.get("device") or None)
            if not drv:
                return _json({"error": NO_SETTINGS, "no_settings": True}, 404)
            devs = [(dev, drv)]
        results = []
        async with write_lock:
            for dev, drv in devs:                         # pe rând, în fiecare; un eșec nu le oprește pe celelalte
                try:
                    results.append((dev, await write_one(request, dev, drv, fn, what)))
                except ValueError as e:
                    return _json({"error": str(e)}, 400)
        bad = [(dev, r) for dev, r in results if "error" in r]
        good = [(dev, r) for dev, r in results if "error" not in r]
        if len(devs) == 1:
            res = results[0][1]
            if bad:
                return _json({"error": res["error"]}, 502)
            return _json(dict(ok=True, changed=res["changed"], label=res["label"], old=res["old"], new=res["new"],
                              settings=invset.describe(res["regs"]), clock=invset.clock(res["regs"])))
        if bad:
            msg = "; ".join(f"EȘUAT la {inv_label(dev)}: {r['error']}" for dev, r in bad)
            if good:
                msg = f"Scris în {', '.join(inv_label(d) for d, _ in good)}. {msg}. Invertoarele pot avea acum valori diferite."
            return _json({"error": msg, "partial": bool(good)}, 502)
        per = [(dev, r["regs"]) for dev, r in good]
        first = good[0][1]
        return _json(dict(ok=True, changed=any(r["changed"] for _, r in good), label=first["label"],
                          settings=combine_settings(per), clock=combine_clock(per),
                          results=[dict(name=inv_label(d), old=r["old"], new=r["new"]) for d, r in good]))

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

    # ---------------- acces de oriunde: Tailscale + Funnel (prin amper-update, root) ----------------
    def remote_seen(data: dict) -> dict:
        # Pi-ul are acum o adresă Tailscale: notificările pe iPhone o folosesc imediat (Apple cere adresa reală)
        wp = engine.notifier.webpush
        if data.get("url") and wp and not (cfg.get("web") or {}).get("public_url") and wp.subject != data["url"]:
            wp.subject = data["url"]
            log.info("adresa pentru notificări: %s", data["url"])
        return data

    async def remote_get(request):
        data, status = await update_api("GET", "/remote")
        return _json(remote_seen(data), status)

    async def remote_login(request):
        body = await request.json() if request.can_read_body else {}
        if auth_.enabled and (err := await confirm(request, body, "conectare la server")) is not None:
            return err
        data, status = await update_api("POST", "/remote/login")
        return _json(remote_seen(data), status)

    async def remote_funnel(request):
        # deschide/închide adresa publică: cere parola din nou
        body = await request.json()
        if (err := await confirm(request, body, "acces de oriunde")) is not None:
            return err
        on = bool(body.get("on"))
        data, status = await update_api("POST", "/remote/funnel", {"on": on})
        if status == 200:
            log.warning("acces de oriunde (Funnel) %s din aplicație (de la %s)", "pornit" if on else "oprit",
                        client_ip(request))
            engine.notifier.send(f"🌍 Accesul de oriunde a fost {'pornit' if on else 'oprit'} din aplicație.",
                                 title="Amper: securitate", priority="high")
        return _json(remote_seen(data), status)

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
        if cfg.get("password_reset"):
            await asyncio.to_thread(save_overlay, cfg, {"password_reset": None})
            cfg.pop("password_reset", None)
        engine.notifier.send(f"🔐 Parola aplicației a fost schimbată (de la {client_ip(request)}).",
                             title="Amper: securitate", priority="high")
        resp = _json({"ok": True})
        set_cookies(request, resp, quiet=True)          # browserul care a pus parola rămâne logat
        return resp

    async def setup_catalog(request):
        from .catalog import CATALOG, LINK_TEXT
        return _json(dict(catalog=CATALOG, links=LINK_TEXT))

    async def setup_scan_start(request):
        if scan_job.get("state") == "running":
            return _json(scan_job)
        try:
            body = await request.json()
        except Exception:
            body = {}
        model, link = body.get("model") or None, body.get("link") or None
        try:
            address = int(body["address"]) if body.get("address") not in (None, "") else None
        except (TypeError, ValueError):
            return _json({"error": "Adresa trebuie să fie un număr (0–15)."}, 400)
        scan_job.clear()
        scan_job.update(state="running", message="Pornesc căutarea…", started=time.time())

        def progress(msg):
            scan_job["message"] = msg

        async def run():
            try:
                res = await asyncio.to_thread(setup_scan.scan, cfg, progress, 8, model, link, address)
                scan_job.update(state="done", message="", model=model, link=link, **res)
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

    # ---------------- „Adaugă dispozitiv” (Setări → Dispozitive conectate → +) ----------------
    dev_job: dict = {"state": "idle"}

    async def devices_scan_start(request):
        body = await request.json()
        # „Adaugă dispozitiv”: mode=auto (cablu + Bluetooth), model ales din listă (catalog), sau vechiul what=cable|ble
        mode = "auto" if body.get("mode") == "auto" else "model" if body.get("model") else None
        what = "ble" if body.get("what") == "ble" else "cable"
        try:
            address = int(body["address"]) if body.get("address") not in (None, "") else None
        except (TypeError, ValueError):
            return _json({"error": "Adresa trebuie să fie un număr (0–15)."}, 400)
        if dev_job.get("state") == "running":
            return _json(dev_job)
        dev_job.clear()
        dev_job.update(state="running", what=mode or what, started=time.time(),
                       message="Caut prin Bluetooth (10 s)…" if (mode, what) == (None, "ble") else "Caut…")

        def progress(msg):
            dev_job["message"] = msg

        async def run():
            try:
                if mode == "auto":
                    def fn():
                        res = setup_scan.scan_cable(cfg, progress)
                        progress("Caut prin Bluetooth (10 s)…")
                        try:
                            ble_res = setup_scan.scan_ble(cfg)
                            res.update(ble=ble_res["found"], types=ble_res["types"])
                        except Exception as e:
                            res.update(ble=[], ble_note=f"Bluetooth indisponibil ({e.__class__.__name__})")
                        return res
                elif mode == "model":
                    def fn():
                        return setup_scan.scan(cfg, progress, 10, body["model"], body.get("link"), address)
                else:
                    fn = (lambda: setup_scan.scan_ble(cfg)) if what == "ble" else (lambda: setup_scan.scan_cable(cfg, progress))
                dev_job.update(state="done", message="", **await asyncio.to_thread(fn))
            except Exception as e:
                log.warning("căutare %s: %s", what, e)
                dev_job.update(state="failed", message=("Bluetooth indisponibil: " if what == "ble" else "Căutarea a eșuat: ") + str(e))
        asyncio.get_running_loop().create_task(run())
        return _json(dev_job)

    async def devices_scan_get(request):
        return _json(dev_job)

    async def devices_add(request):
        body = await request.json()
        real = [dict(d) for d in cfg["devices"] if not d["type"].startswith("sim_")]
        try:
            dev = setup_scan.add_device(real, dict(body.get("device") or {}))
            validate({**cfg, "devices": real + [dev]})
        except (ValueError, TypeError, ConfigError) as e:
            return _json({"error": str(e)}, 400)
        await asyncio.to_thread(save_overlay, cfg, {"devices": real + [dev]})
        sim = any(d["type"].startswith("sim_") for d in cfg["devices"])
        log.warning("dispozitiv adăugat din aplicație: %s (%s)", dev["id"], dev.get("ble") or dev.get("port"))
        engine.notifier.send(f"➕ Dispozitiv adăugat: {dev['name']}.", title="Amper")
        if not sim:
            restart_soon()                                  # se citește după repornire (câteva secunde)
        return _json({"ok": True, "device": dev, "restart": not sim})

    # ---------------- Deconectează / module excluse / ce a găsit căutarea ----------------
    async def device_disconnect(request):
        dev = request.match_info["device"]
        st = engine.devices.get(dev)
        if not st or st.driver.kind != "pack":
            return _json({"error": "BMS necunoscut"}, 404)
        body = await request.json()
        if (err := await confirm(request, body, f"deconectare {dev}")) is not None:
            return err
        res = engine.disconnect_device(dev)
        if res["devices"] is not None:                  # config schimbat (Seplos scos, Daly fix → poziție liberă)
            await asyncio.to_thread(save_overlay, cfg, {"devices": [d for d in res["devices"]
                                                                     if not d["type"].startswith("sim_")] or res["devices"]})
        return _json({"ok": True, **{k: v for k, v in res.items() if k != "devices"}})

    async def excluded_get(request):
        from .drivers import daly_auto
        return _json({"items": daly_auto.load_excluded()})

    async def excluded_allow(request):
        from .drivers import daly_auto
        body = await request.json()
        idx = body.get("index")
        items = await asyncio.to_thread(daly_auto.allow_again, None if idx is None else int(idx))
        log.warning("module excluse: permise din nou (%s)", "toate" if idx is None else f"#{idx}")
        return _json({"items": items})

    async def daly_found(request):
        """Ce a încercat căutarea automată (legate sau respinse), cu valorile citite, plus pozițiile Daly."""
        from .drivers import daly_auto
        pos = [{"id": k, "name": st.cfg.get("name", k), "free": st.driver.inner is None, "link": st.driver.link_desc,
                "state": st.driver.state}
               for k, st in engine.devices.items() if isinstance(st.driver, daly_auto.DalyAutoDriver)]
        return _json({"found": daly_auto.recent_found(), "positions": pos})

    async def daly_reject(request):
        """Respinge un modul găsit (ex. un egalizator activ): pe lista de excluse; dacă era legat, poziția se eliberează."""
        from .drivers import daly_auto
        body = await request.json()
        if (err := await confirm(request, body, "respingere modul")) is not None:
            return err
        item = next((x for x in daly_auto.recent_found() if x["keys"] == body.get("keys")), None)
        if not item and isinstance(body.get("binding"), dict) and (body["binding"].get("ble") or body["binding"].get("port")):
            item = {"binding": body["binding"], "label": str(body.get("label") or "")[:60], "device": None}   # din ghid
        if not item:
            return _json({"error": "Modulul nu mai e în listă; caută din nou."}, 404)
        if item["device"] and item["device"] in engine.devices and \
                getattr(engine.devices[item["device"]].driver, "binding", None) is not None:
            engine.disconnect_device(item["device"], "respins din aplicație (nu e BMS)")
        else:
            daly_auto.exclude(item["binding"], item["label"], "respins din aplicație (nu e BMS)")
        return _json({"ok": True})

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

    # ---------------- comenzile BMS Daly (MOSFET-uri, repornire, SOC) ----------------
    from . import daly_actions
    support = daly_actions.Support(engine.db)
    limits_cache: dict[str, tuple[float, dict]] = {}

    def other_limits(dev_id: str) -> dict:
        """Limitele de curent (0x5B, nivel 1) ale celorlalte Daly, citite cel mult o dată pe oră. Se apelează cu
        daly_lock luat (citește din BMS)."""
        out = {}
        for k in engine.devices:
            if k == dev_id:
                continue
            hit = limits_cache.get(k)
            if hit and time.time() - hit[0] < 3600:
                out[k] = hit[1]
                continue
            _, d = daly_driver(k)
            if not d:
                continue
            try:
                lim = daly_actions.limits(d)
            except (DriverError, OSError):
                continue
            limits_cache[k] = (time.time(), lim)
            out[k] = lim
        return out

    def restarting_ids() -> set[str]:
        now = time.time()
        return {k for k, s_ in engine.devices.items() if s_.restart_until and now < s_.restart_until}

    async def daly_actions_get(request):
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        dev = st.cfg["id"]
        async with daly_lock:
            skey = await asyncio.to_thread(daly_actions.support_key, drv)
            lims = await asyncio.to_thread(other_limits, dev)
        snap = engine.snapshot()
        hidden = support.unsupported(skey)
        r = st.reading
        acts = [dict(key=a["key"], label=a["label"], kind=a["kind"], mos=a.get("mos"), want=a.get("want"),
                     risk=a["risk"], warn=a["warn"], supported=a["key"] not in hidden,
                     refused=daly_actions.precheck(a["key"], dev, snap, lims, restarting_ids()),
                     can_force=a["kind"] == "mos")
                for a in daly_actions.ACTIONS]
        return _json(dict(device=dev, firmware=skey, actions=acts, unsupported=sorted(hidden),
                          mos=dict(chg=getattr(r, "charge_mos", None), dsg=getattr(r, "discharge_mos", None)),
                          soc=getattr(r, "soc", None),
                          suggest=daly_actions.soc_from_voltage(getattr(r, "cells", None) or [],
                                                                getattr(r, "current", None)),
                          restarting=dev in restarting_ids(), forced=st.forced, can_write=auth_.enabled))

    async def daly_action(request):
        body = await request.json()
        if (err := await confirm(request, body, "comandă BMS")) is not None:
            return err
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        key, dev, name = str(body.get("action")), st.cfg["id"], st.cfg.get("name", st.cfg["id"])
        act = daly_actions.BY_KEY.get(key)
        if not act:
            return _json({"error": "comandă necunoscută"}, 400)
        value = None
        if act["kind"] == "soc":
            try:
                value = float(body.get("value"))
                daly_actions.payload(key, value)
            except (TypeError, ValueError):
                return _json({"error": "SOC-ul trebuie să fie un număr între 0 și 100 %"}, 400)
        if daly_lock.locked():
            return _json({"error": "Se scrie acum o setare sau rulează altă comandă BMS. Încearcă peste câteva secunde."},
                         409)
        async with daly_lock:
            skey = await asyncio.to_thread(daly_actions.support_key, drv)
            if key in support.unsupported(skey):
                return _json({"error": "Comanda e marcată nesuportată la acest BMS (Încearcă din nou o reactivează)."},
                             409)
            lims = await asyncio.to_thread(other_limits, dev)
            why = daly_actions.precheck(key, dev, engine.snapshot(), lims, restarting_ids())
            # MOSFET-urile se pot opri și peste avertisment (ex. intervenție la conexiunile unui modul), dar doar
            # cu `force` trimis explicit după ce utilizatorul a văzut motivul; repornirea rămâne blocată
            forced = bool(why) and act["kind"] == "mos" and bool(body.get("force"))
            if why and not forced:
                return _json({"error": why, "refused": True, "can_force": act["kind"] == "mos"}, 409)
            if act["kind"] == "restart":
                st.restart_until = time.time() + 120
            try:
                res = await asyncio.to_thread(daly_actions.execute, drv, key, value)
            except (DriverError, OSError) as e:
                res = dict(ok=False, label=act["label"], old=None, new=None, error=str(e), support_fail=False,
                           critical=False)
            finally:
                st.restart_until = None
        if res["ok"] and act["kind"] == "mos":
            engine.set_forced(dev, act["mos"], off=not act["want"])
        now_unsup = support.record(skey, key, res)
        what = res["label"] + (f" la {value:g} %" if value is not None else "")
        if forced:
            what += f" (forțat, deși: {why})"
        await asyncio.to_thread(engine.db.log_setting, int(time.time()), dev, f"comandă: {what}", res["old"],
                                res["new"], res["ok"], res["error"], client_ip(request))
        log.warning("comandă BMS %s: %s: %s (de la %s)", name, what, "ok" if res["ok"] else res["error"],
                    client_ip(request))
        if res["ok"]:
            engine.notifier.send(f"⚙️ {name}: {what}" + (f" ({res['old']} → {res['new']})" if res["old"] else ""),
                                 title="Amper: comandă BMS", priority="default")
        elif res.get("critical"):
            engine.notifier.send(f"🔴 {name}: {what} — {res['error']}", title="Amper: alertă", priority="high")
        else:
            engine.notifier.send(f"⚙️ {name}: {what} — EȘUAT: {res['error']}"
                                 + (" Comanda e marcată nesuportată la acest BMS." if now_unsup else ""),
                                 title="Amper: comandă BMS", priority="default")
        engine._push(engine.snapshot())
        return _json(dict(ok=res["ok"], error=res["error"], old=res["old"], new=res["new"], seconds=res.get("seconds"),
                          unsupported=now_unsup), 200 if res["ok"] else 502)

    async def daly_actions_retry(request):
        body = await request.json()
        st, drv = daly_driver(request.match_info["device"])
        if not drv:
            return _json({"error": "BMS-ul nu e legat"}, 404)
        async with daly_lock:
            skey = await asyncio.to_thread(daly_actions.support_key, drv)
        support.reset(skey, body.get("action") or None)
        return _json(dict(ok=True))

    # ---------------- sănătatea celulelor (ambele ediții) ----------------
    from . import cell_health

    async def cells_health(request):
        res = await asyncio.to_thread(cell_health.analyze, engine.db, engine.device_names())
        last = await asyncio.to_thread(engine.db.get_meta, cell_health.META)
        res["last_weekly"] = int(float(last)) if last else None
        res["tracked"] = sum(len(st.reading.cells or []) for st in engine.devices.values()
                             if isinstance(st.reading, PackReading) and st.connected)
        return _json(res)

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
    app.router.add_post("/api/pro/activate", pro_activate)
    app.router.add_get("/api/live", live)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/history/all", history_many)
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
    app.router.add_get("/api/daly/{device}/actions", daly_actions_get)
    app.router.add_post("/api/daly/{device}/action", daly_action)
    app.router.add_post("/api/daly/{device}/actions/retry", daly_actions_retry)
    app.router.add_get("/api/system", system_info)
    app.router.add_get("/api/cells/health", cells_health)
    app.router.add_get("/api/wifi/scan", wifi_scan)
    app.router.add_get("/api/wifi/status", wifi_status)
    app.router.add_post("/api/wifi/connect", wifi_connect)
    app.router.add_get("/api/setup", setup_get)
    app.router.add_post("/api/setup/name", setup_name)
    app.router.add_post("/api/setup/password", setup_password)
    app.router.add_get("/api/setup/catalog", setup_catalog)
    app.router.add_post("/api/setup/scan", setup_scan_start)
    app.router.add_get("/api/setup/scan", setup_scan_get)
    app.router.add_post("/api/setup/devices", setup_devices)
    app.router.add_post("/api/setup/finish", setup_finish)
    app.router.add_post("/api/devices/scan", devices_scan_start)
    app.router.add_get("/api/devices/scan", devices_scan_get)
    app.router.add_post("/api/devices/add", devices_add)
    app.router.add_post("/api/devices/{device}/disconnect", device_disconnect)
    app.router.add_get("/api/devices/excluded", excluded_get)
    app.router.add_post("/api/devices/excluded/allow", excluded_allow)
    app.router.add_get("/api/daly/found", daly_found)
    app.router.add_post("/api/daly/reject", daly_reject)
    app.router.add_get("/api/security", security_get)
    app.router.add_post("/api/update/channel", update_channel)
    app.router.add_get("/api/update/status", update_status)
    app.router.add_post("/api/update/check", update_check)
    app.router.add_post("/api/update/download", update_download)
    app.router.add_get("/api/remote", remote_get)
    app.router.add_post("/api/remote/login", remote_login)
    app.router.add_post("/api/remote/funnel", remote_funnel)
    app.router.add_post("/api/update/{action:install|rollback}", update_action)
    app.router.add_get("/api/push/key", push_key)
    app.router.add_post("/api/push/subscribe", push_subscribe)
    app.router.add_post("/api/push/unsubscribe", push_unsubscribe)
    app.router.add_get("/api/ws", ws)
    app.router.add_static("/", STATIC, show_index=False)
    return app

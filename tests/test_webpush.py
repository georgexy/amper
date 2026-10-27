"""Web Push: criptarea (decriptată ca un telefon, RFC 8291), semnătura VAPID, canalele de notificare, parola prin https."""
import asyncio
import json
import os
import struct

from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from amper import webpush
from amper.notify import Notifier


def _h(key, data):
    m = hmac.HMAC(key, hashes.SHA256())
    m.update(data)
    return m.finalize()


def phone_decrypt(body: bytes, ua_priv, auth: bytes) -> bytes:
    """Ce face telefonul (partea „user agent” din RFC 8291)."""
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    ua_pub = webpush._pub_bytes(ua_priv.public_key())
    ecdh = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = _h(_h(auth, ecdh), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    prk = _h(salt, ikm)
    cek = _h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    assert pt.endswith(b"\x02")
    return pt[:-1]


def test_encrypt_roundtrip_like_a_phone():
    ua_priv = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    msg = json.dumps({"title": "Amper", "body": "Seplos 2: PROTECȚIE BMS"}, ensure_ascii=False).encode()
    body = webpush.encrypt(msg, webpush.b64u(webpush._pub_bytes(ua_priv.public_key())), webpush.b64u(auth))
    assert phone_decrypt(body, ua_priv, auth) == msg


def test_vapid_header_is_valid_es256(tmp_path):
    v = webpush.Vapid(str(tmp_path / "vapid.json"))
    hdr = v.header("https://web.push.apple.com/QGuQyavXutnMH", "https://amper.local")
    t = hdr.split("t=")[1].split(",")[0]
    k = hdr.split("k=")[1]
    head, claims, sig = t.split(".")
    c = json.loads(webpush.unb64u(claims))
    assert c["aud"] == "https://web.push.apple.com" and c["sub"] == "https://amper.local"
    raw = webpush.unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert webpush.Vapid(str(tmp_path / "vapid.json")).public_b64 == v.public_b64      # cheile rămân aceleași


def test_failed_channel_is_retried_alone():
    n = Notifier({"ntfy": {"enabled": True, "topic": "t"}})
    calls = {"webpush": 0, "ntfy": 0}

    class WP:
        enabled = True

        async def send(self, http, item):
            calls["webpush"] += 1
    n.webpush = WP()

    async def ntfy(http, item):
        calls["ntfy"] += 1
        if calls["ntfy"] == 1:
            raise RuntimeError("fără internet")
    n._ntfy = ntfy
    item = {"message": "m", "title": "t", "priority": "high"}

    async def go():
        try:
            await n._deliver(None, item)
        except RuntimeError:
            pass
        await n._deliver(None, item)
    asyncio.run(go())
    assert calls == {"webpush": 1, "ntfy": 2}           # telefonul nu primește mesajul de două ori


def test_password_required_through_tailscale_https_proxy():
    from aiohttp.test_utils import TestClient, TestServer

    from amper.config import DEFAULTS, _merge
    from amper.db import Database
    from amper.drivers.simulator import SimWorld
    from amper.engine import Engine
    from amper.web import make_app
    SimWorld._instance = None
    cfg = _merge(DEFAULTS, {"database": ":memory:", "web": {"password": "pw"},
                            "devices": [{"id": "inv", "type": "sim_growatt"}]})
    eng = Engine(cfg, Database(":memory:"), Notifier({}))

    async def go():
        async with TestClient(TestServer(make_app(eng))) as c:
            kiosk = await c.get("/api/info")                                     # direct de pe Pi (HDMI)
            phone = await c.get("/api/info", headers={"X-Forwarded-For": "100.70.157.126",
                                                       "Tailscale-User-Login": "utilizator"})
            return kiosk.status, phone.status
    assert asyncio.run(go()) == (200, 401)


def test_notification_shows_short_text_details_in_app(tmp_path, monkeypatch):
    """Pe telefon: doar textul principal; ce e în paranteze se vede în aplicație, la Alerte."""
    from amper.notify import split_detail
    assert split_detail("Daly 2: BMS-ul a OPRIT încărcarea (MOSFET încărcare OFF)") == \
        ("Daly 2: BMS-ul a OPRIT încărcarea", "MOSFET încărcare OFF")
    assert split_detail("Daly 1 NU se încarcă (curent 0,1 A, SOC 50%) în timp ce restul băncii se încarcă") == \
        ("Daly 1 NU se încarcă în timp ce restul băncii se încarcă", "curent 0,1 A, SOC 50%")
    assert split_detail("Daly 3: nu mai comunică de 3 min (timeout (2 s))") == ("Daly 3: nu mai comunică de 3 min",
                                                                                 "timeout (2 s)")
    assert split_detail("Daly 1: SOC scăzut 20%") == ("Daly 1: SOC scăzut 20%", "")
    assert split_detail("ceva (neînchis") == ("ceva (neînchis", "")

    from amper.db import Database
    db = Database(":memory:")
    db.add_push("https://push.example/x", "k", "a")
    wp = webpush.WebPush(db, str(tmp_path), subject="https://amper.example")
    sent = []
    monkeypatch.setattr(webpush, "encrypt", lambda payload, p256dh, auth: sent.append(json.loads(payload)) or b"")

    class Resp:
        status = 201
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Http:
        def post(self, *a, **k): return Resp()
    asyncio.run(wp.send(Http(), {"title": "Amper", "priority": "high",
                                 "message": "Daly 2: BMS-ul a OPRIT încărcarea (MOSFET încărcare OFF)"}))
    assert sent == [{"title": "Amper", "body": "Daly 2: BMS-ul a OPRIT încărcarea", "priority": "high",
                     "url": "/?v=alerts"}]

"""Limba aplicației (Setări → Limbă): una pe Pi, salvată în settings.yaml; dicționarul englez din web/lang-en.js."""
import re
from pathlib import Path

from test_security_setup import run

WEB = Path(__file__).parent.parent / "web"


def test_language_is_saved_on_the_pi(tmp_path):
    async def steps(c, eng, sent):
        before = (await (await c.get("/api/info")).json())["lang"]
        bad = await c.post("/api/language", json={"lang": "fr"})
        ok = await c.post("/api/language", json={"lang": "en"})
        after = (await (await c.get("/api/info")).json())["lang"]
        return before, bad.status, ok.status, after
    cfg = {"settings_file": str(tmp_path / "settings.yaml")}
    before, bad, ok, after = run(cfg, steps, tmp_path)
    assert (before, bad, ok, after) == ("ro", 400, 200, "en")
    assert "language: en" in (tmp_path / "settings.yaml").read_text()


def _dict_keys():
    src = (WEB / "lang-en.js").read_text(encoding="utf-8")
    return {" ".join(k.split()) for k in re.findall(r'^\s*"((?:[^"\\]|\\.)*)":', src, re.M)} | \
           {" ".join(k.split()) for k in re.findall(r'[{,]\s*"((?:[^"\\]|\\.)*)":\s*"', src)}


def test_every_t_call_has_an_english_text():
    """Textele trimise prin t("…") / tf("…") (confirmări, mesaje) trebuie să aibă traducere în lang-en.js."""
    src = (WEB / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\("((?:[^"\\]|\\.)+)"\)', src)) | set(re.findall(r'\btf\("((?:[^"\\]|\\.)+)"', src))
    used |= {m for pair in re.findall(r'\bt\((?:[^()"]*\?\s*)?"((?:[^"\\]|\\.)+)"\s*:\s*"((?:[^"\\]|\\.)+)"\)', src) for m in pair}
    used |= {m for pair in re.findall(r'\btf\([^"]*\?\s*"((?:[^"\\]|\\.)+)"\s*:\s*"((?:[^"\\]|\\.)+)"', src) for m in pair}
    keys = _dict_keys()
    missing = sorted(u for u in used if " ".join(u.split()) not in keys)
    assert missing == []


def test_scripts_are_loaded_before_the_app():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index("lang-en.js") < html.index("i18n.js") < html.index('src="app.js"')
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    assert "/i18n.js" in sw and "/lang-en.js" in sw


# ---------------- etapa 2: textele scrise pe Pi (amper/i18n.py, amper/lang/en.json) ----------------
import json

import pytest

from amper import i18n


@pytest.fixture
def en():
    i18n.set_lang("en")
    i18n.load()
    i18n.set_names(["Daly 1", "Seplos 2"])
    yield i18n.tr
    i18n.set_lang("ro")
    i18n.set_names([])


def test_dictionary_is_valid_json():
    d = json.loads(i18n.DICT_PATH.read_text(encoding="utf-8"))
    assert len(d) > 500 and all(isinstance(v, str) for v in d.values())


def test_server_texts_in_english(en):
    assert en("Prea multe parole greșite. Încearcă din nou peste 30 s.") == "Too many wrong passwords. Try again in 30 s."
    assert en("⚙️ Daly 1: Oprește încărcarea — EȘUAT: BMS-ul nu a oprit încărcarea") == \
        "⚙️ Daly 1: Turn charging off — FAILED: The BMS did not turn charging off"
    assert en("✅ Amper actualizat la 1.0.0-beta.14.") == "✅ Amper updated to 1.0.0-beta.14."
    assert en("Mă conectez la „Casa”…") == "Connecting to “Casa”…"
    # ce nu e în dicționar rămâne cum e; în română nu se schimbă nimic
    assert en("ceva necunoscut") == "ceva necunoscut"
    assert i18n.tr("Parola nu e corectă.", "ro") == "Parola nu e corectă."


def test_report_translated(en):
    rep = i18n.report({"headline": "Parola nu e corectă.", "points": ["Notificare de test din Amper."], "x": "Parola nu e corectă."})
    assert rep["headline"] == "The password is not correct." and rep["points"] == ["Test notification from Amper."]
    assert rep["x"] == "Parola nu e corectă."


def test_api_errors_and_notifications_follow_the_pi_language(tmp_path):
    async def steps(c, eng, sent):
        await c.post("/api/language", json={"lang": "en"})
        bad = await (await c.post("/api/language", json={"lang": "fr"})).json()
        srv = await (await c.get("/lang-en-srv.js")).text()
        eng.notifier.send("Notificare de test din Amper.")
        await c.post("/api/language", json={"lang": "ro"})
        return bad, srv
    cfg = {"settings_file": str(tmp_path / "settings.yaml")}
    bad, srv = run(cfg, steps, tmp_path)
    assert bad["error"] == "unknown language"
    assert srv.startswith("Object.assign(EN, ") and "Too many wrong passwords" in srv

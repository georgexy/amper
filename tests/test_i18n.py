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

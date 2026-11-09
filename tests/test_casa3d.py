"""Tema „Casă 3D” (cerută 2026-11-07): modulul 3D împachetat și legătura lui cu aplicația."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_bundle_is_built_and_local():
    js = (ROOT / "web" / "casa3d.js").read_text(encoding="utf-8")
    assert "export{" in js.replace(" ", "") and "mount" in js and "supported" in js
    assert "Three.js Authors" in js                         # licența MIT a three.js rămâne în fișier
    assert "http://" not in js.split("@license")[0][:200] and len(js) < 1_000_000
    assert "https://cdn" not in js


def test_theme_is_registered_and_loaded_lazily():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'import("./casa3d.js")' in app and "casa: { name:" in app
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "casa3d.js" not in html                          # nu se încarcă la pornire, doar cu tema aleasă


def test_house_models_have_thumbnails_and_names():
    """Fiecare model de casă din scenă are nume în aplicație (și în engleză) și o miniatură."""
    import re
    root = Path(__file__).resolve().parent.parent
    scene = (root / "tools" / "casa3d" / "scene.js").read_text(encoding="utf-8")
    ids = re.findall(r'"(\w+)"', re.search(r"MODEL_IDS = \[(.*?)\]", scene).group(1))
    assert len(ids) >= 6 and ids[0] == "clasic"
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    en = (root / "web" / "lang-en.js").read_text(encoding="utf-8")
    block = re.search(r"const CASA_MODELS = \{(.*?)\n\};", app, re.S).group(1)
    for m in ids:
        assert re.search(rf"\n    {m}\(\) \{{", scene), f"modelul {m} lipsește din scenă"
        name = re.search(rf'{m}: \["([^"]+)", "([^"]+)"\]', block)
        assert name, f"modelul {m} nu are nume în app.js"
        assert f'"{name.group(1)}":' in en and f'"{name.group(2)}":' in en
        img = root / "web" / "casa" / f"{m}.jpg"
        assert img.exists() and 3000 < img.stat().st_size < 60000

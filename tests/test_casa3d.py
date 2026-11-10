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

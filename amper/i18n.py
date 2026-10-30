"""Limba textelor scrise pe Pi (notificări, alerte, rapoarte, mesajele BMS-urilor și ale invertorului).

Codul scrie textele în română. În engleză, `tr()` le caută în dicționarul `amper/lang/en.json` (același dicționar îl
folosește și aplicația, pentru textele primite de la Pi). Cheia e textul românesc, cu:
  „#” = un număr, „@” = numele unui dispozitiv (din config), „{}” = orice text dintr-o frază, „{...}” = orice text
  (și mai multe fraze); textul prins de „{}” / „{...}” e tradus și el;
valorile se pun la loc în aceeași ordine („@: SOC scăzut #%” → „@: low SOC #%”). Un mesaj compus care nu e întreg în
dicționar se traduce pe bucăți (la „: ”, „; ”, „ · ”, „ — ”, „. ”, apoi la „, ”). Ce nu e în dicționar rămâne în română.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LANGS = ("ro", "en")
_LANG = "ro"
_NAMES_RX: re.Pattern | None = None
_EXACT: dict[str, str] = {}
_PAT: dict[str, str] = {}
_WILD: list[tuple[str, str]] = []
_WILD_RX: dict[str, re.Pattern] = {}
NUM = re.compile(r"(?<![\w.,])[-−]?\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*")
SPLIT = re.compile(r"(: |; | · | — | → |\. |\n)")
COMMA = re.compile(r"(, )")
SEMI = re.compile(r"(; )")
DICT_PATH = Path(__file__).parent / "lang" / "en.json"


def norm(s: str) -> str:
    return " ".join(s.split())


def load() -> dict[str, str]:
    """Dicționarul englez (și pentru aplicație: web.py îl trimite ca /lang-en-srv.js)."""
    global _EXACT, _PAT, _WILD
    try:
        raw = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    d = {norm(k): v for k, v in raw.items() if not k.startswith("//")}
    _EXACT = {k: v for k, v in d.items() if not re.search(r"[#@]|\{\}|\{\.\.\.\}", k)}
    _PAT = {k: v for k, v in d.items() if re.search(r"[#@]", k) and not re.search(r"\{\}|\{\.\.\.\}", k)}
    _WILD = [(k, v) for k, v in d.items() if re.search(r"\{\}|\{\.\.\.\}", k)]
    _WILD_RX.clear()
    return d


def set_lang(lang: str | None) -> str:
    global _LANG
    _LANG = lang if lang in LANGS else "ro"
    if _LANG == "en" and not (_EXACT or _PAT or _WILD):
        load()
    return _LANG


def lang() -> str:
    return _LANG


def set_names(names) -> None:
    """Numele dispozitivelor (config) devin „@” în chei; cele lungi primele („Daly 10” înaintea „Daly 1”)."""
    global _NAMES_RX
    names = sorted({n for n in names if n and n.strip()}, key=len, reverse=True)
    _NAMES_RX = re.compile("|".join(re.escape(x) for x in names)) if names else None
    _WILD_RX.clear()


def _lookup(key: str, depth: int = 0) -> str | None:
    """Cheia are deja numerele „#” și numele „@”; întoarce traducerea tot cu „#”/„@”."""
    if key in _EXACT:
        return _EXACT[key]
    if key in _PAT:
        return _PAT[key]
    return None


TOKENS = re.compile(r"\{\.\.\.\}|\{\}|[#@]")


def _wild(n: str, depth: int) -> str | None:
    """Cheile cu „{}” / „{...}” (orice text, tradus și el), potrivite pe textul original: „#” = număr, „@” = nume."""
    for k, val in _WILD:
        rx = _WILD_RX.get(k)
        if rx is None:
            names = _NAMES_RX.pattern if _NAMES_RX else r"(?!x)x"
            grp = {"#": "(" + NUM.pattern + ")", "@": "(" + names + ")", "{}": r"((?:(?!; |\n).)*?)", "{...}": "(.*?)"}
            body, pos = "", 0
            for m in TOKENS.finditer(k):
                body += re.escape(k[pos:m.start()]) + grp[m.group(0)]
                pos = m.end()
            rx = _WILD_RX[k] = re.compile(body + re.escape(k[pos:]) + r"\Z")
        m = rx.match(n)
        if not m:
            continue
        got = [_num_en(g) if t == "#" else g if t == "@" else _tr(g, depth + 1)
               for t, g in zip(TOKENS.findall(k), m.groups())]
        types = TOKENS.findall(k)
        its = {t: iter([g for tt, g in zip(types, got) if tt == t]) for t in set(types)}
        return TOKENS.sub(lambda x: next(its.get(x.group(0), iter(())), x.group(0)), val)
    return None


def _num_en(v: str) -> str:
    return v.replace(",", ".") if re.fullmatch(r"[-−]?\d+,\d+", v) else v


def _one(s: str) -> str | None:
    n = norm(s)
    if not n or not re.search(r"[A-Za-zĂÂÎȘȚăâîșț]", n):
        return None
    if n in _EXACT:
        return _EXACT[n]
    names: list[str] = []
    key = n
    if _NAMES_RX:
        def keep(m):
            names.append(m.group(0))
            return "@"
        key = _NAMES_RX.sub(keep, key)
    nums = NUM.findall(key)
    key = NUM.sub("#", key)
    v = _lookup(key)
    if v is None:
        return None
    it_n, it_a = iter(nums), iter(names)
    v = re.sub("#", lambda m: _num_en(next(it_n, "#")), v)
    return re.sub("@", lambda m: next(it_a, "@"), v)


def _pieces(s: str, rx: re.Pattern, depth: int = 0) -> str | None:
    parts = rx.split(s)
    if len(parts) < 2:
        return None
    out = [p if i % 2 else (_one(p) or (_wild(p, depth + 1) if depth < 6 else None)
                            or (_pieces(p, COMMA, depth) if rx is SPLIT else None) or p) for i, p in enumerate(parts)]
    joined = "".join(out)
    return joined if joined != s else None


EMOJI = re.compile("^((?:[\u2600-\u27BF\U0001F300-\U0001FAFF\uFE0F\u200d]+\\s*)+)")


def _tr(s: str, depth: int = 0) -> str:
    n = norm(s)
    if not n:
        return s
    em = EMOJI.match(n)                           # „🔴 …”, „✅ Rezolvat: …”: simbolul rămâne, restul se traduce
    if em and depth == 0:
        rest = n[em.end():]
        out = _tr(rest, 1)
        return s if out == rest else s[:len(s) - len(s.lstrip())] + em.group(1) + out
    out = _one(n)
    if out is None and depth > 0 and "; " in n:      # o listă prinsă de „{...}”: fiecare element separat
        parts = SEMI.split(n)
        done = [p if i % 2 else _tr(p, depth + 1) for i, p in enumerate(parts)]
        out = "".join(done) if "".join(done) != n else None
    if out is None and depth < 6:
        out = _wild(n, depth)
    if out is None:
        out = _pieces(n, SPLIT, depth) or _pieces(n, COMMA, depth)
    if out is None:
        return s
    lead, tail = re.match(r"\s*", s).group(0), re.search(r"\s*$", s).group(0)
    return lead + out + tail


def tr(s, lang_: str | None = None):
    """Textul în limba Pi-ului (sau în `lang_`)."""
    if not isinstance(s, str) or (lang_ or _LANG) != "en" or not s:
        return s
    if not (_EXACT or _PAT or _WILD):
        load()
    return _tr(s)


def report(rep: dict) -> dict:
    """Textele unui raport (nopții / pe perioadă / sănătatea celulelor) în limba Pi-ului, frază cu frază."""
    if _LANG != "en":
        return rep
    for k in ("headline", "period", "bank", "watch", "note", "title", "text"):
        if isinstance(rep.get(k), str):
            rep[k] = tr(rep[k])
    for k in ("points", "commands"):
        if isinstance(rep.get(k), list):
            rep[k] = [tr(x) for x in rep[k]]
    for c in rep.get("cells") or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            c["text"] = tr(c["text"])
    return rep

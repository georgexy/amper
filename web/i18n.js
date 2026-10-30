/* Limba aplicației (una pentru tot Pi-ul, Setări → Limbă). Codul scrie textele în română; în engleză le traduce
   pagina însăși: fiecare text din pagină (și aria-label / title / placeholder) e căutat în dicționarul EN
   (lang-en.js). Cheile sunt textele românești cu spațiile normalizate; numerele din text se potrivesc cu „#”
   („Se încarcă cu # kW” → „Charging at # kW”), apoi se pun la loc în aceeași ordine. Numerele și datele sunt deja
   formatate după limbă (LOC), deci în engleză apar cu punct zecimal. Ce nu e în dicționar rămâne în română. */
"use strict";

const LANG = (() => { try { return localStorage.getItem("lang") === "en" ? "en" : "ro"; } catch { return "ro"; } })();
const LOC = LANG === "en" ? "en-GB" : "ro-RO";
document.documentElement.lang = LANG;

const I18N = (() => {
  const dict = new Map(), pat = new Map(), wild = [];
  let names = null, wildRx = new Map();              // numele dispozitivelor („@” în chei), cele lungi primele
  const SPLIT = /(: |; | · | — | → |\. |\n)/, COMMA = /(, )/;
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const NUM_SRC = "(?<![\\w.,])[-−]?\\d+(?:[.,]\\d+)*|\\d+(?:[.,]\\d+)*";
  const NUM = new RegExp(NUM_SRC, "g");               // „-5 W” → „# W”
  const TOK = /\{\.\.\.\}|\{\}|[#@]/g;                 // „{}” = orice text dintr-o frază, „{...}” = orice text
  const reEsc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (LANG === "en" && typeof EN === "object") {
    for (const [k0, v] of Object.entries(EN)) {
      const k = norm(k0);
      if (/\{\}|\{\.\.\.\}/.test(k)) wild.push([k, v]);
      else (/[#@]/.test(k) ? pat : dict).set(k, v);
    }
  }
  const numEn = (v) => (/^[-−]?\d+,\d+$/.test(v) ? v.replace(",", ".") : v);
  function one(n) {
    if (!n || !/[a-zăâîșțA-ZĂÂÎȘȚ]/.test(n)) return null;
    const ex = dict.get(n);
    if (ex != null) return ex;
    const found = [];
    let key = names ? n.replace(names, (m) => { found.push(m); return "@"; }) : n;
    const nums = key.match(NUM) || [];
    key = key.replace(NUM, "#");
    const v = dict.get(key) ?? pat.get(key);
    if (v == null) return null;
    let i = 0, j = 0;
    return v.replace(/#/g, () => numEn(nums[i++] ?? "#")).replace(/@/g, () => found[j++] ?? "@");
  }
  function wildOne(n, depth) {
    for (const [k, val] of wild) {
      let rx = wildRx.get(k);
      if (!rx) {
        const grp = { "#": `(${NUM_SRC})`, "@": `(${names ? names.source : "(?!x)x"})`, "{}": "((?:(?!; |\\n).)*?)", "{...}": "(.*?)" };
        let body = "", pos = 0;
        for (const m of k.matchAll(TOK)) { body += reEsc(k.slice(pos, m.index)) + grp[m[0]]; pos = m.index + m[0].length; }
        rx = new RegExp("^" + body + reEsc(k.slice(pos)) + "$");
        wildRx.set(k, rx);
      }
      const m = n.match(rx);
      if (!m) continue;
      const types = k.match(TOK), got = {};
      types.forEach((t, x) => { const g = m[x + 1] ?? ""; (got[t] ||= []).push(t === "#" ? numEn(g) : t === "@" ? g : trn(g, depth + 1)); });
      return val.replace(TOK, (t) => (got[t] && got[t].length ? got[t].shift() : t));
    }
    return null;
  }
  function pieces(n, rx, depth) {
    const parts = n.split(rx);
    if (parts.length < 2) return null;
    const done = parts.map((p, k) => (k % 2 ? p : one(p) ?? (depth < 6 ? wildOne(p, depth + 1) : null)
      ?? (rx === SPLIT ? pieces(p, COMMA, depth) : null) ?? p)).join("");
    return done === n ? null : done;
  }
  const EMOJI = /^((?:[\u2600-\u27BF\u{1F300}-\u{1FAFF}\uFE0F\u200D]+\s*)+)/u;
  function trn(s, depth = 0) {
    const n = norm(s);
    if (!n) return s;
    const em = depth === 0 && n.match(EMOJI);       // „🔴 …”: simbolul rămâne, restul se traduce
    if (em) { const rest = n.slice(em[0].length), out = trn(rest, 1); return out === rest ? s : em[0] + out; }
    let out = one(n);
    if (out == null && depth > 0 && n.includes("; ")) {       // o listă prinsă de „{...}”: fiecare element separat
      const done = n.split(/(; )/).map((p, k) => (k % 2 ? p : trn(p, depth + 1))).join("");
      if (done !== n) out = done;
    }
    out = out ?? (depth < 6 ? wildOne(n, depth) : null) ?? pieces(n, SPLIT, depth) ?? pieces(n, COMMA, depth);
    if (out == null) return s;
    const lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0];
    return lead + out + tail;
  }
  function tr(s) {
    if (LANG !== "en" || !s) return s;
    return trn(s);
  }
  function setNames(list) {
    const l = [...new Set(list.filter((x) => x && x.trim()))].sort((a, b) => b.length - a.length);
    names = l.length ? new RegExp(l.map(reEsc).join("|"), "g") : null;
    wildRx = new Map();
  }
  const ATTRS = ["aria-label", "title", "placeholder"];
  function node(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode;
      if (!p || p.nodeName === "SCRIPT" || p.nodeName === "STYLE") return;
      const v = tr(n.nodeValue);
      if (v !== n.nodeValue) n.nodeValue = v;
    } else if (n.nodeType === 1) {
      for (const a of ATTRS) {
        const old = n.getAttribute(a);
        if (old) { const v = tr(old); if (v !== old) n.setAttribute(a, v); }
      }
      if (n.nodeName === "INPUT" && (n.type === "button" || n.type === "submit") && n.value) n.value = tr(n.value);
      for (let c = n.firstChild; c; c = c.nextSibling) node(c);
    }
  }
  function start() {
    if (LANG !== "en") return;
    document.title = tr(document.title);
    node(document.body);
    new MutationObserver((ms) => {
      for (const m of ms) {
        if (m.type === "characterData") node(m.target);
        else if (m.type === "attributes") node(m.target);
        else m.addedNodes.forEach(node);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  return { tr, start, setNames };
})();
/* texte care nu ajung în pagină (confirm, alert, prompt): t("text românesc"), tf("text cu {0} și {1}", a, b) */
const t = (s) => I18N.tr(s);
const tf = (s, ...a) => t(s).replace(/\{(\d)\}/g, (_, i) => a[+i] ?? "");

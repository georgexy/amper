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
  const dict = new Map(), pat = new Map();
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const NUM = /(?<![\w.,])[-−]?\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*/g;   // „-5 W” → „# W”
  if (LANG === "en" && typeof EN === "object") {
    for (const [k, v] of Object.entries(EN)) (k.includes("#") ? pat : dict).set(norm(k), v);
  }
  function tr(s) {
    if (LANG !== "en" || !s) return s;
    const n = norm(s);
    if (!n || !/[a-zăâîșțA-ZĂÂÎȘȚ]/.test(n)) return s;
    let out = dict.get(n);
    if (out == null && pat.size) {
      const nums = n.match(NUM);
      if (nums) {
        const v = pat.get(n.replace(NUM, "#"));
        if (v != null) { let i = 0; out = v.replace(/#/g, () => nums[i++] ?? "#"); }
      }
    }
    if (out == null) return s;
    const lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0];
    return lead + out + tail;
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
  return { tr, start };
})();
/* texte care nu ajung în pagină (confirm, alert, prompt): t("text românesc"), tf("text cu {0} și {1}", a, b) */
const t = (s) => I18N.tr(s);
const tf = (s, ...a) => t(s).replace(/\{(\d)\}/g, (_, i) => a[+i] ?? "");

/* Aplicația de telefon pentru amper — fără dependențe externe. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const nf = (d) => new Intl.NumberFormat("ro-RO", { minimumFractionDigits: d, maximumFractionDigits: d });
const N0 = nf(0), N1 = nf(1), N2 = nf(2), N3 = nf(3);
const num = (v, d = 1) => (v == null || isNaN(v) ? "–" : [N0, N1, N2, N3][d].format(v));
function watts(w, html = true) {
  if (w == null) return "–";
  const a = Math.abs(w);
  const [v, u] = a >= 1000 ? [num(w / 1000, a >= 10000 ? 1 : 2), "kW"] : [num(w, 0), "W"];
  return html ? `${v}<small>${u}</small>` : `${v} ${u}`;
}
function duration(h) {
  if (h == null || !isFinite(h)) return "";
  if (h > 72) return `${num(h / 24, 0)} zile`;
  const hh = Math.floor(h), mm = Math.round((h - hh) * 60);
  return hh ? `${hh} h ${mm ? mm + " min" : ""}`.trim() : `${mm} min`;
}
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" });
const dayTime = (ts) => new Date(ts * 1000).toLocaleString("ro-RO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `acum ${Math.round(s)} s`;
  if (s < 3600) return `acum ${Math.round(s / 60)} min`;
  return `la ${dayTime(ts)}`;
}

const SERIES = ["#63B7E6", "#4FD1B5", "#B39DF5", "#F48FB1", "#F0A35E", "#9CCC65"];
const FAULT_RULES = { not_charging: "nu se încarcă", charge_mos_off: "încărcare oprită", discharge_mos_off: "descărcare oprită",
  comm_lost: "fără date", cell_low: "celulă descărcată", temp_high: "prea cald", temp_low_charge: "prea rece",
  low_soc: "aproape goală", cell_high: "celulă prea sus", cell_delta: "dezechilibrată", soc_divergence: "rămâne în urmă",
  bms_alarm: "avertisment BMS", bms_protection: "protecție BMS" };

/* „Daly 2: BMS-ul a OPRIT încărcarea (MOSFET încărcare OFF)” → textul principal + detaliile (ce e în paranteze) */
function splitDetail(m) {
  const short = [], det = [];
  let cur = [], depth = 0;
  for (const ch of String(m || "")) {
    if (ch === "(" && ++depth === 1) { cur = []; continue; }
    if (ch === ")" && depth) { if (--depth === 0) { det.push(cur.join("").trim()); continue; } }
    (depth ? cur : short).push(ch);
  }
  if (depth) return [m, ""];
  const text = short.join("").replace(/\s{2,}/g, " ").replace(/ :/g, ":").trim();
  return [text || m, det.filter(Boolean).join("; ")];
}
// la Alerte apare doar textul scurt; atingerea unei alerte arată/ascunde detaliile (ținute minte peste redesenări)
const openAlerts = new Set();
function alertText(m, key) {
  const [short, det] = splitDetail(m);
  const open = openAlerts.has(key);
  return `<span data-ak="${esc(key)}"><b>${esc(short)}</b>${det ? `<small class="det" ${open ? "" : "hidden"}>${esc(det)}</small>` : ""}</span>`;
}
document.addEventListener("click", (e) => {
  const row = e.target.closest("#al-active .row, #al-hist .row");
  const det = row && $(".det", row);
  if (!det) return;
  const k = $("[data-ak]", row).dataset.ak;
  openAlerts.has(k) ? openAlerts.delete(k) : openAlerts.add(k);
  det.hidden = !openAlerts.has(k);
  row.classList.toggle("open", !det.hidden);
});

const state = { histReq: 0, info: null, snap: null, view: "now", hours: 24, lastMsg: 0, ws: null, sheetDev: null, energy: [],
  look: "cadrane", day: null, dayDrawn: null };

/* ------------------------------------------------------------------ pornire */
async function api(path, opts) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401) { showLogin(); throw new Error("auth"); }
  if (r.status === 403) {
    const j = await r.clone().json().catch(() => ({}));
    if (j.reset_local) { showResetLocal(j.error); throw new Error("reset"); }
  }
  return r.json();
}

async function boot() {
  const q = new URLSearchParams(location.search).get("tema");   // ecranul HDMI: ?tema=flux
  if (q && LOOKS[q]) savePref("look", q);
  setLook(q && LOOKS[q] ? q : loadPref("look", "cadrane"));
  renderLooks();
  try {
    state.info = await api("/api/info");
  } catch (e) {
    if (e.message !== "auth" && e.message !== "reset") { showDown(); setTimeout(boot, 3000); }
    return;
  }
  bootDown = 0;
  $("#login").hidden = true;
  if (state.info.setup_needed) return openSetup(false);
  if (state.info.password_reset) return openSetup(false, "password", true);
  $("#app").hidden = false;
  $("#simnote").hidden = !state.info.simulated;
  if (state.info.site_name) { $("#site-name").textContent = state.info.site_name; document.title = `Amper · ${state.info.site_name}`; }
  connect();
  loadEnergy();
  setInterval(loadEnergy, 60000);
  loadDay();
  setInterval(loadDay, 300000);
  const v = new URLSearchParams(location.search).get("v");
  if (v === "alerts") { show("alerts"); history.replaceState(null, "", location.pathname); }
  if (v === "update") { show("settings"); showPanel("update"); history.replaceState(null, "", location.pathname); }
  $("#version").textContent = `Amper ${state.info.version || ""} · citire la fiecare ${state.info.poll_interval} s.`;
  $("#set-about-sub").textContent = `Amper ${state.info.version || ""} · actualizări, Pi-ul`;
  setInterval(tickConn, 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#pw").hidden = $("#pwgo").hidden = $('label[for="pw"]').hidden = false;    // după mesajul „nu răspunde”
  $("#pwerr").textContent = "";
  $("#pw").focus();
}
$("#pwgo").onclick = async () => {
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("#pw").value }) });
  if (r.ok) return boot();
  const j = await r.json().catch(() => ({}));
  $("#pwerr").textContent = r.status === 429 ? j.error : "Parola nu e corectă.";
};
$("#pw").addEventListener("keydown", (e) => e.key === "Enter" && $("#pwgo").click());

/* Scrierile importante (BMS, invertor, WiFi, actualizare) cer parola aplicației din nou. O ținem minte doar în
   memoria paginii, 5 minute, ca să nu fie cerută la fiecare setare dintr-o serie. */
const pwCache = { v: "", until: 0 };
function askPassword(msg) {
  const d = $("#pwask");
  $("#pwask-msg").textContent = msg || "Scrie parola aplicației ca să confirmi.";
  const inp = $("#pwask-in");
  inp.value = "";
  d.showModal();
  inp.focus({ preventScroll: true });              // pe loc: pe iPhone, tastatura se deschide doar în aceeași atingere
  return new Promise((res) => {
    const done = (v) => { d.close(); $("#pwask-ok").onclick = $("#pwask-no").onclick = null; res(v); };
    const send = () => {
      if (!inp.value) { $("#pwask-msg").textContent = "Scrie parola aplicației."; inp.focus(); return; }
      done(inp.value);
    };
    $("#pwask-ok").onclick = send;
    $("#pwask-no").onclick = () => done(null);
    // nu `e.key === "Enter" && send()`: un `false` întors de onkeydown anulează tasta, deci nu se mai putea scrie
    inp.onkeydown = (e) => { if (e.key === "Enter") send(); };
    d.oncancel = () => done(null);
  });
}
async function securePost(url, body = {}) {
  let msg = "";
  for (let i = 0; i < 3; i++) {
    const pw = Date.now() < pwCache.until ? pwCache.v : await askPassword(msg);
    if (pw == null) throw new Error("Anulat.");
    const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, app_password: pw }) });
    const j = await r.json().catch(() => ({}));
    if (r.status === 403 && j.bad_password) { pwCache.until = 0; msg = "Parola nu e corectă. Încearcă din nou."; continue; }
    if (r.status === 429) { pwCache.until = 0; throw new Error(j.error); }
    if (r.ok) { pwCache.v = pw; pwCache.until = Date.now() + 5 * 60000; }
    return { r, j };
  }
  throw new Error("Parola nu e corectă.");
}

/* ------------------------------------------------------------------ date live */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws;
  try { ws = new WebSocket(`${proto}://${location.host}/api/ws`); } catch { return poll(); }
  state.ws = ws;
  ws.onmessage = (ev) => { state.lastMsg = Date.now(); onSnap(JSON.parse(ev.data)); };
  ws.onclose = () => { state.ws = null; setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
}
async function poll() {
  try { onSnap(await api("/api/live")); state.lastMsg = Date.now(); } catch {}
  if (!state.ws) setTimeout(poll, 5000);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (!state.ws || state.ws.readyState > 1)) connect();
});

function tickConn() {
  const el = $("#conn");
  const age = (Date.now() - state.lastMsg) / 1000;
  const ok = state.lastMsg && age < 20;
  el.className = "conn " + (ok ? "ok" : state.lastMsg ? "bad" : "");
  el.querySelector("span").textContent = !state.lastMsg ? "Se conectează…" : ok ? "Live" : `Fără legătură de ${Math.round(age)} s`;
}

/* Actualizare automată: când pe Pi apare o versiune nouă a aplicației, pagina se reîncarcă singură
   (pe toate telefoanele și pe ecranul HDMI). Nu în timpul unei editări; cel mult o dată pe minut. */
function checkVersion(v) {
  if (!v) return;
  if (!state.appVersion) { state.appVersion = v; return; }
  if (v === state.appVersion || state.reloading || state.updating) return;
  if ($("#edit").open || $("#sheet").open || state.view === "bms" || document.activeElement?.matches?.("input, select")) return;
  let last = 0;
  try { last = +sessionStorage.getItem("reloadedAt") || 0; } catch {}
  if (Date.now() - last < 60000) return;
  state.reloading = true;
  try { sessionStorage.setItem("reloadedAt", Date.now()); } catch {}
  const go = () => fetch("/api/health", { cache: "no-store" }).then((r) => (r.ok ? location.reload() : (state.reloading = false)),
    () => (state.reloading = false));
  navigator.serviceWorker?.getRegistration?.().then((r) => r?.update()).then(go, go) || go();
}

function onSnap(s) {
  checkVersion(s.app_version);
  state.snap = s;
  renderNow();
  renderBadge();
  if (state.view === "alerts") { renderActiveAlerts(); markAlertsSeen(); }
  if (state.sheetDev) renderSheet();
}

/* ------------------------------------------------------------------ ecranul ACUM */
// doar dispozitivele care au răspuns măcar o dată (cele nelegate nu se afișează)
const connected = (d) => d.connected !== false;
// pe ecran apar doar modulele care comunică acum; cele deconectate dau doar notificare (și apar în Alerte)
function packs() {
  return Object.values(state.snap?.devices || {}).filter((d) => d.kind === "pack" && connected(d) && !d.stale);
}
function inverter() {
  return Object.values(state.snap?.devices || {}).find((d) => d.kind === "inverter" && connected(d));
}
function alertsFor(id) {
  return (state.snap?.alerts || []).filter((a) => a.device === id);
}

function packStatus(d) {
  const r = d.reading;
  const al = alertsFor(d.id);
  const crit = al.find((a) => a.level === "critical");
  const warn = al.find((a) => a.level === "warning");
  let cls = "idle", tag = "";
  if (r) {
    if (r.current > 0.3) cls = "charging";
    else if (r.current < -0.3) cls = "discharging";
  }
  if (d.stale) return { cls: "stale fault", tag: "fără date" };
  if (crit) return { cls: cls + " fault", tag: FAULT_RULES[crit.rule] || "alarmă" };
  if (r && r.charge_mos === false) return { cls: cls + " fault", tag: "încărcare oprită" };
  if (r && r.discharge_mos === false) return { cls: cls + " fault", tag: "descărcare oprită" };
  if (warn) return { cls: cls + " warn", tag: FAULT_RULES[warn.rule] || "atenție" };
  if (r?.balancing) tag = "echilibrează";
  return { cls, tag };
}

/* Datele comune temelor: banca, invertorul, starea pachetelor. */
const todayStr = () => new Date().toLocaleDateString("sv-SE");
function homeData() {
  const s = state.snap, b = s.bank || {};
  const inv = inverter();
  const ir = inv && !inv.stale ? inv.reading : null;
  const bp = ir && ir.battery_power != null ? ir.battery_power : b.power;   // + = intră în baterie
  const list = packs().map((d) => ({ d, ...packStatus(d) }));
  const bad = list.filter((x) => x.cls.includes("fault")), warn = list.filter((x) => x.cls.includes("warn"));
  const mode = b.current > 0.5 ? "charging" : b.current < -0.5 ? "discharging" : "idle";
  let health;
  if (!list.length) health = { cls: "stale", text: "Niciun pachet conectat" };
  else if (bad.length) health = { cls: "fault", text: bad.length === 1 ? `${bad[0].d.name}: ${bad[0].tag}` : `${bad.length} pachete cu probleme` };
  else if (warn.length) health = { cls: "warn", text: `${warn[0].d.name}: ${warn[0].tag}` };
  else health = { cls: "ok", text: list.length === 1 ? "1 pachet OK" : `${list.length} pachete OK` };
  const modeText = mode === "charging" ? `Se încarcă cu ${watts(bp, false)}` :
    mode === "discharging" ? `Se descarcă cu ${watts(Math.abs(bp), false)}` : b.packs ? "În repaus" : "Fără date";
  const eta = !b.hours ? "" : mode === "charging" ? `plină în circa ${duration(b.hours)}` :
    mode === "discharging" ? `ajunge pentru circa ${duration(b.hours)}` : "";
  const autonomy = mode === "discharging" ? b.hours : b.kwh && ir?.load_power > 50 ? (b.kwh * 1000) / ir.load_power : null;
  const today = state.energy.find((r) => r.day === todayStr()) || {};
  const week = state.energy.slice(-7).reduce((a, r) => a + (r.pv_kwh || 0), 0);
  return { b, inv, ir, bp, list, mode, modeText, eta, health, autonomy, today, week,
    capKwh: b.capacity_ah && b.voltage ? (b.capacity_ah * b.voltage) / 1000 : null };
}

const ICON = {
  sun: '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
  home: '<path d="M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5M10 20v-5.5h4V20"/>',
  inv: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 14h6M9 17h6M13 6l-2 3h2l-2 3"/>',
  grid: '<path d="M8 3 6 21M16 3l2 18M7.5 8h9M7 13h10M12 3v18"/>',
  batt: '<rect x="3" y="7" width="16" height="10" rx="2"/><path d="M21 10.5v3M9 12h4M11 10v4"/>',
};
const icon = (k) => `<svg viewBox="0 0 24 24">${ICON[k]}</svg>`;
const kv = (v, u, d = 0) => (v == null ? "–" : `${num(v, d)}<span class="u">${u}</span>`);
function put(root, k, html) { root.querySelectorAll(`[data-k="${k}"]`).forEach((e) => { if (e._h !== html) e.innerHTML = e._h = html; }); }
function modeClass(el, h, extra = "") { el.className = `${extra} mode-${h.mode} ${h.health.cls === "fault" ? "fault" : ""}`.trim(); }

/* ---------- butonul „i” de la panouri (toate temele): tensiunea și curentul PV de acum ---------- */
const PV_I = `<button class="pv-i" data-pvinfo aria-label="Tensiunea și curentul panourilor">i</button>`;
function pvInfoHtml() {
  const ir = homeData().ir;
  if (!ir) return "Invertorul nu trimite date acum.";
  const row = (name, v, a) => `<div><span>${name}</span><b>${v == null ? "–" : num(v, 1) + " V"}</b><b>${a == null ? "–" : num(a, 1) + " A"}</b></div>`;
  const two = ir.pv2_voltage >= 10;
  return `<div class="pvp-h"><span></span><span>Tensiune</span><span>Curent</span></div>
    ${row(two ? "PV1" : "Panouri", ir.pv_voltage, ir.pv_current)}${two ? row("PV2", ir.pv2_voltage, ir.pv2_current) : ""}`;
}
function pvInfoShow(btn) {
  const pop = $("#pvpop");
  if (!pop.hidden && pop._btn === btn) { pop.hidden = true; return; }
  pop._btn = btn;
  pop.innerHTML = pvInfoHtml();
  pop.hidden = false;
  const r = btn.getBoundingClientRect(), w = pop.offsetWidth;
  pop.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
  // dedesubt, dacă are loc deasupra barei de jos; altfel deasupra butonului
  const below = r.bottom + 8 + pop.offsetHeight < innerHeight - 110;
  pop.style.top = `${(below ? r.bottom + 8 : r.top - 8 - pop.offsetHeight) + scrollY}px`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-pvinfo]");
  if (b) { e.preventDefault(); e.stopPropagation(); return pvInfoShow(b); }
  if (!e.target.closest("#pvpop")) $("#pvpop").hidden = true;
}, true);

/* ---------- tema Cadrane ---------- */
const ARC = Math.PI * 56, RING = 2 * Math.PI * 62;
const GAUGES = [["pv", "Panouri", "--sun"], ["load", "Consum", "--load"], ["bat", "Baterie", "--charge"], ["temp", "Invertor", "--cell"]];
const cadrane = {
  html: () => `
    <button class="card k-hero" data-open="packs" aria-label="Bateria — vezi pachetele">
      <div class="k-ring"><svg viewBox="0 0 150 150"><circle class="trk" cx="75" cy="75" r="62"/><circle class="arc" cx="75" cy="75" r="62"/></svg>
        <div class="c"><b><span data-k="soc">–</span><sup>%</sup></b><span>baterie</span></div></div>
      <div class="k-ht"><div class="k">Energie rămasă</div><div class="v" data-k="kwh">–</div>
        <span class="pill" data-k="mode"></span><div class="eta" data-k="eta"></div><div class="health" data-k="health"></div></div>
    </button>
    <div class="k-gauges">${GAUGES.map(([k, l, c]) => `
      <div class="card k-g" style="--gc:var(${c})"${k === "temp" ? ' data-open="inverter" role="button" tabindex="0"' : ""}><svg viewBox="0 0 140 78"><path class="trk" d="M14 72 A56 56 0 0 1 126 72"/>
        <path class="arc" data-g="${k}" d="M14 72 A56 56 0 0 1 126 72" stroke-dasharray="0 ${ARC}"/></svg>
        <div class="val num" data-k="g-${k}">–</div><div class="lbl"><span data-k="l-${k}">${l}</span>${k === "pv" ? PV_I : ""}</div></div>`).join("")}</div>
    <section class="card k-day"><h3><span>Azi</span><span data-k="day"></span></h3><svg data-k="chart" viewBox="0 0 330 92" preserveAspectRatio="none"></svg></section>
    <div class="inv-line" data-k="inv" data-open="inverter" role="button" tabindex="0"></div>`,
  update(root, h) {
    const { b, ir, bp } = h;
    modeClass(root.querySelector(".k-hero"), h, "card k-hero");
    root.querySelector(".k-ring .arc").setAttribute("stroke-dasharray", `${((b.soc ?? 0) / 100) * RING} ${RING}`);
    put(root, "soc", b.soc == null ? "–" : num(b.soc, 0));
    put(root, "kwh", b.kwh == null ? "–" : `${num(b.kwh, 1)} <span class="u">din ${num(h.capKwh, 1)} kWh</span>`);
    put(root, "mode", (h.mode === "charging" ? "▲ " : h.mode === "discharging" ? "▼ " : "") + h.modeText);
    put(root, "eta", h.eta);
    put(root, "health", h.health.text);
    root.querySelector('[data-k="health"]').className = "health " + h.health.cls;
    const g = { pv: [ir?.pv_power, 4000, "W"], load: [ir?.load_power, 5000, "W"], bat: [bp == null ? null : Math.abs(bp), 5000, "W"],
      temp: [ir?.inverter_temp, 80, "°C"] };
    for (const [k, [v, max, u]] of Object.entries(g)) {
      const f = v == null ? 0 : Math.min(1, v / Math.max(max, v));
      root.querySelector(`[data-g="${k}"]`).setAttribute("stroke-dasharray", `${v ? Math.max(3, f * ARC) : 0} ${ARC}`);
      put(root, "g-" + k, u === "W" ? watts(v) : kv(v, u));
    }
    root.querySelector('[data-g="bat"]').style.stroke = h.mode === "discharging" ? "var(--cell)" : "var(--charge)";
    put(root, "l-bat", bp > 20 ? "Baterie, intră" : bp < -20 ? "Baterie, iese" : "Baterie");
    const part = (l, v) => `<span class="nw">${l} <b>${num(v, 1)} kWh</b></span>`;      // rândul se rupe doar între ele
    put(root, "day", h.today.pv_kwh == null ? "" : [part("produs", h.today.pv_kwh), part("consumat", h.today.load_kwh),
      part("din rețea", h.today.ac_in_kwh || 0)].join(" · "));
    put(root, "inv", invLine(h));
    drawDay(root.querySelector('[data-k="chart"]'));
  },
};

function invLine({ inv, ir }) {
  if (!inv) return "";
  if (!ir) return `${esc(inv.name)}: fără date ${inv.error ? "(" + esc(inv.error) + ")" : ""}`;
  return `${esc(inv.name)}: ${esc(ir.status || "–")}, ${num(ir.battery_voltage, 1)} V, ${num(ir.inverter_temp, 0)} °C` +
    (ir.fault_code ? `, DEFECT ${ir.fault_code}: ${esc(ir.fault_text || "")}` : "") +
    (ir.warnings?.length ? `, avertisment: ${esc(ir.warnings.join("; "))}` : "");
}

/* graficul zilei (producție și consum), din istoricul invertorului */
async function loadDay() {
  const inv = state.info?.devices.find((d) => d.kind === "inverter");
  if (!inv) return;
  const mid = new Date(); mid.setHours(0, 0, 0, 0);
  const hours = (Date.now() - mid) / 3600000;
  try {
    const r = await api(`/api/history?device=${encodeURIComponent(inv.id)}&hours=${Math.max(0.1, hours)}&points=144`);
    state.day = { t0: mid / 1000, rows: r.rows };
    state.dayDrawn = null;
    if (state.snap) renderNow();
  } catch {}
}
function drawDay(svg) {
  if (!svg || !state.day || state.dayDrawn === svg) return;
  state.dayDrawn = svg;
  const { t0, rows } = state.day, W = 330, H = 92;
  const max = Math.max(1000, ...rows.map((r) => Math.max(r.pv_power || 0, r.load_power || 0))) * 1.08;
  const X = (t) => ((t - t0) / 86400) * W, Y = (v) => H - 2 - (v / max) * (H - 6);
  const line = (k) => rows.filter((r) => r[k] != null).map((r, i) => `${i ? "L" : "M"}${X(r.t).toFixed(1)} ${Y(r[k]).toFixed(1)}`).join(" ");
  const pv = line("pv_power"), ld = line("load_power");
  const last = rows.length ? X(rows[rows.length - 1].t) : 0, first = rows.length ? X(rows[0].t) : 0;
  svg.innerHTML = `<defs><linearGradient id="dpv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F3B840" stop-opacity=".5"/>
    <stop offset="1" stop-color="#F3B840" stop-opacity="0"/></linearGradient></defs>
    ${[6, 12, 18].map((h) => `<line x1="${(h / 24) * W}" y1="0" x2="${(h / 24) * W}" y2="${H}" stroke="var(--line)" stroke-dasharray="2 4"/>`).join("")}
    ${pv ? `<path d="${pv} L${last} ${H} L${first} ${H}Z" fill="url(#dpv)"/><path d="${pv}" fill="none" stroke="#F3B840" stroke-width="2" vector-effect="non-scaling-stroke"/>` : ""}
    ${ld ? `<path d="${ld}" fill="none" stroke="#C9A6F0" stroke-width="2" vector-effect="non-scaling-stroke"/>` : ""}
    ${rows.length ? "" : `<text x="8" y="50" fill="var(--muted)" font-size="13">Încă nu sunt date pentru azi.</text>`}`;
}

/* ---------- tema Flux de energie ---------- */
// coordonate în viewBox 360 × 330; bateria e sub diagramă
const FLUX = {
  pv: "M72 128 C72 182 128 205 152 213", load: "M208 213 C232 205 288 182 288 128",
  bat: "M180 247 L180 330", grid: "M58 290 C100 290 148 262 160 240",
};
const flux = {
  html: () => `
    <div class="f-inv" data-k="inv" data-open="inverter" role="button" tabindex="0"></div>
    <div class="f-diag">
      <svg class="lines" viewBox="0 0 360 330">${Object.entries(FLUX).map(([k, d]) =>
        `<path class="ln" data-ln="${k}" d="${d}"/><path class="dots" data-dots="${k}" d="${d}"/>`).join("")}</svg>
      <div class="f-node" style="left:20%;top:19%;--c:var(--sun)"><div class="disc">${icon("sun")}</div><div class="w" data-k="pv">–</div><div class="l">Panouri ${PV_I}</div></div>
      <div class="f-node" style="left:80%;top:19%;--c:var(--load)"><div class="disc">${icon("home")}</div><div class="w" data-k="load">–</div><div class="l">Consum casă</div></div>
      <div class="f-node sm" data-open="inverter" role="button" tabindex="0" aria-label="Invertor" style="left:50%;top:69%;--c:var(--cell)"><div class="disc" style="width:64px;height:64px">${icon("inv")}</div></div>
      <div class="f-node sm" data-k-cls="grid" style="left:11%;top:88%;--c:var(--muted)"><div class="disc">${icon("grid")}</div><div class="l" data-k="grid">Rețea</div></div>
      <div class="f-blabel" data-k="bat" style="left:53%;top:87%"></div>
    </div>
    <button class="f-batt" data-open="packs" aria-label="Bateria — vezi pachetele">
      <div class="f-cap"></div><div class="f-cell"><div class="f-fill"></div><b><span data-k="soc">–</span>%</b></div>
      <div class="k" data-k="kwh"></div><div class="s" data-k="eta"></div><span class="health" data-k="health"></span>
    </button>
    <div class="f-cards"><div class="card"><span>Produs azi</span><b data-k="tpv">–</b></div>
      <div class="card"><span>Din rețea azi</span><b data-k="tgr">–</b></div>
      <div class="card"><span>Consumat azi</span><b data-k="tld">–</b></div>
      <div class="card"><span>Autonomie</span><b data-k="aut">–</b></div></div>`,
  update(root, h) {
    const { b, ir, bp } = h;
    const ac = ir?.ac_in_power || 0;
    const flows = {
      pv: [ir?.pv_power, "var(--sun)", false], load: [ir?.load_power, "var(--load)", false],
      bat: [bp == null ? null : Math.abs(bp), h.mode === "discharging" ? "var(--cell)" : "var(--charge)", bp < 0],
      grid: [ac, "var(--muted)", false],
    };
    for (const [k, [w, c, rev]] of Object.entries(flows)) {
      const ln = root.querySelector(`[data-ln="${k}"]`), dots = root.querySelector(`[data-dots="${k}"]`);
      ln.style.stroke = c; dots.style.stroke = `color-mix(in srgb, ${c} 45%, #fff)`;
      ln.classList.toggle("off", !(w > 20));
      dots.classList.toggle("on", w > 20);
      dots.classList.toggle("rev", !!rev);
      dots.style.setProperty("--dur", `${Math.max(0.7, 3 - (w || 0) / 1500).toFixed(1)}s`);
    }
    root.querySelector(".f-diag").className = `f-diag mode-${h.mode}`;
    put(root, "pv", watts(ir?.pv_power));
    put(root, "load", watts(ir?.load_power));
    put(root, "bat", bp == null || Math.abs(bp) <= 20 ? "" : watts(Math.abs(bp)));
    const gridOn = ir?.ac_in_voltage > 50;
    root.querySelector('[data-k-cls="grid"]').classList.toggle("dim", !gridOn);
    put(root, "grid", `Rețea<br>${ir?.ac_in_voltage == null ? "–" : num(ir.ac_in_voltage, 0) + " V"}`);
    put(root, "inv", ir ? `Invertor: <b>${esc(ir.status || "–")}</b><br>${num(ir.inverter_temp, 0)} °C · ${num(ir.output_voltage, 0)} V · ${num(ir.output_freq, 1)} Hz`
      : h.inv ? `${esc(h.inv.name)}: fără date` : "");
    modeClass(root.querySelector(".f-batt"), h, "f-batt");
    root.querySelector(".f-fill").style.height = `calc(${Math.max(0, Math.min(100, b.soc ?? 0))}% - 12px)`;
    put(root, "soc", b.soc == null ? "–" : num(b.soc, 0));
    put(root, "kwh", b.kwh == null ? h.modeText : `${num(b.kwh, 1)} kWh · ${h.modeText[0].toLowerCase() + h.modeText.slice(1)}`);
    put(root, "eta", [h.eta, b.voltage ? num(b.voltage, 1) + " V" : ""].filter(Boolean).join(" · "));
    put(root, "health", h.health.text);
    root.querySelector('[data-k="health"]').className = "health " + h.health.cls;
    put(root, "tpv", kv(h.today.pv_kwh, "kWh", 1));
    put(root, "tld", kv(h.today.load_kwh, "kWh", 1));
    put(root, "tgr", h.today.pv_kwh == null ? "–" : kv(h.today.ac_in_kwh || 0, "kWh", 1));
    put(root, "aut", h.autonomy == null ? "–" : `~${duration(h.autonomy)}`);
  },
};

/* ---------- tema Carduri ---------- */
const carduri = {
  html: () => `
    <section class="c-head"><div class="st" data-k="st" data-open="inverter" role="button" tabindex="0"></div>
      <div class="c-sum"><div>Produs azi<b data-k="tpv">–</b></div><div>Din rețea azi<b data-k="tgr">–</b></div><div>Consumat azi<b data-k="tld">–</b></div><div>7 zile<b data-k="tw">–</b></div></div></section>
    <button class="card c-batt" data-open="packs" aria-label="Bateria — vezi pachetele">
      <div class="c-row"><div class="soc"><span data-k="soc">–</span><sup>%</sup></div>
        <div class="r">Energie rămasă<b data-k="kwh">–</b><span data-k="volt"></span></div></div>
      <div class="c-bar"><div class="body">${"<i></i>".repeat(10)}</div><div class="nub"></div></div>
      <div class="c-mode" data-k="mode"></div>
      <div class="c-chips" data-k="chips"></div>
      <div class="c-foot"><span class="health" data-k="health"></span><b>Toate bateriile ›</b></div>
    </button>
    <section class="card c-list">${[["pv", "Panouri solare", "--sun", "sun"], ["load", "Consum casă", "--load", "home"],
      ["bat", "Baterie", "--charge", "batt"], ["grid", "Rețea", "--muted", "grid"]].map(([k, l, c, i]) => `
      <div class="c-it" style="--ic:var(${c})" data-it="${k}"><div class="c-ico">${icon(i)}</div>
        <div class="m"><div class="k"><span data-k="l-${k}">${l}</span>${k === "pv" ? PV_I : ""}</div><div class="tr"><i data-bar="${k}"></i></div></div><div class="v" data-k="v-${k}">–</div></div>`).join("")}</section>`,
  update(root, h) {
    const { b, ir, bp } = h;
    put(root, "st", `${h.ir ? esc(ir.status || "") : "Invertor: fără date"}${ir ? ` · ${num(ir.inverter_temp, 0)} °C` : ""} ›`);
    put(root, "tpv", kv(h.today.pv_kwh, "kWh", 1));
    put(root, "tld", kv(h.today.load_kwh, "kWh", 1));
    put(root, "tgr", h.today.pv_kwh == null ? "–" : kv(h.today.ac_in_kwh || 0, "kWh", 1));
    put(root, "tw", kv(h.week, "kWh", 1));
    modeClass(root.querySelector(".c-batt"), h, "card c-batt");
    put(root, "soc", b.soc == null ? "–" : num(b.soc, 0));
    put(root, "kwh", b.kwh == null ? "–" : `${num(b.kwh, 1)} / ${num(h.capKwh, 1)} kWh`);
    put(root, "volt", b.voltage ? `${num(b.voltage, 1)} V` : "");
    const segs = (b.soc ?? 0) / 10;
    root.querySelectorAll(".c-bar .body i").forEach((el, i) => {
      el.className = i < Math.floor(segs) ? "f" : "";
      el.style.background = i === Math.floor(segs) && segs % 1 > 0.05
        ? `linear-gradient(90deg, var(--mc) ${(segs % 1) * 100}%, var(--panel-2) 0)` : "";
    });
    put(root, "mode", (h.mode === "charging" ? "▲ " : h.mode === "discharging" ? "▼ " : "") + h.modeText + (h.eta ? " · " + h.eta : ""));
    put(root, "chips", h.list.map((x) => `<span class="${x.cls.includes("fault") ? "fault" : x.cls.includes("warn") ? "warn" : ""}">${esc(x.d.name)}
      <b>${x.d.stale || x.d.reading?.soc == null ? "–" : num(x.d.reading.soc, 0) + "%"}</b></span>`).join(""));
    put(root, "health", h.health.text);
    root.querySelector('[data-k="health"]').className = "health " + h.health.cls;
    const vals = { pv: [ir?.pv_power, 4000], load: [ir?.load_power, 5000], bat: [bp == null ? null : Math.abs(bp), 5000] };
    for (const [k, [v, max]] of Object.entries(vals)) {
      put(root, "v-" + k, watts(v));
      root.querySelector(`[data-bar="${k}"]`).style.width = `${v == null ? 0 : Math.min(100, (v / Math.max(max, v)) * 100)}%`;
    }
    root.querySelector('[data-it="bat"]').style.setProperty("--ic", h.mode === "discharging" ? "var(--cell)" : "var(--charge)");
    put(root, "l-bat", bp > 20 ? "Baterie, intră" : bp < -20 ? "Baterie, iese" : "Baterie");
    put(root, "v-grid", kv(ir?.ac_in_voltage, "V"));
    root.querySelector('[data-bar="grid"]').style.width = ir?.ac_in_voltage > 50 ? "100%" : "0";
  },
};

const LOOKS = {
  cadrane: { name: "Cadrane", desc: "Inel cu încărcarea, cadrane și graficul zilei", sw: "conic-gradient(#52C98F 0 75%, #2A4255 0)", c: "#63B7E6", ...cadrane },
  flux: { name: "Flux de energie", desc: "Soare → invertor → casă și baterie, cu energia în mișcare", sw: "radial-gradient(circle at 50% 40%, #F3B840 0 22%, #18303F 24%)", c: "#F3B840", ...flux },
  carduri: { name: "Carduri", desc: "Antet portocaliu, baterie pe segmente, listă cu bare", sw: "linear-gradient(135deg, #E08A2E, #8E3320)", c: "#FF8A3D", ...carduri },
};
let drawnLook = null;

function renderNow() {
  const s = state.snap;
  if (!s) return;
  const home = $("#home");
  const L = LOOKS[state.look] || LOOKS.cadrane;
  if (drawnLook !== state.look) {
    home.innerHTML = L.html();
    drawnLook = state.look;
    state.dayDrawn = null;
    $$("[data-open=packs]", home).forEach((el) => (el.onclick = () => show("packs")));
    $$("[data-open=inverter]", home).forEach((el) => (el.onclick = () => show("inverter")));
  }
  L.update(home, homeData());
  if (!$("#pvpop").hidden) $("#pvpop").innerHTML = pvInfoHtml();            // valorile se schimbă la fiecare citire
  if (state.view === "packs") renderPacks();
  if (state.view === "inverter") renderInverterLive();
  if (state.view === "settings") renderDaly();

  // alertele nu mai apar ca bannere pe ecranul principal: doar la Alerte (cifra roșie de pe buton)
}

/* ------------------------------------------------------------------ ecranul BATERIILE */
// Bluetooth: numele modulului (ca în aplicația Daly de pe telefon) și adresa — ca să știi care baterie e care
function bleId(r) {
  const n = r.raw?.ble_name, mac = r.raw?.ble_mac;
  if (!n && !mac) return "";
  const both = n && mac && !n.toUpperCase().includes(mac.replace(/:/g, ""));
  return `<div class="pk-id"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10-5 4V3l5 4L7 17"/></svg>${esc(n || mac)}${both ? ` · ${esc(mac)}` : ""}</div>`;
}
function renderPacks() {
  const h = homeData(), b = h.b;
  $("#pk-soc").textContent = b.soc == null ? "–" : `${num(b.soc, 0)}%`;
  const temps = h.list.flatMap((x) => (x.d.stale ? [] : x.d.reading?.temps || []));
  $("#pk-sum").innerHTML = [["Rămas", kv(b.kwh, "kWh", 1)], ["Curent", b.current == null ? "–" : `${b.current > 0 ? "+" : ""}${num(b.current, 1)} A`],
    ["Tensiune", kv(b.voltage, "V", 1)], ["Temp. max", temps.length ? kv(Math.max(...temps), "°C") : "–"]]
    .map(([k, v]) => `<div>${k}<b>${v}</b></div>`).join("");
  const box = $("#pk-list");
  const ids = h.list.map((x) => x.d.id).join("|");
  if (box.dataset.ids !== ids) {
    box.dataset.ids = ids;
    box.innerHTML = h.list.map((x) => `<button class="card pk" data-id="${esc(x.d.id)}"></button>`).join("");
    $$(".pk", box).forEach((el) => (el.onclick = (ev) => {
      const g = ev.target.closest("[data-bms]");
      g ? openBms(g.dataset.bms) : openSheet(el.dataset.id);
    }));
  }
  for (const { d, cls, tag } of h.list) {
    const r = d.reading || {}, el = box.querySelector(`[data-id="${CSS.escape(d.id)}"]`);
    const mode = cls.split(" ")[0];
    el.className = `card pk mode-${["charging", "discharging"].includes(mode) ? mode : "idle"} ${cls.includes("fault") ? "fault" : cls.includes("warn") ? "warn" : ""} ${d.stale ? "stale" : ""}`;
    const label = tag || { charging: "se încarcă", discharging: "se descarcă" }[mode] || "în repaus";
    const cells = r.cells || [];
    const lo = cells.length ? Math.min(...cells) : null, hi = cells.length ? Math.max(...cells) : null;
    const floor = Math.min(lo ?? 3, 3.0) - 0.05, span = Math.max((hi ?? 3.45) + 0.02, 3.45) - floor;
    const mos = (v, n) => `<span class="${v === false ? "off" : ""}">${n}${v === false ? " OPRIT" : ""}</span>`;
    const facts = [r.current == null ? null : `${r.current > 0 ? "+" : ""}${num(r.current, 1)} A`, r.voltage == null ? null : `${num(r.voltage, 2)} V`,
      r.temps?.length ? `${num(Math.max(...r.temps), 0)} °C` : null, r.cycles != null ? `${r.cycles} cicluri` : null].filter(Boolean).join(" · ");
    el.innerHTML = `<div class="h"><div class="pk-mini"><i style="height:${Math.max(0, Math.min(100, r.soc ?? 0))}%"></i></div>
      <div class="t"><div class="n">${esc(d.name)} <span class="tag">${esc(label)}</span>${d.type === "daly" && !d.stale
        ? `<span class="pk-gear" data-bms="${esc(d.id)}" role="button" tabindex="0" aria-label="Setările BMS ${esc(d.name)}">⚙</span>` : ""}</div>
      <div class="s">${d.stale ? `Nu comunică${d.error ? ": " + esc(d.error) : ""}` : facts}</div>${bleId(r)}</div>
      <div class="soc">${r.soc == null ? "–" : num(r.soc, 0)}<sup>%</sup></div></div>
      ${cells.length ? `<div class="pk-cells">${cells.map((v) => `<i class="${hi - lo > 0.005 ? (v === hi ? "hi" : v === lo ? "lo" : "") : ""}"
        style="height:${Math.max(8, ((v - floor) / span) * 100)}%"></i>`).join("")}</div>` : ""}
      <div class="ft"><span>${cells.length ? `${cells.length} celule · min <b>${num(lo, 3)}</b> · max <b>${num(hi, 3)}</b>` : ""}</span>
      <span class="mos">${mos(r.charge_mos, "încărcare")}${mos(r.discharge_mos, "descărcare")}</span></div>`;
  }
}
$("#pk-back").onclick = () => show("now");

/* ------------------------------------------------------------------ ecranul INVERTOR */
const inv = { data: null, edit: null };
$("#iv-back").onclick = () => show("now");

function renderInverterLive() {
  const d = inverter();
  if (!d) return;
  const r = d.stale ? null : d.reading;
  $("#iv-title").textContent = d.name;
  $("#iv-state").innerHTML = r ? `Stare: <b>${esc(r.status || "–")}</b>` +
    (r.fault_code ? `<br><b style="color:var(--alarm)">Defect ${r.fault_code}: ${esc(r.fault_text || "")}</b>` : "") +
    (r.warnings?.length ? `<br><b style="color:var(--warn)">Avertisment: ${esc(r.warnings.join("; "))}</b>` : "") +
    (r.notices?.length ? `<br>${esc(r.notices.join("; "))}` : "") :
    `Fără date${d.error ? ": " + esc(d.error) : ""}`;
  $("#iv-sum").innerHTML = [["Panouri", watts(r?.pv_power)], ["Consum", watts(r?.load_power)],
    ["Baterie", kv(r?.battery_voltage, "V", 1)], ["Temp.", kv(r?.inverter_temp, "°C")]]
    .map(([k, v]) => `<div>${k}<b>${v}</b></div>`).join("");
}

async function loadInverter() {
  if (!inv.data) $("#iv-list").innerHTML = `<div class="note">Citesc setările din invertor…</div>`;
  try {
    const r = await api("/api/inverter/settings");
    if (r.error) throw new Error(r.error);
    inv.data = r;
    renderInverter();
  } catch (e) {
    if (e.message !== "auth") $("#iv-list").innerHTML = `<div class="banner">${esc(e.message)}</div>`;
  }
}

function renderInverter() {
  const r = inv.data;
  const groups = [...new Set(r.settings.map((x) => x.group))];
  $("#iv-list").innerHTML = (r.can_write ? "" : `<div class="banner warning"><b>Modificarea e dezactivată</b>
      <small>Setați o parolă pentru aplicație (web.password în config) ca să poată fi schimbate setările.</small></div>`) +
    groups.map((g) => `<h2>${esc(g)}</h2><div class="list iv-set">${r.settings.filter((x) => x.group === g).map((x) => `
      <button class="row iv-row" data-key="${esc(x.key)}" ${r.can_write ? "" : "disabled"}>
        <span class="k">${esc(x.label)}${x.risky ? ' <i class="risk" title="Setare cu risc">!</i>' : ""}</span>
        <span class="v">${esc(x.text)}</span>${r.can_write ? '<span class="ed">Modifică ›</span>' : ""}</button>`).join("")}</div>`).join("");
  $$(".iv-row", $("#iv-list")).forEach((el) => (el.onclick = () => openEdit(el.dataset.key)));
  $("#iv-clock").innerHTML = `Invertor: <b>${esc(r.clock || "–")}</b><br>Pi: <b>${esc(r.pi_clock)}</b>`;
  $("#iv-sync").disabled = !r.can_write;
  $("#iv-log").innerHTML = r.log.length ? r.log.map((x) => `
    <div class="row ${x.ok ? "" : "critical"}"><span>${esc(x.setting)}: ${x.ok ? `${esc(x.old)} → <b>${esc(x.new)}</b>` : `EȘUAT — ${esc(x.error)}`}</span>
    <span class="t">${dayTime(x.ts)}</span></div>`).join("") : `<div class="empty">Nicio modificare până acum.</div>`;
}

const editor = { item: null, target: "", write: null };
const decimals = (step) => (step < 0.01 ? 3 : step < 1 ? 1 : 0);
function openEdit(key) {
  const x = inv.data.settings.find((s) => s.key === key);
  openEditor(x, "invertor", async (v) => {
    await writeInverter("/api/inverter/settings", { key: x.key, value: v }, $("#ed-go"));
    await loadInverter();
  });
}
function openEditor(x, target, write) {
  Object.assign(editor, { item: x, target, write });
  inv.edit = x;
  $("#ed-title").textContent = x.group ? `${x.group}: ${x.label}` : x.label;
  $("#ed-now").innerHTML = `Acum: <b>${esc(x.text)}</b>`;
  $("#ed-field").innerHTML = x.kind === "enum"
    ? `<select id="ed-val" class="ed-in">${x.options.map(([v, t]) => `<option value="${v}" ${v === x.raw ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>`
    : `<div class="ed-num"><input id="ed-val" class="ed-in" type="number" inputmode="decimal" min="${x.min}" max="${x.max}" step="${x.step}" value="${x.value ?? ""}">
       <span>${esc(x.unit)}</span></div><p class="note">Între ${num(x.min, decimals(x.step))} și ${num(x.max, decimals(x.step))} ${esc(x.unit)}.${x.role ? ` Acum e pragul de <b>${esc(x.role)}</b>.` : ""}</p>`;
  $("#ed-warn").hidden = !(x.risky || x.note);
  $("#ed-warn").innerHTML = (x.risky ? "<b>Atenție: setare cu risc.</b> " : "") + esc(x.note || (x.risky ? "O valoare greșită poate opri alimentarea sau afecta bateria." : ""));
  $("#ed-go").textContent = `Scrie în ${target}`;
  $("#ed-ackbox").hidden = !x.risky;
  $("#ed-ack").checked = false;
  $("#ed-err").textContent = "";
  $("#ed-val").oninput = $("#ed-val").onchange = updateDiff;
  $("#ed-ack").onchange = updateDiff;
  updateDiff();
  $("#edit").showModal();
}

function editValue() {
  const x = inv.edit, el = $("#ed-val");
  if (x.kind === "enum") return { value: +el.value, text: x.options.find(([v]) => v === +el.value)?.[1] };
  const v = parseFloat(String(el.value).replace(",", "."));
  if (isNaN(v) || v < x.min - 1e-9 || v > x.max + 1e-9) return { error: `Valoarea trebuie să fie între ${num(x.min, decimals(x.step))} și ${num(x.max, decimals(x.step))} ${x.unit}.` };
  return { value: v, text: `${num(v, decimals(x.step))} ${x.unit}` };
}

function updateDiff() {
  const x = inv.edit, e = editValue();
  const same = !e.error && (x.kind === "enum" ? e.value === x.raw : Math.abs(e.value - x.value) < 1e-9);
  $("#ed-diff").innerHTML = e.error ? `<span class="err">${esc(e.error)}</span>` : same ? "Aceeași valoare ca acum." :
    `${esc(x.text)} → <b>${esc(e.text)}</b>`;
  $("#ed-go").disabled = !!e.error || same || (x.risky && !$("#ed-ack").checked);
}

async function writeInverter(path, body, btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Se scrie și se verifică…";
  try {
    const { r, j } = await securePost(path, body || {});
    if (!r.ok || !j.ok) throw new Error(j.error || `eroare ${r.status}`);
    inv.data.settings = j.settings;
    if (j.clock) inv.data.clock = j.clock;
    return j;
  } finally {
    btn.textContent = label;
    btn.disabled = false;
  }
}

$("#ed-close").onclick = () => $("#edit").close();
$("#ed-go").onclick = async () => {
  const x = editor.item, e = editValue();
  if (e.error) return;
  if (!confirm(`Scriu în ${editor.target}?\n\n${x.group ? x.group + ", " : ""}${x.label}: ${x.text} → ${e.text}`)) return;
  try {
    await editor.write(e.value);
    $("#edit").close();
  } catch (err) {
    $("#ed-err").textContent = err.message;
  }
};
$("#iv-sync").onclick = async () => {
  if (!confirm("Pun ceasul invertorului după ora Pi-ului?")) return;
  try {
    await writeInverter("/api/inverter/clock", null, $("#iv-sync"));
    await loadInverter();
  } catch (err) {
    alert(err.message);
  }
};

/* ------------------------------------------------------------------ SETĂRI BMS (Daly) */
const bms = { id: null, data: null };
$("#bms-back").onclick = () => show("packs");
function openBms(id) {
  bms.id = id;
  bms.data = null;
  show("bms");
}
async function loadBms() {
  const d = state.snap?.devices?.[bms.id];
  $("#bms-title").textContent = `Setări BMS — ${d?.name || bms.id}`;
  if (!bms.data) $("#bms-list").innerHTML = `<div class="note">Citesc setările din BMS…</div>`;
  try {
    const r = await api(`/api/daly/${encodeURIComponent(bms.id)}/settings`);
    if (r.error) throw new Error(r.error);
    bms.data = r;
    renderBms();
  } catch (e) {
    if (e.message !== "auth") $("#bms-list").innerHTML = `<div class="banner">${esc(e.message)}</div>`;
  }
}
function renderBms() {
  const r = bms.data, groups = [...new Set(r.settings.map((x) => x.group))];
  $("#bms-list").innerHTML = (r.can_write ? "" : `<div class="banner warning"><b>Modificarea e dezactivată</b>
      <small>Setați o parolă pentru aplicație ca să poată fi schimbate setările.</small></div>`) +
    groups.map((g) => `<h2>${esc(g)}</h2><div class="list iv-set">${r.settings.filter((x) => x.group === g).map((x) => `
      <button class="row iv-row" data-key="${esc(x.key)}" ${r.can_write ? "" : "disabled"}>
        <span class="k">${esc(x.label)}${x.risky ? ' <i class="risk" title="Setare cu risc">!</i>' : ""}${x.role
          ? ` <span class="role ${x.role === "protecție" ? "p" : ""}">${esc(x.role)}</span>` : ""}</span>
        <span class="v">${esc(x.text)}</span>${r.can_write ? '<span class="ed">Modifică ›</span>' : ""}</button>`).join("")}</div>`).join("");
  $$(".iv-row", $("#bms-list")).forEach((el) => (el.onclick = () => {
    const x = bms.data.settings.find((s) => s.key === el.dataset.key);
    openEditor(x, "BMS", async (v) => {
      const { r: res, j } = await securePost(`/api/daly/${encodeURIComponent(bms.id)}/settings`, { key: x.key, value: v });
      if (!res.ok || !j.ok) throw new Error(j.error || `eroare ${res.status}`);
      bms.data.settings = j.settings;
      renderBms();
    });
  }));
  $("#bms-log").innerHTML = r.log?.length ? r.log.map((x) => `
    <div class="row ${x.ok ? "" : "critical"}"><span>${esc(x.setting)}: ${x.ok ? `${esc(x.old)} → <b>${esc(x.new)}</b>` : `EȘUAT — ${esc(x.error)}`}</span>
    <span class="t">${dayTime(x.ts)}</span></div>`).join("") : `<div class="empty">Nicio modificare până acum.</div>`;
}

/* ------------------------------------------------------------------ SETĂRI */
function renderLooks() {
  $("#looks").innerHTML = Object.entries(LOOKS).map(([k, L]) => `
    <button class="card look" data-look="${k}" aria-pressed="${k === state.look}" style="--lc:${L.c};--lg:${L.sw}">
      <span class="sw"></span><span><b>${L.name}</b><span>${L.desc}</span></span><span class="ck"></span></button>`).join("");
  $$("#looks .look").forEach((el) => (el.onclick = () => {
    setLook(el.dataset.look); savePref("look", state.look); renderLooks();
    $("#set-teme-sub").textContent = `Acum: ${LOOKS[state.look].name}`;
  }));
}
/* Setări → Dispozitive conectate: Invertoare și BMS-uri (grupate după tip, în ordinea adresei), cu legătura și starea */
const DEV_TYPE = { daly: "Daly", jk: "JK", sim_daly: "Daly (simulat)", seplos: "Seplos V2 (RS485)", pace: "PowerCube / PACE (RS485)", sim_seplos: "Seplos (simulat)",
  growatt_spf: "Growatt SPF", sim_growatt: "Growatt (simulat)" };
function devLink(info, d) {
  const live = d.reading?.raw?.link;                          // Daly: legătura găsită („Bluetooth AA:…”, „cablu …”)
  if (live) return live.replace(/\/dev\/serial\/by-id\//, "");
  if (info.ble) return `Bluetooth ${info.ble}`;
  const port = String(info.port || "");
  if (info.type === "daly" && (!port || port === "auto" || port.includes("REPLACE"))) return "se caută (cablu sau Bluetooth)";
  if (port.startsWith("/dev/")) return `${info.type.includes("seplos") || info.type === "pace" ? "RS485" : "cablu"} ${port.replace(/^.*\//, "")}`;
  return info.type.startsWith("sim_") ? "simulare" : "";
}
function devRow(x, devs, actions = false) {
  const d = devs[x.id] || {};
  const st = d.connected && !d.stale ? "comunică" : d.connected ? "nu răspunde acum" : "nu a comunicat încă";
  const cls = d.connected && !d.stale ? "ok" : d.connected ? "warning" : "";
  const addr = x.address == null ? "" : x.type === "daly" ? `adresa 0x${Number(x.address).toString(16).toUpperCase()}`
    : `adresa ${x.address}`;
  const parts = [addr, devLink(x, d), x.baudrate ? `${x.baudrate} baud` : "", x.capacity_ah ? `${num(x.capacity_ah, 0)} Ah` : ""];
  // în „Dispozitive inactive”: Daly cu poziție liberă → „Caută din nou”; legat (sau fix) → „Deconectează”
  const free = x.type === "daly" && dalyPos[x.id]?.free;
  const act = actions && x.kind === "pack" ? (free
    ? `<button class="btn sm" data-research="${esc(x.id)}">Caută din nou</button>`
    : `<button class="btn sm danger" data-disc="${esc(x.id)}">Deconectează</button>`) : "";
  return `<div class="row bms-row ${cls === "warning" ? "warning" : ""}"><span><b>${esc(x.name)}</b>
    <small>${esc((free ? ["poziție liberă — se caută singur", x.capacity_ah ? `${num(x.capacity_ah, 0)} Ah` : ""] : parts).filter(Boolean).join(" · "))}</small></span>
    <span class="bms-st ${cls}">${free ? "" : st}</span>${act ? `<span class="row-act">${act}</span>` : ""}</div>`;
}
function devSection(title, infos, devs, actions = false) {
  if (!infos.length) return "";
  const groups = {};
  for (const x of infos) (groups[x.type] ||= []).push(x);
  const many = Object.keys(groups).length > 1;
  return `<h2>${title}</h2>` + Object.entries(groups).map(([type, list]) => {
    list.sort((a, b) => (a.address ?? 0) - (b.address ?? 0) || a.name.localeCompare(b.name));
    return `${many || title === "BMS-uri" ? `<h3 class="bms-h">${esc(DEV_TYPE[type] || type)}</h3>` : ""}
      <div class="list">${list.map((x) => devRow(x, devs, actions)).join("")}</div>`;
  }).join("");
}
function renderDaly() {
  const all = state.info?.devices || [], devs = state.snap?.devices || {};
  const inv = all.filter((x) => x.kind === "inverter"), bms = all.filter((x) => x.kind === "pack");
  const n = inv.length + bms.length;
  // pe ecran doar cele care comunică acum; celelalte (configurate, dar tăcute) într-un meniu separat
  const on = (x) => devs[x.id]?.connected && !devs[x.id]?.stale;
  const ok = [...inv, ...bms].filter(on), off = [...inv, ...bms].filter((x) => !on(x));
  $("#set-daly-sub").textContent = n ? `${ok.length} comunică${off.length ? ` · ${off.length} inactive` : ""}`
    : "Niciun dispozitiv configurat";
  const oInv = inv.filter(on), oBms = bms.filter(on);
  $("#daly-list").innerHTML = ok.length ? devSection(oInv.length > 1 ? "Invertoare" : "Invertor", oInv, devs) + devSection("BMS-uri", oBms, devs)
    : `<div class="empty">${n ? "Niciun dispozitiv nu comunică acum." : "Niciun dispozitiv configurat. Adaugă-le cu butonul de mai jos."}</div>`;
  $("#dev-off-btn").hidden = !off.length;
  $("#dev-off-n").textContent = off.length === 1 ? "1 dispozitiv configurat care nu comunică" : `${off.length} dispozitive configurate care nu comunică`;
  const fInv = inv.filter((x) => !on(x)), fBms = bms.filter((x) => !on(x));
  $("#dev-off-list").innerHTML = devSection(fInv.length > 1 ? "Invertoare" : "Invertor", fInv, devs) + devSection("BMS-uri", fBms, devs, true);
  $$("#dev-off-list [data-disc]").forEach((b) => (b.onclick = () => devDisconnect(b.dataset.disc)));
  $$("#dev-off-list [data-research]").forEach((b) => (b.onclick = () => dalySearchNow(b)));
  $("#daly-auto-box").hidden = !bms.some((x) => x.type === "daly" && !on(x));
}
$("#daly-search").onclick = async () => {
  const b = $("#daly-search");
  b.disabled = true;
  b.textContent = "Caut… (până la un minut)";
  try { await api("/api/daly/search", { method: "POST" }); } catch {}
  [10, 25, 45, 70].forEach((s) => setTimeout(() => { if (!$("#set-devoff").hidden) loadDalyFound().then(renderDaly); }, s * 1000));
  setTimeout(() => { b.disabled = false; b.textContent = "Caută acum modulele Daly"; }, 30000);
};

/* ---------- Adaugă dispozitiv: ce e nou pe cablu (căutat singur) + căutare prin Bluetooth, apoi tipul BMS ---------- */
const da = { cable: null, ble: null, types: [], timer: 0 };
const DA_NAME = { daly: "Daly", jk: "JK", seplos: "Seplos", pace: "PowerCube", growatt_spf: "Invertor Growatt SPF" };
function devAddOpen() {
  da.ble = null;
  $("#da-ble-list").innerHTML = ""; $("#da-form").hidden = true;
  $("#da-ble-msg").textContent = "Un modul conectat acum la aplicația de pe telefon nu apare; închide-o înainte.";
  devScan("cable");
}
async function devScan(what) {
  try {
    const j = await api("/api/devices/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ what }) });
    devScanShow(j);
  } catch {}
}
function devScanShow(j) {
  clearTimeout(da.timer);
  const running = j.state === "running";
  $("#da-ble").disabled = running;
  $("#da-cable-again").hidden = running;
  if (j.what === "cable") {
    if (running) { $("#da-cable-msg").textContent = j.message || "Caut…"; $("#da-cable").innerHTML = ""; }
    else if (j.state === "failed") $("#da-cable-msg").textContent = j.message;
    else {
      da.cable = j.found || [];
      $("#da-cable-msg").textContent = da.cable.length ? "" : (j.ports || []).length
        ? "Niciun dispozitiv nou pe porturile USB libere." : "Niciun adaptor USB nou legat la Pi.";
      $("#da-cable").innerHTML = da.cable.map((f, i) => `<button class="row row-link" data-cable="${i}"><span><b>${esc(f.label)}</b></span>
        <span class="t">cablu ${esc(f.short)}${f.baudrate ? ` · ${f.baudrate} baud` : ""} · atinge ca să-l adaugi ›</span></button>`).join("");
      $$("[data-cable]").forEach((b) => (b.onclick = () => devForm({ ...da.cable[+b.dataset.cable] })));
    }
  } else {
    $("#da-ble").textContent = running ? "Caut… (10 s)" : da.ble ? "Caută din nou prin Bluetooth" : "Caută prin Bluetooth";
    if (j.state === "failed") $("#da-ble-msg").textContent = j.message;
    if (j.state === "done") {
      da.ble = j.found || []; da.types = j.types || [];
      $("#da-ble-msg").textContent = da.ble.length ? "Alege modulul pe care vrei să-l adaugi." : "N-am găsit niciun modul Bluetooth nou în jur.";
      $("#da-ble-list").innerHTML = da.ble.map((f, i) => `<button class="row row-link" data-ble="${i}"><span><b>${esc(f.name)}</b></span>
        <span class="t">${esc(f.ble)} · semnal ${f.rssi ?? "–"} dBm${f.guess ? ` · pare ${esc(DA_NAME[f.guess] || f.guess)}` : ""} ›</span></button>`).join("");
      $$("[data-ble]").forEach((b) => (b.onclick = () => devForm({ ...da.ble[+b.dataset.ble], link: "Bluetooth" })));
    }
  }
  if (running) da.timer = setTimeout(async () => { try { devScanShow(await api("/api/devices/scan")); } catch {} }, 1500);
  else if (j.what === "cable") $("#da-cable-again").hidden = false;
}
$("#da-ble").onclick = () => devScan("ble");
$("#da-cable-again").onclick = () => devScan("cable");

// formularul: la Bluetooth, întâi tipul BMS-ului; apoi numele și capacitatea
function devForm(c) {
  const f = $("#da-form"), ble = c.link === "Bluetooth";
  if (ble && !c.type) c.type = c.guess || "daly";
  const types = ble ? da.types : [];
  const pack = () => c.type !== "growatt_spf";
  const draw = () => {
    const t = types.find((x) => x.type === c.type);
    const ok = !ble || t?.supported;
    f.innerHTML = `<h3>${esc(ble ? c.name : c.label)}</h3>
      ${ble ? `<p class="note">Ce BMS este?</p><div class="da-types">${types.map((x) => `<label class="row-check ${x.supported ? "" : "dis"}">
        <input type="radio" name="da-type" value="${esc(x.type)}" ${x.type === c.type ? "checked" : ""}>
        <span><b>${esc(x.label)}</b>${x.supported ? "" : "<small>Nu e suportat încă — spune-i autorului ce model ai.</small>"}</span></label>`).join("")}</div>` : ""}
      ${ok ? `<label class="su-l" for="da-name">Nume</label><input class="ed-in" id="da-name" maxlength="40" placeholder="de ex. ${esc(DA_NAME[c.type] || "")} 4">
        ${pack() ? `<label class="su-l" for="da-cap">Capacitate (Ah)</label><input class="ed-in" id="da-cap" type="number" inputmode="decimal" min="1" max="2000" placeholder="de ex. 105">` : ""}
        <button class="btn ed-go" id="da-go">Adaugă dispozitivul</button>` : ""}
      <button class="btn sm" id="da-cancel">Renunță</button>
      <p class="note" id="da-msg"></p>`;
    $$('input[name="da-type"]', f).forEach((r) => (r.onchange = () => { c.type = r.value; draw(); }));
    $("#da-cancel").onclick = () => { f.hidden = true; };
    if ($("#da-go")) $("#da-go").onclick = () => devAdd(c);
  };
  draw();
  f.hidden = false;
  f.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
async function devAdd(c) {
  const cap = $("#da-cap") ? parseFloat(($("#da-cap").value || "").replace(",", ".")) : null;
  if ($("#da-cap") && !(cap >= 1 && cap <= 2000)) { $("#da-msg").textContent = "Scrie capacitatea, între 1 și 2000 Ah."; return; }
  const dev = { type: c.type, name: $("#da-name").value.trim(), capacity_ah: cap, port: c.port, address: c.address,
    baudrate: c.baudrate, protocol: c.protocol, ble: c.ble, ble_name: c.link === "Bluetooth" ? c.name : undefined };
  $("#da-go").disabled = true;
  try {
    const r = await fetch("/api/devices/add", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device: dev }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `eroare ${r.status}`);
    $("#da-msg").textContent = j.restart ? `${j.device.name} a fost adăugat. Amper repornește câteva secunde…` : `${j.device.name} a fost adăugat.`;
    if (j.restart) setTimeout(() => location.reload(), 8000);
  } catch (e) { $("#da-msg").textContent = e.message; $("#da-go").disabled = false; }
}

/* ---------- Deconectează (orice BMS), module excluse, ce a găsit căutarea Daly ---------- */
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 5000);
}
let dalyPos = {};                 // pozițiile Daly cu căutare automată: {id: {free, link, state}}
async function devDisconnect(id) {
  const d = (state.info?.devices || []).find((x) => x.id === id), name = d?.name || id;
  if (!confirm(`Sigur deconectezi ${name}? Amper nu-l mai citește și nu mai dă alerte pentru el.`)) return false;
  try {
    const { r, j } = await securePost(`/api/devices/${encodeURIComponent(id)}/disconnect`);
    if (!r.ok) throw new Error(j.error || "Nu a mers.");
    toast(`${name} a fost deconectat.${j.action === "freed" ? " Poziția rămâne liberă; căutarea poate găsi alt modul." : ""}`);
  } catch (e) { if (e.message !== "Anulat.") toast(e.message || "Pi-ul nu răspunde."); return false; }
  try { state.info = await api("/api/info"); } catch {}
  if ($("#sheet").open) $("#sheet").close();
  await loadDalyFound();
  renderDaly(); loadExcluded();
  if (state.view === "packs") renderPacks();
  return true;
}
$("#sh-disc").onclick = () => state.sheetDev && devDisconnect(state.sheetDev);

async function loadExcluded() {
  let r;
  try { r = await api("/api/devices/excluded"); } catch { return; }
  const items = r.items || [];
  $("#excl-btn").hidden = !items.length;
  $("#excl-n").textContent = items.length === 1 ? "1 modul nu se mai leagă singur" : `${items.length} module nu se mai leagă singure`;
  $("#excl-list").innerHTML = items.length ? items.map((x, i) => `<div class="row bms-row"><span><b>${esc(x.label || x.keys?.join(", "))}</b>
      <small>${esc(x.reason || "")}${x.at ? " · " + dateTime(x.at) : ""}</small></span>
      <span class="row-act"><button class="btn sm" data-allow="${i}">Permite din nou</button></span></div>`).join("")
    : `<div class="empty">Niciun modul exclus.</div>`;
  $("#excl-all").hidden = items.length < 2;
  $$("[data-allow]").forEach((b) => (b.onclick = () => allowAgain(+b.dataset.allow)));
}
async function allowAgain(index) {
  try { await api("/api/devices/excluded/allow", jsonPost(index == null ? {} : { index })); } catch {}
  loadExcluded();
}
$("#excl-all").onclick = () => allowAgain(null);

async function loadDalyFound() {
  let r;
  try { r = await api("/api/daly/found"); } catch { return; }
  dalyPos = Object.fromEntries((r.positions || []).map((p) => [p.id, p]));
  const f = (r.found || []).slice().reverse();
  $("#daly-found-h").hidden = $("#daly-found-note").hidden = !f.length;
  const val = (c) => [c.voltage != null ? `${num(c.voltage, 1)} V` : "", c.soc != null ? `SOC ${num(c.soc, 0)}%` : "",
    c.capacity_ah ? `${num(c.capacity_ah, 0)} Ah` : "fără capacitate", c.cells ? `${c.cells} celule` : "", c.cycles != null ? `${c.cycles} cicluri` : ""]
    .filter(Boolean).join(" · ");
  $("#daly-found").innerHTML = f.map((x, i) => `<div class="row bms-row ${x.ok ? "" : "warning"}"><span><b>${esc(x.label)}</b>
      <small>${esc(x.link)} · ${esc(val(x.check || {}))}</small>
      <small>${x.ok ? `legat la ${esc(dalyPos[x.device]?.name || x.device || "")}` : "nu pare BMS (egalizator?) — nelegat"}</small></span>
      <span class="row-act"><button class="btn sm danger" data-reject="${i}">Respinge</button></span></div>`).join("");
  $$("[data-reject]").forEach((b) => (b.onclick = async () => {
    const x = f[+b.dataset.reject];
    if (!confirm(`Respingi ${x.label}? Nu va mai fi legat automat${x.ok ? "; poziția lui devine liberă" : ""}.`)) return;
    try {
      const { r, j } = await securePost("/api/daly/reject", { keys: x.keys, binding: x.binding, label: x.label });
      if (!r.ok) throw new Error(j.error || "Nu a mers.");
    } catch (e) { if (e.message !== "Anulat.") toast(e.message); return; }
    try { state.info = await api("/api/info"); } catch {}
    loadDalyFound(); loadExcluded(); renderDaly();
  }));
}
function dalySearchNow(btn) {
  btn.disabled = true; btn.textContent = "Caut…";
  api("/api/daly/search", { method: "POST" }).catch(() => {});
  [10, 25, 45, 70].forEach((s) => setTimeout(() => { if (!$("#set-devoff").hidden) { loadDalyFound().then(renderDaly); } }, s * 1000));
  setTimeout(() => { btn.disabled = false; btn.textContent = "Caută din nou"; }, 30000);
}

/* meniul setărilor: butoane → secțiuni (Teme, Notificări, Module Daly) */
function showPanel(name) {
  $("#set-menu").hidden = !!name;
  for (const k of ["teme", "notif", "daly", "devoff", "devadd", "excl", "info", "update", "security", "remote", "auto", "about", "conn", "share", "wifi"])
    $("#set-" + k).hidden = k !== name;
  if (name === "about") $("#about-version").textContent = $("#version").textContent;
  if (name === "share") loadShare();
  if (name === "wifi") loadWifiNow();
  if (name === "auto") loadUserAlerts();
  if (name === "remote") renderRemote($("#remote-body"));
  if (name === "security") loadSecurity();
  clearInterval(state.infoTimer);
  clearTimeout(upd.timer);
  if (name === "update") loadUpdate();
  if (name === "info") { loadInfo(); state.infoTimer = setInterval(() => !$("#set-info").hidden && loadInfo(), 5000); }
  if (name === "notif") renderNotif();
  if (name === "daly" || name === "devoff") renderDaly();
  if (name === "devadd") devAddOpen();
  if (name === "daly" || name === "excl") loadExcluded();
  if (name === "devoff") loadDalyFound().then(renderDaly);
  window.scrollTo(0, 0);
}
$$(".set-btn[data-panel]").forEach((b) => (b.onclick = () => showPanel(b.dataset.panel)));
$("#set-wizard").onclick = () => openSetup(true);
$("#dev-add").onclick = () => showPanel("devadd");
async function loadWifiNow() {
  try {
    const r = await api("/api/wifi/status");
    const a = String(r.active || "").replace(/^wifi-/, "");
    $("#wifi-now").innerHTML = r.error ? esc(r.error) : a ? `Acum Pi-ul e conectat la <b>${esc(a)}</b>.` : "Pi-ul nu e conectat la nicio rețea WiFi (poate e pe cablu).";
  } catch {}
}

async function loadSecurity() {
  let r;
  try { r = await api("/api/security"); } catch { return; }
  $("#sec-devs").innerHTML = r.devices.length ? r.devices.map((d) => `<div class="row"><span><b>${esc(d.ua)}</b>${d.this ? " · acesta" : ""}</span>
      <span class="t">prima dată ${dateTime(d.first)} · ultima ${dateTime(d.last)} · ${esc(d.ip)}</span></div>`).join("")
    : `<div class="empty">Niciun dispozitiv încă (ecranul HDMI nu cere parola).</div>`;
  $("#sec-log").innerHTML = r.log.length ? r.log.map((x) => `<div class="row ${x.ok ? "" : "warning"}">
      <span>${x.ok ? "✓ reușit" : "✗ greșit"} · ${esc(x.note || "logare")}</span>
      <span class="t">${dateTime(x.ts)} · ${esc(x.ip)} · ${esc(x.ua)}</span></div>`).join("")
    : `<div class="empty">Nicio încercare.</div>`;
}
$$(".set-back").forEach((b) => (b.onclick = () => showPanel(b.dataset.back || null)));

/* ---------- Setări → Info: Pi-ul, rețeaua, porturile; schimbarea rețelei WiFi ---------- */
const bars4 = (sig) => "▂▄▆█".slice(0, Math.max(1, Math.min(4, Math.floor((sig || 0) / 25) + 1)));
function upFor(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} zile ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
async function loadInfo() {
  let x;
  try { x = await api("/api/system"); } catch { return; }
  if (x.error) { $("#info-body").innerHTML = `<p class="note">${esc(x.error)}</p>`; return; }
  const n = x.network || {}, w = n.wifi, ts = n.tailscale || {};
  const tempCls = x.cpu_temp >= 75 ? "off" : x.cpu_temp >= 65 ? "warnc" : "";
  const pw = x.power?.flags?.length ? `<span class="v off">${esc(x.power.flags.join("; "))}</span>`
    : x.power?.value ? `<span class="v on">OK</span>` : "–";
  const eth = (n.devices || []).find((d) => d.type === "ethernet");
  $("#info-body").innerHTML = `
    <div class="stats info-stats">
      <div><span class="k">Procesor</span><span class="v">${num(x.cpu_percent, 0)} %</span></div>
      <div><span class="k">Temperatură</span><span class="v ${tempCls}">${num(x.cpu_temp, 1)} °C</span></div>
      <div><span class="k">Memorie</span><span class="v">${num(x.memory?.used_mb, 0)} / ${num(x.memory?.total_mb, 0)} MB</span></div>
      <div><span class="k">Card</span><span class="v">${num(x.disk?.used_gb, 1)} / ${num(x.disk?.total_gb, 0)} GB</span></div>
      <div><span class="k">Pornit de</span><span class="v">${upFor(x.uptime_s)}</span></div>
      <div><span class="k">Alimentare</span>${pw}</div>
    </div>
    <p class="note">${esc(x.model)} · ${esc(x.hostname)} · încărcare ${esc((x.load || []).join(" / "))}</p>
    <h2>Rețea</h2>
    <div class="list">
      <button class="row row-link" id="info-wifi"><span>WiFi: <b class="lnk">${w ? esc(w.ssid) : "neconectat"}</b>${x.wifi_job?.hotspot ? " · hotspot Amper-Setup pornit" : ""}</span>
        <span class="t">${w ? `semnal ${bars4(w.signal)} ${w.signal}% · ${esc(w.freq)} · canal ${esc(w.channel)} · ` : ""}schimbă rețeaua ›</span></button>
      <div class="row"><span>Adrese locale</span><span class="t">${Object.entries(n.ip || {}).filter(([k]) => k !== "lo")
        .map(([k, v]) => `${esc(k.startsWith("tailscale") ? "server" : k)}: ${esc(v)}`).join(" · ") || "–"}</span></div>
      <div class="row"><span>Cablu de rețea</span><span class="t">${eth ? (eth.state === "connected" ? "legat" : "nelegat") : "–"}</span></div>
      <div class="row"><span>Server: <b>${ts.state === "Running" ? "conectat" : ts.state ? "neconectat" : "–"}</b></span>
        <span class="t">${esc(ts.ip || "")} · ${esc(ts.name || "")}</span></div>
    </div>
    ${(ts.peers || []).length ? `<h2>Aparatele tale legate la server</h2>
    <div class="list">${ts.peers.map((p) => `<div class="row"><span>${esc(p.name)}</span>
        <span class="t">${p.online ? "online" : "offline"}${p.os ? " · " + esc(p.os) : ""}</span></div>`).join("")}</div>` : ""}
    <h2>Porturi USB</h2>
    <div class="list">${(x.ports || []).map((p) => `<div class="row"><span>${esc(p.port)}</span>
      <span class="t">${esc(p.dev)} · ${p.used_by.length ? "folosit de " + esc(p.used_by.join(", ")) : "liber"}</span></div>`).join("")
      || `<div class="empty">Niciun adaptor USB-serial legat.</div>`}</div>
    <h2>Bluetooth</h2>
    <div class="list">${(x.bluetooth || []).map((b) => `<div class="row"><span>${esc(b.device)}</span>
      <span class="t">${esc(b.target)} · ${b.ok ? "conectat, citește" : b.connected ? "conectat, nu răspunde" : "neconectat"}</span></div>`).join("")
      || `<div class="empty">Niciun modul Bluetooth.</div>`}</div>
    <p class="note">Actualizat ${esc(x.time)}</p>`;
  $("#info-wifi").onclick = () => showPanel("wifi");
  $("#set-info-sub").textContent = `${num(x.cpu_temp, 0)} °C · ${w ? "WiFi " + w.ssid + " " + w.signal + "%" : "fără WiFi"}`;
}

const wifi = { chosen: null, nets: [] };
$("#wifi-scan").onclick = async () => {
  const b = $("#wifi-scan");
  b.disabled = true; b.textContent = "Caut…";
  try {
    const r = await api("/api/wifi/scan");
    if (r.error) throw new Error(r.error);
    wifi.nets = r.networks || [];
    const active = (r.active || "").replace(/^wifi-/, "");
    $("#wifi-list").innerHTML = wifi.nets.map((n, i) => `<button class="row wifi-net ${n.ssid === active ? "on" : ""}" data-i="${i}">
      <span>${esc(n.ssid)}${n.secure ? " 🔒" : ""}${n.ssid === active ? " · <b>acum</b>" : ""}</span>
      <span class="t">${bars4(n.signal)} ${n.signal}%</span></button>`).join("") || `<div class="empty">Nicio rețea găsită.</div>`;
    $$(".wifi-net").forEach((el) => (el.onclick = () => {
      wifi.chosen = wifi.nets[+el.dataset.i];
      $$(".wifi-net").forEach((x) => x.classList.toggle("sel", x === el));
      $("#wifi-form").hidden = false;
      $("#wifi-chosen").innerHTML = `Rețeaua aleasă: <b>${esc(wifi.chosen.ssid)}</b> (semnal ${wifi.chosen.signal}%)`;
      $("#wifi-pw").hidden = !wifi.chosen.secure;
      $("#wifi-pw").value = "";
    }));
  } catch (e) {
    $("#wifi-msg").textContent = e.message === "auth" ? "" : e.message;
  } finally {
    b.disabled = false; b.textContent = "Caută din nou";
  }
};
$("#wifi-go").onclick = async () => {
  const n = wifi.chosen;
  if (!n) return;
  if (!confirm(`Trec Pi-ul pe rețeaua „${n.ssid}”?\n\nDacă nu merge, revine singur la rețeaua de acum.`)) return;
  const b = $("#wifi-go");
  b.disabled = true;
  try {
    const { r, j } = await securePost("/api/wifi/connect", { ssid: n.ssid, password: $("#wifi-pw").value });
    if (!r.ok) throw new Error(j.error || `eroare ${r.status}`);
    $("#wifi-msg").textContent = `Mă conectez la „${n.ssid}”… legătura cu Pi-ul se poate întrerupe câteva secunde.`;
    const t0 = Date.now();
    const poll = async () => {               // urmărim rezultatul; în timpul schimbării Pi-ul poate să nu răspundă
      try {
        const st = await api("/api/wifi/status");
        if (st.state && st.state !== "connecting") {
          $("#wifi-msg").textContent = st.message;
          b.disabled = false; $("#wifi-form").hidden = st.state === "ok"; loadInfo();
          return;
        }
      } catch {}
      if (Date.now() - t0 < 180000) setTimeout(poll, 3000);
      else { $("#wifi-msg").textContent = "Nu primesc răspuns de la Pi. Verifică din nou peste câteva minute."; b.disabled = false; }
    };
    setTimeout(poll, 4000);
  } catch (e) {
    $("#wifi-msg").textContent = e.message;
    b.disabled = false;
  }
};

/* ---------- Setări → Actualizări (serviciul amper-update: GitHub, semnătură, copie a datelor, revenire) ---------- */
const UP_STEPS = { verify: "Verificarea semnăturii", space: "Spațiu liber", download: "Descărcare", check: "Verificarea arhivei",
  backup: "Copia de siguranță a datelor", install: "Instalare", restart: "Repornire", health: "Verificare: pornește?" };
const UP_RESULT = { ok: "reușit", failed: "eșuat", rolled_back: "revenit automat la versiunea veche" };
const upd = { data: null, timer: 0, busy: false };
const dateTime = (ts) => new Date(ts * 1000).toLocaleString("ro-RO", { day: "numeric", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit" });

async function loadUpdate() {
  clearTimeout(upd.timer);
  let r;
  try { r = await api("/api/update/status"); } catch (e) {
    if (e.message !== "auth") upd.timer = setTimeout(loadUpdate, 5000);     // serviciul se repornește
    return;
  }
  upd.data = r;
  if (r.job?.state === "running" && r.job.kind !== "download" && !state.updating) return startUpdateOverlay(r.job.kind);
  renderUpdate();
  if (r.job?.state === "running" && !$("#set-update").hidden) upd.timer = setTimeout(loadUpdate, 1000);
}
function renderUpdate() {
  const r = upd.data || {}, inst = r.installed || {};
  if (r.error && !r.version) {
    $("#up-now").innerHTML = `<div class="row"><span><b>Amper ${esc(r.running || state.info?.version || "")}</b></span>
      <span class="t">${esc(r.error)}. Actualizările din aplicație merg doar pe Pi-ul instalat cu install.sh.</span></div>`;
    $("#up-check").hidden = true;
    $("#up-beta-row").hidden = true;
    return;
  }
  $("#up-check").hidden = false;
  $("#up-beta-row").hidden = false;
  $("#up-beta").checked = r.channel === "beta";
  const last = r.last_check ? `${dateTime(r.last_check)} (${r.last_check_auto ? "automat" : "manual"})` : "niciodată";
  $("#up-now").innerHTML = `
    <div class="row"><span>Versiunea instalată: <b>${esc(r.version || "?")}</b>${r.channel === "beta" ? ' <span class="role">beta</span>' : ""}</span>
      <span class="t">${inst.at ? "instalată la " + dateTime(inst.at) : ""}</span></div>
    <div class="row"><span>Ultima căutare: ${esc(last)}</span>
      <span class="t">${r.check_error ? esc(r.check_error) : r.latest && !r.available ? "Ai ultima versiune." : ""}</span></div>`;
  $("#set-update-sub").textContent = r.available ? `Versiune nouă: ${r.latest.version}` : `Versiunea ${r.version || ""}`;
  const job = r.job || {}, running = job.state === "running";
  const dl = job.kind === "download", dlRun = running && dl, instRun = running && !dl;
  $("#up-new").hidden = !r.available || instRun || !r.can_install;
  // 1) „Descarcă și verifică” → 2) bara cu procente → 3) „Instalează acum”
  $("#up-dl").hidden = !!r.staged || dlRun;
  $("#up-prog").hidden = !dlRun;
  $("#up-ready").hidden = !r.staged || dlRun;
  if (dlRun) {
    // întreaga etapă pe o singură bară: semnătura și spațiul 0–5%, descărcarea 5–90%, verificarea 90–100%
    const p = job.progress ?? 0, base = { verify: 0, space: 4, download: 5, check: 90 }[job.step] ?? 0;
    const span = { download: 85, check: 10 }[job.step] ?? 0;
    const pct = Math.min(100, Math.round(base + span * p / 100));
    $("#up-bar-fill").style.width = `${pct}%`;
    $("#up-prog-pct").textContent = `${pct}%`;
    $("#up-prog-msg").textContent = (job.message || "").replace(/…\s*\d+%$/, "…");
  }
  if (r.staged) $("#up-ready-txt").textContent = `✓ Amper ${r.staged.version} e descărcat și verificat (semnătură validă).`;
  if (!running && dl && job.state === "failed") $("#up-msg").textContent = job.message;
  if (r.available) {
    $("#up-new-title").textContent = `Versiune nouă: ${r.latest.version}`;
    $("#up-notes").innerHTML = esc(r.latest.notes || "").replace(/^- /gm, "• ").replace(/\n/g, "<br>");
  }
  if (r.available && !r.can_install) $("#up-msg").textContent = "Versiunea instalată nu are cheia de verificare: actualizează manual o dată, cu install.sh.";
  const shown = instRun ? job : dl ? null : (r.last_job && r.last_job.finished && Date.now() / 1000 - r.last_job.finished < 600 ? r.last_job : null);
  $("#up-job").hidden = !shown;
  if (shown) {
    const steps = r.steps || Object.keys(UP_STEPS), cur = steps.indexOf(shown.step);
    const done = shown.state === "ok";
    $("#up-steps").innerHTML = steps.filter((k) => shown.kind !== "rollback" || !["download", "verify", "space"].includes(k)).map((k) => {
      const i = steps.indexOf(k);
      const mark = done || i < cur ? "✓" : i === cur ? (running ? "…" : shown.state === "ok" ? "✓" : "✗") : "";
      return `<div class="row ${i === cur && !running && !done ? "warning" : ""}"><span>${mark ? `<b>${mark}</b> ` : ""}${UP_STEPS[k]}</span></div>`;
    }).join("");
    $("#up-job-msg").textContent = shown.message || "";
  }
  $("#up-prev").hidden = !r.previous || running;
  if (r.previous) $("#up-prev-txt").textContent = `Revine la Amper ${r.previous.version || "anterior"}. Istoricul și setările rămân; se face totuși o copie înainte.`;
  const h = r.history || [];
  $("#up-hist").innerHTML = h.length ? h.map((x) => `<div class="row ${x.result === "ok" ? "" : "warning"}">
      <span><b>${x.kind === "rollback" ? "Revenire" : "Actualizare"} ${esc(x.from || "?")} → ${esc(x.version || "?")}</b> · ${UP_RESULT[x.result] || esc(x.result)}</span>
      <span class="t">${dateTime(x.at)}${x.result === "ok" ? "" : " · " + esc(x.message)}</span></div>`).join("")
    : `<div class="empty">Nicio actualizare făcută încă din aplicație.</div>`;
}
$("#up-check").onclick = async () => {
  const b = $("#up-check");
  b.disabled = true; b.textContent = "Caut actualizări…"; $("#up-msg").textContent = "";
  try {
    const r = await api("/api/update/check", { method: "POST" });
    upd.data = r; renderUpdate();
    $("#up-msg").textContent = r.error || (r.available ? "" : "Ai ultima versiune.");
  } catch (e) { if (e.message !== "auth") $("#up-msg").textContent = "Pi-ul nu răspunde."; }
  b.disabled = false; b.textContent = "Caută actualizări";
};
async function updateAction(action, btn) {
  const q = action === "install" ? `Instalez acum Amper ${upd.data?.latest?.version}? Amper se oprește un minut.`
    : `Revin la Amper ${upd.data?.previous?.version}? Amper se oprește un minut.`;
  if (!confirm(q)) return;
  btn.disabled = true; $("#up-msg").textContent = "";
  try {
    const { r, j } = await securePost(`/api/update/${action}`);
    if (!r.ok) $("#up-msg").textContent = j.error || "Nu a mers.";
    else startUpdateOverlay(action);
  } catch (e) { $("#up-msg").textContent = e.message === "Anulat." ? "" : e.message || "Pi-ul nu răspunde."; }
  btn.disabled = false;
}
$("#up-go").onclick = () => updateAction("install", $("#up-go"));
$("#up-dl").onclick = async () => {
  const b = $("#up-dl");
  b.disabled = true; $("#up-msg").textContent = "Pornesc descărcarea…";
  try {
    const r = await api("/api/update/download", { method: "POST" });
    $("#up-msg").textContent = r.error || "";
    setTimeout(loadUpdate, 300);
  } catch (e) { if (e.message !== "auth") $("#up-msg").textContent = "Pi-ul nu răspunde."; }
  b.disabled = false;
};
$("#up-back").onclick = () => updateAction("rollback", $("#up-back"));
$("#up-beta").onchange = async (e) => {
  const on = e.target.checked;
  try {
    const r = await api("/api/update/channel", jsonPost({ channel: on ? "beta" : "stable" }));
    if (r.error) throw new Error(r.error);
    upd.data = { ...upd.data, ...r }; renderUpdate();
    $("#up-msg").textContent = on ? "Primești și versiunile de test. Apasă „Caută actualizări acum”." : "Doar versiunile finale.";
  } catch (err) { e.target.checked = !on; if (err.message !== "auth") $("#up-msg").textContent = err.message; }
};

function renderNotif() {
  $("#notif-state").textContent = state.info?.notifications
    ? "Alertele se trimit și prin aplicația ntfy (rezervă), dacă e configurată."
    : "Notificările nu sunt configurate încă. Completează secțiunea notify din config.yaml.";
  renderPush();
}

function renderSettingsMenu() {
  $("#set-teme-sub").textContent = `Acum: ${LOOKS[state.look]?.name || ""}`;
  renderDaly();
  if (window.isSecureContext && "PushManager" in window && "serviceWorker" in navigator) {
    navigator.serviceWorker.ready.then((r) => r.pushManager.getSubscription()).then((sub) => {
      $("#set-notif-sub").textContent = sub && Notification.permission === "granted"
        ? "Pornite pe acest telefon" : "Oprite pe acest telefon";
    }).catch(() => {});
  } else {
    $("#set-notif-sub").textContent = "Alertele pe telefon";
  }
}

function setLook(k) {
  state.look = LOOKS[k] ? k : "cadrane";
  document.body.dataset.look = state.look;
  if (state.snap) renderNow();
}
$("#gear").onclick = () => show(state.view === "settings" ? "now" : "settings");

const RULE_ORDER = ["comm_lost", "charge_mos_off", "discharge_mos_off", "not_charging", "cell_low", "temp_high",
  "temp_low_charge", "bms_protection", "low_soc", "inverter_fault", "inverter_warning", "bms_alarm", "cell_high",
  "cell_delta", "soc_divergence"];
function alertOrder(a, b) {
  const l = (x) => (x.level === "critical" ? 0 : 1);
  return l(a) - l(b) || RULE_ORDER.indexOf(a.rule) - RULE_ORDER.indexOf(b.rule);
}

async function loadEnergy() {
  try {
    const e = await api("/api/energy?days=30");
    state.energy = e.rows || [];
    if (state.snap) renderNow();
    if (state.view === "hist") loadEnergyRange();
  } catch {}
}

const alertId = (a) => `${a.key || a.rule + "|" + a.device}|${a.since}`;
function seenAlerts() { try { return new Set(JSON.parse(localStorage.getItem("seenAlerts") || "[]")); } catch { return new Set(); } }
function markAlertsSeen() {
  const ids = (state.snap?.alerts || []).map(alertId);
  try { localStorage.setItem("seenAlerts", JSON.stringify(ids)); } catch {}
  renderBadge();
}
function renderBadge() {
  const seen = seenAlerts();
  const n = (state.snap?.alerts || []).filter((a) => !seen.has(alertId(a))).length;
  const b = $("#badge");
  if (n) b.dataset.n = n; else delete b.dataset.n;
}

/* ------------------------------------------------------------------ fișa modulului */
function openSheet(id) {
  state.sheetDev = id;
  renderSheet();
  $("#sheet").showModal();
}
$("#sh-close").onclick = () => $("#sheet").close();
$("#sheet").addEventListener("close", () => (state.sheetDev = null));
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") $("#sheet").close(); });

function renderSheet() {
  const d = state.snap?.devices?.[state.sheetDev];
  if (!d) return;
  const r = d.reading || {};
  const { cls, tag } = packStatus(d);
  $("#sh-title").textContent = d.name;
  const stEl = $("#sh-state");
  stEl.className = "state " + cls.split(" ")[0];
  stEl.textContent = d.stale ? `Nu comunică${d.error ? ": " + d.error : ""}` :
    ({ charging: "Se încarcă", discharging: "Se descarcă", idle: "În repaus" }[cls.split(" ")[0]] || "") + (tag ? `, ${tag}` : "");
  const mos = (v) => (v == null ? ["–", ""] : v ? ["pornit", "on"] : ["OPRIT", "off"]);
  const [cm, cmc] = mos(r.charge_mos), [dm, dmc] = mos(r.discharge_mos);
  const cells = r.cells || [];
  const stats = [
    ["Încărcare", `${num(r.soc, 1)} %`], ["Tensiune", `${num(r.voltage, 2)} V`], ["Curent", `${num(r.current, 1)} A`],
    ["Putere", watts(r.power, false)], ["Rămas", `${num(r.remaining_ah, 1)} Ah`], ["Capacitate", `${num(d.capacity_ah ?? r.capacity_ah, 0)} Ah`],
    ["MOSFET încărcare", cm, cmc], ["MOSFET descărcare", dm, dmc], ["Cicluri", r.cycles ?? "–"],
    ["Temperaturi", (r.temps || []).map((t) => num(t, 0)).join(" / ") + " °C"],
    ["Diferență celule", r.cell_delta == null ? "–" : `${num(r.cell_delta * 1000, 0)} mV`],
    ["Sănătate", r.soh == null ? "–" : `${num(r.soh, 0)} %`],
  ];
  $("#sh-stats").innerHTML = stats.map(([k, v, c]) => `<div><span class="k">${k}</span><span class="v ${c || ""}">${v}</span></div>`).join("");
  if (cells.length) {
    const lo = Math.min(...cells), hi = Math.max(...cells);
    const floor = Math.min(lo, 3.0) - 0.05, top = Math.max(hi, 3.45) + 0.02;
    $("#sh-cellnote").textContent = `min ${num(lo, 3)} V, max ${num(hi, 3)} V`;
    $("#sh-cells").innerHTML = cells.map((v, i) => {
      const h = ((v - floor) / (top - floor)) * 100;
      const c = cells.length > 1 && hi - lo > 0.005 ? (v === hi ? "hi" : v === lo ? "lo" : "") : "";
      return `<div class="cellbar ${c}"><div class="tube"><i style="height:${h}%"></i></div><b>${num(v, 3).slice(0, 5)}</b>${i + 1}</div>`;
    }).join("");
  } else {
    $("#sh-cellnote").textContent = "BMS-ul nu a trimis tensiunile pe celule.";
    $("#sh-cells").innerHTML = "";
  }
  $("#sh-alarms").innerHTML = (r.protections || []).map((a) => `<li class="prot">Protecție: ${esc(a)}</li>`).join("") +
    (r.alarms || []).map((a) => `<li>${esc(a)}</li>`).join("");
  $("#sh-upd").textContent = d.last_ok ? `Actualizat ${ago(d.last_ok)}` : "";
}

/* ------------------------------------------------------------------ navigare */
$$("nav.tabs button").forEach((b) => (b.onclick = () => show(b.dataset.view)));
function show(v) {
  state.view = v;
  const tab = ["packs", "inverter", "bms"].includes(v) ? "now" : v;      // se deschid din „Acum”
  $$("nav.tabs button").forEach((b) => b.dataset.view === tab ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  for (const k of ["now", "hist", "alerts", "packs", "inverter", "bms", "settings"]) $("#view-" + k).hidden = v !== k;
  $("#pvpop").hidden = true;
  if (v === "bms") loadBms();
  if (v === "packs") renderPacks();
  if (v === "inverter") { renderInverterLive(); loadInverter(); }
  if (v === "settings") { showPanel(null); renderSettingsMenu(); }
  window.scrollTo(0, 0);
  if (v === "hist") loadHistory();
  if (v === "alerts") { loadAlerts(); markAlertsSeen(); }
}

/* ------------------------------------------------------------------ ISTORIC */
$$("#range button").forEach((b) => (b.onclick = () => {
  $$("#range button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  state.hours = +b.dataset.h;
  loadHistory();
}));

async function loadHistory() {
  const devs = state.info.devices.filter((d) => connected(state.snap?.devices?.[d.id] || {}));
  const pk = devs.filter((d) => d.kind === "pack");
  const inv = devs.find((d) => d.kind === "inverter");
  const pts = Math.min(400, Math.round(innerWidth * 1.2));
  // toate graficele într-o singură cerere; până vin datele, „Se încarcă…”; o eroare nu lasă ecranul gol
  const req = ++state.histReq;
  const ids = [...pk, ...(inv ? [inv] : [])].map((d) => encodeURIComponent(d.id)).join(",");
  const key = `${state.hours}|${ids}`;
  // ultima încărcare a aceluiași interval se desenează imediat (din memorie sau de pe telefon); cea nouă vine după
  const cached = histCache.get(key);
  if (cached) drawHistory(cached.data, pk, inv);
  else $$("#charts .when").forEach((el) => (el.textContent = "Se încarcă…"));
  let data;
  try {
    data = await api(`/api/history/all?devices=${ids}&hours=${state.hours}&points=${pts}`);
  } catch (e) {
    if (e.message !== "auth" && req === state.histReq && !cached)
      $$("#charts .when").forEach((el) => (el.textContent = "Pi-ul nu a răspuns. Încearcă din nou."));
    return;
  }
  if (req !== state.histReq) return;                     // între timp s-a ales alt interval
  histCache.put(key, data);
  drawHistory(data, pk, inv);
}

/* graficele încărcate ultima dată, pe interval: în memorie și pe telefon (se deschid instant data viitoare) */
const histCache = {
  mem: new Map(),
  get(k) {
    if (this.mem.has(k)) return this.mem.get(k);
    try { const v = JSON.parse(localStorage.getItem("hist:" + k) || "null"); if (v) this.mem.set(k, v); return v; } catch { return null; }
  },
  put(k, data) {
    const v = { at: Date.now(), data };
    this.mem.set(k, v);
    try {
      for (const x of Object.keys(localStorage)) if (x.startsWith("hist:") && x !== "hist:" + k && x.split("|")[1] !== k.split("|")[1]) localStorage.removeItem(x);
      localStorage.setItem("hist:" + k, JSON.stringify(v));
    } catch {}
  },
};

function drawHistory(data, pk, inv) {
  const S = data.series || {}, rowsOf = (d) => S[d.id]?.rows || [];
  const ph = pk.map((d) => ({ rows: rowsOf(d) })), ih = inv ? { rows: rowsOf(inv) } : null;
  const ser = (rows, key) => rows.map((r) => [r.t, r[key]]).filter((p) => p[1] != null);
  lineChart($("#ch-soc"), pk.map((d, i) => ({ name: d.name, color: SERIES[i % SERIES.length], data: ser(ph[i].rows, "soc") })),
    { min: 0, max: 100, fmt: (v) => num(v, 0) + "%" });
  lineChart($("#ch-cur"), pk.map((d, i) => ({ name: d.name, color: SERIES[i % SERIES.length], data: ser(ph[i].rows, "current") })),
    { zero: true, fmt: (v) => num(v, 1) + " A" });
  const css = getComputedStyle(document.documentElement);
  if (ih) {
    // culori bine diferite: galben producție, roșu consum, verde rețea
    lineChart($("#ch-pow"), [
      { name: "Panouri", color: css.getPropertyValue("--sun").trim(), data: ser(ih.rows, "pv_power") },
      { name: "Consum", color: "#F0524B", data: ser(ih.rows, "load_power") },
      { name: "Rețea", color: "#3FCB7E", data: ser(ih.rows, "ac_in_power") },
    ], { zero: true, fmt: (v) => watts(v, false) });
  }
  drawEnergy();
}

/* ---------- ordinea graficelor, aleasă de utilizator (păstrată pe telefon) ---------- */
function applyChartOrder() {
  const box = $("#charts");
  const saved = loadPref("chartOrder", "").split(",").filter((id) => id && $("#" + id));
  for (const id of saved) box.appendChild($("#" + id));           // cele salvate primele, în ordinea lor
  for (const el of [...box.children]) if (!saved.includes(el.id)) box.appendChild(el);   // cele noi, la coadă
}
function saveChartOrder() {
  savePref("chartOrder", [...$("#charts").children].map((el) => el.id).join(","));
}
function moveChart(id, dir) {
  const el = $("#" + id), box = $("#charts");
  const sib = dir < 0 ? el.previousElementSibling : el.nextElementSibling;
  if (!sib) return;
  dir < 0 ? box.insertBefore(el, sib) : box.insertBefore(sib, el);
  saveChartOrder();
  el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
$("#hist-edit").onclick = () => {
  const box = $("#charts"), on = !box.classList.contains("editing");
  box.classList.toggle("editing", on);
  $("#hist-edit").textContent = on ? "Gata" : "Editează ordinea";
  $("#hist-edit-note").hidden = !on;
  for (const el of box.children) {
    el.querySelector(".mv")?.remove();
    if (on) {
      const mv = document.createElement("div");
      mv.className = "mv";
      mv.innerHTML = `<button aria-label="Mută mai sus">▲</button><button aria-label="Mută mai jos">▼</button>`;
      const [up, down] = mv.querySelectorAll("button");
      up.onclick = () => moveChart(el.id, -1);
      down.onclick = () => moveChart(el.id, 1);
      el.prepend(mv);
    }
  }
};
applyChartOrder();

/* ---------- energia pe zi, cu interval propriu (7 zile, 30 zile, 1 an sau de la–până la) ---------- */
const isoDay = (d) => d.toLocaleDateString("sv-SE");
const enRange = { days: 7, rows: [], from: null, to: null };

function enDates() {
  if (enRange.days !== "custom") {
    const to = new Date(), from = new Date();
    from.setDate(from.getDate() - enRange.days + 1);
    return [isoDay(from), isoDay(to)];
  }
  return [$("#en-from").value, $("#en-to").value].sort();
}

async function loadEnergyRange() {
  const [from, to] = enDates();
  if (!from || !to) return;
  try {
    const r = await api(`/api/energy?from=${from}&to=${to}`);
    Object.assign(enRange, { rows: r.rows || [], from, to });
    drawEnergy();
  } catch {}
}

function drawEnergy() {
  const css = getComputedStyle(document.documentElement);
  const { from, to } = enRange;
  if (!from) return loadEnergyRange();
  const byDay = new Map(enRange.rows.map((r) => [r.day, r]));
  // toate zilele din interval (cele fără date = 0); peste 62 de zile: totaluri pe lună
  const days = [];
  for (let d = new Date(from + "T12:00"); isoDay(d) <= to; d.setDate(d.getDate() + 1)) days.push(isoDay(d));
  const monthly = days.length > 62;
  const keys = [...new Set(days.map((d) => (monthly ? d.slice(0, 7) : d)))];
  const sum = (k, field) => days.filter((d) => (monthly ? d.slice(0, 7) : d) === k).reduce((a, d) => a + (byDay.get(d)?.[field] || 0), 0);
  const pv = keys.map((k) => sum(k, "pv_kwh")), load = keys.map((k) => sum(k, "load_kwh"));
  const grid = keys.map((k) => sum(k, "ac_in_kwh"));                    // intrarea AC a invertorului (rețea/generator)
  const tot = (a) => num(a.reduce((x, y) => x + y, 0), 1);
  $("#ch-energy h3").textContent = monthly ? "Energie pe lună (kWh)" : "Energie pe zi (kWh)";
  $("#en-total").innerHTML = `Total ${dayTime2(from)} – ${dayTime2(to)}: produs <b>${tot(pv)} kWh</b>` +
    ` · consumat <b>${tot(load)} kWh</b> · din rețea <b>${tot(grid)} kWh</b>`;
  barChart($("#ch-energy"), keys, [
    { name: "Produs", color: css.getPropertyValue("--sun").trim(), data: pv },
    { name: "Consumat", color: css.getPropertyValue("--load").trim(), data: load },
    { name: "Din rețea", color: "#3FCB7E", data: grid },
  ]);
}
const dayTime2 = (d) => new Date(d + "T12:00").toLocaleDateString("ro-RO", { day: "numeric", month: "short", year: "numeric" });

$$("#en-range button").forEach((b) => (b.onclick = () => {
  $$("#en-range button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  enRange.days = b.dataset.d === "custom" ? "custom" : +b.dataset.d;
  $("#en-custom").hidden = enRange.days !== "custom";
  if (enRange.days === "custom" && !$("#en-from").value) {
    const t = new Date(), f = new Date();
    f.setDate(f.getDate() - 29);
    $("#en-from").value = isoDay(f);
    $("#en-to").value = isoDay(t);
  }
  loadEnergyRange();
}));
$("#en-from").onchange = $("#en-to").onchange = loadEnergyRange;

/* ---------- grafice pe canvas (fără biblioteci) ---------- */
function setupCanvas(box) {
  const cv = box.querySelector("canvas");
  const dpr = devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(document.documentElement);
  return { cv, ctx, w, h, grid: css.getPropertyValue("--line").trim(), txt: css.getPropertyValue("--muted").trim(),
    font: `13px Barlow, system-ui, sans-serif` };
}

function niceStep(range, n) {
  const raw = range / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || raw;
}

function lineChart(box, series, opt = {}) {
  box._draw = (cursorX) => {
    const { cv, ctx, w, h, grid, txt, font } = setupCanvas(box);
    const all = series.flatMap((s) => s.data);
    const legend = box.querySelector(".legend");
    if (!all.length) {
      ctx.fillStyle = txt; ctx.font = font; ctx.fillText("Încă nu sunt date pentru acest interval.", 8, h / 2);
      legend.innerHTML = ""; return;
    }
    const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
    let lo = opt.min ?? Math.min(...all.map((p) => p[1])), hi = opt.max ?? Math.max(...all.map((p) => p[1]));
    if (opt.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
    const step = niceStep(hi - lo, 4);
    if (opt.min == null) lo = Math.floor(lo / step) * step;
    if (opt.max == null) hi = Math.ceil(hi / step) * step;
    const L = 46, R = 6, T = 8, B = 22;
    const X = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (w - L - R);
    const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (h - T - B);
    ctx.font = font; ctx.lineWidth = 1;
    for (let v = lo; v <= hi + step / 2; v += step) {
      ctx.strokeStyle = grid; ctx.globalAlpha = Math.abs(v) < step / 1000 && opt.zero ? 1 : 0.55;
      ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(w - R, Y(v)); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = txt; ctx.textAlign = "right";
      ctx.fillText(shortVal(v, opt), L - 6, Y(v) + 4);
    }
    const span = t1 - t0;
    const lbl = span > 2 * 86400 ? (t) => new Date(t * 1000).toLocaleDateString("ro-RO", { day: "numeric", month: "short" }) : hhmm;
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) { const t = t0 + (span * i) / 4; ctx.fillText(lbl(t), Math.min(w - 24, Math.max(L + 16, X(t))), h - 5); }
    ctx.lineWidth = 2; ctx.lineJoin = "round";
    // zona de sub linie, plină până jos (sau până la zero, la grafice cu valori negative)
    const base = Y(opt.zero ? 0 : lo);
    // linia se rupe doar la o pauză reală: mult mai mare decât distanța obișnuită dintre puncte (pe 30 de zile
    // punctele sunt la ~1 h 45 min; cu doar 2 zile de date, pragul span/60 le rupea pe toate)
    const diffs = series.flatMap((s) => s.data.slice(1).map((p, i) => p[0] - s.data[i][0])).sort((a, b) => a - b);
    const typical = diffs.length ? diffs[Math.floor(diffs.length / 2)] : 0;
    const gapLimit = Math.max(900, span / 60, typical * 3);
    const segments = (s) => {
      const out = [];
      let prev = null;
      for (const p of s.data) {
        if (prev == null || p[0] - prev > gapLimit) out.push([]);
        out[out.length - 1].push(p);
        prev = p[0];
      }
      return out;
    };
    for (const s of series) {
      ctx.fillStyle = s.color; ctx.globalAlpha = series.length > 3 ? 0.07 : series.length > 1 ? 0.14 : 0.22;
      for (const seg of segments(s)) {
        ctx.beginPath(); ctx.moveTo(X(seg[0][0]), base);
        for (const [t, v] of seg) ctx.lineTo(X(t), Y(v));
        ctx.lineTo(X(seg[seg.length - 1][0]), base); ctx.closePath(); ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    for (const s of series) {
      ctx.strokeStyle = s.color;
      for (const seg of segments(s)) {
        ctx.beginPath();
        seg.forEach(([t, v], i) => (i ? ctx.lineTo(X(t), Y(v)) : ctx.moveTo(X(t), Y(v))));
        ctx.stroke();
      }
    }
    // cursor
    let tc = null;
    if (cursorX != null) {
      tc = t0 + ((cursorX - L) / (w - L - R)) * span;
      tc = Math.max(t0, Math.min(t1, tc));
      ctx.strokeStyle = txt; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(X(tc), T); ctx.lineTo(X(tc), h - B); ctx.stroke();
    }
    const valAt = (s) => {
      if (!s.data.length) return null;
      if (tc == null) return s.data[s.data.length - 1][1];
      let best = s.data[0];
      for (const p of s.data) if (Math.abs(p[0] - tc) < Math.abs(best[0] - tc)) best = p;
      return best[1];
    };
    legend.innerHTML = series.map((s) => `<span style="--c:${s.color}">${esc(s.name)}<b>${opt.fmt(valAt(s))}</b></span>`).join("");
    box.querySelector(".when").textContent = tc == null ? "ultima valoare" : dayTime(tc);
  };
  attachCursor(box);
  box._draw(null);
}

function shortVal(v, opt) {
  if (opt.fmt && opt.max === 100) return num(v, 0);
  const a = Math.abs(v);
  return a >= 1000 ? num(v / 1000, a % 1000 ? 1 : 0) + "k" : num(v, a < 10 && v % 1 ? 1 : 0);
}

function barChart(box, labels, series) {
  box._draw = (cursorX) => {
    const { ctx, w, h, grid, txt, font } = setupCanvas(box);
    const legend = box.querySelector(".legend");
    if (!labels.length) { ctx.fillStyle = txt; ctx.font = font; ctx.fillText("Încă nu sunt date.", 8, h / 2); legend.innerHTML = ""; return; }
    const hiRaw = Math.max(1, ...series.flatMap((s) => s.data));
    const step = niceStep(hiRaw, 4), hi = Math.ceil(hiRaw / step) * step;
    const L = 34, R = 4, T = 8, B = 22;
    const gw = (w - L - R) / labels.length, bw = Math.max(2, (gw * 0.72) / series.length);
    const Y = (v) => T + (1 - v / hi) * (h - T - B);
    ctx.font = font;
    for (let v = 0; v <= hi + step / 2; v += step) {
      ctx.strokeStyle = grid; ctx.globalAlpha = 0.55; ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(w - R, Y(v)); ctx.stroke();
      ctx.globalAlpha = 1; ctx.fillStyle = txt; ctx.textAlign = "right"; ctx.fillText(num(v, 0), L - 6, Y(v) + 4);
    }
    let sel = labels.length - 1;
    if (cursorX != null) sel = Math.max(0, Math.min(labels.length - 1, Math.floor((cursorX - L) / gw)));
    labels.forEach((d, i) => {
      series.forEach((s, k) => {
        ctx.fillStyle = s.color; ctx.globalAlpha = i === sel ? 1 : 0.6;
        const x = L + i * gw + gw * 0.14 + k * bw;
        ctx.fillRect(x, Y(s.data[i] || 0), bw - 1, Y(0) - Y(s.data[i] || 0));
      });
      ctx.globalAlpha = 1;
    });
    ctx.fillStyle = txt; ctx.textAlign = "center";
    const every = Math.ceil(labels.length / 6);
    // etichete: zile (AAAA-LL-ZZ) sau luni (AAAA-LL), când intervalul e lung
    const monthly = labels[0].length === 7;
    const short = (d) => monthly ? new Date(d + "-01").toLocaleDateString("ro-RO", { month: "short" }) : d.slice(8) + "." + d.slice(5, 7);
    labels.forEach((d, i) => { if ((labels.length - 1 - i) % every === 0) ctx.fillText(short(d), L + i * gw + gw / 2, h - 5); });
    legend.innerHTML = series.map((s) => `<span style="--c:${s.color}">${esc(s.name)}<b>${num(s.data[sel], 1)} kWh</b></span>`).join("");
    box.querySelector(".when").textContent = monthly
      ? new Date(labels[sel] + "-01").toLocaleDateString("ro-RO", { month: "long", year: "numeric" })
      : new Date(labels[sel]).toLocaleDateString("ro-RO", { weekday: "long", day: "numeric", month: "long" });
  };
  attachCursor(box);
  box._draw(null);
}

function attachCursor(box) {
  if (box._attached) return;
  box._attached = true;
  const cv = box.querySelector("canvas");
  const at = (e) => e.clientX - cv.getBoundingClientRect().left;
  cv.addEventListener("pointermove", (e) => box._draw(at(e)));
  cv.addEventListener("pointerdown", (e) => box._draw(at(e)));
  cv.addEventListener("pointerleave", () => box._draw(null));
}
let rz;
addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => $$(".chart").forEach((b) => b._draw && !b.closest("[hidden]") && b._draw(null)), 150); });

/* ------------------------------------------------------------------ ALERTE */
function renderActiveAlerts() {
  const a = state.snap?.alerts || [];
  $("#al-active").innerHTML = a.length ? a.map((x) => `
    <div class="row ${x.level} ${openAlerts.has("a" + x.message) ? "open" : ""}">${alertText(x.message, "a" + x.message)}<span class="t">de ${x.since ? ago(x.since).replace("acum ", "") : ""}</span></div>`).join("")
    : `<div class="empty">Nicio alertă. Toate modulele funcționează normal.</div>`;
}
async function loadAlerts() {
  renderActiveAlerts();
  const r = await api("/api/alerts?limit=60");
  const closed = r.history.filter((x) => x.ts_end);
  $("#al-hist").innerHTML = closed.length ? closed.map((x) => `
    <div class="row ${x.level} closed ${openAlerts.has("h" + x.id) ? "open" : ""}">${alertText(x.message, "h" + x.id)}
    <span class="t">${dayTime(x.ts_start)} – ${hhmm(x.ts_end)}</span></div>`).join("")
    : `<div class="empty">Nu au fost alerte până acum.</div>`;
}

/* ---------- notificări direct în aplicația Amper (Web Push) ---------- */
const b64ToBytes = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - s.length % 4) % 4)), (c) => c.charCodeAt(0));
const jsonPost = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

async function renderPush() {
  const st = $("#push-state"), b = $("#push-on");
  $("#push-row").hidden = true;
  const HTTPS_URL = esc(state.info?.public_url || "adresa https a Pi-ului");
  if (!window.isSecureContext) {
    st.innerHTML = `Ca să primești notificări <b>direct în aplicația Amper</b>, deschide-o de la adresa
      <a href="${HTTPS_URL}">${HTTPS_URL}</a> și adaug-o pe ecranul principal (Partajare → Adaugă pe ecranul principal).`;
    return;
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    st.textContent = "Pe iPhone, notificările merg doar din aplicația Amper de pe ecranul principal: în Safari, " +
      "Partajare → Adaugă pe ecranul principal, apoi deschide Amper de pe ecranul principal și revino aici.";
    return;
  }
  const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
  if (Notification.permission === "denied") {
    st.textContent = "Notificările sunt blocate pentru Amper. Pornește-le din Setări iPhone → Notificări → Amper.";
    return;
  }
  $("#push-row").hidden = false;
  b.dataset.on = sub && Notification.permission === "granted" ? "1" : "";
  b.checked = !!b.dataset.on;
  st.textContent = b.dataset.on ? "Acest telefon primește notificările direct în aplicația Amper." :
    "Acest telefon nu primește încă notificări în aplicația Amper.";
}

$("#push-on").onclick = async () => {
  const b = $("#push-on");
  b.disabled = true;
  try {
    if (b.dataset.on) {
      const s = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
      if (s) { await api("/api/push/unsubscribe", jsonPost({ endpoint: s.endpoint })); await s.unsubscribe(); }
    } else {
      await subscribePush();
      $("#test-res").textContent = "Gata. Apasă „Trimite o notificare de test” ca să verifici.";
    }
    await renderPush();
  } catch (e) {
    if (e.message !== "auth") $("#push-state").textContent = e.message;
  } finally {
    b.disabled = false;
  }
};
async function subscribePush() {
  // întâi permisiunea, direct din apăsare (altfel iPhone-ul nu arată întrebarea)
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notificările nu au fost permise.");
  const { key, error } = await api("/api/push/key");
  if (!key) throw new Error(error || "Pi-ul nu are cheia pentru notificări.");
  const reg = await navigator.serviceWorker.ready;
  const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  const r = await api("/api/push/subscribe", jsonPost({ subscription: s.toJSON() }));
  if (!r.ok) throw new Error(r.error || "Abonarea a eșuat.");
}
$("#test-notif").onclick = async () => {
  const r = await api("/api/test-notification", { method: "POST" });
  $("#test-res").textContent = r.ok ? "Trimisă. Ar trebui să apară pe telefon în câteva secunde." : r.error;
};

/* ------------------------------------------------------------------ ALERTELE MELE */
const UA_TEMPLATES = [
  { t: "Bateria a scăzut sub 25%", metric: "bank_soc", op: "<", value: 25, hold: 1, level: "warning", clear: true },
  { t: "Bateria s-a încărcat complet", metric: "bank_soc", op: ">", value: 99, hold: 5, level: "info", clear: false,
    name: "Bateria s-a încărcat complet" },
  { t: "Bateria aproape goală (sub 15%)", metric: "bank_soc", op: "<", value: 15, hold: 1, level: "critical", clear: true },
  { t: "O celulă a coborât sub 3,00 V", metric: "cell_min", op: "<", value: 3.0, hold: 1, level: "warning", clear: true },
  { t: "Consum mare (peste 3000 W)", metric: "load_power", op: ">", value: 3000, hold: 2, level: "info", clear: false },
  { t: "Rețeaua / generatorul a pornit", metric: "grid_voltage", op: ">", value: 100, hold: 1, level: "info", clear: true,
    name: "Rețeaua / generatorul a pornit" },
  { t: "Module prea calde (peste 45 °C)", metric: "pack_temp_max", op: ">", value: 45, hold: 2, level: "warning", clear: true },
  { t: "Altceva (completez eu)", metric: "bank_soc", op: "<", value: 20, hold: 1, level: "warning", clear: true },
];
const ua = { metrics: [], rules: [] };
const LEVELS = { info: "🔔 informare", warning: "🟠 atenție", critical: "🔴 critic" };

async function loadUserAlerts() {
  try {
    const r = await api("/api/user-alerts");
    Object.assign(ua, r);
  } catch { return; }
  const unit = (m) => ua.metrics.find((x) => x.id === m)?.unit || "";
  const mname = (m) => ua.metrics.find((x) => x.id === m)?.name || m;
  $("#ua-list").innerHTML = ua.rules.length ? ua.rules.map((r) => `
    <div class="row ua-rule ${r.enabled ? "" : "off"}"><span><b>${esc(r.name || `${mname(r.metric)} ${r.op === "<" ? "sub" : "peste"} ${num(r.value, r.value % 1 ? 2 : 0)} ${unit(r.metric)}`)}</b>
      <small>${esc(mname(r.metric))} ${r.op === "<" ? "<" : ">"} ${num(r.value, r.value % 1 ? 2 : 0)} ${esc(unit(r.metric))}${r.hold_s ? ` · cel puțin ${Math.round(r.hold_s / 60)} min` : ""} · ${LEVELS[r.level] || r.level}${r.notify_clear ? "" : " · fără „Rezolvat”"}</small></span>
      <span class="ua-act"><input type="checkbox" data-tog="${r.id}" ${r.enabled ? "checked" : ""} aria-label="Pornită">
      <button class="btn sm danger" data-del="${r.id}">Șterge</button></span></div>`).join("")
    : `<div class="empty">Nicio automatizare încă. Apasă „+ Adaugă automatizare”.</div>`;
  $$("[data-tog]", $("#ua-list")).forEach((b) => (b.onclick = async () => {
    const r = ua.rules.find((x) => x.id === +b.dataset.tog);
    await api(`/api/user-alerts/${r.id}`, jsonPost({ enabled: !r.enabled }));
    loadUserAlerts();
  }));
  $$("[data-del]", $("#ua-list")).forEach((b) => (b.onclick = async () => {
    if (!confirm("Ștergi această alertă?")) return;
    await api(`/api/user-alerts/${b.dataset.del}`, { method: "DELETE" });
    loadUserAlerts();
  }));
}

function uaApplyTemplate(i) {
  const t = UA_TEMPLATES[i];
  $("#ua-metric").value = t.metric; $("#ua-op").value = t.op; $("#ua-value").value = t.value;
  $("#ua-hold").value = t.hold; $("#ua-level").value = t.level; $("#ua-clear").checked = t.clear;
  $("#ua-name").value = t.name || "";
  uaPreview();
}
function uaPreview() {
  const m = ua.metrics.find((x) => x.id === $("#ua-metric").value) || {};
  $("#ua-unit").textContent = m.unit || "";
  const v = parseFloat(String($("#ua-value").value).replace(",", "."));
  const hold = +$("#ua-hold").value || 0;
  $("#ua-preview").innerHTML = isNaN(v) ? `<span class="err">Scrie o valoare.</span>` :
    `Primești ${LEVELS[$("#ua-level").value]} când <b>${esc(m.name || "")}</b> e ${$("#ua-op").value === "<" ? "sub" : "peste"}
     <b>${num(v, v % 1 ? 2 : 0)} ${esc(m.unit || "")}</b>${hold ? ` cel puțin ${hold} min` : ""}.`;
  $("#ua-save").disabled = isNaN(v);
}
$("#ua-add").onclick = async () => {
  if (!ua.metrics.length) await loadUserAlerts();
  $("#ua-tpl").innerHTML = UA_TEMPLATES.map((t, i) => `<option value="${i}">${esc(t.t)}</option>`).join("");
  $("#ua-metric").innerHTML = ua.metrics.map((m) => `<option value="${m.id}">${esc(m.name)} (${esc(m.unit)})</option>`).join("");
  $("#ua-err").textContent = "";
  uaApplyTemplate(0);
  $("#ua").showModal();
};
$("#ua-tpl").onchange = () => uaApplyTemplate(+$("#ua-tpl").value);
for (const id of ["#ua-metric", "#ua-op", "#ua-value", "#ua-hold", "#ua-level"]) $(id).oninput = $(id).onchange = uaPreview;
$("#ua-close").onclick = () => $("#ua").close();
$("#ua-save").onclick = async () => {
  const body = { metric: $("#ua-metric").value, op: $("#ua-op").value,
    value: parseFloat(String($("#ua-value").value).replace(",", ".")), hold_min: +$("#ua-hold").value || 0,
    level: $("#ua-level").value, notify_clear: $("#ua-clear").checked, name: $("#ua-name").value.trim() };
  try {
    const r = await api("/api/user-alerts", jsonPost(body));
    if (!r.ok) throw new Error(r.error || "Nu am putut salva alerta.");
    $("#ua").close();
    loadUserAlerts();
  } catch (e) {
    if (e.message !== "auth") $("#ua-err").textContent = e.message;
  }
};

/* ------------------------------------------------------------------ preferințe (pe dispozitiv) */
function loadPref(k, d) { try { return localStorage.getItem(k) || d; } catch { return d; } }
function savePref(k, v) { try { localStorage.setItem(k, v); } catch {} }

boot();

/* ------------------------------------------------------------------ GHIDUL DE CONFIGURARE */
const SU_STEPS = [
  { k: "name", t: "Numele instalației" }, { k: "password", t: "Parola aplicației" }, { k: "remote", t: "Acces de oriunde" },
  { k: "devices", t: "Dispozitivele" }, { k: "capacity", t: "Capacitățile" }, { k: "notif", t: "Notificări" },
];
const DEV_NAME = { daly: "Daly", jk: "JK", seplos: "Seplos", pace: "PowerCube", growatt_spf: "Invertor Growatt SPF" };
const su = { i: 0, d: null, chosen: null, keep: false, again: false, pollT: 0 };

// Pi-ul nu răspunde (Amper oprit sau blocat, Pi fără curent/internet): un mesaj în loc de ecran negru.
// Paznicul de pe Pi repornește Amper după 5 minute și, dacă o actualizare recentă e de vină, revine singur.
let bootDown = 0;
function showDown() {
  if (!bootDown) bootDown = Date.now();
  if (Date.now() - bootDown < 5000) return;              // o clipă (repornire): nu speria pe nimeni
  const min = Math.floor((Date.now() - bootDown) / 60000);
  $("#app").hidden = true; $("#setup").hidden = true; $("#login").hidden = false;
  $("#pw").hidden = true; $("#pwgo").hidden = true; $('label[for="pw"]').hidden = true;
  $("#pwerr").innerHTML = `<b>Amper nu răspunde${min ? ` de ${min} min` : ""}.</b> Reîncerc singur…<br><br>
    Dacă Pi-ul are curent și internet, Amper se repornește singur în câteva minute; după o actualizare care nu
    merge, Pi-ul revine singur la versiunea anterioară. Dacă durează mult, scoate alimentarea Pi-ului câteva
    secunde și pornește-l din nou.`;
}

// parola ștearsă cu fișierul de pe card, iar telefonul e pe adresa publică: parola nouă se alege doar de acasă
function showResetLocal(msg) {
  $("#app").hidden = true; $("#setup").hidden = true; $("#login").hidden = false;
  $("#pw").hidden = true; $("#pwgo").hidden = true; $('label[for="pw"]').hidden = true;
  $("#pwerr").textContent = msg;
}

async function openSetup(again, step, reset = false) {
  su.again = again; su.chosen = null; su.keep = false; su.reset = reset;
  su.i = su.start = Math.max(0, SU_STEPS.findIndex((x) => x.k === step));     // „+ Adaugă dispozitiv” → direct la Dispozitive
  try { su.d = await api("/api/setup"); } catch { return; }
  $("#app").hidden = true; $("#login").hidden = true; $("#setup").hidden = false;
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  suRender();
}
function suField(id, label, attrs = "") {
  return `<label class="su-l" for="${id}">${label}</label><input class="ed-in" id="${id}" ${attrs}>`;
}
function suRender() {
  const st = SU_STEPS[su.i], d = su.d;
  $("#su-step").textContent = `Pasul ${su.i + 1} din ${SU_STEPS.length}`;
  $("#su-dots").innerHTML = SU_STEPS.map((x, i) => `<i class="${i < su.i ? "done" : i === su.i ? "on" : ""}"></i>`).join("");
  $("#su-err").textContent = "";
  $("#su-back").hidden = (su.i === 0 && !su.again) || su.reset;
  $("#su-step").hidden = $("#su-dots").hidden = su.reset;
  $("#su-back").textContent = su.i === su.start ? "‹ Renunță" : "‹ Înapoi";
  $("#su-next").textContent = su.i === SU_STEPS.length - 1 ? "Gata" : "Mai departe";
  $("#su-next").disabled = false;
  const b = $("#su-body");
  if (st.k === "name") {
    b.innerHTML = `<h2>${su.again ? "Ghid de configurare" : "Bun venit în Amper!"}</h2>
      <p class="note">${su.again ? "Poți schimba orice pas; ce nu atingi rămâne cum e." :
        "Câțiva pași și Amper începe să-ți urmărească bateriile și invertorul."}</p>
      ${suField("su-name", "Cum se numește instalația?", `maxlength="60" placeholder="de ex. Casa de la munte" value="${esc(d.site_name)}"`)}
      <p class="note">Apare sus în aplicație și te ajută să deosebești instalațiile, dacă ai mai multe.</p>`;
  } else if (st.k === "password" && su.reset) {
    b.innerHTML = `<h2>Parolă nouă</h2>
      <p class="note">Parola a fost ștearsă cu fișierul de pe card. Alege una nouă (minim 8 caractere); telefoanele
        logate până acum o vor cere din nou.</p>
      ${suField("su-pw", "Parola nouă", 'type="password" autocomplete="new-password"')}
      ${suField("su-pw2", "Scrie-o încă o dată", 'type="password" autocomplete="new-password"')}`;
    $("#su-next").textContent = "Salvează";
  } else if (st.k === "password") {
    b.innerHTML = `<h2>Parola aplicației</h2>
      <p class="note">O vei scrie o singură dată pe fiecare telefon. E cerută din nou la schimbarea setărilor
        bateriilor, ale invertorului și la actualizări. Minim 8 caractere.</p>
      ${d.has_password ? `<p class="note"><b>Există deja o parolă.</b> Completează doar dacă vrei s-o schimbi.</p>
        ${suField("su-old", "Parola de acum", 'type="password" autocomplete="current-password"')}` : ""}
      ${suField("su-pw", d.has_password ? "Parola nouă" : "Alege parola", 'type="password" autocomplete="new-password"')}
      ${suField("su-pw2", "Scrie-o încă o dată", 'type="password" autocomplete="new-password"')}`;
  } else if (st.k === "remote") {
    b.innerHTML = `<h2>Acces de oriunde</h2><div id="su-ts"></div>
      <p class="note">Poți sări peste pasul ăsta („Mai departe”) și îl faci oricând din Setări → Acces de oriunde.</p>`;
    renderRemote($("#su-ts"));
  } else if (st.k === "devices") {
    suRenderDevices();
  } else if (st.k === "capacity") {
    const packs = (su.chosen || []).filter((c) => c.type !== "growatt_spf");
    b.innerHTML = `<h2>Numele și capacitățile</h2>
      <p class="note">Capacitatea fiecărui modul (Ah) — din ea se calculează SOC-ul total și energia rămasă.
        O găsești pe eticheta bateriei.</p>
      ${(su.chosen || []).map((c, i) => `<div class="card su-dev">
        ${suField(`su-n${i}`, `${esc(c.label || DEV_NAME[c.type])}${c.link ? ` · ${esc(c.link)}` : ""}`,
          `maxlength="40" value="${esc(c.name || "")}" placeholder="${esc(suDefaultName(c, i))}"`)}
        ${c.type === "growatt_spf" ? "" : suField(`su-c${i}`, "Capacitate (Ah)",
          `type="number" inputmode="decimal" min="1" max="2000" step="1" value="${c.capacity_ah || ""}"`)}</div>`).join("")}
      ${packs.length ? "" : `<p class="note">Niciun acumulator ales: nu e nimic de completat.</p>`}`;
  } else if (st.k === "notif") {
    const ok = window.isSecureContext && "PushManager" in window;
    b.innerHTML = `<h2>Notificări pe telefon</h2>
      <p class="note">Amper te anunță când un modul nu se mai încarcă, când un BMS oprește încărcarea, la baterie
        descărcată și la defectele invertorului.</p>
      ${ok ? `<button class="btn" id="su-push">Pornește notificările pe acest telefon</button>`
        : `<p class="note">Notificările direct în aplicație merg de la adresa https (accesul de oriunde). Le poți
           porni mai târziu din <b>Setări → Notificări</b>.</p>`}
      <p class="note" id="su-push-msg"></p>`;
    if (ok) $("#su-push").onclick = async () => {
      try {
        await subscribePush();
        await api("/api/test-notification", { method: "POST" });
        $("#su-push-msg").textContent = "Gata — ar trebui să primești acum o notificare de test.";
      } catch (e) { $("#su-push-msg").textContent = e.message; }
    };
  }
  const first = b.querySelector("input");
  if (first && st.k !== "capacity") first.focus();
}
function suCheck(c) {
  return [c.voltage != null ? `${num(c.voltage, 1)} V` : "", c.soc != null ? `SOC ${num(c.soc, 0)}%` : "",
    c.capacity_ah ? `${num(c.capacity_ah, 0)} Ah` : "fără capacitate", c.cells ? `${c.cells} celule` : "",
    c.cycles != null ? `${c.cycles} cicluri` : ""].filter(Boolean).join(" · ");
}
function suDefaultName(c, i) {
  const same = (su.chosen || []).filter((x) => x.type === c.type);
  return same.length > 1 || c.type !== "growatt_spf" ? `${DEV_NAME[c.type]} ${same.indexOf(c) + 1}` : DEV_NAME[c.type];
}
function suRenderDevices() {
  const d = su.d, sc = d.scan || {}, b = $("#su-body");
  const real = d.devices.filter((x) => !x.type.startsWith("sim_"));
  const found = sc.found || [];
  if (!su.chosen) su.chosen = null;
  b.innerHTML = `<h2>Dispozitivele</h2>
    <p class="note">Leagă acum la Pi cablurile USB (invertor, RS485, Daly) și pornește modulele Daly cu Bluetooth.
      Apoi caută. Căutarea durează 1–2 minute.</p>
    ${real.length ? `<div class="list">${real.map((x) => `<div class="row"><span><b>${esc(x.name)}</b></span>
      <span class="t">${esc(x.port || x.ble || "")}${x.capacity_ah ? ` · ${x.capacity_ah} Ah` : ""}</span></div>`).join("")}</div>
      <label class="row-check"><input type="checkbox" id="su-keep" ${su.keep ? "checked" : ""}>
        <span><b>Păstrează dispozitivele de acum</b><small>Nu schimb nimic la dispozitive.</small></span></label>` : ""}
    <button class="btn" id="su-scan" ${sc.state === "running" ? "disabled" : ""}>${sc.state === "running" ? "Caut…" : found.length ? "Caută din nou" : "Caută dispozitivele"}</button>
    <p class="note" id="su-scan-msg">${esc(sc.state === "running" ? sc.message : sc.state === "failed" ? sc.message : sc.ble_note || "")}</p>
    ${sc.state === "done" ? `<div class="list">${found.length ? found.map((f, i) => `<label class="row-check">
        <input type="checkbox" class="su-f" data-i="${i}" ${f.ok === false ? "" : "checked"}>
        <span><b>${esc(f.label)}</b><small>${esc(f.link)}${f.short ? " · " + esc(f.short) : ""}${f.rssi ? ` · semnal ${f.rssi} dBm` : ""}</small>
        ${f.check ? `<small>${esc(suCheck(f.check))}</small>` : ""}
        ${f.ok === false ? `<small class="warnc">Nu pare BMS (egalizator activ?) — nebifat.</small>` : ""}</span></label>
        ${f.binding ? `<button class="btn sm danger su-rej" data-rej="${i}">Respinge ${esc(f.name || "")}</button>` : ""}`).join("")
      : `<div class="empty">N-am găsit nimic. Verifică cablurile și că BMS-urile sunt treze (pornește un consumator), apoi caută din nou.</div>`}</div>` : ""}
    <p class="note">Ai module Daly care nu sunt încă legate? Adaugă-le acum: se vor găsi singure când apar
      (pe cablu sau Bluetooth).</p>
    <div class="su-add"><button class="btn sm" id="su-add-daly">+ Daly (se găsește singur)</button>
      <span class="note" id="su-add-n">${su.extraDaly ? `${su.extraDaly} adăugate` : ""}</span></div>`;
  $("#su-scan").onclick = async () => {
    su.keep = false;
    su.d.scan = await api("/api/setup/scan", { method: "POST" });
    suRenderDevices(); suPollScan();
  };
  $$(".su-rej").forEach((b) => (b.onclick = async () => {
    const f = found[+b.dataset.rej];
    if (!confirm(`Respingi ${f.name}? Nu va mai fi legat automat (se poate permite din nou din Setări).`)) return;
    try {
      const { r, j } = await securePost("/api/daly/reject", { keys: f.keys, binding: f.binding, label: f.label });
      if (!r.ok) throw new Error(j.error || "Nu a mers.");
      found.splice(+b.dataset.rej, 1); suRenderDevices();
    } catch (e) { if (e.message !== "Anulat.") $("#su-err").textContent = e.message; }
  }));
  $("#su-add-daly").onclick = () => { su.extraDaly = (su.extraDaly || 0) + 1; $("#su-add-n").textContent = `${su.extraDaly} adăugate`; };
  if ($("#su-keep")) $("#su-keep").onchange = (e) => (su.keep = e.target.checked);
  if (sc.state === "running") suPollScan();
}
function suPollScan() {
  clearTimeout(su.pollT);
  su.pollT = setTimeout(async () => {
    try { su.d.scan = await api("/api/setup/scan"); } catch {}
    if (SU_STEPS[su.i].k !== "devices") return;
    if (su.d.scan.state === "running") { $("#su-scan-msg").textContent = su.d.scan.message; suPollScan(); }
    else suRenderDevices();
  }, 1500);
}
async function suNext() {
  const st = SU_STEPS[su.i];
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `eroare ${r.status}`);
    return j;
  };
  if (st.k === "name") {
    await post("/api/setup/name", { name: $("#su-name").value });
    su.d.site_name = $("#su-name").value.trim();
  } else if (st.k === "password") {
    const pw = $("#su-pw").value, pw2 = $("#su-pw2").value;
    if (su.d.has_password && !pw && !pw2) { /* păstrează parola */ }
    else {
      if (pw.length < 8) throw new Error("Parola trebuie să aibă cel puțin 8 caractere.");
      if (pw !== pw2) throw new Error("Parolele nu sunt la fel.");
      await post("/api/setup/password", { password: pw, repeat: pw2, old: $("#su-old")?.value || "" });
      su.d.has_password = true;
      pwCache.v = pw; pwCache.until = Date.now() + 5 * 60000;
      if (su.reset) { location.reload(); return; }
    }
  } else if (st.k === "devices") {
    if (su.keep) { su.chosen = null; su.i += 2; return suRender(); }       // sari peste capacități
    const found = su.d.scan?.found || [];
    const sel = $$(".su-f").filter((x) => x.checked).map((x) => ({ ...found[+x.dataset.i] }));
    for (let k = 0; k < (su.extraDaly || 0); k++) sel.push({ type: "daly", link: "se găsește singur", label: "Daly" });
    if (!sel.length) {
      if (su.d.devices.some((x) => !x.type.startsWith("sim_")))
        throw new Error("Alege dispozitivele găsite sau bifează „Păstrează dispozitivele de acum”.");
      if (!confirm("N-ai ales niciun dispozitiv. Continui fără? Le poți adăuga mai târziu din Setări → Ghid de configurare.")) return;
      su.chosen = []; su.i += 2; return suRender();
    }
    // ordinea: Daly, Seplos, invertor
    const order = { daly: 0, jk: 1, seplos: 2, pace: 3, growatt_spf: 4 };
    su.chosen = sel.sort((a, b) => order[a.type] - order[b.type]);
  } else if (st.k === "capacity") {
    su.chosen.forEach((c, i) => {
      c.name = $(`#su-n${i}`).value.trim() || suDefaultName(c, i);
      if (c.type !== "growatt_spf") c.capacity_ah = parseFloat(($(`#su-c${i}`).value || "").replace(",", "."));
    });
    const bad = su.chosen.find((c) => c.type !== "growatt_spf" && !(c.capacity_ah >= 1 && c.capacity_ah <= 2000));
    if (bad) throw new Error(`Completează capacitatea pentru ${bad.name} (între 1 și 2000 Ah).`);
    await post("/api/setup/devices", { devices: su.chosen });
  } else if (st.k === "notif") {
    const j = await post("/api/setup/finish", {});
    $("#su-body").innerHTML = `<h2>Gata!</h2><p class="note">${j.restart
      ? "Amper repornește cu dispozitivele alese (cam 10 secunde)…" : "Deschid aplicația…"}</p>`;
    $(".su-nav").hidden = true;
    const go = () => location.replace(location.pathname);
    if (!j.restart) return setTimeout(go, 800);
    const t0 = Date.now();
    const wait = async () => {
      await new Promise((r) => setTimeout(r, 3000));
      try { const r = await fetch("/api/health", { cache: "no-store" }); if (r.ok && Date.now() - t0 > 4000) return go(); } catch {}
      if (Date.now() - t0 < 90000) wait(); else go();
    };
    return wait();
  }
  su.i++;
  suRender();
}
$("#su-next").onclick = async () => {
  const b = $("#su-next");
  b.disabled = true;
  try { await suNext(); } catch (e) { $("#su-err").textContent = e.message === "auth" ? "" : e.message; }
  b.disabled = false;
};
$("#su-back").onclick = () => {
  if (su.i === su.start) { $("#setup").hidden = true; $("#app").hidden = false; return; }   // reluat din Setări: renunță
  su.i -= SU_STEPS[su.i].k === "notif" && !su.chosen ? 2 : 1;
  suRender();
};

/* atingerea unei notificări cu aplicația deja deschisă: service worker-ul trimite adresa */
navigator.serviceWorker?.addEventListener("message", (e) => {
  const u = String(e.data?.open || "");
  if ($("#app").hidden) return;
  if (u.includes("v=alerts")) show("alerts");
  if (u.includes("v=update")) { show("settings"); showPanel("update"); }
});

/* ---------- ecranul „Actualizez Amper”: cercul se umple după pașii serviciului de actualizare; cât Amper
   repornește, serverul nu răspunde (normal); aplicația se reîncarcă singură când versiunea nouă răspunde. */
const UPOV_PCT = { verify: 4, space: 7, download: 10, check: 55, backup: 62, install: 75, restart: 86, health: 93 };
const UPOV_MSG = { verify: "Verific semnătura…", space: "Verific spațiul liber…", download: "Descarc…",
  check: "Verific arhiva…", backup: "Fac copia de siguranță a datelor…", install: "Instalez versiunea nouă…",
  restart: "Repornesc Amper…", health: "Verific că pornește…" };
function upovSet(pct, msg) {
  pct = Math.max(state.upPct || 0, Math.min(100, Math.round(pct)));     // cercul doar înaintează
  state.upPct = pct;
  $("#upov-fill").style.transform = `translateY(${294 * (1 - pct / 100)}px)`;   // bateria se umple de jos în sus
  $("#upov-pct").textContent = `${pct}%`;
  if (msg) $("#upov-msg").textContent = msg;
}
function startUpdateOverlay(kind) {
  state.updating = true; state.upPct = 0;
  const from = state.info?.version || upd.data?.version;
  $("#upov-title").textContent = kind === "rollback" ? "Revin la versiunea anterioară" : "Actualizez Amper";
  $("#upov-close").hidden = true;
  $("#upov").hidden = false;
  upovSet(1, "Pornesc…");
  let down = 0;
  const tick = async () => {
    // 1) Amper răspunde cu altă versiune → gata
    try {
      const h = await (await fetch("/api/health", { cache: "no-store" })).json();
      if (h.version && from && h.version !== from) {
        upovSet(100, "Gata! Reîncarc aplicația…");
        return setTimeout(() => location.replace(location.pathname), 1200);
      }
    } catch {}
    // 2) pașii, de la serviciul de actualizare (prin Amper; nu răspunde cât Amper e oprit)
    try {
      const r = await (await fetch("/api/update/status", { credentials: "same-origin", cache: "no-store" })).json();
      const j = r.job?.state === "running" ? r.job : r.last_job || r.job || {};
      down = 0;
      if (j.state === "running") {
        const base = UPOV_PCT[j.step] ?? 0;
        const pct = j.step === "download" && j.progress != null ? 10 + j.progress * 0.45 : base;
        upovSet(pct, UPOV_MSG[j.step] || j.message);
      } else if (["failed", "rolled_back"].includes(j.state) && Date.now() / 1000 - (j.finished || 0) < 600) {
        $("#upov-title").textContent = j.state === "rolled_back" ? "Am revenit la versiunea veche" : "Actualizarea nu a reușit";
        $("#upov-msg").textContent = j.message;
        $("#upov-close").hidden = false;
        state.updating = false;
        return;
      } else if (j.state === "ok" && r.version && from && r.version !== from) {
        upovSet(100, "Gata! Reîncarc aplicația…");
        return setTimeout(() => location.replace(location.pathname), 1200);
      }
    } catch {
      // Amper e oprit/repornește: cercul înaintează încet, ca să se vadă că lucrează
      down++;
      upovSet(Math.max(state.upPct, 62) + (down < 40 ? 0.6 : 0), down > 3 ? "Repornesc Amper… (durează până la un minut)" : null);
      if (down > 150) {                                 // ~3 minute fără răspuns
        $("#upov-msg").textContent = "Pi-ul nu răspunde încă. Închide aplicația și redeschide-o peste un minut.";
        $("#upov-close").hidden = false;
      }
    }
    setTimeout(tick, 1200);
  };
  tick();
}
$("#upov-close").onclick = () => { $("#upov").hidden = true; state.updating = false; loadUpdate(); };

/* ------------------------------------------------------------------ ACCES DE ORIUNDE (Tailscale + Funnel; în aplicație: „server”) */
const remote = { timer: 0 };
async function renderRemote(el) {
  clearTimeout(remote.timer);
  if (!el || el.closest("[hidden]")) return;
  if (!el.innerHTML.trim()) el.innerHTML = `<p class="note">Verific conexiunea la server…</p>`;
  let r;
  try {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 25000);
    r = await api("/api/remote", { signal: ctl.signal }).finally(() => clearTimeout(t));
  } catch (e) {
    if (e.message === "auth") return;
    el.innerHTML = `<p class="err">${e.name === "AbortError" ? "Pi-ul nu a răspuns în 25 de secunde (serverul nu răspunde)."
      : "Pi-ul nu răspunde."}</p><button class="btn" id="rm-retry">Încearcă din nou</button>`;
    $("#rm-retry", el).onclick = () => { el.innerHTML = ""; renderRemote(el); };
    return;
  }
  const job = r.job || {}, waiting = job.state === "running";
  let h = "";
  if (r.error) h = `<p class="note">${esc(r.error)}. Accesul de oriunde se pornește doar pe Pi-ul instalat cu install.sh.</p>`;
  else if (!r.installed) h = `<p class="note">Programul de conectare la server nu e instalat pe acest Pi.</p>`;
  else if (r.state !== "Running") {
    h = `<p class="note">Ca să deschizi Amper și când nu ești acasă, Pi-ul se conectează la un <b>server</b> securizat
      (gratuit; te conectezi cu contul tău Google). Contul e al tău; nimeni altcineva nu are acces.</p>`;
    h += waiting && (job.url || r.auth_url)
      ? `<p class="note"><b>1.</b> Deschide pagina de mai jos și loghează-te (cu Google). <b>2.</b> Revino aici: pagina se
           actualizează singură.</p><a class="btn ed-go rm-link" href="${esc(job.url || r.auth_url)}" target="_blank" rel="noopener">Deschide pagina de conectare</a>
         <p class="note">Aștept logarea…</p>`
      : `<button class="btn ed-go" id="rm-login">Conectare la server</button>`;
  } else {
    h = `<div class="list"><div class="row"><span>✓ Conectat la server${r.account ? ` (${esc(r.account)})` : ""}</span>
      <span class="t">${esc(r.url || "")}</span></div></div>`;
    if (r.funnel) {
      h += `<p class="up-ok">✓ Accesul de oriunde e pornit.</p>
        <p class="note">Deschide pe orice telefon, <b>fără nicio aplicație în plus</b>:<br><b class="rm-url">${esc(r.url)}</b><br>
          apoi adaug-o pe ecranul principal (Partajare → Adaugă pe ecranul principal). Intrarea cere parola aplicației.</p>
        <button class="btn danger" id="rm-off">Oprește accesul de oriunde</button>`;
    } else if (waiting && job.kind === "funnel" && job.url) {
      h += `<p class="note">Serverul cere o singură dată aprobarea ta pentru adresa publică.
          Deschide pagina, apasă „Enable” / „Approve”, apoi revino aici.</p>
        <a class="btn ed-go rm-link" href="${esc(job.url)}" target="_blank" rel="noopener">Aprobă pe server</a>
        <p class="note">Aștept aprobarea…</p>`;
    } else {
      h += `<p class="note">Acum adresa merge doar pe telefoanele conectate la server (cu aplicația lui). <b>Accesul de
          oriunde</b> o face să meargă pe orice telefon, fără nicio aplicație în plus. Adresa devine publică: o protejează parola aplicației, blocarea după
          parole greșite și notificarea la fiecare dispozitiv nou.</p>
        <button class="btn ed-go" id="rm-on" ${waiting ? "disabled" : ""}>${waiting ? "Pornesc…" : "Pornește accesul de oriunde"}</button>`;
    }
  }
  if (job.state === "failed" && job.message) h += `<p class="err">${esc(job.message)}</p>`;
  el.innerHTML = h;
  const act = async (url, body) => {
    try {
      const { r: res, j } = await securePost(url, body);
      if (!res.ok) throw new Error(j.error || `eroare ${res.status}`);
    } catch (e) { if (e.message !== "Anulat.") alert(e.message); }
    renderRemote(el);
  };
  if ($("#rm-login", el)) $("#rm-login", el).onclick = () => act("/api/remote/login", {});
  if ($("#rm-on", el)) $("#rm-on", el).onclick = () => act("/api/remote/funnel", { on: true });
  if ($("#rm-off", el)) $("#rm-off", el).onclick = () =>
    confirm("Opresc accesul de oriunde? De la distanță, Amper se va deschide doar pe telefoanele conectate la server.") && act("/api/remote/funnel", { on: false });
  if (waiting) remote.timer = setTimeout(() => renderRemote(el), 2500);
}

/* ------------------------------------------------------------------ PARTAJEAZĂ AMPER (Setări → Despre) */
async function loadShare() {
  const out = [];
  const [rm, sys] = await Promise.all([api("/api/remote").catch(() => ({})), api("/api/system").catch(() => ({}))]);
  if (rm.url && rm.funnel)
    out.push({ t: "De oriunde", d: "Merge pe orice telefon, acasă sau oriunde, fără altă aplicație.", url: rm.url });
  else if (rm.url && rm.state === "Running")
    out.push({ t: "De oriunde (doar telefoanele conectate la server)", d: "Merge doar pe telefoanele cu aplicația serverului, în contul tău. " +
      "Pornește „Acces de oriunde” din Setări ca să meargă pentru oricine.", url: rm.url });
  const port = state.info?.port || 8080;
  const ips = Object.entries(sys.network?.ip || {}).filter(([k, ip]) => k !== "lo" && !k.startsWith("tailscale") &&
    !ip.startsWith("100.") && !ip.startsWith("10.42.") && !ip.startsWith("127."));
  for (const [, ip] of ips.slice(0, 2))
    out.push({ t: "Acasă", d: "Merge doar pe telefoanele conectate la rețeaua de acasă.", url: `http://${ip}:${port}` });
  if (!out.length) out.push({ t: "Adresa de acum", d: "Adresa la care ai deschis acum aplicația.", url: location.origin });
  $("#share-list").innerHTML = out.map((x, i) => `<div class="row share-row"><span><b>${esc(x.t)}</b>
      <small class="share-url">${esc(x.url)}</small><small>${esc(x.d)}</small></span>
      <span class="share-act"><button class="btn sm" data-share="${i}">Trimite</button>
      <button class="btn sm" data-copy="${i}">Copiază</button></span></div>`).join("");
  $$("[data-share]").forEach((b) => (b.onclick = async () => {
    const x = out[+b.dataset.share];
    const text = `Amper — bateriile și invertorul: ${x.url}`;
    if (navigator.share) { try { await navigator.share({ title: "Amper", text, url: x.url }); } catch {} }
    else copyText(x.url);
  }));
  $$("[data-copy]").forEach((b) => (b.onclick = () => copyText(out[+b.dataset.copy].url)));
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); }
  catch {                                        // fără https: varianta veche
    const ta = Object.assign(document.createElement("textarea"), { value: t });
    document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  $("#share-msg").textContent = `Copiat: ${t}`;
  setTimeout(() => ($("#share-msg").textContent = ""), 4000);
}

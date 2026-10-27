/* Service worker: aplicația se deschide instant și arată ultima versiune salvată dacă Pi-ul nu răspunde. */
const CACHE = "amper-v16";
const SHELL = ["/", "/app.css", "/app.js", "/manifest.webmanifest", "/icon.svg", "/icon-192.png",
  "/fonts/barlow-latin-400-normal.woff2", "/fonts/barlow-latin-600-normal.woff2",
  "/fonts/barlow-condensed-latin-600-normal.woff2", "/fonts/barlow-condensed-latin-700-normal.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // rețeaua întâi (ca să vină mereu versiunea nouă), cache-ul dacă Pi-ul nu răspunde. Un răspuns de eroare
  // (ex. 502 de la proxy-ul Tailscale cât Amper repornește) nu se afișează și nu se salvează: ar da ecran negru.
  const fromCache = () => caches.match(e.request)
    .then((c) => c || (e.request.mode === "navigate" ? caches.match("/") : undefined));
  e.respondWith(fetch(e.request).then(async (r) => {
    if (r.ok) {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return r;
    }
    return (await fromCache()) || r;
  }).catch(async () => (await fromCache()) || Response.error()));
});

/* Notificări trimise de Pi (Web Push): apar ca de la aplicația Amper. */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "Amper", {
    body: d.body || "", icon: "/icon-192.png", badge: "/icon-192.png", tag: d.tag || undefined,
    requireInteraction: d.priority === "high", data: { url: d.url || "/" },
  }));
});
// atingerea notificării deschide Amper la Alerte, unde se văd și detaliile
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => {
    const c = cs.find((x) => "focus" in x);
    if (!c) return self.clients.openWindow(url);
    c.postMessage({ open: url });
    return c.focus();
  }));
});

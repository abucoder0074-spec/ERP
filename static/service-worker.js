const cacheName = "erp-public-v1";
const offlinePage = "/offline/";

self.addEventListener("install", event => {
  event.waitUntil(caches.open(cacheName).then(cache =>
    cache.addAll([offlinePage, "/static/app.css"])
  ));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(key => key.startsWith("erp-public-") && key !== cacheName)
      .map(key => caches.delete(key)))
  ));
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(async () =>
      (await caches.match(offlinePage, { ignoreVary: true })) || Response.error()
    ));
  } else if (url.pathname === "/static/app.css") {
    event.respondWith(fetch(event.request).catch(async () =>
      (await caches.match(event.request)) || Response.error()
    ));
  }
});

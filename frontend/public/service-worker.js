const CACHE_NAME = "audioflow-pwa-v15";
const RUNTIME_CACHE = "audioflow-runtime-v14";
const CORE_ASSETS = [
  "/",
  "/?source=pwa&v=m",
  "/offline.html",
  "/manifest.webmanifest",
  "/runtime-env.js",
  "/favicon.svg",
  "/favicon.ico",
  "/apple-touch-icon.png",
  "/pwa/icon-72.png",
  "/pwa/icon-96.png",
  "/pwa/icon-128.png",
  "/pwa/icon-144.png",
  "/pwa/icon-152.png",
  "/pwa/icon-180.png",
  "/pwa/icon-192.png",
  "/pwa/icon-384.png",
  "/pwa/icon-512.png",
  "/pwa/maskable-icon-192.png",
  "/pwa/maskable-icon-512.png",
  "/assets/branding/logos/audioflow-mark.svg",
  "/assets/branding/logos/audioflow-logo-light.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.allSettled(CORE_ASSETS.map((asset) => cache.add(new Request(asset, {cache: "reload"}))))
    )
  );
  self.skipWaiting();
});

// 新版本就绪后由页面发消息触发 skipWaiting，旧版页面随后会自动刷新
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => ![CACHE_NAME, RUNTIME_CACHE].includes(key)).map((key) => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ok: false, error: "当前离线，接口不可用"}), {
          headers: {"Content-Type": "application/json"}
        })
      )
    );
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(async () => (await caches.match("/")) || (await caches.match("/offline.html")))
    );
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/pwa/") || url.pathname.startsWith("/platform-logos/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(RUNTIME_CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      }))
    );
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(RUNTIME_CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/offline.html")))
  );
});
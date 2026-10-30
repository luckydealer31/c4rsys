/* The service worker of c4rsys (ТЗ § 10.4.3): the application shell opens without connection,
 * so an employee who signed in on the device earlier can start a local draft of an inspection.
 *
 * - Only the built shell is cached: index.html and the hashed static files of the build.
 * - API answers (/api/…) are never cached here: personal data stay out of the HTTP cache; the
 *   lists needed offline are kept by the application in IndexedDB by account and company.
 * - Navigation goes to the network first, so a new release is picked up at once; the cached
 *   index.html is used only without connection.
 *
 * Built by vite.config.ts: PRECACHE and VERSION are filled in for every build. */
/* global self, caches, fetch, URL, Response */

const PRECACHE = __C4R_PRECACHE__;
const VERSION = __C4R_VERSION__;
const CACHE = `c4r-shell-${VERSION}`;
const SHELL = "/index.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll([SHELL, ...PRECACHE.map((path) => `/${path}`)]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("c4r-shell-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

async function navigation(request) {
  try {
    const response = await fetch(request);
    const type = response.headers.get("content-type") || "";
    if (response.ok && type.includes("text/html")) {
      const cache = await caches.open(CACHE);
      await cache.put(SHELL, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(SHELL);
    return cached || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

async function asset(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/sw.js") return;
  if (request.mode === "navigate") {
    event.respondWith(navigation(request));
    return;
  }
  // Hashed files of the build never change under the same name.
  if (url.pathname.startsWith("/assets/")) event.respondWith(asset(request));
});

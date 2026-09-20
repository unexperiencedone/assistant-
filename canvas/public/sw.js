/* Nova's service worker: enough to make the canvas an installed app, and nothing more.
 *
 * What it caches is the shell — the page, the hashed bundles, the fonts — so opening
 * Nova from the home screen paints instantly and says something sensible when the PC
 * is asleep. What it must NEVER cache is the assistant's own state: /api and /ws carry
 * the live snapshot and the commands you send, and a stale answer there would be a lie.
 *
 * It also stays out of the way of the token: every request is passed through with its
 * credentials, so the pairing cookie still decides what is allowed.
 */

const CACHE = "nova-shell-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icons/nova-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined) // an unpaired first load cannot prefetch; not fatal
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

/** Live state is never served from a cache. */
function isLive(url) {
  return url.pathname.startsWith("/api/") || url.pathname === "/ws";
}

/** Hashed bundles and self-hosted fonts never change under their own name. */
function isImmutable(url) {
  return url.pathname.startsWith("/assets/") || url.pathname.startsWith("/fonts/");
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || isLive(url)) return; // straight to the network

  if (isImmutable(url)) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
    return;
  }

  // The page itself: network first, so a rebuilt canvas is picked up straight away,
  // falling back to the cached shell only when the PC cannot be reached.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && request.mode === "navigate") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put("/index.html", copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("/index.html"))),
  );
});

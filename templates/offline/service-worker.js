{% load static %}
const CACHE_NAME = "ife-stage-9a-v2";
const OFFLINE_URL = "{% url 'offline:unavailable' %}";
const STATIC_SHELL = [
  OFFLINE_URL,
  "{% static 'css/app.css' %}",
  "{% static 'js/pwa-shell.js' %}",
  "{% static 'js/offline-sales.js' %}",
  "{% static 'img/ife-app-icon.svg' %}",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_SHELL)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)),
        ),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  if (STATIC_SHELL.includes(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request)),
    );
    return;
  }
  if (request.mode !== "navigate") {
    return;
  }
  if (url.pathname === "{% url 'offline:sales' %}") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (
            response.ok &&
            !response.redirected &&
            response.headers.get("X-Ife-Offline-Cache") === "private-shell"
          ) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() =>
          caches
            .match(request)
            .then((cached) => cached || caches.match(OFFLINE_URL)),
        ),
    );
    return;
  }
  event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
});

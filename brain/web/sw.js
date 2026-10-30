// Service worker : reçoit les notifications de Jarvis même quand la page est fermée.
// Aucune mise en cache : la page vient toujours du cerveau (pas de version périmée).

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  const title = typeof data.title === "string" ? data.title.slice(0, 80) : "Jarvis";
  const body = typeof data.body === "string" ? data.body.slice(0, 400) : "";
  event.waitUntil(self.registration.showNotification(title, {
    body,
    tag: typeof data.tag === "string" ? data.tag : "jarvis",
    renotify: true,
    icon: "icon.svg",
    badge: "icon.svg",
    data: { url: "./" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of all) {
      if ("focus" in client) return client.focus();
    }
    return self.clients.openWindow("./");
  })());
});

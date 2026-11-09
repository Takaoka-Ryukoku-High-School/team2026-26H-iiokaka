self.addEventListener("push", (event) => {
  let message = {};
  if (event.data) {
    try {
      message = event.data.json();
    } catch {
      message = { body: event.data.text() };
    }
  }

  event.waitUntil(
    self.registration.showNotification(message.title || "たべきりん", {
      body: message.body || "期限が近い商品があります。",
      tag: message.tag || "food-expiry",
      data: { url: message.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const destination = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const existing = windows.find((window) => window.url === destination);
      return existing ? existing.focus() : clients.openWindow(destination);
    }),
  );
});

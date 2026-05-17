const BINA_URL = 'https://my-ai-agent-production-5e17.up.railway.app';

self.addEventListener('install', function(event) { self.skipWaiting(); });
self.addEventListener('activate', function(event) { event.waitUntil(clients.claim()); });

self.addEventListener('push', function(event) {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'Bina';
    const options = {
        body: data.body || '',
        icon: '/icon-192.png',
        badge: '/icon-192.png',
        vibrate: [200, 100, 200],
        data: { url: data.url || BINA_URL, type: data.type || 'feed' }
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const type = event.notification.data.type;
    const target = type === 'email' ? BINA_URL + '?open=inbox' : BINA_URL + '?open=feed';
    event.waitUntil(
        clients.matchAll({type: 'window', includeUncontrolled: true}).then(function(clientList) {
            for (var i = 0; i < clientList.length; i++) {
                if (clientList[i].url.includes('my-ai-agent-production-5e17') && 'focus' in clientList[i]) {
                    clientList[i].postMessage({action: type === 'email' ? 'openInbox' : 'openFeed'});
                    return clientList[i].focus();
                }
            }
            return clients.openWindow(target);
        })
    );
});

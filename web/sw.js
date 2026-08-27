// 앱 껍데기는 캐시해서 오프라인에서도 열리게, 데이터는 항상 네트워크 우선.
// 캐시 이름 뒤에 붙는 해시는 index.html 내용에서 뽑는다. 화면이 바뀌면 캐시 이름이
// 통째로 바뀌므로 낡은 껍데기가 남아 새 UI 를 가리는 일이 없다.
const SHELL = 'kospi-flow-shell-8ac729190b77';
const SHELL_FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function freshFirst(request) {
  // 네트워크 우선, 성공하면 캐시 갱신, 실패하면 마지막으로 성공한 응답
  return fetch(request)
    .then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(request, copy));
      return res;
    })
    .catch(() => caches.match(request).then((hit) => hit || caches.match('./index.html')));
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  // 리포트 JSON 과 화면 자체(HTML)는 네트워크 우선.
  // HTML 을 캐시 우선으로 두면 배포해도 낡은 화면이 계속 뜬다.
  if (url.pathname.includes('/data/') || e.request.mode === 'navigate' || url.pathname.endsWith('.html')) {
    e.respondWith(freshFirst(e.request));
    return;
  }

  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});

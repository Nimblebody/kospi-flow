"""
정적 사이트(PWA) 빌드.

templates/dashboard.html 하나로 두 가지를 만든다.
  - web/index.html      : data/latest.json 을 fetch 하는 실제 대시보드
  - (선택) 인라인 버전  : 데이터를 파일 안에 박아 넣은 단일 HTML (공유·미리보기용)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import struct
import zlib
from pathlib import Path

import config

log = logging.getLogger(__name__)

TEMPLATE = config.ROOT / "templates" / "dashboard.html"

MANIFEST = {
    "name": "코스피 수급 지도",
    "short_name": "수급지도",
    "description": "코스피 테마별 수급과 테마 간 자금 이동 일간 리포트",
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f6f6f4",
    "theme_color": "#f6f6f4",
    "lang": "ko",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

SERVICE_WORKER = """// 앱 껍데기는 캐시해서 오프라인에서도 열리게, 데이터는 항상 네트워크 우선.
// 캐시 이름 뒤에 붙는 해시는 index.html 내용에서 뽑는다. 화면이 바뀌면 캐시 이름이
// 통째로 바뀌므로 낡은 껍데기가 남아 새 UI 를 가리는 일이 없다.
const SHELL = 'kospi-flow-shell-__VERSION__';
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
"""


# ---------------------------------------------------------------- 아이콘
def _png(size: int, path: Path) -> None:
    """의존성 없이 아이콘 PNG 를 직접 만든다.

    바탕은 잉크색, 가운데에 위(빨강)/아래(파랑)로 갈라지는 막대 두 개.
    """
    bg = (0x16, 0x18, 0x1D)
    up = (0xE0, 0x4B, 0x4B)
    down = (0x3E, 0x8B, 0xE8)

    rows = bytearray()
    m = size // 6           # 여백
    bar_w = size // 7
    gap = size // 12
    left = size // 2 - bar_w - gap // 2
    right = size // 2 + gap // 2
    mid = size // 2

    for y in range(size):
        rows.append(0)  # filter type 0
        for x in range(size):
            px = bg
            in_left = left <= x < left + bar_w
            in_right = right <= x < right + bar_w
            if in_left and m <= y < mid:
                px = up
            elif in_right and mid <= y < size - m:
                px = down
            elif abs(y - mid) <= max(1, size // 128) and m <= x < size - m:
                px = (0x3A, 0x3D, 0x45)
            rows.extend(px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


# ---------------------------------------------------------------- 빌드
def build_site(inline_report: dict | None = None) -> Path:
    """web/ 아래 정적 파일 일체를 만든다."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(inline_report, ensure_ascii=False) if inline_report else "null"
    html = tpl.replace("__BOOTSTRAP__", payload)

    index = config.WEB_DIR / "index.html"
    index.write_text(html, encoding="utf-8")

    (config.WEB_DIR / "manifest.webmanifest").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    version = hashlib.sha256(html.encode("utf-8")).hexdigest()[:12]
    (config.WEB_DIR / "sw.js").write_text(
        SERVICE_WORKER.replace("__VERSION__", version), encoding="utf-8"
    )
    (config.WEB_DIR / ".nojekyll").write_text("", encoding="utf-8")

    for size in (192, 512):
        icon = config.WEB_DIR / f"icon-{size}.png"
        if not icon.exists():
            _png(size, icon)

    log.info("사이트 빌드 완료: %s", index)
    return index


def build_standalone(report: dict, dest: Path) -> Path:
    """데이터를 안에 박아 넣은 단일 HTML (첨부·공유용)."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    html = tpl.replace("__BOOTSTRAP__", json.dumps(report, ensure_ascii=False))
    # 단독 파일에서는 서비스워커/매니페스트가 의미 없으므로 뺀다
    html = html.replace('<link rel="manifest" href="manifest.webmanifest">', "")
    dest.write_text(html, encoding="utf-8")
    return dest


def build_fragment(report: dict, dest: Path) -> Path:
    """<head>/<body> 껍데기를 뺀 조각 (Artifact 등 페이지 안에 끼워 넣을 때)."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    html = tpl.replace("__BOOTSTRAP__", json.dumps(report, ensure_ascii=False))

    head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)

    # 조각에서는 쓸모없는 태그 제거
    drop = (
        r'<meta charset[^>]*>',
        r'<meta name="viewport"[^>]*>',
        r'<link rel="manifest"[^>]*>',
        r'<meta name="apple-mobile-web-app-capable"[^>]*>',
        r'<link rel="apple-touch-icon"[^>]*>',
        r'<meta name="theme-color"[^>]*>',
    )
    for pat in drop:
        head = re.sub(pat, "", head)

    frag = (head.strip() + "\n" + body.strip()).replace(
        'navigator.serviceWorker.register("sw.js")', "Promise.resolve()"
    )
    dest.write_text(frag, encoding="utf-8")
    return dest

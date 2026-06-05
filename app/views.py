from __future__ import annotations

import html


def public_index(naver_map_key: str) -> str:
    escaped_key = html.escape(naver_map_key)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>공공 맛집 지도</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script>
    window.NAVER_MAP_KEY = "{escaped_key}";
  </script>
</head>
<body>
  <main class="shell">
    <section class="map-stage">
      <div class="topbar">
        <div class="brand">공공 맛집 지도</div>
        <form id="search-form" class="searchbar">
          <input id="q" name="q" type="search" placeholder="상호, 주소 검색" autocomplete="off">
          <select id="category" name="category" aria-label="카테고리">
            <option value="">전체</option>
            <option value="restaurant">음식점</option>
            <option value="cafe">카페</option>
            <option value="bar">주점</option>
            <option value="other">기타</option>
          </select>
          <button type="submit">검색</button>
        </form>
      </div>
      <div id="map" class="map" aria-label="지도"></div>
      <aside class="ranking-panel">
        <div class="panel-head">
          <h1>방문 랭킹</h1>
          <span id="result-count">0</span>
        </div>
        <ol id="ranking-list" class="ranking-list"></ol>
      </aside>
      <aside id="detail-panel" class="detail-panel" hidden></aside>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>"""


def admin_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>운영 검토</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell">
    <header class="admin-header">
      <h1>운영 검토</h1>
      <div class="admin-actions">
        <button id="run-live" type="button">실제 부산시 수집</button>
        <button id="verify-pending" type="button">수동검토 재검증</button>
        <button id="run-daily" type="button">Fixture 배치</button>
      </div>
    </header>
    <section id="batch-status" class="batch-status" aria-live="polite">
      <p>아직 실행된 배치가 없습니다.</p>
    </section>
    <section class="verification-section">
      <div class="section-head">
        <h2>검증 상태</h2>
        <span id="verification-summary">0</span>
      </div>
      <div id="verification-status" class="verification-status"></div>
    </section>
    <section class="source-section">
      <div class="section-head">
        <h2>수집 대상</h2>
        <span id="source-summary">0</span>
      </div>
      <div id="source-groups" class="source-groups"></div>
    </section>
    <section class="admin-grid">
      <div>
        <h2>수동 검토 <small id="review-queue-summary"></small></h2>
        <div id="review-queue" class="admin-list"></div>
      </div>
      <div>
        <h2>리뷰 신고</h2>
        <div id="review-reports" class="admin-list"></div>
      </div>
    </section>
  </main>
  <script src="/static/admin.js"></script>
</body>
</html>"""

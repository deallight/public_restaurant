from __future__ import annotations

import html
from datetime import date


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
    today = date.today()
    markup = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>관리자 대시보드</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell dashboard-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a class="active" href="/admin">수집</a>
        <a href="/admin/workflow">수집·검증</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>

    <section class="dashboard-overview" aria-labelledby="dashboard-overview-title">
      <div>
        <span class="dashboard-eyebrow">부산광역시 업무추진비</span>
        <h1 id="dashboard-overview-title">수집 데이터 현황</h1>
        <p id="dashboard-period-label">__START_DATE__ ~ __END_DATE__</p>
        <dl class="dashboard-totals">
          <div><dt>수집 문서</dt><dd id="dashboard-document-count">0</dd></div>
          <div><dt>집행 내역</dt><dd id="dashboard-expense-count">0</dd></div>
          <div><dt>검증 후보</dt><dd id="dashboard-candidate-count">0</dd></div>
        </dl>
      </div>
      <div class="verification-donut-block">
        <div id="verification-donut" class="verification-donut" aria-label="검증 상태 비율">
          <strong id="verification-donut-total">0</strong>
          <span>후보</span>
        </div>
        <ul id="verification-donut-legend" class="verification-donut-legend"></ul>
      </div>
    </section>

    <section class="dashboard-grid">
      <article class="dashboard-panel">
        <header class="dashboard-panel-head">
          <div>
            <span>기간별 상세 조회</span>
            <h2>수집 우선순위 현황</h2>
          </div>
        </header>
        <div class="dashboard-filter-row">
          <label>
            <span>도시</span>
            <select id="dashboard-city" aria-label="도시">
              <option value="busan">부산광역시</option>
            </select>
          </label>
          <label>
            <span>시작일</span>
            <input id="dashboard-start-date" type="date" value="__START_DATE__">
          </label>
          <label>
            <span>종료일</span>
            <input id="dashboard-end-date" type="date" value="__END_DATE__">
          </label>
          <button id="load-dashboard" type="button">조회</button>
        </div>
        <ul class="collection-status-legend" aria-label="수집 상태 색상">
          <li><strong>문서</strong></li>
          <li><i class="collected"></i>수집 완료</li>
          <li><i class="failed"></i>실패</li>
          <li><i class="processing"></i>처리 중</li>
          <li><i class="pending"></i>대기</li>
          <li class="legend-divider"><strong>검증</strong></li>
          <li><i class="approved"></i>승인</li>
          <li><i class="needs-review"></i>수동검토</li>
          <li><i class="rejected"></i>반려</li>
          <li><i class="verification-pending"></i>검증 전</li>
        </ul>
        <div id="priority-chart" class="dashboard-bars">
          <p class="empty">수집 현황을 불러오는 중입니다.</p>
        </div>
      </article>

      <article class="dashboard-panel">
        <header class="dashboard-panel-head">
          <div>
            <span>선택 순위 상세</span>
            <h2 id="priority-detail-title">기관·부서 수집 현황</h2>
          </div>
          <a id="priority-detail-link" class="dashboard-text-link" href="/admin/documents">전체 문서 게시판</a>
        </header>
        <ul class="collection-status-legend" aria-label="수집 상태 색상">
          <li><strong>문서</strong></li>
          <li><i class="collected"></i>수집 완료</li>
          <li><i class="failed"></i>실패</li>
          <li><i class="processing"></i>처리 중</li>
          <li><i class="pending"></i>대기</li>
          <li class="legend-divider"><strong>검증</strong></li>
          <li><i class="approved"></i>승인</li>
          <li><i class="needs-review"></i>수동검토</li>
          <li><i class="rejected"></i>반려</li>
          <li><i class="verification-pending"></i>검증 전</li>
        </ul>
        <div id="priority-detail" class="dashboard-bars dashboard-detail-bars">
          <p class="empty">왼쪽에서 우선순위를 선택하세요.</p>
        </div>
      </article>
    </section>

    <section class="source-section">
      <div class="section-head">
        <h2>수집 대상</h2>
        <span id="source-summary">0</span>
      </div>
      <div id="source-groups" class="source-groups"></div>
    </section>

    <section class="collection-operations">
      <div class="section-head">
        <div>
          <span class="dashboard-eyebrow">수집 실행</span>
          <h2>부산시 수집·검증 작업</h2>
        </div>
        <div class="collection-operation-actions">
          <button id="create-collection-plan" type="button">수집 대상 확정</button>
          <button id="run-plan-batch" type="button">계획 배치 수집</button>
          <button id="retry-plan-failed" type="button">실패 문서 재처리</button>
          <button id="run-live" type="button">즉시 수집</button>
          <button id="verify-collected" type="button">수집 데이터 검증</button>
          <button id="verify-pending" type="button">수동검토 재검증</button>
        </div>
      </div>
      <div class="live-collect-controls" aria-label="부산시 수집 조건">
        <label>
          <span>수집 시작일</span>
          <input id="live-start-date" type="date" value="__START_DATE__">
        </label>
        <label>
          <span>수집 종료일</span>
          <input id="live-end-date" type="date" value="__END_DATE__">
        </label>
        <label>
          <span>배치 크기</span>
          <input id="collection-batch-size" type="number" min="1" max="200" value="20">
        </label>
      </div>
      <section id="collection-plan-status" class="batch-status" aria-live="polite">
        <p>확정된 수집 계획이 없습니다.</p>
      </section>
      <section id="batch-status" class="batch-status" aria-live="polite">
        <p>아직 실행된 배치가 없습니다.</p>
      </section>
    </section>
  </main>
  <script src="/static/dashboard.js"></script>
</body>
</html>"""
    return (
        markup
        .replace("__START_DATE__", f"{today.year}-01-01")
        .replace("__END_DATE__", today.isoformat())
    )


def admin_workflow_index() -> str:
    today = date.today()
    markup = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>수집·검증 작업판</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell workflow-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">수집</a>
        <a class="active" href="/admin/workflow">수집·검증</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>

    <section class="workflow-head">
      <div>
        <span class="dashboard-eyebrow">부산광역시 업무추진비</span>
        <h1>수집·검증 작업판</h1>
        <p id="workflow-range-label">__START_DATE__ ~ __END_DATE__</p>
      </div>
      <a class="button-link secondary" href="/admin/logs">실행 로그</a>
    </section>

    <section class="workflow-scope-controls" aria-label="수집 검증 조회 범위">
      <label>
        <span>도시 선택</span>
        <select id="workflow-city">
          <option value="busan">부산광역시</option>
        </select>
      </label>
      <label>
        <span>순위 선택</span>
        <select id="workflow-priority">
          <option value="">전체 순위</option>
        </select>
      </label>
      <label>
        <span>기관 선택</span>
        <select id="workflow-institution">
          <option value="">전체 기관</option>
        </select>
      </label>
    </section>

    <ol id="workflow-steps" class="workflow-steps" aria-label="수집 검증 단계">
      <li data-step="period" class="active">
        <span>1</span>
        <strong>기간 설정</strong>
        <small id="workflow-step-period">대기</small>
      </li>
      <li data-step="list">
        <span>2</span>
        <strong>목록 가져오기</strong>
        <small id="workflow-step-list">문서 0건</small>
      </li>
      <li data-step="collect-batch">
        <span>3</span>
        <strong>수집 배치</strong>
        <small id="workflow-step-collect-batch">20건</small>
      </li>
      <li data-step="collect">
        <span>4</span>
        <strong>문서 수집</strong>
        <small id="workflow-step-collect">대기</small>
      </li>
      <li data-step="verify-batch">
        <span>5</span>
        <strong>검증 배치</strong>
        <small id="workflow-step-verify-batch">100건</small>
      </li>
      <li data-step="result">
        <span>6</span>
        <strong>결과 분류</strong>
        <small id="workflow-step-result">승인 0 · 수동 0 · 반려 0</small>
      </li>
    </ol>

    <section class="workflow-controls" aria-label="수집 검증 실행 조건">
      <label>
        <span>수집 시작일</span>
        <input id="workflow-start-date" type="date" value="__START_DATE__">
      </label>
      <label>
        <span>수집 종료일</span>
        <input id="workflow-end-date" type="date" value="__END_DATE__">
      </label>
      <label>
        <span>수집 배치 크기</span>
        <input id="workflow-batch-size" type="number" min="1" max="200" value="20">
      </label>
      <label>
        <span>검증 배치 크기</span>
        <input id="workflow-verify-limit" type="number" min="1" max="300" value="100">
      </label>
      <button id="workflow-fetch-list" type="button">목록 가져오기</button>
      <button id="workflow-run-collection" type="button">수집</button>
      <button id="workflow-retry-collection" class="secondary" type="button">재수집</button>
      <button id="workflow-run-verification" type="button">검증</button>
      <button id="workflow-refresh" class="secondary" type="button">새로고침</button>
    </section>

    <section class="workflow-status-strip" aria-label="현재 상태">
      <div><span>문서</span><strong id="workflow-document-total">0</strong></div>
      <div><span>수집 완료</span><strong id="workflow-document-collected">0</strong></div>
      <div><span>수집 실패</span><strong id="workflow-document-failed">0</strong></div>
      <div><span>검증 후보</span><strong id="workflow-candidate-total">0</strong></div>
      <div><span>승인</span><strong id="workflow-approved-total">0</strong></div>
      <div><span>수동검토</span><strong id="workflow-review-total">0</strong></div>
      <div><span>반려</span><strong id="workflow-rejected-total">0</strong></div>
    </section>

    <section class="workflow-main-grid">
      <article class="workflow-panel workflow-collection-panel">
        <header class="workflow-panel-head">
          <div>
            <span class="dashboard-eyebrow">수집 목록</span>
            <h2>기간 내 게시물</h2>
          </div>
          <div class="workflow-panel-tools">
            <select id="workflow-document-status" aria-label="수집 상태">
              <option value="">전체 상태</option>
              <option value="pending">대기</option>
              <option value="processing">처리 중</option>
              <option value="collected">수집</option>
              <option value="duplicate">기존 문서</option>
              <option value="failed">실패</option>
            </select>
            <a class="dashboard-text-link" href="/admin/documents">전체 문서</a>
          </div>
        </header>
        <div class="workflow-table-wrap">
          <table class="workflow-table">
            <thead>
              <tr>
                <th>순번</th>
                <th>작성일</th>
                <th>제목</th>
                <th>진행</th>
                <th>수집 성공/실패</th>
              </tr>
            </thead>
            <tbody id="workflow-document-rows">
              <tr><td colspan="5">수집 목록을 불러오는 중입니다.</td></tr>
            </tbody>
          </table>
        </div>
        <div id="workflow-document-pagination" class="workflow-pagination" aria-label="수집 목록 페이지"></div>
      </article>

      <article id="workflow-verification" class="workflow-panel workflow-verification-panel">
        <header class="workflow-panel-head">
          <div>
            <span class="dashboard-eyebrow">검증</span>
            <h2>검증 항목</h2>
          </div>
          <div class="workflow-panel-tools">
            <select id="workflow-candidate-status" aria-label="검증 상태">
              <option value="">전체 상태</option>
              <option value="pending" selected>검증 대기</option>
              <option value="verified">승인</option>
              <option value="needs_review">수동검토</option>
              <option value="rejected">반려</option>
            </select>
            <select id="workflow-candidate-sort" aria-label="검증 정렬">
              <option value="verification_oldest" selected>검증 대기 오래된순</option>
              <option value="used_date_desc">방문일 최신순</option>
              <option value="used_date_asc">방문일 오래된순</option>
              <option value="source_published_desc">문서 작성일 최신순</option>
              <option value="source_published_asc">문서 작성일 오래된순</option>
              <option value="amount_desc">금액 높은순</option>
              <option value="name_asc">상호명순</option>
              <option value="id_desc">등록 최신순</option>
            </select>
            <input id="workflow-candidate-search" type="search" placeholder="상호, 주소, 기관 검색">
            <button id="workflow-candidate-search-button" class="secondary" type="button">검색</button>
          </div>
        </header>
        <div class="workflow-verification-layout">
          <div class="workflow-table-wrap">
            <table class="workflow-table workflow-verification-table">
              <thead>
                <tr>
                  <th>순번</th>
                  <th>문서 작성일</th>
                  <th>방문일</th>
                  <th>원문 가게명</th>
                  <th>원문 주소</th>
                  <th>진행</th>
                  <th>검증 결과</th>
                </tr>
              </thead>
              <tbody id="workflow-candidate-rows">
                <tr><td colspan="7">검증 항목을 불러오는 중입니다.</td></tr>
              </tbody>
            </table>
          </div>
          <div id="workflow-candidate-pagination" class="workflow-pagination" aria-label="검증 항목 페이지"></div>
          <aside class="workflow-log-panel" aria-live="polite">
            <strong>상태 로그</strong>
            <div id="workflow-log-list"></div>
          </aside>
        </div>
      </article>
    </section>

    <section class="workflow-db-panel">
      <header class="workflow-panel-head">
        <div>
          <span class="dashboard-eyebrow">DB 조회</span>
          <h2>검증 결과별 항목</h2>
        </div>
        <a class="dashboard-text-link" href="/admin/review">상세 검토</a>
      </header>
      <div id="workflow-status-tabs" class="workflow-status-tabs" data-active="needs_review">
        <button type="button" data-status="verified">승인</button>
        <button class="active" type="button" data-status="needs_review">수동검토</button>
        <button type="button" data-status="rejected">반려</button>
      </div>
      <div id="workflow-db-list" class="workflow-db-list"></div>
    </section>

    <div id="workflow-toast" class="workflow-toast" hidden></div>
  </main>
  <script src="/static/workflow.js"></script>
</body>
</html>"""
    return (
        markup.replace("__START_DATE__", f"{today.year}-01-01")
        .replace("__END_DATE__", today.isoformat())
    )


def admin_review_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>데이터 검토</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">수집</a>
        <a class="active" href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="verification-section">
      <div class="section-head">
        <h1>데이터 검토</h1>
        <div class="admin-actions">
          <a class="button-link" href="/admin/map-issues">지도 문제 항목</a>
        </div>
      </div>
      <div class="section-head">
        <h2>검증 상태</h2>
        <span id="verification-summary">0</span>
      </div>
      <div id="verification-status" class="verification-status"></div>
    </section>
    <section class="manual-review-section">
      <h2>후보 데이터 <small id="review-queue-summary"></small></h2>
      <div id="review-queue" class="admin-list"></div>
      <section class="review-reports-section">
        <h2>리뷰 신고</h2>
        <div id="review-reports" class="admin-list"></div>
      </section>
    </section>
  </main>
  <script src="/static/admin.js"></script>
</body>
</html>"""


def map_issues_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>지도 문제 항목</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">수집</a>
        <a class="active" href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="manual-review-section">
      <div class="section-head">
        <h1>승인 항목 점검 <small id="map-issues-summary"></small></h1>
        <button id="refresh-map-issues" type="button">새로고침</button>
      </div>
      <p class="muted">주소 미확인, 지도 비표시, 부산 보정주소와 좌표가 맞지 않는 승인 항목을 표시합니다.</p>
      <div id="map-issues-list" class="admin-list"></div>
    </section>
  </main>
  <script src="/static/map_issues.js"></script>
</body>
</html>"""


def ops_logs_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>수집/검증 로그</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell ops-log-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">수집</a>
        <a href="/admin/review">검토</a>
        <a class="active" href="/admin/logs">로그</a>
      </nav>
    </header>
    <div class="section-head">
      <h1>수집/검증 로그</h1>
      <button id="refresh-ops-logs" type="button">새로고침</button>
    </div>
    <section class="ops-log-controls">
      <label>
        <span>수집 계획</span>
        <select id="ops-log-plan"></select>
      </label>
      <label>
        <span>표시 개수</span>
        <input id="ops-log-limit" type="number" min="10" max="300" value="100">
      </label>
    </section>
    <section class="ops-log-section">
      <h2>수집 계획</h2>
      <div id="ops-log-plans"></div>
    </section>
    <section class="ops-log-section">
      <h2 id="ops-documents-title">문서별 수집 상태</h2>
      <div id="ops-log-documents"></div>
    </section>
    <section class="ops-log-section">
      <h2>배치 실행 로그</h2>
      <div id="ops-log-batches"></div>
    </section>
    <section class="ops-log-section">
      <h2>API 호출 로그</h2>
      <div id="ops-log-api"></div>
    </section>
    <section class="ops-log-section">
      <h2>DLQ</h2>
      <div id="ops-log-dlq"></div>
    </section>
  </main>
  <script src="/static/ops_logs.js"></script>
</body>
</html>"""


def admin_documents_index() -> str:
    today = date.today()
    markup = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>기관별 수집 문서</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell document-admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a class="active" href="/admin">수집</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="document-page-head">
      <div>
        <span class="dashboard-eyebrow">수집 문서 게시판</span>
        <h1>기관별 수집 문서</h1>
        <p>수집 문서, 파싱 결과, 검증 진행 상태를 기관 단위로 조회합니다.</p>
      </div>
      <a class="button-link secondary" href="/admin">대시보드로</a>
    </section>
    <section class="document-filter-panel">
      <div class="document-filter-grid">
        <label>
          <span>도시</span>
          <select id="document-city">
            <option value="busan">부산광역시</option>
          </select>
        </label>
        <label>
          <span>기관</span>
          <select id="document-institution">
            <option value="">전체 기관</option>
          </select>
        </label>
        <label>
          <span>시작일</span>
          <input id="document-start-date" type="date" value="__START_DATE__">
        </label>
        <label>
          <span>종료일</span>
          <input id="document-end-date" type="date" value="__END_DATE__">
        </label>
        <label>
          <span>수집 상태</span>
          <select id="document-status">
            <option value="">전체 상태</option>
            <option value="collected">수집</option>
            <option value="duplicate">기존 문서</option>
            <option value="failed">실패</option>
            <option value="processing">처리 중</option>
            <option value="pending">대기</option>
          </select>
        </label>
        <label>
          <span>정렬</span>
          <select id="document-sort">
            <option value="published_desc">작성일 최신순</option>
            <option value="published_asc">작성일 오래된순</option>
            <option value="collected_desc">수집일 최신순</option>
            <option value="rows_desc">집행 내역 많은순</option>
            <option value="verification_asc">검증률 낮은순</option>
          </select>
        </label>
        <label class="document-search-field">
          <span>검색</span>
          <input id="document-search" type="search" placeholder="제목, 기관, 부서, URL">
        </label>
        <button id="load-documents" type="button">조회</button>
      </div>
    </section>
    <section class="document-summary" aria-label="조회 요약">
      <div><span>문서</span><strong id="document-summary-total">0</strong></div>
      <div><span>수집 완료</span><strong id="document-summary-collected">0</strong></div>
      <div><span>실패</span><strong id="document-summary-failed">0</strong></div>
      <div><span>집행 내역</span><strong id="document-summary-candidates">0</strong></div>
      <div><span>검증 완료</span><strong id="document-summary-verified">0</strong></div>
    </section>
    <section class="document-board">
      <header class="section-head">
        <div>
          <h2 id="document-board-title">수집 문서</h2>
          <p id="document-board-range" class="muted"></p>
        </div>
        <button id="refresh-documents" class="secondary" type="button">새로고침</button>
      </header>
      <div id="document-board-list"></div>
      <nav class="document-pagination" aria-label="문서 페이지">
        <button id="document-prev" class="secondary" type="button">이전</button>
        <span id="document-page-label">1 / 1</span>
        <button id="document-next" class="secondary" type="button">다음</button>
      </nav>
    </section>
  </main>
  <script src="/static/admin_documents.js"></script>
</body>
</html>"""
    return (
        markup.replace("__START_DATE__", f"{today.year}-01-01")
        .replace("__END_DATE__", today.isoformat())
    )


def admin_document_detail_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>수집 문서 상세</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell document-admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a class="active" href="/admin">수집</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="document-detail-head">
      <div>
        <a id="document-back-link" class="document-back-link" href="/admin/documents">기관별 문서로 돌아가기</a>
        <span id="document-detail-status" class="status-badge">불러오는 중</span>
        <h1 id="document-detail-title">수집 문서 상세</h1>
        <p id="document-detail-subtitle"></p>
      </div>
      <a id="document-source-link" class="button-link secondary" href="#" target="_blank" rel="noreferrer">원문 문서 열기</a>
    </section>
    <section id="document-detail-summary" class="document-detail-summary"></section>
    <section class="document-row-toolbar">
      <div class="document-row-filters">
        <label>
          <span>상태</span>
          <select id="document-row-status">
            <option value="">전체</option>
            <option value="needs_review">수동검토</option>
            <option value="verified">승인</option>
            <option value="rejected">반려</option>
          </select>
        </label>
        <label>
          <span>정렬</span>
          <select id="document-row-sort">
            <option value="row_asc">원문 행 순서</option>
            <option value="row_desc">원문 행 역순</option>
            <option value="used_date_desc">사용일 최신순</option>
            <option value="amount_desc">금액 높은순</option>
            <option value="status_asc">상태순</option>
          </select>
        </label>
        <label class="document-search-field">
          <span>행 검색</span>
          <input id="document-row-search" type="search" placeholder="ID, 상호, 주소, 목적">
        </label>
        <button id="load-document-rows" type="button">조회</button>
      </div>
      <div class="document-bulk-actions">
        <span id="document-dirty-count">변경 0건</span>
        <select id="document-bulk-status" aria-label="선택 상태">
          <option value="">선택 상태 변경</option>
          <option value="needs_review">수동검토</option>
          <option value="verified">승인</option>
          <option value="rejected">반려</option>
        </select>
        <button id="apply-document-bulk-status" class="secondary" type="button">선택 적용</button>
        <button id="save-document-rows" type="button">DB 업데이트</button>
      </div>
    </section>
    <section class="document-row-board">
      <div id="document-save-message" class="document-save-message" hidden></div>
      <div id="document-row-list"></div>
      <nav class="document-pagination" aria-label="집행 내역 페이지">
        <button id="document-row-prev" class="secondary" type="button">이전</button>
        <span id="document-row-page-label">1 / 1</span>
        <button id="document-row-next" class="secondary" type="button">다음</button>
      </nav>
    </section>
  </main>
  <script src="/static/admin_document_detail.js"></script>
</body>
</html>"""

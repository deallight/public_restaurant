from __future__ import annotations

import html
from datetime import date
from urllib.parse import quote


def public_index(
    naver_map_key: str,
    app_name: str = "공기밥",
    current_user: dict | None = None,
) -> str:
    escaped_key = html.escape(naver_map_key)
    escaped_app_name = html.escape(app_name)
    category_filters = [
        ("", "전체"),
        ("restaurant", "음식점"),
        ("cafe", "카페"),
        ("bar", "주점"),
        ("other", "기타"),
    ]
    category_buttons = "\n".join(
        f'          <button type="button" data-filter="category" data-value="{html.escape(value)}"'
        f' aria-pressed="{"true" if not value else "false"}">{html.escape(label)}</button>'
        for value, label in category_filters
    )
    visit_buttons = "\n".join(
        f'          <button type="button" data-filter="min_visit_count" data-value="{count}"'
        f' aria-pressed="false">{count}회 이상</button>'
        for count in range(10, 110, 10)
    )
    if current_user:
        display_name = html.escape(str(current_user.get("display_name", "사용자")))
        account_markup = f"""<div class="account-actions signed-in">
            <a class="account-name" href="/mypage"><i aria-hidden="true"></i>{display_name}님의 마이페이지</a>
            <form action="/auth/logout" method="post">
              <button class="account-logout" type="submit">로그아웃</button>
            </form>
          </div>"""
    else:
        account_markup = """<div class="account-actions">
            <a class="account-login" href="/login">로그인</a>
          </div>"""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_app_name}</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script>
    window.NAVER_MAP_KEY = "{escaped_key}";
    window.IS_SIGNED_IN = {"true" if current_user else "false"};
  </script>
</head>
<body>
  <main class="shell">
    <section class="map-stage">
      <div class="map-controls">
        <div class="topbar">
          <div class="brand">{escaped_app_name}</div>
          <form id="search-form" class="searchbar">
            <input id="q" name="q" type="search" placeholder="상호, 주소, 지역 검색" autocomplete="off">
            <button type="submit">검색</button>
            <details id="visit-filter-toggle" class="visit-filter-toggle">
              <summary><span id="visit-filter-label">방문 전체</span></summary>
              <div class="visit-filter-menu" role="group" aria-label="방문횟수">
                <button type="button" data-filter="min_visit_count" data-value="" aria-pressed="true">방문 전체</button>
{visit_buttons}
              </div>
            </details>
          </form>
          {account_markup}
        </div>
        <div id="filter-index" class="filter-index" aria-label="필터 선택">
          <div class="filter-index-group" role="group" aria-label="카테고리">
{category_buttons}
            <button type="button" class="saved-filter" data-filter="saved" data-value="1" aria-pressed="false">
              <span aria-hidden="true">♥</span> 관심 가게
            </button>
          </div>
        </div>
      </div>
      <div id="map" class="map" aria-label="지도"></div>
      <aside class="ranking-panel">
        <div class="panel-head">
          <h1>방문 랭킹</h1>
          <span id="result-count">0</span>
        </div>
        <ol id="ranking-list" class="ranking-list"></ol>
      </aside>
      <aside
        id="detail-panel"
        class="detail-panel"
        aria-label="선택한 음식점 상세 정보"
        aria-live="polite"
        hidden
      ></aside>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>"""


def login_index(
    app_name: str = "공기밥",
    error: str = "",
    current_user: dict | None = None,
    naver_configured: bool = True,
    return_to: str = "/",
    account_deleted: bool = False,
) -> str:
    escaped_app_name = html.escape(app_name)
    error_messages = {
        "cancelled": "네이버 로그인이 취소되었습니다. 원할 때 다시 시도해 주세요.",
        "expired": "로그인 요청이 만료되었어요. 네이버 로그인을 다시 시작해 주세요.",
        "not_configured": "네이버 로그인 설정이 아직 완료되지 않았습니다.",
        "provider_error": "네이버 로그인 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    }
    error_message = error_messages.get(error, "")
    alert_markup = (
        f'<div class="auth-alert" role="alert">{html.escape(error_message)}</div>'
        if error_message
        else (
            '<div class="auth-alert auth-alert-success" role="status">계정과 네이버 연결 정보가 삭제되었습니다.</div>'
            if account_deleted
            else ""
        )
    )
    if current_user:
        raw_display_name = str(current_user.get("display_name", "사용자"))
        display_name = html.escape(raw_display_name)
        display_initial = html.escape(raw_display_name[:1] or "사")
        signed_in_markup = f"""<div class="auth-current-user">
              <span class="auth-avatar" aria-hidden="true">{display_initial}</span>
              <div><strong>{display_name}님</strong><span>이미 로그인되어 있습니다.</span></div>
            </div>
            <a class="auth-primary-link" href="{html.escape(return_to)}">계속하기</a>
            <form class="auth-logout-form" action="/auth/logout" method="post">
              <button type="submit">로그아웃</button>
            </form>"""
        action_markup = signed_in_markup
    elif naver_configured:
        encoded_return_to = quote(return_to, safe="/")
        action_markup = f"""<a class="naver-sso-button" href="/auth/naver/start?return_to={encoded_return_to}">
              <span class="naver-mark" aria-hidden="true">N</span>
              <span>네이버로 계속하기</span>
            </a>
            <p class="auth-provider-note">네이버 인증 한 번으로 바로 시작할 수 있습니다.</p>"""
    else:
        action_markup = """<span class="naver-sso-button is-disabled" aria-disabled="true">
              <span class="naver-mark" aria-hidden="true">N</span>
              <span>네이버 로그인 준비 중</span>
            </span>
            <p class="auth-provider-note">서버에 네이버 로그인 환경변수를 설정하면 바로 사용할 수 있습니다.</p>"""

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#063b3a">
  <title>로그인 | {escaped_app_name}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="auth-page">
  <main class="auth-shell">
    <section class="auth-story" aria-labelledby="auth-story-title">
      <a class="auth-brand" href="/" aria-label="{escaped_app_name} 홈">
        <span class="auth-brand-mark" aria-hidden="true">공</span>
        <span>{escaped_app_name}</span>
      </a>
      <div class="auth-story-copy">
        <span class="auth-story-label">BUSAN PUBLIC DINING GUIDE</span>
        <h1 id="auth-story-title">공공기관의 발자취로<br>부산의 한 끼를 발견하세요.</h1>
        <p>흩어진 업무추진비 기록을 모아, 실제 방문이 쌓인 부산 맛집을 한눈에 보여드립니다.</p>
      </div>
      <div class="auth-story-stats" aria-label="서비스 특징">
        <div><strong>공공데이터</strong><span>근거가 보이는 맛집</span></div>
        <div><strong>부산 전역</strong><span>지도에서 바로 탐색</span></div>
        <div><strong>한 번의 인증</strong><span>비밀번호 없는 시작</span></div>
      </div>
    </section>

    <section class="auth-panel" aria-labelledby="auth-title">
      <div class="auth-card">
        <a class="auth-back" href="/">← 지도로 돌아가기</a>
        <div class="auth-heading">
          <span>START WITH NAVER</span>
          <h2 id="auth-title">부산의 맛집을 더 가깝게</h2>
          <p>공공기관의 실제 방문 기록을 바탕으로, 부산 곳곳의 맛집을 지도에서 발견하고 방문 경험을 나눠보세요.</p>
        </div>
        {alert_markup}
        <div class="auth-actions">
          {action_markup}
        </div>
        <div class="auth-assurance">
          <div><span aria-hidden="true">✓</span><p><strong>비밀번호를 저장하지 않아요</strong>네이버에서 안전하게 인증합니다.</p></div>
          <div><span aria-hidden="true">✓</span><p><strong>최소 정보만 사용해요</strong>로그인 때는 네이버 회원 식별값과 별명만 받습니다.</p></div>
        </div>
        <p class="auth-footnote">등록된 계정이 없으면 인증 과정에서 자동으로 만들어집니다.</p>
        <nav class="auth-legal-links" aria-label="서비스 정책">
          <a href="/privacy">개인정보처리방침</a>
          <a href="/terms">이용약관</a>
        </nav>
      </div>
    </section>
  </main>
</body>
</html>"""


def mypage_index(
    app_name: str,
    data: dict,
    account_delete_token: str = "",
    account_error: str = "",
) -> str:
    user = data["user"]
    reviews = data["reviews"]
    saved_restaurants = data["saved_restaurants"]
    display_name = html.escape(str(user.get("display_name", "사용자")))
    initial = html.escape(str(user.get("display_name", "사"))[:1] or "사")
    account_error_markup = (
        '<p class="account-delete-error" role="alert">확인 문구로 ‘계정 삭제’를 정확히 입력해 주세요.</p>'
        if account_error == "confirmation"
        else ""
    )

    review_status_labels = {
        "visible": "공개 중",
        "hidden": "검토 중",
    }
    review_cards = "\n".join(
        f"""<article class="mypage-card review-card">
          <div class="mypage-card-head">
            <div>
              <span class="mypage-card-kicker">{html.escape(str(review['rating']))}점 · {html.escape(str(review_status_labels.get(review['status'], review['status'])))}</span>
              <h3>{html.escape(str(review['restaurant_name']))}</h3>
            </div>
            <time>{html.escape(str(review['created_at'])[:10])}</time>
          </div>
          <p class="mypage-review-body">{html.escape(str(review['body']))}</p>
          <p class="mypage-card-address">{html.escape(str(review.get('address') or '주소 정보 없음'))}</p>
          <div class="mypage-card-actions">
            <a href="/?restaurant_id={int(review['restaurant_id'])}">가게 보기</a>
            <form action="/api/reviews/{int(review['id'])}/delete" method="post">
              <input type="hidden" name="return_to" value="/mypage">
              <button class="danger" type="submit">리뷰 삭제</button>
            </form>
          </div>
        </article>"""
        for review in reviews
    ) or """<div class="mypage-empty">
          <strong>아직 작성한 리뷰가 없어요.</strong>
          <p>지도에서 다녀온 가게를 선택하고 첫 리뷰를 남겨보세요.</p>
          <a href="/">가게 둘러보기</a>
        </div>"""

    saved_cards = "\n".join(
        f"""<article class="mypage-card saved-card">
          <div class="mypage-card-head">
            <div>
              <span class="mypage-card-kicker">{html.escape(str(restaurant['category_label']))}</span>
              <h3>{html.escape(str(restaurant['name']))}</h3>
            </div>
            <span class="saved-mark" aria-label="저장됨">♥</span>
          </div>
          <p class="mypage-card-address">{html.escape(str(restaurant.get('road_address') or restaurant.get('address') or '주소 정보 없음'))}</p>
          <div class="mypage-card-stats">
            <span>공공기관 방문 <b>{int(restaurant['visit_count'])}회</b></span>
            <span>평점 <b>{float(restaurant['average_rating']):.1f}</b></span>
            <span>리뷰 <b>{int(restaurant['review_count'])}개</b></span>
          </div>
          <div class="mypage-card-actions">
            <a href="/?restaurant_id={int(restaurant['id'])}">가게 보기</a>
            <form action="/api/restaurants/{int(restaurant['id'])}/unsave" method="post">
              <input type="hidden" name="return_to" value="/mypage">
              <button type="submit">저장 해제</button>
            </form>
          </div>
        </article>"""
        for restaurant in saved_restaurants
    ) or """<div class="mypage-empty">
          <strong>저장한 가게가 아직 없어요.</strong>
          <p>관심 있는 가게의 하트를 눌러 이곳에 모아보세요.</p>
          <a href="/">가게 둘러보기</a>
        </div>"""

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#063b3a">
  <title>마이페이지 | {html.escape(app_name)}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="mypage-body">
  <header class="mypage-header">
    <a class="mypage-brand" href="/">{html.escape(app_name)}</a>
    <nav aria-label="사용자 메뉴">
      <a href="/">지도로 돌아가기</a>
      <form action="/auth/logout" method="post">
        <input type="hidden" name="return_to" value="/">
        <button type="submit">로그아웃</button>
      </form>
    </nav>
  </header>
  <main class="mypage-shell">
    <section class="mypage-hero">
      <div class="mypage-avatar" aria-hidden="true">{initial}</div>
      <div>
        <span class="mypage-eyebrow">MY DINING ARCHIVE</span>
        <h1>{display_name}님의 맛집 기록</h1>
        <p>남긴 리뷰와 다시 찾고 싶은 가게를 한곳에서 관리하세요.</p>
      </div>
      <dl class="mypage-summary">
        <div><dt>내 리뷰</dt><dd>{int(data['counts']['reviews'])}</dd></div>
        <div><dt>저장한 가게</dt><dd>{int(data['counts']['saved_restaurants'])}</dd></div>
      </dl>
    </section>

    <section class="mypage-section" aria-labelledby="saved-restaurants-title">
      <div class="mypage-section-head">
        <div><span>SAVED PLACES</span><h2 id="saved-restaurants-title">저장한 가게</h2></div>
        <strong>{len(saved_restaurants)}곳</strong>
      </div>
      <div class="mypage-grid">{saved_cards}</div>
    </section>

    <section class="mypage-section" aria-labelledby="my-reviews-title">
      <div class="mypage-section-head">
        <div><span>MY REVIEWS</span><h2 id="my-reviews-title">내가 쓴 리뷰</h2></div>
        <strong>{len(reviews)}개</strong>
      </div>
      <div class="mypage-grid">{review_cards}</div>
    </section>

    <section class="mypage-section account-delete-section" aria-labelledby="account-delete-title">
      <div>
        <span class="mypage-eyebrow">ACCOUNT CONTROL</span>
        <h2 id="account-delete-title">계정 삭제</h2>
        <p>네이버 연결 정보와 저장한 가게는 즉시 삭제됩니다. 작성한 리뷰는 작성자와 접속 식별값을 제거한 뒤 ‘탈퇴한 사용자’의 리뷰로 남습니다.</p>
        <p>리뷰 본문까지 삭제하려면 계정을 삭제하기 전에 위의 ‘리뷰 삭제’를 먼저 이용해 주세요. 백업 사본은 최대 30일 안에 순차 삭제됩니다.</p>
      </div>
      <form class="account-delete-form" action="/account/delete" method="post">
        <input type="hidden" name="action_token" value="{html.escape(account_delete_token)}">
        <label for="account-delete-confirmation">계속하려면 <strong>계정 삭제</strong>를 입력하세요.</label>
        <div>
          <input id="account-delete-confirmation" name="confirmation" type="text" autocomplete="off" required>
          <button type="submit">계정 삭제</button>
        </div>
        {account_error_markup}
      </form>
    </section>
  </main>
  <footer class="site-policy-footer">
    <a href="/privacy">개인정보처리방침</a>
    <a href="/terms">이용약관</a>
  </footer>
</body>
</html>"""


def privacy_index(app_name: str = "공기밥", contact_email: str = "") -> str:
    escaped_app_name = html.escape(app_name)
    escaped_email = html.escape(contact_email)
    contact_markup = (
        f'<a href="mailto:{escaped_email}">{escaped_email}</a>'
        if escaped_email
        else "운영 환경에 등록된 개인정보 문의 이메일"
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#063b3a">
  <title>개인정보처리방침 | {escaped_app_name}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="legal-body">
  <header class="legal-header"><a href="/">{escaped_app_name}</a><a href="/login">로그인</a></header>
  <main class="legal-shell">
    <div class="legal-heading"><span>PRIVACY POLICY</span><h1>개인정보처리방침</h1><p>시행일: 2026년 7월 27일</p></div>
    <section><h2>1. 처리 목적과 항목</h2>
      <p>{escaped_app_name}은 로그인과 사용자 기능 제공, 리뷰 운영, 부정 이용 방지, 장애 대응을 위해 필요한 최소한의 정보만 처리합니다.</p>
      <ul>
        <li>네이버 로그인: 네이버가 제공하는 회원 식별값과 별명</li>
        <li>사용자 기능: 저장한 가게, 작성한 리뷰·별점, 리뷰 신고 내역</li>
        <li>보안·운영: 세션 쿠키, 접속 IP와 브라우저 정보가 포함될 수 있는 서버 접속 기록, 리뷰·신고 시 생성되는 단방향 IP 식별값</li>
      </ul>
      <p>비밀번호, 성별, 생일, 출생연도, 휴대전화번호는 수집하지 않습니다.</p>
    </section>
    <section><h2>2. 보유 및 파기</h2>
      <ul>
        <li>계정과 네이버 연결 정보: 회원 탈퇴 시 즉시 삭제 또는 비식별화</li>
        <li>저장한 가게: 회원 탈퇴 시 즉시 삭제</li>
        <li>리뷰: 사용자가 삭제할 때까지 공개되며, 회원 탈퇴 시 작성자와 IP 식별값을 제거하여 익명화</li>
        <li>세션 쿠키: 로그인 후 최대 7일, OAuth 진행 쿠키: 최대 10분</li>
        <li>서버 접속 기록: 보안 및 장애 대응을 위해 최대 90일</li>
        <li>백업 사본: 생성 후 최대 30일</li>
      </ul>
      <p>보유 기간이 끝난 전자 기록은 복구하기 어렵도록 삭제하거나 식별할 수 없도록 처리합니다.</p>
    </section>
    <section><h2>3. 외부 서비스 및 처리 위탁</h2>
      <p>네이버 로그인 인증 과정은 네이버에서 진행됩니다. 네이버 지도·검색 및 공공데이터 API에는 장소명·주소 등 음식점 검색에 필요한 정보만 전송하며, 회원 식별값은 전송하지 않습니다.</p>
      <p>리뷰 작성 화면에서 별도 동의한 경우 음식점명, 별점, 공개 리뷰 본문을 암호화된 API 통신으로 미국의 Groq, Inc.에 전송하여 AI 요약 생성을 맡깁니다. 별명과 네이버 회원 식별값은 요청에 포함하지 않습니다. Groq는 일반 추론 요청의 입력·출력을 기본적으로 보관하지 않지만, 장애 대응·부정 이용 조사 시 미국의 GCP에 최대 30일 보관할 수 있다고 고지합니다. 동의하지 않으면 리뷰를 등록할 수 없지만 지도·검색·저장 등 다른 기능은 이용할 수 있습니다. 자세한 내용은 <a href="https://console.groq.com/docs/your-data" target="_blank" rel="noopener noreferrer">Groq 데이터 처리 안내</a>에서 확인할 수 있습니다.</p>
    </section>
    <section><h2>4. 이용자의 권리</h2>
      <p>이용자는 마이페이지에서 저장한 가게와 리뷰를 관리하고 계정을 삭제할 수 있습니다. 리뷰 본문까지 삭제하려면 회원 탈퇴 전에 해당 리뷰를 먼저 삭제해야 합니다. 그 밖의 열람·정정·삭제·처리정지 요청은 아래 연락처로 접수할 수 있습니다.</p>
    </section>
    <section><h2>5. 안전성 확보 조치</h2>
      <p>HTTPS 통신, 비밀번호를 저장하지 않는 OAuth 로그인, 서명된 세션, 접근권한 제한, 방화벽, 데이터베이스의 로컬 전용 바인딩, 정기 백업과 보안 업데이트를 적용합니다.</p>
    </section>
    <section><h2>6. 개인정보 문의</h2><p>공기밥 개인정보 보호 담당 · {contact_markup}</p></section>
    <section><h2>7. 변경 고지</h2><p>방침이 변경되면 시행 전에 이 페이지에서 변경 내용과 시행일을 알립니다.</p></section>
  </main>
  <footer class="site-policy-footer"><a href="/privacy" aria-current="page">개인정보처리방침</a><a href="/terms">이용약관</a></footer>
</body>
</html>"""


def terms_index(app_name: str = "공기밥", contact_email: str = "") -> str:
    escaped_app_name = html.escape(app_name)
    escaped_email = html.escape(contact_email)
    contact_markup = (
        f'<a href="mailto:{escaped_email}">{escaped_email}</a>'
        if escaped_email
        else "운영 환경에 등록된 문의 이메일"
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#063b3a">
  <title>이용약관 | {escaped_app_name}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="legal-body">
  <header class="legal-header"><a href="/">{escaped_app_name}</a><a href="/login">로그인</a></header>
  <main class="legal-shell">
    <div class="legal-heading"><span>TERMS OF SERVICE</span><h1>이용약관</h1><p>시행일: 2026년 7월 27일</p></div>
    <section><h2>1. 목적</h2><p>이 약관은 {escaped_app_name}이 제공하는 공공기관 방문 기록 기반 음식점 탐색, 저장, 리뷰 기능의 이용 조건을 정합니다.</p></section>
    <section><h2>2. 계정과 로그인</h2><p>네이버 인증이 완료되면 계정이 자동 생성됩니다. 이용자는 자신의 인증 수단을 안전하게 관리해야 하며, 타인의 계정을 사용하거나 서비스 운영을 방해해서는 안 됩니다.</p></section>
    <section><h2>3. 공공데이터와 서비스 정보</h2><p>음식점 방문 기록과 순위는 공개 자료를 수집·검증한 결과입니다. 원문 변경, 수집 시점, 외부 API 상태에 따라 실제 정보와 차이가 있을 수 있으므로 중요한 판단에는 원 출처를 함께 확인해 주세요.</p></section>
    <section><h2>4. 사용자 콘텐츠</h2><p>이용자는 자신이 작성한 리뷰에 대한 책임을 집니다. 불법 정보, 개인정보 침해, 명예훼손, 광고·스팸, 조작된 내용은 게시할 수 없습니다. 신고 또는 운영상 필요가 있으면 해당 콘텐츠를 숨기거나 삭제할 수 있습니다.</p></section>
    <section><h2>5. 계정 삭제</h2><p>마이페이지에서 언제든 계정을 삭제할 수 있습니다. 네이버 연결 정보와 저장한 가게는 삭제되고, 공개 리뷰는 작성자 정보가 제거된 상태로 남습니다. 리뷰 본문까지 삭제하려면 탈퇴 전에 리뷰 삭제 기능을 이용해야 합니다.</p></section>
    <section><h2>6. 서비스 변경과 중단</h2><p>점검, 장애, 외부 서비스 변경, 천재지변 등으로 서비스 일부가 변경되거나 일시 중단될 수 있습니다. 중요한 변경은 가능한 범위에서 서비스 화면을 통해 안내합니다.</p></section>
    <section><h2>7. 책임의 범위</h2><p>고의 또는 중대한 과실이 없는 한 무료로 제공되는 정보의 최신성·완전성, 이용자의 선택 또는 외부 서비스 장애로 생긴 간접 손해를 보증하지 않습니다. 관계 법령에서 달리 정한 책임은 그 규정을 따릅니다.</p></section>
    <section><h2>8. 문의</h2><p>약관 및 서비스 문의 · {contact_markup}</p></section>
  </main>
  <footer class="site-policy-footer"><a href="/privacy">개인정보처리방침</a><a href="/terms" aria-current="page">이용약관</a></footer>
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
        <a class="active" href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
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

    <section class="api-usage-section" aria-labelledby="api-usage-title">
      <div class="section-head">
        <div>
          <span class="dashboard-eyebrow">외부 서비스 모니터링</span>
          <h2 id="api-usage-title">API 연동 및 무료 사용량</h2>
          <p id="api-usage-notice">연동 상태와 이 서버에서 기록한 호출량을 확인합니다.</p>
        </div>
        <div class="api-usage-summary" aria-live="polite">
          <strong id="api-connected-count">0/0</strong>
          <span>API 연동</span>
        </div>
      </div>
      <div id="api-usage-grid" class="api-usage-grid">
        <p class="empty">API 상태를 불러오는 중입니다.</p>
      </div>
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


def admin_parsing_index() -> str:
    return admin_workflow_index("parsing")


def admin_restaurant_images_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>음식점 사진 관리</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell restaurant-image-admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a class="active" href="/admin/photos">사진</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>

    <section class="restaurant-image-admin-head">
      <div>
        <span class="dashboard-eyebrow">관리자 직접 등록</span>
        <h1>음식점 사진 관리</h1>
        <p>관리자가 등록한 사진을 먼저 보여주고, 빈 자리는 네이버 이미지 검색 결과로 채웁니다.</p>
      </div>
      <a class="button-link secondary" href="/">운영 맵에서 확인</a>
    </section>

    <section class="restaurant-image-admin-layout">
      <aside class="restaurant-image-restaurant-panel">
        <form id="restaurant-image-search-form" class="restaurant-image-search">
          <label for="restaurant-image-search">음식점 검색</label>
          <div>
            <input id="restaurant-image-search" type="search" placeholder="가게명 또는 주소">
            <button type="submit">검색</button>
          </div>
        </form>
        <p id="restaurant-image-search-summary" class="muted">음식점을 불러오는 중입니다.</p>
        <div id="restaurant-image-restaurant-list" class="restaurant-image-restaurant-list"></div>
      </aside>

      <section id="restaurant-image-editor" class="restaurant-image-editor" aria-live="polite">
        <div class="restaurant-image-editor-empty">
          <strong>사진을 관리할 음식점을 선택하세요.</strong>
          <span>가게당 최대 4장의 PNG, JPEG, WebP 사진을 등록할 수 있습니다.</span>
        </div>
      </section>
    </section>
    <div id="restaurant-image-toast" class="workflow-toast" hidden></div>
  </main>
  <script src="/static/admin_restaurant_images.js"></script>
</body>
</html>"""


def _workflow_status_strip(mode: str) -> str:
    if mode == "review":
        items = [
            ("검증 후보", "workflow-candidate-total"),
            ("승인", "workflow-approved-total"),
            ("수동검토", "workflow-review-total"),
            ("반려", "workflow-rejected-total"),
        ]
    elif mode == "parsing":
        items = [
            ("문서", "workflow-document-total"),
            ("수집 완료", "workflow-document-collected"),
            ("파싱 대기", "workflow-parse-pending"),
            ("파싱 완료", "workflow-parse-success"),
            ("파싱 실패", "workflow-parse-failed"),
        ]
    else:
        items = [
            ("문서", "workflow-document-total"),
            ("수집 완료", "workflow-document-collected"),
            ("수집 실패", "workflow-document-failed"),
        ]
    cells = "\n".join(
        f'      <div><span>{label}</span><strong id="{element_id}">0</strong></div>'
        for label, element_id in items
    )
    return f"""    <section class="workflow-status-strip workflow-status-strip-compact" aria-label="작업 요약">
{cells}
    </section>"""


def _workflow_document_panel(show_parse_status: bool) -> str:
    parse_status_hidden = "" if show_parse_status else " hidden"
    return f"""      <article class="workflow-panel workflow-collection-panel">
        <header class="workflow-panel-head">
          <div>
            <span class="dashboard-eyebrow">수집 목록</span>
            <h2>기간 내 게시물</h2>
          </div>
          <div class="workflow-panel-tools">
            <select id="workflow-document-status" aria-label="문서 상태">
              <option value="">전체 상태</option>
              <option value="pending">대기</option>
              <option value="processing">처리 중</option>
              <option value="collected">수집 완료</option>
              <option value="duplicate">기존 문서</option>
              <option value="failed">수집 실패</option>
            </select>
            <select id="workflow-parse-status" aria-label="파싱 상태"{parse_status_hidden}>
              <option value="">전체 파싱 상태</option>
              <option value="not_requested">파싱 대기</option>
              <option value="parsing">파싱 중</option>
              <option value="parsed">파싱 완료</option>
              <option value="empty">빈 문서</option>
              <option value="failed">파싱 실패</option>
              <option value="unsupported">지원 불가</option>
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
                <th>상태</th>
              </tr>
            </thead>
            <tbody id="workflow-document-rows">
              <tr><td colspan="5">수집 문서를 불러오는 중입니다.</td></tr>
            </tbody>
          </table>
        </div>
        <div id="workflow-document-pagination" class="workflow-pagination" aria-label="수집 목록 페이지"></div>
      </article>"""


def _workflow_candidate_panel() -> str:
    return """      <article id="workflow-verification" class="workflow-panel workflow-verification-panel">
        <header class="workflow-panel-head">
          <div>
            <span class="dashboard-eyebrow">검증</span>
            <h2>검증 항목</h2>
          </div>
          <div class="workflow-panel-tools">
            <select id="workflow-candidate-status" aria-label="검증 상태">
              <option value="pending">검증 대기</option>
              <option value="needs_review">수동검토</option>
              <option value="verified">승인</option>
              <option value="rejected">반려</option>
            </select>
            <select id="workflow-candidate-sort" aria-label="검증 정렬">
              <option value="verification_oldest">검증 대기 오래된순</option>
              <option value="id_desc">최신순</option>
              <option value="amount_desc">금액 높은순</option>
            </select>
            <input id="workflow-candidate-search" type="search" placeholder="상호, 주소, 기관 검색">
            <button id="workflow-candidate-search-button" class="secondary" type="button">검색</button>
          </div>
        </header>
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
                <th>상태</th>
              </tr>
            </thead>
            <tbody id="workflow-candidate-rows">
              <tr><td colspan="7">검증 항목을 불러오는 중입니다.</td></tr>
            </tbody>
          </table>
        </div>
        <div id="workflow-candidate-pagination" class="workflow-pagination" aria-label="검증 항목 페이지"></div>
      </article>"""


def _workflow_log_panel() -> str:
    return """      <aside class="workflow-log-panel" aria-label="상태 로그">
        <strong>상태 로그</strong>
        <div id="workflow-log-list"></div>
      </aside>"""


def _workflow_main_content(mode: str) -> str:
    if mode == "review":
        return f"""    <section class="workflow-main-grid workflow-review-log-grid">
{_workflow_candidate_panel()}
{_workflow_log_panel()}
    </section>"""
    return f"""    <section class="workflow-main-grid workflow-document-log-grid">
{_workflow_document_panel(show_parse_status=mode == "parsing")}
{_workflow_log_panel()}
    </section>"""


def admin_workflow_index(mode: str = "collection") -> str:
    today = date.today()
    safe_mode = mode if mode in {"collection", "parsing", "review"} else "collection"
    page_meta = {
        "collection": {
            "title": "수집 작업판",
            "eyebrow": "부산광역시 업무추진비",
            "subtitle": "목록 확인과 문서 수집 상태를 한 화면에서 관리합니다.",
            "active_collection": ' class="active"',
            "active_parsing": "",
            "active_review": "",
            "hide_fetch": "",
            "hide_collect": "",
            "hide_retry": "",
            "hide_parse": " hidden",
            "hide_verify": " hidden",
            "batch_control": """      <label>
        <span>수집 배치 크기</span>
        <input id="workflow-batch-size" type="number" min="1" max="500" value="100">
      </label>""",
            "verify_control": "",
            "head_action": '<a class="button-link secondary" href="/admin/documents">문서 게시판</a>',
        },
        "parsing": {
            "title": "파싱 작업판",
            "eyebrow": "부산광역시 업무추진비",
            "subtitle": "수집된 원문 문서의 파싱 상태와 신규 행 생성 결과를 확인합니다.",
            "active_collection": "",
            "active_parsing": ' class="active"',
            "active_review": "",
            "hide_fetch": " hidden",
            "hide_collect": " hidden",
            "hide_retry": " hidden",
            "hide_parse": "",
            "hide_verify": " hidden",
            "batch_control": """      <label>
        <span>파싱 배치 크기</span>
        <input id="workflow-batch-size" type="number" min="1" max="500" value="100">
      </label>""",
            "verify_control": "",
            "head_action": '<a class="button-link secondary" href="/admin/documents">문서 게시판</a>',
        },
        "review": {
            "title": "검토 작업판",
            "eyebrow": "부산광역시 업무추진비",
            "subtitle": "검증 후보와 승인·수동검토·반려 결과를 검토합니다.",
            "active_collection": "",
            "active_parsing": "",
            "active_review": ' class="active"',
            "hide_fetch": " hidden",
            "hide_collect": " hidden",
            "hide_retry": " hidden",
            "hide_parse": " hidden",
            "hide_verify": "",
            "batch_control": "",
            "verify_control": """      <label>
        <span>검증 배치 크기</span>
        <input id="workflow-verify-limit" type="number" min="1" max="500" value="100">
      </label>""",
            "head_action": '<a class="button-link secondary" href="/admin/review/results">검증 결과 수정</a>',
        },
    }[safe_mode]
    markup = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell workflow-shell" data-workflow-mode="__MODE__">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">대시보드</a>
        <a__ACTIVE_COLLECTION__ href="/admin/collection">수집</a>
        <a__ACTIVE_PARSING__ href="/admin/parsing">파싱</a>
        <a__ACTIVE_REVIEW__ href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>

    <section class="workflow-head">
      <div>
        <span class="dashboard-eyebrow">__EYEBROW__</span>
        <h1>__TITLE__</h1>
        <p id="workflow-range-label">__START_DATE__ ~ __END_DATE__</p>
      </div>
      __HEAD_ACTION__
    </section>

    <section class="workflow-scope-controls" aria-label="조회 범위">
      <label>
        <span>도시 선택</span>
        <select id="workflow-city" aria-label="도시 선택">
          <option value="busan">부산광역시</option>
        </select>
      </label>
      <label>
        <span>순위 선택</span>
        <select id="workflow-priority" aria-label="순위 선택">
          <option value="">전체 순위</option>
        </select>
      </label>
      <label>
        <span>기관 선택</span>
        <select id="workflow-institution" aria-label="기관 선택">
          <option value="">전체 기관</option>
        </select>
      </label>
    </section>

    <ol id="workflow-steps" class="workflow-steps" aria-label="작업 단계">
      <li data-step="period"><span>1</span><strong>기간 설정</strong><small id="workflow-step-period">__START_DATE__ ~ __END_DATE__</small></li>
      <li data-step="list"><span>2</span><strong>목록 가져오기</strong><small id="workflow-step-list">문서 0건</small></li>
      <li data-step="collect-batch"><span>3</span><strong>수집 배치</strong><small id="workflow-step-collect-batch">100건</small></li>
      <li data-step="collect"><span>4</span><strong>문서 수집</strong><small id="workflow-step-collect">대기</small></li>
      <li data-step="parse"><span>5</span><strong>문서 파싱</strong><small id="workflow-step-parse">대기</small></li>
      <li data-step="verify-batch"><span>6</span><strong>검증 배치</strong><small id="workflow-step-verify-batch">100건</small></li>
      <li data-step="result"><span>7</span><strong>결과 분류</strong><small id="workflow-step-result">승인 0 · 수동 0 · 반려 0</small></li>
    </ol>

    <section class="workflow-controls" aria-label="작업 실행 조건">
      <label>
        <span>수집 시작일</span>
        <input id="workflow-start-date" type="date" value="__START_DATE__">
      </label>
      <label>
        <span>수집 종료일</span>
        <input id="workflow-end-date" type="date" value="__END_DATE__">
      </label>
__BATCH_CONTROL__
__VERIFY_CONTROL__
      <button id="workflow-fetch-list" type="button"__HIDE_FETCH__>목록 가져오기</button>
      <button id="workflow-run-collection" type="button"__HIDE_COLLECT__>수집</button>
      <button id="workflow-run-parse" type="button"__HIDE_PARSE__>파싱</button>
      <button id="workflow-retry-parse" class="secondary" type="button"__HIDE_PARSE__>실패 재파싱</button>
      <button id="workflow-retry-collection" class="secondary" type="button"__HIDE_RETRY__>재수집</button>
      <button id="workflow-run-verification" type="button"__HIDE_VERIFY__>검증</button>
      <button id="workflow-refresh" class="secondary" type="button">새로고침</button>
    </section>

__STATUS_STRIP__

__WORKFLOW_MAIN__

    <div id="workflow-toast" class="workflow-toast" hidden></div>
  </main>
  <script src="/static/workflow.js"></script>
</body>
</html>"""
    return (
        markup.replace("__MODE__", safe_mode)
        .replace("__TITLE__", html.escape(page_meta["title"]))
        .replace("__EYEBROW__", html.escape(page_meta["eyebrow"]))
        .replace("__SUBTITLE__", html.escape(page_meta["subtitle"]))
        .replace("__HEAD_ACTION__", page_meta["head_action"])
        .replace("__ACTIVE_COLLECTION__", page_meta["active_collection"])
        .replace("__ACTIVE_PARSING__", page_meta["active_parsing"])
        .replace("__ACTIVE_REVIEW__", page_meta["active_review"])
        .replace("__HIDE_FETCH__", page_meta["hide_fetch"])
        .replace("__HIDE_COLLECT__", page_meta["hide_collect"])
        .replace("__HIDE_RETRY__", page_meta["hide_retry"])
        .replace("__HIDE_PARSE__", page_meta["hide_parse"])
        .replace("__HIDE_VERIFY__", page_meta["hide_verify"])
        .replace("__BATCH_CONTROL__", page_meta["batch_control"])
        .replace("__VERIFY_CONTROL__", page_meta["verify_control"])
        .replace("__STATUS_STRIP__", _workflow_status_strip(safe_mode))
        .replace("__WORKFLOW_MAIN__", _workflow_main_content(safe_mode))
        .replace("__START_DATE__", f"{today.year}-01-01")
        .replace("__END_DATE__", today.isoformat())
    )


def admin_review_index() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>검증 결과 수정</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="admin-shell">
    <header class="dashboard-header">
      <a class="dashboard-brand" href="/admin">관리자 대시보드</a>
      <nav class="dashboard-nav" aria-label="관리자 메뉴">
        <a href="/">운영 맵</a>
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a class="active" href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="verification-section">
      <div class="section-head">
        <h1>검증 결과 수정</h1>
        <div class="admin-actions">
          <a class="button-link secondary" href="/admin/review">검토 작업판</a>
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
      <h2>승인·수동검토·반려 항목 <small id="review-queue-summary"></small></h2>
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
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a class="active" href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
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
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a href="/admin/review">검토</a>
        <a href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
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
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a href="/admin/review">검토</a>
        <a class="active" href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
        <a href="/admin/logs">로그</a>
      </nav>
    </header>
    <section class="document-page-head">
      <div>
        <span class="dashboard-eyebrow">수집 문서 게시판</span>
        <h1>기관별 수집 문서</h1>
        <p>수집 문서, 파싱 결과, 검증 진행 상태를 기관 단위로 조회합니다.</p>
      </div>
      <a class="button-link secondary" href="/admin/collection">수집 화면으로</a>
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
        <a href="/admin">대시보드</a>
        <a href="/admin/collection">수집</a>
        <a href="/admin/parsing">파싱</a>
        <a href="/admin/review">검토</a>
        <a class="active" href="/admin/documents">문서</a>
        <a href="/admin/photos">사진</a>
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

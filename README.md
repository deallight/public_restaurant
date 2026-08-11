# 공기밥 · 부산 공공기관 업무추진비 맛집 지도

부산 지역 공공기관의 업무추진비 공개 문서를 수집·파싱·검증해 실제 방문 음식점을 지도, 검색, 랭킹으로 보여주는 서버 렌더링 웹 서비스입니다. 원문 데이터를 바로 공개하지 않고 `수집 → 파싱 → 후보 추출 → 자동 검증 → 관리자 검토 → 공개` 순서로 처리합니다.

## 가장 먼저: PostgreSQL과 서버 실행

아래 명령은 이 프로젝트에서 사용하는 macOS 로컬 개발 환경 기준입니다. 프로젝트 루트(`/Users/deallight/Documents/public_restaurant`)에서 실행합니다.

### 1. PostgreSQL 시작

Mac을 재시작했거나 PostgreSQL이 중지되어 있다면 먼저 로컬 클러스터를 시작합니다.

```bash
pg_ctl -D /Users/deallight/develop/server_pc/.postgres/data \
  -o "-p 5432 -k /Users/deallight/develop/server_pc/.postgres/socket -h 127.0.0.1" \
  -l /Users/deallight/develop/server_pc/.postgres/postgres.log start
```

상태 확인:

```bash
pg_ctl -D /Users/deallight/develop/server_pc/.postgres/data status
```

### 2. PostgreSQL 스키마 확인

정상 서버 시작은 PostgreSQL 테이블을 자동으로 만들거나 변경하지 않습니다. 서버를 실행하기 전에 현재 DB가 코드와 호환되는지 확인합니다.

```bash
.venv/bin/python -m scripts.check_db_schema
```

정상 결과는 다음과 같습니다.

```json
{
  "status": "compatible",
  "schema_issues": []
}
```

새 DB이거나 스키마가 아직 적용되지 않은 경우에만 마이그레이션 내용을 검토한 뒤 아래 명령을 실행합니다.

```bash
.venv/bin/python -m scripts.init_db --apply
.venv/bin/python -m scripts.check_db_schema
```

### 3. 개발 서버 실행

```bash
.venv/bin/python -m app.server --host 127.0.0.1 --port 8000
```

수집·파싱·검증 작업은 별도 터미널의 DB 작업 worker가 실행합니다.

```bash
.venv/bin/python -m app.worker --poll-interval 1
```

관리자 작업 POST는 즉시 `202`와 `job_id`를 반환하며, 화면은
`/ops/jobs/{job_id}`를 조회합니다. 운영 배포에서는
`public-restaurant-worker.service`가 worker를 계속 실행합니다.

브라우저에서 다음 주소를 엽니다.

- 공개 지도: <http://127.0.0.1:8000/>
- 로그인: <http://127.0.0.1:8000/login>
- 마이페이지: <http://127.0.0.1:8000/mypage>
- 관리자 대시보드: <http://127.0.0.1:8000/admin>

서버 응답 확인:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

관리자 화면과 `/admin/*`, `/ops/*`, `/review/*` API는 로그인한 계정의 `users.role`이 `admin`일 때만 접근할 수 있습니다.

## 최초 개발 환경 준비

Python 3.10 이상과 PostgreSQL 16 이상을 권장합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

로컬 개발용 `.env`의 DB 설정 예시는 다음과 같습니다. 실제 비밀번호와 API 키는 문서, 코드, 커밋에 넣지 않습니다.

```dotenv
APP_ENV=development
DATABASE_URL=postgresql:///public_restaurant_dev?host=/Users/deallight/develop/server_pc/.postgres/socket
```

개발 DB 자체가 없다면 PostgreSQL을 시작한 뒤 한 번만 생성합니다.

```bash
createdb -h /Users/deallight/develop/server_pc/.postgres/socket \
  -p 5432 public_restaurant_dev
.venv/bin/python -m scripts.init_db --apply
.venv/bin/python -m scripts.check_db_schema
```

개발·테스트·운영 환경 모두 PostgreSQL `DATABASE_URL`이 필수입니다. 값이 없거나 PostgreSQL URL이 아니면 애플리케이션은 시작하지 않습니다.

## 현재 개발 상태

### 공개 서비스

- 검증 완료 음식점의 지도, 목록, 검색, 카테고리·지역 필터, 기간별 랭킹을 제공합니다.
- 같은 위치 또는 가까운 위치의 음식점은 지도 확대 수준에 따라 하나의 군집 핀으로 묶고, 목록에서 개별 가게를 선택할 수 있습니다.
- 상세 화면에서 기관 방문 이력, 방문 횟수, 금액, 리뷰, 평점, AI 리뷰 요약을 확인할 수 있습니다.
- 로그인 사용자는 가게를 저장하거나 해제하고, 마이페이지에서 저장 가게와 본인 리뷰를 관리할 수 있습니다.
- 외부 음식점 사진과 관리자 보관 사진은 공개하지 않으며 네이버 이미지 검색도 호출하지 않습니다. 로그인 사용자가 직접 등록하고 게시 권한을 확인한 음식 사진만 상세 화면에 표시합니다.
- 상세 화면의 간단히 보기에는 사용자 사진을 최대 3장, 방문 정보 펼치기에는 2열로 표시합니다. 사진이 없으면 사진 등록 화면으로 이동하는 안내를 표시합니다.

### 사용자·인증

- 네이버 OAuth 로그인과 자동 회원가입을 지원합니다.
- HMAC 서명 세션 쿠키를 사용하며 운영 환경에서는 `Secure`, `HttpOnly`, `SameSite=Lax` 정책을 적용합니다.
- 리뷰 작성, 추천·비추천, 리뷰 신고, 본인 리뷰 삭제, 저장 가게 관리, 사용자 사진 등록·수정·삭제, 계정 탈퇴를 제공합니다.
- 계정 탈퇴 시 OAuth 연결, 저장 목록, 등록 사진을 삭제하고 기존 리뷰의 사용자·IP 식별값을 익명화합니다.
- Google OAuth 클라이언트와 라우트도 유지하지만 현재 공개 로그인 UI의 기본 흐름은 네이버 로그인입니다.

### 관리자·운영

- 대시보드: 수집·파싱·검증 현황과 외부 API 연결·사용량을 확인합니다.
- 수집: 기간별 문서를 탐색해 수집 계획을 만들고 배치 실행 및 실패 재시도를 수행합니다.
- 파싱: XLSX와 HTML 표를 파싱하고 실패 문서를 다시 처리합니다.
- 검토: 후보 수정, 지오코딩, 신규 음식점 승인, 기존 음식점 병합, 반려를 처리합니다.
- 문서: 수집 문서와 지출 행의 처리 상태를 조회합니다.
- 사진: 관리자 업로드 파일을 추가·수정·삭제할 수 있습니다. 파일과 DB 정보는 보존하지만 현재 공개 화면에는 노출하지 않습니다.
- 로그: 배치 이력, 외부 API 호출 결과, 실패 원인, 검증 진행 상태를 확인합니다.
- 관리자 및 운영 경로는 서버에서 로그인과 `admin` 역할을 검사합니다.

### 데이터 수집·검증

- 부산광역시 업무추진비 통합 게시판의 목록, 상세 HTML, 첨부파일을 수집합니다.
- XLSX 또는 HTML 표에서 집행일자, 사용처, 목적, 금액, 인원, 결제방법을 추출합니다.
- 규칙, 별칭 기억, 기존 검증 근거, 네이버 장소 검색, NCP Maps 지오코딩, 공공데이터포털 인허가 API를 조합합니다.
- 근거가 충분한 후보만 자동 승인하고, 불확실한 후보는 `manual_review_tasks`로 보냅니다.
- 결정 근거, API 호출 요약, 인허가 스냅샷, DLQ, 배치 실행 이력을 DB에 남깁니다.

## 기술 구조

- 웹 서버: Python 표준 라이브러리 `ThreadingHTTPServer`
- 데이터베이스: PostgreSQL 16+와 `psycopg` 3만 지원
- 프론트엔드: Python 서버 렌더링 HTML + 정적 JavaScript/CSS
- 지도: Naver Maps JavaScript API, 키가 없을 때 로컬 fallback 지도
- 테스트: Python `unittest`, 외부 API는 fake client로 대체

주요 모듈:

| 경로 | 역할 |
| --- | --- |
| `app/server.py` | 서버 CLI 진입점 |
| `app/http_server.py` | 애플리케이션 구성, HTTP 라우팅, 인증·권한 처리 |
| `app/services.py` | 공개·사용자·관리자·운영 비즈니스 로직 |
| `app/pipeline.py` | 수집, 파싱, 후보 생성, 검증, 저장 파이프라인 |
| `app/agents.py` | 음식점 판단과 `VerificationDecision` 생성 |
| `app/integrations.py` | Naver, NCP Maps, 공공데이터, OAuth, Groq 클라이언트 |
| `app/database.py` | PostgreSQL DB 접근, 번호형 마이그레이션과 스키마 검증 |
| `app/schema.py` | 애플리케이션 데이터 모델 기준 |
| `app/views.py` | 서버 렌더링 화면 |
| `app/static/` | 공개 지도와 관리자 화면 JavaScript/CSS |
| `database/migrations/` | PostgreSQL 마이그레이션 |
| `scripts/` | PostgreSQL 점검·마이그레이션·수집·검증 운영 명령 |

## 환경 변수

프로젝트 루트의 `.env`는 서버 시작 시 자동으로 읽습니다. `.env`와 `var/`는 Git에 포함하지 않으며, 실제 secret은 실행 환경에서 주입합니다. 전체 형식은 `.env.example`을 참고합니다.

| 변수 | 용도 |
| --- | --- |
| `APP_HOST`, `APP_PORT` | 서버 바인딩 주소와 포트 |
| `APP_ENV` | `development` 또는 `production` |
| `DATABASE_URL` | PostgreSQL 접속 URL. 모든 환경에서 필수 |
| `TEST_DATABASE_URL` | 이름에 `test`가 포함된 격리 PostgreSQL 테스트 DB. 생략 시 개발 DB 이름에 `_test`를 붙여 계산 |
| `RESTAURANT_IMAGE_UPLOAD_DIR` | 관리자 보관 사진과 사용자 등록 사진 저장 경로 |
| `APP_SESSION_SECRET` | 로그인 세션 서명 키 |
| `PRIVACY_CONTACT_EMAIL` | 개인정보처리방침·이용약관 문의 이메일. 운영 환경에서는 필수 |
| `NAVER_MAP_KEY` | Naver Maps JavaScript API Client ID |
| `NAVER_MAPS_CLIENT_ID`, `NAVER_MAPS_CLIENT_SECRET` | NCP Maps 지오코딩 |
| `NAVER_SEARCH_CLIENT_ID`, `NAVER_SEARCH_CLIENT_SECRET` | Naver Search Local API |
| `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` | NAVER API HUB 설정. 공개 이미지 검색은 현재 비활성화 |
| `NAVER_LOGIN_CLIENT_ID`, `NAVER_LOGIN_CLIENT_SECRET`, `NAVER_LOGIN_REDIRECT_URI` | 네이버 OAuth 로그인 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Google OAuth 설정 |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 인허가 API |
| `GROQ_API_KEY`, `GROQ_MODEL` | 리뷰 AI 요약 |
| `REVIEW_RATE_LIMIT_PER_HOUR` | 같은 음식점의 사용자/IP 기준 시간당 리뷰 제한 |
| `*_QUOTA` | 관리자 API 사용량 화면의 기준 한도 |

외부 연동 키가 없는 기능은 비활성화되거나 로컬 fixture/fake client 중심으로 동작합니다. 관리자 화면의 사용량은 이 서버의 `api_call_logs` 기록 기준이며 공급자 콘솔 집계와 다를 수 있습니다.

### 네이버 로그인 설정

Naver Developers에 등록한 Callback URL과 `NAVER_LOGIN_REDIRECT_URI`가 정확히 같아야 합니다.

- 로컬: `http://127.0.0.1:8000/auth/callback/naver`
- 운영: `https://gonggibap.com/auth/callback/naver`

로그인 관련 secret은 `.env` 또는 배포 secret으로만 주입합니다. 별도 회원가입 화면은 없으며, 네이버 인증 후 기존 계정은 로그인하고 신규 계정은 자동 생성됩니다.

## 데이터 처리 흐름

1. `source_registry`에서 수집 대상을 찾습니다.
2. 부산시 업무추진비 게시판 목록과 상세 페이지를 수집합니다.
3. 원문 HTML과 첨부파일을 `var/raw/busan_city/`에 저장합니다.
4. `raw_documents`와 `expense_records`에 문서와 지출 행을 저장합니다.
5. 음식점 가능성이 있는 행을 `restaurant_candidates`로 만듭니다.
6. 규칙, 별칭, 네이버 장소, 지오코딩, 인허가 근거로 검증합니다.
7. 승인 후보는 `restaurants`와 `restaurant_expense_links`에 반영합니다.
8. 불확실한 후보는 `manual_review_tasks`에 남깁니다.
9. 관리자 결정은 `decision_audit_logs`, `alias_memory`, `place_verifications`에 기록합니다.

핵심 테이블 흐름:

```text
raw_documents
  → expense_records
  → restaurant_candidates
  → place_verifications
  → restaurants
  → restaurant_expense_links
```

주요 보조 테이블:

- `collection_plans`, `collection_plan_documents`: 수집 계획과 문서별 진행 상태
- `batch_jobs`: 파이프라인 실행 이력
- `manual_review_tasks`: 관리자 검토 큐
- `permit_snapshots`: 인허가 API 응답 캐시
- `api_call_logs`: 외부 API 호출 요약
- `alias_memory`: 수동 승인 뒤 재사용하는 음식점 별칭
- `decision_audit_logs`: 승인·병합·반려 감사 로그
- `dead_letter_queue`: 파싱·검증 실패 항목
- `users`, `oauth_accounts`: 사용자와 OAuth 연결
- `restaurant_reviews`, `review_reactions`, `review_reports`: 리뷰, 사용자별 추천·비추천, 신고
- `user_saved_restaurants`: 사용자 저장 가게
- `restaurant_ai_summaries`: 리뷰 AI 요약 캐시
- `restaurant_user_images`: 사용자 등록 사진과 소유자 정보
- `restaurant_admin_images`: 관리자 업로드 사진 정보

## 수집·파싱·검증 실행

fixture 기반 일일 파이프라인:

```bash
.venv/bin/python -m scripts.run_daily
```

부산시 라이브 수집 smoke test:

```bash
.venv/bin/python -m scripts.smoke_busan_live
```

서버 실행 중 수집 계획 생성과 배치 처리:

```bash
curl -X POST http://127.0.0.1:8000/ops/collection-plans \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2026-01-01","end_date":"2026-12-31","batch_size":20}'

curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/run \
  -H "Content-Type: application/json" \
  -d '{"repeat":true,"max_batches":100}'

curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/parse \
  -H "Content-Type: application/json" \
  -d '{"max_batches":100}'
```

실패한 수집·파싱 작업 재시도:

```bash
curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/retry-failed \
  -H "Content-Type: application/json" \
  -d '{"max_batches":20}'

curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/retry-parse-failed \
  -H "Content-Type: application/json" \
  -d '{"max_batches":20}'
```

검증 대기 후보 처리:

```bash
.venv/bin/python -m scripts.verify_pending --limit 100

curl -X POST http://127.0.0.1:8000/ops/verify-pending \
  -H "Content-Type: application/json" \
  -d '{"limit":100,"sort":"verification_oldest"}'
```

위 `/ops/*` 요청은 관리자 로그인 세션이 필요합니다. 브라우저에서 오래 걸리는 작업이 시간 초과로 보이더라도 백엔드 작업이 계속 진행 중일 수 있으므로, 즉시 같은 작업을 재실행하지 말고 관리자 진행 상태와 서버 로그를 먼저 확인합니다.

## 주요 화면과 API

### 공개·사용자 화면

- `GET /`: 공개 지도
- `GET /login`: 로그인
- `GET /mypage`: 저장 가게·내 리뷰·계정 관리
- `GET /privacy`: 개인정보처리방침
- `GET /terms`: 이용약관

### 공개·사용자 API

- `GET /api/map/restaurants?q=&category=&region=&bounds=&saved_only=`
- `GET /api/restaurants/{id}`
- `GET /api/rankings?category=&region=&period=`
- `GET /api/search?q=&category=&bounds=`
- `POST /api/restaurants/{id}/reviews`
- `POST /api/reviews/{id}/report`
- `POST /api/restaurants/{id}/save`
- `POST /api/restaurants/{id}/unsave`
- `POST /api/reviews/{id}/delete`
- `POST /account/delete`
- `POST /auth/logout`

`saved_only=1`, 저장·해제, 본인 리뷰 삭제, 계정 탈퇴는 로그인이 필요합니다.

### 관리자 화면

- `/admin`: 대시보드
- `/admin/collection`: 수집
- `/admin/parsing`: 파싱
- `/admin/review`: 검토 작업판
- `/admin/review/results`: 검증 결과 수정
- `/admin/documents`: 문서 현황
- `/admin/map-issues`: 지도 문제 항목
- `/admin/photos`: 사진 관리
- `/admin/logs`: 운영 로그

### 사용자 사진

- `/restaurants/{id}/photos/add`: 로그인 사용자의 사진 등록 화면
- `/mypage`: 본인이 등록한 사진의 설명·파일 수정 및 삭제

### 주요 운영 API

- `POST /ops/run-daily`
- `POST /ops/run-busan-live`
- `POST /ops/collection-plans`
- `POST /ops/collection-plans/{id}/run`
- `POST /ops/collection-plans/{id}/retry-failed`
- `POST /ops/collection-plans/{id}/parse`
- `POST /ops/collection-plans/{id}/retry-parse-failed`
- `POST /ops/verify-pending`
- `POST /ops/verify-collected`
- `GET /ops/dashboard`
- `GET /ops/collection-progress`
- `GET /ops/verification-status`
- `GET /ops/verification-progress`
- `GET /ops/api-usage`
- `GET /ops/logs`

## PostgreSQL 스키마와 마이그레이션

`app/schema.py`가 애플리케이션 데이터 모델의 기준입니다. 서버는 시작할 때 필수 테이블·컬럼·타입과 `app_schema_migrations`의 모든 필수 버전을 확인하고 불일치하면 중단합니다. 일반 서버 시작은 DDL을 자동 적용하지 않습니다.

마이그레이션 SQL 검토:

```bash
.venv/bin/python -m scripts.render_postgres_schema > /tmp/public_restaurant-0001.sql
```

새 DB 또는 기존 PostgreSQL DB에 누락된 번호형 마이그레이션을 적용할 때는 먼저 custom-format 백업과 복원 목록을 검증합니다.

```bash
pg_dump --format=custom --dbname="$DATABASE_URL" --file=/secure-backup/public-restaurant.dump
pg_restore --list /secure-backup/public-restaurant.dump >/dev/null
.venv/bin/python -m scripts.init_db --apply
.venv/bin/python -m scripts.check_db_schema
```

PostGIS는 현재 숫자 위도·경도 bounds 검색에 필요하지 않습니다. `pg_trgm`은 선택 기능이며 `database/migrations/0002_optional_pg_trgm.sql`로 분리되어 있습니다.

## 테스트

전체 테스트:

```bash
.venv/bin/python -m unittest discover -s tests
```

전체 테스트는 격리 PostgreSQL에서만 실행됩니다. 안전을 위해 테스트 DB 이름에는 반드시 `test`가 포함되어야 하며, 각 테스트 전에 해당 DB의 애플리케이션 테이블을 초기화합니다.

```bash
TEST_DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_dev_test' \
  .venv/bin/python -m unittest discover -s tests
```

주요 검증 범위:

- 파이프라인 idempotency와 배치 재시도
- 수집 계획 생성, 원문 저장, 파싱 상태 전이
- 후보 자동 검증과 관리자 승인·병합·반려
- 공개 지도, 검색, 랭킹, 핀 군집화, 상세 응답
- 리뷰 제한, 신고, AI 요약, 저장 가게, 마이페이지, 계정 탈퇴
- 관리자 인증·권한과 운영 API 라우팅
- 외부·관리자 사진 비노출, 사용자 사진 소유권과 등록·수정·삭제
- PostgreSQL 스키마 드리프트 차단과 모든 환경의 PostgreSQL 강제

## 운영 시 확인할 사항

- GitHub `main` 병합 후 N150 업데이트는 `sudo /srv/app/bin/deploy-public-restaurant`로 실행합니다. 최초 설치와 복구용 수동 절차는 [N150 자가 운영 절차서](database/N150_SELF_SERVICE_RUNBOOK.md)를 따릅니다.
- 배포 스크립트는 테스트, PostgreSQL 백업·복원 목록 검증, release에 포함된 검토된 번호형 마이그레이션 적용, 운영 스키마 호환 검사, 별도 포트 사전 점검, 원자적 release 전환, HTTP 검증과 실패 시 코드 롤백을 수행합니다.
- 운영 URL은 <https://gonggibap.com>이며 `APP_ENV=production`을 사용합니다.
- 모든 환경은 PostgreSQL `DATABASE_URL` 없이는 시작하지 않으며, 운영 환경은 추가로 유효한 `PRIVACY_CONTACT_EMAIL`을 요구합니다.
- DB 비밀번호, OAuth secret, API 키, 세션 키는 배포 환경의 secret으로만 주입합니다.
- PostgreSQL 스키마 변경은 서버 시작과 분리되어 있습니다. 배포 시 운영 DB 백업 검증을 먼저 마친 뒤 저장소에 포함된 검토된 마이그레이션만 적용하고, 호환 결과와 HTTP 상태를 확인합니다.
- 라이브 수집은 부산시 게시판 구조와 첨부 포맷에 의존합니다. 지원하지 않는 XLS, DRM, HWP, PDF는 DLQ에 남겨 parser 확장 대상으로 관리합니다.
- 자동 검증은 근거가 부족한 후보를 공개하지 않고 관리자 검토로 넘깁니다.
- 외부·관리자 사진은 공개하지 않습니다. 사용자 사진은 직접 촬영 또는 게시 권한 확인을 거쳐 등록하도록 안내합니다.

## 현재 제한과 다음 단계

- 부산시 본청 외 산하기관·공기업·출자출연기관으로 수집 대상 확대
- HWP, PDF, 구형 XLS parser 추가
- 계정 병합과 사용자 운영 도구 고도화
- 관리자 작업의 CSRF 방어와 세부 권한 분리 강화
- 선택적 PostGIS 도입과 공간 검색 고도화
- 사용자 사진 신고·관리자 숨김 처리와 이미지 최적화 고도화

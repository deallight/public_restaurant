# 부산 공공기관 업무추진비 맛집 지도

부산 지역 공공기관의 업무추진비 공개 문서를 수집해, 실제 음식점 방문 내역을 검증하고 지도·검색·랭킹으로 보여주는 서비스 MVP입니다. 흩어진 문서형 공공데이터를 바로 지도에 올리지 않고 `수집 -> 파싱 -> 후보 추출 -> 검증 -> 수동검토 -> 공개` 흐름으로 처리해 데이터 신뢰도를 높이는 데 초점을 둡니다.

현재 저장소는 Python 표준 라이브러리 기반 웹앱과 PostgreSQL 기본 개발·운영 백엔드를 포함합니다. `DATABASE_URL`로 백엔드를 선택하며 `APP_ENV=production`에서는 PostgreSQL만 허용합니다. SQLite는 기존 데이터 이관, 롤백, 호환성 회귀 테스트를 위해 유지합니다.

## 현재 구현 범위

- 공개 지도: 검증 완료된 음식점을 지도, 목록, 검색, 카테고리 필터, 랭킹으로 제공합니다.
- 사용자 기능: 음식점 상세 조회, 방문자 리뷰 작성, 리뷰 신고, 네이버/구글 OAuth 연동 준비를 포함합니다.
- 관리자 기능: 수집 현황 대시보드, 수집·검증 작업판, 문서별 수집 현황, 후보 데이터 검토, 지도 노출 이슈, 운영 로그 화면을 제공합니다.
- 데이터 수집: 부산광역시 업무추진비 통합 게시판(`https://www.busan.go.kr/ghopen12`)을 대상으로 목록 탐색, 상세 HTML 저장, 첨부파일 다운로드, XLSX/HTML 표 파싱을 수행합니다.
- 수집 계획: 기간별 문서 목록을 먼저 스캔한 뒤 배치 단위로 수집하고, 실패 문서를 재시도할 수 있습니다.
- 검증 파이프라인: 지출 행을 정규화하고 음식점 후보를 만든 뒤 규칙, 별칭 기억, 네이버 장소 검색, NCP Maps 지오코딩, 공공데이터포털 인허가 API를 근거로 판단합니다.
- 수동검토: 자동 승인·반려가 어려운 후보는 `manual_review_tasks`로 넘기고, 관리자가 신규 승인·기존 음식점 병합·반려를 선택합니다.
- 감사/운영 기록: API 호출 요약, 인허가 스냅샷, 결정 감사 로그, dead letter queue, 배치 실행 이력을 남깁니다.

## 기술 구조

이 프로젝트는 프레임워크보다 데이터 흐름 검증에 집중한 MVP입니다.

- 웹 서버: Python 표준 라이브러리 `http.server`의 `ThreadingHTTPServer`
- 기본 개발·운영 DB: PostgreSQL 16 이상, `psycopg` 3 기반 공통 연결 어댑터
- SQLite: 기존 DB 이관, 롤백, 호환성 회귀 테스트용. `DATABASE_URL`이 없을 때만 `APP_DB_PATH`를 사용
- 프론트엔드: 서버 렌더링 HTML 문자열과 정적 JavaScript/CSS
- 지도: `NAVER_MAP_KEY` 또는 `NAVER_MAPS_CLIENT_ID`가 있으면 네이버 지도 JS API 사용, 없으면 로컬 fallback 지도 사용
- 외부 연동: Naver Search Local, NCP Maps Geocoding, Naver Login, Google OAuth, 공공데이터포털 인허가 API
- 테스트: `unittest` 기반, 외부 API는 fake client로 대체

핵심 모듈은 다음과 같습니다.

- `app/server.py`: CLI 진입점
- `app/http_server.py`: HTTP 라우팅, 앱 구성, 운영 작업 실행
- `app/services.py`: 공개 API, 관리자 API, 운영 API의 비즈니스 로직
- `app/pipeline.py`: 문서 수집, 파싱, 정규화, 후보 생성, 검증, 저장
- `app/agents.py`: 음식점 여부 판단, 장소 후보 비교, `VerificationDecision` 생성
- `app/integrations.py`: 네이버, NCP Maps, 공공데이터포털, OAuth 연동 클라이언트
- `app/xlsx_parser.py`: 업무추진비 첨부 표 파싱
- `app/source_catalog.py`: 현재/예정 수집 대상 기관 카탈로그
- `database/`: PostgreSQL 차이 분석, 마이그레이션, 이관·롤백 runbook과 기존 설계 후보 SQL

## 빠른 실행: Mac PostgreSQL 개발 환경

Python 3.10 이상과 PostgreSQL 16 이상을 권장합니다. 기본 개발 DB는 Mac 로컬의 `public_restaurant_dev` PostgreSQL입니다. 정상 앱 시작은 DDL을 자동 실행하지 않습니다.

최초 한 번, 프로젝트 가상환경과 PostgreSQL 드라이버를 준비합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Mac 로컬 클러스터는 `/Users/deallight/develop/server_pc/.postgres`에 두고, 개발 DB는 `public_restaurant_dev`를 사용합니다. `.env`에는 아래 설정을 둡니다. `.env`는 Git에 포함하지 않습니다.

```text
APP_ENV=development
DATABASE_URL=postgresql:///public_restaurant_dev?host=/Users/deallight/develop/server_pc/.postgres/socket
```

새 개발 DB에 스키마를 적용할 때만 명시적으로 실행합니다.

```bash
.venv/bin/python -m scripts.init_db --apply
.venv/bin/python -m scripts.check_db_schema
```

평소 개발 서버 실행은 다음 명령을 사용합니다.

```bash
.venv/bin/python -m app.server --host 127.0.0.1 --port 8000
```

`source .venv/bin/activate`로 가상환경을 활성화했다면 마지막 명령은 `python3 -m app.server --host 127.0.0.1 --port 8000`으로 실행해도 됩니다.

Mac을 재시작한 뒤 PostgreSQL이 실행 중이지 않다면 다음으로 개발 클러스터를 시작합니다.

```bash
pg_ctl -D /Users/deallight/develop/server_pc/.postgres/data \
  -o "-p 5432 -k /Users/deallight/develop/server_pc/.postgres/socket -h 127.0.0.1" \
  -l /Users/deallight/develop/server_pc/.postgres/postgres.log start
```

브라우저에서 다음 주소를 엽니다.

- 공개 지도: `http://127.0.0.1:8000/`
- 관리자 대시보드: `http://127.0.0.1:8000/admin`
- 수집·검증 작업판: `http://127.0.0.1:8000/admin/workflow`
- 후보 검토: `http://127.0.0.1:8000/admin/review`
- 문서 현황: `http://127.0.0.1:8000/admin/documents`
- 지도 이슈: `http://127.0.0.1:8000/admin/map-issues`
- 운영 로그: `http://127.0.0.1:8000/admin/logs`

`scripts.run_daily`는 로컬 fixture 데이터를 기준으로 파이프라인을 실행합니다. 서버를 띄운 뒤 HTTP API로도 같은 작업을 호출할 수 있습니다.

```bash
curl -X POST http://127.0.0.1:8000/ops/run-daily
curl http://127.0.0.1:8000/api/map/restaurants
```

## 환경 변수

프로젝트 루트의 `.env` 파일은 서버 시작 시 자동으로 읽습니다. `.env`와 `var/`는 git에서 제외되어 있으며, 실제 키는 저장소에 커밋하지 않습니다. 필요한 항목은 `.env.example`을 기준으로 채웁니다.

| 변수 | 용도 |
| --- | --- |
| `APP_HOST`, `APP_PORT` | 서버 바인딩 주소와 포트 |
| `APP_ENV` | `development` 또는 `production`; production은 PostgreSQL을 강제 |
| `DATABASE_URL` | 기본 개발·운영 DB URL. Mac 개발은 Unix socket URL을, N150 운영은 secret 환경의 `postgresql://...` URL을 사용 |
| `APP_DB_PATH` | `DATABASE_URL`이 없을 때만 쓰는 SQLite 호환성·롤백 DB 경로 |
| `NAVER_MAP_KEY` | 네이버 지도 JS API Client ID. `NAVER_MAPS_CLIENT_ID`로도 대체 가능 |
| `NAVER_MAPS_CLIENT_ID`, `NAVER_MAPS_CLIENT_SECRET` | NCP Maps Geocoding |
| `NAVER_SEARCH_CLIENT_ID`, `NAVER_SEARCH_CLIENT_SECRET` | Naver Search Local API |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 인허가 API 일반 인증키 |
| `NAVER_SEARCH_DAILY_QUOTA` | 관리자 대시보드의 네이버 검색 일일 무료 한도 기준값 (기본 `25000`) |
| `NAVER_API_HUB_MONTHLY_QUOTA` | 관리자 대시보드의 NAVER API HUB 월간 한도 기준값 (기본 `775000`) |
| `DATA_GO_KR_DAILY_QUOTA` | 관리자 대시보드의 공공데이터포털 일일 한도 기준값 (기본 `10000`) |
| `GROQ_DAILY_QUOTA` | 관리자 대시보드의 Groq 일일 요청 한도 기준값 (기본 `1000`) |
| `NAVER_LOGIN_CLIENT_ID`, `NAVER_LOGIN_CLIENT_SECRET`, `NAVER_LOGIN_REDIRECT_URI` | 네이버 로그인 |
| `APP_SESSION_SECRET` | 로그인 세션 서명 전용 비밀값(선택, 미설정 시 로그인 provider secret에서 별도 키 파생) |
| `PRIVACY_CONTACT_EMAIL` | 개인정보처리방침·이용약관에 공개할 문의 이메일. 운영 환경에서는 필수 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | 구글 OAuth |
| `REVIEW_RATE_LIMIT_PER_HOUR` | 같은 음식점에 대한 사용자/IP 기준 시간당 리뷰 제한 |

실서비스 검증은 네이버 Search, NCP Maps Geocoding, 공공데이터포털 인허가 API 키가 있을 때 활성화됩니다. 키가 없으면 테스트/로컬 fixture 중심으로 동작하며, 지도는 fallback 화면을 사용합니다.

### 네이버 로그인 설정

네이버 Developers 애플리케이션의 Callback URL과 `NAVER_LOGIN_REDIRECT_URI`는 정확히 같아야 합니다. 로컬 기본값은 `http://127.0.0.1:8000/auth/callback/naver`이며, 운영 환경에서는 실제 HTTPS 도메인의 같은 경로를 등록합니다.

발급받은 `NAVER_LOGIN_CLIENT_ID`, `NAVER_LOGIN_CLIENT_SECRET`은 코드나 저장소에 넣지 않고 실행 환경의 secret으로 주입합니다. `APP_SESSION_SECRET`도 별도 secret으로 설정하는 것을 권장하며, 생략하면 로그인 provider secret에서 용도가 분리된 세션 서명 키를 파생합니다.

서버를 시작한 뒤 `http://127.0.0.1:8000/login`에서 확인합니다.

별도 회원가입 화면은 없습니다. 네이버 인증 후 등록된 회원 정보가 있으면 기존 계정으로 로그인하고, 없으면 회원 계정을 자동 생성한 뒤 바로 로그인합니다.

개인정보처리방침은 `/privacy`, 이용약관은 `/terms`에서 공개됩니다. 공개 리뷰 등록에는 AI 요약 처리를 위한 별도 확인·동의가 필요합니다. 사용자는 마이페이지에서 계정을 삭제할 수 있습니다. 탈퇴하면 OAuth 연결과 저장 목록은 즉시 삭제되고 작성 리뷰는 작성자와 IP 식별값이 제거된 익명 리뷰로 남으므로, 리뷰 본문도 지우려면 탈퇴 전에 리뷰를 먼저 삭제해야 합니다. 운영 백업은 최대 30일 안에 순차 삭제하는 정책을 전제로 합니다.

## 데이터 파이프라인

기본 데이터 흐름은 다음과 같습니다.

1. `source_registry`에서 수집 대상을 찾습니다.
2. 부산시 업무추진비 게시판 목록과 상세 페이지를 수집합니다.
3. 상세 HTML과 첨부파일을 `var/raw/busan_city/`에 저장합니다.
4. XLSX 또는 HTML 표에서 집행일자, 사용처, 목적, 금액, 인원, 결제방법을 추출합니다.
5. `raw_documents`와 `expense_records`에 원문 문서와 지출 행을 저장합니다.
6. 음식점 가능성이 있는 행을 `restaurant_candidates`로 만듭니다.
7. 규칙, 별칭 기억, 기존 검증 근거, 네이버 장소 후보, 인허가 정보를 조합해 `VerificationDecision`을 만듭니다.
8. 승인된 후보는 `restaurants`와 `restaurant_expense_links`에 반영하고, 애매한 후보는 `manual_review_tasks`에 남깁니다.
9. 관리자 검토 결과는 `decision_audit_logs`, `alias_memory`, `place_verifications` 등에 근거와 함께 기록됩니다.

자동 검증은 DB에 직접 임의 데이터를 쓰는 방식이 아니라, `VerifierAgent`가 `VerificationDecision`을 반환하고 파이프라인이 그 결정을 저장하는 구조입니다. 판단 근거가 부족하면 `needs_review`로 넘기는 보수적 흐름을 기본값으로 둡니다.

## 라이브 수집과 검증

부산시 게시판 라이브 수집 smoke test는 임시 DB와 임시 raw 디렉토리를 사용합니다.

```bash
python3 -m scripts.smoke_busan_live
```

서버 실행 중에는 운영 API로 수집 계획을 만들고 배치 단위로 처리할 수 있습니다.

```bash
curl -X POST http://127.0.0.1:8000/ops/collection-plans \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2026-01-01","end_date":"2026-12-31","batch_size":20}'

curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/run \
  -H "Content-Type: application/json" \
  -d '{"repeat":true,"max_batches":100}'

curl -X POST http://127.0.0.1:8000/ops/collection-plans/1/retry-failed \
  -H "Content-Type: application/json" \
  -d '{"max_batches":20}'
```

검증 대기 후보는 CLI 또는 HTTP API로 재검증할 수 있습니다.

```bash
python3 scripts/verify_pending.py --limit 100

curl -X POST http://127.0.0.1:8000/ops/verify-pending \
  -H "Content-Type: application/json" \
  -d '{"limit":100,"sort":"verification_oldest"}'

curl -X POST http://127.0.0.1:8000/ops/verify-collected \
  -H "Content-Type: application/json" \
  -d '{"limit":100,"sort":"used_date_desc"}'
```

현재 인허가 검증에 사용하는 공공데이터포털 endpoint는 다음 3개입니다.

- 일반음식점: `https://apis.data.go.kr/1741000/general_restaurants/info`
- 휴게음식점: `https://apis.data.go.kr/1741000/rest_cafes/info`
- 제과점영업: `https://apis.data.go.kr/1741000/bakeries/info`

공통 요청 파라미터는 `serviceKey`, `pageNo`, `numOfRows`, `returnType=json`, `cond[BPLC_NM::LIKE]`입니다. 부산 데이터는 음식점 인허가 관리 주체가 구군 단위로 흩어져 있어 `cond[OPN_ATMY_GRP_CD::EQ]` 대신 `cond[ROAD_NM_ADDR::LIKE]=부산`으로 좁힙니다.

## 주요 화면과 API

공개 API:

- `GET /api/map/restaurants?q=&category=&region=&bounds=&saved_only=` (`saved_only=1`은 로그인 필요)
- `GET /api/restaurants/{id}`
- `GET /api/rankings?category=&region=&period=`
- `GET /api/search?q=&category=&bounds=`
- `POST /api/restaurants/{id}/reviews`
- `POST /api/reviews/{id}/report`
- `POST /api/restaurants/{id}/save`
- `POST /api/restaurants/{id}/unsave`
- `POST /api/reviews/{id}/delete`

인증 API:

- `GET /login`
- `GET /mypage`
- `GET /auth/google/start`
- `GET /auth/naver/start`
- `GET /auth/callback/google`
- `GET /auth/callback/naver`
- `GET /auth/session`
- `POST /auth/logout`

운영 API:

- `POST /ops/run-daily`
- `POST /ops/run-busan-live`
- `POST /ops/collection-plans`
- `POST /ops/collection-plans/{plan_id}/run`
- `POST /ops/collection-plans/{plan_id}/retry-failed`
- `POST /ops/verify-pending`
- `POST /ops/verify-collected`
- `GET /ops/sources`
- `GET /ops/dashboard?start_date=&end_date=`
- `GET /ops/collection-progress?start_date=&end_date=`
- `GET /ops/verification-status`
- `GET /ops/verification-progress`
- `GET /ops/logs?plan_id=&limit=`
- `GET /ops/batches/{batch_id}`

관리자 API:

사진 관리 화면과 아래 `/admin/photos` API는 로그인한 사용자의 `users.role`이
`admin`일 때만 접근할 수 있다. 공개 음식점 상세 응답과
`/media/restaurant-images/...` 이미지 제공 경로는 계속 공개된다.

- `GET /admin/photos`: 음식점 사진 관리 화면
- `GET /admin/photos/restaurants`
- `GET /admin/photos/restaurants/{restaurant_id}`
- `POST /admin/photos/restaurants/{restaurant_id}/images`: 사진 추가 또는 교체
- `POST /admin/photos/restaurants/{restaurant_id}/images/{image_id}`: 설명·순서 수정
- `POST /admin/photos/restaurants/{restaurant_id}/images/{image_id}/delete`
- `GET /review`
- `POST /review/{id}/candidate`
- `POST /review/{id}/approve-new`
- `POST /review/{id}/merge/{restaurant_id}`
- `POST /review/{id}/reject`
- `GET /admin/candidates`
- `POST /admin/candidates/{candidate_id}`
- `POST /admin/candidates/{candidate_id}/geocode`
- `GET /admin/documents/data`
- `GET /admin/documents/{document_id}/data`
- `GET /admin/map-issues/data`
- `GET /admin/review-reports`
- `POST /admin/accounts/{user_id}/merge`

## 데이터 모델 요약

핵심 테이블 흐름:

```text
raw_documents
  -> expense_records
  -> restaurant_candidates
  -> place_verifications
  -> restaurants
  -> restaurant_expense_links
```

운영 보조 테이블:

- `source_registry`: 수집 대상 기관과 adapter 설정
- `collection_plans`, `collection_plan_documents`: 기간별 수집 계획과 문서별 진행 상태
- `batch_jobs`: 파이프라인 실행 이력
- `manual_review_tasks`: 관리자 검토 큐
- `permit_snapshots`: 인허가 API 응답 캐시
- `api_call_logs`: 외부 API 호출 요약
- `alias_memory`: 수동 승인 이후 재사용할 음식점 별칭
- `decision_audit_logs`: 승인·병합·반려 등 결정 감사 로그
- `dead_letter_queue`: 파싱/검증 실패 항목
- `restaurant_reviews`, `review_reports`: 사용자 리뷰와 신고
- `user_saved_restaurants`: 로그인 사용자가 저장한 관심 가게
- `restaurant_admin_images`: 관리자 등록 음식점 사진의 파일 정보와 노출 순서

관리자 사진 파일은 기본적으로 `var/restaurant_images`에 저장되며
`RESTAURANT_IMAGE_UPLOAD_DIR`로 변경할 수 있습니다. PNG, JPEG, WebP 형식을
가게당 최대 4장, 파일당 최대 8MB까지 등록할 수 있습니다. 상세 화면에서는
관리자 사진을 먼저 사용하고 남은 자리만 네이버 이미지 검색 결과로 채웁니다.

SQLite 스키마와 PostgreSQL 호환 기준은 `app/schema.py`입니다. PostgreSQL 0001은 `database/migrations/m0001_app_compatible.py`가 생성하며, 기존 `database/postgres_*.sql`은 설계 후보로만 유지합니다. 차이 분석은 `database/POSTGRESQL_GAP_ANALYSIS.md`, 실행·이관·롤백 절차는 `database/POSTGRESQL_RUNBOOK.md`를 따릅니다.

## 테스트

전체 테스트는 다음 명령으로 실행합니다.

```bash
python3 -m unittest discover -s tests
```

격리된 PostgreSQL 테스트 DB를 준비한 경우 전체 계약을 함께 검증합니다. 안전을 위해 DB 이름에 `test`가 포함되지 않으면 통합 테스트가 실행되지 않습니다.

```bash
TEST_DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test' \
  .venv/bin/python -m unittest discover -s tests
```

외부 API는 테스트에서 실제 호출하지 않고 fake client로 대체합니다. 주요 검증 범위는 다음과 같습니다.

- 파이프라인 idempotency
- 수집 계획 생성과 배치 실행
- 검증 대기 후보 재검증
- 네이버/인허가 결과 기반 승인·반려·수동검토 판단
- 공개 지도, 검색, 랭킹, 리뷰 제한
- 관리자 화면과 운영 API 라우팅
- 설정 로딩과 `.env` 처리
- SQLite/PostgreSQL SQL 변환 및 운영 PostgreSQL 강제
- PostgreSQL fixture 파이프라인, 지도·검색·랭킹·상세·리뷰·관리자 수동검토 계약

## PostgreSQL 이관과 검증

원본 SQLite 운영 파일에는 이관 도구가 직접 접근하지 않습니다. 쓰기를 중지하고 `.backup`으로 만든 복사본만 사용합니다. 명령의 기본 동작은 dry-run이며 행 값이나 접속 비밀을 출력하지 않습니다.

```bash
sqlite3 var/public_restaurant.db '.backup /secure-backup/public_restaurant-cutover.db'

python3 -m scripts.migrate_sqlite_to_postgres \
  --source-copy /secure-backup/public_restaurant-cutover.db --batch-size 500
python3 -m scripts.migrate_sqlite_to_postgres \
  --source-copy /secure-backup/public_restaurant-cutover.db --apply --batch-size 500
python3 -m scripts.compare_databases \
  --sqlite-copy /secure-backup/public_restaurant-cutover.db
```

이관은 메모리 사용량이 테이블 크기에 비례하지 않도록 배치 스트리밍합니다. 비교기는 스키마, 핵심 테이블 레코드 수·SHA-256 지문, 공개 지도·검색·랭킹·상세, 관리자 후보·수집·검증·문서·로그 응답을 비교합니다. 스키마 불일치나 이관 행 오류는 실패 종료 코드로 처리합니다. 운영 앱도 시작할 때 0001의 필수 테이블·컬럼·PostgreSQL 타입을 확인합니다. 반대 방향 롤백 파일은 `scripts.export_postgres_to_sqlite`로 새 경로에만 생성합니다.

PostGIS는 현재 숫자 위경도 bounds 검색에 필요하지 않습니다. `pg_trgm`은 대규모 검색 성능용 선택 기능이며 `database/migrations/0002_optional_pg_trgm.sql`로 분리되어 있습니다.

## 운영 주의

- `.env`, `var/`, `__pycache__/`, `.DS_Store`는 git에 올리지 않습니다.
- 현재 관리자 화면과 운영 API는 로컬 MVP 기준입니다. 외부 공개 전에는 인증, 권한, CSRF/세션 정책, 운영 로그 접근 제어를 별도로 보강해야 합니다.
- 라이브 수집은 부산시 게시판 구조와 첨부 포맷에 의존합니다. 지원하지 않는 구형 XLS, DRM 파일, HWP/PDF 등은 DLQ에 남기고 parser를 확장하는 방식으로 처리합니다.
- 자동 검증은 정확도가 낮은 후보를 억지로 승인하지 않고 `needs_review`로 넘기는 보수적 정책을 유지합니다.
- 실제 API 키는 환경변수 또는 배포 환경의 secret store로만 주입합니다.

## 향후 확장 방향

- 부산시 본청 외 산하기관, 지방공기업, 출자·출연기관, 교육청, 경찰·검찰, 법원·선관위, 중앙부처 부산 지방청, 부산권 국가공공기관으로 수집 대상 확대
- HWP/PDF/구형 XLS 첨부 parser 추가
- 관리자 인증과 역할 기반 권한 분리
- 선택적 PostGIS 도입 후 공간 검색, 반경 필터, 지역별 랭킹 고도화
- 지도 품질 이슈 자동 탐지와 재검증 워크플로 강화
- 추천/큐레이션 화면과 사용자 피드백 기반 별칭·검증 품질 개선

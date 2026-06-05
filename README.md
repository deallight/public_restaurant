# 공공기관 업무추진비 맛집 지도 서비스

부산광역시 v1 범위의 업무추진비 맛집 지도 서비스 MVP입니다. 외부 패키지 설치 없이 실행되도록 Python 표준 라이브러리 HTTP 서버와 SQLite 개발 DB를 사용합니다. 운영 DB 설계는 `database/postgres_schema.sql`과 `database/postgres_app_extensions.sql`에 분리해 두었습니다.

## 실행

```bash
python3 -m scripts.run_daily
python3 -m app.server --host 127.0.0.1 --port 8000
```

수동검토 후보 재검증은 CLI에서도 실행할 수 있습니다.

```bash
python3 scripts/verify_pending.py --limit 100
```

브라우저에서 `http://127.0.0.1:8000`을 열면 공개 지도 화면을 볼 수 있습니다. `NAVER_MAP_KEY` 환경변수가 있으면 네이버 지도 JS API를 시도하고, 없으면 로컬 fallback 지도를 사용합니다.

관리자 화면은 `http://127.0.0.1:8000/admin`입니다.

## 실서비스 키 설정

비밀값은 git에 저장하지 말고 실행 환경변수로만 주입합니다.
`.env.example`은 항목명 확인용이며 실제 키를 채운 `.env` 파일은 git에서 제외됩니다.
서버는 시작 시 프로젝트 루트의 `.env`를 자동으로 읽습니다. `.env`를 수정한 뒤에는 서버를 재시작해야 반영됩니다.

```bash
export NAVER_MAP_KEY="NCP Maps Client ID"
export NAVER_MAPS_CLIENT_ID="NCP Maps Client ID"
export NAVER_MAPS_CLIENT_SECRET="NCP Maps Client Secret"
export NAVER_SEARCH_CLIENT_ID="Naver Developers Search Client ID"
export NAVER_SEARCH_CLIENT_SECRET="Naver Developers Search Client Secret"
export NAVER_LOGIN_CLIENT_ID="Naver Login Client ID"
export NAVER_LOGIN_CLIENT_SECRET="Naver Login Client Secret"
export NAVER_LOGIN_REDIRECT_URI="http://127.0.0.1:8000/auth/callback/naver"
export GOOGLE_CLIENT_ID="Google OAuth Client ID"
export GOOGLE_CLIENT_SECRET="Google OAuth Client Secret"
export GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/auth/callback/google"
export DATA_GO_KR_SERVICE_KEY="공공데이터포털 serviceKey"
```

`DATA_GO_KR_SERVICE_KEY`는 공공데이터포털 활용신청 후 발급받는 일반 인증키입니다. 라이브 검증은 네이버 Search, NCP Maps Geocoding, 공공데이터포털 인허가 API를 사용합니다. API 호출 결과는 `api_call_logs`에 요약 기록하고, 인허가 조회 결과는 `permit_snapshots`에 캐시합니다.

현재 인허가 검증에 사용하는 공공데이터포털 endpoint는 다음 3개입니다.

- 일반음식점: `https://apis.data.go.kr/1741000/general_restaurants/info`
- 휴게음식점: `https://apis.data.go.kr/1741000/rest_cafes/info`
- 제과점영업: `https://apis.data.go.kr/1741000/bakeries/info`

공통 요청 파라미터는 `serviceKey`, `pageNo`, `numOfRows`, `returnType=json`, `cond[BPLC_NM::LIKE]`입니다. 부산 데이터는 음식점 인허가 관리 주체가 구군 단위로 흩어져 있어 `cond[OPN_ATMY_GRP_CD::EQ]` 대신 `cond[ROAD_NM_ADDR::LIKE]=부산`으로 좁힙니다.

## 주요 API

- `GET /api/map/restaurants`
- `GET /api/restaurants/{id}`
- `GET /api/rankings?category=&region=&period=`
- `GET /api/search?q=&category=&bounds=`
- `POST /api/restaurants/{id}/reviews`
- `POST /api/reviews/{id}/report`
- `GET /auth/google/start`, `GET /auth/naver/start`
- `POST /ops/run-daily`, `POST /ops/run-busan-live`, `POST /ops/verify-pending`, `GET /ops/batches/{batch_id}`
- `GET /review`
- `POST /review/{id}/approve-new`
- `POST /review/{id}/merge/{restaurant_id}`
- `POST /review/{id}/reject`
- `GET /admin/review-reports`
- `POST /admin/accounts/{user_id}/merge`

## 검증 원칙

- 외부 API는 테스트에서 실제 호출하지 않습니다. 네이버/인허가/AI는 fake client로 검증합니다.
- AI는 DB에 직접 쓰지 않고 `VerificationDecision` 제안만 반환합니다.
- 동일 fixture를 두 번 실행해도 원본 문서, 지출 행, 음식점, 방문 링크가 중복되지 않아야 합니다.
- 리뷰 도배 방지는 같은 음식점에 대해 동일 사용자/IP 해시 기준 시간당 3개로 제한합니다.

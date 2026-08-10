# N150 자가 운영 절차서

이 문서는 `public_restaurant`를 N150 Ubuntu 서버에서 **AI 도움 없이도** 안전하게 테스트·배포·점검하기 위한 실행 절차서다.

## 0. 현재 구성과 원칙

```text
Mac 개발 PC
  └─ GitHub main
       └─ N150 /srv/app/releases/<새 릴리스>
            ├─ deploy 계정으로 앱 실행
            ├─ systemd public-restaurant.service (127.0.0.1:8001)
            ├─ Nginx + HTTPS (https://gonggibap.com)
            └─ PostgreSQL 127.0.0.1:5432
                 ├─ public_restaurant_test  배포 검증용
                 └─ public_restaurant       실제 서비스용
```

현재 N150에서 이미 완료된 항목:

- Ubuntu 26.04, PostgreSQL 18.4, Nginx, Tailscale 설치
- 방화벽은 `tailscale0` 인바운드만 허용
- `deploy` 계정, GitHub 읽기 전용 배포 키, `/srv/app` 경로 준비
- `public_restaurant_test` PostgreSQL 스키마·실제 수집·외부 API·관리자 UI 검증
- `main` 브랜치에 PostgreSQL 전환 코드 병합
- 운영 DB `public_restaurant`, systemd 서비스, Nginx reverse proxy, 도메인/HTTPS 공개
- release별 배포와 `/srv/app/current`, `/srv/app/shared/var` 분리

> **가장 중요한 원칙**
>
> 1. 테스트는 이름에 `test`가 들어간 DB에서 먼저 한다.
> 2. 운영 DB의 DDL, 수집, 데이터 삭제는 아래의 “운영 전환” 단계에서만 한다.
> 3. 실행 중인 release 폴더에서 `git pull`하지 않는다. 새 release 폴더를 만든다.
> 4. 비밀번호·API 키·`.pgpass`·`.env`는 Git, 채팅, 터미널 출력에 남기지 않는다.

---

## 1. 용어와 경로

| 항목 | 값 | 의미 |
| --- | --- | --- |
| 서버 관리자 계정 | `deallight` | `sudo`를 사용하는 사람 계정 |
| 앱 실행 계정 | `deploy` | 앱·Git clone·배포 파일의 소유자. sudo 없음 |
| release 경로 | `/srv/app/releases/` | 커밋별 배포본을 보관하는 곳 |
| 공유 설정 경로 | `/srv/app/shared/` | release가 바뀌어도 유지할 `.env`, 로그, raw 파일 |
| 테스트 DB | `public_restaurant_test` | 배포 전 안전한 검증용 DB |
| 운영 DB | `public_restaurant` | 실제 공개 서비스 DB |
| DB 앱 계정 | `restaurant_app` | PostgreSQL에 접속하는 앱 전용 Role |
| DB 비밀번호 파일 | `/home/deploy/.pgpass` | `deploy`만 읽을 수 있는 DB 비밀번호 파일 |

### 데이터베이스 URL

```dotenv
# 테스트
DATABASE_URL=postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test

# 운영
DATABASE_URL=postgresql://restaurant_app@127.0.0.1:5432/public_restaurant
```

URL에는 비밀번호를 넣지 않는다. PostgreSQL 클라이언트와 앱은 `/home/deploy/.pgpass`에서 비밀번호를 찾는다.

---

## 2. 시작 전 점검

N150에 SSH로 접속한다.

```bash
ssh -i ~/develop/server_pc/.ssh/n150_codex \
  -o IdentitiesOnly=yes \
  deallight@100.76.123.43
```

서버·DB·방화벽 상태를 확인한다.

```bash
hostnamectl
sudo systemctl status postgresql --no-pager
sudo systemctl status nginx --no-pager
sudo ufw status verbose
sudo ss -ltnp '( sport = :5432 )'
```

기대 상태:

- PostgreSQL과 Nginx가 `active (running)`
- PostgreSQL은 `127.0.0.1:5432`에서만 대기
- UFW는 `tailscale0`만 인바운드 허용

`restaurant_app` 계정의 테스트 DB 접속도 확인한다.

```bash
sudo -u deploy psql \
  -h 127.0.0.1 \
  -U restaurant_app \
  -d public_restaurant_test \
  -c 'SELECT current_user, current_database();'
```

## 자동 배포 (권장)

일반적인 코드 업데이트는 아래 명령 하나로 배포한다.

```bash
sudo /srv/app/bin/deploy-public-restaurant
```

스크립트는 다음 순서로 작업한다.

1. 동시에 두 배포가 실행되지 않도록 잠근다.
2. 현재 systemd 프로세스만 `127.0.0.1:8001`을 점유하는지 확인한다.
3. GitHub `main`의 정확한 커밋으로 새 release를 만든다.
4. release 전용 `.venv`를 만들고 의존성을 설치한다.
5. `public_restaurant_test`에서 전체 테스트를 실행한다.
6. 운영 DB 스키마가 코드와 호환되는지 읽기 전용으로 검사한다.
7. 운영 PostgreSQL 백업을 만들고 `pg_restore --list`로 검증한다.
8. 운영 환경을 사용하되 별도 포트 `18001`에서 사전 HTTP 점검한다.
9. `/srv/app/current` 링크를 원자적으로 바꾸고 systemd를 재시작한다.
10. 내부·외부 HTTP와 실제 프로세스 release를 검사한다.
11. 전환 이후 검사에 실패하면 이전 release로 자동 롤백한다.

스크립트는 운영 DB에 DDL을 자동 적용하지 않는다. `scripts.check_db_schema`가
`compatible`이 아니면 배포를 중단하고, 검토된 마이그레이션을 별도로 적용해야
한다. 이전 release와 백업도 자동 삭제하지 않는다.

격리된 테스트 DB가 일시적으로 준비되지 않은 경우에만 위험을 인지하고 다음
옵션을 사용할 수 있다.

```bash
sudo /srv/app/bin/deploy-public-restaurant --skip-tests
```

### 최초 1회 설치

배포 스크립트가 `main`에 병합된 뒤 N150에서 한 번만 설치한다. 실행 중인
release의 작업 트리는 수정하지 않는다.

```bash
cd /tmp
DEPLOY_INSTALLER=$(mktemp /tmp/deploy-public-restaurant.XXXXXX)
sudo -u deploy -H git -C /srv/app/current fetch origin main
sudo -u deploy -H \
  git -C /srv/app/current show origin/main:scripts/deploy_n150.sh \
  > "$DEPLOY_INSTALLER"
sudo install -d -o root -g root -m 755 /srv/app/bin
sudo install -o root -g root -m 755 \
  "$DEPLOY_INSTALLER" \
  /srv/app/bin/deploy-public-restaurant
rm "$DEPLOY_INSTALLER"
```

설치 확인:

```bash
sudo /srv/app/bin/deploy-public-restaurant --help
```

정상 배포가 끝날 때마다 새 release에 포함된 스크립트로 설치본도 자동
갱신된다. 아래의 기존 수동 절차는 자동 배포가 중단됐을 때 원인을 확인하거나
복구할 때만 사용한다.

---

## 3. GitHub main에서 새 release 만들기

### 왜 새 폴더에 배포하는가

실행 중인 폴더를 수정하면 장애가 났을 때 이전 버전으로 돌아가기 어렵다. 따라서 새 커밋마다 새 폴더를 만들고, 이전 release는 남겨 둔다.

### 3-1. main의 최신 커밋 확인

```bash
sudo -u deploy git ls-remote \
  git@github.com:deallight/public_restaurant.git \
  refs/heads/main
```

출력의 앞 7자리를 release 이름에 사용한다. 예: `304a09c`.

### 3-2. 새 release clone

아래의 `<short-sha>`를 방금 확인한 값으로 바꾼다.

```bash
sudo -u deploy git clone \
  --branch main \
  --single-branch \
  git@github.com:deallight/public_restaurant.git \
  /srv/app/releases/main-<short-sha>
```

이후 명령에서 release 경로를 반복하지 않도록 셸 변수로 둔다.

```bash
export RELEASE=/srv/app/releases/main-<short-sha>
```

### 3-3. Python 가상환경과 의존성 설치

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha> &&
  python3 -m venv .venv &&
  .venv/bin/pip install -r requirements.txt
'
```

`python3 -m venv`가 실패하며 `ensurepip is not available`이라고 나오면 한 번만 설치한다.

```bash
sudo apt install python3.14-venv
```

---

## 4. 테스트 DB로 새 release 검증

### 4-1. 테스트 설정 연결

테스트용 설정 파일은 공유 경로에 둔다.

```text
/srv/app/shared/public_restaurant_test.env
```

새 release의 `.env`를 이 파일에 연결한다.

```bash
sudo -u deploy ln -s \
  /srv/app/shared/public_restaurant_test.env \
  /srv/app/releases/main-<short-sha>/.env
```

테스트 환경에는 최소 다음 항목이 있어야 한다.

```dotenv
APP_ENV=development
DATABASE_URL=postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test
NAVER_SEARCH_CLIENT_ID='값'
NAVER_SEARCH_CLIENT_SECRET='값'
NAVER_MAPS_CLIENT_ID='값'
NAVER_MAPS_CLIENT_SECRET='값'
DATA_GO_KR_SERVICE_KEY='값'
```

파일 권한을 확인한다.

```bash
sudo -u deploy chmod 600 /srv/app/shared/public_restaurant_test.env
sudo -u deploy bash -n /srv/app/shared/public_restaurant_test.env
```

`bash -n`이 아무 출력 없이 끝나면 따옴표 문법이 정상이다.

### 4-2. 스키마 적용과 검사

빈 테스트 DB에만 스키마를 처음 적용한다.

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha> &&
  .venv/bin/python -m scripts.init_db --apply
'
```

모든 테스트 DB에서는 호환성 검사를 실행한다.

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha> &&
  .venv/bin/python -m scripts.check_db_schema
'
```

정상 결과:

```json
{"status":"compatible","schema_issues":[]}
```

### 4-3. 전체 테스트 실행

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha> &&
  set -a && . ./.env && set +a &&
  TEST_DATABASE_URL="$DATABASE_URL" \
    .venv/bin/python -m unittest discover -s tests
'
```

전체 테스트가 통과해야 다음 단계로 간다. 실패하면 운영 DB에는 아무 작업도 하지 않는다.

### 4-4. 임시 웹 서버와 브라우저 확인

테스트 DB만 사용하는 임시 서버를 N150 loopback에 연다.

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha>
  nohup .venv/bin/python -u -m app.server \
    --host 127.0.0.1 \
    --port 18001 \
    > /srv/app/shared/postgres-test-server.log 2>&1 &
  echo "PID=$!"
'
```

서버가 실제 설정을 읽었는지 확인한다.

```bash
curl -sS http://127.0.0.1:18001/ops/verification-status
```

`naver_search`, `naver_maps_geocoding`, `data_go_kr_permit`가 모두 `true`여야 실제 외부 API 검증을 할 수 있다.

맥에서 SSH 터널을 열고 브라우저로 접속한다.

```bash
ssh -i ~/develop/server_pc/.ssh/n150_codex \
  -o IdentitiesOnly=yes \
  -N \
  -L 18001:127.0.0.1:18001 \
  deallight@100.76.123.43
```

브라우저 확인 주소:

- 공개 지도: `http://127.0.0.1:18001/`
- 관리자 검토: `http://127.0.0.1:18001/admin/review`

테스트가 끝나면 임시 서버만 종료한다. DB 데이터는 삭제되지 않는다.

```bash
sudo -u deploy kill <PID>
```

---

## 5. 실제 API·수집 검증 방법

### API 키 상태

값을 출력하지 않고 설정 여부만 검사한다.

```bash
curl -sS http://127.0.0.1:18001/ops/verification-status
```

### API 호출 로그

```bash
sudo -u deploy psql \
  -h 127.0.0.1 \
  -U restaurant_app \
  -d public_restaurant_test \
  -c "
SELECT
  provider,
  endpoint,
  COUNT(*) AS calls,
  COUNT(*) FILTER (WHERE success = 1) AS successful,
  COUNT(*) FILTER (WHERE success = 0) AS failed
FROM api_call_logs
GROUP BY provider, endpoint
ORDER BY provider, endpoint;
"
```

`success`는 현재 정수형(`1` 성공, `0` 실패)이므로 Boolean 조건으로 쓰지 않는다.

### 실패한 외부 API 원인

```bash
sudo -u deploy psql \
  -h 127.0.0.1 \
  -U restaurant_app \
  -d public_restaurant_test \
  -c "
SELECT status_code, error_message, COUNT(*) AS occurrences
FROM api_call_logs
WHERE success = 0
GROUP BY status_code, error_message
ORDER BY occurrences DESC;
"
```

`read operation timed out`은 외부 API의 일시적 지연이다. 키 오류는 보통 `401`·`403`, 서비스/한도 문제는 `429`처럼 HTTP 상태 코드가 남는다.

---

## 6. 운영 DB 전환 전 필수 체크리스트

아래 모두가 만족되기 전에는 `public_restaurant`에 `scripts.init_db --apply`를 실행하지 않는다.

- [ ] GitHub `main`의 새 release를 테스트 DB에서 검증했다.
- [ ] `scripts.check_db_schema`가 `compatible`을 반환했다.
- [ ] 전체 테스트가 통과했다.
- [ ] 공개 지도와 관리자 화면을 SSH 터널로 확인했다.
- [ ] 수집·파싱·검증·수동 승인 흐름을 확인했다.
- [ ] 네이버 장소 검색 호출 로그가 성공으로 기록됐다.
- [ ] 운영 DB에 SQLite 데이터를 이관하지 않고 새 수집을 시작한다는 방침을 확인했다.
- [ ] 운영 전환 시간과 롤백 담당자를 정했다.
- [ ] 운영 DB 작업에 대한 명시적 승인을 받았다.

---

## 7. 운영 DB 전환 절차

> 이 절은 실제 공개 전환 시간에만 실행한다. 현재는 실행하지 않는다.

### 7-1. 운영 설정 파일 만들기

운영 설정은 테스트 설정과 별도 파일을 사용한다.

```text
/srv/app/shared/public_restaurant.env
```

필수 항목 예시:

```dotenv
APP_ENV=production
DATABASE_URL=postgresql://restaurant_app@127.0.0.1:5432/public_restaurant
NAVER_SEARCH_CLIENT_ID='값'
NAVER_SEARCH_CLIENT_SECRET='값'
NAVER_MAPS_CLIENT_ID='값'
NAVER_MAPS_CLIENT_SECRET='값'
DATA_GO_KR_SERVICE_KEY='값'
```

파일 권한:

```bash
sudo -u deploy chmod 600 /srv/app/shared/public_restaurant.env
sudo -u deploy bash -n /srv/app/shared/public_restaurant.env
```

`/home/deploy/.pgpass`에는 운영 DB용 줄도 있어야 한다.

```text
127.0.0.1:5432:public_restaurant:restaurant_app:실제_비밀번호
```

```bash
sudo -u deploy chmod 600 /home/deploy/.pgpass
```

### 7-2. 운영 DB 스키마 적용

release의 `.env`가 운영 설정을 가리키는지 먼저 확인한 뒤 실행한다.

```bash
sudo -u deploy ln -sfn \
  /srv/app/shared/public_restaurant.env \
  /srv/app/releases/main-<short-sha>/.env
```

빈 운영 DB에 스키마를 적용한다.

```bash
sudo -u deploy bash -lc '
  cd /srv/app/releases/main-<short-sha> &&
  .venv/bin/python -m scripts.init_db --apply &&
  .venv/bin/python -m scripts.check_db_schema
'
```

`compatible`이 아닌 결과가 나오면 즉시 중단한다. 원인을 해결하기 전에는 앱을 시작하지 않는다.

### 7-3. 새 데이터 수집 시작

운영 DB는 SQLite 데이터를 이관하지 않고 부산시 원문부터 새로 수집한다.

처음에는 범위를 좁혀 관리자 수집 화면에서 수집 계획을 만들고, 작은 batch로 실행한다.

1. `/admin/collection`에서 시작일·종료일을 입력한다.
2. batch 크기는 20 이하로 시작한다.
3. 수집 → 파싱 → 검증 결과와 DLQ를 확인한다.
4. 자동 승인 데이터와 수동 검토 데이터를 확인한다.
5. 이상이 없을 때만 기간과 batch 범위를 넓힌다.

`/ops/run-busan-live`를 무제한 초기 수집 용도로 바로 호출하지 않는다. 큰 수집은 계획·batch 방식으로 나누어 진행한다.

---

## 8. 백업과 롤백

### PostgreSQL 백업

먼저 백업 디렉터리를 만든다.

```bash
sudo -u deploy install -d -m 700 /srv/app/shared/backups
```

운영 DB 백업:

```bash
sudo -u deploy pg_dump \
  -h 127.0.0.1 \
  -U restaurant_app \
  -d public_restaurant \
  -Fc \
  -f /srv/app/shared/backups/public_restaurant-$(date +%F-%H%M%S).dump
```

백업 파일 목록:

```bash
sudo -u deploy ls -lh /srv/app/shared/backups/
```

### 롤백 원칙

1. 앱·수집 작업을 먼저 멈춘다.
2. 이전 release는 삭제하지 않았으므로, 이전 release로 되돌린다.
3. DB를 되돌려야 한다면 현재 DB를 먼저 별도 백업한다.
4. `pg_restore --clean`은 기존 DB를 삭제·재생성할 수 있으므로, 운영 복구 승인 없이는 실행하지 않는다.

코드 롤백과 데이터 롤백은 별개다. 코드만 이전 release로 바꿔도 이미 저장된 PostgreSQL 데이터는 유지된다.

---

## 9. 장애 진단 순서

### 앱 화면이 열리지 않을 때

```bash
sudo systemctl status nginx --no-pager
sudo ss -ltnp '( sport = :8000 )'
sudo ss -ltnp '( sport = :8001 )'
sudo ss -ltnp '( sport = :18001 )'
tail -n 100 /srv/app/shared/postgres-test-server.log
```

운영 서버는 `127.0.0.1:8001`, 임시 테스트 서버는 `127.0.0.1:18001`을
사용한다. 임시 서버를 맥에서 보려면 SSH 터널이 필요하다.

### PostgreSQL 연결이 안 될 때

```bash
sudo systemctl status postgresql --no-pager
sudo ss -ltnp '( sport = :5432 )'
sudo -u deploy psql -h 127.0.0.1 -U restaurant_app -d public_restaurant_test -c 'SELECT 1;'
```

확인 항목:

- `/home/deploy/.pgpass`의 DB 이름·계정·권한(`600`)
- release `.env`가 올바른 공유 설정 파일을 가리키는지
- `DATABASE_URL`의 DB 이름이 `test`인지 운영 DB인지

### 모든 후보가 수동 검토로 갈 때

```bash
curl -sS http://127.0.0.1:18001/ops/verification-status
```

- `naver_search: false`이면 API 키가 실행 중인 서버에 반영되지 않은 것이다.
- `.env`를 수정한 뒤에는 테스트 서버를 종료하고 다시 시작한다.
- API 로그가 0건이면 이전 프로세스가 남아 있거나 외부 검증 전에 규칙으로 처리됐는지 확인한다.

---

## 10. 새 버전 배포 체크리스트

- [ ] 맥에서 코드·테스트 완료
- [ ] GitHub `main` 병합 완료
- [ ] N150에 새 release 폴더 clone
- [ ] 새 release에 `.venv`와 `requirements.txt` 설치
- [ ] 새 release를 `public_restaurant_test`로 연결
- [ ] 스키마 검사·전체 테스트·웹 화면 검증
- [ ] 이전 release 보존 확인
- [ ] 운영 전환 승인 후에만 운영 `.env`와 운영 DB 사용

## 관련 문서

- [PostgreSQL 전환·롤백 runbook](POSTGRESQL_RUNBOOK.md)
- [PostgreSQL 차이 분석](POSTGRESQL_GAP_ANALYSIS.md)
- [마이그레이션 안내](migrations/README.md)

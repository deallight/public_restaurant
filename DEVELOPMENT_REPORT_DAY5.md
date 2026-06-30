# 공공기관 업무추진비 맛집 지도 서비스 개발 진행 보고서 5일차

작성일: 2026-06-30

## 1. 5일차 개발 요약

5일차 개발은 Day4에서 만든 운영자용 후보 관리 흐름을 문서 단위 운영 화면과 수집·검증 작업판으로 확장하는 데 집중했다. Day4까지는 라이브 수집, 후보 보정, 지오코딩, 상태 전환을 다룰 수 있었다. Day5에서는 운영자가 문서 단위로 수집 결과를 조회하고, 특정 문서의 집행 행을 표 형태로 편집하며, 별도 작업판에서 수집과 검증 과정을 단계별로 실행·추적할 수 있게 했다.

Day4 보고서 이후 변경은 두 범위로 나뉜다.

```text
커밋됨:
d19949b Add public restaurant map reporting enhancements

미커밋 작업트리:
수집·검증 작업판, 검증 진행률 추적, 검증 정렬, 중복 수집 skip 보강
```

커밋된 변경은 11개 파일, 2,009줄 추가, 31줄 삭제다. 현재 미커밋 변경은 기존 파일 9개 수정과 신규 파일 2개 추가가 포함되어 있다.

## 2. 문서 게시판 추가

### 기관별 수집 문서 목록

관리자 화면에 기관별 수집 문서 게시판을 추가했다.

```text
GET /admin/documents
GET /admin/documents/data
```

문서 목록에서는 다음 조건으로 수집 문서를 조회할 수 있다.

- 기간: 시작일, 종료일
- 기관
- 수집 상태: 대기, 처리 중, 수집, 기존 문서, 실패
- 검색: 제목, 기관, 부서, URL
- 정렬: 작성일 최신순/오래된순, 수집일 최신순, 집행 내역 많은순, 검증률 낮은순
- 페이지네이션

목록에는 문서별 수집 상태, 원문 작성일, 수집일, 작성 기관, 문서 제목, 집행 내역 수, 검증 완료율, 승인/수동검토/반려 수치가 표시된다.

### 문서별 검증 진행률 표시

문서 목록 API는 각 문서의 후보 검증 상태를 집계한다.

```text
candidate_count
verification_pending
approved_count
review_count
rejected_count
verification_completed
verification_percent
```

이를 통해 운영자는 수집 문서가 단순히 저장되었는지만 보는 것이 아니라, 그 문서 안의 집행 행이 얼마나 검증되었는지 확인할 수 있다.

## 3. 문서 상세 편집 화면 추가

### 수집 문서 상세 페이지

문서 상세 페이지를 추가했다.

```text
GET /admin/documents/{document_id}
GET /admin/documents/{document_id}/data
```

문서 상세에서는 해당 문서의 집행 행과 후보를 표 형태로 조회한다.

지원 기능은 다음과 같다.

- 상태 필터: 전체, 수동검토, 승인, 반려
- 정렬: 원문 행 순서, 역순, 사용일 최신순, 금액 높은순, 상태순
- 행 검색: 후보 ID, 상호, 주소, 목적
- 페이지네이션
- 원문 상호명/주소 확인
- 보정 상호명/주소/분류 수정
- 후보 상태 변경
- 반려 사유와 검토 의견 입력
- 지오코딩 실행
- 변경 행 일괄 저장
- 선택 행 bulk 상태 변경

기존 후보 관리 화면이 전체 후보를 상태별로 보는 화면이었다면, 문서 상세 화면은 특정 수집 문서 하나를 열고 원문 행 단위로 DB 보정 작업을 하는 화면이다.

### 행 단위 DB 업데이트

문서 상세 화면은 후보별 `POST /admin/candidates/{candidate_id}`와 `POST /admin/candidates/{candidate_id}/geocode`를 사용한다. UI에서는 변경된 행을 dirty 상태로 추적하고, `DB 업데이트` 버튼을 누르면 변경된 행만 순차 저장한다.

이 흐름은 실제 운영자가 엑셀 원문을 검수하듯 문서 단위로 후보를 보정할 수 있게 한다.

## 4. 대시보드 리포팅 개선

### 수집/검증 이중 진행률 표시

대시보드의 우선순위별 진행 막대를 단일 수집률이 아니라 수집과 검증을 분리해 보여주도록 개선했다.

표시되는 진행률은 다음 두 축이다.

- 수집: 수집 완료, 실패, 처리 중, 대기
- 검증: 검증 전, 승인, 수동검토, 반려

우선순위 카드와 기관 상세 목록 모두에서 수집 상태와 검증 상태를 함께 볼 수 있다. 이를 통해 “문서는 수집됐지만 검증은 아직 안 됨” 같은 상태가 명확해졌다.

### 문서 게시판 연결

대시보드의 우선순위 상세 링크가 기존 로그 화면 중심에서 문서 게시판 중심으로 변경되었다.

- 전체 문서 게시판: `/admin/documents`
- 기관별 문서 필터 링크: `/admin/documents?institution=...`
- 기간 필터 유지

운영자는 대시보드에서 특정 우선순위나 기관을 클릭해 바로 해당 문서 목록으로 들어갈 수 있다.

## 5. 수집·검증 작업판 추가

### 독립 작업판 화면

새 관리자 화면을 추가했다.

```text
GET /admin/workflow
```

작업판은 수집과 검증을 한 화면에서 단계별로 실행하기 위한 전용 UI다.

상단 단계는 다음과 같다.

```text
1. 기간 설정
2. 목록 가져오기
3. 수집 배치
4. 문서 수집
5. 검증 배치
6. 결과 분류
```

이 화면은 Day4의 대시보드 버튼들을 더 운영 절차에 맞게 재구성한 것이다.

### 작업판 주요 기능

작업판에는 다음 기능이 들어갔다.

- 도시, 우선순위, 기관 선택
- 수집 시작일/종료일 지정
- 수집 배치 크기 지정
- 검증 배치 크기 지정
- 목록 가져오기
- 수집 실행
- 실패 문서 재수집
- 검증 실행
- 수집 문서 목록 표시
- 검증 후보 목록 표시
- 검증 진행 로그 표시
- 승인/수동검토/반려 결과 목록 표시

신규 프론트엔드 파일은 다음과 같다.

```text
app/static/workflow.js
```

현재 약 804줄 규모로, 작업판의 상태 관리, API 호출, 진행률 폴링, 목록 렌더링, 페이지네이션, 로그 표시를 담당한다.

## 6. 검증 진행률 추적 추가

### 서버 메모리 진행률 저장소

검증 배치의 진행 상태를 UI에서 볼 수 있도록 진행률 저장소를 추가했다.

```text
app/progress.py
```

`VerificationProgressStore`는 현재 검증 배치의 상태를 메모리에 저장한다.

저장하는 주요 값은 다음과 같다.

- active 여부
- batch_id
- job_name
- total
- processed
- summary
- 후보별 진행 상태
- 후보별 stage, label, percent, status, decision
- updated_at

조회 API는 다음과 같다.

```text
GET /ops/verification-progress
```

### 검증 단계별 progress event

`DailyPipeline.verify_collected`와 `verify_pending`에 progress callback을 추가했다. 검증 중 다음 단계에서 이벤트를 발행한다.

```text
batch_started
candidate_queued
candidate_step
candidate_done
batch_finished
```

후보별 단계는 다음처럼 나뉜다.

```text
원문 정규화
업무추진비 범위 조건
음식점 목적 조건
기존 승인 근거 확인
별칭 기억 확인
기존 검색 근거 확인
외부 API 검증
네이버 장소 검색 API
인허가 조회 API
검색 결과 조건 판단
경계값 최종 판단
검증 결과 저장
```

이 변경으로 검증 작업판에서 각 후보가 어떤 단계에 있는지 실시간에 가깝게 볼 수 있다.

## 7. 검증 정렬 및 후보 조회 개선

### 검증 대상 정렬 옵션 추가

`verify_collected`와 `verify_pending`에 정렬 옵션을 추가했다.

지원 정렬은 다음과 같다.

```text
verification_oldest
used_date_desc
used_date_asc
source_published_desc
source_published_asc
amount_desc
name_asc
id_desc
id_asc
```

운영자는 검증 대기 오래된순뿐 아니라 방문일 최신순, 문서 작성일순, 금액순 등으로 검증 우선순위를 바꿀 수 있다.

### 후보 API 확장

`GET /admin/candidates`가 작업판에서 쓰기 좋게 확장되었다.

추가 필터는 다음과 같다.

- start_date
- end_date
- institution
- status
- offset

또한 기존 상태별 그룹 외에 `selected` 페이지를 반환한다. 작업판은 이 값을 사용해 현재 선택한 상태의 후보 목록을 페이지네이션한다.

상태 구분은 다음과 같다.

- `pending`: 검증 전
- `needs_review`: 수동검토
- `verified`: 승인
- `rejected`: 반려

## 8. 중복 수집 방지 개선

수집 계획 생성 시 이미 수집된 원문 문서가 있으면 새 계획 문서를 `duplicate` 상태로 표시하고 기존 `raw_document_id`를 연결하도록 개선했다.

추가된 summary 값은 다음과 같다.

```text
documents_skipped_collected
```

이를 통해 같은 기간과 같은 문서를 다시 계획에 넣더라도 이미 수집된 문서를 다시 다운로드하지 않고, 계획 상태에서 “기존 문서”로 처리할 수 있다.

## 9. 지도 신고/문서 관리 연계

커밋된 `d19949b`에는 문서 관리 기능 외에도 운영 리포팅 성격의 개선이 포함되어 있다.

- 지도 이슈/문제 신고 관리 화면 연결
- 문서 게시판과 문서 상세 화면 추가
- 대시보드 진행률 표시 개선
- 문서별 검증 상태 집계
- HTTP 서버 라우팅 테스트 보강

PPT 설계 파일도 업데이트되었다.

```text
부산시 공공데이터 맛집 지도 플로우차트 및 ui 설계.pptx
```

파일 크기가 증가한 것으로 보아 Day5 UI 설계와 흐름도가 반영된 상태다.

## 10. 테스트 및 검증 결과

Day4 기준 전체 테스트는 80개였다. Day5 기준 전체 테스트는 96개로 확대되었고, 권한 밖 실행 기준으로 모두 통과했다.

```text
Ran 96 tests in 2.501s
OK
```

새로 검증된 주요 항목은 다음과 같다.

- `/admin/workflow` 화면 렌더링
- 작업판 주요 DOM 요소 존재 확인
- `/ops/verification-progress` 응답 구조 확인
- `verify_collected` 진행률 이벤트 발행
- 검증 정렬 옵션 적용
- 최신 방문일 우선 검증
- 수집 계획 생성 시 기존 수집 문서 duplicate 처리
- 문서 게시판 데이터 조회
- 문서 상세 데이터 조회
- 후보 API의 기간/상태/페이지네이션 필터
- 수집 대상 섹션이 수집 실행 섹션보다 위에 배치되는지 확인

HTTP 서버 테스트는 로컬 포트 바인딩이 필요하므로 권한 밖에서 실행했다.

## 11. 현재 상태

현재 진행 상황은 다음과 같다.

- Day1: MVP 파이프라인, 공개 API, 관리자 API, DB 스키마, 기본 테스트
- Day2: 별칭 기억, 주소 없는 후보 자동 승인, 비음식 목적 필터, 재검증
- Day3: 구조화 주소 매칭, 프랜차이즈 규칙, provider 후보 evidence, 관리자 후보 선택 승인
- Day4: 라이브 수집/검증 분리, 후보 보정 필드, 후보 전체 관리 API/UI, 지오코딩
- Day5: 문서 게시판, 문서 상세 행 편집, 대시보드 수집/검증 리포팅, 수집·검증 작업판, 검증 진행률 추적

현재 커밋 히스토리는 다음과 같다.

```text
d19949b Add public restaurant map reporting enhancements
9ad293f Add operations dashboard and verification controls
09fb861 Add day 3 development report
dba4eeb Expand verification workflow and UI reporting
600e3fd Improve restaurant verification alias matching
4648a12 Initial public restaurant application
```

현재 작업트리에는 아직 커밋되지 않은 변경이 남아 있다.

```text
수정됨:
app/agents.py
app/http_server.py
app/pipeline.py
app/services.py
app/static/styles.css
app/views.py
tests/test_http_server.py
tests/test_pipeline.py
PPT 설계 파일

신규:
app/progress.py
app/static/workflow.js
```

## 12. 리스크 및 다음 과제

### 진행률 저장소의 범위

현재 `VerificationProgressStore`는 서버 메모리 기반이다. 단일 프로세스 개발 환경에서는 충분하지만, 운영에서 멀티 프로세스나 재시작이 발생하면 진행 상태가 사라진다. 운영 배포 전에는 DB 기반 progress log 또는 batch event table로 옮길지 결정해야 한다.

### 작업판 API와 UI 안정화

`/admin/workflow`는 기능 범위가 넓다. 실제 브라우저에서 수집 목록, 검증 목록, 상태 로그, DB 결과 탭이 정상적으로 갱신되는지 시각 검증이 필요하다.

### 검증 정렬 기준 운영 검증

금액순, 방문일순, 문서 작성일순 검증은 운영 편의성을 높이지만, 검증 품질과 우선순위 정책에 영향을 준다. 어떤 정렬을 기본값으로 둘지 실제 운영 데이터 기준으로 결정해야 한다.

### 중복 문서 처리 정책

이미 수집된 문서를 duplicate로 연결하는 구조가 들어갔다. 같은 URL이지만 첨부파일이 수정된 경우, 또는 게시글 제목/첨부가 변경된 경우를 어떻게 감지할지 추가 정책이 필요하다.

### 문서 상세 대량 편집 UX

문서 상세에서 여러 행을 일괄 수정할 수 있으므로, 저장 실패 일부 발생, 지오코딩 실패, 잘못된 bulk 상태 변경에 대한 복구 UX가 필요하다.

## 13. 한 줄 결론

5일차 개발에서는 운영자가 데이터를 “후보 단위”뿐 아니라 “문서 단위”와 “작업 단계 단위”로 관리할 수 있게 되었다. 문서 게시판, 문서 상세 행 편집, 수집·검증 작업판, 검증 진행률 추적이 추가되면서 서비스는 실제 공공데이터 운영 업무를 수행할 수 있는 내부 운영 시스템에 가까워졌다.

# DynamoDB TTL 쓰기 경로 구현 스펙

- 문서 버전: 1.1
- 작성일: 2026-06-18
- 변경 이력: 1.1 — 보존기간을 단축 기본값으로 전환(로그성 테이블 단축, 캐시성 테이블 유지). S3 아카이브는 후속 작업으로 분리.
- 대상 브랜치: `feat/dynamodb-cache-ttl`
- 관련 문서: `reports/dynamodb_ttl_application_write_analysis_20260618_ko.md`, `infra/data-stack/template.yaml`
- 적용 범위: `src/**`, `tests/**`, 루트 `template.yaml` (인프라 배선 한정)

## 1. 배경과 현재 상태

`infra/data-stack/template.yaml`은 6개 만료 테이블에 `TimeToLiveSpecification`을 이미 설정해 두었다. 그러나 DynamoDB TTL은 **애플리케이션이 설정된 TTL 속성에 미래 epoch(초) 정수값을 기록한 항목**만 삭제 대상으로 삼는다. 현재 상태는 두 갈래로 갈린다.

- `auth_sessions`: 앱이 `src/auth/session_repository.py`에서 `expiresAt`(camelCase)를 정수 epoch로 기록 → TTL이 끝까지 정상 동작.
- 나머지 5개(`user_event_logs`, `agent_runs`, `festival_verify_cache`, `async_jobs`, `api_logs`): 테이블 TTL 설정(`expires_at`, `Enabled: true`)은 존재하나 **항목을 쓰는 애플리케이션 코드가 저장소에 전혀 없다.** 따라서 해당 테이블의 TTL은 설정만 있고 실질적으로 휴면 상태다.

이 스펙은 5개 비인증 테이블에 대해 TTL이 실제로 동작하도록 쓰기 경로를 정의하고, `auth_sessions`의 회귀를 막는 테스트를 보강한다.

## 2. 목표와 비목표

### 목표

1. 5개 비인증 만료 테이블에 항목을 쓸 때 `expires_at`를 정수 epoch로 일관되게 기록하는 리포지토리/라이터를 정의한다.
2. 보존 기간을 이 스펙의 단축 기본값과 일치시키는 단일 시간 계산 헬퍼를 정의한다.
3. 새 쓰기 함수에 대해 TTL 속성명·값 타입을 검증하는 테스트 요구사항을 정의한다.
4. 새 쓰기 경로가 필요로 하는 환경변수·IAM 배선 규칙을 정의한다.
5. (선택) TTL 동작을 관측하기 위한 모니터링을 9장에 분리해 정의한다.

### 비목표

- `infra/data-stack/template.yaml`의 TTL 속성명 변경 금지. `auth_sessions`는 `expiresAt`, 나머지는 `expires_at`를 그대로 유지한다.
- TTL 삭제 타이밍에 인가·비즈니스 정합성을 의존시키지 않는다(삭제는 비동기, 만료 후 최대 ~48시간까지 지연 가능).
- 제품 기능상 실제 쓰기 흐름이 없는 테이블에 대해 투기적 라이터를 미리 만들지 않는다(아래 7.3 단계 도입 원칙 참조).
- `content_documents`, `visitor_statistics`(TTL 없음)는 본 스펙 범위 밖이다.

## 3. 공통 계약 (불변 규칙)

| 항목 | 규칙 |
| --- | --- |
| TTL 속성명(비인증 5종) | `expires_at` |
| TTL 속성명(auth_sessions) | `expiresAt` (변경 금지) |
| 값 타입 | Number, epoch **초**(seconds), 정수 |
| 값 계산식 | `expires_at = 생성시각(epoch초) + 보존기간(초)` |
| 로그 본문 정책 | 요약·참조·해시만 저장. 사용자 대화 원문 저장 금지 |

> 주의: 속성명 두 종류(`expires_at` / `expiresAt`)가 섞이지 않도록, 시간 계산 헬퍼는 속성명을 책임지지 않는다. 각 테이블 라이터가 자신의 속성명을 명시적으로 기록한다.

## 4. 테이블별 쓰기 계약

키 포맷·GSI는 아래 테이블의 계약과 `infra/data-stack/template.yaml`의 현재 테이블/GSI 정의를 기준으로 한다. 이전 PRD/DB build 문서는 현재 repo에 존재하지 않으므로 이 스펙에서 직접 필요한 계약을 고정한다.

| 논리 테이블 | `pk` 포맷 | `sk` 포맷 | TTL 속성 | 관련 GSI |
| --- | --- | --- | --- | --- |
| `user_event_logs` | `USER#{user_id_hash}` 또는 `ANON#{anon_session_id}` | `EVENT#{created_at}#{event_id}` | `expires_at` | GSI1RequestLookup, GSI3EventTypeDaily, GSI4RecommendationLookup |
| `agent_runs` | `RUN#{agent_run_id}` | `STATE#{created_at}` | `expires_at` | GSI1RequestLookup, GSI2AgentRunLookup, GSI4RecommendationLookup |
| `festival_verify_cache` | `FESTIVAL#{festival_id}` | `YEAR#{travel_year}` | `expires_at` | 없음 |
| `async_jobs` | `JOB#{job_id}` | `STATUS#{updated_at}` | `expires_at` | 없음 |
| `api_logs` | `API#{yyyyMMdd}#{endpoint_group}` | `{created_at}#{request_id}` | `expires_at` | GSI1RequestLookup |

GSI 키 속성(`request_id`, `agent_run_id`, `event_type#yyyyMMdd`, `recommendation_request_id`)은 해당 항목에 그 속성이 실제로 존재할 때만 기록한다.

## 5. 보존 기간 규칙 (단축 기본값)

방향: **DynamoDB는 운영 핫 액세스 창만 커버하도록 보존기간을 짧게 구성한다.** 테이블 성격에 따라 다음 두 부류로 나눈다.

- 로그성(retention 성격): 운영·트러블슈팅·요청 추적용. 짧게 단축한다.
- 캐시성(freshness 성격): 캐시 적중률·재검증 비용에 영향. 무분별 단축은 비용을 오히려 늘리므로 유지한다.

출처(원 권고값): `reports/dynamodb_ttl_application_write_analysis_20260618_ko.md`의 "Retention Rules From Existing Docs" 섹션에 기록된 과거 권고값.
결정: 이 스펙은 아래 **단축 기본값**을 구현 계약으로 채택한다. 원 권고값은 비교용 baseline이며 수용 기준이 아니다. 보존기간을 다시 늘리거나 법무·보안 기준으로 확정해야 하면 별도 변경으로 상수와 수용 기준을 함께 갱신한다.

| 논리 테이블 | 성격 | 원 권고값 | **단축 기본값** |
| --- | --- | --- | --- |
| `user_event_logs` | 로그성 | 90일 | **14일** |
| `agent_runs` | 로그성 | 30일 | **14일** |
| `api_logs` | 로그성 | 30일 | **14일** |
| `async_jobs` | 로그성(작업 수명) | 14일 | **7일** |
| `festival_verify_cache` | 캐시성 | `confirmed` 30일 / `tentative` 7일 / `unknown`·`outdated` 1일 | **유지**(동일) |

`festival_verify_cache`는 검증 상태(status)에 따라 보존기간이 분기하므로, 라이터가 상태값을 입력으로 받아 해당 보존기간을 선택한다. 이 테이블의 TTL은 "데이터 보존"이 아니라 "캐시 신선도"를 의미하므로, 단축 시 외부 검증·재계산 호출이 늘어 비용이 증가할 수 있어 현행 값을 유지한다.

> 데이터 손실 경고: 단축 TTL을 S3 아카이브 없이 적용하면, 보존기간을 넘긴 로그는 **영구 삭제**된다. 원 권고값 수준의 90일 일자별 분석(`GSI3EventTypeDaily` 용도)·감사/컴플라이언스 보존이 확정 요구사항이라면, 단축 TTL 적용 **전 또는 동시에** 별도 후속 스펙의 S3 아카이브 경로(권장: 앱측 묶음 → Kinesis Firehose Direct PUT → S3 Parquet, Glacier 라이프사이클)를 갖춰야 한다. 본 버전은 "짧은 TTL 우선" 방향에 따라 쓰기 경로부터 구성하며, S3 아카이브는 후속 작업으로 분리한다.

## 6. 구현 설계

### 6.1 기존 패턴 준수

`src/auth/session_repository.py`의 `DynamoDbSessionRepository` 패턴을 따른다.

- 생성자에서 `table_name`과 `dynamodb_resource`를 주입 가능하게 한다(테스트 용이성).
- 테이블명은 환경변수에서 읽되 주입값을 우선한다.
- 멱등/조건부 쓰기가 필요한 경우 `ConditionExpression`을 사용한다.

### 6.2 공유 시간 헬퍼

```python
# src/shared/dynamodb_ttl.py
def ttl_epoch(now_epoch, retention_seconds):
    return int(now_epoch) + int(retention_seconds)

# 보존 기간 상수 (초). 이 스펙의 단축 기본값.
DAY = 86_400
RETENTION_SECONDS = {
    "user_event_logs": 14 * DAY,   # 원 권고 90일 → 단축
    "agent_runs": 14 * DAY,        # 원 권고 30일 → 단축
    "api_logs": 14 * DAY,          # 원 권고 30일 → 단축
    "async_jobs": 7 * DAY,         # 원 권고 14일 → 단축
}
# 캐시성: 신선도 기준이므로 단축하지 않고 현행 유지.
FESTIVAL_CACHE_RETENTION_SECONDS = {
    "confirmed": 30 * DAY,
    "tentative": 7 * DAY,
    "unknown": 1 * DAY,
    "outdated": 1 * DAY,
}
```

이 헬퍼는 시간 계산만 책임진다. 속성명(`expires_at`)은 각 라이터가 명시한다.

### 6.3 제안 라이터 모듈 (실제 쓰기 흐름이 범위에 들어올 때만 생성)

| 모듈 | 책임 | 소유 함수(예상) |
| --- | --- | --- |
| `src/shared/dynamodb_ttl.py` | 시간·보존기간 헬퍼/상수 | 공용 |
| `src/shared/operational_events.py` | `user_event_logs`, `api_logs` 쓰기 | 요청을 처리하는 함수의 공통 미들웨어/래퍼 |
| `src/agentcore/run_repository.py` | `agent_runs` 상태 쓰기 | `AgentCoreFunction` |
| `src/agentcore/festival_cache_repository.py` | `festival_verify_cache` 쓰기/조회 | `AgentCoreFunction` |
| `src/shared/async_jobs.py` | `async_jobs` 상태 쓰기 | 비동기 작업을 생성·갱신하는 함수 |

각 라이터는 주입된 DynamoDB resource/table로 테스트 가능해야 한다.

### 6.4 라이터 쓰기 예시 (비인증)

```python
item = {
    "pk": pk,
    "sk": sk,
    "created_at": created_at,
    # ... 도메인 속성, GSI 키 속성(존재 시) ...
    "expires_at": int(expiry_epoch_seconds),
}
table.put_item(Item=item)
```

`auth_sessions`는 기존대로 `"expiresAt": int(expires_at_epoch)`를 유지한다.

## 7. 인프라 배선

### 7.1 현재 상태

루트 `template.yaml`은 DynamoDB TTL 테이블 중 `AuthFunction`에만 `AUTH_SESSIONS_TABLE_NAME` 환경변수와 해당 테이블+GSI에 한정된 IAM을 부여한다. 나머지 5개 테이블에 대한 환경변수·IAM은 어떤 함수에도 부여되어 있지 않다.

Data Stack은 테이블명을 SSM 파라미터로 게시한다(`infra/data-stack/template.yaml`).

- `/lovv/${EnvName}/ddb/user_event_logs`
- `/lovv/${EnvName}/ddb/agent_runs`
- `/lovv/${EnvName}/ddb/festival_verify_cache`
- `/lovv/${EnvName}/ddb/async_jobs`
- `/lovv/${EnvName}/ddb/api_logs`

### 7.2 새 쓰기 함수에 필요한 배선

새 코드가 특정 테이블에 쓰기를 수행하는 함수에만 다음을 추가한다.

1. 환경변수: 해당 테이블명을 배포 파라미터 또는 SSM 해석값으로 주입(`AuthFunction`의 `AUTH_SESSIONS_TABLE_NAME` 패턴 준용).
2. IAM: 최소 권한으로 한정.

| 액션 | 부여 조건 |
| --- | --- |
| `dynamodb:PutItem` | 항목 기록 시 항상 |
| `dynamodb:UpdateItem` | 상태/캐시 갱신이 필요한 경우만(`async_jobs`, `festival_verify_cache` 등) |
| `dynamodb:GetItem` / `dynamodb:Query` | 캐시 조회·룩업이 필요한 경우만 |

3. Resource는 해당 테이블 ARN(필요 시 사용하는 GSI ARN)으로 한정한다. 테이블 와일드카드 금지.

### 7.3 단계 도입 원칙

테이블별 라이터·배선은 그 테이블에 쓰는 **실제 제품 기능이 범위에 들어올 때** 추가한다. 기능 없는 투기적 라이터/IAM은 만들지 않는다.

## 8. 테스트 요구사항

### 8.1 auth_sessions 회귀 방지 (최우선·최소 위험)

`tests/test_session_repository.py`에 다음을 추가한다(기존 `FakeSessionTable`은 `query`만 보유 → `put_item` 캡처 추가 필요).

- `FakeSessionTable.put_item(**kwargs)` 캡처 추가.
- `DynamoDbSessionRepository.create_session(...)` 호출 테스트.
- 단언:
  - `Item["expiresAt"] == int(expires_at_epoch)`
  - `isinstance(Item["expiresAt"], int)`
  - `"expires_at"` 속성이 auth 세션 항목에 **존재하지 않음**

### 8.2 신규 라이터 테스트

각 신규 라이터에 대해:

- TTL 속성명이 정확히 `expires_at`인지.
- 값이 `int`이고 `created_at + 보존기간(초)`와 일치하는지.
- `festival_verify_cache`는 상태별(`confirmed`/`tentative`/`unknown`/`outdated`)로 보존 기간이 올바르게 선택되는지.
- 키 포맷(`pk`/`sk`)이 4장 계약과 일치하는지.

## 9. (선택) TTL 모니터링

쓰기 경로 구현이 우선이며, 모니터링은 후속/선택 작업으로 분리한다. 두 층으로 구성한다.

### 9.1 네이티브 DynamoDB 메트릭 (앱 코드 불필요)

- `AWS/DynamoDB` 네임스페이스의 `TimeToLiveDeletedItemCount`(테이블 차원)로 TTL 삭제량을 관측한다.
  신뢰도: 높음(DynamoDB 표준 제공 메트릭). 정확한 동작·요금은 배포 시 AWS 문서 재확인.
- 알람 후보:
  - 쓰기는 발생하는데(`ConsumedWriteCapacityUnits` > 0) `TimeToLiveDeletedItemCount`가 장기간 0 → `expires_at` 누락 의심(신규 테이블 오탐 처리 필요).
  - 삭제량 이상 급증 → 보존기간 계산 버그(초/밀리초 혼동 등) 의심.

### 9.2 쓰기 측 관측성 (핵심 위험 탐지)

`expires_at` 누락 항목은 TTL 삭제 대상이 되지 않아 9.1 메트릭으로는 잡히지 않는다(삭제가 일어나지 않으므로 0). 이를 탐지하려면 앱 측 신호가 필요하다.

- 권장: EMF(임베디드 메트릭, 로그 기반, 추가 IAM 불필요)로 `expires_at` 포함/누락 쓰기 카운트.
- 대안: `cloudwatch:PutMetricData`(별도 IAM 필요).

### 9.3 인프라 구성 요소(모니터링 채택 시)

- `AWS::CloudWatch::Alarm`(테이블별 또는 통합) — 테이블과 라이프사이클이 같은 `infra/data-stack/template.yaml`에 두는 것을 권장.
- `AWS::SNS::Topic`(+구독) — 알림 채널.
- (선택) `AWS::CloudWatch::Dashboard`.

> 현 저장소에는 SNS/알람/관측성 패턴이 전무하므로 모니터링 채택은 신규 패턴 도입이며 쓰기 경로 작업보다 범위가 넓다. 별도 결정·작업으로 진행한다.

## 10. 수용 기준

1. `auth_sessions`는 `expiresAt`를 유지하고, 테스트가 `create_session()`이 정수 `expiresAt`를 기록함을 확인한다.
2. `user_event_logs`, `agent_runs`, `festival_verify_cache`, `async_jobs`, `api_logs`에 대한 신규 쓰기는 정수 `expires_at`를 포함한다.
3. 보존 기간이 이 스펙의 단축 기본값과 일치한다(상수로 분리).
4. 환경변수·IAM은 실제로 해당 테이블에 읽기/쓰기하는 함수에만 최소 권한으로 추가된다.
5. 신규 라이터마다 TTL 속성명·값 타입·키 포맷을 검증하는 테스트가 존재한다.
6. (모니터링 채택 시) 9장 구성 요소가 Data Stack에 추가되고 알람 임계값이 문서화된다.

## 11. 검증 명령

```powershell
python -m pytest tests
$env:AWS_CLI_FILE_ENCODING='UTF-8'; aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml
```

루트 SAM 템플릿을 변경한 경우, 팀에서 사용하는 SAM 검증 명령도 함께 실행한다.

## 12. 작업 순서 (마일스톤)

1. M1 — 회귀 방지: `tests/test_session_repository.py`에 `put_item` 캡처와 `expiresAt` 정수/`expires_at` 부재 단언 추가. 인프라 변경 없음. (최소 위험)
2. M2 — 공유 헬퍼: `src/shared/dynamodb_ttl.py`(`ttl_epoch`, 보존기간 상수) 추가 + 단위 테스트.
3. M3 — 라이터 도입: 실제 쓰기 흐름이 범위에 들어온 테이블부터 라이터 + 테스트 추가(6.3 매핑 준수).
4. M4 — 인프라 배선: M3에서 추가된 함수에 한해 환경변수·IAM·SSM 해석 추가, `validate-template` 통과.
5. M5 — (선택) 모니터링: 9장에 따라 네이티브 메트릭 알람 → 쓰기 측 관측성 순으로 도입.

> 의존성: M1·M2는 독립 진행 가능. M4는 M3에, M5는 M3에 의존한다.

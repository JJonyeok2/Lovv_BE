# 로브 (Lovv) DB 구축 PRD (SAM 제외 데이터 스토어)

> 문서 버전: v0.1
> 문서 상태: 초안 (Draft)
> 작성일: 2026-06-10
> 작성자: 조동휘
> 범위 한정: **SAM 앱 스택과 분리된 데이터 스토어 구축(프로비저닝 + 스키마 생성)**. 제품 전체 PRD 아님
> 입력 문서: `04_database_design/04_database_design.md`(v0.5), `06_technical_spec`(v0.4, §5.3 SAM 분리 전략), `07_api_spec`(v0.2)
> 보조 문서: 본 문서는 `docs/04_database_design`의 보조 Markdown이다. 대표 문서(`04_database_design.md`) 수정은 별도 요청 시에만 반영한다.

# 1. 개요

## 1.1 목적

본 문서는 로브(Lovv) 백엔드에서 **AWS SAM 애플리케이션 스택(Lambda·API Gateway)과 분리해 구축하는 데이터 스토어**의 구축 요구사항을 정의한다. 구축 범위는 (1) RDS(MySQL) 테이블과 제약사항 생성, (2) DynamoDB 테이블 생성, (3) 이미지 저장용 S3 버킷 구성이며, 이후 애플리케이션 개발 단계에서 바로 사용할 수 있는 **참조 쿼리(액세스 패턴)**를 함께 제공한다.

데이터 모델·정규화·보존 정책의 근거는 `04_database_design.md`를 따르며, 본 문서는 그 설계를 **실제 생성 가능한 산출물(DDL·테이블 정의·버킷 정의·참조 쿼리)**로 구체화한다.

## 1.2 배경

`06_technical_spec` §5.3에 따라 Production 백엔드의 컴퓨팅·서빙 계층(API Gateway, `Auth-Function`·`Map-Function`·`AgentCore-Function` Lambda)은 AWS SAM 템플릿으로 정의·배포한다. 반면 MySQL·DynamoDB·S3 같은 **stateful 데이터 스토어는 애플리케이션과 수명주기가 다르고**(앱 롤백·재배포 시에도 데이터는 유지되어야 함), 실수 삭제 시 영향이 크므로 SAM 스택에서 제외하고 별도로 구축한다.

따라서 SAM 배포 이전에 데이터 스토어가 먼저 존재해야 하며, SAM Lambda는 생성된 스토어를 **환경변수/파라미터로 참조**만 한다.

## 1.3 범위

| 구분 | 내용 |
| --- | --- |
| **구현 대상(신규)** | ① RDS(MySQL 8) 5개 테이블 + 제약사항(PK·FK·Unique·Index) DDL, ② DynamoDB 7개 테이블(키·GSI·TTL) 생성, ③ 이미지 저장용 S3 버킷 구성, ④ ①·②의 **개발용 참조 쿼리** |
| 제외 | SAM Lambda/API Gateway 정의, 추천·Agent 로직, 데이터 수집·전처리·적재 파이프라인(`08_data_preprocessing` 소관), S3 vector index/RAG 인덱스, AWS Neptune(고도화 단계), 초기 데이터 시딩(파이프라인 PRD 소관) |
| 전제 | 데이터 스토어는 SAM 스택과 **분리된 별도 스택/프로비저닝**으로 생성하고, SAM은 이를 참조만 한다 |

## 1.4 용어

| 용어 | 정의 |
| --- | --- |
| Data Stack | SAM 앱 스택과 분리해 stateful 데이터 스토어(RDS·DynamoDB·S3)를 정의·생성하는 단위 |
| DDL | RDS 테이블·제약 생성 SQL (`CREATE TABLE` 등) |
| 참조 쿼리 | 애플리케이션 개발 시 그대로 사용할 수 있는 대표 조회/쓰기 쿼리(액세스 패턴) |
| 원장 | 서비스가 신뢰하는 최종 상태 데이터(MySQL 기준) |

# 2. 아키텍처 경계 (SAM vs Data Stack)

## 2.1 분리 원칙

| 항목 | SAM 앱 스택 | Data Stack (본 PRD) |
| --- | --- | --- |
| 정의 리소스 | API Gateway, Lambda(`Auth`·`Map`·`AgentCore`), IAM Role | RDS(MySQL), DynamoDB 테이블, S3 이미지 버킷 |
| 수명주기 | 잦은 재배포·롤백 | 장기 유지, 삭제 보호 필요 |
| 배포 순서 | Data Stack **이후** 배포 | SAM **이전** 생성 |
| 상호 참조 | 스토어 식별자를 **환경변수/파라미터로 주입받음** | 생성 후 식별자(엔드포인트·테이블명·버킷명)를 **export/파라미터로 공개** |

## 2.2 SAM이 Data Stack을 참조하는 방식

데이터 스토어 생성 후 다음 식별자를 SSM Parameter Store 또는 스택 출력값으로 공개하고, SAM 템플릿은 이를 환경변수로 주입한다.

| 파라미터(예시) | 값 | 사용 Lambda |
| --- | --- | --- |
| `/lovv/rds/host`, `/lovv/rds/db_name` | MySQL 엔드포인트·DB명 | `Auth-Function`, `Map-Function` |
| `/lovv/ddb/<table>` | DynamoDB 테이블명 7종 | 전 Lambda |
| `/lovv/s3/image_bucket` | 이미지 버킷명 | `Map-Function` |

> DB 접속 비밀번호 등 자격증명은 본 문서가 평문으로 다루지 않으며, Secrets Manager/SSM SecureString으로 관리하고 IAM으로 접근을 제한한다(신뢰도: 운영 권고).

# 3. RDS(MySQL) 구축 요구사항

## 3.1 인스턴스·엔진 기준

| 항목 | 결정 | 비고 |
| --- | --- | --- |
| 엔진 | MySQL 8 LTS | `04_database_design.md` §1.2 기준 |
| 문자셋 | `utf8mb4` / `utf8mb4_0900_ai_ci` | 다국어(한·일) 텍스트 저장 |
| 시간대 | UTC 저장, 표시 변환은 앱 계층 | `datetime` 컬럼 기준 |
| 삭제 정책 | PoC는 hard delete 우선, Production 확장 시 soft delete 검토 | §4.1 설계 기준 |

## 3.2 테이블·제약 (DDL)

아래 5개 테이블과 제약사항만 생성한다. 컬럼·타입·제약은 `04_database_design.md` §3.2를 1:1로 반영한다.

```sql
-- 1) users : 사용자 프로필 원장
CREATE TABLE users (
  id           CHAR(36)     NOT NULL,
  email        VARCHAR(255) NULL,
  display_name VARCHAR(80)  NOT NULL,
  avatar_url   VARCHAR(500) NULL,
  created_at   DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2) social_accounts : 소셜 로그인 제공자 계정 연결
CREATE TABLE social_accounts (
  id               CHAR(36)     NOT NULL,
  user_id          CHAR(36)     NOT NULL,
  provider         VARCHAR(30)  NOT NULL,   -- 예: google, kakao
  provider_user_id VARCHAR(255) NOT NULL,
  created_at       DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_social_provider_user (provider, provider_user_id),
  KEY idx_social_user (user_id),
  CONSTRAINT fk_social_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3) itineraries : 사용자가 저장한 최종 여행 일정
CREATE TABLE itineraries (
  id                  CHAR(36)     NOT NULL,
  user_id             CHAR(36)     NOT NULL,
  title               VARCHAR(160) NOT NULL,
  summary             TEXT         NULL,
  duration_label      VARCHAR(40)  NOT NULL,   -- 예: 1박 2일
  festival_choice     VARCHAR(80)  NULL,
  intensity_label     VARCHAR(40)  NULL,       -- 예: 여유/보통/빡빡
  preference_snapshot JSON         NULL,
  request_summary     TEXT         NULL,
  saved_at            DATETIME     NOT NULL,
  created_at          DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_itinerary_user_saved (user_id, saved_at DESC),
  CONSTRAINT fk_itinerary_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4) itinerary_items : 일정 내 세부 장소·방문 순서
CREATE TABLE itinerary_items (
  id                    CHAR(36)     NOT NULL,
  itinerary_id          CHAR(36)     NOT NULL,
  sort_order            INT          NOT NULL,
  time_slot             VARCHAR(40)  NULL,     -- 예: 오전/오후/저녁
  place_name            VARCHAR(160) NOT NULL,
  move_hint             VARCHAR(255) NULL,
  recommendation_reason TEXT         NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_item_order (itinerary_id, sort_order),
  CONSTRAINT fk_item_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5) plan_reactions : 일정에 대한 사용자 반응
CREATE TABLE plan_reactions (
  id            CHAR(36)    NOT NULL,
  user_id       CHAR(36)    NOT NULL,
  itinerary_id  CHAR(36)    NOT NULL,
  reaction_type VARCHAR(30) NOT NULL,   -- 예: like/dislike
  created_at    DATETIME    NOT NULL,
  PRIMARY KEY (id),
  KEY idx_reaction_user (user_id, created_at DESC),
  KEY idx_reaction_itinerary (itinerary_id, created_at),
  CONSTRAINT fk_reaction_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_reaction_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

## 3.3 제약사항 요약

| 테이블 | PK | Unique | FK (ON DELETE) | Index |
| --- | --- | --- | --- | --- |
| `users` | `id` | — | — | `email` |
| `social_accounts` | `id` | (`provider`, `provider_user_id`) | `user_id`→`users` (CASCADE) | `user_id` |
| `itineraries` | `id` | — | `user_id`→`users` (CASCADE) | (`user_id`, `saved_at` desc) |
| `itinerary_items` | `id` | (`itinerary_id`, `sort_order`) | `itinerary_id`→`itineraries` (CASCADE) | (UQ 겸용) |
| `plan_reactions` | `id` | — | `user_id`→`users`, `itinerary_id`→`itineraries` (CASCADE) | (`user_id`,`created_at`), (`itinerary_id`,`created_at`) |

> FK의 `ON DELETE CASCADE`는 보존 정책(§5: "저장 일정 삭제 시 관련 `itinerary_items`·`plan_reactions` 함께 삭제")을 DB 제약으로 강제한 것이다. 사용자 탈퇴 시 익명화/soft delete가 필요하면 Production 확장에서 정책을 재검토한다(신뢰도: 설계 근거 기반 높음).

## 3.4 개발용 참조 쿼리 (MySQL)

`07_api_spec`의 마이페이지 API(`/me/itineraries` 등)에 대응하는 대표 쿼리. `:param`은 바인딩 변수.

```sql
-- A. 소셜 로그인 식별 (Auth-Function)
SELECT u.id, u.email, u.display_name, u.avatar_url
FROM social_accounts s
JOIN users u ON u.id = s.user_id
WHERE s.provider = :provider AND s.provider_user_id = :provider_user_id;

-- B. 마이페이지 저장 일정 목록 (최신순)
SELECT id, title, summary, duration_label, intensity_label, saved_at
FROM itineraries
WHERE user_id = :user_id
ORDER BY saved_at DESC
LIMIT :limit OFFSET :offset;

-- C. 일정 상세 + 항목 (정렬 순서대로)
SELECT i.id AS itinerary_id, i.title, i.summary, i.preference_snapshot,
       it.sort_order, it.time_slot, it.place_name, it.move_hint, it.recommendation_reason
FROM itineraries i
JOIN itinerary_items it ON it.itinerary_id = i.id
WHERE i.id = :itinerary_id AND i.user_id = :user_id
ORDER BY it.sort_order ASC;

-- D. 일정 반응 등록 (idempotent 보강은 앱 계층 또는 추가 UNIQUE로)
INSERT INTO plan_reactions (id, user_id, itinerary_id, reaction_type, created_at)
VALUES (:id, :user_id, :itinerary_id, :reaction_type, :now);

-- E. 일정별 반응 집계
SELECT reaction_type, COUNT(*) AS cnt
FROM plan_reactions
WHERE itinerary_id = :itinerary_id
GROUP BY reaction_type;

-- F. 저장 일정 삭제 (items/reactions는 FK CASCADE로 함께 삭제)
DELETE FROM itineraries WHERE id = :itinerary_id AND user_id = :user_id;
```

# 4. DynamoDB 구축 요구사항

## 4.1 생성 기준

| 항목 | 결정 | 비고 |
| --- | --- | --- |
| 과금 모드 | PoC는 On-Demand(PAY_PER_REQUEST) | 트래픽 불확실, 예산 단순화 |
| 키 스키마 | 각 테이블 PK(`pk`)/SK(`sk`)는 §4.2 기준 | `04_database_design.md` §3.3 |
| TTL | 로그성 테이블은 `expires_at`(Number, epoch) 속성으로 TTL 활성화 | §5.1 보존 기간 |
| GSI | §4.3의 4개 GSI 생성(필요 테이블 한정) | §4.4 GSI 후보 |
| 본문 저장 | 원문 대신 요약·해시만 저장(개인정보·대화 전문 금지) | NoSQL 저장 원칙 |

## 4.2 테이블 정의 (생성 대상 7종)

| 테이블 | Partition Key (`pk`) | Sort Key (`sk`) | TTL 속성 |
| --- | --- | --- | --- |
| `lovv_user_event_logs` | `USER#{user_id_hash}` / `ANON#{anon_session_id}` | `EVENT#{created_at}#{event_id}` | `expires_at` |
| `lovv_agent_runs` | `RUN#{agent_run_id}` | `STATE#{created_at}` | `expires_at` |
| `lovv_festival_verify_cache` | `FESTIVAL#{festival_id}` | `YEAR#{travel_year}` | `expires_at` |
| `lovv_async_jobs` | `JOB#{job_id}` | `STATUS#{updated_at}` | `expires_at` |
| `lovv_api_logs` | `API#{yyyyMMdd}#{endpoint_group}` | `{created_at}#{request_id}` | `expires_at` |
| `lovv_content_documents` | `CONTENT#{country}#{entity_type}` | `ENTITY#{entity_id}` | 없음 |
| `lovv_visitor_statistics` | `CITY#{city_id}` | `STAT#{period}#{source_type}` | 없음 |

> 본 PRD는 **테이블·키·GSI·TTL 생성**까지 책임진다. 정규화 문서(`lovv_content_documents`·`lovv_visitor_statistics`)의 **데이터 적재**는 `08_data_preprocessing` 파이프라인 소관이며 여기서는 빈 테이블만 생성한다.

## 4.3 GSI 정의

| GSI | PK | SK | 적용 테이블 | 용도 |
| --- | --- | --- | --- | --- |
| `GSI1RequestLookup` | `request_id` | `created_at` | event/agent/api 로그 | 요청 단위 trace |
| `GSI2AgentRunLookup` | `agent_run_id` | `created_at` | `lovv_agent_runs` | 실행 전체 단계 조회 |
| `GSI3EventTypeDaily` | `event_type#yyyyMMdd` | `created_at` | `lovv_user_event_logs` | 이벤트 타입별 일자 분석 |
| `GSI4RecommendationLookup` | `recommendation_request_id` | `created_at` | event/agent 로그 | 추천 요청-로그 연결 |

## 4.4 TTL 기간 (권고·잠정, 신뢰도 중)

`expires_at = 생성시각 + 보존기간`(epoch초)으로 저장. 법무·보안 검토로 확정.

| 테이블 | 권고 TTL |
| --- | --- |
| `lovv_user_event_logs` | 90일 |
| `lovv_agent_runs` | 30일 |
| `lovv_async_jobs` | 14일 |
| `lovv_api_logs` | 30일 |
| `lovv_festival_verify_cache` | `confirmed` 30일 / `tentative` 7일 / `unknown·outdated` 1일 |
| `lovv_content_documents`, `lovv_visitor_statistics` | 없음(S3 Raw 기준 재생성) |

## 4.5 개발용 참조 쿼리 (DynamoDB 액세스 패턴)

```text
# 1) 특정 사용자 이벤트 타임라인 (최신순)
Query lovv_user_event_logs
  KeyCondition: pk = "USER#{user_id_hash}" AND begins_with(sk, "EVENT#")
  ScanIndexForward = false

# 2) Agent run 단건 전체 단계 trace
Query lovv_agent_runs
  KeyCondition: pk = "RUN#{agent_run_id}"
# 또는 run_id를 모를 때:
Query GSI2AgentRunLookup
  KeyCondition: agent_run_id = "{agent_run_id}"

# 3) 축제 날짜 검증 캐시 조회 (festival + year)
GetItem lovv_festival_verify_cache
  Key: pk = "FESTIVAL#{festival_id}", sk = "YEAR#{travel_year}"

# 4) 비동기 작업 최신 상태
Query lovv_async_jobs
  KeyCondition: pk = "JOB#{job_id}"
  ScanIndexForward = false, Limit = 1

# 5) 특정 일자/엔드포인트 그룹 API 장애 로그
Query lovv_api_logs
  KeyCondition: pk = "API#{yyyyMMdd}#{endpoint_group}" AND sk between "{from}" and "{to}"

# 6) request_id 기준 교차 로그 추적
Query GSI1RequestLookup
  KeyCondition: request_id = "{request_id}"

# 7) 추천 요청 단위 로그 연결
Query GSI4RecommendationLookup
  KeyCondition: recommendation_request_id = "{recommendation_request_id}"

# 8) 도시별 콘텐츠/통계 조회 (추천 후보)
Query lovv_content_documents
  KeyCondition: pk = "CONTENT#{country}#{entity_type}" AND begins_with(sk, "ENTITY#")
Query lovv_visitor_statistics
  KeyCondition: pk = "CITY#{city_id}" AND begins_with(sk, "STAT#")
```

# 5. S3 이미지 스토리지 요구사항

이미지 저장 전용 버킷을 구성한다. (RAG용 S3 vector index·S3 Raw 수집 원본은 본 PRD 범위가 아니다.)

| 항목 | 결정 |
| --- | --- |
| 버킷 용도 | 사용자 프로필 이미지(`users.avatar_url`), 도시·관광지·축제 콘텐츠 이미지 |
| 버킷 분리 | 환경별 분리 권장: `lovv-image-{env}` (예: `lovv-image-prod`) |
| Prefix 구조 | `avatar/{user_id_hash}/...`, `content/{country}/{entity_type}/{entity_id}/...` |
| 공개 접근 | 버킷 직접 공개 차단(Block Public Access), 배포는 CloudFront 또는 presigned URL 권장 |
| 암호화 | 기본 서버측 암호화(SSE-S3 이상) 활성화 |
| 수명주기 | 이미지 원본은 운영 보존, 임시 업로드 prefix는 단기 만료 정책 검토 |
| 참조 방식 | DB에는 S3 키/URL만 저장하고 바이너리는 저장하지 않음 |

> S3는 테이블 개념이 없으므로 "참조 쿼리" 대신 **객체 키 규칙**을 계약으로 둔다. 앱은 위 prefix 규칙으로 `PutObject`/`GetObject`(또는 presigned URL)를 사용한다.

# 6. 완료 기준 (체크리스트)

- [ ] RDS(MySQL 8) 인스턴스가 SAM 스택과 분리되어 생성되었는가?
- [ ] `users`·`social_accounts`·`itineraries`·`itinerary_items`·`plan_reactions` 5개 테이블이 §3.2 DDL대로 생성되고 PK/FK/Unique/Index 제약이 적용되었는가?
- [ ] DynamoDB 7개 테이블이 §4.2 키 스키마로 생성되고, 로그성 테이블에 `expires_at` TTL이 활성화되었는가?
- [ ] §4.3 GSI 4종이 해당 테이블에 생성되었는가?
- [ ] 이미지 저장용 S3 버킷이 Block Public Access·암호화 설정과 함께 생성되었는가?
- [ ] 생성된 스토어 식별자(RDS 엔드포인트·테이블명·버킷명)가 SSM/출력값으로 공개되어 SAM이 참조 가능한가?
- [ ] §3.4·§4.5 참조 쿼리가 실제 스키마에서 정상 실행되는가?

# 7. 범위 밖 (후속/타 문서 소관)

| 항목 | 소관 |
| --- | --- |
| SAM Lambda/API Gateway 정의 | `06_technical_spec` §5.3, `07_api_spec` |
| 데이터 수집·전처리·적재(시딩) | `08_data_preprocessing` 파이프라인 PRD |
| S3 vector index / RAG | `04_database_design` §3.4 (별도) |
| AWS Neptune 그래프 | `04_database_design` §3.5 (고도화) |
| TTL·보존 기간 법무 확정 | `database_design_retention_neptune_update.md` |

# 8. 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
| --- | --- | --- | --- |
| v0.1 | 2026-06-10 | 조동휘 | SAM 제외 데이터 스토어 구축 PRD 초안 작성 (RDS 테이블+제약, DynamoDB 테이블, S3 이미지 버킷, 개발용 참조 쿼리) |

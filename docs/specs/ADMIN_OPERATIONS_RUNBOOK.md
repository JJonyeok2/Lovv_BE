# Admin Console Operations Runbook

운영자/관리자가 Lovv 관리자 콘솔(`/api/v1/admin/*`)을 운영하고 모니터링하기 위한 런북이다.
1차 PoC 세로축(제안 → 검토 → 승인 → 월간 후보/게시 → 반영 이력)과 운영 기능(공지·추천 정책,
감사 로그)을 포함한다.

## 1. 역할·권한

서버가 권한의 단일 진실 원천이다. 모든 라우트는 검증된 액세스 토큰에서 역할·범위를 다시 도출하며,
프론트의 탭/버튼 게이팅은 UX일 뿐이다. 자세한 권한 매트릭스는 `ADMIN_RBAC_SPEC.md`를 참고한다.

- `R-ADMIN`: 검토/승인/반려, 월간 후보 게시 상태 전이, 반영 잡 운영, 공지·정책 관리, 감사 로그 조회.
- `R-DATA-PROVIDER`: 데이터 제안 등록/조회(본인·조직 범위).
- `R-LOCAL-OPERATOR`: 담당 지역의 제안/월간 후보/반영 이력 조회.

## 2. 핵심 운영 흐름

1. 데이터 제공자가 제안을 등록한다(`POST /api/v1/admin/data-proposals`).
2. 관리자가 검토→승인/반려한다(`POST .../{id}/review|approve|reject`). 본인 제안은 검토할 수 없다.
3. 승인된 제안을 월간 후보로 승격한다(`POST /api/v1/admin/monthly-destinations`).
4. 후보를 게시 상태머신으로 운영한다(`schedule`/`publish`/`hide`/`expire`/`reject`).
5. 게시 시 4종 반영 잡(catalog/RAG/검색/추천)이 자동 enqueue된다. 운영자는 잡을
   `start`/`succeed`/`fail`/`retry`/`cancel`로 진행한다(`POST /api/v1/admin/publish-jobs/{jobId}/{action}`).
6. 후보별 반영 이력은 `GET /api/v1/admin/monthly-destinations/{id}/publish-jobs`로 확인한다.

## 3. 공지·추천 정책

- 공지: `GET/POST /api/v1/admin/notices`, 전이 `publish`/`archive`.
- 추천 정책: `GET/POST /api/v1/admin/recommendation-policies`, 전이 `activate`/`archive`.
- 정책은 우선순위(priority)와 규칙(rules JSON)을 가지며, 추천 후보 정렬에 활용할 운영 파라미터다.

## 4. 감사 로그 (Audit Trail)

모든 관리자 변이는 `admin_audit_logs`에 append-only로 기록된다. 기록은 best-effort이며, 감사
기록 실패가 비즈니스 동작을 실패시키지 않는다(구성 누락 시 조용히 건너뜀, 그 외 예외는 로깅).

기록되는 액션(action):

| 영역 | action |
| --- | --- |
| 제안 | `data_proposal.review` / `.approve` / `.reject` |
| 월간 후보 | `monthly_destination.schedule` / `.publish` / `.hide` / `.expire` / `.reject` |
| 반영 잡 | `publish_job.start` / `.succeed` / `.fail` / `.retry` / `.cancel` |
| 공지 | `notice.create` / `.publish` / `.archive` |
| 추천 정책 | `recommendation_policy.create` / `.activate` / `.archive` |

각 항목은 행위자(actor)·역할/조직/지역 스냅샷·대상 리소스·결과(`succeeded` 등)·after 요약을 남긴다.
`monthly_destination.publish`는 metadata에 enqueue된 반영 잡 수(`reflectionJobCount`)를 기록한다.

조회: `GET /api/v1/admin/audit-logs` (R-ADMIN 전용). 필터 쿼리: `action`, `resourceType`,
`result`, `actorUserId`, `limit`(최대 50).

## 5. 모니터링

별도 모니터링 인프라를 두지 않고, 다음 두 가지를 1차 모니터링 표면으로 사용한다.

1. **감사 로그 조회 API/콘솔 탭** — 누가 무엇을 했고 결과가 무엇인지 추적.
2. **구조화 로그** — Lambda 핸들러는 `shared.logger`로 태그 기반 로그를 남긴다. 처리되지 않은
   예외는 `INTERNAL_ERROR`로 응답되며 `LOGGER.exception`으로 스택이 남는다(CloudWatch Logs).

운영 점검 포인트:

- 게시 후 반영 잡이 `failed`로 누적되면(반복 `publish_job.fail`) 다운스트림 동기화 연동을 점검한다.
- `data_proposal.reject`/`monthly_destination.reject` 급증은 데이터 품질 이슈 신호일 수 있다.
- `audit-logs`에서 예상치 못한 actor/역할의 변이가 보이면 권한 배정(역할/지역)을 재검토한다.

## 6. 데이터 모델 참조

- 제안/이력: `admin_data_proposals`, `admin_data_proposal_history`
- 월간 후보: `monthly_curated_destinations`
- 반영 잡: `admin_publish_jobs`
- 지표: `destination_metrics_daily`
- 공지/정책: `admin_notices`, `admin_recommendation_policies`
- 감사 로그: `admin_audit_logs`

스키마 정의는 `schema/aurora_mysql/002_admin_console_tables.sql` 및
`schema/aurora_mysql/003_admin_operations_tables.sql`를 참고한다.

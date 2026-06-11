# 현재 상태 보고서: Lovv Data Stack

> 보고서 버전: v0.1
> 작성일: 2026-06-10
> 범위: Data Stack PRD, Spec, Plan, 인프라 산출물, 보고서 커밋 이후 현재 저장소 상태 정리

# 1. 요약

Lovv 백엔드 저장소에는 AWS SAM 애플리케이션 스택과 분리된 stateful Data Stack의 1차 계약 및 구현 산출물이 추가되었다.

현재 구현 방향:

- Data Stack provisioning은 CloudFormation을 사용한다.
- AWS SAM은 Lambda, API Gateway, 애플리케이션 IAM을 담당한다.
- RDS MySQL은 서비스 원장으로 유지한다.
- DynamoDB는 로그, 캐시, 비동기 작업 상태, 콘텐츠 문서, 방문 통계 저장소로 사용한다.
- S3는 이미지 객체 저장소로 사용한다.
- SAM local 개발에서는 RDS 원장을 DynamoDB로 대체하지 않고 Docker MySQL을 사용한다.

# 2. 생성된 산출물

문서:

- `docs/PRD/db_build_prd.md`
- `docs/SPEC/db_build_spec.md`
- `docs/SPEC/service_api_schema_extension_spec.md`
- `docs/PLAN/db_build_plan.md`
- `docs/PLAN/service_api_schema_extension_plan.md`

인프라:

- `infra/data-stack/template.yaml`
- `infra/data-stack/README.md`
- `infra/data-stack/parameters/dev.parameters.example.json`
- `infra/data-stack/rds/schema.sql`
- `infra/data-stack/rds/reference_queries.sql`

보고서:

- `reports/data_stack_build_report.md`
- `reports/current_status_report.md`
- `reports/current_status_report_ko.md`

# 3. Data Stack 구성

CloudFormation 템플릿은 현재 다음 리소스를 정의한다.

- 개발용 VPC
- private subnet 2개
- private route table 및 subnet 연결
- RDS security group
- interface endpoint security group
- Secrets Manager, SSM, DynamoDB, S3용 VPC Endpoint
- RDS DB subnet group
- RDS MySQL DB instance
- RDS managed master user secret
- DynamoDB 테이블 8개
- DynamoDB TTL 및 GSI 설정
- S3 이미지 버킷
- RDS, network, DynamoDB, S3 식별자를 위한 SSM parameters

RDS SQL은 현재 다음 6개 테이블을 정의한다.

- `users`
- `social_accounts`
- `user_preferences`
- `itineraries`
- `itinerary_items`
- `plan_reactions`

서비스 API 스키마 보강 대상:

- Auth 프로필 및 계정 상태: `users`, `social_accounts`
- 온보딩 및 마이페이지 취향: `user_preferences`
- 저장 일정 idempotency 및 snapshot: `itineraries`
- 1박 2일 이상 지도 연동 일정 항목: `itinerary_items`
- 좋아요/싫어요 toggle 유일성: `plan_reactions`
- refresh token 세션: DynamoDB `auth_sessions`

위 보강은 PR #1 Data Stack 기반 위에 Auth, Preference, Saved Plans, Reaction API 흐름을 붙이기 위한 후속 확장이다.

# 4. 배포 상태

저장소 산출물은 준비되었다.

현재 저장소 세션에서 확인되지 않은 항목:

- CloudFormation stack 실제 배포 결과
- AWS 리소스 live validation
- RDS schema 실제 적용 여부
- 서비스 API 스키마 확장 후 기존 RDS 테이블 상태
- 서비스 API 확장 제약 추가 전 기존 RDS 중복 데이터 상태

만약 서비스 API 확장 전에 테이블을 이미 생성했다면, 실제 DB에는 통제된 `ALTER TABLE` 마이그레이션과 unique constraint 추가 전 중복 데이터 점검이 필요하다.

# 5. SAM 연동 상태

보고서에는 SAM 개발자와 Agent가 Data Stack을 소비하는 방식이 기록되어 있다.

SAM 연동 원칙:

- RDS, DynamoDB, S3, network 식별자는 SSM Parameter Store에서 읽는다.
- SAM에서 RDS, DynamoDB, S3, VPC, subnet을 중복 생성하지 않는다.
- RDS에 접근하는 Lambda에는 `VpcConfig`를 추가한다.
- 배포된 Lambda는 Data Stack의 private subnet을 사용한다.
- DB 자격증명은 Secrets Manager ARN을 통해 접근한다.

현재 network 관련 주의사항:

- v0.1 Data Stack은 `DevMysqlIngressCidr` 기반으로 RDS inbound를 허용한다.
- 이후에는 Lambda security group 기반 RDS ingress로 강화하는 것이 권장된다.

# 6. Local 개발 판단

기록된 결정:

- RDS MySQL은 서비스 원장으로 유지한다.
- SAM local 편의성 때문에 RDS 원장을 DynamoDB로 대체하지 않는다.
- SAM local 개발에서는 Docker MySQL을 사용한다.
- 배포된 dev 환경에서는 Data Stack RDS를 사용한다.

근거:

- RDS 원장은 FK, cascade delete, unique constraint, 정렬된 일정 item, 집계 쿼리 같은 관계형 무결성에 의존한다.
- DynamoDB로 대체하면 데이터 모델 재설계와 애플리케이션 레벨 무결성 구현이 필요하다.

# 7. VPC 접속 판단

배포된 SAM Lambda:

- Data Stack이 생성한 private subnet에 연결되어야 한다.
- Lambda security group은 RDS `3306` 접근 경로를 가져야 한다.
- RDS host, DB name, secret ARN은 SSM parameter에서 읽어야 한다.

SAM local:

- local Docker container는 AWS VPC 내부에서 실행되지 않는다.
- 따라서 private RDS에 직접 접속할 수 없다.
- private RDS에 접근하려면 VPN, bastion, SSM Session Manager port forwarding 같은 network path가 필요하다.

권장 방식:

- `SAM local -> Docker MySQL`
- `SAM deployed dev -> Data Stack RDS`

# 8. 즉시 다음 작업

권장 작업 순서:

1. 대상 AWS profile로 CloudFormation template을 검증한다.
2. `lovv-dev-data-stack`을 배포한다.
3. SSM parameters가 생성되었는지 확인한다.
4. Secrets Manager에서 RDS secret 값을 확인한다.
5. private RDS에 접근 가능한 환경에서 `infra/data-stack/rds/schema.sql`을 적용한다.
6. 기존 schema가 이미 적용되었다면 service API 확장 마이그레이션과 unique constraint 추가 전 중복 점검을 수행한다.
7. SAM template에 `VpcConfig`, DB 환경변수, Secrets Manager 권한, DynamoDB 권한, S3 권한을 추가한다.
8. 갱신된 Data Stack 배포 후 Auth API가 `/lovv/dev/ddb/auth_sessions`를 참조하도록 연결한다.

# 9. 검증 미실행 항목

이 저장소 세션에서는 live validation을 실행하지 않았다.

이유:

- 실제 AWS 배포와 RDS schema 적용은 대상 AWS credential, account 상태, region, profile, private network 접근 방식, 배포 승인에 의존한다.

# 10. 관련 커밋

현재 Data Stack 기반 산출물은 아래 커밋에 포함되어 있다.

```text
09815d8 feat(data-stack): add Lovv data stack foundation
```

이 한국어 보고서는 위 커밋 이후 추가된 보조 현황 보고서다.

# 11. 후속 수정 기록

- RDS for MySQL master username은 `lovvadmin`으로 통일한다.
- RDS for MySQL 초기 DB 이름은 `lovvdev`로 통일한다.
- `lovv_admin`, `lovv_dev`처럼 underscore가 포함된 값은 CloudFormation template validation을 통과하더라도 실제 RDS 생성 단계에서 실패할 수 있으므로 사용하지 않는다.
- private subnet에 연결된 SAM Lambda가 Secrets Manager, SSM, DynamoDB, S3에 접근할 수 있도록 Data Stack에 VPC Endpoint를 추가한다.
- 일반 인터넷 egress는 여전히 제공하지 않는다. 외부 API 호출이 필요하면 NAT Gateway, egress proxy, 또는 VPC 밖 Lambda 설계를 별도로 검토한다.
- Auth, Preference, Saved Plans, Reaction API 흐름을 위한 서비스 API 스키마 확장 산출물을 추가했다.

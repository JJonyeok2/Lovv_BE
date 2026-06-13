# Lovv_BE

Lovv용 AWS SAM 백엔드입니다.

## 현재 범위

현재 구현된 백엔드 도메인은 다음과 같습니다.

- Auth 소셜 로그인 / Cognito bridge: `POST /api/v1/auth/google`, `POST /api/v1/auth/kakao`, `POST /api/v1/auth/cognito/session`, `GET /api/v1/auth/me`, `GET /api/v1/auth/session`, `POST /api/v1/auth/logout`
- 사용자 선호도: `GET /api/v1/me/preferences`, `PUT /api/v1/me/preferences`
- 지도 / 도시: 기존 `GET /api/small-cities`, `GET /api/small-cities/{cityId}`, `GET /api/small-cities/{cityId}/places`와 `/api/v1` alias, marker projection
- AgentCore mock: `POST /api/v1/recommendations`
- 저장 일정: `POST /api/v1/me/itineraries`, `GET /api/v1/me/itineraries`, `GET /api/v1/me/itineraries/{itineraryId}`, `DELETE /api/v1/me/itineraries/{itineraryId}`, like/unlike routes

이번 구현 범위에서 제외된 항목은 다음과 같습니다.

- Bedrock AgentCore 실연동
- LLM 호출
- 추천 품질 / 랭킹 개선
- 대화 이력 영속화
- 진행 중인 draft 영속화

## 저장소

- `users`, `social_accounts`, `user_preferences`, 저장 일정 관련 테이블: 기존 Lovv Data Stack의 RDS MySQL에 VPC 내부 직접 MySQL 연결로 접근합니다.
- `auth_sessions`: 기존 Lovv Data Stack의 DynamoDB 테이블을 사용하며 `expiresAt` 기준 TTL을 적용합니다.
- 지도 / 도시 원천 데이터: `raw/KR/details/20260609/` 아래의 S3 raw city detail JSON을 사용합니다.
- attractions, festivals, visitor statistics는 이번 범위에서 Aurora에 적재하지 않습니다.

기존 Data Stack RDS DDL:

```text
infra/data-stack/rds/schema.sql
```

저장 일정 API는 Data Stack RDS의 `itineraries`, `itinerary_items`, `plan_reactions` 테이블 shape에 맞춰 동작합니다. 일정 삭제는 `deleted_at` 기반 soft delete이며, 삭제된 일정은 목록/상세/반응 흐름에서 제외됩니다.

## 인증 모델

- 서비스 user 조회 / 생성 전에 provider credential을 서버에서 검증합니다.
- access token은 `AUTH_TOKEN_SIGNING_SECRET`으로 서명한 짧은 수명의 JWT입니다.
- refresh / session 유지는 HttpOnly Secure cookie 안의 opaque token을 사용합니다.
- DynamoDB에는 refresh token hash만 저장합니다.
- logout은 refresh session을 revoke합니다. 이미 발급된 access JWT는 이후 active-session authorizer 검사가 추가되지 않는 한 stateless하게 `exp`까지 유효합니다.
- logout 요청에 유효한 refresh cookie가 없더라도 유효한 bearer access JWT가 있으면 JWT의 `sid` session revoke를 시도합니다.
- Google / Kakao production login은 OIDC `id_token`과 OAuth `authorization_code`를 모두 허용합니다.
- `authorization_code` login에는 `redirectUri`가 필요합니다. Google code exchange에는 `GOOGLE_CLIENT_SECRET`도 필요하며, Kakao는 앱 설정에서 client secret을 요구할 때만 `KAKAO_CLIENT_SECRET`을 사용합니다.
- code exchange 결과는 OIDC `id_token`을 포함해야 합니다. 백엔드는 provider ID token을 다시 검증한 뒤 Lovv session을 생성합니다.
- `POST /api/v1/auth/cognito/session`은 API Gateway Cognito JWT authorizer가 전달한 `requestContext.authorizer.jwt.claims`를 Lovv user/session 응답으로 bridge합니다. 초기 Cognito bridge 단계에서는 role을 `R-USER`로 고정하며, Cognito group 기반 Admin/Operator/Data Provider 권한 매핑은 별도 Admin/권한 Task 범위에서 확장합니다. Cognito User Pool, Hosted UI, Google/Kakao IdP, API Gateway JWT Authorizer 리소스 생성은 별도 PoC/배포 Task 범위입니다.
- `EnableCognitoPoC=true` 배포 파라미터를 사용할 때만 Cognito User Pool, Hosted UI domain, Google IdP, Kakao OIDC IdP, Cognito app client 리소스를 생성합니다. 기본값은 `false`라 기존 배포에 Cognito 리소스를 강제 생성하지 않습니다.
- 기존 demo용 `POST /api/auth/login` route는 production auth로 mount하지 않습니다.

## 지도 / 도시 데이터 소스

예상 S3 source:

```text
s3://lovv-data-pipeline-dev-925273580929/raw/KR/details/20260609/{CityNameEn}.json
```

각 city file은 city record와 raw `attraction`, `festival`, `visitor_statistics` records를 포함합니다. Lambda는 이 JSON 파일을 직접 읽고, city record와 summary fields를 기반으로 city list/detail response를 매핑하며, attractions/festivals는 `/places`를 통해 노출합니다.

이번 구현에서 Aurora는 상세 관광 콘텐츠의 source of truth가 아닙니다. Aurora는 users, social accounts, preferences, saved plans처럼 사용자가 소유한 영속 데이터에 우선 사용합니다.

Lambda는 `image_url`을 반환하기 전에 HTTP(S) URL인지 검증하며, 지도 / 도시 데이터 조회를 위해 Kakao 또는 다른 live provider API를 호출하지 않습니다.

## 로컬 검증

```bash
python3 -m unittest discover -s tests
python3 scripts/local_api_smoke.py
sam validate
sam validate --lint
git diff --check
sam build
```

`scripts/local_api_smoke.py`는 외부 provider나 실제 secret을 사용하지 않고 handler 레벨에서 로그인, preferences 저장/조회, small-cities list/detail/places, saved plans 저장/list/detail/delete, like/unlike 흐름을 1회 검증합니다.

## 배포 파라미터

실제 값은 deploy parameter 또는 환경 설정으로 주입합니다. 실제 secret은 커밋하지 않습니다.

```bash
sam deploy --guided \
  --parameter-overrides \
  MapCityS3Bucket=lovv-data-pipeline-dev-925273580929 \
  MapCityS3Prefix=raw/KR/details/20260609/ \
  AllowedCorsOrigin=http://localhost:5173,http://127.0.0.1:5173,https://d3nuef0zacpyj.cloudfront.net \
  AuthTokenSigningSecret=replace-with-secret-manager-or-ci-value \
  AuthRefreshCookieSameSite=None \
  AuthRefreshCookieSecure=true \
  AuthRefreshCookieDomain=.your-service-domain.example \
  AuthRefreshCookiePath=/ \
  GoogleClientId=replace-with-google-web-client-id \
  GoogleClientSecret=replace-with-google-web-client-secret \
  KakaoClientId=replace-with-kakao-oidc-client-id \
  KakaoClientSecret=replace-with-kakao-client-secret-if-enabled \
  EnableCognitoPoC=true \
  CognitoJwtIssuer=https://replace-with-cognito-issuer.example \
  CognitoJwtAudience=replace-with-cognito-app-client-id \
  CognitoUserPoolName=lovv-auth-users \
  CognitoUserPoolClientName=lovv-web \
  CognitoHostedUiDomainPrefix=replace-with-globally-unique-prefix \
  CognitoCallbackUrls=http://localhost:5173/auth/callback/cognito,http://127.0.0.1:5173/auth/callback/cognito,https://d3nuef0zacpyj.cloudfront.net/auth/callback/cognito \
  CognitoLogoutUrls=http://localhost:5173/,http://127.0.0.1:5173/,https://d3nuef0zacpyj.cloudfront.net/ \
  CognitoGoogleClientId=replace-with-google-oauth-client-id \
  CognitoGoogleClientSecret=replace-with-google-oauth-client-secret \
  CognitoKakaoClientId=replace-with-kakao-oidc-client-id \
  CognitoKakaoClientSecret=replace-with-kakao-oidc-client-secret \
  RdsHost=replace-with-existing-lovv-data-stack-rds-host \
  RdsSecretArn=replace-with-existing-lovv-data-stack-secret-arn \
  RdsDatabaseName=lovvdev \
  VpcId=replace-with-existing-lovv-data-stack-vpc-id \
  PrivateSubnetA=replace-with-existing-lovv-data-stack-private-subnet-a \
  PrivateSubnetC=replace-with-existing-lovv-data-stack-private-subnet-c \
  AuthSessionsTableName=lovv_dev_auth_sessions
```

Auth Lambda가 private Data Stack VPC 안에서 실행되는 경우, live Google/Kakao token verification과 authorization-code exchange에는 NAT 또는 승인된 다른 egress 설계 같은 outbound internet egress 경로가 필요합니다.

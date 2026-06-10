# Lovv_BE

AWS SAM backend for Lovv.

## Current Scope

Implemented backend domains:

- Auth social login: `POST /api/v1/auth/google`, `POST /api/v1/auth/kakao`, `GET /api/v1/auth/me`, `GET /api/v1/auth/session`, `POST /api/v1/auth/logout`
- User Preference: `GET /api/v1/me/preferences`, `PUT /api/v1/me/preferences`
- Map / City: legacy `GET /api/small-cities`, `GET /api/small-cities/{cityId}`, `GET /api/small-cities/{cityId}/places` plus `/api/v1` aliases and marker projection
- AgentCore mock: `POST /api/v1/recommendations`
- Saved Plans: `POST /api/v1/me/itineraries`, `GET /api/v1/me/itineraries`, `GET /api/v1/me/itineraries/{itineraryId}`, like/unlike routes

Out of scope for this implementation:

- Bedrock AgentCore live integration
- LLM calls
- recommendation quality/ranking improvements
- conversation history persistence
- in-progress draft persistence

## Storage

- `users`, `social_accounts`, `preferences`, and `saved_plans`: Aurora MySQL through RDS Data API.
- `auth_sessions`: DynamoDB table with TTL on `expiresAt`.
- Map/City source data: S3 raw city detail JSON under `raw/KR/details/20260609/`.
- Attractions, festivals, and visitor statistics are not loaded into Aurora in this scope.

Aurora baseline DDL:

```text
schema/aurora_mysql/001_product_api_tables.sql
```

## Auth Model

- Provider credentials are verified server-side before service user lookup/create.
- Access tokens are short-lived JWTs signed by `AUTH_TOKEN_SIGNING_SECRET`.
- Refresh/session continuity uses an opaque token in an HttpOnly Secure cookie.
- Only the refresh token hash is stored in DynamoDB.
- Logout revokes refresh sessions. Already-issued access JWTs remain stateless and valid until `exp` unless a future active-session authorizer check is added.
- If logout receives no valid refresh cookie but receives a valid bearer access JWT, it attempts to revoke the JWT `sid` session.
- Kakao production login accepts an OIDC `id_token` and validates `aud`, `iss`, and `exp` against `KAKAO_CLIENT_ID`.
- The old demo `POST /api/auth/login` route is not mounted as production auth.

## Map / City Data Source

Expected S3 source:

```text
s3://lovv-data-pipeline-dev-925273580929/raw/KR/details/20260609/{CityNameEn}.json
```

Each city file contains a city record plus raw `attraction`, `festival`, and `visitor_statistics` records. The Lambda reads those JSON files directly, maps city list/detail responses from the city record and summary fields, and exposes attractions/festivals through `/places`.

Aurora is not the source of truth for detailed tourism content in this implementation. It is used first for permanent user-owned data such as users, social accounts, preferences, and saved plans.

The Lambda validates `image_url` as an HTTP(S) URL before returning it and does not call Kakao or other live provider APIs for Map/City data.

## Local Verification

```bash
python3 -m unittest discover -s tests
sam validate
sam build
```

## Deploy Parameters

Provide real values through deploy parameters or environment configuration. Do not commit real secrets.

```bash
sam deploy --guided \
  --parameter-overrides \
  MapCityS3Bucket=lovv-data-pipeline-dev-925273580929 \
  MapCityS3Prefix=raw/KR/details/20260609/ \
  AllowedCorsOrigin=https://your-frontend-origin.example \
  AuthTokenSigningSecret=replace-with-secret-manager-or-ci-value \
  GoogleClientId=replace-with-google-web-client-id \
  KakaoClientId=replace-with-kakao-oidc-client-id \
  AuroraClusterArn=replace-with-aurora-cluster-arn \
  AuroraSecretArn=replace-with-secret-arn \
  AuroraDatabaseName=lovv
```

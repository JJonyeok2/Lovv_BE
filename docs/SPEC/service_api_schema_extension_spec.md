# Lovv Service API Schema Extension Spec

> Document version: v0.1
> Document status: Draft
> Created: 2026-06-10
> Baseline: PR #1 Data Stack schema
> Source Spec: `docs/SPEC/db_build_spec.md` v0.1
> Scope: Schema additions and reinforcements required before implementing Auth, Preference, Saved Plans, and Reaction service APIs.

# 1. Objective

PR #1 creates the initial Data Stack foundation, but its schema is intentionally limited to storage provisioning. This spec defines the additional RDS and DynamoDB schema contract needed for user-facing service APIs.

The target service flows are:

- Auth and social login.
- Refresh-token session lifecycle.
- Onboarding preference save/load/update.
- Saved plan creation, idempotent save, detail read, list, and delete.
- Like/dislike toggle.

Success means the application can implement these APIs without inventing persistence fields at the Lambda layer, without storing provider access tokens, and without relying on table scans for the core service flows.

# 2. Baseline

PR #1 already defines these RDS tables:

- `users`
- `social_accounts`
- `itineraries`
- `itinerary_items`
- `plan_reactions`

PR #1 already defines these DynamoDB tables:

- `user_event_logs`
- `agent_runs`
- `festival_verify_cache`
- `async_jobs`
- `api_logs`
- `content_documents`
- `visitor_statistics`

This spec treats PR #1 as the Data Stack baseline and adds a service-flow extension on top of it.

# 3. Assumptions

- RDS MySQL remains the service ledger for users, preferences, saved itineraries, itinerary items, and reactions.
- DynamoDB remains appropriate for expiring operational state such as refresh-token sessions.
- Authentication is based on OAuth social login plus Lovv-issued access/refresh tokens.
- Provider access tokens and provider refresh tokens are never stored.
- Soft delete is preferred for user and itinerary records that may need audit or recovery behavior.
- JSON columns in MySQL store snapshots and structured preference payloads that the app validates before write.

# 4. Non-Goals

- Do not implement API Gateway, Lambda handlers, or business logic in this spec.
- Do not replace the RDS ledger with DynamoDB.
- Do not define password login tables unless a separate email/password login policy is approved.
- Do not store raw provider access tokens, provider refresh tokens, or plaintext refresh tokens.
- Do not change the seven existing DynamoDB tables except where service API access patterns require a new table reference.

# 4.1 First Implementation Decisions

The first service API schema migration uses these decisions:

- `itinerary_days` is deferred. Multi-day ordering is handled by `itinerary_items.day_index` plus `sort_order`.
- `display_name` and `nickname` coexist for compatibility. API response mapping can prefer `nickname` later without dropping `display_name`.
- First-pass enum-like values are enforced by DB `CHECK` constraints where practical and must also be enforced in application validation.
- User withdrawal uses `users.deleted_at` and `users.status = 'withdrawn'` first. Hard-deleting social account links requires a separate privacy/account-deletion decision.

# 5. RDS Schema Extension

## 5.1 `users` Reinforcement

Purpose:

- Support Auth API account state, role checks, profile display, login audit, and soft delete.

Required columns to add:

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `email_verified` | `BOOLEAN` | `NOT NULL` | `false` | True only when provider or service verification confirms the email. |
| `nickname` | `VARCHAR(80)` | `NULL` |  | User-facing nickname. Keep `display_name` for existing compatibility during migration. |
| `status` | `VARCHAR(30)` | `NOT NULL` | `'active'` | Allowed values: `active`, `suspended`, `withdrawn`. |
| `role` | `VARCHAR(30)` | `NOT NULL` | `'user'` | Allowed values: `user`, `admin`, `system`. |
| `last_login_at` | `DATETIME` | `NULL` |  | UTC. Updated after successful login. |
| `updated_at` | `DATETIME` | `NOT NULL` | current UTC at write time | Application-managed unless migration chooses DB default. |
| `deleted_at` | `DATETIME` | `NULL` |  | Soft delete marker. |

Required indexes:

| Index | Columns | Purpose |
| --- | --- | --- |
| `idx_users_status` | `status` | Admin/support filtering and safety checks. |
| `idx_users_deleted_at` | `deleted_at` | User cleanup and soft-delete filtering. |

Recommended constraints:

- `CHECK (status IN ('active', 'suspended', 'withdrawn'))`
- `CHECK (role IN ('user', 'admin', 'system'))`

If the target MySQL version or migration tool does not enforce `CHECK` reliably, enforce the same allowed values in application validation.

## 5.2 `social_accounts` Reinforcement

Purpose:

- Store provider profile metadata needed for Google/Kakao login and account linking.

Required columns to add:

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `email` | `VARCHAR(255)` | `NULL` | Provider email at last login. |
| `email_verified` | `BOOLEAN` | `NOT NULL` | Provider email verification state. |
| `provider_nickname` | `VARCHAR(80)` | `NULL` | Provider display nickname. |
| `provider_profile_image_url` | `VARCHAR(500)` | `NULL` | Provider profile image URL. Do not proxy/store binary content here. |
| `last_login_at` | `DATETIME` | `NULL` | UTC. Updated on successful provider login. |

Required rules:

- Keep `UNIQUE (provider, provider_user_id)`.
- Do not add `provider_access_token` or `provider_refresh_token`.
- Persist only profile metadata and provider identity.

## 5.3 New `user_preferences` Table

Purpose:

- Store onboarding preferences, login-time preference load, and my-page preference updates.

Canonical table name:

- `user_preferences`

`preferences` is acceptable only as an alias in API naming. The physical RDS table should use `user_preferences` to avoid ambiguity.

Required DDL contract:

```sql
CREATE TABLE user_preferences (
  id                       CHAR(36)    NOT NULL,
  user_id                  CHAR(36)    NOT NULL,
  country_track            VARCHAR(30) NOT NULL,
  mapped_themes            JSON        NULL,
  preferred_regions        JSON        NULL,
  selected_city_style      VARCHAR(50) NULL,
  pace                     VARCHAR(30) NULL,
  trip_days                INT         NULL,
  companion_style          VARCHAR(50) NULL,
  travel_styles            JSON        NULL,
  onboarding_completed     BOOLEAN     NOT NULL DEFAULT false,
  created_at               DATETIME    NOT NULL,
  updated_at               DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_preferences_user (user_id),
  KEY idx_user_preferences_country (country_track),
  CONSTRAINT fk_user_preferences_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

Required validation:

- `trip_days` must be positive when present.
- `country_track` must match a supported service country track such as `KR` or `JP`.
- JSON columns must be valid JSON arrays or objects according to the API request contract.

## 5.4 `itineraries` Reinforcement

Purpose:

- Support saved recommendation results, idempotent save, recommendation traceability, detail rendering, and soft delete.

Required columns to add:

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `source_recommendation_id` | `VARCHAR(80)` | `NULL` | Recommendation request/result identifier from agent flow. |
| `idempotency_key` | `VARCHAR(120)` | `NULL` | Client or server-issued idempotency key for save API. |
| `snapshot_hash` | `CHAR(64)` | `NULL` | Hash of persisted recommendation snapshot. |
| `destination_json` | `JSON` | `NULL` | Destination/country/city/festival payload snapshot. |
| `trip_type` | `VARCHAR(50)` | `NULL` | Trip category such as solo, couple, family, friends. |
| `themes_json` | `JSON` | `NULL` | Theme snapshot used for recommendation. |
| `conditions_snapshot_json` | `JSON` | `NULL` | Request conditions used to generate the plan. |
| `alternative_itinerary_json` | `JSON` | `NULL` | Alternative plan summary when available. |
| `updated_at` | `DATETIME` | `NOT NULL` | UTC update time. |
| `deleted_at` | `DATETIME` | `NULL` | Soft delete marker. |

Required constraints:

| Constraint | Columns | Purpose |
| --- | --- | --- |
| `uq_itinerary_user_idempotency` | `user_id`, `idempotency_key` | Prevent duplicate saved plans for the same idempotency key. |
| `uq_itinerary_user_source_snapshot` | `user_id`, `source_recommendation_id`, `snapshot_hash` | Prevent duplicate saved copies of the same generated recommendation snapshot. |

Required indexes:

| Index | Columns | Purpose |
| --- | --- | --- |
| `idx_itinerary_user_deleted_saved` | `user_id`, `deleted_at`, `saved_at DESC` | List active saved plans latest first. |
| `idx_itinerary_source_recommendation` | `source_recommendation_id` | Trace a saved plan back to recommendation output. |

Implementation note:

- MySQL unique constraints allow multiple `NULL` values. The API must provide `idempotency_key`, `source_recommendation_id`, and `snapshot_hash` for save requests that need deduplication.

## 5.5 `itinerary_items` Reinforcement

Purpose:

- Support multi-day itineraries, map rendering, place linkage, and item-level source badges.

Required columns to add:

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `day_index` | `INT` | `NOT NULL` | 1-based day index. |
| `content_id` | `VARCHAR(80)` | `NULL` | Content/catalog identifier when item maps to a content document. |
| `place_id` | `VARCHAR(120)` | `NULL` | External or internal place identifier. |
| `latitude` | `DECIMAL(10,7)` | `NULL` | WGS84 latitude. |
| `longitude` | `DECIMAL(10,7)` | `NULL` | WGS84 longitude. |
| `body` | `TEXT` | `NULL` | User-facing item detail body. |
| `source_badges` | `JSON` | `NULL` | Source labels such as official, estimated, festival, local. |

Required constraints and indexes:

| Object | Columns | Purpose |
| --- | --- | --- |
| `uq_item_day_order` | `itinerary_id`, `day_index`, `sort_order` | Preserve deterministic order inside each day. |
| `idx_item_content` | `content_id` | Content lookup and analytics. |
| `idx_item_place` | `place_id` | Place lookup and map integration. |

Migration note:

- Existing `uq_item_order (itinerary_id, sort_order)` must be replaced or supplemented carefully. For multi-day plans, `sort_order` may repeat per day, so the target uniqueness rule is `itinerary_id + day_index + sort_order`.

## 5.6 Optional `itinerary_days` Table

Purpose:

- Normalize day-level metadata if the service needs day title, date, area summary, or route summary independently from item rows.

Recommended only if API responses need day-level metadata beyond `day_index`.

Optional DDL contract:

```sql
CREATE TABLE itinerary_days (
  id              CHAR(36)     NOT NULL,
  itinerary_id    CHAR(36)     NOT NULL,
  day_index       INT          NOT NULL,
  title           VARCHAR(120) NULL,
  date_label      VARCHAR(40)  NULL,
  area_summary    VARCHAR(255) NULL,
  route_summary   TEXT         NULL,
  created_at      DATETIME     NOT NULL,
  updated_at      DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_itinerary_day_index (itinerary_id, day_index),
  CONSTRAINT fk_itinerary_day_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

If this table is adopted, `itinerary_items` should add `itinerary_day_id CHAR(36) NULL` and a foreign key to `itinerary_days(id)`. Do not require this table for the first API implementation unless the response contract needs separate day metadata.

## 5.7 `plan_reactions` Reinforcement

Purpose:

- Support stable like/dislike toggle behavior with one active reaction per user and itinerary.

Required columns to add:

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `updated_at` | `DATETIME` | `NOT NULL` | UTC update time for toggle changes. |

Required constraints:

| Constraint | Columns | Purpose |
| --- | --- | --- |
| `uq_plan_reaction_user_itinerary` | `user_id`, `itinerary_id` | Enforce one reaction per user per itinerary. |

Required allowed values:

- `reaction_type = 'like'`
- `reaction_type = 'dislike'`

Recommended constraint:

```sql
CHECK (reaction_type IN ('like', 'dislike'))
```

If `CHECK` is not enforced in the deployed MySQL configuration, the application must enforce the same rule.

# 6. DynamoDB Schema Extension

## 6.1 New `auth_sessions` Table

Purpose:

- Store refresh-token session records for login, refresh, logout revoke, and TTL-based cleanup.

Physical name:

- `lovv_{env}_auth_sessions`

SSM parameter:

- `/lovv/{env}/ddb/auth_sessions`

Required key contract:

| Key | Attribute | Type | Notes |
| --- | --- | --- | --- |
| PK | `sessionId` | `S` | Session identifier. |
| GSI hash | `refreshTokenHash` | `S` | Lookup by hashed refresh token. |
| TTL | `expiresAt` | `N` | Epoch seconds. |

Required attributes:

| Attribute | Type | Required | Notes |
| --- | --- | --- | --- |
| `sessionId` | `S` | Yes | Primary key. |
| `userId` | `S` | Yes | RDS `users.id`. |
| `refreshTokenHash` | `S` | Yes | Hash only, never plaintext refresh token. |
| `createdAt` | `S` | Yes | ISO-8601 UTC string. |
| `expiresAt` | `N` | Yes | TTL epoch seconds. |
| `revokedAt` | `S` | No | ISO-8601 UTC string when logged out or revoked. |
| `userAgent` | `S` | No | Truncated or normalized user-agent string. |
| `ipHash` | `S` | No | Hash of source IP, not raw IP. |

Required GSI:

| GSI | Partition key | Projection | Purpose |
| --- | --- | --- | --- |
| `GSI1RefreshTokenHashLookup` | `refreshTokenHash` | `ALL` | Validate refresh request by token hash. |

Recommended access patterns:

| ID | Pattern | Key |
| --- | --- | --- |
| AUTH-SESS-001 | Create session after login | `PutItem` by `sessionId` with conditional no existing item. |
| AUTH-SESS-002 | Refresh token lookup | `GSI1RefreshTokenHashLookup`. |
| AUTH-SESS-003 | Revoke session on logout | `UpdateItem` by `sessionId`, set `revokedAt`. |
| AUTH-SESS-004 | Automatic session cleanup | TTL on `expiresAt`. |

Security rules:

- Store only a hash of the refresh token.
- Do not store access tokens.
- Do not store raw IP address unless a separate privacy policy approves it.
- Treat revoked sessions as invalid even before TTL deletion occurs.

# 7. Service Flow Mapping

| Service flow | Required schema |
| --- | --- |
| Auth / Login | `users` reinforcement, `social_accounts` reinforcement, `auth_sessions` |
| Logout / Refresh revoke | `auth_sessions.revokedAt`, `auth_sessions.expiresAt` |
| Onboarding / Preference | `user_preferences` |
| Login-time preference load | `user_preferences.uq_user_preferences_user` |
| My-page preference update | `user_preferences.updated_at` and JSON preference columns |
| Saved Plans | `itineraries` reinforcement, `itinerary_items` reinforcement |
| Multi-day itinerary detail | `itinerary_items.day_index`; optional `itinerary_days` |
| Idempotent saved-plan creation | `itineraries.idempotency_key`, `itineraries.snapshot_hash`, unique constraints |
| Like / Dislike toggle | `plan_reactions.uq_plan_reaction_user_itinerary`, `updated_at`, allowed values |

# 8. Access Patterns

| ID | Pattern | Required schema support |
| --- | --- | --- |
| API-AUTH-001 | Find user by provider identity | `social_accounts.uq_social_provider_user` |
| API-AUTH-002 | Update login timestamps | `users.last_login_at`, `social_accounts.last_login_at` |
| API-AUTH-003 | Create refresh session | `auth_sessions.sessionId` |
| API-AUTH-004 | Validate refresh token | `auth_sessions.GSI1RefreshTokenHashLookup` |
| API-PREF-001 | Load current user's preferences | `user_preferences.uq_user_preferences_user` |
| API-PREF-002 | Upsert onboarding preferences | `user_preferences.user_id`, `updated_at` |
| API-PLAN-001 | Idempotently save recommended plan | `itineraries.uq_itinerary_user_idempotency` |
| API-PLAN-002 | Prevent duplicate recommendation snapshot save | `itineraries.uq_itinerary_user_source_snapshot` |
| API-PLAN-003 | List active saved plans | `idx_itinerary_user_deleted_saved` |
| API-PLAN-004 | Read plan detail in day/order sequence | `uq_item_day_order` |
| API-REACT-001 | Toggle one reaction per user and plan | `uq_plan_reaction_user_itinerary` |

# 9. Migration and Backfill Requirements

Implementation must be done as an additive migration where possible.

Required migration order:

1. Add nullable or defaulted columns to existing RDS tables.
2. Backfill `users.updated_at`, `plan_reactions.updated_at`, and `itinerary_items.day_index`.
3. Create `user_preferences`.
4. Add new indexes and unique constraints after duplicate checks.
5. Create DynamoDB `auth_sessions`.
6. Publish `/lovv/{env}/ddb/auth_sessions`.
7. Update reference queries and service integration notes.

Pre-constraint duplicate checks:

```sql
SELECT user_id, itinerary_id, COUNT(*) AS reaction_count
FROM plan_reactions
GROUP BY user_id, itinerary_id
HAVING COUNT(*) > 1;
```

```sql
SELECT user_id, idempotency_key, COUNT(*) AS duplicate_count
FROM itineraries
WHERE idempotency_key IS NOT NULL
GROUP BY user_id, idempotency_key
HAVING COUNT(*) > 1;
```

```sql
SELECT user_id, source_recommendation_id, snapshot_hash, COUNT(*) AS duplicate_count
FROM itineraries
WHERE source_recommendation_id IS NOT NULL
  AND snapshot_hash IS NOT NULL
GROUP BY user_id, source_recommendation_id, snapshot_hash
HAVING COUNT(*) > 1;
```

# 10. Commands

Template validation:

```powershell
aws cloudformation validate-template `
  --template-body file://infra/data-stack/template.yaml
```

Deploy updated dev Data Stack:

```powershell
aws cloudformation deploy `
  --stack-name lovv-dev-data-stack `
  --template-file infra/data-stack/template.yaml `
  --parameter-overrides file://infra/data-stack/parameters/dev.parameters.example.json
```

Apply RDS schema or migration SQL from a network path that can reach private RDS:

```powershell
$rdsHost = aws ssm get-parameter --name /lovv/dev/rds/host --query "Parameter.Value" --output text
$dbName = aws ssm get-parameter --name /lovv/dev/rds/db_name --query "Parameter.Value" --output text

mysql --host $rdsHost --user lovvadmin --database $dbName < infra/data-stack/rds/schema.sql
```

Verify new DynamoDB table parameter:

```powershell
aws ssm get-parameter --name /lovv/dev/ddb/auth_sessions
```

# 11. Boundaries

Always:

- Keep RDS as the user-service ledger.
- Store refresh-token hashes only.
- Publish new DynamoDB table names through SSM parameters.
- Add schema constraints only after duplicate-risk checks.
- Keep PR #1 Data Stack resources outside the SAM app stack.

Ask first:

- Adding `itinerary_days` as mandatory schema.
- Replacing existing `display_name` usage with `nickname` in API contracts.
- Making `idempotency_key`, `source_recommendation_id`, or `snapshot_hash` non-null for all saved plans.
- Adding password-login-specific columns or tables.

Never:

- Store provider access tokens or provider refresh tokens.
- Store plaintext refresh tokens.
- Store raw private conversation logs in service API tables.
- Duplicate RDS, DynamoDB, S3, VPC, or subnet resources in SAM.

# 12. Success Criteria

- `user_preferences` exists with `UNIQUE (user_id)` and FK to `users(id)`.
- `auth_sessions` exists in DynamoDB with PK `sessionId`, GSI `refreshTokenHash`, and TTL `expiresAt`.
- `users`, `social_accounts`, `itineraries`, `itinerary_items`, and `plan_reactions` include the required service API columns.
- `plan_reactions` enforces one reaction per `user_id + itinerary_id`.
- Saved-plan creation can be idempotent by `user_id + idempotency_key`.
- Saved recommendation duplicates can be prevented by `user_id + source_recommendation_id + snapshot_hash`.
- Multi-day itinerary items can be ordered by `itinerary_id + day_index + sort_order`.
- The Data Stack exposes `/lovv/{env}/ddb/auth_sessions` for SAM consumers.

# 13. Open Questions

- Should `itinerary_days` be promoted from optional to required when the API response contract needs day-level metadata?
- Should `nickname` replace `display_name` in API response contracts after the first compatibility release?
- Should the enum-like value sets move from application validation into strict lookup tables later?
- Should soft-deleted users retain social account rows, or should unlinking happen during withdrawal after a separate privacy decision?

# 14. Change Log

| Version | Date | Author | Changes |
| --- | --- | --- | --- |
| v0.1 | 2026-06-10 | Codex | Initial service API schema extension spec for Auth, Preference, Saved Plans, and Reaction flows. |

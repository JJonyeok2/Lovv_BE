# Implementation Plan: Lovv Service API Schema Extension

> Plan version: v0.1
> Created: 2026-06-10
> Source Spec: `docs/SPEC/service_api_schema_extension_spec.md`
> Baseline: PR #1 Data Stack
> Scope: RDS/DynamoDB schema updates required before Auth, Preference, Saved Plans, and Reaction API implementation.

# 1. Overview

This plan turns the service API schema extension spec into ordered implementation tasks. The work extends PR #1's Data Stack without moving stateful resources into SAM.

Implementation should proceed in this order:

1. Confirm final schema decisions and migration strategy.
2. Add additive RDS fields and new `user_preferences`.
3. Add unique constraints after duplicate checks.
4. Add DynamoDB `auth_sessions` and publish its SSM parameter.
5. Update reference queries and integration documentation.
6. Validate CloudFormation, SQL shape, and access-pattern readiness.

# 2. Architecture Decisions

- Keep RDS MySQL as the service ledger for user, preference, itinerary, item, and reaction records.
- Use DynamoDB for `auth_sessions` because refresh sessions are operational state with TTL cleanup.
- Keep provider OAuth tokens out of persistence; store only provider identity/profile metadata.
- Use `user_preferences` as the physical table name.
- Defer `itinerary_days` unless the service response contract requires day-level metadata separate from items.
- Add unique constraints only after checking existing data for duplicates.

# 3. Dependency Graph

```text
Spec decisions
  -> RDS additive columns
    -> Backfill defaulted fields
      -> New RDS table user_preferences
        -> RDS unique constraints and indexes
          -> Reference queries

Spec decisions
  -> DynamoDB auth_sessions table
    -> SSM parameter publication
      -> SAM integration notes

RDS + DynamoDB artifacts
  -> Validation
    -> API implementation readiness
```

# 4. Task List

## Phase 1: Schema Decision Gate

## Task 1: Confirm extension choices

**Description:** Confirm the small number of product/schema choices that affect the physical model before editing SQL or CloudFormation.

**Acceptance criteria:**

- [ ] Decide whether `itinerary_days` is deferred or included in the first migration.
- [ ] Decide whether `display_name` and `nickname` coexist for compatibility.
- [ ] Decide first-pass enum values for preference and trip-style fields.
- [ ] Record decisions in the spec or an implementation note.

**Verification:**

- [ ] `docs/SPEC/service_api_schema_extension_spec.md` has no unresolved blocker for the chosen first migration.

**Dependencies:** None

**Files likely touched:**

- `docs/SPEC/service_api_schema_extension_spec.md`

**Estimated scope:** S

## Phase 2: RDS Foundation

## Task 2: Add user and social-account columns

**Description:** Update the RDS schema artifact for Auth API account state and provider profile metadata.

**Acceptance criteria:**

- [ ] `users` includes `email_verified`, `nickname`, `status`, `role`, `last_login_at`, `updated_at`, and `deleted_at`.
- [ ] `social_accounts` includes `email`, `email_verified`, `provider_nickname`, `provider_profile_image_url`, and `last_login_at`.
- [ ] Provider token columns are not added.
- [ ] Required indexes and allowed-value checks are represented or documented for app-level enforcement.

**Verification:**

- [ ] Inspect `infra/data-stack/rds/schema.sql`.
- [ ] Run MySQL syntax validation in a MySQL 8-compatible environment.

**Dependencies:** Task 1

**Files likely touched:**

- `infra/data-stack/rds/schema.sql`

**Estimated scope:** S

## Task 3: Add `user_preferences`

**Description:** Add the preference ledger table used by onboarding, login-time preference load, and my-page preference update.

**Acceptance criteria:**

- [ ] `user_preferences` exists with all required columns from the spec.
- [ ] `UNIQUE (user_id)` is present.
- [ ] FK to `users(id)` is present with cascade behavior.
- [ ] JSON preference columns are present for theme, region, and travel-style payloads.

**Verification:**

- [ ] Run `SHOW CREATE TABLE user_preferences` after applying to a dev database.
- [ ] Insert and update a sample preference row in a disposable dev database.

**Dependencies:** Task 2

**Files likely touched:**

- `infra/data-stack/rds/schema.sql`
- `infra/data-stack/rds/reference_queries.sql`

**Estimated scope:** S

## Task 4: Reinforce saved-plan tables

**Description:** Update `itineraries` and `itinerary_items` so saved recommendation results can be idempotently persisted and rendered as multi-day map-ready plans.

**Acceptance criteria:**

- [ ] `itineraries` includes source recommendation, idempotency, snapshot, destination/theme/condition JSON, alternative itinerary JSON, update, and soft-delete fields.
- [ ] `itineraries` has unique constraints for `user_id + idempotency_key` and `user_id + source_recommendation_id + snapshot_hash`.
- [ ] `itinerary_items` includes `day_index`, content/place identifiers, coordinates, body, and source badges.
- [ ] Item ordering supports `itinerary_id + day_index + sort_order`.

**Verification:**

- [ ] Run duplicate-check SQL before adding unique constraints in any existing environment.
- [ ] Verify a two-day itinerary can store repeated `sort_order` values across different `day_index` values.

**Dependencies:** Task 3

**Files likely touched:**

- `infra/data-stack/rds/schema.sql`
- `infra/data-stack/rds/reference_queries.sql`

**Estimated scope:** M

## Task 5: Reinforce plan reactions

**Description:** Enforce stable like/dislike toggle semantics in `plan_reactions`.

**Acceptance criteria:**

- [ ] `updated_at` is added.
- [ ] `UNIQUE (user_id, itinerary_id)` is added.
- [ ] `reaction_type` is constrained or documented as app-enforced to `like` or `dislike`.

**Verification:**

- [ ] Run duplicate-check SQL before adding the unique constraint.
- [ ] Verify duplicate insert for the same `user_id + itinerary_id` fails in dev.
- [ ] Verify toggle is implemented as update/upsert in reference query notes.

**Dependencies:** Task 4

**Files likely touched:**

- `infra/data-stack/rds/schema.sql`
- `infra/data-stack/rds/reference_queries.sql`

**Estimated scope:** S

## Checkpoint A: RDS service-flow readiness

- [ ] Auth profile fields are available.
- [ ] Preferences can be saved and loaded by user.
- [ ] Saved plans support idempotency and multi-day detail.
- [ ] Reactions enforce one row per user and itinerary.
- [ ] RDS schema still uses MySQL 8, InnoDB, `utf8mb4`, and `utf8mb4_0900_ai_ci`.

## Phase 3: DynamoDB Auth Sessions

## Task 6: Add `auth_sessions` DynamoDB table

**Description:** Extend the CloudFormation Data Stack with the refresh-token session table.

**Acceptance criteria:**

- [ ] Table name follows `lovv_{env}_auth_sessions`.
- [ ] Primary key is `sessionId`.
- [ ] `GSI1RefreshTokenHashLookup` exists with partition key `refreshTokenHash`.
- [ ] TTL is enabled on `expiresAt`.
- [ ] Billing mode remains `PAY_PER_REQUEST`.

**Verification:**

- [ ] `aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml` succeeds.
- [ ] After deploy, `aws dynamodb describe-table` confirms key schema, GSI, and TTL.

**Dependencies:** Task 1

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** M

## Task 7: Publish `auth_sessions` SSM parameter and output

**Description:** Expose the new DynamoDB table name to SAM consumers using the same Data Stack publication pattern as existing tables.

**Acceptance criteria:**

- [ ] `/lovv/{env}/ddb/auth_sessions` is published.
- [ ] CloudFormation output exposes `AuthSessionsTableName`.
- [ ] README/report integration notes include the new parameter.

**Verification:**

- [ ] `aws ssm get-parameter --name /lovv/dev/ddb/auth_sessions` returns the table name after deploy.
- [ ] SAM integration checklist includes the new parameter.

**Dependencies:** Task 6

**Files likely touched:**

- `infra/data-stack/template.yaml`
- `infra/data-stack/README.md`
- `reports/data_stack_build_report.md`

**Estimated scope:** S

## Checkpoint B: Auth session readiness

- [ ] Refresh sessions can be written by `sessionId`.
- [ ] Refresh token validation can query by `refreshTokenHash`.
- [ ] Expired sessions are eligible for TTL cleanup.
- [ ] Revoked sessions can be marked without waiting for TTL deletion.

## Phase 4: Documentation and API Readiness

## Task 8: Update reference queries

**Description:** Add service API reference queries for preferences, idempotent plan save, multi-day detail read, and reaction toggle.

**Acceptance criteria:**

- [ ] Preference upsert/load query examples exist.
- [ ] Saved-plan idempotent insert/upsert guidance exists.
- [ ] Itinerary detail query orders by `day_index`, then `sort_order`.
- [ ] Reaction toggle query uses one-row-per-user-and-itinerary semantics.

**Verification:**

- [ ] Review `infra/data-stack/rds/reference_queries.sql`.
- [ ] Execute representative queries against a disposable MySQL 8 schema where available.

**Dependencies:** Tasks 3, 4, 5

**Files likely touched:**

- `infra/data-stack/rds/reference_queries.sql`

**Estimated scope:** S

## Task 9: Update Data Stack docs and reports

**Description:** Keep implementation docs aligned with the new service API schema contract.

**Acceptance criteria:**

- [ ] `infra/data-stack/README.md` references the service API schema extension.
- [ ] `reports/data_stack_build_report.md` documents the new RDS table/columns and `auth_sessions`.
- [ ] Current status reports identify remaining unverified deployment steps accurately.

**Verification:**

- [ ] `rg -n "auth_sessions|user_preferences|idempotency_key|uq_plan_reaction_user_itinerary" docs infra reports` finds aligned references.

**Dependencies:** Tasks 6, 7, 8

**Files likely touched:**

- `infra/data-stack/README.md`
- `reports/data_stack_build_report.md`
- `reports/current_status_report.md`
- `reports/current_status_report_ko.md`

**Estimated scope:** M

## Checkpoint C: Documentation readiness

- [ ] Spec, plan, SQL, template, README, and report do not contradict each other.
- [ ] PR #1 remains described as the Data Stack baseline.
- [ ] Service API extension is clearly marked as follow-up scope.

## Phase 5: Validation

## Task 10: Run local static validation

**Description:** Validate the changed artifacts without requiring live AWS or private RDS access.

**Acceptance criteria:**

- [ ] CloudFormation template validates.
- [ ] SQL file is inspectable and free of obvious duplicate constraint names.
- [ ] Search checks confirm required schema names are present.

**Verification:**

```powershell
aws cloudformation validate-template `
  --template-body file://infra/data-stack/template.yaml

rg -n "user_preferences|auth_sessions|refreshTokenHash|expiresAt|idempotency_key|uq_plan_reaction_user_itinerary" docs infra reports
```

**Dependencies:** Tasks 2 through 9

**Files likely touched:** None

**Estimated scope:** S

## Task 11: Run environment validation

**Description:** Validate the extension against actual dev AWS/Data Stack resources when credentials and network access are available.

**Acceptance criteria:**

- [ ] Updated CloudFormation deploy succeeds in dev.
- [ ] RDS schema or migration applies successfully.
- [ ] `SHOW CREATE TABLE` confirms the new RDS table, columns, indexes, and constraints.
- [ ] DynamoDB `auth_sessions` table exists with GSI and TTL.
- [ ] `/lovv/dev/ddb/auth_sessions` exists in SSM.

**Verification:**

```powershell
aws cloudformation deploy `
  --stack-name lovv-dev-data-stack `
  --template-file infra/data-stack/template.yaml `
  --parameter-overrides file://infra/data-stack/parameters/dev.parameters.example.json
```

```sql
SHOW CREATE TABLE users;
SHOW CREATE TABLE social_accounts;
SHOW CREATE TABLE user_preferences;
SHOW CREATE TABLE itineraries;
SHOW CREATE TABLE itinerary_items;
SHOW CREATE TABLE plan_reactions;
```

```powershell
aws dynamodb describe-table --table-name lovv_dev_auth_sessions
aws dynamodb describe-time-to-live --table-name lovv_dev_auth_sessions
aws ssm get-parameter --name /lovv/dev/ddb/auth_sessions
```

**Dependencies:** Task 10

**Files likely touched:** None

**Estimated scope:** M

## Final Checkpoint: Ready for service API implementation

- [ ] Auth APIs have user, provider account, and session persistence.
- [ ] Preference APIs have a one-row-per-user preference table.
- [ ] Saved Plans APIs have idempotency, snapshot, soft-delete, and multi-day item support.
- [ ] Reaction APIs have one-row toggle semantics.
- [ ] SAM can discover `auth_sessions` through SSM.
- [ ] No provider tokens or plaintext refresh tokens are persisted.

# 5. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Existing duplicate reactions block the new unique constraint | Migration failure | Run duplicate check and resolve duplicates before constraint creation. |
| MySQL unique constraints with nullable columns do not prevent duplicate null-key rows | Duplicate saved plans can still happen if API omits keys | Require idempotency fields in save-plan API for deduplicated writes. |
| `itinerary_items` uniqueness change conflicts with existing `uq_item_order` | Multi-day plans cannot reuse per-day sort order | Replace with or migrate toward `itinerary_id + day_index + sort_order`. |
| DynamoDB TTL deletion is asynchronous | Expired sessions may remain physically present | API must treat `expiresAt` and `revokedAt` as authoritative before accepting a session. |
| CHECK constraints differ by MySQL version/tooling | Invalid enum-like values may be inserted | Enforce allowed values in application validation and keep DB checks where supported. |
| Adding `itinerary_days` too early expands migration and API work | Slower delivery | Defer until day-level response metadata is required. |

# 6. Parallelization Opportunities

Safe to parallelize after Task 1:

- RDS schema edits for users/social accounts/preferences.
- DynamoDB `auth_sessions` CloudFormation edits.
- Documentation updates that reference finalized names.

Must be sequential:

- Duplicate checks before unique constraints.
- CloudFormation validation before deployment.
- RDS migration application before service API handlers depend on new fields.

Needs coordination:

- API request/response contracts for preference enum values and saved-plan idempotency keys.
- Decision to include or defer `itinerary_days`.

# 7. Open Questions

- Resolved for first implementation: defer `itinerary_days`; use `itinerary_items.day_index` as the first-pass multi-day model.
- Resolved for first implementation: keep `nickname` additive beside `display_name` for compatibility.
- Resolved for first implementation: enforce `country_track` for `KR` and `JP` at DB level; keep other preference/trip-style enum validation in the API layer until frontend values are finalized.
- Deferred product decision: whether user withdrawal should hard-delete social account links or preserve them with `users.deleted_at`.

# 8. Definition of Done

- `docs/SPEC/service_api_schema_extension_spec.md` is accepted as the service API schema contract.
- `docs/PLAN/service_api_schema_extension_plan.md` is accepted as the implementation breakdown.
- RDS and DynamoDB artifacts are updated for the service API extension.
- Static validation passes locally where tooling is available.
- Live dev validation is completed or explicitly recorded as blocked by missing AWS/network access.

# 9. Change Log

| Version | Date | Author | Changes |
| --- | --- | --- | --- |
| v0.1 | 2026-06-10 | Codex | Initial implementation plan for service API schema extension. |

# DynamoDB TTL Application Write Analysis

Date: 2026-06-18
Branch: `feat/dynamodb-cache-ttl`
Scope: `infra/data-stack/template.yaml`, `src/**`, `tests/**`

## Summary

`infra/data-stack/template.yaml` already enables DynamoDB TTL through `TimeToLiveSpecification` on six expiring tables.

The infrastructure side is therefore not the main missing piece. DynamoDB TTL only deletes items when the application writes the exact configured TTL attribute name as a numeric Unix epoch timestamp in seconds. The follow-up implementation should focus on application write paths that create log, cache, job, agent-run, and auth-session items.

Current source status:

- `auth_sessions` already writes `expiresAt` as `int(expires_at_epoch)` in `src/auth/session_repository.py`.
- No source write repository was found for `user_event_logs`, `agent_runs`, `festival_verify_cache`, `async_jobs`, or `api_logs`.
- The five non-auth TTL tables are defined and published by the Data Stack, but this repo currently has no Python code that writes items into those tables.

## Current TTL Table Contract

The current `infra/data-stack/template.yaml` line numbers differ from the external note, but the table contracts match.

| Logical resource | Physical table pattern | TTL attribute | Current template lines |
| --- | --- | --- | --- |
| `UserEventLogsTable` | `lovv_${EnvName}_user_event_logs` | `expires_at` | `infra/data-stack/template.yaml:470`, `infra/data-stack/template.yaml:494-497` |
| `AgentRunsTable` | `lovv_${EnvName}_agent_runs` | `expires_at` | `infra/data-stack/template.yaml:531`, `infra/data-stack/template.yaml:555-558` |
| `FestivalVerifyCacheTable` | `lovv_${EnvName}_festival_verify_cache` | `expires_at` | `infra/data-stack/template.yaml:592`, `infra/data-stack/template.yaml:608-610` |
| `AsyncJobsTable` | `lovv_${EnvName}_async_jobs` | `expires_at` | `infra/data-stack/template.yaml:619`, `infra/data-stack/template.yaml:635-637` |
| `ApiLogsTable` | `lovv_${EnvName}_api_logs` | `expires_at` | `infra/data-stack/template.yaml:646`, `infra/data-stack/template.yaml:666-668` |
| `AuthSessionsTable` | `lovv_${EnvName}_auth_sessions` | `expiresAt` | `infra/data-stack/template.yaml:734`, `infra/data-stack/template.yaml:748-751` |

The Data Stack also publishes table names through SSM parameters:

- `/lovv/${EnvName}/ddb/user_event_logs`
- `/lovv/${EnvName}/ddb/agent_runs`
- `/lovv/${EnvName}/ddb/festival_verify_cache`
- `/lovv/${EnvName}/ddb/async_jobs`
- `/lovv/${EnvName}/ddb/api_logs`
- `/lovv/${EnvName}/ddb/auth_sessions`

See `infra/data-stack/template.yaml:953-1002`.

## Source Analysis

### Auth sessions

Relevant files:

- `src/auth/session_repository.py`
- `src/auth/app.py`
- `tests/test_session_repository.py`
- root SAM template `template.yaml`

`src/auth/session_repository.py` already satisfies the DynamoDB TTL write contract:

```python
item = {
    "sessionId": session_id,
    "userId": user_id,
    "provider": provider,
    "refreshTokenHash": refresh_token_hash,
    "createdAt": now,
    "expiresAt": int(expires_at_epoch),
}
```

The root SAM template passes the data-stack table name into the Auth Lambda with `AUTH_SESSIONS_TABLE_NAME`, and grants only the required DynamoDB actions against that table and its refresh-token GSI.

`src/auth/app.py` computes the session expiry as:

```python
expires_at_epoch = _now_epoch() + _refresh_ttl_seconds()
```

and passes it into `session_repository.create_session(...)`.

This means the auth-session TTL path already uses:

- configured attribute name: `expiresAt`
- expected value type: integer epoch seconds
- active-session guard: `_is_active()` rejects expired or revoked sessions even if DynamoDB TTL deletion is delayed

Recommended small test improvement:

- Extend `tests/test_session_repository.py` with a fake `put_item` assertion so it verifies `create_session()` writes `expiresAt` as an `int`.

### Non-auth TTL tables

Searches across `src/**` and `tests/**` found no `put_item`, `update_item`, or repository code for:

- `lovv_user_event_logs`
- `lovv_agent_runs`
- `lovv_festival_verify_cache`
- `lovv_async_jobs`
- `lovv_api_logs`

The only current DynamoDB write repository in `src/**` is `DynamoDbSessionRepository`.

Therefore Claude should not look for an existing non-auth writer to patch unless new files have appeared after this report. The likely implementation is to add focused repository/writer modules and wire them into the owning Lambda paths only where product behavior already needs those records.

## Retention Rules From Existing Docs

Existing docs define the retention windows for the `expires_at` tables:

| Table | Retention rule |
| --- | --- |
| `user_event_logs` | 90 days |
| `agent_runs` | 30 days |
| `async_jobs` | 14 days |
| `api_logs` | 30 days |
| `festival_verify_cache` | `confirmed`: 30 days, `tentative`: 7 days, `unknown` or `outdated`: 1 day |

Reference: `docs/prd/db_build_prd.md:251-259` and `docs/spec/db_build_spec.md:241-245`.

## Implementation Guidance For Claude

### Do not change

- Do not rename TTL attributes in `infra/data-stack/template.yaml`.
- Do not change `auth_sessions` from `expiresAt` to `expires_at`; the Data Stack and Auth API contract intentionally use camelCase for auth sessions.
- Do not rely on DynamoDB TTL deletion timing for authorization or business correctness. TTL deletion is asynchronous.

### Add or patch only where there is a real write path

For every item written to the five non-auth expiring tables, include:

```python
"expires_at": int(expiry_epoch_seconds)
```

For auth sessions, keep:

```python
"expiresAt": int(expires_at_epoch)
```

### Suggested helper

If adding multiple writers, prefer one small shared helper to avoid inconsistent time math:

```python
def ttl_epoch(now_epoch, retention_seconds):
    return int(now_epoch) + int(retention_seconds)
```

Do not make this helper responsible for table-specific attribute names. Keep the table writer explicit so `expires_at` and `expiresAt` do not get mixed.

### Suggested target modules

Only create these if the corresponding feature write path is in scope:

- `src/shared/dynamodb_ttl.py`: time/retention helper constants.
- `src/shared/operational_events.py`: user event and API log writer.
- `src/agentcore/run_repository.py`: agent run state writer.
- `src/agentcore/festival_cache_repository.py`: festival verify cache writer.
- `src/shared/async_jobs.py`: async job status writer.

Keep each repository testable with injected DynamoDB resource/table objects, following the `DynamoDbSessionRepository` pattern.

### Environment and IAM wiring

The root SAM app currently only exposes `AUTH_SESSIONS_TABLE_NAME` to `AuthFunction`.

If new Lambda code writes the five non-auth tables, the root `template.yaml` must also receive table names from deploy parameters or SSM-resolved parameters and grant narrowly scoped IAM actions:

- `dynamodb:PutItem`
- `dynamodb:UpdateItem` only if status/cache updates are required
- `dynamodb:GetItem` or `dynamodb:Query` only for cache reads or lookups

Do not grant broad table wildcards if a function only needs one table.

## Acceptance Criteria

1. `auth_sessions` remains on `expiresAt`, and tests confirm `create_session()` writes an integer `expiresAt`.
2. Any new writes to `user_event_logs`, `agent_runs`, `festival_verify_cache`, `async_jobs`, or `api_logs` include integer `expires_at`.
3. Retention windows match the existing PRD/spec values.
4. Lambda environment variables and IAM policies are added only for functions that actually read/write the target table.
5. Tests cover the exact TTL attribute name and value type for each added writer.
6. Validation includes:

```powershell
python -m pytest tests
$env:AWS_CLI_FILE_ENCODING='UTF-8'; aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml
```

If the root SAM template is changed, also run the project SAM validation command used by the team.

## Recommended First Patch

The lowest-risk first patch is not to change infrastructure. It is to add a test for the already implemented auth-session write path:

- Add `FakeSessionTable.put_item(...)` capture to `tests/test_session_repository.py`.
- Add a test calling `DynamoDbSessionRepository.create_session(...)`.
- Assert:
  - `Item["expiresAt"] == int(expires_at_epoch)`
  - `isinstance(Item["expiresAt"], int)`
  - `expires_at` is not present in the auth-session item

After that, implement non-auth TTL writes only when the owning write flows are introduced.

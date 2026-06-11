# Lovv Data Stack Build Spec

> Document version: v0.1
> Document status: Draft
> Created: 2026-06-10
> Source PRD: `docs/PRD/db_build_prd.md` v0.1
> Scope: Data store provisioning and schema creation only. AWS SAM application stack is out of scope.

# 1. Objective

Build the Lovv backend data stack independently from the AWS SAM application stack.

The spec converts the DB build PRD into an implementation contract for:

- RDS MySQL 8 database schema creation.
- DynamoDB table, key, GSI, and TTL creation.
- S3 image bucket configuration.
- Store identifier publication for later SAM consumption.
- Reference query compatibility for application developers.

Success means the data stack can be provisioned before SAM deployment, can survive SAM redeploys or rollbacks, and exposes stable identifiers that Lambda functions can consume through environment variables or parameter lookups.

# 2. Assumptions

- The first implementation targets AWS-managed services, not local-only emulators.
- Stateful resources are provisioned outside the AWS SAM template.
- Data stack resources are environment-specific, at minimum `dev` and `prod`.
- The implementation may use IaC such as Terraform, CloudFormation, CDK, or scripted AWS CLI, but the resulting resources must match this spec.
- Secrets are not committed to the repository and are not stored in plain text parameters.
- Initial data seeding is handled by a later preprocessing pipeline and is not part of this spec.

# 3. Non-Goals

- Do not define API Gateway or Lambda resources.
- Do not implement Auth, Map, AgentCore, recommendation, or agent logic.
- Do not build the data collection, preprocessing, or seed-loading pipeline.
- Do not provision S3 vector index or RAG index resources.
- Do not provision AWS Neptune.
- Do not change the canonical database design document unless explicitly requested.

# 4. Architecture Boundary

The data stack owns persistent resources. The SAM app stack only references them.

| Area | Data Stack | SAM App Stack |
| --- | --- | --- |
| Owns | RDS MySQL, DynamoDB tables, S3 image bucket | API Gateway, Lambda, app IAM roles |
| Lifecycle | Long-lived, deletion-protected where possible | Frequently redeployed |
| Deployment order | First | After Data Stack |
| Integration | Publishes endpoints, names, and ARNs | Reads identifiers through parameters or stack outputs |

Required exported identifiers:

| Identifier | Example parameter | Consumer |
| --- | --- | --- |
| RDS host | `/lovv/{env}/rds/host` | Auth, Map |
| RDS database name | `/lovv/{env}/rds/db_name` | Auth, Map |
| RDS secret ARN or secure parameter | `/lovv/{env}/rds/secret_arn` | Auth, Map |
| DynamoDB table names | `/lovv/{env}/ddb/{logical_table}` | Auth, Map, AgentCore |
| S3 image bucket name | `/lovv/{env}/s3/image_bucket` | Map |

# 5. Resource Naming

Use deterministic, environment-scoped names.

| Resource | Naming rule | Example |
| --- | --- | --- |
| RDS database | `lovv{env}` | `lovvdev` |
| DynamoDB table | `lovv_{env}_{table}` | `lovv_dev_user_event_logs` |
| S3 image bucket | `lovv-image-{env}-{account_or_suffix}` | `lovv-image-dev-123456789012` |
| SSM parameter | `/lovv/{env}/{service}/{name}` | `/lovv/dev/rds/host` |

If an existing AWS naming constraint requires a suffix, keep the logical name stable in parameters.

# 6. RDS MySQL Specification

## 6.1 Engine and Defaults

| Setting | Required value |
| --- | --- |
| Engine | MySQL 8 LTS-compatible |
| Character set | `utf8mb4` |
| Collation | `utf8mb4_0900_ai_ci` |
| Time policy | Store UTC in `DATETIME`; presentation timezone conversion belongs to the app |
| Binary storage | Not allowed in MySQL tables; store S3 keys or URLs only |

## 6.2 Tables

Create exactly these five tables for v0.1:

- `users`
- `social_accounts`
- `itineraries`
- `itinerary_items`
- `plan_reactions`

## 6.3 DDL Contract

```sql
CREATE TABLE users (
  id           CHAR(36)     NOT NULL,
  email        VARCHAR(255) NULL,
  display_name VARCHAR(80)  NOT NULL,
  avatar_url   VARCHAR(500) NULL,
  created_at   DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE social_accounts (
  id               CHAR(36)     NOT NULL,
  user_id          CHAR(36)     NOT NULL,
  provider         VARCHAR(30)  NOT NULL,
  provider_user_id VARCHAR(255) NOT NULL,
  created_at       DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_social_provider_user (provider, provider_user_id),
  KEY idx_social_user (user_id),
  CONSTRAINT fk_social_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE itineraries (
  id                  CHAR(36)     NOT NULL,
  user_id             CHAR(36)     NOT NULL,
  title               VARCHAR(160) NOT NULL,
  summary             TEXT         NULL,
  duration_label      VARCHAR(40)  NOT NULL,
  festival_choice     VARCHAR(80)  NULL,
  intensity_label     VARCHAR(40)  NULL,
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

CREATE TABLE itinerary_items (
  id                    CHAR(36)     NOT NULL,
  itinerary_id          CHAR(36)     NOT NULL,
  sort_order            INT          NOT NULL,
  time_slot             VARCHAR(40)  NULL,
  place_name            VARCHAR(160) NOT NULL,
  move_hint             VARCHAR(255) NULL,
  recommendation_reason TEXT         NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_item_order (itinerary_id, sort_order),
  CONSTRAINT fk_item_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE plan_reactions (
  id            CHAR(36)    NOT NULL,
  user_id       CHAR(36)    NOT NULL,
  itinerary_id  CHAR(36)    NOT NULL,
  reaction_type VARCHAR(30) NOT NULL,
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

## 6.4 Required Access Patterns

The schema must support these access patterns without table scans:

| ID | Pattern | Required index or key |
| --- | --- | --- |
| RDS-AP-001 | Find user by social provider identity | `uq_social_provider_user` |
| RDS-AP-002 | List saved itineraries by user, latest first | `idx_itinerary_user_saved` |
| RDS-AP-003 | Read itinerary detail and ordered items | `itineraries.id`, `uq_item_order` |
| RDS-AP-004 | Register user reaction to itinerary | `plan_reactions` PK and FK checks |
| RDS-AP-005 | Aggregate reactions by itinerary | `idx_reaction_itinerary` |
| RDS-AP-006 | Delete itinerary with dependent items and reactions | FK cascade constraints |

# 7. DynamoDB Specification

## 7.1 Defaults

| Setting | Required value |
| --- | --- |
| Billing mode | `PAY_PER_REQUEST` for PoC and early production |
| TTL attribute | `expires_at` for expiring tables |
| TTL type | Number, epoch seconds |
| Log body policy | Store summaries, references, and hashes only; do not store full private conversation bodies |

## 7.2 Tables

| Logical table | Partition key | Sort key | TTL |
| --- | --- | --- | --- |
| `user_event_logs` | `pk` | `sk` | `expires_at` |
| `agent_runs` | `pk` | `sk` | `expires_at` |
| `festival_verify_cache` | `pk` | `sk` | `expires_at` |
| `async_jobs` | `pk` | `sk` | `expires_at` |
| `api_logs` | `pk` | `sk` | `expires_at` |
| `content_documents` | `pk` | `sk` | None |
| `visitor_statistics` | `pk` | `sk` | None |

Physical names must include the environment prefix, for example `lovv_dev_agent_runs`.

## 7.3 Key Formats

| Logical table | `pk` format | `sk` format |
| --- | --- | --- |
| `user_event_logs` | `USER#{user_id_hash}` or `ANON#{anon_session_id}` | `EVENT#{created_at}#{event_id}` |
| `agent_runs` | `RUN#{agent_run_id}` | `STATE#{created_at}` |
| `festival_verify_cache` | `FESTIVAL#{festival_id}` | `YEAR#{travel_year}` |
| `async_jobs` | `JOB#{job_id}` | `STATUS#{updated_at}` |
| `api_logs` | `API#{yyyyMMdd}#{endpoint_group}` | `{created_at}#{request_id}` |
| `content_documents` | `CONTENT#{country}#{entity_type}` | `ENTITY#{entity_id}` |
| `visitor_statistics` | `CITY#{city_id}` | `STAT#{period}#{source_type}` |

## 7.4 GSI Contract

Create the following GSIs only on tables that contain the required attributes.

| GSI | Partition key | Sort key | Applies to | Purpose |
| --- | --- | --- | --- | --- |
| `GSI1RequestLookup` | `request_id` | `created_at` | `user_event_logs`, `agent_runs`, `api_logs` | Trace by request ID |
| `GSI2AgentRunLookup` | `agent_run_id` | `created_at` | `agent_runs` | Read all states for an agent run |
| `GSI3EventTypeDaily` | `event_type#yyyyMMdd` | `created_at` | `user_event_logs` | Daily event-type analysis |
| `GSI4RecommendationLookup` | `recommendation_request_id` | `created_at` | `user_event_logs`, `agent_runs` | Link recommendation request logs |

## 7.5 TTL Rules

| Logical table | Retention |
| --- | --- |
| `user_event_logs` | 90 days |
| `agent_runs` | 30 days |
| `async_jobs` | 14 days |
| `api_logs` | 30 days |
| `festival_verify_cache` | `confirmed`: 30 days, `tentative`: 7 days, `unknown` or `outdated`: 1 day |
| `content_documents` | No TTL |
| `visitor_statistics` | No TTL |

# 8. S3 Image Bucket Specification

Create one image bucket per environment.

| Setting | Required behavior |
| --- | --- |
| Public access | Block all direct public access |
| Encryption | Enable default server-side encryption, at least SSE-S3 |
| Versioning | Recommended for production; optional for dev |
| Access path | CloudFront or presigned URL; direct public object URLs are not allowed |
| DB reference | Store S3 key or URL in DB, not binary image content |

Required key prefixes:

```text
avatar/{user_id_hash}/{object_name}
content/{country}/{entity_type}/{entity_id}/{object_name}
tmp/{upload_session_id}/{object_name}
```

Temporary uploads under `tmp/` should have a short lifecycle expiration policy when supported by the chosen provisioning tool.

# 9. Implementation Structure

The repository should keep data-stack implementation artifacts separate from SAM app artifacts.

Recommended layout:

```text
infra/
  data-stack/
    README.md
    rds/
      schema.sql
    dynamodb/
      tables.*
    s3/
      image-bucket.*
    parameters/
      outputs.*
docs/
  PRD/
    db_build_prd.md
  SPEC/
    db_build_spec.md
```

If the implementation uses a single IaC tool, keep the same logical grouping in module names or file names.

# 10. Commands

The exact commands depend on the selected provisioning tool. Until the tool is chosen, the implementation must document equivalent commands for these actions:

```powershell
# Provision or update the data stack
<tool> apply -var env=dev

# Destroy non-production resources only
<tool> destroy -var env=dev

# Apply RDS schema if schema is not managed directly by IaC
mysql --host <rds-host> --user <user> --database <db-name> < infra/data-stack/rds/schema.sql

# Verify published parameters
aws ssm get-parameter --name /lovv/dev/rds/host
aws ssm get-parameter --name /lovv/dev/s3/image_bucket
```

Production destroy commands must require explicit human approval and should not be automated as a default script.

# 11. Validation Strategy

## 11.1 Provisioning Validation

- Confirm all expected resources exist in the target environment.
- Confirm data-stack outputs or SSM parameters exist for RDS, DynamoDB, and S3.
- Confirm secrets are stored in Secrets Manager or SecureString, not in plain text files.
- Confirm production-like resources have deletion protection or an equivalent guard where available.

## 11.2 RDS Validation

- Confirm all five tables exist.
- Confirm all primary keys, foreign keys, unique constraints, and indexes match section 6.
- Confirm `utf8mb4` charset and `utf8mb4_0900_ai_ci` collation.
- Confirm cascade delete behavior for itinerary child records.
- Run the PRD reference queries against controlled sample data.

## 11.3 DynamoDB Validation

- Confirm all seven tables exist.
- Confirm key schemas match section 7.
- Confirm TTL is enabled for the five expiring tables.
- Confirm GSIs exist on the required tables.
- Run representative Query/GetItem operations for request trace, agent run trace, cache lookup, async job status, content lookup, and visitor statistics lookup.

## 11.4 S3 Validation

- Confirm bucket public access is blocked.
- Confirm default encryption is enabled.
- Confirm expected prefixes can be written by authorized roles.
- Confirm unauthorized public reads are rejected.

# 12. Boundaries

Always:

- Keep data resources outside the SAM template.
- Use environment-scoped resource names.
- Publish stable resource identifiers for SAM consumption.
- Keep secrets out of Git.
- Preserve PRD-defined table, key, GSI, and TTL contracts unless the PRD changes.

Ask first:

- Changing table columns, key formats, or GSI definitions.
- Adding new AWS services.
- Adding dependencies or selecting a new IaC framework.
- Changing retention periods.
- Enabling production deletion or destructive migration.

Never:

- Commit credentials, DB passwords, access keys, or generated secret values.
- Store private full conversation logs in DynamoDB.
- Put image binaries into MySQL.
- Make the image bucket directly public.
- Couple stateful data resources to SAM stack deletion.

# 13. Acceptance Criteria

- Data stack implementation creates the RDS schema exactly as defined in section 6.
- Data stack implementation creates the seven DynamoDB tables, required GSIs, and TTL settings from section 7.
- Data stack implementation creates the image S3 bucket with public access blocked and encryption enabled.
- Store identifiers are available through SSM parameters or equivalent stack outputs.
- Reference MySQL and DynamoDB access patterns from the PRD can be executed against the provisioned schema.
- SAM app stack can receive data-store identifiers without owning the stateful resources.
- No secrets or generated credentials are committed.

# 14. Open Questions

- Which provisioning tool should be used for the data stack: Terraform, CloudFormation, CDK, or AWS CLI scripts?
- Should `prod` RDS deletion protection be mandatory from the first implementation?
- Should DynamoDB table names keep the PRD literal names in all environments or use environment-prefixed physical names with stable SSM parameters?
- Should `plan_reactions` enforce one reaction per user per itinerary through an additional unique constraint?
- Should S3 object keys store file extensions from original uploads or normalized content-type-derived extensions?

# 15. Implementation Task Breakdown

- [ ] Task: Select data-stack provisioning tool and directory layout.
  - Acceptance: The selected tool and command set are documented under `infra/data-stack/README.md`.
  - Verify: A developer can identify the exact deploy, update, and non-prod destroy commands.
  - Files: `infra/data-stack/README.md`

- [ ] Task: Implement RDS schema artifact.
  - Acceptance: `schema.sql` contains the five PRD-defined tables and constraints.
  - Verify: Apply to a MySQL 8-compatible database and inspect table constraints.
  - Files: `infra/data-stack/rds/schema.sql`

- [ ] Task: Implement DynamoDB table definitions.
  - Acceptance: Seven tables, required GSIs, and TTL settings are represented in IaC.
  - Verify: Provision dev environment and inspect table schemas.
  - Files: `infra/data-stack/dynamodb/*`

- [ ] Task: Implement S3 image bucket definition.
  - Acceptance: Bucket is environment-scoped, encrypted, and blocks public access.
  - Verify: Inspect bucket public access block and encryption settings.
  - Files: `infra/data-stack/s3/*`

- [ ] Task: Publish integration parameters.
  - Acceptance: RDS, DynamoDB, and S3 identifiers are exported through SSM parameters or stack outputs.
  - Verify: Retrieve expected identifiers with AWS CLI or equivalent.
  - Files: `infra/data-stack/parameters/*`

- [ ] Task: Add validation guide.
  - Acceptance: Validation commands cover RDS, DynamoDB, S3, and parameter publication.
  - Verify: A developer can run the guide after provisioning and determine pass/fail.
  - Files: `infra/data-stack/README.md`

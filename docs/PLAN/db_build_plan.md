# Implementation Plan: Lovv Data Stack Build

> Plan version: v0.1
> Created: 2026-06-10
> Source PRD: `docs/PRD/db_build_prd.md`
> Source Spec: `docs/SPEC/db_build_spec.md`
> Scope: RDS MySQL, DynamoDB, and S3 image bucket provisioning outside AWS SAM.

# 1. Overview

This plan breaks the Lovv Data Stack build into small implementation tasks. The goal is to provision stateful resources before the SAM application stack and expose stable identifiers that SAM Lambdas can consume later.

The work should proceed in this order:

1. Choose and document the provisioning method.
2. Create the data-stack directory structure.
3. Implement RDS schema artifacts.
4. Implement DynamoDB table artifacts.
5. Implement S3 image bucket artifacts.
6. Publish resource identifiers.
7. Add validation instructions.

# 2. Architecture Decisions

- Data resources must stay outside the AWS SAM template because their lifecycle is longer than Lambda/API Gateway deployments.
- The first implementation should keep resource contracts close to `docs/SPEC/db_build_spec.md` and avoid adding columns, tables, indexes, or services without explicit approval.
- Environment-specific names should be physical-resource names, while app-facing references should go through SSM parameters or equivalent stack outputs.
- Secrets must be held in Secrets Manager or SSM SecureString, never in Git or plain-text config.
- The plan intentionally does not include data seeding, RAG index creation, Neptune, or Lambda code.

# 3. Dependency Graph

```text
Provisioning tool decision
  -> Directory layout
    -> RDS schema artifact
    -> DynamoDB table artifact
    -> S3 image bucket artifact
      -> Published parameters / outputs
        -> Validation guide
          -> SAM integration readiness
```

# 4. Task List

## Phase 1: Foundation

## Task 1: Select provisioning method

**Description:** Decide whether the data stack will be implemented with Terraform, CloudFormation, CDK, or AWS CLI scripts. Record the decision and exact command shape before creating resources.

**Acceptance criteria:**

- [ ] One provisioning method is selected.
- [ ] The selected method supports RDS, DynamoDB, S3, SSM parameters, and secret references.
- [ ] Deploy, update, and non-production destroy command shapes are documented.

**Verification:**

- [ ] Confirm `infra/data-stack/README.md` states the chosen method.
- [ ] Confirm production destructive operations are not part of the default workflow.

**Dependencies:** None

**Files likely touched:**

- `infra/data-stack/README.md`

**Estimated scope:** S

## Task 2: Create data-stack project layout

**Description:** Create a clear artifact layout under `infra/data-stack/` so RDS, DynamoDB, S3, parameters, and validation are maintained separately from SAM app resources.

**Acceptance criteria:**

- [ ] `infra/data-stack/` exists.
- [ ] RDS, DynamoDB, S3, and parameter artifacts have separate folders or modules.
- [ ] The layout does not imply SAM ownership of stateful resources.

**Verification:**

- [ ] Inspect the directory layout and confirm it matches the chosen provisioning method.

**Dependencies:** Task 1

**Files likely touched:**

- `infra/data-stack/README.md`
- `infra/data-stack/rds/*`
- `infra/data-stack/dynamodb/*`
- `infra/data-stack/s3/*`
- `infra/data-stack/parameters/*`

**Estimated scope:** S

## Checkpoint A: Foundation review

- [ ] Provisioning method is accepted.
- [ ] Directory layout is accepted.
- [ ] No provider credentials or secrets are committed.

# 5. Phase 2: RDS MySQL

## Task 3: Add RDS schema SQL

**Description:** Add the MySQL schema artifact for the five PRD-defined tables: `users`, `social_accounts`, `itineraries`, `itinerary_items`, and `plan_reactions`.

**Acceptance criteria:**

- [ ] `schema.sql` contains exactly the five v0.1 tables.
- [ ] PK, FK, unique constraints, and indexes match `docs/SPEC/db_build_spec.md`.
- [ ] Tables use `InnoDB`, `utf8mb4`, and `utf8mb4_0900_ai_ci`.

**Verification:**

- [ ] Apply the SQL to a MySQL 8-compatible dev database.
- [ ] Inspect constraints and indexes after apply.
- [ ] Confirm FK cascade behavior for itinerary deletion.

**Dependencies:** Task 2

**Files likely touched:**

- `infra/data-stack/rds/schema.sql`

**Estimated scope:** S

## Task 4: Add RDS provisioning artifact

**Description:** Define the RDS database resource or document the externally managed RDS prerequisite, depending on the chosen provisioning method.

**Acceptance criteria:**

- [ ] MySQL 8-compatible engine is declared or required.
- [ ] Database name follows the environment naming convention.
- [ ] Credentials are referenced from a secret store, not embedded.
- [ ] Production deletion protection is either enabled or explicitly marked as an open decision.

**Verification:**

- [ ] Dry-run, plan, or template validation succeeds for the chosen provisioning method.
- [ ] Confirm generated outputs include host and database name.

**Dependencies:** Task 1, Task 2

**Files likely touched:**

- `infra/data-stack/rds/*`
- `infra/data-stack/parameters/*`

**Estimated scope:** M

## Checkpoint B: RDS review

- [ ] RDS schema artifact matches the Spec.
- [ ] RDS resource lifecycle does not depend on SAM.
- [ ] Credentials are not committed.

# 6. Phase 3: DynamoDB

## Task 5: Define DynamoDB base tables

**Description:** Implement the seven DynamoDB table definitions from the Spec with `pk` and `sk` key attributes.

**Acceptance criteria:**

- [ ] Tables are defined for user event logs, agent runs, festival verify cache, async jobs, API logs, content documents, and visitor statistics.
- [ ] Each table uses the required partition key and sort key names.
- [ ] Billing mode is `PAY_PER_REQUEST` unless explicitly changed.

**Verification:**

- [ ] Dry-run, plan, or template validation succeeds.
- [ ] Inspect generated table definitions before deployment.

**Dependencies:** Task 2

**Files likely touched:**

- `infra/data-stack/dynamodb/*`

**Estimated scope:** M

## Task 6: Add DynamoDB TTL and GSI definitions

**Description:** Add TTL configuration and required GSIs to the DynamoDB definitions.

**Acceptance criteria:**

- [ ] TTL is enabled on `user_event_logs`, `agent_runs`, `festival_verify_cache`, `async_jobs`, and `api_logs`.
- [ ] TTL is not enabled on `content_documents` and `visitor_statistics`.
- [ ] Required GSIs are created only on tables that contain the relevant attributes.

**Verification:**

- [ ] Inspect table definitions for TTL attribute `expires_at`.
- [ ] Inspect GSI names and key schemas.

**Dependencies:** Task 5

**Files likely touched:**

- `infra/data-stack/dynamodb/*`

**Estimated scope:** M

## Checkpoint C: DynamoDB review

- [ ] Seven table definitions exist.
- [ ] TTL configuration matches retention rules.
- [ ] GSI contracts match the Spec.

# 7. Phase 4: S3 Image Bucket

## Task 7: Define image bucket

**Description:** Define one environment-scoped S3 image bucket for profile and content images.

**Acceptance criteria:**

- [ ] Bucket name follows the environment naming rule.
- [ ] Block Public Access is enabled.
- [ ] Default server-side encryption is enabled.
- [ ] Direct public object access is not allowed.

**Verification:**

- [ ] Dry-run, plan, or template validation succeeds.
- [ ] Inspect bucket public-access and encryption settings after deployment.

**Dependencies:** Task 2

**Files likely touched:**

- `infra/data-stack/s3/*`

**Estimated scope:** S

## Task 8: Add image key prefix and lifecycle notes

**Description:** Document the object key contract for application code and define lifecycle handling for temporary uploads if supported by the provisioning tool.

**Acceptance criteria:**

- [ ] Prefixes are documented for `avatar/`, `content/`, and `tmp/`.
- [ ] Temporary upload expiration is defined or explicitly deferred.
- [ ] DB storage guidance states that only S3 keys or URLs are stored.

**Verification:**

- [ ] Inspect README or bucket configuration for prefix and lifecycle rules.

**Dependencies:** Task 7

**Files likely touched:**

- `infra/data-stack/README.md`
- `infra/data-stack/s3/*`

**Estimated scope:** S

## Checkpoint D: S3 review

- [ ] Bucket security defaults are correct.
- [ ] Image key contract is clear enough for app developers.
- [ ] No bucket policy makes objects public by default.

# 8. Phase 5: Integration Parameters

## Task 9: Publish RDS parameters

**Description:** Publish RDS host, database name, and secret reference for later SAM Lambda configuration.

**Acceptance criteria:**

- [ ] `/lovv/{env}/rds/host` is published.
- [ ] `/lovv/{env}/rds/db_name` is published.
- [ ] `/lovv/{env}/rds/secret_arn` or an equivalent secure reference is published.
- [ ] Password values are not exposed as plain text.

**Verification:**

- [ ] Retrieve expected parameter names with AWS CLI or chosen tooling.

**Dependencies:** Task 4

**Files likely touched:**

- `infra/data-stack/parameters/*`
- `infra/data-stack/rds/*`

**Estimated scope:** S

## Task 10: Publish DynamoDB and S3 parameters

**Description:** Publish DynamoDB table names and S3 image bucket name for later SAM Lambda configuration.

**Acceptance criteria:**

- [ ] All seven DynamoDB logical table names are exposed through stable parameters or outputs.
- [ ] S3 image bucket name is exposed.
- [ ] Logical names are stable even if physical names include account-specific suffixes.

**Verification:**

- [ ] Retrieve all expected parameter names with AWS CLI or chosen tooling.

**Dependencies:** Task 5, Task 6, Task 7

**Files likely touched:**

- `infra/data-stack/parameters/*`
- `infra/data-stack/dynamodb/*`
- `infra/data-stack/s3/*`

**Estimated scope:** M

## Checkpoint E: SAM readiness review

- [ ] SAM can receive all required resource identifiers without owning resources.
- [ ] Parameter names are deterministic across environments.
- [ ] Secure values are not plain-text outputs.

# 9. Phase 6: Validation Guide

## Task 11: Add validation commands and checklist

**Description:** Add a validation section to the data-stack README covering RDS, DynamoDB, S3, and parameter publication.

**Acceptance criteria:**

- [ ] RDS validation covers tables, indexes, FKs, charset, collation, and cascade behavior.
- [ ] DynamoDB validation covers table names, keys, TTL, and GSIs.
- [ ] S3 validation covers public access block and encryption.
- [ ] Parameter validation covers RDS, DynamoDB, and S3 identifiers.

**Verification:**

- [ ] A developer can follow the README and mark each data-stack resource pass/fail.

**Dependencies:** Task 9, Task 10

**Files likely touched:**

- `infra/data-stack/README.md`

**Estimated scope:** M

## Task 12: Run dev-stack validation

**Description:** Provision or inspect the dev environment and execute the validation guide.

**Acceptance criteria:**

- [ ] RDS validation passes.
- [ ] DynamoDB validation passes.
- [ ] S3 validation passes.
- [ ] Parameter publication validation passes.
- [ ] Any deviations are documented as follow-up issues.

**Verification:**

- [ ] Save validation output or checklist results in the chosen project note location.

**Dependencies:** Task 11

**Files likely touched:**

- `infra/data-stack/README.md`
- `docs/PLAN/db_build_plan.md` or a separate validation note if needed

**Estimated scope:** M

# 10. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Provisioning tool is not selected | Implementation can drift or become hard to reproduce | Decide Task 1 before writing IaC |
| RDS schema drifts from PRD | App queries fail or docs become unreliable | Treat `docs/SPEC/db_build_spec.md` as implementation contract |
| DynamoDB GSIs are over-applied | Extra cost and operational complexity | Create GSIs only on tables with required attributes |
| Secrets leak into repo | Security incident | Use Secrets Manager or SecureString; review diffs before commit |
| Production delete path is too easy | Data loss | Require explicit approval and enable deletion protection |
| SAM starts owning stateful resources | Coupled lifecycle and accidental deletion | Keep resources under `infra/data-stack/`, not SAM template |

# 11. Open Decisions

- Provisioning tool: Terraform, CloudFormation, CDK, or AWS CLI scripts.
- Whether production RDS deletion protection is mandatory in v0.1.
- Whether DynamoDB physical names use exact PRD names or environment-prefixed names with stable parameters.
- Whether `plan_reactions` needs a uniqueness rule for one reaction per user per itinerary.
- Whether temporary S3 uploads should expire in dev only or all environments.

# 12. Definition of Done

- [ ] The selected provisioning method is documented.
- [ ] Data-stack artifacts exist outside SAM.
- [ ] RDS schema matches the Spec.
- [ ] DynamoDB tables, TTL, and GSIs match the Spec.
- [ ] S3 image bucket security settings match the Spec.
- [ ] Integration parameters expose all required identifiers.
- [ ] Validation guide exists and can be executed by another developer.
- [ ] No secrets or generated credentials are committed.

# 13. Execution Status

Updated: 2026-06-10

Completed in repository artifacts:

- [x] Provisioning method selected: CloudFormation outside SAM.
- [x] Data-stack directory layout created under `infra/data-stack/`.
- [x] RDS schema SQL added at `infra/data-stack/rds/schema.sql`.
- [x] RDS reference queries added at `infra/data-stack/rds/reference_queries.sql`.
- [x] CloudFormation template added at `infra/data-stack/template.yaml`.
- [x] DynamoDB seven-table, TTL, and GSI definitions added to the template.
- [x] S3 image bucket definition added with public access block, encryption, versioning, and `tmp/` lifecycle expiration.
- [x] SSM parameter publication added for RDS, DynamoDB, and S3 identifiers.
- [x] Validation guide added to `infra/data-stack/README.md`.
- [x] Detailed README content moved to `reports/data_stack_build_report.md`.
- [x] Development parameter source added at `infra/data-stack/parameters/dev.parameters.example.json`.
- [x] Development VPC, private subnets, and RDS security group added to the CloudFormation template.
- [x] SAM integration notes added to `reports/data_stack_build_report.md`.
- [x] Local development storage decision recorded: keep RDS ledger and use Docker MySQL for SAM local.
- [x] VPC connection guide added to `reports/data_stack_build_report.md`.

Not executed in this session:

- [ ] CloudFormation template validation with AWS CLI.
- [ ] Dev stack deployment.
- [ ] RDS schema application to a live MySQL instance.
- [ ] Live AWS validation checklist.

Reason: the current execution produced repository artifacts only. Live AWS validation requires configured AWS account, subnet IDs, security group IDs, credentials, and explicit approval to provision resources.

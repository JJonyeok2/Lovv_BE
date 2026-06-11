# Current Status Report: Lovv Data Stack

> Report version: v0.1
> Created: 2026-06-10
> Scope: Current repository state before committing Data Stack PRD, Spec, Plan, infrastructure artifacts, and reports.

# 1. Summary

The Lovv backend repository now contains a first-pass Data Stack contract and implementation artifact set for AWS-managed stateful resources outside the AWS SAM application stack.

Current implementation direction:

- CloudFormation is the provisioning mechanism for the Data Stack.
- AWS SAM remains responsible for Lambda, API Gateway, and application IAM.
- RDS MySQL remains the service ledger.
- DynamoDB remains the log/cache/job/content/statistics store.
- S3 remains the image object store.
- SAM local development should use Docker MySQL instead of replacing the RDS ledger with DynamoDB.

# 2. Created Artifacts

Documentation:

- `docs/PRD/db_build_prd.md`
- `docs/SPEC/db_build_spec.md`
- `docs/SPEC/service_api_schema_extension_spec.md`
- `docs/PLAN/db_build_plan.md`
- `docs/PLAN/service_api_schema_extension_plan.md`

Infrastructure:

- `infra/data-stack/template.yaml`
- `infra/data-stack/README.md`
- `infra/data-stack/parameters/dev.parameters.example.json`
- `infra/data-stack/rds/schema.sql`
- `infra/data-stack/rds/reference_queries.sql`

Reports:

- `reports/data_stack_build_report.md`
- `reports/current_status_report.md`

# 3. Data Stack Contents

CloudFormation currently defines:

- Development VPC.
- Two private subnets.
- Private route table and subnet associations.
- RDS security group.
- Interface endpoint security group.
- VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3.
- RDS DB subnet group.
- RDS MySQL DB instance with managed master user secret.
- Eight DynamoDB tables with required TTL/GSI configuration, including `auth_sessions`.
- S3 image bucket with public access blocked, encryption, versioning, and `tmp/` lifecycle expiration.
- SSM parameters for RDS, network, DynamoDB, and S3 identifiers.

RDS SQL currently defines:

- `users`
- `social_accounts`
- `user_preferences`
- `itineraries`
- `itinerary_items`
- `plan_reactions`

Service API schema reinforcement has been applied for:

- Auth profile/account state: `users`, `social_accounts`
- Onboarding and my-page preferences: `user_preferences`
- Saved-plan idempotency and snapshots: `itineraries`
- Multi-day map-ready plan items: `itinerary_items`
- Like/dislike toggle uniqueness: `plan_reactions`
- Refresh-token sessions: DynamoDB `auth_sessions`

# 4. Deployment Status

Repository artifacts are prepared.

Not confirmed in this repository session:

- CloudFormation stack deployment result.
- Live AWS resource validation.
- Live RDS schema application.
- Existing RDS table state after service API schema extension.
- Existing RDS table state before service API extension constraints.

If tables were already created before the schema extension, the live database needs controlled `ALTER TABLE` migration steps and duplicate checks before adding unique constraints.

# 5. SAM Integration Status

The report now documents how SAM developers and agents should consume the Data Stack:

- Read RDS/DynamoDB/S3/network identifiers from SSM Parameter Store.
- Do not duplicate stateful resources in SAM.
- Add `VpcConfig` to Lambda functions that require RDS.
- Use Data Stack private subnet IDs for deployed Lambda functions.
- Use Secrets Manager ARN for DB credentials.

Important current network note:

- The v0.1 Data Stack allows RDS ingress by `DevMysqlIngressCidr`.
- Recommended hardening is to move to Lambda security-group-based RDS ingress.

# 6. Local Development Decision

Decision recorded:

- Keep RDS MySQL as the service ledger.
- Do not replace the RDS ledger with DynamoDB for SAM local convenience.
- Use Docker MySQL for SAM local development.
- Use Data Stack RDS for deployed dev.

Reason:

- The RDS ledger depends on relational integrity: FK, cascade delete, unique constraints, ordered items, and aggregate queries.
- DynamoDB replacement would require a data model redesign and app-managed integrity.

# 7. Immediate Next Steps

Recommended next operational steps:

1. Validate the CloudFormation template with the target AWS profile.
2. Deploy `lovv-dev-data-stack`.
3. Confirm SSM parameters exist.
4. Retrieve the RDS secret from Secrets Manager.
5. Apply `infra/data-stack/rds/schema.sql` from a network path that can reach private RDS.
6. If the old schema was already applied, run a controlled service API extension migration and duplicate checks before unique constraints.
7. Add SAM-side `VpcConfig`, DB env vars, Secrets Manager permission, DynamoDB permissions, and S3 permissions.
8. Wire Auth APIs to `/lovv/dev/ddb/auth_sessions` after the updated Data Stack is deployed.

# 8. Validation Not Run

No live validation was run in this repository session.

Reason:

- AWS deployment and RDS schema application require target AWS credentials, account state, region, profile, and private network access decisions.

# 9. Follow-up Fix

- RDS for MySQL master username is standardized as `lovvadmin`.
- The previous `lovv_admin` form can pass CloudFormation template validation but may fail during actual RDS creation because MySQL master usernames should use letters and numbers only.
- The template validation pattern now rejects underscores for `DBMasterUsername`.
- RDS for MySQL initial database name is standardized as `lovvdev`.
- The previous `lovv_dev` form can pass CloudFormation template validation but may fail during actual RDS creation because the initial DB name should use letters and numbers only.
- The Data Stack now provisions private AWS service access through VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3.
- General internet egress is still out of scope unless NAT Gateway or another egress path is added later.
- Service API schema extension artifacts have been added for Auth, Preference, Saved Plans, and Reaction flows.

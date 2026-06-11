# Lovv Data Stack Build Report

> Report version: v0.1
> Created: 2026-06-10
> Source PRD: `docs/PRD/db_build_prd.md`
> Source Spec: `docs/SPEC/db_build_spec.md`
> Service API Extension Spec: `docs/SPEC/service_api_schema_extension_spec.md`
> Source Plan: `docs/PLAN/db_build_plan.md`
> Implementation path: `infra/data-stack/`

# 1. Scope

This report records the current Data Stack implementation artifacts and the development-environment deployment contract.

Included:

- RDS MySQL 8-compatible DB instance provisioning.
- RDS schema SQL for the PR #1 tables plus service API extensions.
- DynamoDB table, GSI, and TTL provisioning, including refresh-token auth sessions.
- S3 image bucket provisioning.
- SSM parameters and CloudFormation outputs for SAM integration.

Excluded:

- API Gateway and Lambda resources.
- Data collection, preprocessing, and seed loading.
- S3 vector index and RAG resources.
- AWS Neptune.
- Application code.

# 2. Provisioning Method

The v0.1 implementation uses plain CloudFormation instead of embedding stateful resources in the SAM template.

Template:

```text
infra/data-stack/template.yaml
```

Development parameter example:

```text
infra/data-stack/parameters/dev.parameters.example.json
```

RDS schema:

```text
infra/data-stack/rds/schema.sql
```

Reference queries:

```text
infra/data-stack/rds/reference_queries.sql
```

Service API schema extension:

```text
docs/SPEC/service_api_schema_extension_spec.md
docs/PLAN/service_api_schema_extension_plan.md
```

# 3. Development Environment Defaults

Development usage is standardized on these values:

| Item | Value |
| --- | --- |
| Environment | `dev` |
| CloudFormation stack | `lovv-dev-data-stack` |
| RDS database | `lovvdev` |
| RDS instance identifier | `lovv-dev-mysql` |
| RDS instance class | `db.t4g.micro` |
| RDS allocated storage | `20` GiB |
| RDS master username | `lovvadmin` |
| RDS deletion protection | `true` |
| DynamoDB prefix | `lovv_dev_` |
| S3 image bucket | `lovv-image-dev-{AWS::AccountId}` |
| SSM parameter prefix | `/lovv/dev/` |

The current dev template creates its own development VPC, two private subnets, and RDS security group. Separate subnet IDs and security group IDs are not required for the default dev deployment.
The current dev template also creates VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3. SAM Lambda functions attached to the private subnets can call these AWS services without a NAT Gateway.
General internet egress is not provided. External API calls require a separate NAT Gateway, egress proxy, or non-VPC Lambda design.

# 4. Deploy Development Stack

Before deployment, review the dev CIDR values in `infra/data-stack/parameters/dev.parameters.example.json`.

```powershell
aws cloudformation deploy `
  --stack-name lovv-dev-data-stack `
  --template-file infra/data-stack/template.yaml `
  --parameter-overrides file://infra/data-stack/parameters/dev.parameters.example.json
```

Notes:

- The template creates private subnets for RDS.
- The template creates a development RDS security group with MySQL ingress controlled by `DevMysqlIngressCidr`.
- The template creates VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3.
- The template does not create a NAT Gateway.
- Keep `RDSDeletionProtection=true` unless explicitly testing teardown.
- The template uses RDS managed master user password. The generated secret ARN is published, not the password.

# 5. Apply RDS Schema

After CloudFormation creates the RDS instance, apply the SQL schema with a MySQL client from an allowed network path.

```powershell
$rdsHost = aws ssm get-parameter --name /lovv/dev/rds/host --query "Parameter.Value" --output text
$dbName = aws ssm get-parameter --name /lovv/dev/rds/db_name --query "Parameter.Value" --output text

mysql --host $rdsHost --user lovvadmin --database $dbName < infra/data-stack/rds/schema.sql
```

Retrieve the generated password from Secrets Manager using the ARN stored at:

```text
/lovv/dev/rds/secret_arn
```

Do not write the password into this repository.

# 6. Published Development Parameters

The stack publishes these SSM parameters for development:

```text
/lovv/dev/rds/host
/lovv/dev/rds/db_name
/lovv/dev/rds/secret_arn
/lovv/dev/network/vpc_id
/lovv/dev/network/private_subnet_a
/lovv/dev/network/private_subnet_c
/lovv/dev/network/rds_security_group
/lovv/dev/network/endpoint_security_group
/lovv/dev/ddb/user_event_logs
/lovv/dev/ddb/agent_runs
/lovv/dev/ddb/festival_verify_cache
/lovv/dev/ddb/async_jobs
/lovv/dev/ddb/api_logs
/lovv/dev/ddb/content_documents
/lovv/dev/ddb/visitor_statistics
/lovv/dev/ddb/auth_sessions
/lovv/dev/s3/image_bucket
```

SAM Lambdas should consume these values through environment variables or runtime parameter lookup. SAM must not own these resources.

# 7. DynamoDB Item Contracts

Use the key formats from `docs/SPEC/db_build_spec.md`.

For `GSI3EventTypeDaily`, the CloudFormation attribute name is:

```text
event_type_day
```

Store the value in the PRD logical format:

```text
{event_type}#{yyyyMMdd}
```

This keeps the value contract while avoiding special characters in the physical GSI key attribute name.

# 8. S3 Image Key Contract

Application code should write objects under these prefixes:

```text
avatar/{user_id_hash}/{object_name}
content/{country}/{entity_type}/{entity_id}/{object_name}
tmp/{upload_session_id}/{object_name}
```

The `tmp/` prefix expires after 7 days in the v0.1 template.

Direct public object access is blocked. Use CloudFront or presigned URLs for delivery.

# 9. SAM Integration Notes

SAM application developers and agents must treat this Data Stack as an external dependency. SAM should not recreate RDS, DynamoDB, S3, VPC, subnet, or data-stack security group resources.

## 9.1 Required Lookup Values

SAM should read these values from SSM Parameter Store or receive them as deployment parameters:

```text
/lovv/dev/rds/host
/lovv/dev/rds/db_name
/lovv/dev/rds/secret_arn
/lovv/dev/network/vpc_id
/lovv/dev/network/private_subnet_a
/lovv/dev/network/private_subnet_c
/lovv/dev/network/rds_security_group
/lovv/dev/ddb/user_event_logs
/lovv/dev/ddb/agent_runs
/lovv/dev/ddb/festival_verify_cache
/lovv/dev/ddb/async_jobs
/lovv/dev/ddb/api_logs
/lovv/dev/ddb/content_documents
/lovv/dev/ddb/visitor_statistics
/lovv/dev/ddb/auth_sessions
/lovv/dev/s3/image_bucket
```

## 9.2 Lambda to RDS Connectivity

For a SAM Lambda to connect to the RDS MySQL instance, all of the following must be true:

- The Lambda has `VpcConfig`.
- The Lambda is attached to the private subnets published by the Data Stack.
- The Lambda has a security group that can egress to MySQL port `3306`.
- The RDS security group allows inbound MySQL traffic from the Lambda network path.
- The Lambda uses `/lovv/dev/rds/host`, `/lovv/dev/rds/db_name`, and `/lovv/dev/rds/secret_arn` rather than hardcoded values.

Current v0.1 Data Stack behavior:

- The template creates a development VPC and two private subnets.
- The template creates an RDS security group.
- The template creates an endpoint security group and VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3.
- The RDS security group currently allows MySQL ingress from `DevMysqlIngressCidr`.
- Default `DevMysqlIngressCidr` is `10.40.0.0/16`, so traffic from Lambdas inside the generated dev VPC can reach RDS if the Lambda security group allows egress.
- General internet egress is still unavailable because NAT Gateway is not provisioned.

Recommended next hardening:

- Create a dedicated Lambda security group in the Data Stack or SAM Stack.
- Change RDS ingress from CIDR-based access to `SourceSecurityGroupId`-based access from the Lambda security group.
- Publish the Lambda security group ID as `/lovv/dev/network/lambda_security_group` if the Data Stack owns it.

## 9.3 SAM Template Example

Use this pattern in SAM resources that need RDS access:

```yaml
VpcConfig:
  SecurityGroupIds:
    - !Ref LambdaSecurityGroup
  SubnetIds:
    - "{{resolve:ssm:/lovv/dev/network/private_subnet_a}}"
    - "{{resolve:ssm:/lovv/dev/network/private_subnet_c}}"
Environment:
  Variables:
    LOVV_RDS_HOST: "{{resolve:ssm:/lovv/dev/rds/host}}"
    LOVV_RDS_DB_NAME: "{{resolve:ssm:/lovv/dev/rds/db_name}}"
    LOVV_RDS_SECRET_ARN: "{{resolve:ssm:/lovv/dev/rds/secret_arn}}"
```

If the Lambda security group is created in SAM, its egress must allow `tcp/3306` to the RDS security group or VPC CIDR. The RDS security group must also allow the corresponding inbound path.

## 9.4 DynamoDB and S3 Access from SAM

SAM Lambda IAM policies should allow only the required actions on the tables and bucket referenced through SSM parameters or deployment parameters.

Required DynamoDB access should be scoped by Lambda role:

- Auth Lambda: `auth_sessions`, user/session event logs, and auth-related log tables only.
- Map Lambda: content, visitor statistics, image bucket, saved itinerary API needs.
- AgentCore Lambda: agent runs, event logs, verify cache, async jobs, API logs, content documents, visitor statistics.

S3 image access should be scoped to:

```text
avatar/*
content/*
tmp/*
```

Do not grant public bucket access from SAM.

## 9.5 SAM Agent Checklist

Before implementing or modifying SAM resources, check:

- [ ] Do not define duplicate RDS, DynamoDB, S3, VPC, or subnet resources in SAM.
- [ ] Read Data Stack identifiers from SSM or deployment parameters.
- [ ] Add `VpcConfig` to Lambda functions that need RDS.
- [ ] Ensure Lambda network path can reach RDS on `tcp/3306`.
- [ ] Grant Secrets Manager read access only to the RDS secret ARN.
- [ ] Grant DynamoDB table access per Lambda responsibility.
- [ ] Grant S3 image bucket access without making the bucket public.

# 10. VPC Connection Guide

This section records how SAM developers and agents should connect application code to the Data Stack VPC.

## 10.1 Deployed SAM Lambda to RDS

For deployed SAM Lambda functions, connect through Lambda `VpcConfig`.

Required Data Stack parameters:

```text
/lovv/dev/network/private_subnet_a
/lovv/dev/network/private_subnet_c
/lovv/dev/network/rds_security_group
/lovv/dev/network/endpoint_security_group
/lovv/dev/rds/host
/lovv/dev/rds/db_name
/lovv/dev/rds/secret_arn
```

SAM template pattern:

```yaml
Parameters:
  LambdaSecurityGroupId:
    Type: AWS::EC2::SecurityGroup::Id
    Description: Security group attached to Lambda functions that need RDS access.

Resources:
  MapFunction:
    Type: AWS::Serverless::Function
    Properties:
      VpcConfig:
        SecurityGroupIds:
          - !Ref LambdaSecurityGroupId
        SubnetIds:
          - "{{resolve:ssm:/lovv/dev/network/private_subnet_a}}"
          - "{{resolve:ssm:/lovv/dev/network/private_subnet_c}}"
      Environment:
        Variables:
          LOVV_RDS_HOST: "{{resolve:ssm:/lovv/dev/rds/host}}"
          LOVV_RDS_DB_NAME: "{{resolve:ssm:/lovv/dev/rds/db_name}}"
          LOVV_RDS_SECRET_ARN: "{{resolve:ssm:/lovv/dev/rds/secret_arn}}"
```

Current v0.1 security model:

- Data Stack creates an RDS security group.
- RDS security group allows MySQL ingress from `DevMysqlIngressCidr`.
- Default `DevMysqlIngressCidr=10.40.0.0/16`.
- Lambda attached to the generated dev private subnets can reach RDS if its own security group permits egress.
- Lambda attached to the generated dev private subnets can reach Secrets Manager, SSM, DynamoDB, and S3 through VPC Endpoints.
- Lambda still cannot call arbitrary public internet APIs unless NAT Gateway or another egress path is added.

Recommended hardening:

- Create a dedicated Lambda security group.
- Replace CIDR-based RDS ingress with security-group-based ingress from the Lambda security group.
- Publish the Lambda security group as `/lovv/dev/network/lambda_security_group` if the Data Stack owns it.

## 10.2 SAM Local to Private RDS

`sam local invoke` runs inside a local Docker container. It does not automatically join the AWS VPC.

Therefore, local SAM code cannot reach private RDS unless one of these network paths exists:

- VPN from local machine to the dev VPC.
- Bastion host with SSH port forwarding.
- AWS Systems Manager Session Manager port forwarding.
- Temporary public RDS access with local public IP `/32` ingress. This is not recommended except for short debugging windows.

Recommended local development model:

```text
SAM local -> Docker MySQL
SAM deployed dev -> Data Stack RDS
```

## 10.3 SSM Session Manager Port Forwarding Option

If a bastion or EC2 instance exists in the same VPC and has SSM access, local developers can tunnel to private RDS without making RDS public.

```powershell
$rdsHost = aws ssm get-parameter `
  --name /lovv/dev/rds/host `
  --query "Parameter.Value" `
  --output text `
  --profile <your-profile>

aws ssm start-session `
  --target <ec2-instance-id> `
  --document-name AWS-StartPortForwardingSessionToRemoteHost `
  --parameters "host=$rdsHost,portNumber=3306,localPortNumber=13306" `
  --profile <your-profile>
```

Then connect locally through:

```powershell
mysql --host 127.0.0.1 --port 13306 --user lovvadmin --database lovvdev
```

## 10.4 Bastion SSH Port Forwarding Option

If using a bastion host:

```powershell
ssh -N `
  -L 13306:<rds-host-from-ssm>:3306 `
  ec2-user@<bastion-public-dns>
```

Then connect locally through:

```powershell
mysql --host 127.0.0.1 --port 13306 --user lovvadmin --database lovvdev
```

## 10.5 Temporary Public Access Option

This option is not recommended as a default workflow.

Only use it for short debugging windows when approved:

- Set RDS public accessibility intentionally.
- Restrict ingress to one local public IP `/32`.
- Revert the rule immediately after debugging.
- Never use broad CIDRs such as `0.0.0.0/0`.

Preferred alternatives are Docker MySQL for SAM local or SSM port forwarding for private RDS access.

# 11. Local Development Storage Decision

Decision: keep RDS MySQL as the service ledger and use local Docker MySQL for SAM local development. Do not replace the RDS ledger with DynamoDB only to improve SAM local convenience.

Rationale:

- The PRD defines MySQL as the ledger for users, social accounts, saved itineraries, itinerary items, and plan reactions.
- The RDS schema depends on relational guarantees: foreign keys, cascade delete, unique constraints, ordered child records, and reaction aggregation.
- DynamoDB can model these records, but it would move FK, cascade, uniqueness, and join-like behavior into application code.
- Changing the ledger from MySQL to DynamoDB would be a product/data-model decision, not just a local-development convenience change.
- SAM local has difficulty reaching private RDS because local Docker containers do not run inside the AWS VPC by default.

Recommended local development model:

```text
SAM local:
- Use Docker MySQL as the local replacement for RDS.
- Use DynamoDB Local or AWS dev DynamoDB for DynamoDB-backed logs/cache/job state.
- Use AWS dev SSM/Secrets/S3 only when credentials and network policy allow it.

SAM deployed dev:
- Use Data Stack RDS in private subnets.
- Use Data Stack DynamoDB tables.
- Use Data Stack S3 image bucket.
```

Rejected option:

```text
Replace RDS ledger with DynamoDB for all environments.
```

Reason rejected:

- It conflicts with the current PRD and Spec.
- It removes database-enforced relational integrity.
- It requires redesigning access patterns, conditional writes, denormalized item layouts, and delete workflows.
- It would require updates to PRD, Spec, Plan, CloudFormation, and future SAM application code.

Acceptable future revisit condition:

- Reopen this decision only if the product explicitly chooses a DynamoDB-ledger architecture and accepts app-managed integrity, denormalized records, and rewritten query/access patterns.

# 12. Validation Checklist

## 12.1 CloudFormation

```powershell
aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml
aws cloudformation describe-stacks --stack-name lovv-dev-data-stack
```

## 12.2 Parameters

```powershell
aws ssm get-parameter --name /lovv/dev/rds/host
aws ssm get-parameter --name /lovv/dev/rds/db_name
aws ssm get-parameter --name /lovv/dev/rds/secret_arn
aws ssm get-parameter --name /lovv/dev/network/vpc_id
aws ssm get-parameter --name /lovv/dev/network/private_subnet_a
aws ssm get-parameter --name /lovv/dev/network/private_subnet_c
aws ssm get-parameter --name /lovv/dev/network/rds_security_group
aws ssm get-parameter --name /lovv/dev/network/endpoint_security_group
aws ssm get-parameter --name /lovv/dev/s3/image_bucket
aws ssm get-parameter --name /lovv/dev/ddb/user_event_logs
aws ssm get-parameter --name /lovv/dev/ddb/agent_runs
aws ssm get-parameter --name /lovv/dev/ddb/festival_verify_cache
aws ssm get-parameter --name /lovv/dev/ddb/async_jobs
aws ssm get-parameter --name /lovv/dev/ddb/api_logs
aws ssm get-parameter --name /lovv/dev/ddb/content_documents
aws ssm get-parameter --name /lovv/dev/ddb/visitor_statistics
aws ssm get-parameter --name /lovv/dev/ddb/auth_sessions
```

## 12.3 RDS Schema

```sql
SHOW TABLES;
SHOW CREATE TABLE users;
SHOW CREATE TABLE social_accounts;
SHOW CREATE TABLE user_preferences;
SHOW CREATE TABLE itineraries;
SHOW CREATE TABLE itinerary_items;
SHOW CREATE TABLE plan_reactions;
```

Expected:

- Six service-ledger tables exist.
- All tables use `InnoDB`.
- Charset is `utf8mb4`.
- Collation is `utf8mb4_0900_ai_ci`.
- Foreign keys use `ON DELETE CASCADE ON UPDATE CASCADE`.
- Indexes match `docs/SPEC/db_build_spec.md` and `docs/SPEC/service_api_schema_extension_spec.md`.
- `plan_reactions` enforces one row per `user_id + itinerary_id`.
- `itinerary_items` orders multi-day plans by `itinerary_id + day_index + sort_order`.

## 12.4 DynamoDB

```powershell
aws dynamodb describe-table --table-name lovv_dev_user_event_logs
aws dynamodb describe-table --table-name lovv_dev_agent_runs
aws dynamodb describe-table --table-name lovv_dev_festival_verify_cache
aws dynamodb describe-table --table-name lovv_dev_async_jobs
aws dynamodb describe-table --table-name lovv_dev_api_logs
aws dynamodb describe-table --table-name lovv_dev_content_documents
aws dynamodb describe-table --table-name lovv_dev_visitor_statistics
aws dynamodb describe-table --table-name lovv_dev_auth_sessions
aws dynamodb describe-time-to-live --table-name lovv_dev_user_event_logs
aws dynamodb describe-time-to-live --table-name lovv_dev_agent_runs
aws dynamodb describe-time-to-live --table-name lovv_dev_festival_verify_cache
aws dynamodb describe-time-to-live --table-name lovv_dev_async_jobs
aws dynamodb describe-time-to-live --table-name lovv_dev_api_logs
aws dynamodb describe-time-to-live --table-name lovv_dev_auth_sessions
```

Expected:

- All eight tables exist.
- PR #1 log/cache/content/statistics tables use `pk` and `sk`.
- `auth_sessions` uses `sessionId` as PK and `GSI1RefreshTokenHashLookup` on `refreshTokenHash`.
- Log/cache/job/API tables have TTL enabled on `expires_at`.
- `auth_sessions` has TTL enabled on `expiresAt`.
- `content_documents` and `visitor_statistics` do not have TTL.
- GSIs match the Spec.

## 12.5 S3

```powershell
$bucket = aws ssm get-parameter --name /lovv/dev/s3/image_bucket --query "Parameter.Value" --output text
aws s3api get-public-access-block --bucket $bucket
aws s3api get-bucket-encryption --bucket $bucket
aws s3api get-bucket-versioning --bucket $bucket
aws s3api get-bucket-lifecycle-configuration --bucket $bucket
```

Expected:

- Public access block is fully enabled.
- Default encryption is enabled.
- Versioning is enabled.
- `tmp/` lifecycle expiration exists.

# 13. Non-Production Destroy

Only run destroy-style operations for non-production environments after explicit approval.

```powershell
aws cloudformation delete-stack --stack-name lovv-dev-data-stack
```

RDS, DynamoDB, and S3 resources use retention-oriented policies, so manual cleanup may still be required.

# 14. Implementation Notes

- RDS, DynamoDB, and S3 resources use retention-oriented policies so stack lifecycle changes do not casually remove stateful data.
- RDS table creation is a separate schema application step because basic CloudFormation provisions the DB instance but does not execute MySQL DDL.
- The service API extension adds `user_preferences`, `auth_sessions`, saved-plan idempotency fields, multi-day item ordering, and `UNIQUE(user_id, itinerary_id)` for reaction toggles.
- DynamoDB TTL retention windows depend on application writes setting correct `expires_at` values.
- `auth_sessions` TTL uses `expiresAt`, matching the Auth API token-session contract.
- SAM should use SSM parameters and must not hardcode environment-prefixed physical table or bucket names.

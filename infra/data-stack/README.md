# Lovv Data Stack

Stateful data-stack artifacts for Lovv backend. These resources are intentionally kept outside the AWS SAM application stack.

## Files

```text
infra/data-stack/template.yaml
infra/data-stack/parameters/dev.parameters.example.json
infra/data-stack/rds/schema.sql
infra/data-stack/rds/reference_queries.sql
```

Related schema contracts:

```text
docs/SPEC/db_build_spec.md
docs/SPEC/service_api_schema_extension_spec.md
docs/PLAN/service_api_schema_extension_plan.md
```

주석 정책:

- CloudFormation YAML과 SQL 파일에는 한국어 주석을 함께 둔다.
- JSON parameter example은 JSON 표준상 주석을 넣을 수 없으므로, 설명은 이 README와 `reports/` 문서에 둔다.

## Development defaults

Development is standardized as:

- Stack: `lovv-dev-data-stack`
- Environment: `dev`
- Database: `lovvdev`
- DynamoDB prefix: `lovv_dev_`
- SSM prefix: `/lovv/dev/`

Use `infra/data-stack/parameters/dev.parameters.example.json` as the single development parameter source. Replace placeholder subnet and security group IDs with actual development VPC values before deployment.
The template now creates the development VPC, two private subnets, and RDS security group directly, so separate subnet or security group IDs are not required for the default dev deployment.
The template also creates VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3 so SAM Lambda functions in the private subnets can reach required AWS services without a NAT Gateway.

## Report

Detailed deployment, validation, and operation notes have been moved to:

```text
reports/data_stack_build_report.md
```

SAM developers and agents should read the report section `SAM Integration Notes` before adding Lambda `VpcConfig`, database environment variables, Secrets Manager permissions, DynamoDB permissions, or S3 image-bucket permissions.

For VPC access patterns, read the report section `VPC Connection Guide`.

For Auth, Preference, Saved Plans, and Reaction APIs, read `docs/SPEC/service_api_schema_extension_spec.md` before changing RDS tables, DynamoDB auth sessions, or service reference queries.

# Implementation Plan: Lovv Data Stack NAT Instance

> Plan version: v0.1
> Created: 2026-06-18
> Source Spec: `docs/SPEC/nat_instance_spec.md`
> GitHub Issue: https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/13
> Baseline: `infra/data-stack/template.yaml`
> Scope: Optional dev-oriented NAT instance support for the Lovv Data Stack VPC.
> Implementation status: Local implementation and live dev deployment verification complete.

# 1. Overview

This plan turns the NAT instance spec into ordered implementation tasks. The goal is to add opt-in public internet egress for private-subnet workloads while preserving the current VPC Endpoint-first design for AWS service access.

Implementation should proceed in this order:

1. Completed: Confirm unresolved design choices from the spec.
2. Completed: Add template tests that lock the intended security and routing behavior.
3. Completed: Add CloudFormation parameters and conditions.
4. Completed: Add public subnet and Internet Gateway routing.
5. Completed: Add NAT instance IAM, security group, EC2, and private route.
6. Completed: Publish outputs and SSM parameters.
7. Completed: Update docs and validate the template.

# 2. Architecture Decisions

- NAT instance support stays disabled by default through `EnableNatInstance=false`.
- Existing VPC Endpoints for S3, DynamoDB, SSM, and Secrets Manager stay in place.
- Public internet egress is added only through an explicit private default route to the NAT instance.
- RDS remains in private subnets and remains `PubliclyAccessible: false`.
- NAT instance shell access uses SSM Session Manager; no public SSH ingress is added.
- v0.1 is dev-oriented and single-AZ. Production HA NAT requires a separate spec and plan.
- The public subnet is conditionally created with NAT resources in the first implementation.
- The default NAT instance type is `t4g.nano`.

# 3. Dependency Graph

```text
Spec decision gate
  -> Template test expectations
    -> Parameters and Conditions
      -> Public subnet + IGW + public route table
        -> NAT IAM role + instance profile
          -> NAT security group
            -> NAT EC2 instance UserData
              -> Private default route to NAT
                -> SSM parameters and outputs
                  -> README and parameter example updates
                    -> CloudFormation validation and tests
```

# 4. Task List

## Phase 1: Decision Gate

## Task 1: Resolve first-implementation choices

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/14

**Description:** Close the open questions from the spec before changing CloudFormation. This prevents rework around conditional resource shape, instance architecture, and supported workload scope.

**Acceptance criteria:**

- [x] Decide whether `LovvPublicSubnetA` is always created or only created when `EnableNatInstance=true`.
- [x] Choose default `NatInstanceType`: `t4g.nano` for lowest cost or `t4g.micro` for safer dev headroom.
- [x] Confirm v0.1 egress target is Lambda/private-subnet workloads only.
- [x] Record the decisions in `docs/SPEC/nat_instance_spec.md` if the spec needs refinement.

**Verification:**

- [x] `docs/SPEC/nat_instance_spec.md` has no unresolved blocker for v0.1 implementation.

**Dependencies:** None

**Files likely touched:**

- `docs/SPEC/nat_instance_spec.md`

**Estimated scope:** S

## Phase 2: Test Guardrails

## Task 2: Add NAT template tests

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/15

**Description:** Add template-inspection tests before implementation so security and routing expectations are explicit.

**Acceptance criteria:**

- [x] Tests assert `EnableNatInstance` exists and defaults to `false`.
- [x] Tests assert NAT EC2 uses `SourceDestCheck: false`.
- [x] Tests assert NAT EC2 requires IMDSv2.
- [x] Tests assert NAT security group does not allow SSH from `0.0.0.0/0`.
- [x] Tests assert private default route to NAT is conditional.
- [x] Tests assert existing VPC Endpoint resources remain present.
- [x] Tests assert RDS remains `PubliclyAccessible: false`.

**Verification:**

- [x] Run `python -m pytest tests\test_data_stack_nat_instance.py` and confirm new tests fail before implementation for the expected missing NAT resources.

**Dependencies:** Task 1

**Files likely touched:**

- `tests/test_data_stack_nat_instance.py`

**Estimated scope:** S

## Checkpoint A: Intent locked

- [x] v0.1 decisions are recorded.
- [x] Tests express the intended CloudFormation behavior.
- [x] No production or SSH expansion is included.

## Phase 3: CloudFormation Network Foundation

## Task 3: Add NAT parameters and conditions

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/16

**Description:** Add opt-in parameters and CloudFormation conditions for NAT resources.

**Acceptance criteria:**

- [x] `EnableNatInstance` exists with allowed values `true` and `false`, defaulting to `false`.
- [x] `PublicSubnetCidr` exists with a non-overlapping dev default such as `10.40.1.0/24`.
- [x] `NatInstanceType` exists with the approved default.
- [x] `NatInstanceAmiId` exists and resolves from an Amazon Linux 2023 public SSM parameter compatible with the instance type architecture.
- [x] `CreateNatInstance` condition or equivalent is defined.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Inspect the parameter block in `infra/data-stack/template.yaml`.

**Dependencies:** Task 2

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** S

## Task 4: Add public subnet and internet routing

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/17

**Description:** Add the public subnet, Internet Gateway, public route table, and public default route needed by the NAT instance.

**Acceptance criteria:**

- [x] `LovvPublicSubnetA` is defined in `LovvDevVPC`.
- [x] Public subnet uses `PublicSubnetCidr`, AZ 0, and `MapPublicIpOnLaunch: true`.
- [x] `LovvInternetGateway` and attachment are defined.
- [x] `LovvPublicRouteTable` is associated with `LovvPublicSubnetA`.
- [x] Public route table routes `0.0.0.0/0` to the Internet Gateway.
- [x] Tags follow existing `Project`, `Environment`, and `Name` conventions.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Run CloudFormation validation after Task 7, when all references exist.

**Dependencies:** Task 3

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** M

## Checkpoint B: Public egress foundation

- [x] Public network resources are defined.
- [x] Private route table was changed only with conditional NAT default route.
- [x] Existing private subnet and endpoint resources remain intact.

## Phase 4: NAT Instance Core

## Task 5: Add NAT IAM role and security group

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/18

**Description:** Add the minimum management role and security group for the NAT instance.

**Acceptance criteria:**

- [x] NAT IAM role exists with `AmazonSSMManagedInstanceCore`.
- [x] NAT instance profile exists.
- [x] NAT security group allows VPC-origin traffic needed for NAT.
- [x] NAT security group allows outbound internet egress.
- [x] NAT security group does not allow public SSH ingress.
- [x] NAT role does not grant RDS, DynamoDB, S3 data-plane, or Secrets Manager data access.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Manually inspect IAM policies in `infra/data-stack/template.yaml`.

**Dependencies:** Task 4

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** M

## Task 6: Add NAT EC2 instance and UserData

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/19

**Description:** Add the EC2 NAT instance resource with source/destination checks disabled, IMDSv2 enforced, and idempotent NAT setup in UserData.

**Acceptance criteria:**

- [x] `LovvNatInstance` is created only when `EnableNatInstance=true`.
- [x] Instance runs in `LovvPublicSubnetA`.
- [x] Instance uses the NAT security group and instance profile.
- [x] `SourceDestCheck` is `false`.
- [x] IMDSv2 is required.
- [x] UserData enables IPv4 forwarding and NAT masquerading.
- [x] UserData is documented enough to maintain across Amazon Linux 2023 updates.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Confirm rendered YAML has no unresolved references.

**Dependencies:** Task 5

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** M

## Task 7: Add conditional private default route

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/20

**Description:** Route private-subnet default internet traffic through the NAT instance only when NAT is enabled.

**Acceptance criteria:**

- [x] `LovvPrivateDefaultRouteToNatInstance` targets `LovvNatInstance`.
- [x] The private route has destination `0.0.0.0/0`.
- [x] The route is conditional on `EnableNatInstance=true`.
- [x] Existing S3 and DynamoDB Gateway Endpoint definitions remain unchanged.
- [x] Existing private route table associations remain unchanged.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Confirm endpoint route table IDs still reference `LovvPrivateRouteTable`.

**Dependencies:** Task 6

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** S

## Checkpoint C: NAT path complete

- [x] NAT instance resources are conditionally defined.
- [x] Private default route is opt-in.
- [x] Existing endpoint and RDS privacy behavior remains protected by tests.

## Phase 5: Publication and Documentation

## Task 8: Publish NAT identifiers

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/21

**Description:** Add SSM parameters and CloudFormation outputs for public subnet and NAT instance operations.

**Acceptance criteria:**

- [x] `/lovv/${EnvName}/network/public_subnet_a` is published when the subnet exists.
- [x] `/lovv/${EnvName}/network/nat_instance_id` is published when NAT is enabled.
- [x] `/lovv/${EnvName}/network/nat_instance_security_group` is published when NAT is enabled.
- [x] Outputs include `PublicSubnetA`, `NatInstanceId`, and `NatInstanceSecurityGroup` with correct conditions.

**Verification:**

- [x] Run `python -m pytest tests/test_data_stack_nat_instance.py`.
- [x] Inspect `Outputs` for condition consistency.

**Dependencies:** Task 7

**Files likely touched:**

- `infra/data-stack/template.yaml`

**Estimated scope:** S

## Task 9: Update dev parameter example and README

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/22

**Description:** Document how to keep NAT disabled by default and how to enable it for dev when public egress is required.

**Acceptance criteria:**

- [x] `dev.parameters.example.json` includes NAT parameters with safe defaults.
- [x] `infra/data-stack/README.md` explains NAT instance purpose, cost, and dev-only availability tradeoff.
- [x] README states existing VPC Endpoints are still preferred for covered AWS services.
- [x] README documents the CloudFormation validation command with `AWS_CLI_FILE_ENCODING=UTF-8`.

**Verification:**

- [x] Inspect README for enable/disable instructions.
- [x] Confirm parameter JSON remains valid.

**Dependencies:** Task 8

**Files likely touched:**

- `infra/data-stack/parameters/dev.parameters.example.json`
- `infra/data-stack/README.md`

**Estimated scope:** S

## Phase 6: Validation

## Task 10: Run local tests and CloudFormation validation

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/23

**Description:** Verify the implementation through unit tests and AWS template validation before deployment.

**Acceptance criteria:**

- [x] Template tests pass.
- [x] Existing test suite passes.
- [x] CloudFormation validates the updated template.
- [x] Validation results are recorded in the final implementation summary.

**Verification:**

- [x] Run `python -m pytest tests`.
- [x] Run `$env:AWS_CLI_FILE_ENCODING='UTF-8'; aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml`.

**Dependencies:** Task 9

**Files likely touched:**

- None, unless validation reveals required fixes.

**Estimated scope:** S

## Task 11: Optional dev deployment verification

**GitHub Issue:** https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/24

**Description:** Deploy and inspect the dev stack with NAT disabled and then enabled, if AWS deployment approval is available.

**Acceptance criteria:**

- [x] With `EnableNatInstance=false`, NAT EC2 and NAT route are absent in the template default path.
- [x] With `EnableNatInstance=true`, NAT EC2 and NAT route are present in the dev stack.
- [x] Public subnet route table has `0.0.0.0/0` to the Internet Gateway.
- [x] Private route table has `0.0.0.0/0` to the NAT instance.
- [x] S3 and DynamoDB Gateway Endpoint routes remain present in the template.
- [x] RDS remains private and allows MySQL only from the VPC CIDR and NAT security group.

**Verification:**

- [x] Inspect CloudFormation stack resources and security groups in AWS.
- [x] Confirm SSM Session Manager port forwarding reaches the waiting-for-connections state.
- [x] Record DB tool usage and troubleshooting in `reports/nat_instance_rds_access_report_20260618_ko.md`.

**Dependencies:** Task 10

**Files likely touched:**

- None by default.

**Estimated scope:** M

## Checkpoint D: Ready for review

- [x] All local tests pass.
- [x] CloudFormation template validation passes.
- [x] README and parameter example are updated.
- [x] Deployment verification is complete.

# 5. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| NAT instance AMI architecture does not match instance type | High | Pair ARM64 AMI parameter with `t4g.*`, or switch both to x86_64 if needed. |
| UserData NAT setup differs across Amazon Linux versions | Medium | Keep UserData minimal, test on dev deploy, and document the assumptions. |
| Private AWS service traffic unintentionally shifts from endpoints to NAT | Medium | Keep endpoint resources and tests; do not remove endpoint route table associations. |
| Public SSH exposure is accidentally added | High | Add tests forbidding `0.0.0.0/0` SSH ingress and use SSM Session Manager only. |
| Single NAT instance outage blocks private egress | Medium | Mark v0.1 dev-only and require separate production HA design. |
| Enabling NAT creates unexpected cost | Medium | Default disabled, document EC2/data-transfer cost, and keep parameter example safe. |

# 6. Parallelization Opportunities

- Task 2 tests and README wording can be drafted in parallel after Task 1.
- CloudFormation resource implementation tasks must stay mostly sequential because later resources reference earlier resources.
- Deployment verification can be handled separately after local validation passes.

# 7. Follow-up Questions

- Should a separate private-subnet Lambda smoke function be created for repeatable outbound HTTPS verification?
- Should production public egress, if needed later, prefer NAT Gateway instead of NAT instance?

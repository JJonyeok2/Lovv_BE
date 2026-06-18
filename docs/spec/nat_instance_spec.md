# Lovv Data Stack NAT Instance Spec

> Document version: v0.1
> Document status: Draft
> Created: 2026-06-18
> Baseline: `infra/data-stack/template.yaml`
> Scope: Optional NAT instance support for the Lovv Data Stack VPC.

# 1. Objective

Add an optional NAT instance path to the Lovv Data Stack VPC so private-subnet workloads can reach public internet destinations when VPC Endpoints are not enough.

The current Data Stack intentionally supports private AWS service access through VPC Endpoints for Secrets Manager, SSM, DynamoDB, and S3. This spec does not remove that design. NAT instance support is an additional controlled path for outbound internet egress from private subnets.

Success means the CloudFormation data stack can provision:

- One public subnet with internet egress.
- One managed EC2 NAT instance in the public subnet.
- A private route table default route through the NAT instance only when explicitly enabled.
- Security controls that avoid inbound SSH exposure.
- Published identifiers for operations and future SAM integration checks.

# 2. Baseline

The current Data Stack already defines:

- One VPC: `LovvDevVPC`.
- Two private subnets: `LovvPrivateSubnetA`, `LovvPrivateSubnetC`.
- One private route table: `LovvPrivateRouteTable`.
- Gateway VPC Endpoints for DynamoDB and S3.
- Interface VPC Endpoints for Secrets Manager and SSM.
- SSM parameters for VPC ID, private subnet IDs, RDS security group, and endpoint security group.

The current template comment states that private subnet AWS service access is handled without a NAT Gateway. This remains true for AWS services already covered by endpoints. NAT instance support is for non-AWS public internet destinations or AWS services without configured endpoints.

# 3. Assumptions

- The first implementation targets `dev`, not production high availability.
- CloudFormation remains the provisioning method.
- A NAT instance is requested instead of a NAT Gateway to reduce development cost.
- Private Lambda functions may eventually need outbound HTTPS to public APIs.
- VPC Endpoint resources should stay in place to keep AWS service traffic private and reduce NAT dependency.
- SSH access is not required for normal operations. Systems Manager Session Manager is the preferred access path if shell access is needed.
- The NAT instance can be single-AZ for dev. Production-grade multi-AZ NAT requires a separate approval.

# 4. Non-Goals

- Do not replace VPC Endpoints with NAT for S3, DynamoDB, SSM, or Secrets Manager.
- Do not add a NAT Gateway.
- Do not implement multi-AZ NAT failover in v0.1.
- Do not expose SSH from `0.0.0.0/0`.
- Do not change Lambda application code.
- Do not make public subnets available for RDS.
- Do not alter RDS public accessibility; RDS must remain `PubliclyAccessible: false`.

# 5. Architecture Decisions

| Decision | Requirement | Rationale |
| --- | --- | --- |
| NAT type | EC2 NAT instance | Lower dev cost than NAT Gateway. |
| Default behavior | Disabled by default | Avoid unexpected public egress and EC2 cost. |
| Public access | Public subnet plus Internet Gateway | NAT instance requires internet-reachable egress path. |
| Private routing | `0.0.0.0/0` route to NAT instance only when enabled | Keeps private subnets isolated unless explicitly configured. |
| AWS service access | Keep existing VPC Endpoints | Avoid routing AWS service traffic through NAT when private endpoints exist. |
| Admin access | SSM Session Manager, no public SSH ingress | Reduces exposed attack surface. |
| HA | Single instance for dev | Accepts dev-only availability tradeoff. |

# 5.1 First Implementation Decisions

The first implementation uses these concrete v0.1 decisions:

- `LovvPublicSubnetA` is created only when `EnableNatInstance=true`.
- The default NAT instance type is `t4g.nano`.
- The default NAT AMI is Amazon Linux 2023 ARM64 from the public SSM parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64`.
- The initial egress target is private-subnet application workloads, especially Lambda functions that need public HTTPS egress.
- Production public egress remains out of scope and requires a separate HA design.

# 6. CloudFormation Parameters

Add these parameters to `infra/data-stack/template.yaml`.

| Parameter | Type | Default | Required behavior |
| --- | --- | --- | --- |
| `EnableNatInstance` | `String` with `AllowedValues: ["true", "false"]` | `false` | Controls whether NAT instance resources and private default route are created. |
| `PublicSubnetCidr` | `String` | `10.40.1.0/24` | CIDR for the public subnet. Must not overlap with private subnets. |
| `NatInstanceType` | `String` | `t4g.nano` or `t4g.micro` | Low-cost dev instance type. Must match the selected AMI architecture. |
| `NatInstanceAmiId` | `AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>` | Latest Amazon Linux 2023 ARM64 SSM public parameter | AMI used for NAT instance. |

The implementation may use `Conditions` such as `CreateNatInstance: !Equals [!Ref EnableNatInstance, "true"]`.

# 7. Network Resources

## 7.1 Public Subnet

Add one public subnet in the same VPC:

- Resource name: `LovvPublicSubnetA`.
- VPC: `LovvDevVPC`.
- CIDR: `PublicSubnetCidr`.
- AZ: `!Select [0, !GetAZs ""]`.
- `MapPublicIpOnLaunch: true`.
- Tags:
  - `Name: lovv-${EnvName}-public-a`
  - `Project: Lovv`
  - `Environment: ${EnvName}`
  - `Tier: public`

## 7.2 Internet Gateway

Add:

- `LovvInternetGateway`.
- `LovvInternetGatewayAttachment`.

The gateway must attach to `LovvDevVPC`.

## 7.3 Public Route Table

Add:

- `LovvPublicRouteTable`.
- `LovvPublicSubnetARouteTableAssociation`.
- `LovvPublicDefaultRoute`.

`LovvPublicDefaultRoute` must route `0.0.0.0/0` to `LovvInternetGateway`.

## 7.4 Private Default Route

Add a private default route only when NAT is enabled:

- Resource name: `LovvPrivateDefaultRouteToNatInstance`.
- Route table: `LovvPrivateRouteTable`.
- Destination: `0.0.0.0/0`.
- Target: NAT instance.
- Condition: `CreateNatInstance`.

Existing Gateway Endpoint routes for S3 and DynamoDB must remain intact.

# 8. NAT Instance Resources

## 8.1 Security Group

Add `LovvNatInstanceSecurityGroup`.

Inbound:

- Allow traffic from `VpcCidr`.
- Minimum required protocols for NAT may be all protocols from the VPC CIDR in dev.
- Do not allow SSH from public internet.

Outbound:

- Allow all outbound to `0.0.0.0/0`.

Required tags:

- `Name: lovv-${EnvName}-nat-instance-sg`
- `Project: Lovv`
- `Environment: ${EnvName}`

## 8.2 IAM Role and Instance Profile

Add an IAM role and instance profile for the NAT instance.

Required managed policy:

- `AmazonSSMManagedInstanceCore`

Do not grant broad application data permissions. The NAT instance should not need RDS, DynamoDB, S3 bucket, or Secrets Manager data access.

## 8.3 EC2 Instance

Add `LovvNatInstance`.

Required properties:

- `ImageId: !Ref NatInstanceAmiId`
- `InstanceType: !Ref NatInstanceType`
- `SubnetId: !Ref LovvPublicSubnetA`
- `SecurityGroupIds: [!Ref LovvNatInstanceSecurityGroup]`
- `IamInstanceProfile: !Ref LovvNatInstanceProfile`
- `SourceDestCheck: false`
- `MetadataOptions.HttpTokens: required`
- `MetadataOptions.HttpEndpoint: enabled`

The instance must be conditionally created only when `EnableNatInstance` is `true`.

## 8.4 UserData

UserData must configure Linux IP forwarding and NAT masquerading.

Required behavior:

- Enable IPv4 forwarding at runtime.
- Persist IPv4 forwarding across reboot.
- Configure masquerade for outbound traffic through the primary network interface.
- Start or enable the required firewall/nftables/iptables service if the AMI needs it.

The UserData must be idempotent enough for instance replacement.

# 9. Published Identifiers

When NAT is enabled, publish identifiers through SSM parameters and CloudFormation outputs.

## 9.1 SSM Parameters

Add:

| Parameter name | Value |
| --- | --- |
| `/lovv/${EnvName}/network/public_subnet_a` | `!Ref LovvPublicSubnetA` |
| `/lovv/${EnvName}/network/nat_instance_id` | `!Ref LovvNatInstance` |
| `/lovv/${EnvName}/network/nat_instance_security_group` | `!Ref LovvNatInstanceSecurityGroup` |

For disabled NAT, conditional resources may omit NAT-specific parameters. The public subnet parameter may also be conditional if the public subnet is created only for NAT.

## 9.2 Outputs

Add outputs:

- `PublicSubnetA`
- `NatInstanceId`
- `NatInstanceSecurityGroup`

NAT-specific outputs must use the same condition as the NAT instance.

# 10. Security Requirements

- NAT instance must not allow public inbound SSH.
- NAT instance must require IMDSv2.
- NAT instance IAM role must be limited to SSM management.
- RDS must remain private and not publicly accessible.
- Existing VPC Endpoint security group behavior must not be weakened.
- Private subnet default internet egress must be opt-in through `EnableNatInstance`.
- The template must make the dev-only, single-AZ availability tradeoff clear in comments or README text.

# 11. Operations Requirements

## 11.1 Cost Control

- Default `EnableNatInstance` must be `false`.
- README must document that enabling NAT starts an EC2 instance and may create public internet data transfer cost.
- Instance type should be small enough for dev unless load testing proves otherwise.

## 11.2 Availability

- v0.1 accepts NAT outage if the single instance is stopped, impaired, or replaced.
- No automatic failover is required in v0.1.
- Production use requires a separate spec for multi-AZ routing and failover.

## 11.3 Access

- Normal shell access should use SSM Session Manager.
- SSH key pair is not required for v0.1.
- If SSH is later required, it must be approved as a separate change with restricted source CIDR.

# 12. Testing Strategy

Add or update tests under `tests/` that inspect `infra/data-stack/template.yaml`.

Required template tests:

- `EnableNatInstance` parameter exists and defaults to `false`.
- Public subnet, Internet Gateway, public route table, and public default route exist.
- NAT instance has `SourceDestCheck: false`.
- NAT instance requires IMDSv2.
- NAT security group does not contain public SSH ingress.
- Private default route to NAT is conditional.
- Existing VPC Endpoint resources for S3, DynamoDB, SSM, and Secrets Manager remain present.
- RDS remains `PubliclyAccessible: false`.

Required CloudFormation validation:

```powershell
$env:AWS_CLI_FILE_ENCODING='UTF-8'; aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml
```

Required unit test command:

```powershell
python -m pytest tests
```

# 13. Deployment Verification

When implementation is ready, verify in this order:

1. Validate the CloudFormation template.
2. Deploy with `EnableNatInstance=false` and confirm no NAT instance is created.
3. Deploy with `EnableNatInstance=true` in dev.
4. Confirm the public subnet route table has `0.0.0.0/0` to the Internet Gateway.
5. Confirm the private route table has `0.0.0.0/0` to the NAT instance.
6. Confirm existing S3 and DynamoDB Gateway Endpoint routes still exist.
7. From a private-subnet test workload, confirm HTTPS egress to a public endpoint works.
8. Confirm RDS remains inaccessible from public internet.

# 14. Boundaries

Always:

- Keep NAT disabled by default.
- Preserve existing VPC Endpoints.
- Keep RDS private.
- Add tests for template-level security expectations.
- Document cost and availability tradeoffs.

Ask first:

- Enabling NAT by default.
- Replacing VPC Endpoints with NAT.
- Adding SSH ingress.
- Adding production multi-AZ NAT failover.
- Moving RDS or Lambda resources to public subnets.

Never:

- Commit secrets, key pairs, or private SSH keys.
- Open SSH to `0.0.0.0/0`.
- Set RDS `PubliclyAccessible` to `true`.
- Remove existing endpoint resources without explicit approval.
- Add broad data-plane IAM permissions to the NAT instance role.

# 15. Success Criteria

- The spec is saved as `docs/SPEC/nat_instance_spec.md`.
- A later implementation can be planned directly from this spec.
- NAT instance support is clearly optional and dev-oriented.
- The current endpoint-first private AWS access design remains intact.
- Security and cost controls are explicit and testable.

# 16. Deferred Questions

- Should future ECS or EC2 workloads reuse this NAT path or define a separate egress design?
- Should production use NAT Gateway instead of NAT instance if public egress becomes required?
- Should a dedicated private-subnet egress smoke test workload be added later?

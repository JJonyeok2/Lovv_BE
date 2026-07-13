# AWS 비용 최적화 인프라 조회 보고서

작성일: 2026-06-30
범위: `03_lovv_BE` repo의 Terraform/CloudFormation 구성과 AWS Cost Explorer 비용 상위 항목 매핑

## 결론

현재 이 repo에는 Terraform 구성 또는 Terraform state가 없다.

- `.tf`, `.tfvars`, `.tfstate`, `.terraform`, `terragrunt.hcl` 파일 없음
- `terraform state list` 결과: `No state file was found`
- 현재 비용을 발생시키는 주요 리소스는 Terraform이 아니라 CloudFormation stack `lovv-dev-data-stack`이 소유
- 따라서 비용 절감 작업은 Terraform 수정이 아니라 `infra/data-stack/template.yaml` 및 stack parameter update로 진행해야 한다

이 repo에서 바로 줄일 수 있는 1순위 비용은 VPC Interface Endpoint 시간 과금이었다.
2026-06-30에 비용 절감 change set을 실행해 Interface Endpoint를 단일 subnet으로 축소하고 optional NAT instance를 비활성화했다.

## 변경 전 비용 요약

조회 기준:

- 기간: 2026-06-01부터 2026-06-30 전까지
- Metric: `UnblendedCost`
- 제외: Credit, Refund
- Cost Explorer 응답: `Estimated=true`

| 항목 | 6월 누적 비용 | 비고 |
|---|---:|---|
| Amazon Virtual Private Cloud | 21.029194 USD | 최대 비용 항목 |
| Amazon Relational Database Service | 8.919267 USD | `lovv-dev-mysql`, `db.t4g.micro` |
| Amazon Elastic Compute Cloud - Compute | 1.146600 USD | NAT instance `t4g.nano` |

VPC 세부 비용:

| Usage type | 6월 누적 비용 | 원인 |
|---|---:|---|
| `USE1-VpcEndpoint-Hours` | 17.730000 USD | Interface Endpoint 시간 과금 |
| `USE1-PublicIPv4:IdleAddress` | 1.922303 USD | 월중 idle public IPv4 잔여 비용 |
| `USE1-PublicIPv4:InUseAddress` | 1.376083 USD | 변경 전 NAT instance public IPv4 |
| `USE1-VpcEndpoint-Bytes` | 0.000808 USD | 무시 가능한 수준 |

최근 full-day 기준 run-rate:

| 항목 | 일 비용 | 30일 환산 |
|---|---:|---:|
| Interface Endpoint 시간 과금 | 0.960000 USD/day | 28.800000 USD/month |
| NAT instance public IPv4 | 0.120000 USD/day | 3.600000 USD/month |

## 실제 AWS 리소스 매핑

CloudFormation stack:

- Stack name: `lovv-dev-data-stack`
- Status: `UPDATE_COMPLETE`
- Current parameter: `EnableNatInstance=false`

Interface Endpoint:

| Logical ID | Physical ID | Service | 현재 Subnet 수 |
|---|---|---|---:|
| `SecretsManagerVpcEndpoint` | `vpce-00bb7058ce18d984c` | Secrets Manager | 1 |
| `SSMVpcEndpoint` | `vpce-0acf51d81b0dfe1ec` | SSM | 1 |

NAT/EC2:

| Logical ID | Physical ID | Type | State |
|---|---|---|---|
| `LovvNatInstance` | `i-0c6dad9690abd0101` | `t4g.nano` | terminated |

RDS:

| DB identifier | Class | Storage | Public | Multi-AZ |
|---|---|---:|---|---|
| `lovv-dev-mysql` | `db.t4g.micro` | 20 GiB gp2 | false | false |

## repo에서 수정 가능한 절감 후보

### 1. Interface Endpoint를 단일 AZ로 축소

변경 전에는 `SecretsManagerVpcEndpoint`와 `SSMVpcEndpoint`가 각각 private subnet A/C 2개에 붙어 있었다.
비용은 endpoint service 수 * subnet 수에 비례해서 발생한다.

수정 위치:

- `infra/data-stack/template.yaml`
- `SecretsManagerVpcEndpoint.SubnetIds`
- `SSMVpcEndpoint.SubnetIds`

적용 효과:

- 변경 결과 4 endpoint-AZ 단위에서 2 endpoint-AZ 단위로 축소
- 최근 run-rate 기준 약 14.400000 USD/month 절감

리스크:

- PrivateSubnetC에 배치된 Lambda가 Secrets Manager/SSM을 호출할 때 cross-AZ 경로가 될 수 있다
- dev 환경에서는 비용 절감 우선이면 타당
- prod/HA 기준이면 2 AZ endpoint 유지가 더 안전

중요 관찰:

- `tests/test_data_stack_vpc_endpoints.py`는 이미 단일 AZ endpoint를 기대한다
- 변경 전 focused test 결과는 2개 실패, 12개 통과
- 실패 사유는 Secrets Manager/SSM endpoint가 `LovvPrivateSubnetA`, `LovvPrivateSubnetC` 2개 subnet을 모두 포함하기 때문
- 즉 단일 AZ 축소는 새 정책이 아니라 기존 테스트 의도와 템플릿/배포를 맞추는 작업이다

### 2. 중복 `SSMVpcEndpoint` logical id 정리

변경 전 `infra/data-stack/template.yaml`에는 `SSMVpcEndpoint`가 두 번 정의되어 있었다.

- 첫 번째 정의: private subnet A/C 2개 사용
- 두 번째 정의: private subnet A 1개 사용

이 상태는 CloudFormation 템플릿 품질상 위험했다. 실제 배포 상태는 2개 subnet이었으므로 당시 배포에는 첫 번째 정의가 반영된 상태로 보였다.

적용 방향:

- 중복 logical id를 제거
- 남기는 `SSMVpcEndpoint`는 단일 AZ 정책이면 `LovvPrivateSubnetA`만 사용
- Secrets Manager endpoint도 같은 단일 AZ 정책으로 맞춤

적용 효과:

- 1번 절감안의 전제 작업
- 테스트 실패 2건도 함께 해소 가능

### 3. NAT instance 운영 모드를 stopped 기본으로 전환

변경 전 stack parameter는 `EnableNatInstance=true`이고 NAT instance `i-0c6dad9690abd0101`이 running 상태였다.
README에는 NAT instance가 SSM port forwarding을 통한 private RDS 접속 때만 필요하고, 작업 후 중지하라고 되어 있다.

적용 내용:

- dev stack update에서 `EnableNatInstance=false` 적용
- CloudFormation 조건부 리소스인 NAT instance, public subnet, Internet Gateway, public route, NAT route, NAT SSM parameters 제거
- `i-0c6dad9690abd0101` 상태는 `terminated`

적용 효과:

- Public IPv4 run-rate 기준 약 3.600000 USD/month 절감
- EC2 compute도 running 시간만큼 추가 절감

리스크:

- SSM port forwarding으로 private RDS에 접속해야 하는 동안에는 NAT instance가 필요
- 장기적으로는 필요할 때만 start/stop하는 운영 스크립트나 문서 강화가 적절

### 4. RDS는 당장 IaC로 줄일 여지가 작음

현재 RDS는 이미 최소 계열에 가깝다.

- `db.t4g.micro`
- 20 GiB gp2
- MultiAZ false
- PubliclyAccessible false

가능한 절감은 RDS 자체를 중지하거나 삭제하는 쪽인데, 앱 기능과 데이터 보존 영향이 커서 단순 템플릿 수정 대상으로 보기 어렵다.
dev 환경에서 장시간 미사용이면 stop schedule을 별도 운영 정책으로 검토할 수 있다.

## 실행한 작업

1. `infra/data-stack/template.yaml`의 중복 `SSMVpcEndpoint` 정의를 제거했다.
2. Secrets Manager/SSM Interface Endpoint를 `LovvPrivateSubnetA` 단일 subnet으로 맞췄다.
3. 중복 logical id 회귀 방지 테스트를 추가했다.
4. focused test를 통과시켰다: `15 passed`
5. 전체 테스트를 통과시켰다: `303 passed`
6. AWS CloudFormation `validate-template`를 통과시켰다.
7. 현재 live stack template 기준으로 change set `cost-opt-vpc-endpoints-disable-nat-20260630-1200`을 생성했다.
8. change set 변경 목록에서 데이터 리소스 변경이 없고 endpoint 수정/NAT 제거만 있음을 확인했다.
9. change set을 실행했고 stack은 `UPDATE_COMPLETE`가 됐다.

## 이후 권장 작업

1. Cost Explorer 반영 지연을 고려해 24-48시간 뒤 `USE1-VpcEndpoint-Hours`와 `USE1-PublicIPv4:InUseAddress` 일 비용이 줄었는지 재확인한다.
2. private RDS에 SSM port forwarding이 필요한 작업이 생기면 `EnableNatInstance=true`로 재배포하기 전에 작업 시간을 제한한다.
3. repo template 전체와 live stack template 사이의 누적 차이는 별도 change set으로 분리 검토한다.

## 이번 조회에서 확인한 명령 결과

- Terraform 상태: state 없음
- Terraform 구성 파일: 없음
- CloudFormation stack: `lovv-dev-data-stack`, `UPDATE_COMPLETE`
- Initial focused infra tests: 3 failed, 12 passed
- Initial 실패 테스트:
  - `DataStackVpcEndpointsTest.test_data_stack_resource_logical_ids_are_unique`
  - `DataStackVpcEndpointsTest.test_secretsmanager_endpoint_single_az`
  - `DataStackVpcEndpointsTest.test_ssm_endpoint_single_az`
- Final focused infra tests: 15 passed
- Final full tests: 303 passed

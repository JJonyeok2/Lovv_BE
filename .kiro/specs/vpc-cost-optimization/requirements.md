# Requirements Document

## Introduction

`lovv-dev-data-stack` CloudFormation 스택(us-east-1, 계정 925273580929)의 VPC 관련 불필요 비용을 제거하는 기능이다. AWS CLI 조사를 통해 확인된 사실에 기반하여, SSM Interface VPC Endpoint와 Secrets Manager Interface VPC Endpoint를 각각 단일 AZ(1 ENI)로 축소하여 월 약 $14.6를 절감한다. 변경 후에도 Lambda 함수의 정상 동작과 RDS 보안 격리를 유지해야 한다.

## Glossary

- **Data_Stack**: `infra/data-stack/template.yaml`로 정의된 CloudFormation 스택. VPC, 서브넷, RDS, VPC 엔드포인트, DynamoDB 테이블 등 stateful 리소스를 관리한다.
- **SSM_Endpoint**: AWS Systems Manager Parameter Store용 Interface VPC Endpoint (`vpce-0acf51d81b0dfe1ec`). 현재 Lambda 런타임에서 사용되지 않으나, 향후 활용 가능성을 고려하여 단일 AZ로 축소 유지한다.
- **SecretsManager_Endpoint**: AWS Secrets Manager용 Interface VPC Endpoint (`vpce-00bb7058ce18d984c`). Lambda가 RDS 비밀번호를 조회할 때 사용한다.
- **PrivateSubnetA**: 첫 번째 AZ의 private subnet (`subnet-0e04f80cfb58e0f35`). 모든 VPC Lambda가 배치된 서브넷이다.
- **PrivateSubnetC**: 두 번째 AZ의 private subnet. RDS DB subnet group 구성에만 사용되며 Lambda는 배치되지 않는다.
- **VPC_Lambda**: VPC 내부에 배치된 Lambda 함수(Auth, Admin, SavedPlans, Preference). Secrets Manager를 통해 RDS 비밀번호를 조회한다.
- **DB_Subnet_Group**: RDS가 요구하는 최소 2개 AZ 서브넷 그룹 (`LovvDBSubnetGroup`). PrivateSubnetA + PrivateSubnetC로 구성된다.
- **NAT_Instance**: dev 환경에서 SSM port forwarding을 통한 RDS 접속용 t4g.nano EC2 인스턴스이다.
- **Gateway_Endpoint**: S3, DynamoDB용 Gateway VPC Endpoint. 무료이며 변경 대상이 아니다.
- **CloudFormation_Template**: `infra/data-stack/template.yaml` 파일이다.

## Requirements

### Requirement 1: SSM VPC Endpoint 단일 AZ 축소

**User Story:** 인프라 운영자로서, Lambda 런타임에서 사용되지 않지만 향후 활용 가능성을 고려하여 SSM VPC Endpoint를 완전 삭제하지 않고 단일 AZ로 축소하여, 월 ~$7.3의 비용을 절감하고 싶다.

#### Acceptance Criteria

1. WHEN Data_Stack 배포가 완료되면, THE SSM_Endpoint SHALL SubnetIds에 PrivateSubnetA 1개만 포함하고, PrivateDnsEnabled를 true로 유지한다.
2. WHEN Data_Stack 배포가 완료되면, THE SSM_Endpoint SHALL PrivateSubnetC에 ENI를 배치하지 않는다.
3. WHEN SSM_Endpoint가 단일 AZ로 축소된 후 VPC_Lambda가 호출되면, THE VPC_Lambda SHALL SecretsManager_Endpoint를 통해 `secretsmanager:GetSecretValue` API를 호출하여 30초 이내에 비어 있지 않은 RDS 비밀번호 값을 반환받는다.
4. WHEN SSM_Endpoint가 단일 AZ로 축소된 후 Data_Stack을 재배포하면, THE Data_Stack SHALL CloudFormation이 SSM Parameter Store 참조(`AWS::SSM::Parameter::Value` 타입 파라미터)를 성공적으로 resolve하여 스택 상태가 `UPDATE_COMPLETE`에 도달한다.

### Requirement 2: Secrets Manager VPC Endpoint 단일 AZ 축소

**User Story:** 인프라 운영자로서, Secrets Manager VPC Endpoint를 Lambda가 실제 배치된 단일 AZ로 축소하여, 사용되지 않는 2번째 AZ ENI 비용 월 ~$7.3을 절감하고 싶다.

#### Acceptance Criteria

1. WHEN Data_Stack 배포가 완료되면, THE SecretsManager_Endpoint SHALL SubnetIds에 PrivateSubnetA 1개만 포함하고, PrivateDnsEnabled를 true로 유지한다.
2. WHEN Data_Stack 배포가 완료되면, THE SecretsManager_Endpoint SHALL PrivateSubnetC에 ENI를 배치하지 않는다.
3. WHEN SecretsManager_Endpoint가 단일 AZ로 축소된 후, THE VPC_Lambda SHALL `secretsmanager:GetSecretValue` API 호출 시 RDS 비밀번호가 포함된 유효한 응답을 수신하고, 해당 비밀번호로 RDS 연결을 성공한다.
4. IF SecretsManager_Endpoint가 단일 AZ로 축소된 상태에서 `secretsmanager:GetSecretValue` API 호출이 실패하면, THEN THE VPC_Lambda SHALL 10초 이내에 타임아웃 처리하고 에러를 로그에 기록한다.

### Requirement 3: RDS 보안 격리 유지

**User Story:** 인프라 운영자로서, VPC Endpoint 변경 후에도 RDS가 private subnet에서만 접근 가능하도록 유지하여, 데이터베이스 보안 수준을 보장하고 싶다.

#### Acceptance Criteria

1. THE DB_Subnet_Group SHALL PrivateSubnetA와 PrivateSubnetC 두 서브넷을 모두 포함한다.
2. THE Data_Stack SHALL RDS 인스턴스의 `PubliclyAccessible` 속성을 `false`로 유지한다.
3. THE Data_Stack SHALL RDS 보안 그룹(`LovvRDSSecurityGroup`)의 인바운드 규칙을 VPC Endpoint 변경 전과 동일하게 유지한다: TCP 3306 포트에 대해 DevMysqlIngressCidr 파라미터 CIDR 허용 1개 규칙과, NAT 인스턴스 활성화 시 NAT 인스턴스 보안 그룹으로부터의 TCP 3306 허용 조건부 규칙만 존재해야 한다.
4. THE Data_Stack SHALL PrivateSubnetA와 PrivateSubnetC가 연결된 private route table에 Internet Gateway로의 직접 라우트(0.0.0.0/0 → IGW)를 포함하지 않는다.

### Requirement 4: Lambda VPC 구성 일관성 유지

**User Story:** 개발자로서, VPC Endpoint 변경 후에도 Lambda 함수가 올바른 서브넷에서 정상 동작하여, 서비스 중단 없이 비용을 절감하고 싶다.

#### Acceptance Criteria

1. THE VPC_Lambda SHALL VpcConfig의 SubnetIds에 PrivateSubnetA를 포함한다.
2. WHEN VPC_Lambda가 호출되면, THE VPC_Lambda SHALL PrivateSubnetA의 SecretsManager_Endpoint를 통해 `secretsmanager:GetSecretValue` API 호출을 수행하여 RDS 비밀번호를 조회한다.
3. IF SecretsManager_Endpoint에 접근할 수 없으면, THEN THE VPC_Lambda SHALL 에러를 로그에 기록하고 HTTP 500 응답을 반환하며, 응답 본문에 Secrets Manager 연결 실패를 나타내는 에러 메시지를 포함한다.
4. THE VPC_Lambda SHALL 모든 VPC Lambda 함수(Auth, Admin, SavedPlans, Preference)에 동일한 VpcConfig SubnetIds 설정을 적용한다.

### Requirement 5: Gateway Endpoint 무변경 보장

**User Story:** 인프라 운영자로서, 비용 최적화 작업이 무료인 Gateway Endpoint(S3, DynamoDB)에 영향을 주지 않도록 보장하여, 기존 서비스 연동을 보호하고 싶다.

#### Acceptance Criteria

1. THE Data_Stack SHALL DynamoDB Gateway Endpoint(`DynamoDBGatewayEndpoint`) 리소스의 VpcId, ServiceName, VpcEndpointType, RouteTableIds 속성을 비용 최적화 변경 전과 동일하게 유지한다.
2. THE Data_Stack SHALL S3 Gateway Endpoint(`S3GatewayEndpoint`) 리소스의 VpcId, ServiceName, VpcEndpointType, RouteTableIds 속성을 비용 최적화 변경 전과 동일하게 유지한다.
3. WHEN 비용 최적화 변경이 적용된 CloudFormation changeset을 생성하면, THE Data_Stack SHALL `DynamoDBGatewayEndpoint`와 `S3GatewayEndpoint` 리소스에 대해 변경(Modify), 삭제(Remove), 교체(Replace) action을 포함하지 않는다.
4. WHEN Data_Stack 배포가 완료되면, THE VPC_Lambda SHALL DynamoDB Gateway Endpoint를 경유하여 DynamoDB API 호출을 정상 수행한다.

### Requirement 6: CloudFormation 템플릿 정합성

**User Story:** 인프라 운영자로서, 변경된 CloudFormation 템플릿이 유효하고 배포 가능한 상태를 유지하여, 안전한 인프라 관리를 보장하고 싶다.

#### Acceptance Criteria

1. WHEN CloudFormation_Template가 수정되면, THE CloudFormation_Template SHALL `aws cloudformation validate-template` 검증을 통과한다.
2. WHEN SSM_Endpoint가 단일 AZ로 축소되면, THE CloudFormation_Template SHALL SSM_Endpoint 리소스의 SubnetIds에 PrivateSubnetA만 포함하고, 기존 SecurityGroupIds와 VpcEndpointType 속성을 변경 없이 유지한다.
3. WHEN CloudFormation_Template가 수정되면, THE CloudFormation_Template SHALL 기존 무조건부 SSM Parameter 리소스(RDSHostParameter, VpcIdParameter, PrivateSubnetAParameter, PrivateSubnetCParameter, RDSSecurityGroupParameter, EndpointSecurityGroupParameter, RDSDatabaseNameParameter, RDSSecretArnParameter, UserEventLogsTableParameter, AgentRunsTableParameter, FestivalVerifyCacheTableParameter, AsyncJobsTableParameter, ApiLogsTableParameter, ContentDocumentsTableParameter, VisitorStatisticsTableParameter, AuthSessionsTableParameter, ImageBucketParameter, ImageCdnDomainParameter, ImageCdnBaseUrlParameter)의 논리적 리소스 ID와 Name/Value 속성을 변경 없이 유지한다.
4. WHEN CloudFormation_Template가 수정되면, THE CloudFormation_Template SHALL YAML 구문 오류 없이 파싱되며 모든 `!Ref` 및 `!GetAtt` 대상이 동일 템플릿 내에 정의된 리소스 또는 파라미터를 가리킨다.

### Requirement 7: NAT 인스턴스(EC2) 비용 최적화

**User Story:** 인프라 운영자로서, 개발자 DB 접속 시에만 사용되는 NAT 인스턴스가 24시간 running 상태로 불필요한 비용을 발생시키지 않도록 하여, 월 ~$3의 추가 절감을 달성하고 싶다.

#### 배경 (AWS 조사 결과)

- NAT 인스턴스 (`i-0c6dad9690abd0101`, t4g.nano)는 6월 18일부터 계속 running 상태이다.
- CPU 사용률: 평균 0.3% (SSM agent heartbeat 수준, 거의 유휴)
- NetworkIn: 일 ~6MB, NetworkOut: 일 ~3.4MB (실질적 Lambda NAT 트래픽 없음)
- 용도: 개발자가 SSM port forwarding으로 private RDS에 접속할 때만 필요
- Lambda는 VPC Endpoint를 통해 AWS 서비스에 접근하므로 NAT를 경유하지 않음

#### Acceptance Criteria

1. THE CloudFormation_Template SHALL NAT 인스턴스의 기본 상태(`EnableNatInstance`)를 `false`로 유지한다.
2. THE Data_Stack README SHALL 개발자에게 NAT 인스턴스를 DB 작업 시에만 `EnableNatInstance=true`로 재배포해 생성하고, 작업 완료 후 `EnableNatInstance=false`로 재배포해 제거하도록 안내하는 운영 가이드를 포함한다.
3. WHEN NAT 인스턴스가 생성되지 않은 상태(`EnableNatInstance=false`)일 때, THE VPC_Lambda SHALL SecretsManager_Endpoint와 Gateway_Endpoint를 통해 모든 AWS 서비스 호출을 정상 수행한다 (NAT 인스턴스에 의존하지 않는다).
4. THE Data_Stack README SHALL `EnableNatInstance=false` 상태에서는 NAT EC2와 `/lovv/dev/network/nat_instance_id` SSM 파라미터가 존재하지 않아 기존 인스턴스를 `start-instances`로 재시작할 수 없음을 문서화한다.

### Requirement 8: 테스트 업데이트

**User Story:** 개발자로서, 인프라 변경 사항을 반영한 테스트를 유지하여, 향후 회귀를 방지하고 싶다.

#### Acceptance Criteria

1. WHEN SSM_Endpoint가 단일 AZ로 축소되면, THE 테스트_코드 SHALL SSM VPC Endpoint(`SSMVpcEndpoint`)의 SubnetIds에 PrivateSubnetA만 포함되어 있음을 검증한다.
2. WHEN SecretsManager_Endpoint가 단일 AZ로 축소되면, THE 테스트_코드 SHALL SecretsManager Endpoint의 SubnetIds 속성에 PrivateSubnetA만 포함되어 있음을 검증한다.
3. THE 테스트_코드 SHALL VPC_Lambda의 VpcConfig SubnetIds에 SecretsManager_Endpoint와 동일한 서브넷(PrivateSubnetA)이 포함됨을 검증한다.
4. WHEN 기존 테스트(`test_data_stack_nat_instance.py`)에서 SSMVpcEndpoint의 SubnetIds 수를 검증하는 코드가 있으면, THE 테스트_코드 SHALL 해당 검증을 SubnetIds가 1개(PrivateSubnetA만)임을 검증하도록 변경한다.

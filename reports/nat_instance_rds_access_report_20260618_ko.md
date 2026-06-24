# NAT 인스턴스 경유 RDS 접속 보고서

> 보고서 버전: v0.1
> 작성일: 2026-06-18
> 범위: Lovv dev Data Stack의 optional NAT instance 배포와 SSM port forwarding 기반 RDS 접속 방법
> 관련 Spec: `docs/SPEC/nat_instance_spec.md`
> 관련 Plan: `docs/PLAN/nat_instance_plan.md`
> 관련 이슈: `https://github.com/Joraemon-s-Secret-Gadgets/Lovv_BE/issues/13`

# 1. 요약

Lovv dev Data Stack에 optional NAT instance를 배포하고, 외부 개발 PC에서 private RDS에 접근할 수 있는 SSM port forwarding 경로를 확인했다.

이 방식은 RDS를 public internet에 직접 노출하지 않는다. 개발자는 로컬 PC에서 AWS Systems Manager Session Manager 터널을 열고, DB tool은 `127.0.0.1`로 접속한다.

접속 흐름:

```text
DB tool on local PC
  -> 127.0.0.1:3306
  -> AWS SSM Session Manager tunnel
  -> NAT instance
  -> private RDS endpoint:3306
```

# 2. 배포 상태

CloudFormation stack:

| 항목 | 값 |
| --- | --- |
| Stack name | `lovv-dev-data-stack` |
| Region | `us-east-1` |
| Account | `925273580929` |
| Stack status | `UPDATE_COMPLETE` |
| NAT enabled | `EnableNatInstance=true` |

확인된 리소스:

| 항목 | 값 |
| --- | --- |
| NAT instance ID | `i-0c6dad9690abd0101` |
| NAT instance private IP | `10.40.1.27` |
| NAT instance public IP | `54.157.96.154` |
| NAT security group | `sg-01e6b5db8bf59e88e` |
| RDS security group | `sg-01374ec78cec3d1fb` |
| RDS host | `lovv-dev-mysql.ci18eue8oz99.us-east-1.rds.amazonaws.com` |
| RDS database | `lovvdev` |
| RDS username | `lovvadmin` |
| SSM managed status | `Online` |

# 3. 보안 모델

RDS는 계속 private resource로 유지한다.

유지되는 조건:

- RDS `PubliclyAccessible`은 `false`이다.
- RDS DB subnet group은 `LovvPrivateSubnetA`, `LovvPrivateSubnetC`만 사용한다.
- RDS에는 public subnet을 추가하지 않는다.
- RDS security group은 NAT instance security group에서 오는 MySQL `3306`만 추가 허용한다.
- NAT instance에는 public SSH ingress를 열지 않는다.
- 운영 접속은 SSM Session Manager를 사용한다.

CloudFormation 리소스:

```text
LovvRDSIngressFromNatInstance
```

역할:

- `LovvNatInstanceSecurityGroup` -> `LovvRDSSecurityGroup`
- Protocol: `tcp`
- Port: `3306`
- Condition: `CreateNatInstance`

# 4. 사전 조건

로컬 PC에 다음이 필요하다.

1. AWS CLI v2
2. AWS Session Manager Plugin
3. dev 계정/region에 접근 가능한 AWS credential
4. MySQL client 또는 DB tool

AWS CLI 확인:

```powershell
aws --version
aws sts get-caller-identity
aws configure get region
```

Session Manager Plugin 확인:

```powershell
session-manager-plugin --version
```

플러그인이 없으면 Windows에서 공식 설치 파일을 설치한다.

```text
https://s3.amazonaws.com/session-manager-downloads/plugin/latest/windows/SessionManagerPluginSetup.exe
```

설치 후 PowerShell을 새로 열어야 PATH가 반영될 수 있다.

# 5. SSM 터널 열기

PowerShell에서 다음을 실행한다.

```powershell
$natInstanceId = aws ssm get-parameter --name /lovv/dev/network/nat_instance_id --query Parameter.Value --output text
$rdsHost = aws ssm get-parameter --name /lovv/dev/rds/host --query Parameter.Value --output text

aws ssm start-session --target $natInstanceId --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=$rdsHost,portNumber=3306,localPortNumber=3306"
```

정상 출력 예:

```text
Starting session with SessionId: ...
Port 3306 opened for sessionId ...
Waiting for connections...
```

이 PowerShell 창은 터널 세션이다. DB tool을 사용하는 동안 닫지 않는다.

로컬 `3306` 포트가 이미 사용 중이면 local port만 바꾼다.

```powershell
aws ssm start-session --target $natInstanceId --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=$rdsHost,portNumber=3306,localPortNumber=13306"
```

이 경우 DB tool의 port도 `13306`으로 설정한다.

# 6. DB tool 등록값

DBeaver, DataGrip, MySQL Workbench 등에서 다음 값으로 등록한다.

| 항목 | 값 |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `3306` 또는 터널에서 지정한 local port |
| Database | `lovvdev` |
| User | `lovvadmin` |
| Password | RDS managed secret의 `password` |
| SSH tunnel | 사용하지 않음 |
| SSL | 기본값으로 시작. 필요 시 `Prefer` 또는 DB tool 기본 MySQL 설정 사용 |

주의:

- DB tool의 SSH tunnel 기능을 쓰는 방식이 아니다.
- 외부 PowerShell에서 SSM tunnel을 먼저 열고, DB tool은 localhost로 붙는다.

# 7. RDS 비밀번호 조회

RDS master password는 CloudFormation 파라미터가 아니라 RDS managed secret에 있다.

조회:

```powershell
$secretArn = aws ssm get-parameter --name /lovv/dev/rds/secret_arn --query Parameter.Value --output text
aws secretsmanager get-secret-value --secret-id $secretArn --query SecretString --output text
```

출력 JSON의 `password` 값을 DB tool password에 입력한다.

비밀번호를 문서, Git, issue, PR 본문에 붙여넣지 않는다.

# 8. CLI 접속 테스트

터널이 열린 상태에서 다른 PowerShell 창에서 테스트한다.

```powershell
mysql -h 127.0.0.1 -P 3306 -u lovvadmin -p lovvdev
```

local port를 `13306`으로 열었다면:

```powershell
mysql -h 127.0.0.1 -P 13306 -u lovvadmin -p lovvdev
```

# 9. 자주 발생하는 문제

## 9.1 `ParameterNotFound`

예:

```text
An error occurred (ParameterNotFound) when calling the GetParameter operation
```

원인:

- NAT stack update가 아직 적용되지 않았다.
- `EnableNatInstance=true`로 배포되지 않았다.
- 잘못된 AWS region/profile을 사용 중이다.

확인:

```powershell
aws configure get region
aws cloudformation describe-stacks --stack-name lovv-dev-data-stack --query "Stacks[0].StackStatus" --output text
aws ssm get-parameter --name /lovv/dev/network/nat_instance_id --query Parameter.Value --output text
```

## 9.2 `SessionManagerPlugin is not found`

원인:

- 로컬 PC에 AWS Session Manager Plugin이 없다.

해결:

- Windows용 plugin installer를 관리자 권한으로 설치한다.
- PowerShell을 새로 열고 `session-manager-plugin --version`을 확인한다.

## 9.3 `--target: expected one argument`

원인:

- `$natInstanceId` 변수가 비어 있다.
- 보통 앞 단계의 `get-parameter`가 실패했다.

확인:

```powershell
$natInstanceId
aws ssm get-parameter --name /lovv/dev/network/nat_instance_id --query Parameter.Value --output text
```

## 9.4 DB tool connection refused

원인 후보:

- SSM tunnel PowerShell 창을 닫았다.
- DB tool port와 `localPortNumber`가 다르다.
- 로컬 3306을 다른 프로세스가 사용 중이다.

해결:

- SSM tunnel을 다시 열고 `Waiting for connections...` 상태를 유지한다.
- `localPortNumber=13306`으로 바꿔서 시도한다.
- DB tool port도 동일하게 `13306`으로 바꾼다.

# 10. 운영 주의사항

- 이 NAT instance는 dev용 단일 AZ 구성이다.
- production 접속 경로로 사용하지 않는다.
- RDS public access를 켜지 않는다.
- MySQL `3306`을 `0.0.0.0/0`에 열지 않는다.
- 작업이 끝나면 SSM tunnel 세션을 종료한다.
- NAT instance를 계속 켜두면 EC2 비용과 public data transfer 비용이 발생한다.

# 11. 검증 기록

저장소 검증:

```text
python -m pytest tests/test_data_stack_nat_instance.py -> 6 passed
python -m pytest tests -> 136 passed
aws cloudformation validate-template --template-body file://infra/data-stack/template.yaml -> passed
```

AWS live 확인:

```text
CloudFormation stack status -> UPDATE_COMPLETE
SSM parameter /lovv/dev/network/nat_instance_id -> i-0c6dad9690abd0101
SSM managed instance status -> Online
RDS SG ingress -> NAT SG sg-01e6b5db8bf59e88e allowed on tcp/3306
```

# VPC 엔드포인트 비용 최적화 및 보안 검토 보고서

> 보고서 버전: v1.0
> 작성일: 2026-06-17
> 범위: `infra/data-stack/template.yaml`, `template.yaml`, `src/shared/mysql_data.py` 기반 실제 코드 검토

---

## 1. 요약

CloudFormation 템플릿과 Lambda 소스 코드를 함께 검토한 결과, 두 가지 주요 사항을 확인하였다.

첫째, Interface VPC 엔드포인트(Secrets Manager, SSM)가 2개 AZ에 배치되어 불필요한 비용이 발생하고 있으며, SSM 엔드포인트는 Lambda 런타임에서 실제로 사용되지 않는 것으로 확인되었다.

둘째, RDS 비밀번호는 Secrets Manager를 통해 적절하게 관리되고 있으나, JWT 서명키와 OAuth 클라이언트 시크릿이 Lambda 환경변수에 직접 주입되어 보안 개선 여지가 있다.

---

## 2. VPC 엔드포인트 비용 분석

### 2.1 현재 구성

| 엔드포인트 | 타입 | AZ 수 | 월 비용 |
|-----------|------|--------|--------|
| `SecretsManagerVpcEndpoint` | Interface | 2 | ~$14.6 |
| `SSMVpcEndpoint` | Interface | 2 | ~$14.6 |
| `DynamoDBGatewayEndpoint` | Gateway | — | 무료 |
| `S3GatewayEndpoint` | Gateway | — | 무료 |
| **합계** | | | **~$29.2/월** |

### 2.2 SSM 엔드포인트 불필요 확인

`infra/data-stack/template.yaml`에서 SSM Parameter Store는 RDS 호스트, VPC ID, 서브넷 ID 등의 설정값을 배포 시 스택 간 참조 용도로 저장한다. Lambda 함수의 IAM 정책 어디에도 `ssm:GetParameter` 권한이 없으며, 소스 코드(`src/` 전체)에서도 SSM API 호출이 확인되지 않았다.

Lambda 런타임 흐름:
```
배포 시 (개발자 머신, VPC 외부)
  aws ssm get-parameter → 값 획득 → sam deploy --parameter-overrides로 전달

Lambda 런타임 (VPC 내부)
  os.environ["RDS_HOST"] 읽기       ← 환경변수 (SSM 불필요)
  secretsmanager.get_secret_value()  ← Secrets Manager만 호출
```

결론: `SSMVpcEndpoint`는 Lambda 런타임에서 사용되지 않으므로 제거 가능하다.

### 2.3 절감 옵션

**옵션 A — 보수적: 양쪽 엔드포인트 1 AZ 축소**

- `SecretsManagerVpcEndpoint`, `SSMVpcEndpoint` 모두 `LovvPrivateSubnetA` 하나만 유지
- VPC Lambda 3개(`AuthFunction`, `PreferenceFunction`, `SavedPlansFunction`) VpcConfig SubnetIds도 동일하게 축소
- `LovvDBSubnetGroup`은 RDS 요구사항(최소 2개 AZ 서브넷)에 따라 변경 불가
- 절감: **~$14.6/월**

**옵션 B — 적극적: SSM 엔드포인트 제거 + Secrets Manager 1 AZ 축소** (권장)

- `SSMVpcEndpoint` 리소스 전체 삭제
- `SecretsManagerVpcEndpoint` SubnetIds를 `LovvPrivateSubnetA` 하나로 축소
- VPC Lambda 3개 SubnetIds 동일하게 축소
- `LovvDBSubnetGroup` 변경 없음
- 절감: **~$21.9/월**

| | 현재 | 옵션 A | 옵션 B |
|--|------|--------|--------|
| Interface 엔드포인트 비용 | ~$29.2/월 | ~$14.6/월 | ~$7.3/월 |
| 절감 | — | $14.6 | **$21.9** |

dev 환경 특성상 고가용성 요구가 없으므로 옵션 B를 권장한다.

---

## 3. 시크릿 관리 보안 검토

### 3.1 RDS 비밀번호 — 적절함

RDS 비밀번호는 `ManageMasterUserPassword: true`를 통해 RDS Managed Master User Secret으로 자동 생성·로테이션된다. Lambda는 `secretsmanager:GetSecretValue`를 통해 런타임에 직접 Secrets Manager에서 조회하며, SSM에는 비밀번호가 저장되지 않는다.

```python
# mysql_data.py: 올바른 방식
response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
```

SSM Parameter Store에는 비민감 값(호스트, DB명, ARN 등)만 저장되어 있어 역할이 적절히 분리되어 있다.

### 3.2 Lambda 환경변수 시크릿 — 개선 필요

다음 3개 시크릿이 Lambda 환경변수로 직접 주입되고 있다. 환경변수는 `lambda:GetFunctionConfiguration` 권한을 가진 IAM 주체, CloudFormation 콘솔, 일부 Lambda 레이어 또는 로그를 통해 노출될 수 있다.

| 파라미터 | 위험도 | 비고 |
|---------|--------|------|
| `AUTH_TOKEN_SIGNING_SECRET` | 높음 | JWT 서명키. 노출 시 임의 토큰 생성 가능 |
| `GOOGLE_CLIENT_SECRET` | 중간 | Google OAuth 클라이언트 시크릿 |
| `KAKAO_CLIENT_SECRET` | 중간 | Kakao OAuth 클라이언트 시크릿 |

### 3.3 Secrets Manager 호출 캐싱 미적용

`MySqlClient` 생성자가 매 Lambda 호출마다 `secretsmanager.get_secret_value()`를 호출한다. Lambda 실행 컨텍스트가 재사용되더라도 인스턴스를 매번 생성하면 불필요한 API 호출이 발생한다.

- 영향: Secrets Manager API 비용 추가, 레이턴시 증가

---

## 4. 개선 권장사항

### 우선순위 높음 — `AUTH_TOKEN_SIGNING_SECRET` Secrets Manager 이전

```yaml
# template.yaml
AuthTokenSigningSecretArn:
  Type: String
  Description: Secrets Manager ARN for JWT signing secret.
```

```python
# auth/app.py: 런타임 조회로 전환
secret_arn = os.environ["AUTH_TOKEN_SIGNING_SECRET_ARN"]
signing_secret = boto3.client("secretsmanager") \
    .get_secret_value(SecretId=secret_arn)["SecretString"]
```

### 우선순위 중간 — Secrets Manager 호출 캐싱

```python
# mysql_data.py: 모듈 레벨 캐시
_secret_cache: dict[str, str] = {}

def _load_secret(secret_arn: str) -> str:
    if secret_arn not in _secret_cache:
        response = boto3.client("secretsmanager") \
            .get_secret_value(SecretId=secret_arn)
        _secret_cache[secret_arn] = response["SecretString"]
    return _secret_cache[secret_arn]
```

### 우선순위 낮음 — OAuth 시크릿 Secrets Manager 이전

`GOOGLE_CLIENT_SECRET`, `KAKAO_CLIENT_SECRET`도 동일하게 Secrets Manager ARN 참조 방식으로 전환 가능하다. dev 환경에서는 현행 유지도 허용 가능한 수준이다.

---

## 5. 변경 시 주의사항

- `LovvDBSubnetGroup`은 RDS 생성 요구사항(2개 AZ 서브넷 필수)에 따라 반드시 SubnetA + SubnetC를 유지해야 한다.
- Lambda VpcConfig 서브넷 변경 시 해당 AZ의 Secrets Manager 엔드포인트가 존재해야 한다.
- SSMVpcEndpoint 제거는 배포 프로세스에 영향을 주지 않는다 (배포는 VPC 외부에서 실행).

---

## 6. 결론

| 항목 | 현황 | 권장 조치 |
|------|------|----------|
| RDS 비밀번호 관리 | 적절 (Secrets Manager) | 유지 |
| SSM 역할 | 적절 (비민감 설정값만 저장) | 유지 |
| SSM VPC 엔드포인트 | 불필요 (런타임 미사용) | 제거 |
| Secrets Manager VPC 엔드포인트 | 2 AZ 과잉 | 1 AZ 축소 |
| JWT 서명키 관리 | 환경변수 노출 위험 | Secrets Manager 이전 권장 |
| Secrets Manager 캐싱 | 미적용 | 캐싱 적용 권장 |

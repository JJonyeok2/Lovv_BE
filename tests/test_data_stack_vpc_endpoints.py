import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_STACK_TEMPLATE = PROJECT_ROOT / "infra" / "data-stack" / "template.yaml"
SAM_TEMPLATE = PROJECT_ROOT / "template.yaml"


class DataStackVpcEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = DATA_STACK_TEMPLATE.read_text(encoding="utf-8")
        cls.sam_template = SAM_TEMPLATE.read_text(encoding="utf-8")

    def _block(self, resource_name: str, next_resource_name: str) -> str:
        start = self.template.index(f"  {resource_name}:")
        end = self.template.index(f"  {next_resource_name}:")
        return self.template[start:end]

    def _sam_block(self, resource_name: str, next_resource_name: str) -> str:
        start = self.sam_template.index(f"  {resource_name}:")
        end = self.sam_template.index(f"  {next_resource_name}:")
        return self.sam_template[start:end]

    def _subnet_refs(self, resource_name: str, next_resource_name: str) -> list[str]:
        block = self._block(resource_name, next_resource_name)
        subnet_section_start = block.index("SubnetIds:")
        subnet_section_end = block.index("SecurityGroupIds:", subnet_section_start)
        subnet_section = block[subnet_section_start:subnet_section_end]

        return [
            line.strip()
            for line in subnet_section.splitlines()
            if line.strip().startswith("- !Ref")
        ]

    def test_data_stack_resource_logical_ids_are_unique(self):
        resources_start = self.template.index("Resources:")
        outputs_start = self.template.index("Outputs:")
        resources_section = self.template[resources_start:outputs_start]

        logical_ids = [
            line.strip()[:-1]
            for line in resources_section.splitlines()
            if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":")
        ]
        duplicates = sorted(
            logical_id
            for logical_id in set(logical_ids)
            if logical_ids.count(logical_id) > 1
        )

        self.assertEqual([], duplicates)

    # --- Task 5.1: SSM Endpoint Tests ---

    def test_ssm_endpoint_single_az(self):
        """SSMVpcEndpoint SubnetIds에 LovvPrivateSubnetA만 포함되어 있음을 검증한다."""
        subnet_refs = self._subnet_refs("SSMVpcEndpoint", "DynamoDBGatewayEndpoint")

        self.assertEqual(["- !Ref LovvPrivateSubnetA"], subnet_refs)

    def test_ssm_endpoint_private_dns_enabled(self):
        """SSMVpcEndpoint의 PrivateDnsEnabled가 true임을 검증한다."""
        ssm_block = self._block("SSMVpcEndpoint", "DynamoDBGatewayEndpoint")

        self.assertIn("PrivateDnsEnabled: true", ssm_block)

    def test_ssm_endpoint_security_group(self):
        """SSMVpcEndpoint SecurityGroupIds에 LovvEndpointSecurityGroup이 포함됨을 검증한다."""
        ssm_block = self._block("SSMVpcEndpoint", "DynamoDBGatewayEndpoint")

        self.assertIn("SecurityGroupIds:", ssm_block)
        self.assertIn("- !Ref LovvEndpointSecurityGroup", ssm_block)


    # --- Task 5.2: SecretsManager Endpoint & Gateway Endpoint Tests ---

    def test_secretsmanager_endpoint_single_az(self):
        """SecretsManagerVpcEndpoint SubnetIds에 PrivateSubnetA만 포함되어 있음을 검증한다."""
        subnet_refs = self._subnet_refs("SecretsManagerVpcEndpoint", "SSMVpcEndpoint")

        self.assertEqual(["- !Ref LovvPrivateSubnetA"], subnet_refs)

    def test_gateway_endpoints_unchanged(self):
        """DynamoDB/S3 Gateway Endpoint의 VpcId, ServiceName, VpcEndpointType, RouteTableIds 속성 무변경을 검증한다."""
        # DynamoDB Gateway Endpoint 검증
        dynamo_block = self._block("DynamoDBGatewayEndpoint", "S3GatewayEndpoint")

        self.assertIn("VpcId: !Ref LovvDevVPC", dynamo_block)
        self.assertIn("ServiceName: !Sub com.amazonaws.${AWS::Region}.dynamodb", dynamo_block)
        self.assertIn("VpcEndpointType: Gateway", dynamo_block)
        self.assertIn("RouteTableIds:", dynamo_block)
        self.assertIn("- !Ref LovvPrivateRouteTable", dynamo_block)

        # S3 Gateway Endpoint 검증
        s3_block = self._block("S3GatewayEndpoint", "LovvNatInstance")

        self.assertIn("VpcId: !Ref LovvDevVPC", s3_block)
        self.assertIn("ServiceName: !Sub com.amazonaws.${AWS::Region}.s3", s3_block)
        self.assertIn("VpcEndpointType: Gateway", s3_block)
        self.assertIn("RouteTableIds:", s3_block)
        self.assertIn("- !Ref LovvPrivateRouteTable", s3_block)


    # --- Task 5.3: RDS 보안 격리 및 라우팅 테스트 ---

    def test_rds_security_isolation(self):
        """RDS PubliclyAccessible=false, DBSubnetGroup에 양쪽 서브넷 포함을 검증한다."""
        # DBSubnetGroup 블록: LovvDBSubnetGroup → LovvRDSInstance
        db_subnet_block = self._block("LovvDBSubnetGroup", "LovvRDSInstance")
        self.assertIn("- !Ref LovvPrivateSubnetA", db_subnet_block)
        self.assertIn("- !Ref LovvPrivateSubnetC", db_subnet_block)

        # RDS 인스턴스 블록: LovvRDSInstance → UserEventLogsTable
        rds_block = self._block("LovvRDSInstance", "UserEventLogsTable")
        self.assertIn("PubliclyAccessible: false", rds_block)

    def test_rds_security_group_rules(self):
        """RDS SG 인바운드 규칙이 DevMysqlIngressCidr과 조건부 NAT 인스턴스 규칙만 존재함을 검증한다."""
        # LovvRDSSecurityGroup 블록: LovvRDSSecurityGroup → LovvEndpointSecurityGroup
        rds_sg_block = self._block("LovvRDSSecurityGroup", "LovvEndpointSecurityGroup")

        # DevMysqlIngressCidr 인바운드 규칙 존재 확인
        self.assertIn("CidrIp: !Ref DevMysqlIngressCidr", rds_sg_block)
        self.assertIn("FromPort: 3306", rds_sg_block)
        self.assertIn("ToPort: 3306", rds_sg_block)

        # NAT 인스턴스 조건부 인바운드 규칙 확인 (별도 리소스 LovvRDSIngressFromNatInstance)
        self.assertIn("LovvRDSIngressFromNatInstance:", self.template)
        nat_ingress_idx = self.template.index("LovvRDSIngressFromNatInstance:")
        # 조건부(Condition: CreateNatInstance) 확인
        nat_ingress_section = self.template[nat_ingress_idx:nat_ingress_idx + 500]
        self.assertIn("Condition: CreateNatInstance", nat_ingress_section)
        self.assertIn("SourceSecurityGroupId: !Ref LovvNatInstanceSecurityGroup", nat_ingress_section)

        # RDS SG 자체의 SecurityGroupIngress에 규칙이 1개만 있는지 확인 (DevMysqlIngressCidr만)
        ingress_start = rds_sg_block.index("SecurityGroupIngress:")
        egress_start = rds_sg_block.index("SecurityGroupEgress:")
        ingress_section = rds_sg_block[ingress_start:egress_start]
        ingress_rules = [
            line.strip()
            for line in ingress_section.splitlines()
            if line.strip().startswith("- IpProtocol:")
        ]
        self.assertEqual(
            len(ingress_rules), 1,
            f"Expected 1 inline ingress rule (DevMysqlIngressCidr), got {len(ingress_rules)}: {ingress_rules}",
        )

    def test_private_route_no_igw(self):
        """Private route table에 IGW 직접 라우트(GatewayId: !Ref LovvInternetGateway)가 없음을 검증한다."""
        # LovvPrivateRouteTable 블록: LovvPrivateRouteTable → LovvPrivateSubnetARouteTableAssociation
        private_rt_block = self._block("LovvPrivateRouteTable", "LovvPrivateSubnetARouteTableAssociation")
        self.assertNotIn("GatewayId: !Ref LovvInternetGateway", private_rt_block)

        # LovvPrivateSubnetARouteTableAssociation 블록
        private_assoc_a_block = self._block(
            "LovvPrivateSubnetARouteTableAssociation", "LovvPrivateSubnetCRouteTableAssociation"
        )
        self.assertNotIn("GatewayId: !Ref LovvInternetGateway", private_assoc_a_block)

        # LovvPrivateSubnetCRouteTableAssociation 블록
        private_assoc_c_block = self._block(
            "LovvPrivateSubnetCRouteTableAssociation", "LovvRDSSecurityGroup"
        )
        self.assertNotIn("GatewayId: !Ref LovvInternetGateway", private_assoc_c_block)

    def test_lambda_subnet_alignment(self):
        private_subnet_parameter = self._block("PrivateSubnetAParameter", "PrivateSubnetCParameter")
        self.assertIn("Name: !Sub /lovv/${EnvName}/network/private_subnet_a", private_subnet_parameter)
        self.assertIn("Value: !Ref LovvPrivateSubnetA", private_subnet_parameter)

        for resource_name, next_resource_name in (
            ("AuthFunction", "AuthAuthorizerFunction"),
            ("AdminFunction", "PreferenceFunction"),
            ("PreferenceFunction", "AgentCoreFunction"),
            ("SavedPlansFunction", "SmallCitiesFunction"),
        ):
            lambda_block = self._sam_block(resource_name, next_resource_name)
            self.assertIn("VpcConfig:", lambda_block)
            self.assertIn("SecurityGroupIds:", lambda_block)
            self.assertIn("- !Ref AppFunctionSecurityGroup", lambda_block)
            self.assertIn("SubnetIds:", lambda_block)
            self.assertIn("- !Ref PrivateSubnetA", lambda_block)
            self.assertNotIn("- !Ref PrivateSubnetC", lambda_block)


if __name__ == "__main__":
    unittest.main()

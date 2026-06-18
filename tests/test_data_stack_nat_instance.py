import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_STACK_TEMPLATE = PROJECT_ROOT / "infra" / "data-stack" / "template.yaml"


class DataStackNatInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = DATA_STACK_TEMPLATE.read_text(encoding="utf-8")

    def _block(self, resource_name: str, next_resource_name: str) -> str:
        start = self.template.index(f"  {resource_name}:")
        end = self.template.index(f"  {next_resource_name}:")
        return self.template[start:end]

    def test_nat_instance_is_opt_in_by_default(self):
        enable_nat = self._block("EnableNatInstance", "VpcCidr")

        self.assertIn('Default: "false"', enable_nat)
        self.assertIn("AllowedValues:", enable_nat)
        self.assertIn('- "true"', enable_nat)
        self.assertIn('- "false"', enable_nat)
        self.assertIn("CreateNatInstance: !Equals [!Ref EnableNatInstance, \"true\"]", self.template)

    def test_public_subnet_and_internet_gateway_are_defined_for_nat(self):
        public_subnet = self._block("LovvPublicSubnetA", "LovvInternetGateway")
        public_route = self._block("LovvPublicDefaultRoute", "LovvPublicSubnetARouteTableAssociation")

        self.assertIn("CidrBlock: !Ref PublicSubnetCidr", public_subnet)
        self.assertIn("MapPublicIpOnLaunch: true", public_subnet)
        self.assertIn("Value: public", public_subnet)
        self.assertIn("LovvInternetGateway:", self.template)
        self.assertIn("LovvInternetGatewayAttachment:", self.template)
        self.assertIn("LovvPublicRouteTable:", self.template)
        self.assertIn("DestinationCidrBlock: 0.0.0.0/0", public_route)
        self.assertIn("GatewayId: !Ref LovvInternetGateway", public_route)

    def test_nat_instance_has_required_security_and_routing_controls(self):
        nat_sg = self._block("LovvNatInstanceSecurityGroup", "LovvNatInstanceRole")
        nat_instance = self._block("LovvNatInstance", "LovvPrivateDefaultRouteToNatInstance")
        private_route = self._block("LovvPrivateDefaultRouteToNatInstance", "LovvDBSubnetGroup")

        self.assertIn("CidrIp: !Ref VpcCidr", nat_sg)
        self.assertNotIn("FromPort: 22", nat_sg)
        self.assertNotIn("CidrIp: 0.0.0.0/0\n          Description: SSH", nat_sg)
        self.assertIn("SourceDestCheck: false", nat_instance)
        self.assertIn("HttpTokens: required", nat_instance)
        self.assertIn("Condition: CreateNatInstance", nat_instance)
        self.assertIn("Condition: CreateNatInstance", private_route)
        self.assertIn("DestinationCidrBlock: 0.0.0.0/0", private_route)
        self.assertIn("InstanceId: !Ref LovvNatInstance", private_route)

    def test_existing_private_endpoint_and_rds_controls_remain(self):
        rds = self._block("LovvRDSInstance", "UserEventLogsTable")

        for expected in (
            "SecretsManagerVpcEndpoint:",
            "SSMVpcEndpoint:",
            "DynamoDBGatewayEndpoint:",
            "S3GatewayEndpoint:",
            "RouteTableIds:",
            "- !Ref LovvPrivateRouteTable",
        ):
            self.assertIn(expected, self.template)

        self.assertIn("PubliclyAccessible: false", rds)

    def test_nat_identifiers_are_published_conditionally(self):
        for expected in (
            "/lovv/${EnvName}/network/public_subnet_a",
            "/lovv/${EnvName}/network/nat_instance_id",
            "/lovv/${EnvName}/network/nat_instance_security_group",
            "PublicSubnetA:",
            "NatInstanceId:",
            "NatInstanceSecurityGroup:",
        ):
            self.assertIn(expected, self.template)


if __name__ == "__main__":
    unittest.main()

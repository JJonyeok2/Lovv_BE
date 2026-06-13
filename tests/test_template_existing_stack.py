import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "template.yaml"
RDS_SCHEMA = PROJECT_ROOT / "infra" / "data-stack" / "rds" / "schema.sql"
PREFERENCES_MIGRATION = (
    PROJECT_ROOT
    / "infra"
    / "data-stack"
    / "rds"
    / "migrations"
    / "20260612_allow_both_country_track.sql"
)


class ExistingDataStackTemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_api_stack_uses_existing_data_stack_parameters(self):
        for expected in (
            "RdsHost:",
            "RdsSecretArn:",
            "RdsDatabaseName:",
            "AuthSessionsTableName:",
            "PrivateSubnetA:",
            "PrivateSubnetC:",
            "VpcId:",
            "DB_ACCESS_MODE: mysql",
            "RDS_HOST: !Ref RdsHost",
            "RDS_SECRET_ARN: !Ref RdsSecretArn",
            "RDS_DATABASE_NAME: !Ref RdsDatabaseName",
            "AUTH_SESSIONS_TABLE_NAME: !Ref AuthSessionsTableName",
            "VpcConfig:",
        ):
            self.assertIn(expected, self.template)

    def test_api_stack_does_not_create_duplicate_auth_session_table(self):
        self.assertNotIn("AuthSessionsTable:\n    Type: AWS::DynamoDB::Table", self.template)
        self.assertIn("table/${AuthSessionsTableName}", self.template)
        self.assertIn("table/${AuthSessionsTableName}/index/GSI1RefreshTokenHashLookup", self.template)

    def test_saved_plans_routes_use_lovv_token_authorizer(self):
        saved_plans_index = self.template.index("SavedPlansFunction:")
        saved_plans_block = self.template[saved_plans_index : self.template.index("SmallCitiesFunction:")]
        self.assertEqual(saved_plans_block.count("Authorizer: LovvTokenAuthorizer"), 6)
        for path in (
            "Path: /api/v1/me/itineraries",
            "Path: /api/v1/me/itineraries/{itineraryId}",
            "Path: /api/v1/me/itineraries/{itineraryId}/reactions/like",
        ):
            path_index = saved_plans_block.index(path)
            self.assertIn("Authorizer: LovvTokenAuthorizer", saved_plans_block[path_index : path_index + 220])

    def test_preferences_routes_use_lovv_token_authorizer(self):
        preferences_index = self.template.index("PreferenceFunction:")
        preferences_block = self.template[preferences_index : self.template.index("AgentCoreFunction:")]
        self.assertEqual(preferences_block.count("Authorizer: LovvTokenAuthorizer"), 2)
        for path in (
            "Path: /api/v1/me/preferences",
        ):
            path_index = preferences_block.index(path)
            self.assertIn("Authorizer: LovvTokenAuthorizer", preferences_block[path_index : path_index + 220])

    def test_lovv_token_authorizer_allows_http_api_invoke(self):
        self.assertIn("AuthAuthorizerInvokePermission:", self.template)
        self.assertIn("Type: AWS::Lambda::Permission", self.template)
        self.assertIn("FunctionName: !Ref AuthAuthorizerFunction", self.template)
        self.assertIn("${LovvHttpApi}/authorizers/*", self.template)

    def test_template_accepts_comma_separated_cors_origins(self):
        self.assertIn("CORS_ALLOW_ORIGINS: !Ref AllowedCorsOrigin", self.template)
        self.assertIn('AllowOrigins: !Split [",", !Ref AllowedCorsOrigin]', self.template)
        self.assertIn("Default: http://localhost:5173,http://127.0.0.1:5173", self.template)
        self.assertIn("https://d3nuef0zacpyj.cloudfront.net", self.template)

    def test_auth_function_exposes_cognito_bridge_route_without_cognito_infra_cutover(self):
        self.assertIn("AuthCognitoSession:", self.template)
        self.assertIn("Path: /api/v1/auth/cognito/session", self.template)

    def test_cognito_bridge_route_uses_cognito_jwt_authorizer(self):
        self.assertIn("LovvCognitoJwtAuthorizer:", self.template)
        self.assertIn("JwtConfiguration:", self.template)
        self.assertIn('IdentitySource: "$request.header.Authorization"', self.template)
        self.assertIn("Authorizer: LovvCognitoJwtAuthorizer", self.template)

    def test_template_defines_optional_cognito_poc_resources(self):
        for expected in (
            "EnableCognitoPoC:",
            "CreateCognitoPoC:",
            "LovvCognitoUserPool:",
            "Type: AWS::Cognito::UserPool",
            "LovvGoogleIdentityProvider:",
            "ProviderType: Google",
            "LovvKakaoIdentityProvider:",
            "ProviderType: OIDC",
            "oidc_issuer: !Ref CognitoKakaoOidcIssuer",
            "LovvCognitoUserPoolClient:",
            "AllowedOAuthFlowsUserPoolClient: true",
            "AllowedOAuthFlows:",
            "- code",
            "LovvCognitoUserPoolDomain:",
            "Type: AWS::Cognito::UserPoolDomain",
        ):
            self.assertIn(expected, self.template)

        for secret_parameter in ("CognitoGoogleClientSecret:", "CognitoKakaoClientSecret:"):
            index = self.template.index(secret_parameter)
            self.assertIn("NoEcho: true", self.template[index : index + 160])

    def test_cognito_callback_defaults_match_frontend_bridge_route(self):
        self.assertIn(
            "Default: http://localhost:5173/auth/callback/cognito,http://127.0.0.1:5173/auth/callback/cognito,https://d3nuef0zacpyj.cloudfront.net/auth/callback/cognito",
            self.template,
        )
        self.assertIn("CallbackURLs: !Ref CognitoCallbackUrls", self.template)
        self.assertIn("LogoutURLs: !Ref CognitoLogoutUrls", self.template)

    def test_small_cities_function_timeout_matches_live_marker_smoke_requirement(self):
        index = self.template.index("SmallCitiesFunction:")
        self.assertIn("Timeout: 30", self.template[index : index + 260])


class ExistingDataStackSchemaTest(unittest.TestCase):
    def test_user_preferences_country_track_schema_is_not_changed_by_api_policy(self):
        schema = RDS_SCHEMA.read_text(encoding="utf-8")
        migration = PREFERENCES_MIGRATION.read_text(encoding="utf-8")

        self.assertIn("chk_user_preferences_country", schema)
        self.assertIn("chk_user_preferences_country", migration)


if __name__ == "__main__":
    unittest.main()

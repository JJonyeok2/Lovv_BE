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
ITINERARY_SNAPSHOT_MIGRATION = (
    PROJECT_ROOT
    / "schema"
    / "aurora_mysql"
    / "005_add_itinerary_snapshot_json.sql"
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
            "USER_ROLE_ASSIGNMENTS_TABLE_NAME: !Ref UserRoleAssignmentsTableName",
            "USER_REGION_ASSIGNMENTS_TABLE_NAME: !Ref UserRegionAssignmentsTableName",
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
        self.assertEqual(saved_plans_block.count("Authorizer: LovvTokenAuthorizer"), 8)
        for path in (
            "Path: /api/v1/me/itineraries",
            "Path: /api/v1/me/itineraries/{itineraryId}",
            "Path: /api/v1/me/itineraries/{itineraryId}/reactions/like",
            "Path: /api/v1/me/itineraries/{itineraryId}/share",
            "Path: /api/v1/me/itineraries/{itineraryId}/clone",
        ):
            path_index = saved_plans_block.index(path)
            self.assertIn("Authorizer: LovvTokenAuthorizer", saved_plans_block[path_index : path_index + 220])

    def test_saved_plans_public_routes_are_exposed_without_token_authorizer(self):
        saved_plans_index = self.template.index("SavedPlansFunction:")
        saved_plans_block = self.template[saved_plans_index : self.template.index("SmallCitiesFunction:")]
        for path in (
            "Path: /api/v1/itineraries/public",
            "Path: /api/v1/itineraries/{itineraryId}",
        ):
            path_index = saved_plans_block.index(path)
            next_event_index = saved_plans_block.find("\n        ", path_index + 1)
            route_block = saved_plans_block[path_index:next_event_index]
            self.assertNotIn("Authorizer: LovvTokenAuthorizer", route_block)

    def test_preferences_routes_use_lovv_token_authorizer(self):
        preferences_index = self.template.index("PreferenceFunction:")
        preferences_block = self.template[preferences_index : self.template.index("AgentCoreFunction:")]
        self.assertEqual(preferences_block.count("Authorizer: LovvTokenAuthorizer"), 2)
        for path in (
            "Path: /api/v1/me/preferences",
        ):
            path_index = preferences_block.index(path)
            self.assertIn("Authorizer: LovvTokenAuthorizer", preferences_block[path_index : path_index + 220])

    def test_admin_routes_use_lovv_token_authorizer(self):
        admin_index = self.template.index("AdminFunction:")
        admin_block = self.template[admin_index : self.template.index("PreferenceFunction:")]
        # Every admin HttpApi route must be guarded by the token authorizer. Assert
        # one authorizer per route declaration instead of a hard-coded count, so the
        # check stays correct as routes are added/removed but still fails if any
        # route is left unguarded.
        route_count = admin_block.count("Path: /api/v1/admin/")
        authorizer_count = admin_block.count("Authorizer: LovvTokenAuthorizer")
        self.assertGreater(route_count, 0)
        self.assertEqual(authorizer_count, route_count)
        for path in (
            "Path: /api/v1/admin/users",
            "Path: /api/v1/admin/users/{userId}",
            "Path: /api/v1/admin/data-proposals",
            "Path: /api/v1/admin/data-proposals/{proposalId}",
            "Path: /api/v1/admin/data-proposals/{proposalId}/review",
            "Path: /api/v1/admin/data-proposals/{proposalId}/approve",
            "Path: /api/v1/admin/data-proposals/{proposalId}/reject",
            "Path: /api/v1/admin/data-proposals/{proposalId}/history",
            "Path: /api/v1/admin/monthly-destinations",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/schedule",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/publish",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/hide",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/expire",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/reject",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/events",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/metrics",
            "Path: /api/v1/admin/metrics/destinations",
            "Path: /api/v1/admin/monthly-destinations/{destinationId}/publish-jobs",
            "Path: /api/v1/admin/publish-jobs/{jobId}/start",
            "Path: /api/v1/admin/publish-jobs/{jobId}/succeed",
            "Path: /api/v1/admin/publish-jobs/{jobId}/fail",
            "Path: /api/v1/admin/publish-jobs/{jobId}/retry",
            "Path: /api/v1/admin/publish-jobs/{jobId}/cancel",
            "Path: /api/v1/admin/notices",
            "Path: /api/v1/admin/notices/{noticeId}/publish",
            "Path: /api/v1/admin/notices/{noticeId}/archive",
            "Path: /api/v1/admin/recommendation-policies",
            "Path: /api/v1/admin/recommendation-policies/{policyId}/activate",
            "Path: /api/v1/admin/recommendation-policies/{policyId}/archive",
            "Path: /api/v1/admin/audit-logs",
        ):
            path_index = admin_block.index(path)
            self.assertIn("Authorizer: LovvTokenAuthorizer", admin_block[path_index : path_index + 220])

    def test_admin_function_uses_existing_data_stack_tables(self):
        admin_index = self.template.index("AdminFunction:")
        admin_block = self.template[admin_index : self.template.index("PreferenceFunction:")]
        for expected in (
            "DB_ACCESS_MODE: mysql",
            "RDS_HOST: !Ref RdsHost",
            "RDS_SECRET_ARN: !Ref RdsSecretArn",
            "RDS_DATABASE_NAME: !Ref RdsDatabaseName",
            "ADMIN_DATA_PROPOSALS_TABLE_NAME: !Ref AdminDataProposalsTableName",
            "ADMIN_DATA_PROPOSAL_HISTORY_TABLE_NAME: !Ref AdminDataProposalHistoryTableName",
            "MONTHLY_CURATED_DESTINATIONS_TABLE_NAME: monthly_curated_destinations",
            "ADMIN_PUBLISH_JOBS_TABLE_NAME: admin_publish_jobs",
            "DESTINATION_METRICS_DAILY_TABLE_NAME: destination_metrics_daily",
            "ADMIN_NOTICES_TABLE_NAME: admin_notices",
            "ADMIN_RECOMMENDATION_POLICIES_TABLE_NAME: admin_recommendation_policies",
            "ADMIN_AUDIT_LOGS_TABLE_NAME: admin_audit_logs",
        ):
            self.assertIn(expected, admin_block)

    def test_admin_authz_cache_table_has_ttl(self):
        self.assertIn("AdminAuthzCacheTable:", self.template)
        self.assertIn("Type: AWS::DynamoDB::Table", self.template)
        table_index = self.template.index("AdminAuthzCacheTable:")
        block = self.template[table_index : table_index + 600]
        self.assertIn("AttributeName: userId", block)
        self.assertIn("KeyType: HASH", block)
        self.assertIn("AttributeName: expiresAt", block)
        self.assertIn("Enabled: true", block)

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
        self.assertIn("https://lovv-admin-web.vercel.app", self.template)
        self.assertIn("https://lovv-admin-web-skn26.vercel.app", self.template)

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

    def test_small_cities_image_cdn_base_url_is_environment_parameter(self):
        self.assertIn("ImageCdnBaseUrl:", self.template)
        index = self.template.index("SmallCitiesFunction:")
        block = self.template[index : index + 760]

        self.assertIn("IMAGE_CDN_BASE_URL: !Ref ImageCdnBaseUrl", block)
        self.assertNotIn("IMAGE_CDN_BASE_URL: '{{resolve:ssm:/lovv/dev/cloudfront/image_base_url}}'", block)

    def test_small_cities_uses_tour_korea_dynamodb_v2_by_default(self):
        self.assertIn("MapCityDynamoTableName:", self.template)
        self.assertIn("Default: TourKoreaDomainDataV2", self.template)
        index = self.template.index("SmallCitiesFunction:")
        block = self.template[index : index + 1100]

        self.assertIn("MAP_CITY_SOURCE: dynamodb", block)
        self.assertIn("MAP_CITY_DYNAMODB_TABLE: !Ref MapCityDynamoTableName", block)

    def test_small_cities_can_use_optional_metadata_audit_s3_key(self):
        self.assertIn("MapCityMetadataAuditKey:", self.template)
        index = self.template.index("SmallCitiesFunction:")
        block = self.template[index : index + 1800]

        self.assertIn("MAP_CITY_METADATA_AUDIT_BUCKET: !Ref MapCityS3Bucket", block)
        self.assertIn("MAP_CITY_METADATA_AUDIT_KEY: !Ref MapCityMetadataAuditKey", block)
        self.assertIn("!Sub arn:${AWS::Partition}:s3:::${MapCityS3Bucket}/metadata_audit/*", block)

    def test_small_cities_can_query_dynamodb_city_domain_index(self):
        index = self.template.index("SmallCitiesFunction:")
        block = self.template[index : index + 1800]

        self.assertIn("dynamodb:Query", block)
        self.assertIn("dynamodb:Scan", block)
        self.assertIn("!Sub arn:${AWS::Partition}:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${MapCityDynamoTableName}", block)
        self.assertIn("!Sub arn:${AWS::Partition}:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${MapCityDynamoTableName}/index/CityDomainIndex", block)

    def test_agentcore_runtime_arn_is_environment_parameter(self):
        self.assertIn("AgentCoreRuntimeArn:", self.template)
        index = self.template.index("AgentCoreFunction:")
        block = self.template[index : index + 1300]

        self.assertIn("BEDROCK_AGENT_ARN: !Ref AgentCoreRuntimeArn", block)
        self.assertIn('Resource: !Sub "${AgentCoreRuntimeArn}*"', block)

    def test_agentcore_openrouteservice_key_is_server_side_noecho_parameter(self):
        self.assertIn("OpenRouteServiceApiKey:", self.template)
        parameter_index = self.template.index("OpenRouteServiceApiKey:")
        self.assertIn("NoEcho: true", self.template[parameter_index : parameter_index + 180])
        self.assertIn("OpenRouteServiceApiKeySsmName:", self.template)

        agentcore_index = self.template.index("AgentCoreFunction:")
        agentcore_block = self.template[agentcore_index : agentcore_index + 1300]

        self.assertIn("OPENROUTESERVICE_API_KEY: !Ref OpenRouteServiceApiKey", agentcore_block)
        self.assertIn("OPENROUTESERVICE_API_KEY_SSM_NAME: !Ref OpenRouteServiceApiKeySsmName", agentcore_block)
        self.assertIn("OPENROUTESERVICE_PROFILE: !Ref OpenRouteServiceProfile", agentcore_block)
        self.assertIn("OPENROUTESERVICE_TIMEOUT_SECONDS: !Ref OpenRouteServiceTimeoutSeconds", agentcore_block)
        self.assertIn("ssm:GetParameter", agentcore_block)
        self.assertIn("parameter${OpenRouteServiceApiKeySsmName}", agentcore_block)


class ExistingDataStackSchemaTest(unittest.TestCase):
    def test_user_preferences_country_track_schema_is_not_changed_by_api_policy(self):
        schema = RDS_SCHEMA.read_text(encoding="utf-8")
        migration = PREFERENCES_MIGRATION.read_text(encoding="utf-8")

        self.assertIn("chk_user_preferences_country", schema)
        self.assertIn("chk_user_preferences_country", migration)

    def test_itinerary_snapshot_migration_is_idempotent_for_fresh_local_db(self):
        migration = ITINERARY_SNAPSHOT_MIGRATION.read_text(encoding="utf-8")

        self.assertIn("INFORMATION_SCHEMA.COLUMNS", migration)
        self.assertIn("COLUMN_NAME = 'itinerary_json'", migration)
        self.assertIn("@itinerary_json_exists = 0", migration)


if __name__ == "__main__":
    unittest.main()

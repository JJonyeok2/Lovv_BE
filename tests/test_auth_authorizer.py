import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.authorizer import lambda_handler
from shared.auth import create_access_token


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "unit-test-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_ISSUER": "lovv-test-auth",
    "AUTH_AUDIENCE": "lovv-test-api",
}


class AuthAuthorizerTest(unittest.TestCase):
    def test_authorizer_allows_valid_bearer_token_with_product_context(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token_result = create_access_token(
                user_id="user-123",
                session_id="session-456",
                provider="google",
                display_name="Lovv User",
            )
            response = lambda_handler({"headers": {"Authorization": f"Bearer {token_result.token}"}}, None)

            self.assertEqual(response["isAuthorized"], True)
            self.assertEqual(response["context"]["sub"], "user-123")
            self.assertEqual(response["context"]["userId"], "user-123")
            self.assertEqual(response["context"]["sid"], "session-456")
            self.assertEqual(response["context"]["sessionId"], "session-456")
            self.assertEqual(response["context"]["provider"], "google")
            self.assertEqual(response["context"]["roles"], "R-USER")
            self.assertNotIn("accessToken", response["context"])

    def test_authorizer_reads_identity_source_when_headers_are_absent(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token_result = create_access_token(user_id="user-123", session_id="session-456", provider="kakao")
            response = lambda_handler({"identitySource": [f"Bearer {token_result.token}"]}, None)

            self.assertEqual(response["isAuthorized"], True)
            self.assertEqual(response["context"]["userId"], "user-123")
            self.assertEqual(response["context"]["provider"], "kakao")

    def test_authorizer_rejects_missing_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = lambda_handler({"headers": {}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "UNAUTHORIZED")

    def test_authorizer_rejects_malformed_bearer_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = lambda_handler({"headers": {"Authorization": "Token abc"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "UNAUTHORIZED")

    def test_authorizer_fails_closed_when_signing_secret_is_missing(self):
        env = dict(AUTH_ENV)
        env.pop("AUTH_TOKEN_SIGNING_SECRET")

        with patch.dict(os.environ, env, clear=True):
            response = lambda_handler({"headers": {"Authorization": "Bearer abc.def.ghi"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "AUTH_NOT_CONFIGURED")

    def test_authorizer_rejects_tampered_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token_result = create_access_token(user_id="user-123", session_id="session-456", provider="google")
            tampered = token_result.token[:-1] + ("a" if token_result.token[-1] != "a" else "b")
            response = lambda_handler({"headers": {"Authorization": f"Bearer {tampered}"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertIn(response["context"]["error"], {"INVALID_TOKEN", "INVALID_TOKEN_SIGNATURE"})

    def test_authorizer_rejects_expired_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token_result = create_access_token(
                user_id="user-123",
                session_id="session-456",
                provider="google",
                now=1_000,
                ttl_seconds=1,
            )
            response = lambda_handler({"headers": {"Authorization": f"Bearer {token_result.token}"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "TOKEN_EXPIRED")

    def test_authorizer_rejects_wrong_audience(self):
        signing_env = {**AUTH_ENV, "AUTH_AUDIENCE": "different-api"}

        with patch.dict(os.environ, signing_env, clear=True):
            token_result = create_access_token(user_id="user-123", session_id="session-456", provider="google")

        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = lambda_handler({"headers": {"Authorization": f"Bearer {token_result.token}"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "INVALID_TOKEN_CLAIMS")

    def test_authorizer_rejects_wrong_issuer(self):
        signing_env = {**AUTH_ENV, "AUTH_ISSUER": "different-issuer"}

        with patch.dict(os.environ, signing_env, clear=True):
            token_result = create_access_token(user_id="user-123", session_id="session-456", provider="google")

        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = lambda_handler({"headers": {"Authorization": f"Bearer {token_result.token}"}}, None)

            self.assertEqual(response["isAuthorized"], False)
            self.assertEqual(response["context"]["error"], "INVALID_TOKEN_CLAIMS")


if __name__ == "__main__":
    unittest.main()

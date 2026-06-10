import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.provider_verifier import ProviderValidationError, ProviderVerifier


KAKAO_ENV = {
    "KAKAO_CLIENT_ID": "lovv-kakao-client-id",
    "KAKAO_TOKENINFO_URL": "https://kauth.kakao.com/oauth/tokeninfo",
}


class ProviderVerifierTest(unittest.TestCase):
    def test_kakao_id_token_validates_audience_issuer_and_expiration(self):
        payload = {
            "iss": "https://kauth.kakao.com",
            "aud": "lovv-kakao-client-id",
            "sub": "kakao-user-123",
            "exp": 1_800_000_000,
            "email": "kakao@example.com",
            "email_verified": True,
            "nickname": "Kakao User",
            "picture": "https://images.example.com/kakao.png",
        }

        with patch.dict(os.environ, KAKAO_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value=payload,
            create=True,
        ) as json_post:
            identity = ProviderVerifier().verify("kakao", "id_token", "valid-kakao-id-token")

        json_post.assert_called_once()
        self.assertEqual(identity.provider, "kakao")
        self.assertEqual(identity.provider_user_id, "kakao-user-123")
        self.assertEqual(identity.email, "kakao@example.com")
        self.assertTrue(identity.email_verified)
        self.assertEqual(identity.display_name, "Kakao User")

    def test_kakao_id_token_rejects_wrong_audience(self):
        payload = {
            "iss": "https://kauth.kakao.com",
            "aud": "other-client-id",
            "sub": "kakao-user-123",
            "exp": 1_800_000_000,
        }

        with patch.dict(os.environ, KAKAO_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value=payload,
            create=True,
        ):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify("kakao", "id_token", "wrong-audience-id-token")

        self.assertEqual(context.exception.code, "PROVIDER_TOKEN_INVALID_AUDIENCE")

    def test_kakao_id_token_rejects_wrong_issuer(self):
        payload = {
            "iss": "https://example.com",
            "aud": "lovv-kakao-client-id",
            "sub": "kakao-user-123",
            "exp": 1_800_000_000,
        }

        with patch.dict(os.environ, KAKAO_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value=payload,
            create=True,
        ):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify("kakao", "id_token", "wrong-issuer-id-token")

        self.assertEqual(context.exception.code, "PROVIDER_TOKEN_INVALID_ISSUER")

    def test_kakao_id_token_rejects_expired_token(self):
        payload = {
            "iss": "https://kauth.kakao.com",
            "aud": "lovv-kakao-client-id",
            "sub": "kakao-user-123",
            "exp": 1,
        }

        with patch.dict(os.environ, KAKAO_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value=payload,
            create=True,
        ):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify("kakao", "id_token", "expired-id-token")

        self.assertEqual(context.exception.code, "PROVIDER_TOKEN_EXPIRED")

    def test_kakao_access_token_is_not_accepted_as_production_login_credential(self):
        with patch.dict(os.environ, KAKAO_ENV, clear=True):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify("kakao", "access_token", "legacy-access-token")

        self.assertEqual(context.exception.code, "UNSUPPORTED_CREDENTIAL_TYPE")


if __name__ == "__main__":
    unittest.main()

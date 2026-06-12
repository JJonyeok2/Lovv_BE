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
GOOGLE_ENV = {
    "GOOGLE_CLIENT_ID": "lovv-google-client-id",
    "GOOGLE_CLIENT_SECRET": "lovv-google-client-secret",
    "GOOGLE_TOKEN_URL": "https://oauth2.googleapis.com/token",
    "GOOGLE_TOKENINFO_URL": "https://oauth2.googleapis.com/tokeninfo",
}


class ProviderVerifierTest(unittest.TestCase):
    def test_google_authorization_code_exchanges_code_and_validates_id_token(self):
        token_response = {"id_token": "google-id-token"}
        tokeninfo_payload = {
            "iss": "https://accounts.google.com",
            "aud": "lovv-google-client-id",
            "sub": "google-user-123",
            "email": "google@example.com",
            "email_verified": "true",
            "name": "Google User",
            "picture": "https://images.example.com/google.png",
        }

        with patch.dict(os.environ, GOOGLE_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value=token_response,
            create=True,
        ) as json_post, patch(
            "auth.provider_verifier._json_get",
            return_value=tokeninfo_payload,
            create=True,
        ) as json_get:
            identity = ProviderVerifier().verify(
                "google",
                "authorization_code",
                "google-auth-code",
                redirect_uri="https://lovv.example/auth/callback/google",
                code_verifier="google-pkce-verifier",
            )

        json_post.assert_called_once_with(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": "google-auth-code",
                "client_id": "lovv-google-client-id",
                "client_secret": "lovv-google-client-secret",
                "redirect_uri": "https://lovv.example/auth/callback/google",
                "code_verifier": "google-pkce-verifier",
            },
        )
        json_get.assert_called_once()
        self.assertIn("id_token=google-id-token", json_get.call_args.args[0])
        self.assertEqual(identity.provider, "google")
        self.assertEqual(identity.provider_user_id, "google-user-123")
        self.assertEqual(identity.email, "google@example.com")
        self.assertTrue(identity.email_verified)

    def test_google_authorization_code_requires_redirect_uri(self):
        with patch.dict(os.environ, GOOGLE_ENV, clear=True):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify("google", "authorization_code", "google-auth-code")

        self.assertEqual(context.exception.code, "INVALID_REQUEST")

    def test_kakao_authorization_code_exchanges_code_and_validates_id_token(self):
        token_response = {"id_token": "kakao-id-token"}
        tokeninfo_payload = {
            "iss": "https://kauth.kakao.com",
            "aud": "lovv-kakao-client-id",
            "sub": "kakao-user-123",
            "exp": 1_800_000_000,
            "email": "kakao@example.com",
            "email_verified": True,
            "nickname": "Kakao User",
            "picture": "https://images.example.com/kakao.png",
        }

        with patch.dict(os.environ, {**KAKAO_ENV, "KAKAO_CLIENT_SECRET": "lovv-kakao-client-secret"}, clear=True), patch(
            "auth.provider_verifier._json_post",
            side_effect=[token_response, tokeninfo_payload],
            create=True,
        ) as json_post:
            identity = ProviderVerifier().verify(
                "kakao",
                "authorization_code",
                "kakao-auth-code",
                redirect_uri="https://lovv.example/auth/callback/kakao",
            )

        self.assertEqual(json_post.call_args_list[0].args[0], "https://kauth.kakao.com/oauth/token")
        self.assertEqual(
            json_post.call_args_list[0].kwargs["data"],
            {
                "grant_type": "authorization_code",
                "code": "kakao-auth-code",
                "client_id": "lovv-kakao-client-id",
                "redirect_uri": "https://lovv.example/auth/callback/kakao",
                "client_secret": "lovv-kakao-client-secret",
            },
        )
        self.assertEqual(json_post.call_args_list[1].kwargs["data"], {"id_token": "kakao-id-token"})
        self.assertEqual(identity.provider, "kakao")
        self.assertEqual(identity.provider_user_id, "kakao-user-123")
        self.assertEqual(identity.email, "kakao@example.com")

    def test_authorization_code_rejects_token_response_without_id_token(self):
        with patch.dict(os.environ, GOOGLE_ENV, clear=True), patch(
            "auth.provider_verifier._json_post",
            return_value={"access_token": "google-access-token"},
            create=True,
        ):
            with self.assertRaises(ProviderValidationError) as context:
                ProviderVerifier().verify(
                    "google",
                    "authorization_code",
                    "google-auth-code",
                    redirect_uri="https://lovv.example/auth/callback/google",
                )

        self.assertEqual(context.exception.code, "PROVIDER_TOKEN_INVALID")

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

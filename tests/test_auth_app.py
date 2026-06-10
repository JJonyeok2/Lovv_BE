import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.app import handle_request
from auth.authorizer import lambda_handler as authorizer_handler
from auth.provider_verifier import ProviderIdentity, ProviderValidationError
from auth.session_repository import InMemorySessionRepository
from auth.user_repository import InMemoryUserRepository
from preferences.repository import InMemoryPreferenceRepository
from shared.auth import verify_access_token


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "unit-test-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_REFRESH_TTL_SECONDS": "1209600",
    "AUTH_ISSUER": "lovv-test-auth",
    "AUTH_AUDIENCE": "lovv-test-api",
    "AUTH_REFRESH_COOKIE_NAME": "lovv_session",
}


class FakeProviderVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, provider, credential_type, credential, nonce=None, redirect_uri=None):
        self.calls.append(
            {
                "provider": provider,
                "credential_type": credential_type,
                "credential": credential,
                "nonce": nonce,
                "redirect_uri": redirect_uri,
            }
        )
        if credential == "bad-provider-token":
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID", "Provider credential is invalid")

        if provider == "google":
            return ProviderIdentity(
                provider="google",
                provider_user_id="google-sub-123",
                email="user@example.com",
                email_verified=True,
                display_name="Google User",
                avatar_url="https://images.example.com/google.png",
            )

        return ProviderIdentity(
            provider="kakao",
            provider_user_id="kakao-456",
            email=None,
            email_verified=False,
            display_name="Kakao User",
            avatar_url="https://images.example.com/kakao.png",
        )


def make_event(method, path, body=None, headers=None, cookies=None, authorizer_context=None):
    event = {
        "rawPath": path,
        "headers": headers or {},
        "requestContext": {"http": {"method": method, "sourceIp": "127.0.0.1", "userAgent": "unit-test"}},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    if cookies is not None:
        event["cookies"] = cookies
    if authorizer_context is not None:
        event["requestContext"]["authorizer"] = {"lambda": authorizer_context}
    return event


class AuthAppTest(unittest.TestCase):
    def setUp(self):
        self.provider_verifier = FakeProviderVerifier()
        self.user_repository = InMemoryUserRepository(now="2026-06-10T09:00:00Z")
        self.session_repository = InMemorySessionRepository(now_epoch=1_781_053_200)
        self.preference_repository = InMemoryPreferenceRepository(now="2026-06-10T09:00:00Z")

    def request(self, event):
        return handle_request(
            event,
            provider_verifier=self.provider_verifier,
            user_repository=self.user_repository,
            session_repository=self.session_repository,
            preference_repository=self.preference_repository,
        )

    def test_google_login_validates_provider_token_creates_user_and_sets_refresh_cookie(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "POST",
                    "/api/v1/auth/google",
                    {"credentialType": "id_token", "credential": "valid-google-token", "userId": "client-forged"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(body["tokenType"], "Bearer")
            self.assertEqual(body["expiresIn"], 900)
            self.assertEqual(body["linkedProvider"], "google")
            self.assertTrue(body["user"]["isNewUser"])
            self.assertNotEqual(body["user"]["userId"], "client-forged")
            self.assertEqual(body["user"]["email"], "user@example.com")
            self.assertEqual(len(self.provider_verifier.calls), 1)
            self.assertEqual(self.provider_verifier.calls[0]["credential"], "valid-google-token")

            set_cookie = response["headers"]["Set-Cookie"]
            self.assertIn("lovv_session=", set_cookie)
            self.assertIn("HttpOnly", set_cookie)
            self.assertIn("Secure", set_cookie)
            self.assertIn("SameSite=Lax", set_cookie)

            claims = verify_access_token(body["accessToken"])
            self.assertEqual(claims["sub"], body["user"]["userId"])
            self.assertEqual(claims["sid"], body["session"]["sessionId"])
            self.assertEqual(claims["provider"], "google")
            self.assertEqual(claims["roles"], ["R-USER"])
            self.assertNotIn("valid-google-token", response["body"])

    def test_kakao_login_reuses_existing_social_account(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            first = self.request(
                make_event("POST", "/api/v1/auth/kakao", {"credentialType": "id_token", "credential": "valid-kakao-token"})
            )
            second = self.request(
                make_event("POST", "/api/v1/auth/kakao", {"credentialType": "id_token", "credential": "valid-kakao-token"})
            )
            first_body = json.loads(first["body"])
            second_body = json.loads(second["body"])

            self.assertEqual(first["statusCode"], 200)
            self.assertEqual(second["statusCode"], 200)
            self.assertTrue(first_body["user"]["isNewUser"])
            self.assertFalse(second_body["user"]["isNewUser"])
            self.assertEqual(first_body["user"]["userId"], second_body["user"]["userId"])
            self.assertEqual(len(self.user_repository.users), 1)

    def test_login_rejects_invalid_provider_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "bad-provider-token"})
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 401)
            self.assertEqual(body["error"]["code"], "PROVIDER_TOKEN_INVALID")
            self.assertNotIn("Set-Cookie", response["headers"])

    def test_demo_login_route_is_not_mounted_as_production_auth(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(make_event("POST", "/api/auth/login", {"login_code": "demo-code-only"}))
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 404)
            self.assertEqual(body["error"]["code"], "NOT_FOUND")

    def test_me_uses_authorizer_context_and_returns_public_user_shape(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            user_id = json.loads(login["body"])["user"]["userId"]

            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/auth/me",
                    authorizer_context={"userId": user_id, "sessionId": "session-1", "roles": "R-USER"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(body["user"]["userId"], user_id)
            self.assertEqual(body["user"]["displayName"], "Google User")
            self.assertEqual(body["user"]["roles"], ["R-USER"])

    def test_session_cookie_restores_user_and_refreshes_access_token(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            cookie = login["headers"]["Set-Cookie"].split(";", 1)[0]

            response = self.request(make_event("GET", "/api/v1/auth/session", cookies=[cookie]))
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertTrue(body["authenticated"])
            self.assertEqual(body["tokenType"], "Bearer")
            self.assertEqual(body["user"]["email"], "user@example.com")
            self.assertIsNone(body["preferences"])
            self.assertFalse(body["onboardingCompleted"])
            self.assertEqual(verify_access_token(body["accessToken"])["sub"], body["user"]["userId"])

    def test_session_cookie_loads_saved_preferences_after_login(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            user_id = json.loads(login["body"])["user"]["userId"]
            cookie = login["headers"]["Set-Cookie"].split(";", 1)[0]
            self.preference_repository.upsert(
                user_id,
                {
                    "countryTrack": "KR",
                    "mappedThemes": ["history_tradition"],
                    "preferredRegions": ["gyeongbuk"],
                    "selectedCityStyle": "GYEONGJU",
                    "pace": "balanced",
                    "tripDays": 3,
                    "companionStyle": "solo",
                    "travelStyles": ["slow_walk"],
                },
            )

            response = self.request(make_event("GET", "/api/v1/auth/session", cookies=[cookie]))
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertTrue(body["onboardingCompleted"])
            self.assertEqual(body["preferences"]["countryTrack"], "KR")
            self.assertEqual(body["preferences"]["mappedThemes"], ["history_tradition"])

    def test_logout_revokes_refresh_session_and_clears_cookie(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            cookie = login["headers"]["Set-Cookie"].split(";", 1)[0]
            session_id = json.loads(login["body"])["session"]["sessionId"]

            response = self.request(make_event("POST", "/api/v1/auth/logout", cookies=[cookie]))
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(body, {"success": True})
            self.assertIsNotNone(self.session_repository.sessions[session_id]["revokedAt"])
            self.assertIn("lovv_session=", response["headers"]["Set-Cookie"])
            self.assertIn("Max-Age=0", response["headers"]["Set-Cookie"])

    def test_logout_with_bearer_only_revokes_session_by_sid(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            body = json.loads(login["body"])
            session_id = body["session"]["sessionId"]

            response = self.request(
                make_event(
                    "POST",
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {body['accessToken']}"},
                )
            )

            self.assertEqual(response["statusCode"], 200)
            self.assertIsNotNone(self.session_repository.sessions[session_id]["revokedAt"])
            self.assertIn("Max-Age=0", response["headers"]["Set-Cookie"])

    def test_logout_with_cookie_and_bearer_revokes_refresh_session(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            body = json.loads(login["body"])
            cookie = login["headers"]["Set-Cookie"].split(";", 1)[0]
            session_id = body["session"]["sessionId"]

            response = self.request(
                make_event(
                    "POST",
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {body['accessToken']}"},
                    cookies=[cookie],
                )
            )

            self.assertEqual(response["statusCode"], 200)
            self.assertIsNotNone(self.session_repository.sessions[session_id]["revokedAt"])

    def test_logout_without_cookie_or_bearer_is_idempotent_no_content(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(make_event("POST", "/api/v1/auth/logout"))

            self.assertEqual(response["statusCode"], 204)
            self.assertEqual(response["body"], "")
            self.assertIn("Max-Age=0", response["headers"]["Set-Cookie"])

    def test_access_token_remains_stateless_until_expiration_after_logout(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            login = self.request(
                make_event("POST", "/api/v1/auth/google", {"credentialType": "id_token", "credential": "valid-google-token"})
            )
            body = json.loads(login["body"])
            cookie = login["headers"]["Set-Cookie"].split(";", 1)[0]

            self.request(make_event("POST", "/api/v1/auth/logout", cookies=[cookie]))
            response = authorizer_handler({"headers": {"Authorization": f"Bearer {body['accessToken']}"}}, None)

            self.assertEqual(response["isAuthorized"], True)
            self.assertEqual(response["context"]["sessionId"], body["session"]["sessionId"])


if __name__ == "__main__":
    unittest.main()

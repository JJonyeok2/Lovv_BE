import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from shared.auth import create_access_token


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "unit-test-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_ISSUER": "lovv-test-auth",
    "AUTH_AUDIENCE": "lovv-test-api",
}


def make_event(method, path, headers=None, authorizer_context=None):
    event = {
        "rawPath": path,
        "headers": headers or {},
        "requestContext": {"http": {"method": method}},
    }
    if authorizer_context is not None:
        event["requestContext"]["authorizer"] = {"lambda": authorizer_context}
    return event


class InMemoryAdminUserRepository:
    def __init__(self):
        self.users = [
            {
                "userId": "user-1",
                "displayName": "Regular User",
                "nickname": "regular",
                "email": "regular@example.com",
                "status": "active",
                "role": "user",
                "roles": ["R-USER"],
                "createdAt": "2026-06-10T09:00:00Z",
                "updatedAt": "2026-06-10T09:01:00Z",
                "lastLoginAt": "2026-06-10T09:02:00Z",
                "linkedProviders": ["google"],
                "onboardingCompleted": True,
                "savedItineraryCount": 2,
            },
            {
                "userId": "admin-1",
                "displayName": "Admin User",
                "nickname": None,
                "email": "admin@example.com",
                "status": "active",
                "role": "admin",
                "roles": ["R-ADMIN"],
                "createdAt": "2026-06-10T10:00:00Z",
                "updatedAt": "2026-06-10T10:01:00Z",
                "lastLoginAt": "2026-06-10T10:02:00Z",
                "linkedProviders": ["kakao"],
                "onboardingCompleted": False,
                "savedItineraryCount": 0,
            },
        ]

    def list_users(self):
        return [dict(user) for user in self.users]

    def get_user(self, user_id):
        for user in self.users:
            if user["userId"] == user_id:
                return dict(user)
        return None


class AdminAppTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryAdminUserRepository()

    def request(self, event):
        return handle_request(event, repository=self.repository)

    def test_admin_user_can_list_minimal_user_profiles(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users",
                    authorizer_context={"userId": "admin-1", "roles": "R-ADMIN"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(len(body["users"]), 2)
            self.assertEqual(body["users"][0]["userId"], "user-1")
            self.assertEqual(body["users"][0]["roles"], ["R-USER"])
            self.assertEqual(body["users"][0]["linkedProviders"], ["google"])
            self.assertEqual(body["users"][0]["onboardingCompleted"], True)
            self.assertEqual(body["users"][0]["savedItineraryCount"], 2)
            self.assertNotIn("providerUserId", json.dumps(body))
            self.assertNotIn("token", json.dumps(body).lower())

    def test_admin_user_can_get_user_detail(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users/user-1",
                    authorizer_context={"userId": "admin-1", "roles": "R-ADMIN"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 200)
            self.assertEqual(body["user"]["userId"], "user-1")
            self.assertEqual(body["user"]["email"], "regular@example.com")

    def test_regular_user_cannot_list_or_get_admin_users(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            list_response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users",
                    authorizer_context={"userId": "user-1", "roles": "R-USER"},
                )
            )
            detail_response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users/user-1",
                    authorizer_context={"userId": "user-1", "roles": "R-USER"},
                )
            )

            self.assertEqual(list_response["statusCode"], 403)
            self.assertEqual(detail_response["statusCode"], 403)

    def test_system_role_cannot_access_admin_users(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users",
                    authorizer_context={"userId": "system-1", "roles": ""},
                )
            )

            self.assertEqual(response["statusCode"], 403)

    def test_missing_auth_returns_existing_unauthorized_shape(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(make_event("GET", "/api/v1/admin/users"))
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 401)
            self.assertEqual(body["error"]["code"], "UNAUTHORIZED")

    def test_invalid_bearer_auth_returns_existing_unauthorized_shape(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users",
                    headers={"Authorization": "Bearer invalid.token.value"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 401)
            self.assertIn(body["error"]["code"], {"INVALID_TOKEN", "INVALID_TOKEN_SIGNATURE"})

    def test_valid_admin_bearer_can_access_without_authorizer_context(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token = create_access_token(user_id="admin-1", roles=["R-ADMIN"]).token

            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users",
                    headers={"Authorization": f"Bearer {token}"},
                )
            )

            self.assertEqual(response["statusCode"], 200)

    def test_missing_admin_user_detail_returns_404(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            response = self.request(
                make_event(
                    "GET",
                    "/api/v1/admin/users/missing",
                    authorizer_context={"userId": "admin-1", "roles": "R-ADMIN"},
                )
            )
            body = json.loads(response["body"])

            self.assertEqual(response["statusCode"], 404)
            self.assertEqual(body["error"]["code"], "USER_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from preferences.app import handle_request
from preferences.repository import InMemoryPreferenceRepository
from shared.auth import create_access_token


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "unit-test-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_ISSUER": "lovv-test-auth",
    "AUTH_AUDIENCE": "lovv-test-api",
}


def make_event(method, path, body=None, user_id="user-1"):
    event = {
        "rawPath": path,
        "headers": {"content-type": "application/json"},
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"lambda": {"userId": user_id, "roles": "R-USER"}},
        },
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def make_bearer_event(method, path, body=None, token=None):
    event = {
        "rawPath": path,
        "headers": {"content-type": "application/json", "authorization": f"Bearer {token}"},
        "requestContext": {"http": {"method": method}},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


class PreferencesAppTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryPreferenceRepository(now="2026-06-10T09:00:00Z")

    def test_get_returns_missing_preference_state_for_new_user(self):
        response = handle_request(make_event("GET", "/api/v1/me/preferences"), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"preferences": None, "onboardingCompleted": False})

    def test_put_upserts_current_user_preferences(self):
        payload = {
            "countryTrack": "KR",
            "preferredRegions": ["gyeongbuk"],
            "selectedCityStyle": "GYEONGJU",
            "mappedThemes": ["history_tradition"],
            "pace": "balanced",
            "tripDays": 3,
            "companionStyle": "solo",
            "travelStyles": ["slow_walk", "local_food"],
        }

        response = handle_request(make_event("PUT", "/api/v1/me/preferences", payload), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(body["preferences"]["onboardingCompleted"])
        self.assertEqual(body["preferences"]["userId"], "user-1")
        self.assertEqual(body["preferences"]["mappedThemes"], ["history_tradition"])
        self.assertEqual(body["preferences"]["selectedThemeIds"], ["history_tradition"])
        self.assertEqual(self.repository.preferences_by_user["user-1"]["countryTrack"], "KR")

    def test_put_accepts_selected_theme_ids_alias_and_stores_mapped_themes(self):
        payload = {
            "countryTrack": "JP",
            "selectedThemeIds": ["hot_spring_rest", "food_local"],
        }

        response = handle_request(make_event("PUT", "/api/v1/me/preferences", payload), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(self.repository.preferences_by_user["user-1"]["mappedThemes"], ["hot_spring_rest", "food_local"])
        self.assertEqual(body["preferences"]["mappedThemes"], ["hot_spring_rest", "food_local"])
        self.assertEqual(body["preferences"]["selectedThemeIds"], ["hot_spring_rest", "food_local"])

    def test_put_defaults_country_track_when_missing(self):
        payload = {"selectedThemeIds": ["history_tradition"]}

        response = handle_request(make_event("PUT", "/api/v1/me/preferences", payload), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(self.repository.preferences_by_user["user-1"]["countryTrack"], "BOTH")
        self.assertEqual(body["preferences"]["countryTrack"], "BOTH")

    def test_get_returns_mapped_themes_and_selected_theme_ids_alias(self):
        self.repository.upsert(
            "user-1",
            {
                "countryTrack": "KR",
                "mappedThemes": ["history_tradition"],
                "preferredRegions": [],
                "selectedCityStyle": None,
                "pace": None,
                "tripDays": None,
                "companionStyle": None,
                "travelStyles": [],
            },
        )

        response = handle_request(make_event("GET", "/api/v1/me/preferences"), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["preferences"]["mappedThemes"], ["history_tradition"])
        self.assertEqual(body["preferences"]["selectedThemeIds"], ["history_tradition"])

    def test_put_rejects_client_supplied_owner_fields(self):
        payload = {
            "userId": "attacker",
            "countryTrack": "KR",
            "mappedThemes": ["history_tradition"],
        }

        response = handle_request(make_event("PUT", "/api/v1/me/preferences", payload), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertNotIn("attacker", response["body"])

    def test_put_rejects_incomplete_required_preference_payload(self):
        response = handle_request(
            make_event("PUT", "/api/v1/me/preferences", {"countryTrack": "KR", "mappedThemes": []}),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")

    def test_put_rejects_missing_theme_aliases(self):
        response = handle_request(
            make_event("PUT", "/api/v1/me/preferences", {"countryTrack": "KR"}),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")

    def test_put_rejects_invalid_country_track(self):
        response = handle_request(
            make_event(
                "PUT",
                "/api/v1/me/preferences",
                {"countryTrack": "US", "selectedThemeIds": ["history_tradition"]},
            ),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")

    def test_preferences_are_isolated_by_authenticated_user(self):
        payload = {"countryTrack": "KR", "mappedThemes": ["history_tradition"]}
        handle_request(make_event("PUT", "/api/v1/me/preferences", payload, user_id="user-1"), repository=self.repository)

        response = handle_request(make_event("GET", "/api/v1/me/preferences", user_id="user-2"), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"preferences": None, "onboardingCompleted": False})

    def test_get_accepts_bearer_token_without_authorizer_context(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token = create_access_token(user_id="user-1").token
            self.repository.upsert(
                "user-1",
                {
                    "countryTrack": "KR",
                    "mappedThemes": ["history_tradition"],
                    "preferredRegions": [],
                    "selectedCityStyle": None,
                    "pace": None,
                    "tripDays": None,
                    "companionStyle": None,
                    "travelStyles": [],
                },
            )

            response = handle_request(
                make_bearer_event("GET", "/api/v1/me/preferences", token=token),
                repository=self.repository,
            )
            body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["preferences"]["userId"], "user-1")
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from preferences.app import handle_request
from preferences.repository import InMemoryPreferenceRepository


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
        self.assertEqual(self.repository.preferences_by_user["user-1"]["countryTrack"], "KR")

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

    def test_preferences_are_isolated_by_authenticated_user(self):
        payload = {"countryTrack": "KR", "mappedThemes": ["history_tradition"]}
        handle_request(make_event("PUT", "/api/v1/me/preferences", payload, user_id="user-1"), repository=self.repository)

        response = handle_request(make_event("GET", "/api/v1/me/preferences", user_id="user-2"), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body, {"preferences": None, "onboardingCompleted": False})


if __name__ == "__main__":
    unittest.main()

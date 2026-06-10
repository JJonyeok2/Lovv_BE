import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saved_plans.app import handle_request
from saved_plans.repository import InMemorySavedPlanRepository


def make_event(method, path, body=None, user_id="user-1", path_parameters=None, query=None):
    event = {
        "rawPath": path,
        "pathParameters": path_parameters or {},
        "headers": {"content-type": "application/json"},
        "queryStringParameters": query,
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"lambda": {"userId": user_id, "roles": "R-USER"}},
        },
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def save_payload(**overrides):
    payload = {
        "sourceRecommendationId": "rec-1",
        "idempotencyKey": "idem-1",
        "title": "강릉 1박 2일 미식 산책",
        "summary": "바다와 미식을 묶은 일정입니다.",
        "destination": {"destinationId": "KR-Gangneung", "name": "강릉", "country": "KR", "region": "강원"},
        "tripType": "2d1n",
        "durationLabel": "1박 2일",
        "themes": ["food_local"],
        "conditionsSnapshot": {"travelMonth": 10},
        "requestSummary": "조용한 바다와 미식",
        "itinerary": {
            "days": [
                {
                    "day": 1,
                    "title": "바다 산책",
                    "items": [
                        {"itemId": "item-1", "sortOrder": 1, "title": "안목해변", "body": "해변 산책"}
                    ],
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


class SavedPlansAppTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemorySavedPlanRepository(now="2026-06-10T09:00:00Z")

    def test_saves_generated_itinerary_snapshot(self):
        response = handle_request(
            make_event("POST", "/api/v1/me/itineraries", save_payload()),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(body["sourceRecommendationId"], "rec-1")
        self.assertFalse(body["duplicate"])
        self.assertIn(body["itineraryId"], self.repository.plans)
        self.assertEqual(self.repository.plans[body["itineraryId"]]["userId"], "user-1")

    def test_repeated_save_with_same_idempotency_key_returns_duplicate(self):
        first = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        second = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        first_body = json.loads(first["body"])
        second_body = json.loads(second["body"])

        self.assertEqual(first["statusCode"], 201)
        self.assertEqual(second["statusCode"], 200)
        self.assertEqual(first_body["itineraryId"], second_body["itineraryId"])
        self.assertTrue(second_body["duplicate"])

    def test_rejects_raw_chat_history_fields(self):
        response = handle_request(
            make_event("POST", "/api/v1/me/itineraries", save_payload(messages=[{"role": "user"}])),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "RAW_CHAT_HISTORY_NOT_ALLOWED")

    def test_lists_only_authenticated_users_saved_plans(self):
        handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload(title="내 일정"), user_id="user-1"), repository=self.repository)
        handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload(idempotencyKey="other", title="남의 일정"), user_id="user-2"), repository=self.repository)

        response = handle_request(make_event("GET", "/api/v1/me/itineraries", user_id="user-1"), repository=self.repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["title"], "내 일정")
        self.assertNotIn("itinerary", body["items"][0])

    def test_detail_requires_plan_ownership(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]

        response = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                user_id="user-2",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["error"]["code"], "ITINERARY_NOT_FOUND")

    def test_like_and_unlike_are_idempotent(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]

        liked = handle_request(
            make_event(
                "PUT",
                f"/api/v1/me/itineraries/{itinerary_id}/reactions/like",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        liked_again = handle_request(
            make_event(
                "PUT",
                f"/api/v1/me/itineraries/{itinerary_id}/reactions/like",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        unliked = handle_request(
            make_event(
                "DELETE",
                f"/api/v1/me/itineraries/{itinerary_id}/reactions/like",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )

        self.assertEqual(liked["statusCode"], 200)
        self.assertTrue(json.loads(liked["body"])["changed"])
        self.assertFalse(json.loads(liked_again["body"])["changed"])
        self.assertEqual(unliked["statusCode"], 204)
        self.assertEqual(unliked.get("body", ""), "")
        self.assertFalse(self.repository.plans[itinerary_id]["isLiked"])


if __name__ == "__main__":
    unittest.main()

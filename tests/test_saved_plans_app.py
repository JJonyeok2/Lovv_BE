import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saved_plans.app import handle_request
from saved_plans.repository import InMemorySavedPlanRepository
from shared.auth import create_access_token


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "unit-test-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_ISSUER": "lovv-test-auth",
    "AUTH_AUDIENCE": "lovv-test-api",
}


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


def make_bearer_event(method, path, body=None, token=None, path_parameters=None, query=None):
    event = {
        "rawPath": path,
        "pathParameters": path_parameters or {},
        "headers": {"content-type": "application/json", "authorization": f"Bearer {token}"},
        "queryStringParameters": query,
        "requestContext": {"http": {"method": method}},
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
        "festivalChoice": "include",
        "festivalThemeLabel": "축제 포함",
        "intensityLabel": "덜 걷는 일정",
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
        "alternativeItinerary": {"trigger": "rain", "days": []},
    }
    payload.update(overrides)
    return payload


def response_body(response):
    return json.loads(response["body"])


def frontend_adapter_shape(plan):
    days = (plan.get("itinerary") or {}).get("days")
    first_day = days[0] if isinstance(days, list) and days else {}
    return {
        "itineraryId": plan.get("itineraryId"),
        "title": plan.get("title"),
        "destination": plan.get("destination"),
        "durationLabel": plan.get("durationLabel"),
        "themes": plan.get("themes"),
        "isLiked": plan.get("isLiked"),
        "hasDays": isinstance(days, list) and bool(days),
        "hasItems": isinstance(first_day.get("items"), list) and bool(first_day.get("items")),
        "hasStops": isinstance(first_day.get("stops"), list) and bool(first_day.get("stops")),
        "firstItemTitle": first_day.get("items", [{}])[0].get("title") if first_day.get("items") else None,
        "firstStopTitle": first_day.get("stops", [{}])[0].get("title") if first_day.get("stops") else None,
    }


class SavedPlansAppTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemorySavedPlanRepository(now="2026-06-10T09:00:00Z")

    def test_saves_generated_itinerary_snapshot(self):
        payload = save_payload()
        response = handle_request(
            make_event("POST", "/api/v1/me/itineraries", payload),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(body["title"], payload["title"])
        self.assertEqual(body["sourceRecommendationId"], "rec-1")
        self.assertEqual(body["destination"], payload["destination"])
        self.assertEqual(body["durationLabel"], payload["durationLabel"])
        self.assertEqual(body["themes"], payload["themes"])
        self.assertEqual(body["itinerary"]["days"][0]["items"][0]["title"], payload["itinerary"]["days"][0]["items"][0]["title"])
        self.assertEqual(body["itinerary"]["days"][0]["stops"][0]["title"], payload["itinerary"]["days"][0]["items"][0]["title"])
        self.assertEqual(body["festivalChoice"], payload["festivalChoice"])
        self.assertEqual(body["festivalThemeLabel"], payload["festivalThemeLabel"])
        self.assertEqual(body["intensityLabel"], payload["intensityLabel"])
        self.assertEqual(body["conditionsSnapshot"]["travelMonth"], 10)
        self.assertEqual(body["conditionsSnapshot"]["festivalChoice"], payload["festivalChoice"])
        self.assertEqual(body["conditionsSnapshot"]["festivalThemeLabel"], payload["festivalThemeLabel"])
        self.assertEqual(body["conditionsSnapshot"]["intensityLabel"], payload["intensityLabel"])
        self.assertEqual(body["alternativeItinerary"], payload["alternativeItinerary"])
        self.assertFalse(body["isLiked"])
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
        self.assertEqual(second_body["title"], first_body["title"])
        self.assertEqual(second_body["destination"], first_body["destination"])
        self.assertEqual(second_body["durationLabel"], first_body["durationLabel"])
        self.assertEqual(second_body["themes"], first_body["themes"])
        self.assertEqual(second_body["itinerary"], first_body["itinerary"])
        self.assertTrue(second_body["duplicate"])

    def test_same_active_idempotency_key_with_different_payload_returns_conflict(self):
        first = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        conflict = handle_request(
            make_event("POST", "/api/v1/me/itineraries", save_payload(title="다른 일정 제목")),
            repository=self.repository,
        )
        body = json.loads(conflict["body"])

        self.assertEqual(first["statusCode"], 201)
        self.assertEqual(conflict["statusCode"], 409)
        self.assertEqual(body["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")

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
        item = body["items"][0]
        self.assertEqual(item["title"], "내 일정")
        self.assertEqual(item["itineraryId"], "plan-1")
        self.assertEqual(item["destination"]["destinationId"], "KR-Gangneung")
        self.assertEqual(item["durationLabel"], "1박 2일")
        self.assertEqual(item["themes"], ["food_local"])
        self.assertEqual(item["itinerary"]["days"][0]["stops"][0]["title"], "안목해변")
        self.assertEqual(item["itinerary"]["days"][0]["items"][0]["title"], "안목해변")
        self.assertFalse(item["isLiked"])

    def test_accepts_frontend_stops_snapshot_and_returns_items_alias(self):
        frontend_payload = save_payload(
            idempotencyKey="frontend-stops",
            itinerary={
                "days": [
                    {
                        "day": 1,
                        "title": "느린 산책",
                        "stops": [
                            {"itemId": "stop-1", "sortOrder": 1, "title": "경포호", "body": "호수 산책"}
                        ],
                    }
                ]
            },
        )

        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", frontend_payload), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]
        detail = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        listed = handle_request(make_event("GET", "/api/v1/me/itineraries"), repository=self.repository)

        detail_body = json.loads(detail["body"])
        list_body = json.loads(listed["body"])
        saved_body = json.loads(saved["body"])
        saved_day = saved_body["itinerary"]["days"][0]
        detail_day = detail_body["itinerary"]["days"][0]
        list_day = list_body["items"][0]["itinerary"]["days"][0]
        self.assertEqual(saved["statusCode"], 201)
        self.assertEqual(saved_day["stops"][0]["title"], "경포호")
        self.assertEqual(saved_day["items"][0]["title"], "경포호")
        self.assertEqual(detail_body["userId"], "user-1")
        self.assertEqual(detail_body["ownerId"], "user-1")
        self.assertEqual(list_body["items"][0]["userId"], "user-1")
        self.assertEqual(list_body["items"][0]["ownerId"], "user-1")
        self.assertEqual(detail_day["stops"][0]["title"], "경포호")
        self.assertEqual(detail_day["items"][0]["title"], "경포호")
        self.assertEqual(list_day["stops"][0]["title"], "경포호")
        self.assertEqual(list_day["items"][0]["title"], "경포호")

    def test_detail_returns_richer_plan_fields_than_list(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]

        listed = handle_request(make_event("GET", "/api/v1/me/itineraries"), repository=self.repository)
        detail = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        list_item = json.loads(listed["body"])["items"][0]
        detail_body = json.loads(detail["body"])

        self.assertEqual(detail["statusCode"], 200)
        self.assertEqual(detail_body["itineraryId"], list_item["itineraryId"])
        self.assertEqual(detail_body["title"], list_item["title"])
        self.assertEqual(detail_body["destination"], list_item["destination"])
        self.assertEqual(detail_body["durationLabel"], list_item["durationLabel"])
        self.assertEqual(detail_body["themes"], list_item["themes"])
        self.assertEqual(detail_body["itinerary"], list_item["itinerary"])
        self.assertEqual(detail_body["isLiked"], list_item["isLiked"])
        self.assertEqual(detail_body["conditionsSnapshot"]["travelMonth"], 10)
        self.assertEqual(detail_body["requestSummary"], "조용한 바다와 미식")
        self.assertEqual(detail_body["alternativeItinerary"], {"trigger": "rain", "days": []})
        self.assertEqual(detail_body["festivalChoice"], "include")
        self.assertEqual(detail_body["festivalThemeLabel"], "축제 포함")
        self.assertEqual(detail_body["intensityLabel"], "덜 걷는 일정")
        self.assertNotIn("conditionsSnapshot", list_item)
        self.assertNotIn("requestSummary", list_item)
        self.assertNotIn("alternativeItinerary", list_item)

    def test_create_response_matches_detail_core_fields_for_immediate_frontend_use(self):
        created = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        create_body = response_body(created)
        itinerary_id = create_body["itineraryId"]

        detail = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        detail_body = response_body(detail)

        self.assertEqual(created["statusCode"], 201)
        for field in (
            "itineraryId",
            "title",
            "destination",
            "durationLabel",
            "themes",
            "itinerary",
            "isLiked",
            "savedAt",
            "updatedAt",
        ):
            self.assertIn(field, create_body)
            self.assertEqual(create_body[field], detail_body[field])

    def test_create_list_and_detail_share_frontend_adapter_shape(self):
        created = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        create_body = response_body(created)
        itinerary_id = create_body["itineraryId"]
        listed = handle_request(make_event("GET", "/api/v1/me/itineraries"), repository=self.repository)
        detail = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )

        list_item = response_body(listed)["items"][0]
        detail_body = response_body(detail)

        self.assertEqual(frontend_adapter_shape(create_body), frontend_adapter_shape(list_item))
        self.assertEqual(frontend_adapter_shape(create_body), frontend_adapter_shape(detail_body))

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

    def test_deletes_owned_saved_plan_and_removes_it_from_list(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]

        response = handle_request(
            make_event(
                "DELETE",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        listed = handle_request(make_event("GET", "/api/v1/me/itineraries"), repository=self.repository)
        detail = handle_request(
            make_event(
                "GET",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        list_body = json.loads(listed["body"])

        self.assertEqual(response["statusCode"], 204)
        self.assertEqual(response.get("body", ""), "")
        self.assertEqual(list_body["items"], [])
        self.assertEqual(detail["statusCode"], 404)
        self.assertIn(itinerary_id, self.repository.plans)
        self.assertIsNotNone(self.repository.plans[itinerary_id]["deletedAt"])

    def test_delete_rejects_another_users_saved_plan(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]

        response = handle_request(
            make_event(
                "DELETE",
                f"/api/v1/me/itineraries/{itinerary_id}",
                user_id="user-2",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["error"]["code"], "FORBIDDEN")
        self.assertIn(itinerary_id, self.repository.plans)

    def test_delete_missing_saved_plan_returns_404(self):
        response = handle_request(
            make_event(
                "DELETE",
                "/api/v1/me/itineraries/missing-plan",
                path_parameters={"itineraryId": "missing-plan"},
            ),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["error"]["code"], "ITINERARY_NOT_FOUND")

    def test_resaves_soft_deleted_plan_without_unique_key_collision(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]
        handle_request(
            make_event(
                "DELETE",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )

        restored = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        restored_body = json.loads(restored["body"])
        listed = handle_request(make_event("GET", "/api/v1/me/itineraries"), repository=self.repository)
        list_body = json.loads(listed["body"])

        self.assertEqual(restored["statusCode"], 201)
        self.assertFalse(restored_body["duplicate"])
        self.assertEqual(restored_body["itineraryId"], itinerary_id)
        self.assertIsNone(self.repository.plans[itinerary_id]["deletedAt"])
        self.assertEqual(len(list_body["items"]), 1)
        self.assertEqual(list_body["items"][0]["itineraryId"], itinerary_id)

    def test_resave_soft_deleted_plan_restores_even_when_snapshot_hash_changes(self):
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]
        handle_request(
            make_event(
                "DELETE",
                f"/api/v1/me/itineraries/{itinerary_id}",
                path_parameters={"itineraryId": itinerary_id},
            ),
            repository=self.repository,
        )

        response = handle_request(
            make_event("POST", "/api/v1/me/itineraries", save_payload(title="conflicting title")),
            repository=self.repository,
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 201)
        self.assertFalse(body["duplicate"])
        self.assertEqual(body["itineraryId"], itinerary_id)
        self.assertEqual(body["title"], "conflicting title")
        self.assertIsNone(self.repository.plans[itinerary_id]["deletedAt"])

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

    def test_list_accepts_bearer_token_without_authorizer_context(self):
        with patch.dict(os.environ, AUTH_ENV, clear=True):
            token = create_access_token(user_id="user-1").token
            handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload()), repository=self.repository)

            response = handle_request(
                make_bearer_event("GET", "/api/v1/me/itineraries", token=token),
                repository=self.repository,
            )
            body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "http://localhost:5173")

    def test_sharing_toggling_detail_access_and_cloning_api(self):
        # 1. Save an itinerary as user-1
        saved = handle_request(make_event("POST", "/api/v1/me/itineraries", save_payload(), user_id="user-1"), repository=self.repository)
        itinerary_id = json.loads(saved["body"])["itineraryId"]
        self.assertEqual(saved["statusCode"], 201)

        # 2. user-2 tries to view detail of user-1's private itinerary (must return 404/403 or ITINERARY_NOT_FOUND)
        unauth_view = handle_request(
            make_event("GET", f"/api/v1/me/itineraries/{itinerary_id}", user_id="user-2", path_parameters={"itineraryId": itinerary_id}),
            repository=self.repository,
        )
        self.assertEqual(unauth_view["statusCode"], 404)

        # 3. user-1 makes it public
        share_res = handle_request(
            make_event("PATCH", f"/api/v1/me/itineraries/{itinerary_id}/share", save_payload(isPublic=True), user_id="user-1", path_parameters={"itineraryId": itinerary_id}),
            repository=self.repository,
        )
        self.assertEqual(share_res["statusCode"], 200)
        self.assertTrue(json.loads(share_res["body"])["isPublic"])

        # 4. user-2 can now view it
        auth_view_shared = handle_request(
            make_event("GET", f"/api/v1/me/itineraries/{itinerary_id}", user_id="user-2", path_parameters={"itineraryId": itinerary_id}),
            repository=self.repository,
        )
        self.assertEqual(auth_view_shared["statusCode"], 200)
        self.assertTrue(json.loads(auth_view_shared["body"])["isPublic"])

        # 5. Guest user (no user_id) can view it via public path
        guest_view = handle_request(
            make_event("GET", f"/api/v1/itineraries/{itinerary_id}", user_id=None, path_parameters={"itineraryId": itinerary_id}),
            repository=self.repository,
        )
        self.assertEqual(guest_view["statusCode"], 200)
        self.assertTrue(json.loads(guest_view["body"])["isPublic"])

        # 6. Guest user (no user_id) trying to view private itinerary should fail
        private_id = "some-private-id"
        self.repository.plans[private_id] = {"itineraryId": private_id, "userId": "user-1", "isPublic": False, "deletedAt": None}
        guest_view_private = handle_request(
            make_event("GET", f"/api/v1/itineraries/{private_id}", user_id=None, path_parameters={"itineraryId": private_id}),
            repository=self.repository,
        )
        self.assertEqual(guest_view_private["statusCode"], 404)

        # 7. List public plans (guest)
        public_list = handle_request(
            make_event("GET", "/api/v1/itineraries/public", user_id=None),
            repository=self.repository,
        )
        self.assertEqual(public_list["statusCode"], 200)
        self.assertEqual(len(json.loads(public_list["body"])["items"]), 1)

        # 8. Clone itinerary as user-2
        clone_res = handle_request(
            make_event("POST", f"/api/v1/me/itineraries/{itinerary_id}/clone", user_id="user-2", path_parameters={"itineraryId": itinerary_id}),
            repository=self.repository,
        )
        self.assertEqual(clone_res["statusCode"], 201)
        cloned_body = json.loads(clone_res["body"])
        self.assertEqual(cloned_body["ownerId"], "user-2")
        self.assertEqual(cloned_body["copiedFromItineraryId"], itinerary_id)
        self.assertFalse(cloned_body["isPublic"])


if __name__ == "__main__":
    unittest.main()

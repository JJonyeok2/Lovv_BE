import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saved_plans.repository import RdsDataSavedPlanRepository, canonical_snapshot_hash
from shared.rds_data import json_dumps


def save_payload(**overrides):
    payload = {
        "sourceRecommendationId": "rec-1",
        "idempotencyKey": "idem-1",
        "title": "안동 하루 전통 산책",
        "summary": "전통 명소를 중심으로 걷는 일정입니다.",
        "destination": {"destinationId": "KR-Andong", "name": "안동", "country": "KR", "region": "경북"},
        "tripType": "daytrip",
        "durationLabel": "당일치기",
        "themes": ["전통"],
        "conditionsSnapshot": {"travelMonth": 6},
        "requestSummary": "6월 전통 테마",
        "itinerary": {
            "days": [
                {
                    "day": 1,
                    "title": "전통 산책",
                    "items": [
                        {
                            "itemId": "item-1",
                            "sortOrder": 2,
                            "timeSlot": "오후",
                            "title": "하회마을",
                            "body": "마을 산책",
                            "contentId": "126157",
                            "placeId": "place-1",
                            "latitude": 36.54,
                            "longitude": 128.52,
                            "moveHint": "도보",
                            "recommendationReason": "전통 테마와 맞습니다.",
                            "sourceBadges": ["festival"],
                        }
                    ],
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


class FakeSqlClient:
    def __init__(self, fetch_one_rows=None, fetch_all_rows=None):
        self.fetch_one_rows = list(fetch_one_rows or [])
        self.fetch_all_rows = list(fetch_all_rows or [])
        self.executed = []
        self.fetch_one_calls = []
        self.fetch_all_calls = []

    def execute(self, sql, parameters=None, include_result_metadata=True):
        self.executed.append(
            {
                "sql": " ".join(sql.split()),
                "parameters": parameters or {},
                "include_result_metadata": include_result_metadata,
            }
        )
        return {"numberOfRecordsUpdated": 1}

    def fetch_one(self, sql, parameters=None):
        self.fetch_one_calls.append({"sql": " ".join(sql.split()), "parameters": parameters or {}})
        return self.fetch_one_rows.pop(0) if self.fetch_one_rows else None

    def fetch_all(self, sql, parameters=None):
        self.fetch_all_calls.append({"sql": " ".join(sql.split()), "parameters": parameters or {}})
        return self.fetch_all_rows.pop(0) if self.fetch_all_rows else []


class SavedPlansRepositorySchemaTest(unittest.TestCase):
    def test_defaults_to_data_stack_table_names(self):
        repository = RdsDataSavedPlanRepository(rds_client=FakeSqlClient())

        self.assertEqual(repository.table_name, "itineraries")
        self.assertEqual(repository.item_table_name, "itinerary_items")
        self.assertEqual(repository.reaction_table_name, "plan_reactions")

    def test_save_writes_data_stack_itineraries_and_items_tables(self):
        client = FakeSqlClient()
        repository = RdsDataSavedPlanRepository(rds_client=client)
        payload = save_payload()
        snapshot_hash = canonical_snapshot_hash(payload)

        plan, duplicate = repository.save("user-1", payload, snapshot_hash, "2026-06-13T00:00:00Z")

        sql_statements = [call["sql"] for call in client.executed]
        itinerary_insert = next(sql for sql in sql_statements if "INSERT INTO itineraries" in sql)
        item_insert = next(sql for sql in sql_statements if "INSERT INTO itinerary_items" in sql)
        item_params = next(call["parameters"] for call in client.executed if "INSERT INTO itinerary_items" in call["sql"])

        self.assertFalse(duplicate)
        self.assertEqual(plan["itineraryId"], item_params["itinerary_id"])
        self.assertIn("itinerary_json", itinerary_insert)
        self.assertIn(":itinerary_json", itinerary_insert)
        self.assertNotIn("is_liked", itinerary_insert)
        self.assertIn("created_at", itinerary_insert)
        self.assertIn("day_index", item_insert)
        self.assertEqual(item_params["day_index"], 1)
        self.assertEqual(item_params["sort_order"], 2)
        self.assertEqual(item_params["place_name"], "하회마을")
        self.assertEqual(item_params["source_badges"], "[\"festival\"]")
        self.assertIn('"days"', next(call["parameters"] for call in client.executed if "INSERT INTO itineraries" in call["sql"])["itinerary_json"])

    def test_list_preserves_snapshot_route_when_rehydrating_item_rows(self):
        snapshot_itinerary = {
            "days": [
                {
                    "day": 1,
                    "title": "도로 경로 포함",
                    "summary": "route snapshot",
                    "route": {
                        "provider": "openrouteservice",
                        "geometry": {"type": "LineString", "coordinates": [[128.947, 37.771], [128.908, 37.805]]},
                        "distanceMeters": 4200,
                        "durationSeconds": 780,
                    },
                    "stops": [
                        {"time": "아침", "title": "안목해변", "move": "13분", "latitude": 37.771, "longitude": 128.947}
                    ],
                }
            ]
        }
        client = FakeSqlClient(
            fetch_all_rows=[
                [
                    {
                        "id": "plan-1",
                        "user_id": "user-1",
                        "source_recommendation_id": "rec-1",
                        "title": "강릉 1박 2일",
                        "summary": "바다 산책",
                        "destination_json": "{\"destinationId\":\"KR-Gangneung\",\"name\":\"강릉\"}",
                        "trip_type": "2d1n",
                        "duration_label": "1박 2일",
                        "themes_json": "[\"sea_coast\"]",
                        "conditions_snapshot_json": "{}",
                        "request_summary": "바다",
                        "itinerary_json": json_dumps(snapshot_itinerary),
                        "alternative_itinerary_json": None,
                        "is_liked": 0,
                        "saved_at": "2026-06-13T00:00:00Z",
                        "updated_at": "2026-06-13T00:00:00Z",
                        "deleted_at": None,
                    }
                ],
                [
                    {
                        "id": "item-row-1",
                        "itinerary_id": "plan-1",
                        "day_index": 1,
                        "sort_order": 1,
                        "time_slot": "아침",
                        "place_name": "안목해변",
                        "content_id": "attraction#1",
                        "place_id": None,
                        "latitude": 37.771,
                        "longitude": 128.947,
                        "move_hint": "13분",
                        "recommendation_reason": "바다 테마",
                        "body": "해변 산책",
                        "source_badges": "[]",
                    }
                ],
            ]
        )
        repository = RdsDataSavedPlanRepository(rds_client=client)

        plans = repository.list_by_user("user-1", limit=20)

        list_sql = client.fetch_all_calls[0]["sql"]
        day = plans[0]["itinerary"]["days"][0]
        self.assertIn("itinerary_json", list_sql)
        self.assertEqual(day["route"]["provider"], "openrouteservice")
        self.assertEqual(day["route"]["geometry"]["coordinates"], [[128.947, 37.771], [128.908, 37.805]])
        self.assertEqual(day["stops"][0]["latitude"], 37.771)

    def test_save_maps_frontend_stop_aliases_to_item_columns(self):
        client = FakeSqlClient()
        repository = RdsDataSavedPlanRepository(rds_client=client)
        payload = save_payload(
            idempotencyKey="frontend-stops",
            itinerary={
                "days": [
                    {
                        "day": 1,
                        "title": "느린 산책",
                        "stops": [
                            {
                                "time": "아침",
                                "move": "도보 10분",
                                "title": "경포호",
                                "body": "호수 산책",
                                "reason": "동선이 짧습니다.",
                            }
                        ],
                    }
                ]
            },
        )
        snapshot_hash = canonical_snapshot_hash(payload)

        repository.save("user-1", payload, snapshot_hash, "2026-06-13T00:00:00Z")

        item_params = next(call["parameters"] for call in client.executed if "INSERT INTO itinerary_items" in call["sql"])
        self.assertEqual(item_params["time_slot"], "아침")
        self.assertEqual(item_params["move_hint"], "도보 10분")
        self.assertEqual(item_params["recommendation_reason"], "동선이 짧습니다.")

    def test_list_rehydrates_itinerary_items_from_data_stack_items_table(self):
        client = FakeSqlClient(
            fetch_all_rows=[
                [
                    {
                        "id": "plan-1",
                        "user_id": "user-1",
                        "source_recommendation_id": "rec-1",
                        "title": "안동 하루 전통 산책",
                        "summary": "전통 명소를 중심으로 걷는 일정입니다.",
                        "destination_json": "{\"name\":\"안동\"}",
                        "trip_type": "daytrip",
                        "duration_label": "당일치기",
                        "themes_json": "[\"전통\"]",
                        "conditions_snapshot_json": "{\"travelMonth\":6}",
                        "request_summary": "6월 전통 테마",
                        "alternative_itinerary_json": None,
                        "is_liked": 1,
                        "saved_at": "2026-06-13T00:00:00Z",
                        "updated_at": "2026-06-13T00:00:00Z",
                        "deleted_at": None,
                    }
                ],
                [
                    {
                        "id": "item-row-1",
                        "itinerary_id": "plan-1",
                        "day_index": 1,
                        "sort_order": 2,
                        "time_slot": "오후",
                        "place_name": "하회마을",
                        "content_id": "126157",
                        "place_id": "place-1",
                        "latitude": 36.54,
                        "longitude": 128.52,
                        "move_hint": "도보",
                        "recommendation_reason": "전통 테마와 맞습니다.",
                        "body": "마을 산책",
                        "source_badges": "[\"festival\"]",
                    }
                ],
            ]
        )
        repository = RdsDataSavedPlanRepository(rds_client=client)

        plans = repository.list_by_user("user-1", limit=20)

        list_sql = client.fetch_all_calls[0]["sql"]
        item_sql = client.fetch_all_calls[1]["sql"]
        item = plans[0]["itinerary"]["days"][0]["items"][0]
        self.assertIn("FROM itineraries", list_sql)
        self.assertIn("plan_reactions", list_sql)
        self.assertIn("FROM itinerary_items", item_sql)
        self.assertTrue(plans[0]["isLiked"])
        self.assertEqual(item["itemId"], "item-row-1")
        self.assertEqual(item["sortOrder"], 2)
        self.assertEqual(item["title"], "하회마을")
        self.assertEqual(item["time"], "오후")
        self.assertEqual(item["move"], "도보")
        self.assertEqual(item["reason"], "전통 테마와 맞습니다.")
        self.assertEqual(item["timeSlot"], "오후")
        self.assertEqual(item["moveHint"], "도보")
        self.assertEqual(item["recommendationReason"], "전통 테마와 맞습니다.")
        self.assertEqual(plans[0]["itinerary"]["days"][0]["stops"][0]["title"], "하회마을")

    def test_like_uses_plan_reactions_table(self):
        client = FakeSqlClient(
            fetch_one_rows=[
                {
                    "id": "plan-1",
                    "user_id": "user-1",
                    "source_recommendation_id": "rec-1",
                    "title": "안동 하루 전통 산책",
                    "is_liked": 0,
                    "saved_at": "2026-06-13T00:00:00Z",
                    "updated_at": "2026-06-13T00:00:00Z",
                    "deleted_at": None,
                }
            ],
            fetch_all_rows=[[]],
        )
        repository = RdsDataSavedPlanRepository(rds_client=client)

        plan, changed = repository.set_like("user-1", "plan-1", True, "2026-06-13T00:05:00Z")

        sql_statements = [call["sql"] for call in client.executed]
        self.assertTrue(changed)
        self.assertTrue(plan["isLiked"])
        self.assertTrue(any("INSERT INTO plan_reactions" in sql for sql in sql_statements))
        self.assertFalse(any("SET is_liked" in sql for sql in sql_statements))

    def test_resave_soft_deleted_plan_clears_previous_reactions(self):
        payload = save_payload()
        snapshot_hash = canonical_snapshot_hash(payload)
        client = FakeSqlClient(
            fetch_one_rows=[
                None,
                {
                    "id": "plan-1",
                    "user_id": "user-1",
                    "source_recommendation_id": "rec-1",
                    "idempotency_key": "idem-1",
                    "snapshot_hash": snapshot_hash,
                    "deleted_at": "2026-06-13T00:01:00Z",
                },
            ]
        )
        repository = RdsDataSavedPlanRepository(rds_client=client)

        plan, duplicate = repository.save("user-1", payload, snapshot_hash, "2026-06-13T00:05:00Z")

        sql_statements = [call["sql"] for call in client.executed]
        self.assertFalse(duplicate)
        self.assertEqual(plan["itineraryId"], "plan-1")
        self.assertTrue(any("DELETE FROM plan_reactions" in sql for sql in sql_statements))

    def test_sharing_and_cloning_repository_logic(self):
        # 1. Test In-Memory Repository
        from saved_plans.repository import InMemorySavedPlanRepository
        repo = InMemorySavedPlanRepository(now="2026-06-25T12:00:00Z")
        payload = save_payload()

        # Save private plan
        plan, dup = repo.save("user-1", payload, "hash-1")
        plan_id = plan["itineraryId"]
        self.assertFalse(plan["isPublic"])

        # Non-owner cannot view private plan
        self.assertIsNone(repo.get_public_or_owned("user-2", plan_id))

        # Make plan public
        updated = repo.update_share_status("user-1", plan_id, True)
        self.assertTrue(updated["isPublic"])

        # Now non-owner can view
        shared_plan = repo.get_public_or_owned("user-2", plan_id)
        self.assertIsNotNone(shared_plan)
        self.assertTrue(shared_plan["isPublic"])

        # List public plans
        public_list = repo.list_public()
        self.assertEqual(len(public_list), 1)
        self.assertEqual(public_list[0]["itineraryId"], plan_id)

        # Clone plan by another user
        cloned = repo.clone_itinerary("user-2", plan_id)
        self.assertIsNotNone(cloned)
        self.assertNotEqual(cloned["itineraryId"], plan_id)
        self.assertEqual(cloned["userId"], "user-2")
        self.assertEqual(cloned["copiedFromItineraryId"], plan_id)
        self.assertFalse(cloned["isPublic"]) # Cloned plan must be private by default


if __name__ == "__main__":
    unittest.main()

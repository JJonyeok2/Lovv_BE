import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shared.rds_data import json_dumps
from recommendations.reaction_repository import RdsRecommendationReactionRepository


class FakeRdsClient:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def fetch_all(self, sql, parameters=None):
        self.calls.append({"sql": sql, "parameters": parameters or {}})
        return list(self.rows)


class RecommendationReactionRepositoryTest(unittest.TestCase):
    def test_reads_liked_reaction_signals_from_saved_plan_tables(self):
        client = FakeRdsClient([
            {
                "itinerary_id": "itinerary-1",
                "reaction_type": "like",
                "reacted_at": "2026-07-01T00:00:00Z",
                "destination_json": json_dumps({"destinationId": "KR-Donghae", "name": "동해시", "country": "KR"}),
                "themes_json": json_dumps(["바다", "자연"]),
                "duration_label": "2박 3일",
                "trip_type": "2d3n",
                "title": "동해 바다 일정",
                "summary": "해안 산책 일정",
            }
        ])
        repository = RdsRecommendationReactionRepository(
            rds_client=client,
            itinerary_table_name="itineraries",
            reaction_table_name="plan_reactions",
        )

        signals = repository.list_liked_itinerary_signals("user-1", limit=6)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["sourceReaction"], {
            "itineraryId": "itinerary-1",
            "reaction": "like",
            "reactedAt": "2026-07-01T00:00:00Z",
        })
        self.assertEqual(signals[0]["destination"]["destinationId"], "KR-Donghae")
        self.assertEqual(signals[0]["themes"], ["바다", "자연"])
        self.assertIn("JOIN itineraries i ON i.id = pr.itinerary_id", client.calls[0]["sql"])
        self.assertIn("pr.reaction_type = 'like'", client.calls[0]["sql"])
        self.assertEqual(client.calls[0]["parameters"], {"user_id": "user-1", "limit": 6})

    def test_reads_popular_destination_signals_without_public_filter(self):
        client = FakeRdsClient([
            {
                "itinerary_id": "private-itinerary-1",
                "user_id": "user-1",
                "destination_json": json_dumps({"destinationId": "KR-Donghae", "name": "동해시", "country": "KR"}),
                "themes_json": json_dumps(["바다", "자연"]),
                "saved_at": "2026-07-01T00:00:00Z",
                "birth_date": "1993-07-11",
                "reaction_count": 3,
            }
        ])
        repository = RdsRecommendationReactionRepository(
            rds_client=client,
            itinerary_table_name="itineraries",
            reaction_table_name="plan_reactions",
            users_table_name="users",
        )

        signals = repository.list_popular_destination_signals(limit=12)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["destination"]["destinationId"], "KR-Donghae")
        self.assertEqual(signals[0]["themes"], ["바다", "자연"])
        self.assertEqual(signals[0]["reactionCount"], 3)
        self.assertEqual(signals[0]["savedPlanCount"], 1)
        self.assertEqual(signals[0]["birthDate"], "1993-07-11")
        self.assertIn("FROM itineraries i", client.calls[0]["sql"])
        self.assertIn("JOIN plan_reactions pr", client.calls[0]["sql"])
        self.assertIn("LEFT JOIN users u ON u.id = i.user_id", client.calls[0]["sql"])
        self.assertIn("i.deleted_at IS NULL", client.calls[0]["sql"])
        self.assertNotIn("is_public", client.calls[0]["sql"])
        self.assertIn("i.user_id", client.calls[0]["sql"])
        self.assertEqual(client.calls[0]["parameters"], {"limit": 12})


if __name__ == "__main__":
    unittest.main()

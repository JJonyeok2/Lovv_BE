import json
import unittest
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recommendations.app import handle_request


class FakeCityRepository:
    def __init__(self):
        self.records = [
            {
                "id": "KR-Donghae",
                "country": "KR",
                "country_label": "한국",
                "region": "강원",
                "name_ko": "동해시",
                "themes": ["바다", "자연"],
                "summary": "해안 산책과 자연 테마가 어울리는 소도시입니다.",
                "detail": "7월 바다 산책에 맞는 추천 후보입니다.",
                "highlights": ["망상해변", "무릉계곡"],
                "image_url": "https://images.example.com/donghae.jpg",
                "internal_meta": {"festivalCount": 1},
            },
            {
                "id": "KR-Jecheon",
                "country": "KR",
                "country_label": "한국",
                "region": "충북",
                "name_ko": "제천시",
                "themes": ["자연", "산책"],
                "summary": "호수와 숲길을 중심으로 걷기 좋은 소도시입니다.",
                "detail": "여름 산책 후보입니다.",
                "highlights": ["의림지"],
                "image_url": None,
                "internal_meta": {"festivalCount": 0},
            },
            {
                "id": "KR-Andong",
                "country": "KR",
                "country_label": "한국",
                "region": "경북",
                "name_ko": "안동시",
                "themes": ["전통"],
                "summary": "전통 테마가 강한 소도시입니다.",
                "detail": "하회마을 중심의 전통 여행지입니다.",
                "highlights": ["하회마을"],
                "image_url": "https://images.example.com/andong.jpg",
                "internal_meta": {"festivalCount": 0},
            },
            {
                "id": "JP-Otaru",
                "country": "JP",
                "country_label": "일본",
                "region": "홋카이도",
                "name_ko": "오타루",
                "themes": ["바다", "미식"],
                "summary": "운하와 미식이 어울리는 일본 소도시입니다.",
                "detail": "여름 운하 산책 후보입니다.",
                "highlights": ["오타루 운하"],
                "image_url": "https://images.example.com/otaru.jpg",
                "internal_meta": {"festivalCount": 0},
            },
        ]

    def list_city_records(self):
        return list(self.records)


class FakeReactionRepository:
    def __init__(self, reactions=None, popular_signals=None):
        self.reactions = reactions or []
        self.popular_signals = popular_signals or []

    def list_liked_itinerary_signals(self, user_id, limit=20):
        return [dict(reaction) for reaction in self.reactions[:limit]]

    def list_popular_destination_signals(self, limit=120):
        return [dict(signal) for signal in self.popular_signals[:limit]]


def event(method, path, query=None, user_id=None):
    payload = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": query or {},
        "headers": {"origin": "http://localhost:5173"},
    }
    if user_id:
        payload["requestContext"]["authorizer"] = {"lambda": {"userId": user_id, "sub": user_id}}
    return payload


class RecommendationFeedAppTest(unittest.TestCase):
    def test_monthly_cities_uses_asia_seoul_current_month_and_country_limit(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/monthly-cities", {"country": "KR", "limit": "2"}),
            city_repository=FakeCityRepository(),
            now=datetime(2026, 6, 30, 15, 10),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["month"], 7)
        self.assertEqual(body["timezone"], "Asia/Seoul")
        self.assertEqual(len(body["items"]), 2)
        self.assertTrue(all(item["country"] == "KR" for item in body["items"]))
        self.assertEqual(body["items"][0]["cityId"], "KR-Donghae")
        self.assertEqual(body["items"][0]["badge"], "7월 바다")

    def test_monthly_cities_keeps_missing_image_as_null(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/monthly-cities", {"country": "KR", "limit": "3"}),
            city_repository=FakeCityRepository(),
            now=datetime(2026, 7, 1, 1, 0),
        )
        body = json.loads(response["body"])

        missing_image_item = next(item for item in body["items"] if item["cityId"] == "KR-Jecheon")
        self.assertIsNone(missing_image_item["imageUrl"])

    def test_reaction_cities_requires_authentication(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/reaction-cities", {"limit": "6"}),
            city_repository=FakeCityRepository(),
            reaction_repository=FakeReactionRepository(),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")

    def test_reaction_cities_returns_empty_items_without_user_reactions(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/reaction-cities", {"limit": "6"}, user_id="user-1"),
            city_repository=FakeCityRepository(),
            reaction_repository=FakeReactionRepository(),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["items"], [])

    def test_reaction_cities_recommends_from_liked_itinerary_signals(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/reaction-cities", {"limit": "6"}, user_id="user-1"),
            city_repository=FakeCityRepository(),
            reaction_repository=FakeReactionRepository([
                {
                    "sourceReaction": {
                        "itineraryId": "itinerary-1",
                        "reaction": "like",
                        "reactedAt": "2026-07-01T00:00:00Z",
                    },
                    "destination": {"destinationId": "KR-Donghae", "name": "동해시", "country": "KR"},
                    "themes": ["바다", "자연"],
                    "durationLabel": "2박 3일",
                }
            ]),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertGreaterEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["sourceReaction"]["itineraryId"], "itinerary-1")
        self.assertEqual(body["items"][0]["cityId"], "KR-Donghae")
        self.assertIn("바다", body["items"][0]["themes"])

    def test_popular_destinations_aggregates_all_feedback_without_auth(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/popular-destinations", {"limit": "2"}),
            city_repository=FakeCityRepository(),
            reaction_repository=FakeReactionRepository(popular_signals=[
                {
                    "userId": "user-1",
                    "destination": {"destinationId": "KR-Andong", "name": "안동시", "country": "KR"},
                    "themes": ["전통"],
                    "reactionCount": 2,
                    "savedPlanCount": 1,
                    "birthDate": "1991-03-01",
                },
                {
                    "userId": "user-2",
                    "destination": {"destinationId": "KR-Donghae", "name": "동해시", "country": "KR"},
                    "themes": ["바다", "자연"],
                    "reactionCount": 3,
                    "savedPlanCount": 1,
                    "birthDate": "1993-07-11",
                },
                {
                    "userId": "user-3",
                    "destination": {"destinationId": "KR-Donghae", "name": "동해시", "country": "KR"},
                    "themes": ["바다"],
                    "reactionCount": 4,
                    "savedPlanCount": 1,
                    "birthDate": "1994-10-21",
                    "sourceReaction": {"itineraryId": "private-itinerary"},
                    "title": "노출되면 안 되는 private 일정 제목",
                },
            ]),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["items"][0]["cityId"], "KR-Donghae")
        self.assertEqual(body["items"][0]["name"], "동해시")
        self.assertEqual(body["items"][0]["reactionCount"], 7)
        self.assertEqual(body["items"][0]["savedPlanCount"], 2)
        self.assertIn("바다", body["items"][0]["themes"])
        self.assertNotIn("sourceReaction", body["items"][0])
        self.assertNotIn("itineraryId", json.dumps(body["items"][0], ensure_ascii=False))
        self.assertNotIn("private 일정 제목", json.dumps(body["items"][0], ensure_ascii=False))
        self.assertEqual(body["ageGroups"][0]["label"], "30대")
        self.assertEqual(body["ageGroups"][0]["items"][0]["cityId"], "KR-Donghae")
        self.assertNotIn("user", json.dumps(body["ageGroups"], ensure_ascii=False).lower())

    def test_popular_destinations_returns_empty_slots_source_when_no_feedback(self):
        response = handle_request(
            event("GET", "/api/v1/recommendations/popular-destinations", {"limit": "6"}),
            city_repository=FakeCityRepository(),
            reaction_repository=FakeReactionRepository(),
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["items"], [])
        self.assertEqual(body["ageGroups"], [])


if __name__ == "__main__":
    unittest.main()

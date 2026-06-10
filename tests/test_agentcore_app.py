import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentcore.app import handle_request


def make_event(body, headers=None):
    return {
        "rawPath": "/api/v1/recommendations",
        "headers": headers or {},
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(body),
    }


class AgentCoreMockAppTest(unittest.TestCase):
    def test_returns_mock_recommendation_without_bedrock_call(self):
        response = handle_request(
            make_event(
                {
                    "entryType": "map_marker",
                    "destinationId": "KR-Gangneung",
                    "country": "KR",
                    "tripType": "2d1n",
                    "themes": ["food_local"],
                    "includeFestivals": True,
                    "naturalLanguageQuery": "바다와 미식 중심 일정",
                }
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(body["mock"])
        self.assertEqual(body["destination"]["destinationId"], "KR-Gangneung")
        self.assertEqual(body["saveCompatibility"]["targetEndpoint"], "/api/v1/me/itineraries")
        self.assertIn("itinerary", body)
        self.assertIn("days", body["itinerary"])
        self.assertNotIn("bedrockAgentCore", response["body"])

    def test_validates_required_map_marker_destination(self):
        response = handle_request(
            make_event(
                {
                    "entryType": "map_marker",
                    "country": "KR",
                    "tripType": "2d1n",
                    "themes": ["food_local"],
                    "includeFestivals": True,
                }
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")

    def test_rejects_unsupported_country(self):
        response = handle_request(
            make_event(
                {
                    "entryType": "chat",
                    "country": "US",
                    "tripType": "2d1n",
                    "themes": ["food_local"],
                    "includeFestivals": True,
                }
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()

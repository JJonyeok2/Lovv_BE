import json
import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

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
    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {"MOCK_RECOMMENDATION": "true"}, clear=False)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

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

    def test_agentcore_failure_returns_error_without_savable_mock_or_internal_detail(self):
        with patch.dict(os.environ, {"MOCK_RECOMMENDATION": "false"}, clear=False):
            with patch("agentcore.app._invoke_bedrock_agent", side_effect=RuntimeError("secret backend failure")):
                response = handle_request(
                    make_event(
                        {
                            "entryType": "chat",
                            "country": "KR",
                            "tripType": "2d1n",
                            "themes": ["food_local"],
                            "includeFestivals": True,
                            "naturalLanguageQuery": "바다와 미식 중심 일정",
                        }
                    )
                )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 502)
        self.assertEqual(body["error"]["code"], "AGENTCORE_UNAVAILABLE")
        self.assertNotIn("secret backend failure", response["body"])
        self.assertNotIn("saveCompatibility", response["body"])

    def test_agentcore_response_is_enriched_with_openrouteservice_route_when_configured(self):
        agentcore_payload = {
            "result": {
                "destination": {
                    "destinationId": "KR-Gangneung",
                    "name": "강릉",
                    "country": "KR",
                    "region": "강원",
                },
                "itinerary": {
                    "tripType": "2d1n",
                    "days": [
                        {
                            "day": 1,
                            "items": [
                                {"title": "안목해변", "latitude": 37.771, "longitude": 128.947},
                                {"title": "경포해변", "latitude": 37.805, "longitude": 128.908},
                            ],
                        }
                    ],
                },
                "explainability": {"itineraryFlowReason": "해안 동선"},
            }
        }

        class FakeBedrockClient:
            def invoke_agent_runtime(self, **kwargs):
                return {"response": BytesIO(json.dumps(agentcore_payload).encode("utf-8"))}

        ors_response = {
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[128.947, 37.771], [128.908, 37.805]],
                    },
                    "properties": {
                        "summary": {"distance": 4200, "duration": 780},
                        "segments": [{"distance": 4200, "duration": 780}],
                    },
                }
            ]
        }

        with patch.dict(
            os.environ,
            {
                "MOCK_RECOMMENDATION": "false",
                "BEDROCK_AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
                "OPENROUTESERVICE_API_KEY": "test-ors-key",
            },
            clear=False,
        ):
            with patch("agentcore.app._get_bedrock_client", return_value=FakeBedrockClient()):
                with patch("agentcore.routing._post_json", return_value=ors_response):
                    response = handle_request(
                        make_event(
                            {
                                "entryType": "chat",
                                "country": "KR",
                                "tripType": "2d1n",
                                "themes": ["sea_coast"],
                                "includeFestivals": False,
                                "naturalLanguageQuery": "해안 산책 일정",
                            }
                        )
                    )

        body = json.loads(response["body"])
        day = body["itinerary"]["days"][0]

        self.assertEqual(response["statusCode"], 200)
        self.assertFalse(body["mock"])
        self.assertEqual(day["route"]["provider"], "openrouteservice")
        self.assertEqual(day["route"]["distanceMeters"], 4200)
        self.assertEqual(day["items"][0]["moveMinutes"], 13)
        self.assertEqual(body["saveCompatibility"]["payload"]["itinerary"]["days"][0]["route"]["provider"], "openrouteservice")
        self.assertNotIn("mock", body["itinerary"]["title"].lower())
        self.assertNotIn("mock", body["itinerary"]["summary"].lower())
        self.assertNotIn("mock", body["saveCompatibility"]["payload"]["title"].lower())
        self.assertNotIn("mock", body["saveCompatibility"]["payload"]["summary"].lower())
        self.assertEqual(body["saveCompatibility"]["payload"]["title"], body["itinerary"]["title"])
        self.assertEqual(body["saveCompatibility"]["payload"]["summary"], body["itinerary"]["summary"])

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

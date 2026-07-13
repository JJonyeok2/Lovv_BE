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

    def test_accepts_frontend_create_payload_for_agentcore_v1(self):
        response = handle_request(
            make_event(
                {
                    "entryType": "create",
                    "requestId": "frontend-request-1",
                    "rawQuery": "경주 1박 2일",
                    "destinationId": "KR-Gyeongju",
                    "executionMode": "anchored_place_search",
                    "activeRequiredThemes": ["역사·전통"],
                    "country": "KR",
                    "travelYear": 2026,
                    "travelMonth": 7,
                    "tripType": "2d1n",
                    "includeFestivals": False,
                    "userLocation": None,
                }
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["requestSnapshot"]["entryType"], "create")
        self.assertEqual(body["requestSnapshot"]["themes"], ["history_tradition"])
        self.assertEqual(body["requestSnapshot"]["naturalLanguageQuery"], "경주 1박 2일")
        self.assertEqual(body["destination"]["destinationId"], "KR-Gyeongju")

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

    def test_invokes_agentcore_with_v2_top_level_payload(self):
        captured = {}
        agentcore_payload = {
            "selectedDestination": {
                "destinationId": "KR-Gyeongju",
                "name": "경주",
                "country": "KR",
                "region": "경북",
            },
            "itinerary": {
                "tripType": "2d1n",
                "days": [{"day": 1, "items": [{"title": "황리단길"}]}],
            },
            "recommendationReasons": ["역사 테마와 잘 맞습니다."],
            "itineraryFlowReason": "도보 동선을 먼저 배치했습니다.",
            "confidence": 0.87,
            "user_notice": "운영 시간은 방문 전 확인하세요.",
            "festivalDateVerifications": [{"festivalId": "festival-1", "dateStatus": "confirmed"}],
            "externalLinks": {"map": "https://maps.example/gyeongju", "staySearch": "https://stay.example/gyeongju"},
            "alternativeItinerary": {"trigger": "rain", "days": []},
        }

        class FakeBedrockClient:
            def invoke_agent_runtime(self, **kwargs):
                captured.update(kwargs)
                return {"response": BytesIO(json.dumps(agentcore_payload).encode("utf-8"))}

        with patch.dict(
            os.environ,
            {
                "MOCK_RECOMMENDATION": "false",
                "BEDROCK_AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
            },
            clear=False,
        ):
            with patch("agentcore.app._get_bedrock_client", return_value=FakeBedrockClient()):
                response = handle_request(
                    make_event(
                        {
                            "entryType": "create",
                            "requestId": "frontend-request-1",
                            "rawQuery": "경주 1박 2일",
                            "destinationId": "KR-Gyeongju",
                            "executionMode": "anchored_place_search",
                            "activeRequiredThemes": ["역사·전통"],
                            "country": "KR",
                            "travelYear": 2026,
                            "travelMonth": 7,
                            "tripType": "2d1n",
                            "includeFestivals": False,
                            "userLocation": None,
                        }
                    )
                )

        request_payload = json.loads(captured["payload"].decode("utf-8"))
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("request", request_payload)
        self.assertEqual(request_payload["session_id"], captured["runtimeSessionId"])
        self.assertEqual(request_payload["recommendation_request_id"], "frontend-request-1")
        self.assertEqual(request_payload["sessionId"], captured["runtimeSessionId"])
        self.assertEqual(request_payload["threadId"], captured["runtimeSessionId"])
        self.assertEqual(request_payload["entryType"], "create")
        self.assertEqual(request_payload["requestId"], "frontend-request-1")
        self.assertEqual(request_payload["destinationId"], "KR-Gyeongju")
        self.assertEqual(request_payload["themes"], ["history_tradition"])
        self.assertEqual(request_payload["activeRequiredThemes"], ["역사·전통"])
        self.assertEqual(request_payload["naturalLanguageQuery"], "경주 1박 2일")
        self.assertEqual(request_payload["rawQuery"], "경주 1박 2일")
        self.assertEqual(request_payload["onboardingProfile"], {"themes": ["history_tradition"]})
        self.assertEqual(request_payload["feedbackHistory"], [])
        self.assertEqual(body["destination"]["destinationId"], "KR-Gyeongju")
        self.assertEqual(body["explainability"]["recommendationReasons"], ["역사 테마와 잘 맞습니다."])
        self.assertEqual(body["explainability"]["itineraryFlowReason"], "도보 동선을 먼저 배치했습니다.")
        self.assertEqual(body["explanations"]["userNotice"], "운영 시간은 방문 전 확인하세요.")
        self.assertEqual(body["festivalDateVerifications"][0]["festivalId"], "festival-1")
        self.assertEqual(body["links"]["map"], "https://maps.example/gyeongju")
        self.assertEqual(body["alternativeItinerary"]["trigger"], "rain")
        self.assertEqual(body["saveCompatibility"]["payload"]["alternativeItinerary"]["trigger"], "rain")
        self.assertEqual(body["saveCompatibility"]["payload"]["links"]["staySearch"], "https://stay.example/gyeongju")

    def test_accepts_frontend_clarify_payload_for_agentcore_v2(self):
        captured = {}
        agentcore_payload = {
            "threadId": "thread-001",
            "recommendationId": "rec-001",
            "clarification": {
                "question": "숙박 중심으로 더 좁힐까요?",
                "options": [{"optionId": "stay", "label": "숙박 중심"}],
            },
        }

        class FakeBedrockClient:
            def invoke_agent_runtime(self, **kwargs):
                captured.update(kwargs)
                return {"response": BytesIO(json.dumps(agentcore_payload).encode("utf-8"))}

        with patch.dict(
            os.environ,
            {
                "MOCK_RECOMMENDATION": "false",
                "BEDROCK_AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
            },
            clear=False,
        ):
            with patch("agentcore.app._get_bedrock_client", return_value=FakeBedrockClient()):
                response = handle_request(
                    make_event(
                        {
                            "entryType": "clarify",
                            "threadId": "thread-001",
                            "recommendationId": "rec-001",
                            "selectedOptionId": "stay",
                        }
                    )
                )

        request_payload = json.loads(captured["payload"].decode("utf-8"))
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(request_payload["entryType"], "clarify")
        self.assertEqual(request_payload["sessionId"], "thread-001")
        self.assertEqual(request_payload["threadId"], "thread-001")
        self.assertEqual(request_payload["recommendationId"], "rec-001")
        self.assertEqual(request_payload["selectedOptionId"], "stay")
        self.assertEqual(body["threadId"], "thread-001")
        self.assertEqual(body["recommendationId"], "rec-001")
        self.assertEqual(body["clarification"]["question"], "숙박 중심으로 더 좁힐까요?")

    def test_invokes_agentcore_modify_with_v2_current_order(self):
        captured = {}
        current_order = [
            {
                "itemId": "item-1",
                "contentId": "A-100",
                "itemType": "attraction",
                "day": 1,
                "order": 1,
                "title": "묵호등대",
                "cityId": "KR-32-3",
                "theme": "바다·해안",
            }
        ]
        agentcore_payload = {
            "recommendationId": "req-modify-001",
            "destination": {"destinationId": "KR-32-3", "name": "동해시", "country": "KR"},
            "itinerary": {"tripType": "2d1n", "days": [{"day": 1, "items": current_order}]},
            "explainability": {"userNotice": "수정했습니다."},
            "festivalDateVerifications": [],
            "links": {},
        }

        class FakeBedrockClient:
            def invoke_agent_runtime(self, **kwargs):
                captured.update(kwargs)
                return {"response": BytesIO(json.dumps(agentcore_payload).encode("utf-8"))}

        with patch.dict(
            os.environ,
            {
                "MOCK_RECOMMENDATION": "false",
                "BEDROCK_AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
            },
            clear=False,
        ):
            with patch("agentcore.app._get_bedrock_client", return_value=FakeBedrockClient()):
                response = handle_request(
                    make_event(
                        {
                            "entryType": "modify",
                            "requestId": "req-modify-001",
                            "sessionId": "session-123456789012345678901234567",
                            "threadId": "session-123456789012345678901234567",
                            "destinationId": "KR-32-3",
                            "rawModifyQuery": "1일차 첫 장소를 더 조용한 곳으로 바꿔줘.",
                            "currentOrder": current_order,
                        }
                    )
                )

        request_payload = json.loads(captured["payload"].decode("utf-8"))
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(request_payload["entryType"], "modify")
        self.assertEqual(request_payload["sessionId"], "session-123456789012345678901234567")
        self.assertEqual(request_payload["threadId"], "session-123456789012345678901234567")
        self.assertEqual(request_payload["session_id"], "session-123456789012345678901234567")
        self.assertEqual(request_payload["recommendation_request_id"], "req-modify-001")
        self.assertEqual(request_payload["itineraryRevision"], "req-modify-001")
        self.assertEqual(request_payload["rawModifyQuery"], "1일차 첫 장소를 더 조용한 곳으로 바꿔줘.")
        self.assertEqual(request_payload["currentOrder"], current_order)
        self.assertEqual(body["itinerary"]["days"][0]["items"], current_order)

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

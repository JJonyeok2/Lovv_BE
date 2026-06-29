import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentcore.routing import _post_json, enrich_itinerary_routes


class AgentCoreRoutingTest(unittest.TestCase):
    def test_enriches_itinerary_day_with_openrouteservice_geometry_and_leg_summary(self):
        itinerary = {
            "days": [
                {
                    "day": 1,
                    "items": [
                        {"title": "안목해변", "latitude": 37.771, "longitude": 128.947},
                        {"title": "경포해변", "latitude": 37.805, "longitude": 128.908},
                    ],
                }
            ]
        }

        ors_response = {
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[128.947, 37.771], [128.908, 37.805]],
                    },
                    "properties": {
                        "summary": {"distance": 4200.5, "duration": 780.2},
                        "segments": [
                            {"distance": 4200.5, "duration": 780.2},
                        ],
                    },
                }
            ]
        }

        def fake_post_json(url, api_key, body, timeout_seconds):
            self.assertEqual(api_key, "test-ors-key")
            self.assertIn("/v2/directions/foot-walking/geojson", url)
            self.assertEqual(body["coordinates"], [[128.947, 37.771], [128.908, 37.805]])
            self.assertTrue(body["instructions"])
            self.assertEqual(timeout_seconds, 4.0)
            return ors_response

        with patch.dict(
            os.environ,
            {
                "OPENROUTESERVICE_API_KEY": "test-ors-key",
                "OPENROUTESERVICE_PROFILE": "foot-walking",
                "OPENROUTESERVICE_TIMEOUT_SECONDS": "4",
            },
            clear=False,
        ):
            with patch("agentcore.routing._post_json", side_effect=fake_post_json) as post_json:
                enriched = enrich_itinerary_routes(itinerary)

        day = enriched["days"][0]
        self.assertEqual(post_json.call_count, 1)
        self.assertEqual(day["route"]["provider"], "openrouteservice")
        self.assertEqual(day["route"]["profile"], "foot-walking")
        self.assertEqual(day["route"]["geometry"]["coordinates"], [[128.947, 37.771], [128.908, 37.805]])
        self.assertEqual(day["route"]["distanceMeters"], 4200)
        self.assertEqual(day["route"]["durationSeconds"], 780)
        self.assertEqual(day["items"][0]["moveMinutes"], 13)
        self.assertEqual(day["items"][0]["moveDistanceMeters"], 4200)

    def test_skips_route_enrichment_when_openrouteservice_key_is_missing(self):
        itinerary = {
            "days": [
                {
                    "day": 1,
                    "items": [
                        {"title": "A", "latitude": 37.1, "longitude": 127.1},
                        {"title": "B", "latitude": 37.2, "longitude": 127.2},
                    ],
                }
            ]
        }

        with patch.dict(os.environ, {"OPENROUTESERVICE_API_KEY": ""}, clear=False):
            with patch("agentcore.routing._post_json") as post_json:
                enriched = enrich_itinerary_routes(itinerary)

        self.assertIs(enriched, itinerary)
        self.assertNotIn("route", enriched["days"][0])
        post_json.assert_not_called()

    def test_reads_openrouteservice_key_from_ssm_name_when_direct_env_key_is_missing(self):
        itinerary = {
            "days": [
                {
                    "day": 1,
                    "items": [
                        {"title": "A", "latitude": 37.1, "longitude": 127.1},
                        {"title": "B", "latitude": 37.2, "longitude": 127.2},
                    ],
                }
            ]
        }
        ors_response = {
            "features": [
                {
                    "geometry": {"type": "LineString", "coordinates": [[127.1, 37.1], [127.2, 37.2]]},
                    "properties": {
                        "summary": {"distance": 1000, "duration": 180},
                        "segments": [{"distance": 1000, "duration": 180}],
                    },
                }
            ]
        }

        with patch.dict(
            os.environ,
            {
                "OPENROUTESERVICE_API_KEY": "",
                "OPENROUTESERVICE_API_KEY_SSM_NAME": "/lovv/dev/openrouteservice/api_key",
            },
            clear=False,
        ):
            with patch("agentcore.routing._get_ssm_parameter", return_value="ssm-ors-key", create=True) as get_parameter:
                with patch("agentcore.routing._post_json", return_value=ors_response) as post_json:
                    enriched = enrich_itinerary_routes(itinerary)

        self.assertEqual(get_parameter.call_args.args[0], "/lovv/dev/openrouteservice/api_key")
        self.assertEqual(post_json.call_args.kwargs.get("api_key") or post_json.call_args.args[1], "ssm-ors-key")
        self.assertEqual(enriched["days"][0]["route"]["provider"], "openrouteservice")

    def test_skips_days_without_two_valid_coordinates(self):
        itinerary = {
            "days": [
                {
                    "day": 1,
                    "items": [
                        {"title": "A", "latitude": 37.1, "longitude": 127.1},
                        {"title": "B", "latitude": None, "longitude": None},
                    ],
                }
            ]
        }

        with patch.dict(os.environ, {"OPENROUTESERVICE_API_KEY": "test-ors-key"}, clear=False):
            with patch("agentcore.routing._post_json") as post_json:
                enriched = enrich_itinerary_routes(itinerary)

        self.assertIs(enriched, itinerary)
        self.assertNotIn("route", enriched["days"][0])
        post_json.assert_not_called()

    def test_post_json_requests_openrouteservice_geojson_response_format(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"features":[]}'

        def fake_urlopen(http_request, timeout):
            captured["accept"] = http_request.get_header("Accept")
            captured["authorization"] = http_request.get_header("Authorization")
            captured["content_type"] = http_request.get_header("Content-type")
            captured["timeout"] = timeout
            captured["body"] = http_request.data
            return FakeResponse()

        with patch("agentcore.routing.request.urlopen", side_effect=fake_urlopen):
            response = _post_json(
                "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                "test-ors-key",
                {"coordinates": [[128.947, 37.771], [128.908, 37.805]]},
                4.0,
            )

        self.assertEqual(response, {"features": []})
        self.assertEqual(captured["accept"], "application/geo+json")
        self.assertEqual(captured["authorization"], "test-ors-key")
        self.assertEqual(captured["content_type"], "application/json")
        self.assertEqual(captured["timeout"], 4.0)
        self.assertIn(b'"coordinates"', captured["body"])


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from small_cities.app import handle_request
from small_cities.s3_raw_repository import S3RawCityRepository


class FakeS3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "fake s3 error"}}


class FakeS3Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class FakeS3Client:
    def __init__(self, objects=None, list_error=None, get_error=None):
        self.objects = objects or {}
        self.list_error = list_error
        self.get_error = get_error

    def list_objects_v2(self, **kwargs):
        if self.list_error:
            raise self.list_error
        prefix = kwargs["Prefix"]
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]}

    def get_object(self, Bucket, Key):
        if self.get_error:
            raise self.get_error
        return {"Body": FakeS3Body(self.objects[Key])}


def s3_repository(fake_client):
    return S3RawCityRepository(
        bucket="bucket",
        prefix="raw/KR/details/20260609/",
        s3_client=fake_client,
    )


class FakeRepository:
    def __init__(self):
        self.records = [
            {
                "id": "KR-Gangneung",
                "country": "KR",
                "country_label": "한국",
                "region": "강원",
                "name_ko": "강릉",
                "name_local": "강릉시",
                "latitude": 37.74,
                "longitude": 128.86,
                "themes": ["미식", "바다"],
                "summary": "강릉은 미식과 바다 여행 후보입니다.",
                "detail": "강릉커피축제와 해변을 기준으로 추천합니다.",
                "highlights": ["강릉커피축제", "안목해변"],
                "route_seed": ["강릉커피축제", "안목해변"],
                "internal_meta": {"source": "S3RawCityDetails"},
            },
            {
                "id": "KR-Andong",
                "country": "KR",
                "country_label": "한국",
                "region": "경북",
                "name_ko": "안동",
                "name_local": "안동시",
                "latitude": 36.56,
                "longitude": 128.72,
                "themes": ["전통"],
                "summary": "안동은 전통 여행 후보입니다.",
                "detail": "하회마을을 기준으로 추천합니다.",
                "highlights": ["하회마을"],
                "route_seed": ["하회마을"],
                "internal_meta": {"source": "S3RawCityDetails"},
            },
            {
                "id": "JP-Otaru",
                "country": "JP",
                "country_label": "일본",
                "region": "홋카이도",
                "name_ko": "오타루",
                "name_local": "小樽",
                "latitude": 43.19,
                "longitude": 140.99,
                "themes": ["미식", "바다"],
                "summary": "오타루는 미식과 바다 여행 후보입니다.",
                "detail": "운하와 스시거리를 기준으로 추천합니다.",
                "highlights": ["오타루 운하", "스시거리"],
                "route_seed": ["오타루 운하", "스시거리"],
                "internal_meta": {"source": "S3RawCityDetails"},
            },
        ]
        self.places = {
            "KR-Gangneung": {
                "cityId": "KR-Gangneung",
                "cityName": "강릉",
                "summary": {
                    "attractionCount": 1,
                    "festivalCount": 1,
                    "visitorStatisticsCount": 1,
                },
                "attractions": [
                    {
                        "placeId": "ATT-1",
                        "type": "attraction",
                        "title": "안목해변",
                        "imageUrl": "https://example.com/beach.jpg",
                        "latitude": 37.77,
                        "longitude": 128.95,
                    }
                ],
                "festivals": [
                    {
                        "placeId": "FEST-1",
                        "type": "festival",
                        "title": "강릉커피축제",
                        "imageUrl": "https://example.com/coffee.jpg",
                        "latitude": 37.76,
                        "longitude": 128.90,
                    }
                ],
            }
        }

    def list_city_records(self):
        return self.records

    def get_city_record(self, city_id):
        return next((record for record in self.records if record["id"] == city_id), None)

    def get_city_places(self, city_id):
        return self.places.get(city_id)


class SmallCityHandlerTest(unittest.TestCase):
    def test_handles_small_city_list_request(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"country": "KR", "q": "커피", "themes": "미식", "page": "1", "page_size": "20"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["page"], {"page": 1, "pageSize": 20, "total": 1, "hasNext": False})
        self.assertEqual(body["data"][0]["id"], "KR-Gangneung")

    def test_v1_small_city_list_alias_keeps_existing_response_shape(self):
        event = {
            "rawPath": "/api/v1/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"country": "KR", "themes": "전통"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(set(body.keys()), {"data", "page"})
        self.assertEqual(body["data"][0]["id"], "KR-Andong")

    def test_v1_map_markers_return_coordinate_projection(self):
        event = {
            "rawPath": "/api/v1/map/markers",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"country": "KR", "page_size": "20"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["page"], {"page": 1, "pageSize": 20, "total": 2, "hasNext": False})
        marker = body["data"][0]
        self.assertEqual(set(marker.keys()), {"cityId", "name", "country", "countryLabel", "region", "latitude", "longitude", "imageUrl"})
        self.assertEqual(marker["cityId"], "KR-Gangneung")
        self.assertNotIn("summary", marker)
        self.assertNotIn("internal_meta", marker)

    def test_composes_country_theme_and_search_filters_with_list_envelope(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"country": "KR", "q": "해변", "themes": "미식,바다", "page": "1", "page_size": "1"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(set(body.keys()), {"data", "page"})
        self.assertEqual(body["page"], {"page": 1, "pageSize": 1, "total": 1, "hasNext": False})
        self.assertEqual([city["id"] for city in body["data"]], ["KR-Gangneung"])
        self.assertIn("summary", body["data"][0])
        self.assertIn("detail", body["data"][0])
        self.assertIn("themes", body["data"][0])
        self.assertIn("highlights", body["data"][0])
        self.assertIn("route_seed", body["data"][0])

    def test_handles_small_city_detail_request(self):
        event = {
            "rawPath": "/api/small-cities/KR-Gangneung",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["data"]["id"], "KR-Gangneung")
        self.assertEqual(body["data"]["internal_meta"]["source"], "S3RawCityDetails")

    def test_v1_map_city_detail_alias_reads_city_id_from_path(self):
        event = {
            "rawPath": "/api/v1/map/cities/KR-Gangneung",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["data"]["id"], "KR-Gangneung")

    def test_small_city_places_returns_s3_raw_attractions_and_festivals(self):
        event = {
            "rawPath": "/api/small-cities/KR-Gangneung/places",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["cityId"], "KR-Gangneung")
        self.assertNotIn("sourceKey", body)
        self.assertEqual(body["summary"], {"attractionCount": 1, "festivalCount": 1, "visitorStatisticsCount": 1})
        self.assertEqual(body["attractions"][0]["placeId"], "ATT-1")
        self.assertEqual(body["festivals"][0]["type"], "festival")

    def test_options_returns_credential_compatible_cors_headers(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "OPTIONS"}},
            "queryStringParameters": None,
        }

        response = handle_request(event, repository=FakeRepository())

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(response["headers"]["Access-Control-Allow-Credentials"], "true")

    def test_public_get_uses_shared_cors_headers(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        }

        response = handle_request(event, repository=FakeRepository())

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertEqual(response["headers"]["Access-Control-Allow-Credentials"], "true")

    def test_s3_no_such_key_returns_not_found_for_detail_and_places(self):
        repository = s3_repository(FakeS3Client(get_error=FakeS3Error("NoSuchKey")))
        detail_event = {
            "rawPath": "/api/small-cities/KR-Gangneung",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }
        places_event = {
            "rawPath": "/api/small-cities/KR-Gangneung/places",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        detail_response = handle_request(detail_event, repository=repository)
        places_response = handle_request(places_event, repository=repository)
        detail_body = json.loads(detail_response["body"])
        places_body = json.loads(places_response["body"])

        self.assertEqual(detail_response["statusCode"], 404)
        self.assertEqual(places_response["statusCode"], 404)
        self.assertEqual(detail_body["error"]["code"], "NOT_FOUND")
        self.assertEqual(places_body["error"]["code"], "NOT_FOUND")

    def test_s3_access_denied_returns_upstream_error_for_detail_and_places(self):
        repository = s3_repository(FakeS3Client(get_error=FakeS3Error("AccessDenied")))
        detail_event = {
            "rawPath": "/api/small-cities/KR-Gangneung",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }
        places_event = {
            "rawPath": "/api/small-cities/KR-Gangneung/places",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        detail_response = handle_request(detail_event, repository=repository)
        places_response = handle_request(places_event, repository=repository)
        detail_body = json.loads(detail_response["body"])
        places_body = json.loads(places_response["body"])

        self.assertEqual(detail_response["statusCode"], 502)
        self.assertEqual(places_response["statusCode"], 502)
        self.assertEqual(detail_body["error"]["code"], "UPSTREAM_UNAVAILABLE")
        self.assertEqual(places_body["error"]["code"], "UPSTREAM_UNAVAILABLE")

    def test_s3_invalid_json_returns_safe_error_for_detail_and_places(self):
        repository = s3_repository(FakeS3Client({"raw/KR/details/20260609/Gangneung.json": b"{not-json"}))
        detail_event = {
            "rawPath": "/api/small-cities/KR-Gangneung",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }
        places_event = {
            "rawPath": "/api/small-cities/KR-Gangneung/places",
            "pathParameters": {"cityId": "KR-Gangneung"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        detail_response = handle_request(detail_event, repository=repository)
        places_response = handle_request(places_event, repository=repository)
        detail_body = json.loads(detail_response["body"])
        places_body = json.loads(places_response["body"])

        self.assertEqual(detail_response["statusCode"], 500)
        self.assertEqual(places_response["statusCode"], 500)
        self.assertEqual(detail_body["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(places_body["error"]["code"], "INTERNAL_ERROR")

    def test_s3_list_failure_returns_upstream_error(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        }
        repository = s3_repository(FakeS3Client(list_error=FakeS3Error("SlowDown")))

        response = handle_request(event, repository=repository)
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 502)
        self.assertEqual(body["error"]["code"], "UPSTREAM_UNAVAILABLE")

    def test_rejects_invalid_query_parameters_before_repository_work(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"country": "US", "page": "0", "page_size": "500"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "INVALID_QUERY")

    def test_rejects_unsupported_theme_label(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"themes": "history_tradition"},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "INVALID_QUERY")

    def test_rejects_overlong_search_query(self):
        event = {
            "rawPath": "/api/small-cities",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {"q": "가" * 81},
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "INVALID_QUERY")

    def test_returns_not_found_for_unknown_city_detail(self):
        event = {
            "rawPath": "/api/small-cities/KR-Unknown",
            "pathParameters": {"cityId": "KR-Unknown"},
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": None,
        }

        response = handle_request(event, repository=FakeRepository())
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()

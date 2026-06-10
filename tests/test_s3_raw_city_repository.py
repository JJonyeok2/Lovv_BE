import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from small_cities.s3_raw_repository import S3RawCityRepository


class FakeBody:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeS3Client:
    def __init__(self, objects):
        self.objects = objects

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        contents = [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]
        return {"Contents": contents}

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}


def raw_city():
    city_record = {
        "city_id": "KR-Gangneung",
        "city_name_en": "Gangneung",
        "city_name_ko": "강릉시",
        "province": "강원특별자치도",
        "attraction_count": 1,
        "festival_count": 1,
        "visitor_statistics_count": 1,
    }
    return {
        "city_id": "KR-Gangneung",
        "city_name_en": "Gangneung",
        "city_record": city_record,
        "records": [
            {
                "entity_id": "ATT-1",
                "entity_type": "attraction",
                "title": "안목해변",
                "description": "바다 산책",
                "address": "강원 강릉",
                "image_url": "https://example.com/beach.jpg",
                "latitude": 37.77,
                "longitude": 128.95,
                "theme": "바다·해안",
                "theme_tags": ["바다·해안"],
            },
            {
                "entity_id": "FEST-1",
                "entity_type": "festival",
                "title": "강릉커피축제",
                "description": "커피 축제",
                "image_url": "https://example.com/coffee.jpg",
                "latitude": 37.76,
                "longitude": 128.90,
                "theme": "미식·노포",
                "theme_tags": ["미식·노포"],
                "eventstartdate": "2026-10-01",
                "eventenddate": "2026-10-03",
            },
            {
                "entity_id": "STAT-1",
                "entity_type": "visitor_statistics",
                "statistics": {"month": "2026-01", "total_visitors": 1000},
            },
        ],
    }


class S3RawCityRepositoryTest(unittest.TestCase):
    def test_lists_city_records_from_raw_json_objects(self):
        repository = S3RawCityRepository(
            bucket="bucket",
            prefix="raw/KR/details/20260609/",
            s3_client=FakeS3Client({"raw/KR/details/20260609/Gangneung.json": raw_city()}),
        )

        records = repository.list_city_records()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "KR-Gangneung")
        self.assertEqual(records[0]["internal_meta"]["source"], "S3RawCityDetails")
        self.assertEqual(records[0]["internal_meta"]["sourceKey"], "raw/KR/details/20260609/Gangneung.json")

    def test_returns_places_from_s3_raw_attractions_and_festivals_only(self):
        repository = S3RawCityRepository(
            bucket="bucket",
            prefix="raw/KR/details/20260609/",
            s3_client=FakeS3Client({"raw/KR/details/20260609/Gangneung.json": raw_city()}),
        )

        places = repository.get_city_places("KR-Gangneung")

        self.assertEqual(places["cityId"], "KR-Gangneung")
        self.assertNotIn("sourceKey", places)
        self.assertEqual(places["summary"], {"attractionCount": 1, "festivalCount": 1, "visitorStatisticsCount": 1})
        self.assertEqual([place["placeId"] for place in places["attractions"]], ["ATT-1"])
        self.assertEqual([place["placeId"] for place in places["festivals"]], ["FEST-1"])
        self.assertNotIn("visitorStatistics", places)


if __name__ == "__main__":
    unittest.main()

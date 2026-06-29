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


def raw_city_actual_s3_shape():
    return {
        "meta": {
            "province": "강원특별자치도",
            "city_name_ko": "강릉시",
            "city_name_en": "Gangneung",
            "scraped_at": "2026-06-05 15:10:05",
        },
        "attractions_count_filtered": 1,
        "festivals_count_filtered": 1,
        "attractions": [
            {
                "contentid": "2868839",
                "title": "가람집옹심이",
                "addr1": "강원특별자치도 강릉시 공항길30번길 16",
                "addr2": "",
                "tel": "0507-1313-3266",
                "firstimage": "http://tong.visitkorea.or.kr/cms/resource/29/2868829_image2_1.jpeg",
                "mapx": "128.9393320379",
                "mapy": "37.7611934162",
                "_assigned_theme": "미식·노포",
                "detail": {
                    "common": {
                        "overview": "강원도 토속 음식점이다.",
                    }
                },
            }
        ],
        "festivals": [
            {
                "contentid": "695592",
                "title": "강릉 경포벚꽃축제",
                "addr1": "강원특별자치도 강릉시 경포로 365",
                "addr2": "경포 습지광장",
                "tel": "033-640-5130",
                "firstimage": "https://tong.visitkorea.or.kr/cms/resource/23/4041323_image2_1.jpg",
                "mapx": "128.895500767487",
                "mapy": "37.7942610311229",
                "_assigned_theme": "자연·트레킹",
                "eventstartdate": "20260404",
                "eventenddate": "20260411",
                "detail": {
                    "common": {
                        "overview": "벚꽃길을 즐길 수 있는 축제이다.",
                    }
                },
            }
        ],
        "visitor_statistics": {
            "year": 2025,
            "monthly_statistics": [{"month": "2025-01"}, {"month": "2025-02"}],
        },
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

    def test_maps_actual_s3_raw_shape_into_city_record_and_places(self):
        image_map = {
            "KR-Gangneung/GangreungAnmokhaematigongwon": "images/KR/Gangneung/GangreungAnmokhaematigongwon_1.jpg",
            "KR-Gangneung/GangreungGyeongpodae": "images/KR/Gangneung/GangreungGyeongpodae_1.jpg",
        }
        repository = S3RawCityRepository(
            bucket="bucket",
            prefix="raw/KR/details/20260609/",
            s3_client=FakeS3Client({"raw/KR/details/20260609/Gangneung.json": raw_city_actual_s3_shape()}),
            cdn_base="https://images.example.com",
            image_map=image_map,
        )

        records = repository.list_city_records()
        places = repository.get_city_places("KR-Gangneung")

        self.assertEqual(records[0]["id"], "KR-Gangneung")
        self.assertEqual(records[0]["name_ko"], "강릉")
        self.assertEqual(records[0]["internal_meta"]["attractionCount"], 1)
        self.assertEqual(records[0]["internal_meta"]["festivalCount"], 1)
        self.assertEqual(records[0]["internal_meta"]["visitorStatisticsCount"], 2)
        self.assertAlmostEqual(records[0]["latitude"], (37.7611934162 + 37.7942610311229) / 2)
        self.assertAlmostEqual(records[0]["longitude"], (128.9393320379 + 128.895500767487) / 2)
        self.assertTrue(records[0]["image_url"].startswith("https://images.example.com/images/KR/Gangneung/"))
        self.assertEqual(places["cityId"], "KR-Gangneung")
        self.assertEqual(places["attractions"][0]["contentId"], "2868839")
        self.assertEqual(places["attractions"][0]["description"], "강원도 토속 음식점이다.")
        self.assertTrue(places["attractions"][0]["imageUrl"].startswith("https://images.example.com/images/KR/Gangneung/"))
        self.assertEqual(places["festivals"][0]["startDate"], "20260404")
        self.assertEqual(places["summary"], {"attractionCount": 1, "festivalCount": 1, "visitorStatisticsCount": 2})

    def test_falls_back_to_tour_api_image_when_s3_city_images_are_unavailable(self):
        repository = S3RawCityRepository(
            bucket="bucket",
            prefix="raw/KR/details/20260609/",
            s3_client=FakeS3Client({"raw/KR/details/20260609/Gangneung.json": raw_city_actual_s3_shape()}),
            cdn_base="https://images.example.com",
            image_map={},
        )

        places = repository.get_city_places("KR-Gangneung")

        self.assertEqual(
            places["attractions"][0]["imageUrl"],
            "https://tong.visitkorea.or.kr/cms/resource/29/2868829_image2_1.jpeg",
        )


if __name__ == "__main__":
    unittest.main()

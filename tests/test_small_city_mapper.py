import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from small_cities.mapper import build_city_api_record, normalize_theme
from small_cities.service import SmallCityService


class SmallCityMapperTest(unittest.TestCase):
    def test_builds_frontend_api_record_from_s3_raw_city_items(self):
        metadata = {
            "PK": "CITY#Gangneung",
            "SK": "METADATA#city",
            "city_id": "KR-Gangneung",
            "city_name_en": "Gangneung",
            "city_name_ko": "강릉시",
            "entity_type": "city",
            "province": "강원특별자치도",
        }
        items = [
            metadata,
            {
                "PK": "CITY#Gangneung",
                "SK": "ATTRACTION#125417",
                "address": "강원특별자치도 강릉시 성산면",
                "city_id": "KR-Gangneung",
                "city_name_en": "Gangneung",
                "content_id": "125417",
                "description": "대관령 숲길을 기준으로 걷기 좋은 자연 휴양지입니다.",
                "entity_id": "ATT-125417",
                "entity_type": "attraction",
                "image_url": "https://example.com/forest.jpg",
                "latitude": 37.72,
                "longitude": 128.83,
                "quality_status": "passed",
                "theme": "자연·트레킹",
                "theme_tags": ["자연·트레킹"],
                "title": "국립대관령자연휴양림",
            },
            {
                "PK": "CITY#Gangneung",
                "SK": "FESTIVAL#825295",
                "address": "강원특별자치도 강릉시",
                "city_id": "KR-Gangneung",
                "city_name_en": "Gangneung",
                "content_id": "825295",
                "description": "강릉의 커피 문화를 중심으로 열리는 가을 축제입니다.",
                "entity_id": "FEST-825295",
                "entity_type": "festival",
                "eventenddate": "2025-11-02",
                "eventstartdate": "2025-10-30",
                "image_url": "https://example.com/coffee.jpg",
                "latitude": 37.76,
                "longitude": 128.90,
                "month": 10,
                "quality_status": "passed",
                "season": "autumn",
                "season_tags": ["autumn"],
                "theme": "미식·노포",
                "theme_tags": ["미식·노포"],
                "title": "강릉커피축제",
                "visit_months": [10, 11],
            },
            {
                "PK": "CITY#Gangneung",
                "SK": "STAT#202501",
                "city_id": "KR-Gangneung",
                "city_name_en": "Gangneung",
                "entity_id": "KR-STAT-KR-Gangneung-202501",
                "entity_type": "visitor_statistics",
                "month": "202501",
                "quality_status": "passed",
                "statistics": {"total_visitors": 5014075.33, "month": "2025-01"},
            },
        ]

        record = build_city_api_record(metadata, items, source="S3RawCityDetails")

        self.assertEqual(record["id"], "KR-Gangneung")
        self.assertEqual(record["country"], "KR")
        self.assertEqual(record["country_label"], "한국")
        self.assertEqual(record["region"], "강원")
        self.assertEqual(record["name_ko"], "강릉")
        self.assertEqual(record["name_local"], "강릉시")
        self.assertAlmostEqual(record["latitude"], 37.74)
        self.assertAlmostEqual(record["longitude"], 128.865)
        self.assertEqual(record["themes"], ["미식", "자연", "축제"])
        self.assertEqual(record["highlights"], ["강릉커피축제", "국립대관령자연휴양림"])
        self.assertEqual(record["route_seed"], ["강릉커피축제", "국립대관령자연휴양림"])
        self.assertEqual(record["image_url"], "https://example.com/coffee.jpg")
        self.assertIn("축제 1건", record["detail"])
        self.assertEqual(record["internal_meta"]["source"], "S3RawCityDetails")
        self.assertEqual(record["internal_meta"]["attractionCount"], 1)
        self.assertEqual(record["internal_meta"]["festivalCount"], 1)
        self.assertEqual(record["internal_meta"]["visitorStatisticsCount"], 1)

    def test_normalizes_pipeline_theme_labels_to_frontend_theme_labels(self):
        self.assertEqual(normalize_theme("자연·트레킹"), "자연")
        self.assertEqual(normalize_theme("미식·노포"), "미식")
        self.assertEqual(normalize_theme("역사·전통"), "전통")
        self.assertEqual(normalize_theme("바다·해안"), "바다")
        self.assertEqual(normalize_theme("예술·감성"), "예술")
        self.assertIsNone(normalize_theme(""))

    def test_invalid_or_missing_image_urls_degrade_to_null(self):
        metadata = {
            "PK": "CITY#Andong",
            "SK": "METADATA#city",
            "city_id": "KR-Andong",
            "city_name_ko": "안동시",
            "entity_type": "city",
            "province": "경상북도",
        }
        items = [
            metadata,
            {
                "PK": "CITY#Andong",
                "SK": "ATTRACTION#1",
                "entity_type": "attraction",
                "image_url": "not-a-url",
                "latitude": 36.56,
                "longitude": 128.72,
                "theme": "역사·전통",
                "title": "하회마을",
            },
            {
                "PK": "CITY#Andong",
                "SK": "ATTRACTION#2",
                "entity_type": "attraction",
                "latitude": 36.57,
                "longitude": 128.73,
                "theme": "산책",
                "title": "월영교",
            },
        ]

        record = build_city_api_record(metadata, items)

        self.assertIsNone(record["image_url"])

    def test_no_coordinate_source_rows_do_not_build_city_record(self):
        metadata = {
            "PK": "CITY#NoCoordinate",
            "SK": "METADATA#city",
            "city_id": "KR-NoCoordinate",
            "city_name_ko": "좌표없음시",
            "entity_type": "city",
            "province": "강원도",
        }
        items = [
            metadata,
            {
                "PK": "CITY#NoCoordinate",
                "SK": "ATTRACTION#1",
                "entity_type": "attraction",
                "image_url": "https://example.com/place.jpg",
                "latitude": "",
                "longitude": None,
                "theme": "자연",
                "title": "좌표 없는 장소",
            },
        ]

        with self.assertRaisesRegex(ValueError, "no usable place coordinates"):
            build_city_api_record(metadata, items)

    def test_service_filters_by_country_query_theme_and_paginates(self):
        service = SmallCityService(
            repository=None,
            city_records=[
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
                },
            ],
        )

        response = service.list_cities(country="KR", query="커피", themes=["미식"], page=1, page_size=1)

        self.assertEqual(response["page"], {"page": 1, "pageSize": 1, "total": 1, "hasNext": False})
        self.assertEqual([city["id"] for city in response["data"]], ["KR-Gangneung"])


if __name__ == "__main__":
    unittest.main()

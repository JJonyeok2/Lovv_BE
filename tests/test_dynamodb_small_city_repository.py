import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from small_cities.dynamodb_repository import DynamoDbSmallCityRepository, city_id_to_city_key


def gyeongju_items():
    return [
        {
            "PK": "CITY#GYEONGJU",
            "SK": "ATTRACTION#125419",
            "city_key": "CITY#GYEONGJU",
            "city_id": "KR-47-130",
            "city_name_en": "GYEONGJU",
            "city_name_ko": "경주시",
            "province": "경상북도",
            "entity_id": "ATT-125419",
            "entity_type": "attraction",
            "content_id": "125419",
            "title": "황리단길",
            "description": "경주 중심 산책 후보입니다.",
            "address": "경상북도 경주시",
            "latitude": "35.836",
            "longitude": "129.210",
            "theme": "전통",
            "theme_tags": ["전통", "산책"],
            "image_url": "https://example.com/hwangridan.jpg",
            "quality_status": "ok",
        },
        {
            "PK": "CITY#GYEONGJU",
            "SK": "FESTIVAL#3076781",
            "city_key": "CITY#GYEONGJU",
            "city_id": "KR-47-130",
            "city_name_en": "GYEONGJU",
            "city_name_ko": "경주시",
            "province": "경상북도",
            "entity_id": "FEST-3076781",
            "entity_type": "festival",
            "content_id": "3076781",
            "title": "경주 벚꽃 축제",
            "description": "봄 축제 후보입니다.",
            "latitude": "35.842",
            "longitude": "129.224",
            "theme": "축제",
            "theme_tags": ["축제"],
            "event_start_date": "20260404",
            "event_end_date": "20260407",
            "visit_months": [4],
        },
    ]


class FakeDynamoTable:
    def __init__(self, items=None, items_by_city=None):
        self.items = items or []
        self.items_by_city = items_by_city or {}
        self.scan_calls = []
        self.query_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return {"Items": list(self.items)}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        city_key = kwargs["ExpressionAttributeValues"][":city_key"]
        return {"Items": list(self.items_by_city.get(city_key, []))}


class DynamoDbSmallCityRepositoryTest(unittest.TestCase):
    def test_legacy_city_alias_preserves_hyphenated_dynamodb_city_key(self):
        self.assertEqual(city_id_to_city_key("KR-Dong-Ulsan"), "CITY#DONG-ULSAN")

    def test_lists_city_records_grouped_from_tour_korea_domain_data_v2_items(self):
        table = FakeDynamoTable(items=gyeongju_items(), items_by_city={"CITY#GYEONGJU": gyeongju_items()})
        repository = DynamoDbSmallCityRepository(table_name="TourKoreaDomainDataV2", table=table, catalog_records=[])

        records = repository.list_city_records()

        self.assertIn("ProjectionExpression", table.scan_calls[0])
        self.assertIn("#city_key", table.scan_calls[0]["ExpressionAttributeNames"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "KR-47-130")
        self.assertEqual(records[0]["name_ko"], "경주")
        self.assertEqual(records[0]["country"], "KR")
        self.assertIn("전통", records[0]["themes"])
        self.assertEqual(records[0]["internal_meta"]["source"], "DynamoDBTourKoreaDomainDataV2")
        self.assertEqual(records[0]["internal_meta"]["sourceKey"], "CITY#GYEONGJU")

    def test_lists_city_records_from_catalog_without_runtime_scan(self):
        catalog_record = {
            "id": "KR-47-130",
            "country": "KR",
            "country_label": "한국",
            "region": "경북",
            "name_ko": "경주",
            "name_local": "경주시",
            "latitude": 35.83,
            "longitude": 129.21,
            "themes": ["전통"],
            "summary": "경주 요약",
            "detail": "경주 상세",
            "highlights": ["황리단길"],
            "route_seed": ["황리단길"],
            "internal_meta": {"source": "DynamoDBTourKoreaDomainDataV2", "sourceKey": "CITY#GYEONGJU"},
        }
        table = FakeDynamoTable(items=gyeongju_items(), items_by_city={"CITY#GYEONGJU": gyeongju_items()})
        repository = DynamoDbSmallCityRepository(table_name="TourKoreaDomainDataV2", table=table, catalog_records=[catalog_record])

        records = repository.list_city_records()
        detail = repository.get_city_record("KR-47-130")
        places = repository.get_city_places("KR-47-130")

        self.assertEqual(records, [catalog_record])
        self.assertEqual(detail, catalog_record)
        self.assertEqual(table.scan_calls, [])
        self.assertEqual(table.query_calls[0]["ExpressionAttributeValues"][":city_key"], "CITY#GYEONGJU")
        self.assertEqual(places["cityId"], "KR-47-130")

    def test_get_city_record_resolves_canonical_city_id_without_changing_api_shape(self):
        table = FakeDynamoTable(items=gyeongju_items())
        repository = DynamoDbSmallCityRepository(table_name="TourKoreaDomainDataV2", table=table, catalog_records=[])

        record = repository.get_city_record("KR-47-130")

        self.assertEqual(record["id"], "KR-47-130")
        self.assertEqual(record["name_local"], "경주시")
        self.assertIn("황리단길", record["highlights"])

    def test_get_city_places_queries_city_domain_index_and_maps_v2_festival_dates(self):
        table = FakeDynamoTable(items_by_city={"CITY#GYEONGJU": gyeongju_items()})
        repository = DynamoDbSmallCityRepository(table_name="TourKoreaDomainDataV2", table=table, catalog_records=[])

        places = repository.get_city_places("KR-Gyeongju")

        self.assertEqual(table.query_calls[0]["IndexName"], "CityDomainIndex")
        self.assertEqual(places["cityId"], "KR-47-130")
        self.assertEqual(places["cityName"], "경주")
        self.assertEqual(places["summary"], {"attractionCount": 1, "festivalCount": 1, "visitorStatisticsCount": 0})
        self.assertEqual(places["attractions"][0]["placeId"], "ATT-125419")
        self.assertEqual(places["festivals"][0]["startDate"], "20260404")
        self.assertEqual(places["festivals"][0]["endDate"], "20260407")
        self.assertEqual(places["festivals"][0]["visitMonths"], [4])

    def test_metadata_audit_supplies_canonical_city_metadata(self):
        items = [
            {
                "PK": "CITY#GEOJE",
                "SK": "ATTRACTION#1",
                "city_key": "CITY#GEOJE",
                "entity_id": "ATT-1",
                "entity_type": "attraction",
                "content_id": "1",
                "title": "거제 바람의 언덕",
                "latitude": "34.744",
                "longitude": "128.663",
                "theme": "바다",
                "theme_tags": ["바다"],
            }
        ]
        metadata_audit = {
            "source": "metadata_audit/kr-tour-domain-v2-all-metadata-20260630T001340Z.json",
            "cities": [
                {
                    "city_id": "KR-36-1",
                    "ddb_pk": "CITY#GEOJE",
                    "city_key": "CITY#GEOJE",
                    "city_name_ko": "거제시",
                    "city_name_en": "GEOJE",
                    "province": "경상남도",
                    "country": "KR",
                }
            ],
        }
        table = FakeDynamoTable(items=items)
        repository = DynamoDbSmallCityRepository(
            table_name="TourKoreaDomainDataV2",
            table=table,
            metadata_audit=metadata_audit,
            catalog_records=[],
        )

        records = repository.list_city_records()

        self.assertEqual(records[0]["id"], "KR-36-1")
        self.assertEqual(records[0]["name_ko"], "거제")
        self.assertEqual(records[0]["region"], "경상남도")


if __name__ == "__main__":
    unittest.main()

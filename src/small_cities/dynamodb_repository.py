import os
import json
from collections import defaultdict
from pathlib import Path

from small_cities.image_resolver import load_image_map, resolve_image_url
from small_cities.mapper import build_city_api_record, normalize_image_url, read_number
from small_cities.s3_raw_repository import CityDataInvalidError, CityDataNotFoundError, CityDataUpstreamError


DEFAULT_TABLE_NAME = "TourKoreaDomainDataV2"
DEFAULT_SOURCE_NAME = "DynamoDBTourKoreaDomainDataV2"
DEFAULT_METADATA_AUDIT_BUCKET = "lovv-data-pipeline-dev-925273580929"
DEFAULT_CATALOG_FILENAME = "city_catalog_kr_v2.json"
CITY_DOMAIN_INDEX = "CityDomainIndex"
LIST_SCAN_ATTRIBUTE_NAMES = {
    "#pk": "PK",
    "#city_key": "city_key",
    "#city_id": "city_id",
    "#city_name_en": "city_name_en",
    "#city_name_ko": "city_name_ko",
    "#province": "province",
    "#entity_type": "entity_type",
    "#title": "title",
    "#latitude": "latitude",
    "#longitude": "longitude",
    "#theme": "theme",
    "#theme_tags": "theme_tags",
    "#image_url": "image_url",
    "#quality_status": "quality_status",
    "#schema_version": "schema_version",
}
LIST_SCAN_PROJECTION = ", ".join(LIST_SCAN_ATTRIBUTE_NAMES)


class DynamoDbSmallCityRepository:
    def __init__(
        self,
        table_name=None,
        dynamodb_resource=None,
        table=None,
        cdn_base=None,
        image_map=None,
        metadata_audit=None,
        metadata_bucket=None,
        metadata_key=None,
        s3_client=None,
        catalog_records=None,
        catalog_path=None,
    ):
        self.table_name = table_name or os.environ.get("MAP_CITY_DYNAMODB_TABLE") or DEFAULT_TABLE_NAME
        self.table = table or (dynamodb_resource or _dynamodb_resource()).Table(self.table_name)
        self.source_name = _source_name_for_table(self.table_name)
        self.cdn_base = cdn_base or ""
        self.image_map = image_map if isinstance(image_map, dict) else {}
        self.metadata_bucket = metadata_bucket
        self.metadata_key = metadata_key
        self.s3 = s3_client
        self._metadata_by_city_key = _metadata_by_city_key_from_audit(metadata_audit) if metadata_audit else None
        self._catalog_records = _catalog_records_from_payload(catalog_records) if catalog_records is not None else _load_catalog_records(catalog_path)
        self._catalog_by_id = {record.get("id"): record for record in self._catalog_records if record.get("id")}
        self._catalog_city_key_by_id = _catalog_city_key_by_id(self._catalog_records)
        self._city_item_groups = None
        self._city_records = None

    @classmethod
    def from_env(cls):
        image_data = load_image_map()
        cdn_base = (
            os.environ.get("IMAGE_CDN_BASE_URL", "").strip().rstrip("/")
            or str(image_data.get("cdnBase", "")).strip().rstrip("/")
        )
        return cls(
            table_name=os.environ.get("MAP_CITY_DYNAMODB_TABLE", DEFAULT_TABLE_NAME),
            cdn_base=cdn_base,
            image_map=image_data.get("images", {}),
            metadata_bucket=(
                os.environ.get("MAP_CITY_METADATA_AUDIT_BUCKET", "").strip()
                or os.environ.get("MAP_CITY_S3_BUCKET", "").strip()
                or DEFAULT_METADATA_AUDIT_BUCKET
            ),
            metadata_key=os.environ.get("MAP_CITY_METADATA_AUDIT_KEY", "").strip(),
            catalog_path=os.environ.get("MAP_CITY_CATALOG_PATH", "").strip() or None,
        )

    def list_city_records(self):
        if self._catalog_records:
            return list(self._catalog_records)

        if self._city_records is not None:
            return list(self._city_records)

        records = []
        for city_key, items in sorted(self._load_city_item_groups().items()):
            try:
                records.append(self._build_city_record(city_key, items))
            except (CityDataInvalidError, KeyError, TypeError, ValueError):
                continue

        self._city_records = records
        return list(records)

    def get_city_record(self, city_id):
        if city_id in self._catalog_by_id:
            return self._catalog_by_id[city_id]

        city_key = city_id_to_city_key(city_id)
        if city_key:
            catalog_record = self._catalog_record_by_city_key(city_key)
            if catalog_record:
                return catalog_record
            items = self._query_city_items(city_key)
            if items:
                return self._build_city_record(city_key, items)

        resolved = self._resolve_city_items(city_id)
        if not resolved:
            return None
        city_key, items = resolved
        return self._build_city_record(city_key, items)

    def get_city_places(self, city_id):
        city_key = self._catalog_city_key_by_id.get(city_id) or city_id_to_city_key(city_id)
        items = self._query_city_items(city_key) if city_key else []
        if not items:
            resolved = self._resolve_city_items(city_id)
            if not resolved:
                return None
            city_key, items = resolved
        else:
            city_key = city_key

        try:
            metadata = self._metadata_from_items(city_key, items)
            resolved_city_id = metadata.get("city_id") or city_id
            attractions = [
                _place_from_item(item, "attraction", resolved_city_id, self.cdn_base, self.image_map)
                for item in items if item.get("entity_type") == "attraction"
            ]
            festivals = [
                _place_from_item(item, "festival", resolved_city_id, self.cdn_base, self.image_map)
                for item in items if item.get("entity_type") == "festival"
            ]
            attractions = [place for place in attractions if place is not None]
            festivals = [place for place in festivals if place is not None]
        except (AttributeError, TypeError, ValueError) as error:
            raise CityDataInvalidError() from error

        return {
            "cityId": resolved_city_id,
            "cityName": _display_city_name(metadata),
            "summary": _summary(metadata, items),
            "attractions": attractions,
            "festivals": festivals,
        }

    def _build_city_record(self, city_key, items):
        metadata = self._metadata_from_items(city_key, items)
        record = build_city_api_record(metadata, items, source=self.source_name, source_key=city_key)
        record["image_url"] = _resolve_representative_image_url(
            items,
            record.get("id"),
            self.cdn_base,
            self.image_map,
        ) or record.get("image_url")
        record["detail_summary"] = _summary(metadata, items)
        return record

    def _load_city_item_groups(self):
        if self._city_item_groups is not None:
            return self._city_item_groups

        groups = defaultdict(list)
        for item in self._scan_items():
            if not _is_city_domain_item(item):
                continue
            groups[_city_key_from_item(item)].append(item)

        self._city_item_groups = dict(groups)
        return self._city_item_groups

    def _scan_items(self):
        items = []
        request = {
            "ProjectionExpression": LIST_SCAN_PROJECTION,
            "ExpressionAttributeNames": LIST_SCAN_ATTRIBUTE_NAMES,
        }
        while True:
            try:
                response = self.table.scan(**request)
            except Exception as error:
                raise _repository_error_from_client_error(error) from error
            items.extend(response.get("Items") or [])
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key
        return items

    def _query_city_items(self, city_key):
        if not city_key:
            return []

        items = []
        request = {
            "IndexName": CITY_DOMAIN_INDEX,
            "KeyConditionExpression": "city_key = :city_key",
            "ExpressionAttributeValues": {":city_key": city_key},
        }
        while True:
            try:
                response = self.table.query(**request)
            except Exception as error:
                raise _repository_error_from_client_error(error) from error
            items.extend(item for item in response.get("Items") or [] if _is_city_domain_item(item))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key
        return items

    def _resolve_city_items(self, city_id):
        for city_key, items in self._load_city_item_groups().items():
            metadata = self._metadata_from_items(city_key, items)
            if metadata.get("city_id") == city_id or legacy_city_id_from_city_key(city_key) == city_id:
                return city_key, items
        return None

    def _metadata_from_items(self, city_key, items):
        return _metadata_from_items(city_key, items, self._metadata_for_city_key(city_key))

    def _metadata_for_city_key(self, city_key):
        if self._metadata_by_city_key is None:
            self._metadata_by_city_key = self._load_metadata_audit_map()
        return self._metadata_by_city_key.get(city_key) or {}

    def _load_metadata_audit_map(self):
        if not self.metadata_key:
            return {}

        s3 = self.s3 or _s3_client()
        try:
            response = s3.get_object(Bucket=self.metadata_bucket, Key=self.metadata_key)
            payload = response["Body"].read().decode("utf-8")
            parsed = json.loads(payload)
        except Exception as error:
            raise _repository_error_from_client_error(error) from error
        if not isinstance(parsed, dict):
            raise CityDataInvalidError()
        return _metadata_by_city_key_from_audit(parsed)

    def _catalog_record_by_city_key(self, city_key):
        for record in self._catalog_records:
            source_key = (record.get("internal_meta") or {}).get("sourceKey")
            if source_key == city_key:
                return record
        return None


def city_id_to_city_key(city_id):
    if not isinstance(city_id, str) or not city_id.strip():
        return None

    value = city_id.strip()
    if value.upper().startswith("CITY#"):
        return value.upper()

    if "-" not in value:
        return None

    country, stem = value.split("-", 1)
    if country.upper() not in ("KR", "JP"):
        return None

    # Canonical admin ids such as KR-47-130 are resolved by scanning grouped
    # records because the DynamoDB city key is the English city slug.
    if all(part.isdigit() for part in stem.split("-") if part):
        return None

    normalized_stem = stem.replace(" ", "_").upper()
    return f"CITY#{normalized_stem}" if normalized_stem else None


def legacy_city_id_from_city_key(city_key):
    if not isinstance(city_key, str) or not city_key.startswith("CITY#"):
        return None
    stem = city_key.split("#", 1)[1]
    return f"KR-{stem.title().replace('_', '-')}" if stem else None


def _metadata_from_items(city_key, items, metadata=None):
    first = next((item for item in items if isinstance(item, dict)), None)
    if not first:
        raise CityDataInvalidError()

    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "PK": city_key,
        "city_id": metadata.get("city_id") or first.get("city_id") or legacy_city_id_from_city_key(city_key),
        "city_name_en": metadata.get("city_name_en") or first.get("city_name_en"),
        "city_name_ko": metadata.get("city_name_ko") or first.get("city_name_ko"),
        "province": metadata.get("province") or first.get("province"),
        "source_status": first.get("quality_status") or first.get("schema_version"),
    }


def _metadata_by_city_key_from_audit(metadata_audit):
    if not isinstance(metadata_audit, dict):
        return {}

    cities = metadata_audit.get("cities")
    if not isinstance(cities, list):
        return {}

    metadata_by_city_key = {}
    for city in cities:
        if not isinstance(city, dict):
            continue
        city_key = city.get("city_key") or city.get("ddb_pk")
        if isinstance(city_key, str) and city_key.startswith("CITY#"):
            metadata_by_city_key[city_key] = city
    return metadata_by_city_key


def _load_catalog_records(catalog_path=None):
    path = Path(catalog_path) if catalog_path else Path(__file__).with_name(DEFAULT_CATALOG_FILENAME)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CityDataInvalidError() from error
    return _catalog_records_from_payload(payload)


def _catalog_records_from_payload(payload):
    records = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict) and record.get("id")]


def _catalog_city_key_by_id(records):
    city_key_by_id = {}
    for record in records:
        city_id = record.get("id")
        city_key = (record.get("internal_meta") or {}).get("sourceKey")
        if isinstance(city_id, str) and isinstance(city_key, str) and city_key.startswith("CITY#"):
            city_key_by_id[city_id] = city_key
            legacy_id = legacy_city_id_from_city_key(city_key)
            if legacy_id:
                city_key_by_id[legacy_id] = city_key
    return city_key_by_id


def _is_city_domain_item(item):
    return isinstance(item, dict) and _city_key_from_item(item) and item.get("entity_type") in ("attraction", "festival", "visitor_statistics")


def _city_key_from_item(item):
    city_key = item.get("city_key") or item.get("PK")
    if isinstance(city_key, str) and city_key.startswith("CITY#"):
        return city_key
    return None


def _place_from_item(item, place_type, city_id="", cdn_base="", image_map=None):
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    title = title.strip()
    s3_image_url = resolve_image_url(city_id, title, cdn_base, image_map or {}, allow_city_fallback=True)
    fallback_url = item.get("image_url")
    resolved_image_url = s3_image_url or normalize_image_url(fallback_url)

    return {
        "placeId": item.get("entity_id") or f"{place_type.upper()}-{item.get('content_id') or item.get('contentid') or ''}",
        "type": place_type,
        "contentId": item.get("content_id") or item.get("contentid"),
        "title": title,
        "description": _short_text(item.get("description")),
        "address": item.get("address"),
        "phone": item.get("phone"),
        "imageUrl": resolved_image_url,
        "latitude": read_number(item.get("latitude")),
        "longitude": read_number(item.get("longitude")),
        "theme": item.get("theme"),
        "themeTags": item.get("theme_tags") if isinstance(item.get("theme_tags"), list) else [],
        "startDate": item.get("eventstartdate") or item.get("event_start_date") or None,
        "endDate": item.get("eventenddate") or item.get("event_end_date") or None,
        "visitMonths": _read_int_list(item.get("visit_months")),
    }


def _summary(city_record, records):
    return {
        "attractionCount": _count_or_field(city_record, records, "attraction_count", "attraction"),
        "festivalCount": _count_or_field(city_record, records, "festival_count", "festival"),
        "visitorStatisticsCount": _count_or_field(city_record, records, "visitor_statistics_count", "visitor_statistics"),
    }


def _count_or_field(city_record, records, field, entity_type):
    value = city_record.get(field)
    if isinstance(value, int):
        return value
    return sum(1 for item in records if item.get("entity_type") == entity_type)


def _display_city_name(city_record):
    name = city_record.get("city_name_ko")
    if isinstance(name, str):
        trimmed = name.strip()
        for suffix in ("시", "군"):
            if trimmed.endswith(suffix) and len(trimmed) > len(suffix):
                return trimmed[: -len(suffix)]
        return trimmed
    return city_record.get("city_name_en")


def _short_text(value, limit=280):
    if not isinstance(value, str):
        return None
    trimmed = " ".join(value.split())
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: limit - 3] + "..."


def _resolve_representative_image_url(items, city_id="", cdn_base="", image_map=None):
    if not city_id or not cdn_base:
        return None

    for item in items:
        if item.get("entity_type") not in ("attraction", "festival"):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        image_url = resolve_image_url(city_id, title.strip(), cdn_base, image_map or {}, allow_city_fallback=True)
        if image_url:
            return image_url

    return None


def _read_int_list(value):
    if not isinstance(value, list):
        return []

    parsed = []
    for item in value:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _source_name_for_table(table_name):
    return DEFAULT_SOURCE_NAME if table_name == DEFAULT_TABLE_NAME else f"DynamoDB{table_name}"


def _repository_error_from_client_error(error):
    code = _client_error_code(error)
    if code in {"ResourceNotFoundException", "NotFound", "404"}:
        return CityDataNotFoundError()
    if code in {"AccessDeniedException", "ProvisionedThroughputExceededException", "ThrottlingException", "InternalServerError"}:
        return CityDataUpstreamError()
    return CityDataUpstreamError()


def _client_error_code(error):
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        error_body = response.get("Error")
        if isinstance(error_body, dict):
            code = error_body.get("Code")
            if code is not None:
                return str(code)
        status_code = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if status_code is not None:
            return str(status_code)
    return error.__class__.__name__


def _dynamodb_resource():
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("boto3 is required in the Lambda runtime.") from error
    return boto3.resource("dynamodb")


def _s3_client():
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("boto3 is required in the Lambda runtime.") from error
    return boto3.client("s3")

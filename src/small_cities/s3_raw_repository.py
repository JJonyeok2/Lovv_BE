import json
import os

from small_cities.image_resolver import load_image_map, resolve_image_url
from small_cities.mapper import build_city_api_record, normalize_image_url, read_number


DEFAULT_BUCKET = "lovv-data-pipeline-dev-925273580929"
DEFAULT_PREFIX = "raw/KR/details/20260609/"
NOT_FOUND_ERROR_CODES = {"NoSuchKey", "NoSuchBucket", "NotFound", "404"}
UPSTREAM_ERROR_CODES = {"AccessDenied", "SlowDown", "Throttling", "ThrottlingException", "RequestTimeout", "ServiceUnavailable", "InternalError"}


class CityDataRepositoryError(Exception):
    def __init__(self, code, message, status_code):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CityDataNotFoundError(CityDataRepositoryError):
    def __init__(self):
        super().__init__("NOT_FOUND", "Small city was not found.", 404)


class CityDataUpstreamError(CityDataRepositoryError):
    def __init__(self):
        super().__init__("UPSTREAM_UNAVAILABLE", "Small-city source data is unavailable.", 502)


class CityDataInvalidError(CityDataRepositoryError):
    def __init__(self):
        super().__init__("INTERNAL_ERROR", "Small-city source data is invalid.", 500)


class S3RawCityRepository:
    def __init__(self, bucket, prefix, s3_client=None, cdn_base=None, image_map=None):
        self.bucket = bucket
        self.prefix = _normalize_prefix(prefix)
        self.s3 = s3_client or _s3_client()
        self._city_records = None
        self.cdn_base = cdn_base or ""
        # image_map: load_image_map()["images"] dict, or empty dict
        self.image_map = image_map if isinstance(image_map, dict) else {}

    @classmethod
    def from_env(cls):
        image_data = load_image_map()
        cdn_base = (
            os.environ.get("IMAGE_CDN_BASE_URL", "").strip().rstrip("/")
            or str(image_data.get("cdnBase", "")).strip().rstrip("/")
        )
        return cls(
            bucket=os.environ.get("MAP_CITY_S3_BUCKET", DEFAULT_BUCKET),
            prefix=os.environ.get("MAP_CITY_S3_PREFIX", DEFAULT_PREFIX),
            cdn_base=cdn_base,
            image_map=image_data.get("images", {}),
        )

    def list_city_records(self):
        if self._city_records is not None:
            return list(self._city_records)

        records = []
        for key in self._list_city_keys():
            try:
                records.append(self._build_city_record_from_document(self._read_document(key), key))
            except (CityDataInvalidError, KeyError, TypeError, ValueError):
                continue

        self._city_records = records
        return list(records)

    def get_city_record(self, city_id):
        key = self._city_key(city_id)
        try:
            return self._build_city_record_from_document(self._read_document(key), key)
        except CityDataNotFoundError:
            return None
        except (KeyError, TypeError, ValueError) as error:
            raise CityDataInvalidError() from error

    def get_city_places(self, city_id):
        key = self._city_key(city_id)
        try:
            document = self._read_document(key)
        except CityDataNotFoundError:
            return None
        except (KeyError, TypeError, ValueError) as error:
            raise CityDataInvalidError() from error

        try:
            city_record = _city_record_from_document(document)
            records = _items_from_document(document)
            resolved_city_id = document.get("city_id") or city_record.get("city_id") or city_id
            attractions = [
                _place_from_item(item, "attraction", resolved_city_id, self.cdn_base, self.image_map)
                for item in records if item.get("entity_type") == "attraction"
            ]
            festivals = [
                _place_from_item(item, "festival", resolved_city_id, self.cdn_base, self.image_map)
                for item in records if item.get("entity_type") == "festival"
            ]
            attractions = [place for place in attractions if place is not None]
            festivals = [place for place in festivals if place is not None]
        except (AttributeError, TypeError, ValueError) as error:
            raise CityDataInvalidError() from error

        return {
            "cityId": resolved_city_id,
            "cityName": _display_city_name(city_record),
            "summary": _summary(city_record, records),
            "attractions": attractions,
            "festivals": festivals,
        }

    def _build_city_record_from_document(self, document, key):
        city_record = _city_record_from_document(document)
        items = _items_from_document(document)
        record = build_city_api_record(city_record, items, source="S3RawCityDetails", source_key=key)
        record["image_url"] = _resolve_representative_image_url(
            items,
            record.get("id"),
            self.cdn_base,
            self.image_map,
        ) or record.get("image_url")
        record["detail_summary"] = _summary(city_record, items)
        return record

    def _list_city_keys(self):
        keys = []
        request = {"Bucket": self.bucket, "Prefix": self.prefix}
        while True:
            try:
                response = self.s3.list_objects_v2(**request)
            except Exception as error:
                raise _repository_error_from_client_error(error) from error
            for item in response.get("Contents") or []:
                key = item.get("Key")
                if key and key.endswith(".json"):
                    keys.append(key)
            token = response.get("NextContinuationToken")
            if not token:
                break
            request["ContinuationToken"] = token
        return keys

    def _read_document(self, key):
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            raise _repository_error_from_client_error(error) from error
        try:
            payload = response["Body"].read().decode("utf-8")
            parsed = json.loads(payload)
        except (KeyError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CityDataInvalidError() from error
        if not isinstance(parsed, dict):
            raise CityDataInvalidError()
        return parsed

    def _city_key(self, city_id):
        city_name_en = city_id_to_file_stem(city_id)
        return f"{self.prefix}{city_name_en}.json"


def city_id_to_file_stem(city_id):
    if not isinstance(city_id, str) or "-" not in city_id:
        return ""
    return city_id.split("-", 1)[1]


def _city_record_from_document(document):
    city_record = document.get("city_record")
    if isinstance(city_record, dict) and city_record:
        return city_record

    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    attractions = document.get("attractions") if isinstance(document.get("attractions"), list) else []
    festivals = document.get("festivals") if isinstance(document.get("festivals"), list) else []
    city_name_en = meta.get("city_name_en") or document.get("city_name_en")
    city_id = meta.get("city_id") or document.get("city_id") or (f"KR-{city_name_en}" if city_name_en else None)
    return {
        "city_id": city_id,
        "city_name_en": city_name_en,
        "city_name_ko": meta.get("city_name_ko") or document.get("city_name_ko"),
        "province": meta.get("province") or document.get("province"),
        "attraction_count": _count_field(document, "attractions_count_filtered", attractions),
        "festival_count": _count_field(document, "festivals_count_filtered", festivals),
        "visitor_statistics_count": _visitor_statistics_count(document.get("visitor_statistics")),
    }


def _items_from_document(document):
    records = document.get("records")
    if isinstance(records, list):
        return records

    attractions = document.get("attractions") if isinstance(document.get("attractions"), list) else []
    festivals = document.get("festivals") if isinstance(document.get("festivals"), list) else []
    visitor_statistics_count = _visitor_statistics_count(document.get("visitor_statistics"))
    items = [_normalize_raw_place(item, "attraction") for item in attractions if isinstance(item, dict)]
    items.extend(_normalize_raw_place(item, "festival") for item in festivals if isinstance(item, dict))
    items.extend({"entity_type": "visitor_statistics"} for _ in range(visitor_statistics_count))
    return items


def _normalize_raw_place(item, entity_type):
    common = _common_detail(item)
    theme = item.get("_assigned_theme") or item.get("theme")
    theme_tags = [theme] if isinstance(theme, str) and theme else []
    address = _join_address(item.get("addr1") or common.get("addr1"), item.get("addr2") or common.get("addr2"))
    return {
        "entity_type": entity_type,
        "content_id": item.get("content_id") or item.get("contentid") or common.get("contentid"),
        "title": item.get("title") or common.get("title"),
        "description": item.get("description") or common.get("overview"),
        "address": address,
        "phone": item.get("phone") or item.get("tel") or common.get("tel"),
        "image_url": item.get("image_url") or item.get("firstimage") or common.get("firstimage") or item.get("firstimage2") or common.get("firstimage2"),
        "latitude": item.get("latitude") or item.get("mapy") or common.get("mapy"),
        "longitude": item.get("longitude") or item.get("mapx") or common.get("mapx"),
        "theme": theme,
        "theme_tags": theme_tags,
        "eventstartdate": item.get("eventstartdate") or _intro_detail(item).get("eventstartdate"),
        "eventenddate": item.get("eventenddate") or _intro_detail(item).get("eventenddate"),
        "visit_months": item.get("visit_months") if isinstance(item.get("visit_months"), list) else [],
    }


def _common_detail(item):
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    common = detail.get("common") if isinstance(detail.get("common"), dict) else {}
    return common


def _intro_detail(item):
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    intro = detail.get("intro") if isinstance(detail.get("intro"), dict) else {}
    return intro


def _join_address(addr1, addr2):
    parts = [value.strip() for value in (addr1, addr2) if isinstance(value, str) and value.strip()]
    return " ".join(parts) or None


def _count_field(document, field, fallback_items):
    value = document.get(field)
    return value if isinstance(value, int) else len(fallback_items)


def _visitor_statistics_count(value):
    if isinstance(value, dict):
        monthly = value.get("monthly_statistics")
        if isinstance(monthly, list):
            return len(monthly)
        return 1 if value else 0
    if isinstance(value, list):
        return len(value)
    return 0


def _place_from_item(item, place_type, city_id="", cdn_base="", image_map=None):
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    title = title.strip()

    # 1순위: S3 이미지 매핑에서 CloudFront URL 조회
    s3_image_url = resolve_image_url(city_id, title, cdn_base, image_map or {}, allow_city_fallback=True)

    # 2순위: 기존 관광공사 firstimage URL (유효한 경우)
    fallback_url = item.get("image_url")
    resolved_image_url = s3_image_url or normalize_image_url(fallback_url)

    return {
        "placeId": item.get("entity_id") or f"{place_type.upper()}-{item.get('content_id', '')}",
        "type": place_type,
        "contentId": item.get("content_id"),
        "title": title,
        "description": _short_text(item.get("description")),
        "address": item.get("address"),
        "phone": item.get("phone"),
        "imageUrl": resolved_image_url,
        "latitude": read_number(item.get("latitude")),
        "longitude": read_number(item.get("longitude")),
        "theme": item.get("theme"),
        "themeTags": item.get("theme_tags") if isinstance(item.get("theme_tags"), list) else [],
        "startDate": item.get("eventstartdate") or None,
        "endDate": item.get("eventenddate") or None,
        "visitMonths": item.get("visit_months") if isinstance(item.get("visit_months"), list) else [],
    }


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


def _normalize_prefix(prefix):
    prefix = prefix or DEFAULT_PREFIX
    return prefix if prefix.endswith("/") else f"{prefix}/"


def _repository_error_from_client_error(error):
    code = _client_error_code(error)
    if code in NOT_FOUND_ERROR_CODES:
        return CityDataNotFoundError()
    if code in UPSTREAM_ERROR_CODES:
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


def _s3_client():
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("boto3 is required in the Lambda runtime.") from error
    return boto3.client("s3")

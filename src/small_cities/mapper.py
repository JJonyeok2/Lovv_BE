from collections import Counter
from decimal import Decimal
from urllib.parse import urlparse


VALID_THEMES = ["온천", "바다", "미식", "전통", "자연", "예술", "축제", "산책"]

THEME_ALIASES = {
    "온천": "온천",
    "해안": "바다",
    "바다": "바다",
    "미식": "미식",
    "노포": "미식",
    "전통": "전통",
    "역사": "전통",
    "자연": "자연",
    "트레킹": "자연",
    "예술": "예술",
    "감성": "예술",
    "축제": "축제",
    "산책": "산책",
}

PROVINCE_LABELS = {
    "강원특별자치도": "강원",
    "강원도": "강원",
    "경상북도": "경북",
}

CITY_SUFFIXES = ("특별자치시", "광역시", "특별시", "자치시", "시", "군", "구")


def normalize_theme(value):
    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    if trimmed in VALID_THEMES:
        return trimmed

    for token, normalized in THEME_ALIASES.items():
        if token in trimmed:
            return normalized

    return None


def normalize_region(province):
    if not isinstance(province, str) or not province.strip():
        return "기타"

    trimmed = province.strip()
    return PROVINCE_LABELS.get(trimmed, trimmed)


def normalize_city_name(city_name):
    if not isinstance(city_name, str):
        return ""

    trimmed = city_name.strip()
    for suffix in CITY_SUFFIXES:
        if trimmed.endswith(suffix) and len(trimmed) > len(suffix):
            return trimmed[: -len(suffix)]

    return trimmed


def read_number(value):
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (float, int)):
        return float(value)

    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None

    return None


def get_country(city_id):
    if isinstance(city_id, str) and city_id.startswith("JP-"):
        return "JP"
    return "KR"


def get_country_label(country):
    return "일본" if country == "JP" else "한국"


def is_place_item(item):
    return item.get("entity_type") in ("attraction", "festival")


def is_festival(item):
    return item.get("entity_type") == "festival"


def get_title(item):
    title = item.get("title")
    return title.strip() if isinstance(title, str) else ""


def rank_place(item):
    return (0 if is_festival(item) else 1, get_title(item))


def collect_coordinates(items):
    coordinates = []
    for item in items:
        if not is_place_item(item):
            continue

        latitude = read_number(item.get("latitude"))
        longitude = read_number(item.get("longitude"))
        if latitude is None or longitude is None:
            continue
        coordinates.append((latitude, longitude))

    if not coordinates:
        raise ValueError("City has no usable place coordinates.")

    return (
        sum(latitude for latitude, _ in coordinates) / len(coordinates),
        sum(longitude for _, longitude in coordinates) / len(coordinates),
    )


def collect_themes(items):
    counts = Counter()
    for item in items:
        if is_festival(item):
            counts["축제"] += 1

        raw_theme_values = []
        theme_tags = item.get("theme_tags")
        if isinstance(theme_tags, list):
            raw_theme_values.extend(theme_tags)
        raw_theme_values.append(item.get("theme"))

        for raw_theme in raw_theme_values:
            normalized = normalize_theme(raw_theme)
            if normalized:
                counts[normalized] += 1

    if not counts:
        return ["자연"]

    return sorted(counts, key=lambda theme: (-counts[theme], VALID_THEMES.index(theme)))[:4]


def collect_highlights(items):
    highlights = []
    for item in sorted((item for item in items if is_place_item(item)), key=rank_place):
        title = get_title(item)
        if title and title not in highlights:
            highlights.append(title)
        if len(highlights) == 4:
            break

    return highlights or ["추천 후보 확인 필요"]


def collect_image_url(items):
    for item in sorted((item for item in items if is_place_item(item)), key=rank_place):
        image_url = normalize_image_url(item.get("image_url"))
        if image_url:
            return image_url
    return None


def is_usable_image_url(value):
    return normalize_image_url(value) is not None


def normalize_image_url(value):
    if not isinstance(value, str):
        return None

    trimmed = value.strip()
    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.scheme == "http":
        return parsed._replace(scheme="https").geturl()
    return trimmed


def build_city_api_record(metadata, items, source="S3RawCityDetails", source_key=None):
    city_id = metadata.get("city_id")
    city_name_ko = metadata.get("city_name_ko")
    country = get_country(city_id)
    region = normalize_region(metadata.get("province"))
    display_name = normalize_city_name(city_name_ko)
    latitude, longitude = collect_coordinates(items)
    themes = collect_themes(items)
    highlights = collect_highlights(items)
    attraction_count = sum(1 for item in items if item.get("entity_type") == "attraction")
    festival_count = sum(1 for item in items if item.get("entity_type") == "festival")
    visitor_statistics_count = sum(1 for item in items if item.get("entity_type") == "visitor_statistics")
    theme_summary = "·".join(themes[:2])

    return {
        "id": city_id,
        "country": country,
        "country_label": get_country_label(country),
        "region": region,
        "name_ko": display_name,
        "name_local": city_name_ko,
        "latitude": latitude,
        "longitude": longitude,
        "themes": themes,
        "summary": f"{region} {display_name}는 {theme_summary} 여행 후보가 모여 있는 소도시입니다.",
        "detail": (
            f"대표 후보는 {', '.join(highlights[:3])}이며 "
            f"관광지 {attraction_count}건, 축제 {festival_count}건, "
            f"방문 통계 {visitor_statistics_count}개월을 기준으로 추천에 활용합니다."
        ),
        "highlights": highlights,
        "route_seed": highlights[:4],
        "image_url": collect_image_url(items),
        "internal_meta": {
            "source": source,
            "sourceKey": source_key,
            "cityPk": metadata.get("PK"),
            "sourceStatus": metadata.get("source_status"),
            "attractionCount": attraction_count,
            "festivalCount": festival_count,
            "visitorStatisticsCount": visitor_statistics_count,
        },
    }

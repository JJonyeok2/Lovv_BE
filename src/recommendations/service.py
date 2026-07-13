from datetime import datetime
from collections import Counter
from zoneinfo import ZoneInfo

from small_cities.mapper import get_country_label


SERVICE_TIMEZONE = "Asia/Seoul"
VALID_COUNTRIES = {"KR", "JP"}
DEFAULT_LIMIT = 6
MAX_LIMIT = 12

THEME_ID_BY_LABEL = {
    "온천": "hot_spring",
    "바다": "sea_coast",
    "미식": "food_local",
    "전통": "history_tradition",
    "자연": "nature_trekking",
    "예술": "art_emotion",
    "축제": "festival_event",
    "산책": "slow_walk",
}

SUMMER_THEMES = {"바다", "자연", "축제", "산책"}


class RecommendationValidationError(ValueError):
    pass


def service_month(now=None):
    current = now or datetime.now(ZoneInfo(SERVICE_TIMEZONE))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("UTC"))
    return current.astimezone(ZoneInfo(SERVICE_TIMEZONE)).month


def parse_country(value, default="KR"):
    country = (value or default or "KR").strip().upper()
    if country not in VALID_COUNTRIES:
        raise RecommendationValidationError("country must be KR or JP.")
    return country


def parse_limit(value, default=DEFAULT_LIMIT, maximum=MAX_LIMIT):
    if value in (None, ""):
        return default
    if not str(value).isdigit():
        raise RecommendationValidationError("limit must be a positive integer.")
    parsed = int(value)
    if parsed < 1:
        raise RecommendationValidationError("limit must be a positive integer.")
    return min(parsed, maximum)


def monthly_cities(city_records, country="KR", limit=DEFAULT_LIMIT, now=None):
    month = service_month(now)
    records = [record for record in city_records if record.get("country") == country]
    ranked = sorted(
        records,
        key=lambda record: (-_monthly_score(record, month), record.get("name_ko") or "", record.get("id") or ""),
    )
    return {
        "month": month,
        "timezone": SERVICE_TIMEZONE,
        "items": [
            _monthly_item(record, month, index + 1)
            for index, record in enumerate(ranked[:limit])
        ],
    }


def reaction_cities(city_records, liked_signals, limit=DEFAULT_LIMIT):
    if not liked_signals:
        return {"items": []}

    candidates = {}
    for signal_index, signal in enumerate(liked_signals):
        signal_country = _signal_country(signal)
        signal_themes = set(_theme_labels(signal.get("themes") or []))
        for record in city_records:
            if signal_country and record.get("country") != signal_country:
                continue
            score = _reaction_score(record, signal_themes, signal)
            if score <= 0:
                continue
            city_id = record.get("id")
            previous = candidates.get(city_id)
            if previous and previous["score"] >= score:
                continue
            candidates[city_id] = {
                "score": score,
                "signalIndex": signal_index,
                "record": record,
                "signal": signal,
            }

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item["score"], item["signalIndex"], item["record"].get("name_ko") or ""),
    )
    return {
        "items": [
            _reaction_item(item["record"], item["signal"], index + 1)
            for index, item in enumerate(ranked[:limit])
        ]
    }


def popular_destinations(city_records, aggregate_signals, limit=DEFAULT_LIMIT):
    if not aggregate_signals:
        return {"items": [], "ageGroups": []}

    city_lookup = {record.get("id"): record for record in city_records if record.get("id")}
    aggregates = {}
    age_group_aggregates = {}

    for signal in aggregate_signals:
        city_id = _signal_city_id(signal)
        destination = signal.get("destination") or {}
        if not city_id:
            continue

        aggregate = aggregates.setdefault(city_id, {
            "cityId": city_id,
            "destination": destination,
            "reactionCount": 0,
            "savedPlanCount": 0,
            "themeCounter": Counter(),
        })
        aggregate["reactionCount"] += int(signal.get("reactionCount") or 0)
        aggregate["savedPlanCount"] += int(signal.get("savedPlanCount") or 1)
        aggregate["themeCounter"].update(_theme_labels(signal.get("themes") or []))

        age_group = _age_group(signal.get("birthDate"))
        if age_group:
            age_group_map = age_group_aggregates.setdefault(age_group, {})
            age_aggregate = age_group_map.setdefault(city_id, {
                "cityId": city_id,
                "destination": destination,
                "reactionCount": 0,
                "savedPlanCount": 0,
                "themeCounter": Counter(),
                "userIds": set(),
            })
            age_aggregate["reactionCount"] += int(signal.get("reactionCount") or 0)
            age_aggregate["savedPlanCount"] += int(signal.get("savedPlanCount") or 1)
            age_aggregate["themeCounter"].update(_theme_labels(signal.get("themes") or []))
            if signal.get("userId"):
                age_aggregate["userIds"].add(signal.get("userId"))

    ranked = sorted(
        aggregates.values(),
        key=lambda item: (
            -item["reactionCount"],
            -item["savedPlanCount"],
            _aggregate_name(item, city_lookup),
        ),
    )

    return {
        "items": [
            _popular_destination_item(item, city_lookup.get(item["cityId"]), index + 1)
            for index, item in enumerate(ranked[:limit])
        ],
        "ageGroups": _age_group_items(age_group_aggregates, city_lookup, limit),
    }


def _monthly_score(record, month):
    themes = set(_theme_labels(record.get("themes") or []))
    score = 0
    if month in (6, 7, 8):
        score += len(themes & SUMMER_THEMES) * 20
    score += int(((record.get("internal_meta") or {}).get("festivalCount") or 0)) * 8
    if record.get("image_url"):
        score += 5
    if record.get("summary"):
        score += 3
    return score


def _reaction_score(record, signal_themes, signal):
    themes = set(_theme_labels(record.get("themes") or []))
    score = len(themes & signal_themes) * 30
    if (record.get("id") or "") == _signal_city_id(signal):
        score += 15
    if record.get("image_url"):
        score += 4
    return score


def _monthly_item(record, month, priority):
    themes = _theme_labels(record.get("themes") or [])
    primary_theme = _primary_monthly_theme(themes, month)
    name = record.get("name_ko") or record.get("name_local") or record.get("id")
    return {
        "cityId": record.get("id"),
        "name": name,
        "region": record.get("region"),
        "country": record.get("country"),
        "countryLabel": record.get("country_label") or get_country_label(record.get("country")),
        "themeIds": _theme_ids(themes),
        "themes": themes,
        "imageUrl": record.get("image_url") or None,
        "badge": f"{month}월 {primary_theme}",
        "title": f"{name}에서 만나는 {month}월 {primary_theme}",
        "summary": record.get("summary") or record.get("detail") or "",
        "recommendationReason": f"{month}월 계절·축제·테마 경향이 맞는 지역",
        "priority": priority,
    }


def _reaction_item(record, signal, priority):
    themes = _theme_labels(record.get("themes") or [])
    name = record.get("name_ko") or record.get("name_local") or record.get("id")
    theme_label = themes[0] if themes else "취향"
    return {
        "sourceReaction": signal.get("sourceReaction") or {},
        "cityId": record.get("id"),
        "name": name,
        "region": record.get("region"),
        "country": record.get("country"),
        "countryLabel": record.get("country_label") or get_country_label(record.get("country")),
        "themeIds": _theme_ids(themes),
        "themes": themes,
        "imageUrl": record.get("image_url") or None,
        "title": f"좋아한 {theme_label} 일정과 비슷한 {name}",
        "summary": record.get("summary") or record.get("detail") or "",
        "recommendationReason": f"최근 반응 남긴 일정의 {'·'.join(_theme_labels(signal.get('themes') or [])[:2]) or '여행'} 테마와 유사",
        "priority": priority,
    }


def _popular_destination_item(aggregate, record, priority):
    destination = aggregate.get("destination") or {}
    themes = _popular_theme_labels(aggregate.get("themeCounter") or Counter(), record)
    name = (
        (record or {}).get("name_ko")
        or (record or {}).get("name_local")
        or destination.get("name")
        or destination.get("destinationName")
        or destination.get("cityName")
        or aggregate.get("cityId")
    )
    country = (record or {}).get("country") or _signal_country({"destination": destination}) or "KR"
    region = (record or {}).get("region") or destination.get("region")
    return {
        "cityId": aggregate.get("cityId"),
        "name": name,
        "region": region,
        "country": country,
        "countryLabel": (record or {}).get("country_label") or get_country_label(country),
        "themeIds": _theme_ids(themes),
        "themes": themes,
        "imageUrl": (record or {}).get("image_url") or None,
        "reactionCount": aggregate.get("reactionCount") or 0,
        "savedPlanCount": aggregate.get("savedPlanCount") or 0,
        "title": f"{name}에 쌓인 여행자 반응",
        "summary": (record or {}).get("summary") or (record or {}).get("detail") or "",
        "recommendationReason": "저장 일정과 반응이 많이 쌓인 인기 지역",
        "priority": priority,
    }


def _age_group_items(age_group_aggregates, city_lookup, limit):
    groups = []
    for age_group in ("20s", "30s", "40s", "50s", "60s_plus"):
        aggregates = age_group_aggregates.get(age_group) or {}
        eligible_items = [
            item
            for item in aggregates.values()
            if len(item.get("userIds") or set()) >= 2
        ]
        ranked = sorted(
            eligible_items,
            key=lambda item: (
                -item["reactionCount"],
                -item["savedPlanCount"],
                _aggregate_name(item, city_lookup),
            ),
        )
        if not ranked:
            continue
        groups.append({
            "ageGroup": age_group,
            "label": _age_group_label(age_group),
            "items": [
                _popular_destination_item(item, city_lookup.get(item["cityId"]), index + 1)
                for index, item in enumerate(ranked[:limit])
            ],
        })
    return groups


def _popular_theme_labels(theme_counter, record):
    ranked_themes = [
        theme
        for theme, _count in sorted(theme_counter.items(), key=lambda item: (-item[1], item[0]))
        if theme
    ]
    if ranked_themes:
        return ranked_themes[:3]
    return _theme_labels((record or {}).get("themes") or [])[:3]


def _aggregate_name(aggregate, city_lookup):
    record = city_lookup.get(aggregate.get("cityId")) or {}
    destination = aggregate.get("destination") or {}
    return (
        record.get("name_ko")
        or record.get("name_local")
        or destination.get("name")
        or destination.get("destinationName")
        or destination.get("cityName")
        or aggregate.get("cityId")
        or ""
    )


def _age_group(value):
    birth_year = _birth_year(value)
    if not birth_year:
        return None
    current_year = datetime.now(ZoneInfo(SERVICE_TIMEZONE)).year
    age = current_year - birth_year
    if age < 20:
        return None
    if age < 30:
        return "20s"
    if age < 40:
        return "30s"
    if age < 50:
        return "40s"
    if age < 60:
        return "50s"
    return "60s_plus"


def _birth_year(value):
    if not value:
        return None
    if hasattr(value, "year"):
        return int(value.year)
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _age_group_label(age_group):
    return {
        "20s": "20대",
        "30s": "30대",
        "40s": "40대",
        "50s": "50대",
        "60s_plus": "60대 이상",
    }.get(age_group, age_group)


def _primary_monthly_theme(themes, month):
    if month in (6, 7, 8):
        for theme in ("바다", "자연", "축제", "산책"):
            if theme in themes:
                return theme
    return themes[0] if themes else "추천"


def _theme_labels(values):
    labels = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in labels:
            labels.append(value.strip())
    return labels


def _theme_ids(labels):
    return [THEME_ID_BY_LABEL[label] for label in labels if label in THEME_ID_BY_LABEL]


def _signal_city_id(signal):
    destination = signal.get("destination") or {}
    return destination.get("destinationId") or destination.get("cityId") or signal.get("cityId")


def _signal_country(signal):
    destination = signal.get("destination") or {}
    country = destination.get("country")
    if country in VALID_COUNTRIES:
        return country
    city_id = _signal_city_id(signal)
    if isinstance(city_id, str) and city_id.startswith("JP-"):
        return "JP"
    if isinstance(city_id, str) and city_id.startswith("KR-"):
        return "KR"
    return None

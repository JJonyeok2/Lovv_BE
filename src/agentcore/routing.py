# @file src/agentcore/routing.py
# @description Optional OpenRouteService itinerary route enrichment for AgentCore results.
# @lastModified 2026-06-25

import json
import math
import os
from urllib import request

from shared.logger import Tag, get_logger


LOGGER = get_logger(__name__)
DEFAULT_ORS_BASE_URL = "https://api.openrouteservice.org"
DEFAULT_ORS_PROFILE = "driving-car"
DEFAULT_ORS_TIMEOUT_SECONDS = 4.0
_ssm_client = None
_ssm_parameter_cache = {}


def enrich_itinerary_routes(itinerary):
    """Attach ORS route geometry to itinerary days when an API key is configured.

    This is an optional enhancement. A missing key, insufficient coordinates, or
    upstream ORS failure must not make an otherwise valid recommendation fail.
    """
    api_key = _openrouteservice_api_key()
    if not api_key or not isinstance(itinerary, dict):
        return itinerary

    days = itinerary.get("days")
    if not isinstance(days, list):
        return itinerary

    profile = (os.environ.get("OPENROUTESERVICE_PROFILE") or DEFAULT_ORS_PROFILE).strip() or DEFAULT_ORS_PROFILE
    base_url = (os.environ.get("OPENROUTESERVICE_BASE_URL") or DEFAULT_ORS_BASE_URL).strip().rstrip("/")
    timeout_seconds = _timeout_seconds()

    for day in days:
        if not isinstance(day, dict):
            continue
        items = day.get("items") or day.get("stops")
        if not isinstance(items, list):
            continue

        points = _valid_route_points(items)
        if len(points) < 2:
            continue

        try:
            ors_result = _fetch_route(
                base_url=base_url,
                profile=profile,
                api_key=api_key,
                coordinates=[point["coordinate"] for point in points],
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            LOGGER.warning(
                Tag.PLAN,
                "OpenRouteService route enrichment skipped day=%s pointCount=%s errorType=%s",
                day.get("day"),
                len(points),
                error.__class__.__name__,
            )
            continue

        if ors_result:
            day["route"] = ors_result
            _apply_leg_summaries(items, points, ors_result.get("segments") or [])

    return itinerary


def _fetch_route(base_url, profile, api_key, coordinates, timeout_seconds):
    url = f"{base_url}/v2/directions/{profile}/geojson"
    body = {
        "coordinates": coordinates,
        "instructions": True,
        "geometry_simplify": True,
    }
    response = _post_json(url, api_key, body, timeout_seconds)
    feature = _first_feature(response)
    if not feature:
        return None

    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    properties = feature.get("properties") if isinstance(feature, dict) else None
    summary = properties.get("summary") if isinstance(properties, dict) else None
    segments = properties.get("segments") if isinstance(properties, dict) else None

    if not isinstance(geometry, dict) or not isinstance(summary, dict):
        return None

    return {
        "provider": "openrouteservice",
        "profile": profile,
        "geometry": {
            "type": geometry.get("type") or "LineString",
            "coordinates": geometry.get("coordinates") or [],
        },
        "distanceMeters": int(float(summary.get("distance") or 0)),
        "durationSeconds": int(float(summary.get("duration") or 0)),
        "segments": _normalized_segments(segments),
    }


def _post_json(url, api_key, body, timeout_seconds):
    encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    http_request = request.Request(
        url,
        data=encoded_body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        },
        method="POST",
    )
    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _openrouteservice_api_key():
    direct_key = (os.environ.get("OPENROUTESERVICE_API_KEY") or "").strip()
    if direct_key:
        return direct_key

    parameter_name = (os.environ.get("OPENROUTESERVICE_API_KEY_SSM_NAME") or "").strip()
    if not parameter_name:
        return ""
    try:
        return _get_ssm_parameter(parameter_name).strip()
    except Exception as error:
        LOGGER.warning(
            Tag.PLAN,
            "OpenRouteService key lookup skipped source=ssm errorType=%s",
            error.__class__.__name__,
        )
        return ""


def _get_ssm_parameter(parameter_name):
    cached_value = _ssm_parameter_cache.get(parameter_name)
    if cached_value is not None:
        return cached_value

    global _ssm_client
    if _ssm_client is None:
        import boto3

        _ssm_client = boto3.client("ssm")
    response = _ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
    value = ((response.get("Parameter") or {}).get("Value") or "").strip()
    _ssm_parameter_cache[parameter_name] = value
    return value


def _valid_route_points(items):
    points = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        latitude = _number_or_none(item.get("latitude"))
        longitude = _number_or_none(item.get("longitude"))
        if latitude is None or longitude is None:
            continue
        points.append({"itemIndex": index, "coordinate": [longitude, latitude]})
    return points


def _apply_leg_summaries(items, points, segments):
    for segment_index, segment in enumerate(segments):
        if segment_index >= len(points) - 1:
            break
        item_index = points[segment_index]["itemIndex"]
        item = items[item_index]
        duration_seconds = int(float(segment.get("durationSeconds") or segment.get("duration") or 0))
        distance_meters = int(float(segment.get("distanceMeters") or segment.get("distance") or 0))
        if duration_seconds > 0:
            item["moveMinutes"] = max(1, math.ceil(duration_seconds / 60))
            item["moveDurationSeconds"] = duration_seconds
        if distance_meters > 0:
            item["moveDistanceMeters"] = distance_meters


def _normalized_segments(segments):
    if not isinstance(segments, list):
        return []
    normalized = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        normalized.append(
            {
                "distanceMeters": int(float(segment.get("distance") or 0)),
                "durationSeconds": int(float(segment.get("duration") or 0)),
            }
        )
    return normalized


def _first_feature(response):
    if not isinstance(response, dict):
        return None
    features = response.get("features")
    if not isinstance(features, list) or not features:
        return None
    feature = features[0]
    return feature if isinstance(feature, dict) else None


def _number_or_none(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _timeout_seconds():
    raw_value = os.environ.get("OPENROUTESERVICE_TIMEOUT_SECONDS")
    if not raw_value:
        return DEFAULT_ORS_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_ORS_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_ORS_TIMEOUT_SECONDS
    return min(value, 10.0)

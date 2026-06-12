# @file src/saved_plans/app.py
# @description Authenticated saved itinerary Lambda handler.
# @lastModified 2026-06-12

import base64
import json
from datetime import datetime, timezone

from saved_plans.repository import (
    IdempotencyConflictError,
    RdsDataSavedPlanRepository,
    canonical_snapshot_hash,
)
from shared.auth import AuthTokenError
from shared.current_user import authenticated_claims
from shared.http import empty_response, error_response, json_response


RAW_HISTORY_FIELDS = {"messages", "chatHistory", "conversation", "transcript"}
FORBIDDEN_OWNER_FIELDS = {"userId", "user_id", "ownerId", "createdBy"}


class SavedPlanRequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, repository=None):
    try:
        return _handle_request(event or {}, repository)
    except SavedPlanRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Saved plans API is unavailable")


def _handle_request(event, repository=None):
    method = _event_method(event)
    path = _event_path(event)
    if method == "OPTIONS":
        return json_response(200, {})

    user_id = _current_user_id(event)
    repository = repository or RdsDataSavedPlanRepository.from_env()
    itinerary_id = _itinerary_id(event, path)

    if method == "POST" and path == "/api/v1/me/itineraries":
        return _save_plan(event, user_id, repository)
    if method == "GET" and path == "/api/v1/me/itineraries":
        return _list_plans(event, user_id, repository)
    if method == "GET" and itinerary_id and path.endswith(f"/{itinerary_id}"):
        return _get_plan(user_id, itinerary_id, repository)
    if method == "PUT" and itinerary_id and path.endswith(f"/{itinerary_id}/reactions/like"):
        return _set_like(user_id, itinerary_id, True, repository)
    if method == "DELETE" and itinerary_id and path.endswith(f"/{itinerary_id}/reactions/like"):
        return _set_like(user_id, itinerary_id, False, repository)

    return error_response(404, "NOT_FOUND", "Route not found")


def _save_plan(event, user_id, repository):
    payload = _validate_save_payload(_json_body(event))
    snapshot_hash = canonical_snapshot_hash(_hash_payload(payload))
    try:
        plan, duplicate = repository.save(user_id, payload, snapshot_hash, _now_iso())
    except IdempotencyConflictError:
        raise SavedPlanRequestError(409, "IDEMPOTENCY_KEY_CONFLICT", "Idempotency key conflicts with another payload")

    return json_response(
        200 if duplicate else 201,
        {
            "itineraryId": plan["itineraryId"],
            "sourceRecommendationId": plan.get("sourceRecommendationId"),
            "savedAt": plan.get("savedAt"),
            "duplicate": bool(duplicate),
        },
    )


def _list_plans(event, user_id, repository):
    limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
    return json_response(200, {"items": repository.list_by_user(user_id, limit=limit), "nextCursor": None})


def _get_plan(user_id, itinerary_id, repository):
    plan = repository.get_owned(user_id, itinerary_id)
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    return json_response(200, _public_detail(plan))


def _set_like(user_id, itinerary_id, liked, repository):
    plan, changed = repository.set_like(user_id, itinerary_id, liked, _now_iso())
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    if not liked:
        return empty_response(204)
    return json_response(
        200,
        {
            "itineraryId": itinerary_id,
            "reactionType": "like",
            "isLiked": True,
            "changed": changed,
            "updatedAt": plan.get("updatedAt"),
        },
    )


def _validate_save_payload(payload):
    owner_fields = FORBIDDEN_OWNER_FIELDS.intersection(payload.keys())
    if owner_fields:
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "Owner fields are not writable")
    if RAW_HISTORY_FIELDS.intersection(payload.keys()):
        raise SavedPlanRequestError(400, "RAW_CHAT_HISTORY_NOT_ALLOWED", "Raw chat history cannot be saved")
    if not _non_empty_string(payload.get("title")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "title is required")
    if not _non_empty_string(payload.get("sourceRecommendationId")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "sourceRecommendationId is required")
    if not isinstance(payload.get("destination"), dict) or not _non_empty_string(payload["destination"].get("destinationId")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "destination is required")
    if not _non_empty_string(payload.get("durationLabel")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "durationLabel is required")
    itinerary = payload.get("itinerary")
    if not isinstance(itinerary, dict) or not isinstance(itinerary.get("days"), list) or not itinerary["days"]:
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "itinerary days are required")
    if not any(isinstance(day, dict) and _day_entries(day) for day in itinerary["days"]):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "itinerary items are required")
    return payload


def _public_detail(plan):
    return {
        "itineraryId": plan.get("itineraryId"),
        "sourceRecommendationId": plan.get("sourceRecommendationId"),
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination": plan.get("destination") or {},
        "tripType": plan.get("tripType"),
        "durationLabel": plan.get("durationLabel"),
        "themes": plan.get("themes") or [],
        "festivalChoice": plan.get("festivalChoice"),
        "intensityLabel": plan.get("intensityLabel"),
        "conditionsSnapshot": plan.get("conditionsSnapshot") or {},
        "requestSummary": plan.get("requestSummary"),
        "itinerary": plan.get("itinerary") or {},
        "alternativeItinerary": plan.get("alternativeItinerary"),
        "isLiked": bool(plan.get("isLiked")),
        "savedAt": plan.get("savedAt"),
        "updatedAt": plan.get("updatedAt"),
    }


def _hash_payload(payload):
    return {
        key: value
        for key, value in payload.items()
        if key not in {"idempotencyKey"}
    }


def _parse_limit(value):
    if value in (None, ""):
        return 20
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SavedPlanRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    if parsed < 1:
        raise SavedPlanRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    return min(parsed, 50)


def _current_user_id(event):
    claims = authenticated_claims(event)
    user_id = claims.get("userId") or claims.get("sub")
    if not user_id:
        raise SavedPlanRequestError(401, "UNAUTHORIZED", "Authentication is required")
    return user_id


def _itinerary_id(event, path):
    path_parameters = event.get("pathParameters") or {}
    if path_parameters.get("itineraryId"):
        return path_parameters["itineraryId"]
    prefix = "/api/v1/me/itineraries/"
    if path.startswith(prefix):
        remainder = path[len(prefix) :]
        return remainder.split("/", 1)[0]
    return None


def _json_body(event):
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise SavedPlanRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise SavedPlanRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "Request body must be a JSON object")
    return parsed


def _event_method(event):
    return (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


def _non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _day_entries(day):
    items = day.get("items")
    stops = day.get("stops")
    if isinstance(items, list) and items:
        return items
    if isinstance(stops, list) and stops:
        return stops
    return []


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# EOF: src/saved_plans/app.py

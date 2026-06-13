# @file src/preferences/app.py
# @description Authenticated user preferences Lambda handler.
# @lastModified 2026-06-12

import base64
import json
from datetime import datetime, timezone

from preferences.repository import RdsDataPreferenceRepository
from shared.auth import AuthTokenError
from shared.current_user import authenticated_claims
from shared.http import error_response, json_response


COUNTRY_TRACKS = {"KR", "JP"}
PACES = {"relaxed", "balanced", "active"}
FORBIDDEN_OWNER_FIELDS = {"userId", "user_id", "ownerId", "createdBy", "preferenceId", "createdAt", "updatedAt"}
FORBIDDEN_FREE_TEXT_FIELDS = {"dislikedConstraints", "freeText", "naturalLanguagePreference", "chatText", "messages"}


class PreferenceRequestError(Exception):
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
    except PreferenceRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Preference API is unavailable")


def _handle_request(event, repository=None):
    method = _event_method(event)
    path = _event_path(event)
    if method == "OPTIONS":
        return json_response(200, {})
    if path != "/api/v1/me/preferences":
        return error_response(404, "NOT_FOUND", "Route not found")

    user_id = _current_user_id(event)
    repository = repository or RdsDataPreferenceRepository.from_env()

    if method == "GET":
        preference = repository.get_by_user_id(user_id)
        if not preference or not preference.get("onboardingCompleted"):
            return json_response(200, {"preferences": None, "onboardingCompleted": False})
        return json_response(200, {"preferences": public_preference(preference), "onboardingCompleted": True})

    if method == "PUT":
        payload = _validate_payload(_json_body(event))
        preference = repository.upsert(user_id, payload, _now_iso())
        return json_response(200, {"preferences": public_preference(preference)})

    return error_response(405, "INVALID_METHOD", "Only GET and PUT are supported")


def _validate_payload(body):
    forbidden = sorted((FORBIDDEN_OWNER_FIELDS | FORBIDDEN_FREE_TEXT_FIELDS).intersection(body.keys()))
    if forbidden:
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "Preference payload contains unsupported fields")

    country_track = _read_country_track(body)
    mapped_themes = _read_mapped_themes(body)

    if "preferredRegions" in body and not _is_string_list(body.get("preferredRegions")):
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "preferredRegions must be an array")
    if "travelStyles" in body and not _is_string_list(body.get("travelStyles")):
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "travelStyles must be an array")
    if body.get("pace") not in (None, "", *PACES):
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "pace is invalid")
    if "tripDays" in body and (not isinstance(body.get("tripDays"), int) or body.get("tripDays") < 1):
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "tripDays must be a positive integer")

    return {
        "countryTrack": country_track,
        "mappedThemes": mapped_themes,
        "preferredRegions": body.get("preferredRegions") or [],
        "selectedCityStyle": body.get("selectedCityStyle"),
        "pace": body.get("pace"),
        "tripDays": body.get("tripDays"),
        "companionStyle": body.get("companionStyle"),
        "travelStyles": body.get("travelStyles") or [],
    }


def _read_country_track(body):
    if "countryTrack" not in body:
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "countryTrack is required")

    country_track = body.get("countryTrack")
    if country_track not in COUNTRY_TRACKS:
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "countryTrack is invalid")
    return country_track


def _read_mapped_themes(body):
    mapped_themes = body.get("mappedThemes")
    if _is_non_empty_string_list(mapped_themes):
        return mapped_themes

    # Frontend state uses selectedThemeIds; backend storage remains mappedThemes.
    selected_theme_ids = body.get("selectedThemeIds")
    if _is_non_empty_string_list(selected_theme_ids):
        return selected_theme_ids

    raise PreferenceRequestError(400, "VALIDATION_ERROR", "mappedThemes or selectedThemeIds is required")


def public_preference(preference):
    mapped_themes = preference.get("mappedThemes") or []

    return {
        "preferenceId": preference.get("preferenceId"),
        "userId": preference.get("userId"),
        "countryTrack": preference.get("countryTrack"),
        "preferredRegions": preference.get("preferredRegions") or [],
        "selectedCityStyle": preference.get("selectedCityStyle"),
        "mappedThemes": mapped_themes,
        "selectedThemeIds": mapped_themes,
        "pace": preference.get("pace"),
        "tripDays": preference.get("tripDays"),
        "companionStyle": preference.get("companionStyle"),
        "travelStyles": preference.get("travelStyles") or [],
        "onboardingCompleted": bool(preference.get("onboardingCompleted")),
        "createdAt": preference.get("createdAt"),
        "updatedAt": preference.get("updatedAt"),
    }


def _current_user_id(event):
    claims = authenticated_claims(event)
    user_id = claims.get("userId") or claims.get("sub")
    if not user_id:
        raise PreferenceRequestError(401, "UNAUTHORIZED", "Authentication is required")
    return user_id


def _json_body(event):
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise PreferenceRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise PreferenceRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise PreferenceRequestError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return parsed


def _event_method(event):
    return (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


def _is_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _is_non_empty_string_list(value):
    return _is_string_list(value) and bool(value)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# EOF: src/preferences/app.py

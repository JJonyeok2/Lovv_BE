import base64
import hashlib
import json
from datetime import datetime, timezone

from shared.http import error_response, json_response


ENTRY_TYPES = {"map_marker", "chat", "home_recommendation"}
COUNTRIES = {"KR", "JP"}
TRIP_TYPES = {"daytrip", "2d1n", "3d2n", "4d3n", "5d4n"}


class AgentCoreRequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event):
    try:
        return _handle_request(event or {})
    except AgentCoreRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Recommendation API is unavailable")


def _handle_request(event):
    method = _event_method(event)
    path = _event_path(event)
    if method == "OPTIONS":
        return json_response(200, {})
    if method != "POST" or path != "/api/v1/recommendations":
        return error_response(404, "NOT_FOUND", "Route not found")

    payload = _validate_payload(_json_body(event))
    return json_response(200, _mock_recommendation(payload))


def _validate_payload(body):
    entry_type = body.get("entryType")
    if entry_type not in ENTRY_TYPES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "entryType is invalid")
    country = body.get("country")
    if country not in COUNTRIES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "country is invalid")
    trip_type = body.get("tripType")
    if trip_type not in TRIP_TYPES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "tripType is invalid")
    themes = body.get("themes")
    if not isinstance(themes, list) or not themes or not all(isinstance(theme, str) and theme for theme in themes):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "themes is required")
    if not isinstance(body.get("includeFestivals"), bool):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "includeFestivals is required")
    if entry_type == "map_marker" and not body.get("destinationId"):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "destinationId is required for map marker entry")
    return body


def _mock_recommendation(payload):
    now = _now_iso()
    destination_id = payload.get("destinationId") or ((payload.get("city") or {}).get("cityId")) or f"{payload['country']}-mock-city"
    recommendation_id = _stable_id("rec", payload)
    city_name = ((payload.get("city") or {}).get("name")) or destination_id
    title = f"{city_name} {payload['tripType']} mock itinerary"
    natural_language_query = payload.get("naturalLanguageQuery") or ""

    return {
        "mock": True,
        "recommendationId": recommendation_id,
        "generatedAt": now,
        "destination": {
            "destinationId": destination_id,
            "cityId": destination_id,
            "name": city_name,
            "country": payload["country"],
            "region": None,
        },
        "requestSnapshot": {
            "entryType": payload["entryType"],
            "country": payload["country"],
            "tripType": payload["tripType"],
            "themes": payload["themes"],
            "includeFestivals": payload["includeFestivals"],
            "naturalLanguageQuery": natural_language_query,
        },
        "itinerary": {
            "tripType": payload["tripType"],
            "title": title,
            "summary": "AgentCore actual integration is deferred; this mock response is for frontend API wiring.",
            "durationLabel": _duration_label(payload["tripType"]),
            "days": [
                {
                    "day": 1,
                    "title": "Mock route",
                    "summary": "City context and preference context will be used by the follow-up AgentCore integration.",
                    "items": [
                        {
                            "itemId": _stable_id("item", {"recommendationId": recommendation_id, "order": 1}),
                            "contentId": destination_id,
                            "sortOrder": 1,
                            "timeOfDay": "morning",
                            "title": "Mock city walk",
                            "body": "Frontend can render this placeholder itinerary while Bedrock AgentCore is deferred.",
                            "reason": "Mock response only; no LLM or Bedrock call was made.",
                            "moveMinutes": 0,
                            "latitude": None,
                            "longitude": None,
                            "sourceBadges": ["mock"],
                        }
                    ],
                }
            ],
        },
        "explanations": {
            "userNotice": "Mock itinerary only. Actual Bedrock AgentCore integration is a follow-up task.",
            "confidence": "mock",
        },
        "validationStatus": {
            "singleDestination": True,
            "countrySeparated": True,
            "festivalConfirmedOnly": bool(payload["includeFestivals"]),
        },
        "saveCompatibility": {
            "targetEndpoint": "/api/v1/me/itineraries",
            "payload": {
                "sourceRecommendationId": recommendation_id,
                "title": title,
                "summary": "AgentCore mock response for frontend integration.",
                "destination": {
                    "destinationId": destination_id,
                    "name": city_name,
                    "country": payload["country"],
                    "region": None,
                },
                "tripType": payload["tripType"],
                "durationLabel": _duration_label(payload["tripType"]),
                "themes": payload["themes"],
                "conditionsSnapshot": {
                    "entryType": payload["entryType"],
                    "includeFestivals": payload["includeFestivals"],
                },
                "requestSummary": natural_language_query[:240],
                "itinerary": {
                    "days": [
                        {
                            "day": 1,
                            "title": "Mock route",
                            "items": [
                                {
                                    "itemId": _stable_id("item", {"recommendationId": recommendation_id, "order": 1}),
                                    "sortOrder": 1,
                                    "title": "Mock city walk",
                                    "body": "Mock item.",
                                }
                            ],
                        }
                    ]
                },
            },
        },
    }


def _duration_label(trip_type):
    labels = {
        "daytrip": "당일치기",
        "2d1n": "1박 2일",
        "3d2n": "2박 3일",
        "4d3n": "3박 4일",
        "5d4n": "4박 5일",
    }
    return labels.get(trip_type, trip_type)


def _stable_id(prefix, value):
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _json_body(event):
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise AgentCoreRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise AgentCoreRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return parsed


def _event_method(event):
    return (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

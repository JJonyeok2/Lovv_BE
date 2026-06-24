# @file src/shared/http.py
# @description Shared JSON response and CORS header helpers.
# @lastModified 2026-06-12

import json
import os


DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "Authorization,Content-Type,Cookie,X-CSRF-Token",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}


def json_response(status_code, body, headers=None, event=None):
    response_headers = cors_headers(event)
    if headers:
        response_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }


def empty_response(status_code, headers=None, event=None):
    response_headers = cors_headers(event)
    if headers:
        response_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": "",
    }


def error_response(status_code, code, message, event=None):
    return json_response(status_code, {"error": {"code": code, "message": message}}, event=event)


def cors_headers(event=None):
    headers = dict(DEFAULT_HEADERS)
    allowed_origins = _allowed_origins()
    origin = _header_value((event or {}).get("headers") or {}, "origin")
    if not origin and allowed_origins:
        origin = allowed_origins[0]
    if origin and origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = origin
    else:
        if "Access-Control-Allow-Origin" in headers:
            del headers["Access-Control-Allow-Origin"]
    if len(allowed_origins) > 1:
        headers["Vary"] = "Origin"
    return headers


def _allowed_origins():
    raw_value = os.environ.get("CORS_ALLOW_ORIGINS") or os.environ.get("CORS_ALLOW_ORIGIN") or "http://localhost:5173"
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


def _header_value(headers, name):
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


# EOF: src/shared/http.py

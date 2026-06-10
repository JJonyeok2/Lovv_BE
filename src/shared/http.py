import json
import os


DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "http://localhost:5173"),
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def json_response(status_code, body, headers=None):
    response_headers = dict(DEFAULT_HEADERS)
    if headers:
        response_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }


def empty_response(status_code, headers=None):
    response_headers = dict(DEFAULT_HEADERS)
    if headers:
        response_headers.update(headers)

    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": "",
    }


def error_response(status_code, code, message):
    return json_response(status_code, {"error": {"code": code, "message": message}})

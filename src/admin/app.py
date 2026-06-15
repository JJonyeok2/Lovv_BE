# @file src/admin/app.py
# @description Read-only admin user Lambda handler for Lovv API.
# @lastModified 2026-06-14

import logging

from admin.repository import RdsDataAdminUserRepository
from shared.auth import AuthTokenError
from shared.current_user import authenticated_claims
from shared.http import error_response, json_response


LOGGER = logging.getLogger(__name__)


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, repository=None):
    try:
        return _handle_request(event or {}, repository)
    except AdminRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception as error:
        LOGGER.exception("Unhandled admin API error: %s", error.__class__.__name__)
        return error_response(500, "INTERNAL_ERROR", "Internal server error")


def _handle_request(event, repository):
    method = _event_method(event)
    path = _event_path(event)

    if method == "OPTIONS":
        return json_response(200, {})

    claims = authenticated_claims(event)
    _require_admin(claims)
    repository = repository or RdsDataAdminUserRepository.from_env()

    if method == "GET" and path == "/api/v1/admin/users":
        return json_response(200, {"users": [_public_admin_user(user) for user in repository.list_users()]})

    if method == "GET" and path.startswith("/api/v1/admin/users/"):
        user_id = path.rsplit("/", 1)[-1]
        user = repository.get_user(user_id)
        if not user:
            raise AdminRequestError(404, "USER_NOT_FOUND", "User was not found")
        return json_response(200, {"user": _public_admin_user(user)})

    return error_response(404, "NOT_FOUND", "Route not found")


def _require_admin(claims):
    if "R-ADMIN" not in _claim_roles(claims):
        raise AdminRequestError(403, "FORBIDDEN", "Admin role is required")


def _claim_roles(claims):
    roles = (claims or {}).get("roles")
    if isinstance(roles, str):
        return [role.strip() for role in roles.split(",") if role.strip()]
    if isinstance(roles, list):
        return [str(role) for role in roles]
    return []


def _public_admin_user(user):
    return {
        "userId": user.get("userId"),
        "displayName": user.get("displayName"),
        "nickname": user.get("nickname"),
        "email": user.get("email"),
        "status": user.get("status"),
        "roles": user.get("roles") if "roles" in user else [],
        "createdAt": user.get("createdAt"),
        "updatedAt": user.get("updatedAt"),
        "lastLoginAt": user.get("lastLoginAt"),
        "linkedProviders": user.get("linkedProviders") or [],
        "onboardingCompleted": bool(user.get("onboardingCompleted")),
        "savedItineraryCount": int(user.get("savedItineraryCount") or 0),
    }


def _event_method(event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    return (http_context.get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


class AdminRequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# EOF: src/admin/app.py

# @file src/admin/app.py
# @description Admin console Lambda handler for Lovv API.
# @lastModified 2026-06-23

import base64
import json
import logging
from datetime import datetime, timezone

from admin.repository import RdsDataAdminUserRepository
from admin.proposals_repository import RdsDataAdminProposalRepository
from shared.auth import AuthTokenError
from shared.authorization import (
    AuthorizationError,
    ROLE_ADMIN,
    ROLE_DATA_PROVIDER,
    ROLE_LOCAL_OPERATOR,
    has_any_role,
    require_admin_access,
    require_roles,
)
from shared.http import error_response, json_response


LOGGER = logging.getLogger(__name__)
PROPOSAL_COLLECTION_PATH = "/api/v1/admin/data-proposals"
PROPOSAL_FORBIDDEN_FIELDS = {
    "roles",
    "role",
    "userId",
    "user_id",
    "ownerId",
    "createdBy",
    "created_by",
    "organizationId",
    "organization_id",
    "regionIds",
    "region_ids",
    "reviewerId",
    "reviewedBy",
    "reviewedAt",
    "status",
}
PROPOSAL_CONTENT_TYPES = {"attraction", "festival", "experience", "transport", "monthly_destination"}


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, repository=None, proposal_repository=None):
    try:
        return _handle_request(event or {}, repository, proposal_repository)
    except AdminRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthorizationError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception as error:
        LOGGER.exception("Unhandled admin API error: %s", error.__class__.__name__)
        return error_response(500, "INTERNAL_ERROR", "Internal server error")


def _handle_request(event, repository, proposal_repository):
    method = _event_method(event)
    path = _event_path(event)

    if method == "OPTIONS":
        return json_response(200, {})

    if method == "GET" and path == "/api/v1/admin/users":
        require_admin_access(event)
        repository = repository or RdsDataAdminUserRepository.from_env()
        return json_response(200, {"users": [_public_admin_user(user) for user in repository.list_users()]})

    if method == "GET" and path.startswith("/api/v1/admin/users/"):
        require_admin_access(event)
        repository = repository or RdsDataAdminUserRepository.from_env()
        user_id = path.rsplit("/", 1)[-1]
        user = repository.get_user(user_id)
        if not user:
            raise AdminRequestError(404, "USER_NOT_FOUND", "User was not found")
        return json_response(200, {"user": _public_admin_user(user)})

    if method == "POST" and path == PROPOSAL_COLLECTION_PATH:
        principal = require_roles(
            event,
            {ROLE_DATA_PROVIDER},
            message="Data provider role is required",
        )
        payload = _validate_create_proposal_payload(_json_body(event))
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        proposal = proposal_repository.create(principal, payload, _now_iso())
        return json_response(201, {"proposal": _public_proposal(proposal, include_detail=True)})

    if method == "GET" and path == PROPOSAL_COLLECTION_PATH:
        principal = require_roles(event, {ROLE_ADMIN, ROLE_DATA_PROVIDER, ROLE_LOCAL_OPERATOR})
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
        if has_any_role(principal, {ROLE_ADMIN}):
            proposals = proposal_repository.list_all(limit=limit)
        elif has_any_role(principal, {ROLE_LOCAL_OPERATOR}):
            proposals = proposal_repository.list_for_regions(
                principal.get("regionIds") or [],
                limit=limit,
            )
        else:
            proposals = proposal_repository.list_for_provider(
                principal["userId"],
                organization_ids=principal.get("organizationIds") or [],
                limit=limit,
            )
        return json_response(200, {"items": [_public_proposal(proposal) for proposal in proposals], "nextCursor": None})

    proposal_id = _proposal_id(event, path)
    if method == "GET" and proposal_id and path.endswith(f"/{proposal_id}"):
        principal = require_roles(event, {ROLE_ADMIN, ROLE_DATA_PROVIDER, ROLE_LOCAL_OPERATOR})
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        proposal = proposal_repository.get_visible(proposal_id, principal)
        if not proposal:
            raise AdminRequestError(404, "PROPOSAL_NOT_FOUND", "Data proposal was not found")
        return json_response(200, {"proposal": _public_proposal(proposal, include_detail=True)})

    return error_response(404, "NOT_FOUND", "Route not found")


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


def _validate_create_proposal_payload(payload):
    forbidden = sorted(PROPOSAL_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "Authority fields are not writable")

    content_type = payload.get("contentType")
    if content_type not in PROPOSAL_CONTENT_TYPES:
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "contentType is invalid")
    if not _non_empty_string(payload.get("regionId")):
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "regionId is required")
    if not _non_empty_string(payload.get("title")):
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "title is required")

    normalized = {
        "contentType": content_type,
        "regionId": payload.get("regionId").strip(),
        "cityId": _optional_string(payload.get("cityId")),
        "cityName": _optional_string(payload.get("cityName")),
        "title": payload.get("title").strip(),
        "description": _optional_string(payload.get("description")),
        "officialSourceName": _optional_string(payload.get("officialSourceName")),
        "officialSourceUrl": _optional_string(payload.get("officialSourceUrl")),
        "sourceUpdatedAt": _optional_string(payload.get("sourceUpdatedAt")),
        "evidenceText": _optional_string(payload.get("evidenceText")),
        "payload": _optional_object(payload.get("payload"), "payload"),
        "serviceBoundary": _optional_object(payload.get("serviceBoundary"), "serviceBoundary"),
        "gatewayCity": _optional_object(payload.get("gatewayCity"), "gatewayCity"),
    }
    return normalized


def _public_proposal(proposal, include_detail=False):
    result = {
        "proposalId": proposal.get("proposalId"),
        "proposalCode": proposal.get("proposalCode"),
        "contentType": proposal.get("contentType"),
        "regionId": proposal.get("regionId"),
        "cityId": proposal.get("cityId"),
        "cityName": proposal.get("cityName"),
        "title": proposal.get("title"),
        "description": proposal.get("description"),
        "officialSourceName": proposal.get("officialSourceName"),
        "officialSourceUrl": proposal.get("officialSourceUrl"),
        "sourceUpdatedAt": proposal.get("sourceUpdatedAt"),
        "status": proposal.get("status"),
        "createdBy": proposal.get("createdBy"),
        "organizationId": proposal.get("organizationId"),
        "submittedAt": proposal.get("submittedAt"),
        "reviewedBy": proposal.get("reviewedBy"),
        "reviewedAt": proposal.get("reviewedAt"),
        "reviewNote": proposal.get("reviewNote"),
        "createdAt": proposal.get("createdAt"),
        "updatedAt": proposal.get("updatedAt"),
    }
    if include_detail:
        result.update(
            {
                "evidenceText": proposal.get("evidenceText"),
                "payload": proposal.get("payload") or {},
                "serviceBoundary": proposal.get("serviceBoundary") or {},
                "gatewayCity": proposal.get("gatewayCity") or {},
                "approvedContentHash": proposal.get("approvedContentHash"),
            }
        )
    return result


def _json_body(event):
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise AdminRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise AdminRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "Request body must be a JSON object")
    return parsed


def _parse_limit(value):
    if value in (None, ""):
        return 20
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AdminRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    if parsed < 1:
        raise AdminRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    return min(parsed, 50)


def _event_method(event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    return (http_context.get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


def _proposal_id(event, path):
    path_parameters = event.get("pathParameters") or {}
    if path_parameters.get("proposalId"):
        return path_parameters["proposalId"]
    prefix = f"{PROPOSAL_COLLECTION_PATH}/"
    if path.startswith(prefix):
        return path[len(prefix) :].split("/", 1)[0]
    return None


def _non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _optional_string(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", "Optional text fields must be strings")
    return value.strip() or None


def _optional_object(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AdminRequestError(400, "INVALID_PROPOSAL_PAYLOAD", f"{field} must be an object")
    return dict(value)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AdminRequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# EOF: src/admin/app.py

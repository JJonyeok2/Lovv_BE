# @file src/admin/app.py
# @description Admin console Lambda handler for Lovv API.
# @lastModified 2026-06-23
#
# Routes the admin console endpoints (users + data-proposal workflow). Every
# branch authorizes against the verified token via shared.authorization, and
# ownership/authority fields are never read from the request body: the server
# derives them from the principal. See docs/specs/ADMIN_RBAC_SPEC.md.

import base64
import json
import logging
import re
from datetime import datetime, timezone

from admin.repository import RdsDataAdminUserRepository
from admin.proposals_repository import ProposalTransitionError, RdsDataAdminProposalRepository
from admin.monthly_destinations_repository import (
    MonthlyDestinationTransitionError,
    RdsDataMonthlyDestinationRepository,
)
from admin.publish_jobs_repository import (
    PublishJobTransitionError,
    RdsDataPublishJobRepository,
)
from admin.audit_logs_repository import RdsDataAuditLogRepository, build_audit_entry
from shared.rds_data import RdsDataConfigurationError
from admin.metrics_repository import (
    EVENT_COUNTER_COLUMNS,
    RdsDataDestinationMetricsRepository,
)
from admin.operations_repository import (
    NOTICE_STATUSES,
    POLICY_STATUSES,
    OperationTransitionError,
    RdsDataAdminOperationsRepository,
)
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
# Append-only audit trail of admin mutations (step 17).
AUDIT_LOGS_COLLECTION_PATH = "/api/v1/admin/audit-logs"
# Authority/ownership fields only the server may set; clients may never send
# these on create/review payloads (rejected with INVALID_*_PAYLOAD).
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

# Monthly curated destination workflow (step 11). Candidates are promoted from an
# approved proposal; clients may never set authority/state fields (server-owned).
MONTHLY_DESTINATION_COLLECTION_PATH = "/api/v1/admin/monthly-destinations"
MONTHLY_ACTIONS = {"schedule", "publish", "hide", "expire", "reject"}
MONTHLY_FORBIDDEN_FIELDS = {
    "id", "status", "publishedBy", "published_by", "publishedAt", "published_at",
    "hiddenBy", "hidden_by", "hiddenAt", "hidden_at", "createdBy", "created_by",
    "roles", "role", "userId", "user_id",
}
CURATION_MONTH_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}$")

# Publish jobs (step 12). A publish fans out into reflection jobs; clients only
# drive their status machine and never set server-owned bookkeeping fields.
PUBLISH_JOBS_COLLECTION_PATH = "/api/v1/admin/publish-jobs"
PUBLISH_JOB_ACTIONS = {"start", "succeed", "fail", "retry", "cancel"}
PUBLISH_JOB_FORBIDDEN_FIELDS = {
    "id", "status", "attemptCount", "attempt_count", "requestedBy", "requested_by",
    "startedAt", "started_at", "finishedAt", "finished_at",
    "roles", "role", "userId", "user_id",
}

# Basic aggregate metrics (step 13). Event writes increment daily aggregate
# counters only; raw user-level event logs are intentionally not stored.
METRICS_SUMMARY_PATH = "/api/v1/admin/metrics/destinations"
METRICS_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
METRICS_FORBIDDEN_FIELDS = {
    "roles", "role", "userId", "user_id", "createdBy", "created_by",
    "organizationId", "organization_id", "regionIds", "region_ids",
    "cityId", "city_id", "regionId", "region_id",
}

# Admin operations (step 16): notices and recommendation policy controls. These
# are high-impact operational changes, so only R-ADMIN may read or mutate them.
NOTICES_COLLECTION_PATH = "/api/v1/admin/notices"
POLICIES_COLLECTION_PATH = "/api/v1/admin/recommendation-policies"
NOTICE_ACTIONS = {"publish", "archive"}
POLICY_ACTIONS = {"activate", "archive"}
OPERATION_FORBIDDEN_FIELDS = {
    "id", "status", "createdBy", "created_by", "publishedBy", "published_by",
    "publishedAt", "published_at", "activatedBy", "activated_by", "activatedAt",
    "activated_at", "archivedAt", "archived_at", "roles", "role", "userId", "user_id",
}


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, repository=None, proposal_repository=None, monthly_repository=None, publish_jobs_repository=None, metrics_repository=None, operations_repository=None, audit_repository=None):
    try:
        return _handle_request(event or {}, repository, proposal_repository, monthly_repository, publish_jobs_repository, metrics_repository, operations_repository, audit_repository)
    except AdminRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except ProposalTransitionError as error:
        return error_response(error.status_code, error.code, error.message)
    except MonthlyDestinationTransitionError as error:
        return error_response(error.status_code, error.code, error.message)
    except PublishJobTransitionError as error:
        return error_response(error.status_code, error.code, error.message)
    except OperationTransitionError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthorizationError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception as error:
        LOGGER.exception("Unhandled admin API error: %s", error.__class__.__name__)
        return error_response(500, "INTERNAL_ERROR", "Internal server error")


def _handle_request(event, repository, proposal_repository, monthly_repository=None, publish_jobs_repository=None, metrics_repository=None, operations_repository=None, audit_repository=None):
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

    notice_id = _notice_id(path)
    notice_action = _notice_action(path, notice_id)
    policy_id = _policy_id(path)
    policy_action = _policy_action(path, policy_id)

    if method == "GET" and path == NOTICES_COLLECTION_PATH:
        require_admin_access(event)
        query = event.get("queryStringParameters") or {}
        status = _validate_notice_status(query.get("status")) if query.get("status") else None
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        notices = operations_repository.list_notices(status=status, limit=_parse_limit(query.get("limit")))
        return json_response(200, {"items": [_public_notice(notice) for notice in notices], "nextCursor": None})

    if method == "POST" and path == NOTICES_COLLECTION_PATH:
        principal = require_admin_access(event)
        payload = _validate_notice_payload(_json_body(event))
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        notice = operations_repository.create_notice(principal, payload, _now_iso())
        _record_audit(audit_repository, principal, "notice.create", "notice", notice.get("id"), _now_iso(), after={"status": notice.get("status")})
        return json_response(201, {"notice": _public_notice(notice)})

    if method == "POST" and notice_id and notice_action in NOTICE_ACTIONS:
        principal = require_admin_access(event)
        payload = _validate_empty_operation_payload(_json_body(event))
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        notice = operations_repository.transition_notice(notice_id, notice_action, principal, _now_iso())
        if not notice:
            raise AdminRequestError(404, "NOTICE_NOT_FOUND", "Notice was not found")
        _record_audit(audit_repository, principal, f"notice.{notice_action}", "notice", notice_id, _now_iso(), after={"status": notice.get("status")})
        return json_response(200, {"notice": _public_notice(notice)})

    if method == "GET" and path == POLICIES_COLLECTION_PATH:
        require_admin_access(event)
        query = event.get("queryStringParameters") or {}
        status = _validate_policy_status(query.get("status")) if query.get("status") else None
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        policies = operations_repository.list_policies(status=status, limit=_parse_limit(query.get("limit")))
        return json_response(200, {"items": [_public_policy(policy) for policy in policies], "nextCursor": None})

    if method == "POST" and path == POLICIES_COLLECTION_PATH:
        principal = require_admin_access(event)
        payload = _validate_policy_payload(_json_body(event))
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        policy = operations_repository.create_policy(principal, payload, _now_iso())
        _record_audit(audit_repository, principal, "recommendation_policy.create", "recommendation_policy", policy.get("id"), _now_iso(), after={"status": policy.get("status")})
        return json_response(201, {"policy": _public_policy(policy)})

    if method == "POST" and policy_id and policy_action in POLICY_ACTIONS:
        principal = require_admin_access(event)
        payload = _validate_empty_operation_payload(_json_body(event))
        operations_repository = operations_repository or RdsDataAdminOperationsRepository.from_env()
        policy = operations_repository.transition_policy(policy_id, policy_action, principal, _now_iso())
        if not policy:
            raise AdminRequestError(404, "POLICY_NOT_FOUND", "Recommendation policy was not found")
        _record_audit(audit_repository, principal, f"recommendation_policy.{policy_action}", "recommendation_policy", policy_id, _now_iso(), after={"status": policy.get("status")})
        return json_response(200, {"policy": _public_policy(policy)})

    if method == "POST" and path == PROPOSAL_COLLECTION_PATH:
        # Only data providers author proposals. Admins review but cannot create:
        # roles are not hierarchical (R-ADMIN does not imply R-DATA-PROVIDER).
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
        # Visibility is scoped by role: admin sees all, local operator sees its
        # assigned regions, provider sees its own/organization proposals.
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
    proposal_action = _proposal_action(path, proposal_id)
    if method == "POST" and proposal_id and proposal_action in {"review", "approve", "reject"}:
        # State changes are admin-only; the repository also blocks reviewing
        # one's own proposal (SELF_REVIEW_FORBIDDEN).
        principal = require_admin_access(event)
        payload = _validate_review_payload(_json_body(event), require_note=proposal_action == "reject")
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        proposal = proposal_repository.transition(
            proposal_id,
            _review_action_to_status(proposal_action),
            principal,
            _now_iso(),
            note=payload.get("reviewNote"),
        )
        if not proposal:
            raise AdminRequestError(404, "PROPOSAL_NOT_FOUND", "Data proposal was not found")
        _record_audit(audit_repository, principal, f"data_proposal.{proposal_action}", "data_proposal", proposal_id, _now_iso(), after={"status": proposal.get("status")})
        return json_response(200, {"proposal": _public_proposal(proposal, include_detail=True)})

    if method == "GET" and proposal_id and proposal_action == "history":
        principal = require_roles(event, {ROLE_ADMIN, ROLE_DATA_PROVIDER, ROLE_LOCAL_OPERATOR})
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
        history = proposal_repository.list_history_visible(proposal_id, principal, limit=limit)
        if history is None:
            raise AdminRequestError(404, "PROPOSAL_NOT_FOUND", "Data proposal was not found")
        return json_response(200, {"items": [_public_proposal_history(item) for item in history], "nextCursor": None})

    if method == "GET" and proposal_id and path.endswith(f"/{proposal_id}"):
        principal = require_roles(event, {ROLE_ADMIN, ROLE_DATA_PROVIDER, ROLE_LOCAL_OPERATOR})
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        proposal = proposal_repository.get_visible(proposal_id, principal)
        if not proposal:
            raise AdminRequestError(404, "PROPOSAL_NOT_FOUND", "Data proposal was not found")
        return json_response(200, {"proposal": _public_proposal(proposal, include_detail=True)})

    monthly_id = _monthly_destination_id(path)
    monthly_action = _monthly_action(path, monthly_id)

    if method == "POST" and path == MONTHLY_DESTINATION_COLLECTION_PATH:
        # Promote an approved proposal into a monthly candidate. Admin-only; the
        # city/region/source fields are copied from the proposal so the candidate
        # cannot drift from the content that was actually approved.
        principal = require_admin_access(event)
        payload = _validate_monthly_create_payload(_json_body(event))
        proposal_repository = proposal_repository or RdsDataAdminProposalRepository.from_env()
        source = proposal_repository.get_visible(payload["sourceProposalId"], principal)
        if not source:
            raise AdminRequestError(404, "PROPOSAL_NOT_FOUND", "Source proposal was not found")
        if source.get("status") != "approved":
            raise AdminRequestError(409, "PROPOSAL_NOT_APPROVED", "Only approved proposals can be promoted")
        record = _merge_promotion_payload(payload, source)
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        destination = monthly_repository.create(principal, record, _now_iso())
        _record_audit(
            audit_repository,
            principal,
            "monthly_destination.promote",
            "monthly_destination",
            destination.get("id"),
            _now_iso(),
            after={"status": destination.get("status")},
            metadata={"sourceProposalId": destination.get("sourceProposalId")},
        )
        return json_response(201, {"destination": _public_monthly_destination(destination)})

    if method == "GET" and path == MONTHLY_DESTINATION_COLLECTION_PATH:
        # Admin sees every region; a local operator only its assigned regions.
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        query = event.get("queryStringParameters") or {}
        curation_month = _validate_curation_month(query.get("month")) if query.get("month") else None
        status = _validate_monthly_status(query.get("status")) if query.get("status") else None
        limit = _parse_limit(query.get("limit"))
        if has_any_role(principal, {ROLE_ADMIN}):
            destinations = monthly_repository.list_all(
                curation_month=curation_month,
                region_id=_optional_string(query.get("regionId")),
                status=status,
                limit=limit,
            )
        else:
            destinations = monthly_repository.list_for_regions(
                principal.get("regionIds") or [],
                curation_month=curation_month,
                status=status,
                limit=limit,
            )
        return json_response(200, {"items": [_public_monthly_destination(item) for item in destinations], "nextCursor": None})

    if method == "GET" and path == METRICS_SUMMARY_PATH:
        # Admin sees all destinations; local operators are scoped to their
        # assigned regions. Metrics remain aggregated per destination/day.
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        query = event.get("queryStringParameters") or {}
        start_date = _validate_metric_date(query.get("startDate"), "startDate") if query.get("startDate") else None
        end_date = _validate_metric_date(query.get("endDate"), "endDate") if query.get("endDate") else None
        limit = _parse_limit(query.get("limit"))
        metrics_repository = metrics_repository or RdsDataDestinationMetricsRepository.from_env()
        if has_any_role(principal, {ROLE_ADMIN}):
            items = metrics_repository.list_summary(
                start_date=start_date,
                end_date=end_date,
                region_id=_optional_string(query.get("regionId")),
                limit=limit,
            )
        else:
            items = metrics_repository.list_summary(
                start_date=start_date,
                end_date=end_date,
                region_ids=principal.get("regionIds") or [],
                limit=limit,
            )
        return json_response(200, {"items": items, "nextCursor": None})

    if method == "POST" and monthly_id and monthly_action in MONTHLY_ACTIONS:
        # Publish-state transitions are admin-only and validated against the state
        # machine in the repository (409 MONTHLY_TRANSITION_FORBIDDEN if illegal).
        principal = require_admin_access(event)
        payload = _validate_monthly_action_payload(_json_body(event))
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        now = _now_iso()
        destination = monthly_repository.transition(
            monthly_id, monthly_action, principal, now, payload=payload
        )
        if not destination:
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        body = {"destination": _public_monthly_destination(destination)}
        # Reflecting approved data = publishing it. A publish fans out into the
        # downstream reflection jobs so the console can track each surface.
        if monthly_action == "publish":
            publish_jobs_repository = publish_jobs_repository or RdsDataPublishJobRepository.from_env()
            jobs = publish_jobs_repository.enqueue_for_destination(monthly_id, principal, now)
            body["reflectionJobs"] = [_public_publish_job(job) for job in jobs]
        _record_audit(audit_repository, principal, f"monthly_destination.{monthly_action}", "monthly_destination", monthly_id, now, after={"status": destination.get("status")}, metadata=({"reflectionJobCount": len(body.get("reflectionJobs", []))} if monthly_action == "publish" else None))
        return json_response(200, body)

    if method == "POST" and monthly_id and monthly_action == "events":
        # Event collection is limited to admin/local-operator sessions in this
        # admin PoC. Product-facing anonymous collection can be split into a
        # separate public route later; this path is for controlled verification.
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        payload = _validate_metrics_event_payload(_json_body(event))
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        destination = monthly_repository.get(monthly_id)
        if not destination:
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        if not has_any_role(principal, {ROLE_ADMIN}) and destination.get("regionId") not in set(principal.get("regionIds") or []):
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        metrics_repository = metrics_repository or RdsDataDestinationMetricsRepository.from_env()
        metric = metrics_repository.record_event(
            destination,
            payload["eventType"],
            payload["metricDate"],
            _now_iso(),
            increment=payload["increment"],
            distinct_user_increment=payload["distinctUserIncrement"],
        )
        return json_response(202, {"metric": metric})

    if method == "GET" and monthly_id and path.endswith(f"/{monthly_id}"):
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        destination = monthly_repository.get(monthly_id)
        if not destination:
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        if not has_any_role(principal, {ROLE_ADMIN}) and destination.get("regionId") not in set(principal.get("regionIds") or []):
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        return json_response(200, {"destination": _public_monthly_destination(destination)})

    if method == "GET" and monthly_id and monthly_action == "metrics":
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        destination = monthly_repository.get(monthly_id)
        if not destination:
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        if not has_any_role(principal, {ROLE_ADMIN}) and destination.get("regionId") not in set(principal.get("regionIds") or []):
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        query = event.get("queryStringParameters") or {}
        start_date = _validate_metric_date(query.get("startDate"), "startDate") if query.get("startDate") else None
        end_date = _validate_metric_date(query.get("endDate"), "endDate") if query.get("endDate") else None
        limit = _parse_limit(query.get("limit"))
        metrics_repository = metrics_repository or RdsDataDestinationMetricsRepository.from_env()
        items = metrics_repository.list_for_destination(
            monthly_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return json_response(200, {"items": items, "nextCursor": None})

    if method == "GET" and monthly_id and monthly_action == "publish-jobs":
        # Reflection history for one destination (admin all, operator own regions).
        principal = require_roles(event, {ROLE_ADMIN, ROLE_LOCAL_OPERATOR})
        monthly_repository = monthly_repository or RdsDataMonthlyDestinationRepository.from_env()
        destination = monthly_repository.get(monthly_id)
        if not destination:
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        if not has_any_role(principal, {ROLE_ADMIN}) and destination.get("regionId") not in set(principal.get("regionIds") or []):
            raise AdminRequestError(404, "MONTHLY_DESTINATION_NOT_FOUND", "Monthly destination was not found")
        publish_jobs_repository = publish_jobs_repository or RdsDataPublishJobRepository.from_env()
        limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
        jobs = publish_jobs_repository.list_for_destination(monthly_id, limit=limit)
        return json_response(200, {"items": [_public_publish_job(job) for job in jobs], "nextCursor": None})

    publish_job_id = _publish_job_id(path)
    publish_job_action = _publish_job_action(path, publish_job_id)
    if method == "POST" and publish_job_id and publish_job_action in PUBLISH_JOB_ACTIONS:
        # Drive a reflection job through its status machine. Admin-only; the state
        # machine is enforced in the repository (409 PUBLISH_JOB_TRANSITION_FORBIDDEN).
        principal = require_admin_access(event)
        payload = _validate_publish_job_action_payload(_json_body(event))
        publish_jobs_repository = publish_jobs_repository or RdsDataPublishJobRepository.from_env()
        job = publish_jobs_repository.transition(
            publish_job_id, publish_job_action, principal, _now_iso(), payload=payload
        )
        if not job:
            raise AdminRequestError(404, "PUBLISH_JOB_NOT_FOUND", "Publish job was not found")
        _record_audit(audit_repository, principal, f"publish_job.{publish_job_action}", "publish_job", publish_job_id, _now_iso(), after={"status": job.get("status")})
        return json_response(200, {"job": _public_publish_job(job)})

    if method == "GET" and path == AUDIT_LOGS_COLLECTION_PATH:
        # Admin-only audit trail read. This is also the monitoring surface: every
        # admin mutation is recorded here with actor + result.
        require_admin_access(event)
        audit_repository = audit_repository or RdsDataAuditLogRepository.from_env()
        query = event.get("queryStringParameters") or {}
        entries = audit_repository.list(
            action=_optional_string(query.get("action")),
            resource_type=_optional_string(query.get("resourceType")),
            result=_optional_string(query.get("result")),
            actor_user_id=_optional_string(query.get("actorUserId")),
            limit=_parse_limit(query.get("limit")),
        )
        return json_response(200, {"items": [_public_audit_log(entry) for entry in entries], "nextCursor": None})

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


def _validate_review_payload(payload, require_note=False):
    forbidden = sorted(PROPOSAL_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_REVIEW_PAYLOAD", "Authority fields are not writable")

    allowed = {"reviewNote", "note"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_REVIEW_PAYLOAD", "Review payload contains unsupported fields")

    note = payload.get("reviewNote", payload.get("note"))
    if note not in (None, "") and not isinstance(note, str):
        raise AdminRequestError(400, "INVALID_REVIEW_PAYLOAD", "reviewNote must be a string")
    note = note.strip() if isinstance(note, str) else None
    note = note or None
    if require_note and not note:
        raise AdminRequestError(400, "INVALID_REVIEW_PAYLOAD", "reviewNote is required")
    return {"reviewNote": note}


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


def _public_proposal_history(item):
    return {
        "historyId": item.get("historyId"),
        "proposalId": item.get("proposalId"),
        "action": item.get("action"),
        "fromStatus": item.get("fromStatus"),
        "toStatus": item.get("toStatus"),
        "actorUserId": item.get("actorUserId"),
        "actorRoles": item.get("actorRoles") or [],
        "note": item.get("note"),
        "metadata": item.get("metadata") or {},
        "createdAt": item.get("createdAt"),
    }


def _validate_monthly_create_payload(payload):
    forbidden = sorted(MONTHLY_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "Authority fields are not writable")
    if not _non_empty_string(payload.get("sourceProposalId")):
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "sourceProposalId is required")
    curation_month = _validate_curation_month(payload.get("curationMonth"))
    theme_codes = _validate_theme_codes(payload.get("themeCodes"))
    return {
        "sourceProposalId": payload.get("sourceProposalId").strip(),
        "curationMonth": curation_month,
        "themeCodes": theme_codes,
        # Optional overrides; anything omitted falls back to the source proposal.
        "cityId": _optional_string(payload.get("cityId")),
        "cityName": _optional_string(payload.get("cityName")),
        "regionId": _optional_string(payload.get("regionId")),
        "officialSourceName": _optional_string(payload.get("officialSourceName")),
        "officialSourceUrl": _optional_string(payload.get("officialSourceUrl")),
        "sourceUpdatedAt": _optional_string(payload.get("sourceUpdatedAt")),
        "validFrom": _optional_string(payload.get("validFrom")),
        "validUntil": _optional_string(payload.get("validUntil")),
    }


def _merge_promotion_payload(payload, source):
    # Prefer explicit overrides, else copy from the approved proposal so the
    # candidate stays anchored to the content that was actually reviewed.
    return {
        "sourceProposalId": payload["sourceProposalId"],
        "curationMonth": payload["curationMonth"],
        "themeCodes": payload["themeCodes"],
        "cityId": payload.get("cityId") or source.get("cityId"),
        "cityName": payload.get("cityName") or source.get("cityName"),
        "regionId": payload.get("regionId") or source.get("regionId"),
        "officialSourceName": payload.get("officialSourceName") or source.get("officialSourceName"),
        "officialSourceUrl": payload.get("officialSourceUrl") or source.get("officialSourceUrl"),
        "sourceUpdatedAt": payload.get("sourceUpdatedAt") or source.get("sourceUpdatedAt"),
        "validFrom": payload.get("validFrom"),
        "validUntil": payload.get("validUntil"),
        "serviceBoundary": source.get("serviceBoundary") or {},
        "gatewayCity": source.get("gatewayCity") or {},
    }


def _validate_monthly_action_payload(payload):
    forbidden = sorted(MONTHLY_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "Authority fields are not writable")
    allowed = {"reason", "validFrom", "validUntil"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "Action payload contains unsupported fields")
    for key in ("reason", "validFrom", "validUntil"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, str):
            raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", f"{key} must be a string")
    return {
        "reason": _optional_string(payload.get("reason")),
        "validFrom": _optional_string(payload.get("validFrom")),
        "validUntil": _optional_string(payload.get("validUntil")),
    }


def _validate_curation_month(value):
    if not _non_empty_string(value) or not CURATION_MONTH_PATTERN.match(value.strip()):
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "curationMonth must be in YYYY-MM format")
    return value.strip()


def _validate_theme_codes(value):
    if not isinstance(value, list) or not value:
        raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "themeCodes must be a non-empty array")
    codes = []
    for item in value:
        if not _non_empty_string(item):
            raise AdminRequestError(400, "INVALID_MONTHLY_PAYLOAD", "themeCodes must contain non-empty strings")
        text = item.strip()
        if text not in codes:
            codes.append(text)
    return codes


def _validate_monthly_status(value):
    statuses = {"candidate", "scheduled", "published", "hidden", "expired", "rejected"}
    if value not in statuses:
        raise AdminRequestError(400, "VALIDATION_ERROR", "status filter is invalid")
    return value


def _public_monthly_destination(destination):
    return {
        "id": destination.get("id"),
        "cityId": destination.get("cityId"),
        "cityName": destination.get("cityName"),
        "regionId": destination.get("regionId"),
        "sourceProposalId": destination.get("sourceProposalId"),
        "curationMonth": destination.get("curationMonth"),
        "themeCodes": destination.get("themeCodes") or [],
        "officialSourceName": destination.get("officialSourceName"),
        "officialSourceUrl": destination.get("officialSourceUrl"),
        "sourceUpdatedAt": destination.get("sourceUpdatedAt"),
        "validFrom": destination.get("validFrom"),
        "validUntil": destination.get("validUntil"),
        "status": destination.get("status"),
        "publishReason": destination.get("publishReason"),
        "publishedBy": destination.get("publishedBy"),
        "publishedAt": destination.get("publishedAt"),
        "hiddenBy": destination.get("hiddenBy"),
        "hiddenAt": destination.get("hiddenAt"),
        "hiddenReason": destination.get("hiddenReason"),
        "createdAt": destination.get("createdAt"),
        "updatedAt": destination.get("updatedAt"),
    }


def _monthly_destination_id(path):
    prefix = f"{MONTHLY_DESTINATION_COLLECTION_PATH}/"
    if path.startswith(prefix):
        return path[len(prefix):].split("/", 1)[0] or None
    return None


def _monthly_action(path, destination_id):
    if not destination_id:
        return None
    prefix = f"{MONTHLY_DESTINATION_COLLECTION_PATH}/{destination_id}/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].strip("/") or None


def _validate_publish_job_action_payload(payload):
    forbidden = sorted(PUBLISH_JOB_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_PUBLISH_JOB_PAYLOAD", "Authority fields are not writable")
    allowed = {"errorCode", "errorMessage"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_PUBLISH_JOB_PAYLOAD", "Action payload contains unsupported fields")
    for key in ("errorCode", "errorMessage"):
        value = payload.get(key)
        if value not in (None, "") and not isinstance(value, str):
            raise AdminRequestError(400, "INVALID_PUBLISH_JOB_PAYLOAD", f"{key} must be a string")
    return {
        "errorCode": _optional_string(payload.get("errorCode")),
        "errorMessage": _optional_string(payload.get("errorMessage")),
    }


def _validate_metrics_event_payload(payload):
    forbidden = sorted(METRICS_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", "Authority fields are not writable")
    allowed = {"eventType", "metricDate", "occurredAt", "increment", "distinctUserIncrement"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", "Metrics event payload contains unsupported fields")
    event_type = payload.get("eventType")
    if event_type not in EVENT_COUNTER_COLUMNS:
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", "eventType is invalid")
    metric_date = payload.get("metricDate")
    if not metric_date and payload.get("occurredAt"):
        metric_date = str(payload.get("occurredAt"))[:10]
    metric_date = _validate_metric_date(metric_date, "metricDate")
    return {
        "eventType": event_type,
        "metricDate": metric_date,
        "increment": _parse_positive_int(payload.get("increment"), field="increment", default=1, max_value=100),
        "distinctUserIncrement": _parse_non_negative_int(
            payload.get("distinctUserIncrement"),
            field="distinctUserIncrement",
            default=0,
            max_value=100,
        ),
    }


def _validate_metric_date(value, field):
    if not _non_empty_string(value) or not METRICS_DATE_PATTERN.match(value.strip()):
        raise AdminRequestError(400, "INVALID_METRICS_DATE", f"{field} must be in YYYY-MM-DD format")
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise AdminRequestError(400, "INVALID_METRICS_DATE", f"{field} must be a valid calendar date")
    return value.strip()


def _validate_notice_payload(payload):
    _reject_operation_forbidden_fields(payload, "INVALID_NOTICE_PAYLOAD")
    allowed = {"title", "body", "audience", "severity", "startsAt", "endsAt"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_NOTICE_PAYLOAD", "Notice payload contains unsupported fields")
    if not _non_empty_string(payload.get("title")):
        raise AdminRequestError(400, "INVALID_NOTICE_PAYLOAD", "title is required")
    if not _non_empty_string(payload.get("body")):
        raise AdminRequestError(400, "INVALID_NOTICE_PAYLOAD", "body is required")
    audience = _optional_string(payload.get("audience")) or "all"
    if audience not in {"all", "traveler", "local_operator", "data_provider", "admin"}:
        raise AdminRequestError(400, "INVALID_NOTICE_PAYLOAD", "audience is invalid")
    severity = _optional_string(payload.get("severity")) or "info"
    if severity not in {"info", "warning", "critical"}:
        raise AdminRequestError(400, "INVALID_NOTICE_PAYLOAD", "severity is invalid")
    return {
        "title": payload["title"].strip(),
        "body": payload["body"].strip(),
        "audience": audience,
        "severity": severity,
        "startsAt": _optional_string(payload.get("startsAt")),
        "endsAt": _optional_string(payload.get("endsAt")),
    }


def _validate_policy_payload(payload):
    _reject_operation_forbidden_fields(payload, "INVALID_POLICY_PAYLOAD")
    allowed = {"policyKey", "title", "description", "rules", "priority", "effectiveFrom", "effectiveUntil"}
    unexpected = sorted(set(payload.keys()) - allowed)
    if unexpected:
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "Recommendation policy payload contains unsupported fields")
    if not _non_empty_string(payload.get("policyKey")):
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "policyKey is required")
    if not _non_empty_string(payload.get("title")):
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "title is required")
    rules = payload.get("rules")
    if rules is not None and not isinstance(rules, dict):
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "rules must be an object")
    priority = _parse_operation_priority(payload.get("priority"))
    return {
        "policyKey": payload["policyKey"].strip(),
        "title": payload["title"].strip(),
        "description": _optional_string(payload.get("description")),
        "rules": dict(rules or {}),
        "priority": priority,
        "effectiveFrom": _optional_string(payload.get("effectiveFrom")),
        "effectiveUntil": _optional_string(payload.get("effectiveUntil")),
    }


def _validate_empty_operation_payload(payload):
    _reject_operation_forbidden_fields(payload, "INVALID_OPERATION_PAYLOAD")
    if payload:
        raise AdminRequestError(400, "INVALID_OPERATION_PAYLOAD", "Action payload contains unsupported fields")
    return {}


def _reject_operation_forbidden_fields(payload, code):
    forbidden = sorted(OPERATION_FORBIDDEN_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AdminRequestError(400, code, "Authority fields are not writable")


def _validate_notice_status(value):
    if value not in NOTICE_STATUSES:
        raise AdminRequestError(400, "VALIDATION_ERROR", "notice status filter is invalid")
    return value


def _validate_policy_status(value):
    if value not in POLICY_STATUSES:
        raise AdminRequestError(400, "VALIDATION_ERROR", "policy status filter is invalid")
    return value


def _parse_operation_priority(value):
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "priority must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "priority must be an integer")
    if parsed < 0:
        raise AdminRequestError(400, "INVALID_POLICY_PAYLOAD", "priority must be non-negative")
    return min(parsed, 1000)


def _parse_positive_int(value, field, default, max_value):
    parsed = _parse_non_negative_int(value, field, default, max_value)
    if parsed < 1:
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", f"{field} must be a positive integer")
    return parsed


def _parse_non_negative_int(value, field, default, max_value):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", f"{field} must be an integer")
    if parsed < 0:
        raise AdminRequestError(400, "INVALID_METRICS_EVENT_PAYLOAD", f"{field} must be non-negative")
    return min(parsed, max_value)


def _public_notice(notice):
    return {
        "id": notice.get("id"),
        "title": notice.get("title"),
        "body": notice.get("body"),
        "audience": notice.get("audience"),
        "severity": notice.get("severity"),
        "status": notice.get("status"),
        "startsAt": notice.get("startsAt"),
        "endsAt": notice.get("endsAt"),
        "createdBy": notice.get("createdBy"),
        "publishedBy": notice.get("publishedBy"),
        "publishedAt": notice.get("publishedAt"),
        "archivedAt": notice.get("archivedAt"),
        "createdAt": notice.get("createdAt"),
        "updatedAt": notice.get("updatedAt"),
    }


def _public_policy(policy):
    return {
        "id": policy.get("id"),
        "policyKey": policy.get("policyKey"),
        "title": policy.get("title"),
        "description": policy.get("description"),
        "rules": policy.get("rules") or {},
        "priority": policy.get("priority") or 0,
        "status": policy.get("status"),
        "effectiveFrom": policy.get("effectiveFrom"),
        "effectiveUntil": policy.get("effectiveUntil"),
        "createdBy": policy.get("createdBy"),
        "activatedBy": policy.get("activatedBy"),
        "activatedAt": policy.get("activatedAt"),
        "archivedAt": policy.get("archivedAt"),
        "createdAt": policy.get("createdAt"),
        "updatedAt": policy.get("updatedAt"),
    }


def _public_publish_job(job):
    return {
        "id": job.get("id"),
        "proposalId": job.get("proposalId"),
        "monthlyCuratedDestinationId": job.get("monthlyCuratedDestinationId"),
        "jobType": job.get("jobType"),
        "status": job.get("status"),
        "attemptCount": job.get("attemptCount") or 0,
        "lastErrorCode": job.get("lastErrorCode"),
        "lastErrorMessage": job.get("lastErrorMessage"),
        "requestedBy": job.get("requestedBy"),
        "startedAt": job.get("startedAt"),
        "finishedAt": job.get("finishedAt"),
        "createdAt": job.get("createdAt"),
        "updatedAt": job.get("updatedAt"),
    }


def _publish_job_id(path):
    prefix = f"{PUBLISH_JOBS_COLLECTION_PATH}/"
    if path.startswith(prefix):
        return path[len(prefix):].split("/", 1)[0] or None
    return None


def _publish_job_action(path, job_id):
    if not job_id:
        return None
    prefix = f"{PUBLISH_JOBS_COLLECTION_PATH}/{job_id}/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].strip("/") or None


def _notice_id(path):
    prefix = f"{NOTICES_COLLECTION_PATH}/"
    if path.startswith(prefix):
        return path[len(prefix):].split("/", 1)[0] or None
    return None


def _notice_action(path, notice_id):
    if not notice_id:
        return None
    prefix = f"{NOTICES_COLLECTION_PATH}/{notice_id}/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].strip("/") or None


def _policy_id(path):
    prefix = f"{POLICIES_COLLECTION_PATH}/"
    if path.startswith(prefix):
        return path[len(prefix):].split("/", 1)[0] or None
    return None


def _policy_action(path, policy_id):
    if not policy_id:
        return None
    prefix = f"{POLICIES_COLLECTION_PATH}/{policy_id}/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix):].strip("/") or None


def _record_audit(audit_repository, principal, action, resource_type, resource_id, now, result="succeeded", after=None, metadata=None):
    # Best-effort audit write: a logging failure must never fail the business
    # operation it records, so any error is swallowed (and logged).
    try:
        repository = audit_repository or RdsDataAuditLogRepository.from_env()
        entry = build_audit_entry(
            principal,
            action,
            resource_type,
            resource_id,
            now,
            result=result,
            after=after,
            metadata=metadata,
        )
        repository.record(entry)
    except RdsDataConfigurationError:
        # Audit storage is not configured (e.g. local/dev or unit tests). Skip
        # quietly: the business operation already succeeded.
        LOGGER.debug("Audit storage not configured; skipping audit for %s", action)
    except Exception:
        LOGGER.exception("Failed to record admin audit log for action %s", action)


def _public_audit_log(entry):
    return {
        "id": entry.get("id"),
        "occurredAt": entry.get("occurredAt"),
        "actorUserId": entry.get("actorUserId"),
        "rolesSnapshot": entry.get("rolesSnapshot") or [],
        "action": entry.get("action"),
        "resourceType": entry.get("resourceType"),
        "resourceId": entry.get("resourceId"),
        "result": entry.get("result"),
        "reasonCode": entry.get("reasonCode"),
        "afterSummary": entry.get("afterSummary") or {},
        "metadata": entry.get("metadata") or {},
    }


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


def _proposal_action(path, proposal_id):
    if not proposal_id:
        return None
    prefix = f"{PROPOSAL_COLLECTION_PATH}/{proposal_id}/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :].strip("/") or None


def _review_action_to_status(action):
    return {
        "review": "in_review",
        "approve": "approved",
        "reject": "rejected",
    }[action]


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

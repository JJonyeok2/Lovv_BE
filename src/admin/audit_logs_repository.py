# @file src/admin/audit_logs_repository.py
# @description Admin audit log repository (step 17: audit trail + monitoring).
# @lastModified 2026-06-24
#
# Append-only record of every admin mutation. Each entry snapshots who acted
# (actor + roles/org/region scopes), what action on which resource, and the
# result, so the console and operators can reconstruct an audit trail. Writes are
# best-effort: an audit failure must never fail the business operation it records.
# Both the call site (admin.app._record_audit) and RdsDataAuditLogRepository.record
# swallow-and-log write errors so a DB/network blip cannot break an admin action.

import logging
import os
import uuid

from shared.database import create_database_client
from shared.rds_data import json_dumps, json_loads


LOGGER = logging.getLogger(__name__)


AUDIT_RESULTS = {"allowed", "denied", "succeeded", "failed"}


def build_audit_entry(
    principal,
    action,
    resource_type,
    resource_id,
    now,
    result="succeeded",
    reason_code=None,
    before=None,
    after=None,
    metadata=None,
):
    # Normalize a principal + action into a storable audit row (camelCase domain
    # dict). Roles/org/region are snapshotted so the trail stays accurate even if
    # the actor's access changes later.
    principal = principal or {}
    return {
        "id": str(uuid.uuid4()),
        "occurredAt": now,
        "actorUserId": principal.get("userId"),
        "sessionId": principal.get("sessionId") or None,
        "rolesSnapshot": list(principal.get("roles") or []),
        "organizationIdsSnapshot": list(principal.get("organizationIds") or []),
        "regionIdsSnapshot": list(principal.get("regionIds") or []),
        "action": action,
        "resourceType": resource_type,
        "resourceId": str(resource_id) if resource_id is not None else None,
        "result": result if result in AUDIT_RESULTS else "succeeded",
        "reasonCode": reason_code,
        "beforeSummary": before or {},
        "afterSummary": after or {},
        "metadata": metadata or {},
        "createdAt": now,
    }


class RdsDataAuditLogRepository:
    def __init__(self, rds_client=None, table=None):
        self.rds = rds_client or create_database_client()
        self.table = table or os.environ.get("ADMIN_AUDIT_LOGS_TABLE_NAME", "admin_audit_logs")

    @classmethod
    def from_env(cls):
        return cls()

    def record(self, entry):
        # Best-effort write: audit logging must never fail the business operation
        # it records, so a DB/network failure here is logged and swallowed.
        try:
            self.rds.execute(
                f"""
                INSERT INTO {self.table}
                  (id, occurred_at, actor_user_id, session_id, roles_snapshot,
                   organization_ids_snapshot, region_ids_snapshot, action, resource_type,
                   resource_id, result, reason_code, request_id, before_summary_json,
                   after_summary_json, metadata_json, created_at)
                VALUES
                  (:id, :occurred_at, :actor_user_id, :session_id, :roles_snapshot,
                   :organization_ids_snapshot, :region_ids_snapshot, :action, :resource_type,
                   :resource_id, :result, :reason_code, :request_id, :before_summary_json,
                   :after_summary_json, :metadata_json, :created_at)
                """,
                _row_params(entry),
                include_result_metadata=False,
            )
        except Exception:
            LOGGER.exception("Failed to persist admin audit log (action=%s)", entry.get("action"))
        return entry

    def list(self, action=None, resource_type=None, result=None, actor_user_id=None, limit=50):
        clauses = []
        params = {}
        if action:
            clauses.append("action = :action")
            params["action"] = action
        if resource_type:
            clauses.append("resource_type = :resource_type")
            params["resource_type"] = resource_type
        if result:
            clauses.append("result = :result")
            params["result"] = result
        if actor_user_id:
            clauses.append("actor_user_id = :actor_user_id")
            params["actor_user_id"] = actor_user_id
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.table}
            {(' WHERE ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY occurred_at DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_entry_from_row(row) for row in rows]


class InMemoryAuditLogRepository:
    def __init__(self):
        self.entries = []

    def record(self, entry):
        self.entries.append(dict(entry))
        return dict(entry)

    def list(self, action=None, resource_type=None, result=None, actor_user_id=None, limit=50):
        items = [
            entry
            for entry in self.entries
            if (not action or entry.get("action") == action)
            and (not resource_type or entry.get("resourceType") == resource_type)
            and (not result or entry.get("result") == result)
            and (not actor_user_id or entry.get("actorUserId") == actor_user_id)
        ]
        items.sort(key=lambda entry: entry.get("occurredAt") or "", reverse=True)
        return [dict(entry) for entry in items[:limit]]


def _row_params(entry):
    return {
        "id": entry.get("id"),
        "occurred_at": entry.get("occurredAt"),
        "actor_user_id": entry.get("actorUserId"),
        "session_id": entry.get("sessionId"),
        "roles_snapshot": json_dumps(entry.get("rolesSnapshot") or []),
        "organization_ids_snapshot": json_dumps(entry.get("organizationIdsSnapshot") or []),
        "region_ids_snapshot": json_dumps(entry.get("regionIdsSnapshot") or []),
        "action": entry.get("action"),
        "resource_type": entry.get("resourceType"),
        "resource_id": entry.get("resourceId"),
        "result": entry.get("result"),
        "reason_code": entry.get("reasonCode"),
        "request_id": entry.get("requestId"),
        "before_summary_json": json_dumps(entry.get("beforeSummary") or {}),
        "after_summary_json": json_dumps(entry.get("afterSummary") or {}),
        "metadata_json": json_dumps(entry.get("metadata") or {}),
        "created_at": entry.get("createdAt"),
    }


def _entry_from_row(row):
    return {
        "id": row.get("id"),
        "occurredAt": row.get("occurred_at"),
        "actorUserId": row.get("actor_user_id"),
        "sessionId": row.get("session_id"),
        "rolesSnapshot": json_loads(row.get("roles_snapshot"), default=[]),
        "organizationIdsSnapshot": json_loads(row.get("organization_ids_snapshot"), default=[]),
        "regionIdsSnapshot": json_loads(row.get("region_ids_snapshot"), default=[]),
        "action": row.get("action"),
        "resourceType": row.get("resource_type"),
        "resourceId": row.get("resource_id"),
        "result": row.get("result"),
        "reasonCode": row.get("reason_code"),
        "beforeSummary": json_loads(row.get("before_summary_json"), default={}),
        "afterSummary": json_loads(row.get("after_summary_json"), default={}),
        "metadata": json_loads(row.get("metadata_json"), default={}),
        "createdAt": row.get("created_at"),
    }

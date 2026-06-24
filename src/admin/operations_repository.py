# @file src/admin/operations_repository.py
# @description Admin notices and recommendation policy repository.
# @lastModified 2026-06-24
#
# Step 16 PoC: R-ADMIN can manage operator notices and recommendation policy
# records. These records are operational control-plane data, so only aggregate
# metadata/rule JSON is stored here; the product recommendation runtime can read
# the active policy later through a separate integration.

import os
import uuid

from shared.database import create_database_client
from shared.rds_data import json_dumps, json_loads


# Notice and policy lifecycles are simple draft -> published/active -> archived
# state machines. For each action: the statuses it may move FROM and the status
# it moves TO. Illegal transitions raise *_TRANSITION_FORBIDDEN (409).
NOTICE_STATUSES = {"draft", "published", "archived"}
NOTICE_TRANSITIONS = {
    "publish": {"from": {"draft", "archived"}, "to": "published"},
    "archive": {"from": {"draft", "published"}, "to": "archived"},
}
POLICY_STATUSES = {"draft", "active", "archived"}
POLICY_TRANSITIONS = {
    "activate": {"from": {"draft", "archived"}, "to": "active"},
    "archive": {"from": {"draft", "active"}, "to": "archived"},
}


class OperationTransitionError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class RdsDataAdminOperationsRepository:
    def __init__(self, rds_client=None, notices_table=None, policies_table=None):
        self.rds = rds_client or create_database_client()
        self.notices_table = notices_table or os.environ.get("ADMIN_NOTICES_TABLE_NAME", "admin_notices")
        self.policies_table = policies_table or os.environ.get(
            "ADMIN_RECOMMENDATION_POLICIES_TABLE_NAME",
            "admin_recommendation_policies",
        )

    @classmethod
    def from_env(cls):
        return cls()

    def create_notice(self, principal, payload, now):
        notice = _build_notice(str(uuid.uuid4()), principal, payload, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.notices_table}
              (id, title, body, audience, severity, status, starts_at, ends_at,
               created_by, published_by, published_at, archived_at, created_at, updated_at)
            VALUES
              (:id, :title, :body, :audience, :severity, :status, :starts_at, :ends_at,
               :created_by, :published_by, :published_at, :archived_at, :created_at, :updated_at)
            """,
            _notice_row_params(notice),
            include_result_metadata=False,
        )
        return notice

    def list_notices(self, status=None, limit=20):
        clauses = []
        params = {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.notices_table}
            {(' WHERE ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_notice_from_row(row) for row in rows]

    def get_notice(self, notice_id):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.notices_table} WHERE id = :id",
            {"id": notice_id},
        )
        return _notice_from_row(row) if row else None

    def transition_notice(self, notice_id, action, principal, now):
        notice = self.get_notice(notice_id)
        if not notice:
            return None
        to_status = _validate_transition(notice, action, NOTICE_TRANSITIONS, "NOTICE_TRANSITION_FORBIDDEN")
        updates = {"status": to_status, "updatedAt": now}
        # Stamp who published / when it was archived so the trail is auditable.
        if action == "publish":
            updates["publishedBy"] = principal.get("userId")
            updates["publishedAt"] = now
        elif action == "archive":
            updates["archivedAt"] = now
        notice.update(updates)
        self.rds.execute(
            f"""
            UPDATE {self.notices_table}
            SET status = :status,
                published_by = :published_by,
                published_at = :published_at,
                archived_at = :archived_at,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {**_notice_row_params(notice), "id": notice_id},
            include_result_metadata=False,
        )
        return notice

    def create_policy(self, principal, payload, now):
        policy = _build_policy(str(uuid.uuid4()), principal, payload, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.policies_table}
              (id, policy_key, title, description, rules_json, priority, status,
               effective_from, effective_until, created_by, activated_by, activated_at,
               archived_at, created_at, updated_at)
            VALUES
              (:id, :policy_key, :title, :description, :rules_json, :priority, :status,
               :effective_from, :effective_until, :created_by, :activated_by, :activated_at,
               :archived_at, :created_at, :updated_at)
            """,
            _policy_row_params(policy),
            include_result_metadata=False,
        )
        return policy

    def list_policies(self, status=None, limit=20):
        clauses = []
        params = {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.policies_table}
            {(' WHERE ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY priority DESC, updated_at DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_policy_from_row(row) for row in rows]

    def get_policy(self, policy_id):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.policies_table} WHERE id = :id",
            {"id": policy_id},
        )
        return _policy_from_row(row) if row else None

    def transition_policy(self, policy_id, action, principal, now):
        policy = self.get_policy(policy_id)
        if not policy:
            return None
        to_status = _validate_transition(policy, action, POLICY_TRANSITIONS, "POLICY_TRANSITION_FORBIDDEN")
        updates = {"status": to_status, "updatedAt": now}
        if action == "activate":
            updates["activatedBy"] = principal.get("userId")
            updates["activatedAt"] = now
        elif action == "archive":
            updates["archivedAt"] = now
        policy.update(updates)
        self.rds.execute(
            f"""
            UPDATE {self.policies_table}
            SET status = :status,
                activated_by = :activated_by,
                activated_at = :activated_at,
                archived_at = :archived_at,
                updated_at = :updated_at
            WHERE id = :id
            """,
            {**_policy_row_params(policy), "id": policy_id},
            include_result_metadata=False,
        )
        return policy


class InMemoryAdminOperationsRepository:
    def __init__(self, now="2026-06-24T00:00:00Z"):
        self.now = now
        self.notices = {}
        self.policies = {}

    def create_notice(self, principal, payload, now=None):
        notice_id = f"notice-{len(self.notices) + 1}"
        notice = _build_notice(notice_id, principal, payload, now or self.now)
        self.notices[notice_id] = notice
        return dict(notice)

    def list_notices(self, status=None, limit=20):
        items = [notice for notice in self.notices.values() if not status or notice.get("status") == status]
        items.sort(key=lambda notice: notice.get("updatedAt") or "", reverse=True)
        return [dict(notice) for notice in items[:limit]]

    def get_notice(self, notice_id):
        notice = self.notices.get(notice_id)
        return dict(notice) if notice else None

    def transition_notice(self, notice_id, action, principal, now=None):
        notice = self.notices.get(notice_id)
        if not notice:
            return None
        notice["status"] = _validate_transition(notice, action, NOTICE_TRANSITIONS, "NOTICE_TRANSITION_FORBIDDEN")
        notice["updatedAt"] = now or self.now
        if action == "publish":
            notice["publishedBy"] = principal.get("userId")
            notice["publishedAt"] = now or self.now
        elif action == "archive":
            notice["archivedAt"] = now or self.now
        return dict(notice)

    def create_policy(self, principal, payload, now=None):
        policy_id = f"policy-{len(self.policies) + 1}"
        policy = _build_policy(policy_id, principal, payload, now or self.now)
        self.policies[policy_id] = policy
        return dict(policy)

    def list_policies(self, status=None, limit=20):
        items = [policy for policy in self.policies.values() if not status or policy.get("status") == status]
        items.sort(key=lambda policy: (int(policy.get("priority") or 0), policy.get("updatedAt") or ""), reverse=True)
        return [dict(policy) for policy in items[:limit]]

    def get_policy(self, policy_id):
        policy = self.policies.get(policy_id)
        return dict(policy) if policy else None

    def transition_policy(self, policy_id, action, principal, now=None):
        policy = self.policies.get(policy_id)
        if not policy:
            return None
        policy["status"] = _validate_transition(policy, action, POLICY_TRANSITIONS, "POLICY_TRANSITION_FORBIDDEN")
        policy["updatedAt"] = now or self.now
        if action == "activate":
            policy["activatedBy"] = principal.get("userId")
            policy["activatedAt"] = now or self.now
        elif action == "archive":
            policy["archivedAt"] = now or self.now
        return dict(policy)


def _validate_transition(record, action, transitions, code):
    rule = transitions.get(action)
    if not rule:
        raise OperationTransitionError(400, "INVALID_OPERATION_ACTION", "Unsupported operation action")
    current = record.get("status")
    if current not in rule["from"]:
        raise OperationTransitionError(409, code, f"Cannot {action} a record in status '{current}'")
    return rule["to"]


def _build_notice(notice_id, principal, payload, now):
    return {
        "id": notice_id,
        "title": payload.get("title"),
        "body": payload.get("body"),
        "audience": payload.get("audience") or "all",
        "severity": payload.get("severity") or "info",
        "status": "draft",
        "startsAt": payload.get("startsAt"),
        "endsAt": payload.get("endsAt"),
        "createdBy": principal.get("userId"),
        "publishedBy": None,
        "publishedAt": None,
        "archivedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


def _build_policy(policy_id, principal, payload, now):
    return {
        "id": policy_id,
        "policyKey": payload.get("policyKey"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "rules": payload.get("rules") or {},
        "priority": int(payload.get("priority") or 0),
        "status": "draft",
        "effectiveFrom": payload.get("effectiveFrom"),
        "effectiveUntil": payload.get("effectiveUntil"),
        "createdBy": principal.get("userId"),
        "activatedBy": None,
        "activatedAt": None,
        "archivedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


def _notice_row_params(notice):
    return {
        "id": notice.get("id"),
        "title": notice.get("title"),
        "body": notice.get("body"),
        "audience": notice.get("audience"),
        "severity": notice.get("severity"),
        "status": notice.get("status"),
        "starts_at": notice.get("startsAt"),
        "ends_at": notice.get("endsAt"),
        "created_by": notice.get("createdBy"),
        "published_by": notice.get("publishedBy"),
        "published_at": notice.get("publishedAt"),
        "archived_at": notice.get("archivedAt"),
        "created_at": notice.get("createdAt"),
        "updated_at": notice.get("updatedAt"),
    }


def _policy_row_params(policy):
    return {
        "id": policy.get("id"),
        "policy_key": policy.get("policyKey"),
        "title": policy.get("title"),
        "description": policy.get("description"),
        "rules_json": json_dumps(policy.get("rules") or {}),
        "priority": policy.get("priority") or 0,
        "status": policy.get("status"),
        "effective_from": policy.get("effectiveFrom"),
        "effective_until": policy.get("effectiveUntil"),
        "created_by": policy.get("createdBy"),
        "activated_by": policy.get("activatedBy"),
        "activated_at": policy.get("activatedAt"),
        "archived_at": policy.get("archivedAt"),
        "created_at": policy.get("createdAt"),
        "updated_at": policy.get("updatedAt"),
    }


def _notice_from_row(row):
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "body": row.get("body"),
        "audience": row.get("audience"),
        "severity": row.get("severity"),
        "status": row.get("status"),
        "startsAt": row.get("starts_at"),
        "endsAt": row.get("ends_at"),
        "createdBy": row.get("created_by"),
        "publishedBy": row.get("published_by"),
        "publishedAt": row.get("published_at"),
        "archivedAt": row.get("archived_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _policy_from_row(row):
    return {
        "id": row.get("id"),
        "policyKey": row.get("policy_key"),
        "title": row.get("title"),
        "description": row.get("description"),
        "rules": json_loads(row.get("rules_json"), {}),
        "priority": row.get("priority") or 0,
        "status": row.get("status"),
        "effectiveFrom": row.get("effective_from"),
        "effectiveUntil": row.get("effective_until"),
        "createdBy": row.get("created_by"),
        "activatedBy": row.get("activated_by"),
        "activatedAt": row.get("activated_at"),
        "archivedAt": row.get("archived_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }

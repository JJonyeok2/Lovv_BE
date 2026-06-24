# @file src/admin/monthly_destinations_repository.py
# @description Monthly curated destination repository (candidate -> publish state).
# @lastModified 2026-06-24
#
# Owns the monthly_curated_destinations lifecycle: an approved data proposal is
# promoted into a "candidate", then moved through its publish state machine. The
# allowed transitions and the publish/hide bookkeeping live here so they hold
# regardless of the calling route. See docs/specs/ADMIN_RBAC_SPEC.md.

import os
import uuid

from shared.database import create_database_client
from shared.rds_data import json_dumps, json_loads


# Publish state machine. For each action: the statuses it may move FROM and the
# single status it moves TO. Enforced in _validate_transition (409 if not allowed).
MONTHLY_TRANSITIONS = {
    "schedule": {"from": {"candidate"}, "to": "scheduled"},
    "publish": {"from": {"candidate", "scheduled", "hidden"}, "to": "published"},
    "hide": {"from": {"published"}, "to": "hidden"},
    "expire": {"from": {"scheduled", "published", "hidden"}, "to": "expired"},
    "reject": {"from": {"candidate", "scheduled"}, "to": "rejected"},
}
MONTHLY_STATUSES = {"candidate", "scheduled", "published", "hidden", "expired", "rejected"}


class MonthlyDestinationTransitionError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class RdsDataMonthlyDestinationRepository:
    def __init__(self, rds_client=None, table=None):
        self.rds = rds_client or create_database_client()
        self.table = table or os.environ.get(
            "MONTHLY_CURATED_DESTINATIONS_TABLE_NAME", "monthly_curated_destinations"
        )

    @classmethod
    def from_env(cls):
        return cls()

    def create(self, principal, payload, now):
        destination_id = str(uuid.uuid4())
        destination = _build_destination(destination_id, principal, payload, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.table}
              (id, city_id, city_name, region_id, source_proposal_id, curation_month,
               theme_codes, official_source_url, official_source_name, source_updated_at,
               valid_from, valid_until, status, publish_reason, service_boundary_json,
               gateway_city_json, published_by, published_at, hidden_by, hidden_at,
               hidden_reason, created_at, updated_at)
            VALUES
              (:id, :city_id, :city_name, :region_id, :source_proposal_id, :curation_month,
               :theme_codes, :official_source_url, :official_source_name, :source_updated_at,
               :valid_from, :valid_until, :status, :publish_reason, :service_boundary_json,
               :gateway_city_json, :published_by, :published_at, :hidden_by, :hidden_at,
               :hidden_reason, :created_at, :updated_at)
            """,
            _row_params(destination),
            include_result_metadata=False,
        )
        return destination

    def list_all(self, curation_month=None, region_id=None, status=None, limit=20):
        clauses, params = _list_filters(curation_month, region_id, status)
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.table}
            {(' WHERE ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_destination_from_row(row) for row in rows]

    def list_for_regions(self, region_ids, curation_month=None, status=None, limit=20):
        region_ids = [region_id for region_id in (region_ids or []) if region_id]
        if not region_ids:
            return []
        placeholders = ", ".join(f":region_{index}" for index in range(len(region_ids)))
        clauses = [f"region_id IN ({placeholders})"]
        params = {f"region_{index}": value for index, value in enumerate(region_ids)}
        if curation_month:
            clauses.append("curation_month = :curation_month")
            params["curation_month"] = curation_month
        if status:
            clauses.append("status = :status")
            params["status"] = status
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.table}
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_destination_from_row(row) for row in rows]

    def get(self, destination_id):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.table} WHERE id = :id",
            {"id": destination_id},
        )
        return _destination_from_row(row) if row else None

    def transition(self, destination_id, action, principal, now, payload=None):
        destination = self.get(destination_id)
        if not destination:
            return None
        to_status = _validate_transition(destination, action)
        updates = _transition_updates(action, to_status, principal, now, payload or {})
        destination.update(updates)
        assignments = ", ".join(f"{column} = :{column}" for column in _row_params_subset(updates))
        self.rds.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = :id",
            {**_row_params_subset(updates), "id": destination_id},
            include_result_metadata=False,
        )
        return destination


class InMemoryMonthlyDestinationRepository:
    def __init__(self, now="2026-06-24T00:00:00Z"):
        self.now = now
        self.destinations = {}

    def create(self, principal, payload, now=None):
        destination_id = f"monthly-{len(self.destinations) + 1}"
        destination = _build_destination(destination_id, principal, payload, now or self.now)
        self.destinations[destination_id] = destination
        return dict(destination)

    def list_all(self, curation_month=None, region_id=None, status=None, limit=20):
        items = [
            destination
            for destination in self.destinations.values()
            if (not curation_month or destination.get("curationMonth") == curation_month)
            and (not region_id or destination.get("regionId") == region_id)
            and (not status or destination.get("status") == status)
        ]
        items.sort(key=lambda destination: destination.get("updatedAt") or "", reverse=True)
        return [dict(destination) for destination in items[:limit]]

    def list_for_regions(self, region_ids, curation_month=None, status=None, limit=20):
        region_ids = set(region_id for region_id in (region_ids or []) if region_id)
        items = [
            destination
            for destination in self.destinations.values()
            if destination.get("regionId") in region_ids
            and (not curation_month or destination.get("curationMonth") == curation_month)
            and (not status or destination.get("status") == status)
        ]
        items.sort(key=lambda destination: destination.get("updatedAt") or "", reverse=True)
        return [dict(destination) for destination in items[:limit]]

    def get(self, destination_id):
        destination = self.destinations.get(destination_id)
        return dict(destination) if destination else None

    def transition(self, destination_id, action, principal, now=None, payload=None):
        destination = self.destinations.get(destination_id)
        if not destination:
            return None
        to_status = _validate_transition(destination, action)
        destination.update(_transition_updates(action, to_status, principal, now or self.now, payload or {}))
        return dict(destination)


def _validate_transition(destination, action):
    rule = MONTHLY_TRANSITIONS.get(action)
    if not rule:
        raise MonthlyDestinationTransitionError(400, "INVALID_MONTHLY_ACTION", "Unsupported monthly destination action")
    current = destination.get("status")
    if current not in rule["from"]:
        raise MonthlyDestinationTransitionError(
            409,
            "MONTHLY_TRANSITION_FORBIDDEN",
            f"Cannot {action} a destination in status '{current}'",
        )
    return rule["to"]


def _transition_updates(action, to_status, principal, now, payload):
    updates = {"status": to_status, "updatedAt": now}
    reason = _optional_text(payload.get("reason"))
    if action == "publish":
        updates["publishedBy"] = principal.get("userId")
        updates["publishedAt"] = now
        if payload.get("validFrom") is not None:
            updates["validFrom"] = _optional_text(payload.get("validFrom"))
        if payload.get("validUntil") is not None:
            updates["validUntil"] = _optional_text(payload.get("validUntil"))
        updates["publishReason"] = reason
    elif action == "schedule":
        if payload.get("validFrom") is not None:
            updates["validFrom"] = _optional_text(payload.get("validFrom"))
        if payload.get("validUntil") is not None:
            updates["validUntil"] = _optional_text(payload.get("validUntil"))
    elif action == "hide":
        updates["hiddenBy"] = principal.get("userId")
        updates["hiddenAt"] = now
        updates["hiddenReason"] = reason
    elif action == "reject":
        updates["publishReason"] = reason
    return updates


def _build_destination(destination_id, principal, payload, now):
    return {
        "id": destination_id,
        "cityId": payload.get("cityId"),
        "cityName": payload.get("cityName"),
        "regionId": payload.get("regionId"),
        "sourceProposalId": payload.get("sourceProposalId"),
        "curationMonth": payload.get("curationMonth"),
        "themeCodes": list(payload.get("themeCodes") or []),
        "officialSourceUrl": payload.get("officialSourceUrl"),
        "officialSourceName": payload.get("officialSourceName"),
        "sourceUpdatedAt": payload.get("sourceUpdatedAt"),
        "validFrom": payload.get("validFrom"),
        "validUntil": payload.get("validUntil"),
        "status": "candidate",
        "publishReason": None,
        "serviceBoundary": payload.get("serviceBoundary") or {},
        "gatewayCity": payload.get("gatewayCity") or {},
        "publishedBy": None,
        "publishedAt": None,
        "hiddenBy": None,
        "hiddenAt": None,
        "hiddenReason": None,
        "createdBy": principal.get("userId"),
        "createdAt": now,
        "updatedAt": now,
    }


def _row_params(destination):
    return {
        "id": destination.get("id"),
        "city_id": destination.get("cityId"),
        "city_name": destination.get("cityName"),
        "region_id": destination.get("regionId"),
        "source_proposal_id": destination.get("sourceProposalId"),
        "curation_month": destination.get("curationMonth"),
        "theme_codes": json_dumps(destination.get("themeCodes") or []),
        "official_source_url": destination.get("officialSourceUrl"),
        "official_source_name": destination.get("officialSourceName"),
        "source_updated_at": destination.get("sourceUpdatedAt"),
        "valid_from": destination.get("validFrom"),
        "valid_until": destination.get("validUntil"),
        "status": destination.get("status"),
        "publish_reason": destination.get("publishReason"),
        "service_boundary_json": json_dumps(destination.get("serviceBoundary") or {}),
        "gateway_city_json": json_dumps(destination.get("gatewayCity") or {}),
        "published_by": destination.get("publishedBy"),
        "published_at": destination.get("publishedAt"),
        "hidden_by": destination.get("hiddenBy"),
        "hidden_at": destination.get("hiddenAt"),
        "hidden_reason": destination.get("hiddenReason"),
        "created_at": destination.get("createdAt"),
        "updated_at": destination.get("updatedAt"),
    }


# Map the camelCase keys updated by a transition to their snake_case columns so the
# UPDATE statement only writes the fields that actually changed.
_COLUMN_FOR_FIELD = {
    "status": "status",
    "publishReason": "publish_reason",
    "validFrom": "valid_from",
    "validUntil": "valid_until",
    "publishedBy": "published_by",
    "publishedAt": "published_at",
    "hiddenBy": "hidden_by",
    "hiddenAt": "hidden_at",
    "hiddenReason": "hidden_reason",
    "updatedAt": "updated_at",
}


def _row_params_subset(updates):
    return {_COLUMN_FOR_FIELD[field]: value for field, value in updates.items() if field in _COLUMN_FOR_FIELD}


def _list_filters(curation_month, region_id, status):
    clauses = []
    params = {}
    if curation_month:
        clauses.append("curation_month = :curation_month")
        params["curation_month"] = curation_month
    if region_id:
        clauses.append("region_id = :region_id")
        params["region_id"] = region_id
    if status:
        clauses.append("status = :status")
        params["status"] = status
    return clauses, params


def _destination_from_row(row):
    return {
        "id": row.get("id"),
        "cityId": row.get("city_id"),
        "cityName": row.get("city_name"),
        "regionId": row.get("region_id"),
        "sourceProposalId": row.get("source_proposal_id"),
        "curationMonth": row.get("curation_month"),
        "themeCodes": json_loads(row.get("theme_codes"), default=[]),
        "officialSourceUrl": row.get("official_source_url"),
        "officialSourceName": row.get("official_source_name"),
        "sourceUpdatedAt": row.get("source_updated_at"),
        "validFrom": row.get("valid_from"),
        "validUntil": row.get("valid_until"),
        "status": row.get("status"),
        "publishReason": row.get("publish_reason"),
        "serviceBoundary": json_loads(row.get("service_boundary_json"), default={}),
        "gatewayCity": json_loads(row.get("gateway_city_json"), default={}),
        "publishedBy": row.get("published_by"),
        "publishedAt": row.get("published_at"),
        "hiddenBy": row.get("hidden_by"),
        "hiddenAt": row.get("hidden_at"),
        "hiddenReason": row.get("hidden_reason"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _optional_text(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text or None

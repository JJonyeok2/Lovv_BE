# @file src/admin/proposals_repository.py
# @description Admin console data proposal repository backed by the configured SQL client.
# @lastModified 2026-06-23

import os
import uuid

from shared.authorization import ROLE_ADMIN, ROLE_LOCAL_OPERATOR, has_any_role
from shared.database import create_database_client
from shared.rds_data import json_dumps, json_loads


class RdsDataAdminProposalRepository:
    def __init__(self, rds_client=None, proposals_table=None, history_table=None):
        self.rds = rds_client or create_database_client()
        self.proposals_table = proposals_table or os.environ.get("ADMIN_DATA_PROPOSALS_TABLE_NAME", "admin_data_proposals")
        self.history_table = history_table or os.environ.get("ADMIN_DATA_PROPOSAL_HISTORY_TABLE_NAME", "admin_data_proposal_history")

    @classmethod
    def from_env(cls):
        return cls()

    def create(self, principal, payload, now):
        proposal_id = str(uuid.uuid4())
        proposal = _build_proposal(proposal_id, _proposal_code(proposal_id), principal, payload, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.proposals_table}
              (id, proposal_code, content_type, region_id, city_id, city_name, title, description,
               official_source_name, official_source_url, source_updated_at, evidence_text,
               payload_json, service_boundary_json, gateway_city_json, status, created_by,
               organization_id, submitted_at, reviewed_by, reviewed_at, review_note,
               approved_content_hash, created_at, updated_at, deleted_at)
            VALUES
              (:id, :proposal_code, :content_type, :region_id, :city_id, :city_name, :title, :description,
               :official_source_name, :official_source_url, :source_updated_at, :evidence_text,
               :payload_json, :service_boundary_json, :gateway_city_json, :status, :created_by,
               :organization_id, :submitted_at, :reviewed_by, :reviewed_at, :review_note,
               :approved_content_hash, :created_at, :updated_at, :deleted_at)
            """,
            _row_params(proposal),
            include_result_metadata=False,
        )
        self._append_history(proposal, "submitted", None, "submitted", principal, now)
        return proposal

    def list_all(self, limit=20):
        rows = self.rds.fetch_all(
            f"""
            SELECT *
            FROM {self.proposals_table}
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        return [_proposal_from_row(row) for row in rows]

    def list_for_provider(self, user_id, organization_ids=None, limit=20):
        params = {"user_id": user_id, "limit": limit}
        org_clause = ""
        organization_ids = [org_id for org_id in (organization_ids or []) if org_id]
        if organization_ids:
            placeholders = []
            for index, organization_id in enumerate(organization_ids):
                name = f"organization_id_{index}"
                params[name] = organization_id
                placeholders.append(f":{name}")
            org_clause = f" OR organization_id IN ({', '.join(placeholders)})"

        rows = self.rds.fetch_all(
            f"""
            SELECT *
            FROM {self.proposals_table}
            WHERE deleted_at IS NULL
              AND (created_by = :user_id{org_clause})
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            params,
        )
        return [_proposal_from_row(row) for row in rows]

    def list_for_regions(self, region_ids, limit=20):
        region_ids = [region_id for region_id in (region_ids or []) if region_id]
        if not region_ids:
            return []

        params = {"limit": limit}
        placeholders = []
        for index, region_id in enumerate(region_ids):
            name = f"region_id_{index}"
            params[name] = region_id
            placeholders.append(f":{name}")

        rows = self.rds.fetch_all(
            f"""
            SELECT *
            FROM {self.proposals_table}
            WHERE deleted_at IS NULL
              AND region_id IN ({', '.join(placeholders)})
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            params,
        )
        return [_proposal_from_row(row) for row in rows]

    def get_visible(self, proposal_id, principal):
        row = self.rds.fetch_one(
            f"""
            SELECT *
            FROM {self.proposals_table}
            WHERE id = :id
              AND deleted_at IS NULL
            """,
            {"id": proposal_id},
        )
        proposal = _proposal_from_row(row) if row else None
        if not proposal or not _can_view(proposal, principal):
            return None
        return proposal

    def _append_history(self, proposal, action, from_status, to_status, principal, now):
        self.rds.execute(
            f"""
            INSERT INTO {self.history_table}
              (id, proposal_id, action, from_status, to_status, actor_user_id,
               actor_roles_json, note, metadata_json, created_at)
            VALUES
              (:id, :proposal_id, :action, :from_status, :to_status, :actor_user_id,
               :actor_roles_json, :note, :metadata_json, :created_at)
            """,
            {
                "id": str(uuid.uuid4()),
                "proposal_id": proposal["proposalId"],
                "action": action,
                "from_status": from_status,
                "to_status": to_status,
                "actor_user_id": principal.get("userId"),
                "actor_roles_json": json_dumps(principal.get("roles") or []),
                "note": None,
                "metadata_json": json_dumps({"proposalCode": proposal.get("proposalCode")}),
                "created_at": now,
            },
            include_result_metadata=False,
        )


class InMemoryAdminProposalRepository:
    def __init__(self, now="2026-06-23T00:00:00Z"):
        self.now = now
        self.proposals = {}
        self.history = []

    def create(self, principal, payload, now=None):
        proposal_id = f"proposal-{len(self.proposals) + 1}"
        proposal = _build_proposal(proposal_id, f"PROP-{len(self.proposals) + 1:06d}", principal, payload, now or self.now)
        self.proposals[proposal_id] = proposal
        self.history.append(
            {
                "proposalId": proposal_id,
                "action": "submitted",
                "fromStatus": None,
                "toStatus": "submitted",
                "actorUserId": principal.get("userId"),
                "actorRoles": list(principal.get("roles") or []),
                "createdAt": now or self.now,
            }
        )
        return dict(proposal)

    def list_all(self, limit=20):
        proposals = [proposal for proposal in self.proposals.values() if not proposal.get("deletedAt")]
        proposals.sort(key=lambda proposal: proposal.get("updatedAt") or "", reverse=True)
        return [dict(proposal) for proposal in proposals[:limit]]

    def list_for_provider(self, user_id, organization_ids=None, limit=20):
        organization_ids = set(organization_ids or [])
        proposals = [
            proposal
            for proposal in self.proposals.values()
            if not proposal.get("deletedAt")
            and (proposal.get("createdBy") == user_id or (proposal.get("organizationId") and proposal.get("organizationId") in organization_ids))
        ]
        proposals.sort(key=lambda proposal: proposal.get("updatedAt") or "", reverse=True)
        return [dict(proposal) for proposal in proposals[:limit]]

    def list_for_regions(self, region_ids, limit=20):
        region_ids = set(region_ids or [])
        proposals = [
            proposal
            for proposal in self.proposals.values()
            if not proposal.get("deletedAt") and proposal.get("regionId") in region_ids
        ]
        proposals.sort(key=lambda proposal: proposal.get("updatedAt") or "", reverse=True)
        return [dict(proposal) for proposal in proposals[:limit]]

    def get_visible(self, proposal_id, principal):
        proposal = self.proposals.get(proposal_id)
        if not proposal or proposal.get("deletedAt") or not _can_view(proposal, principal):
            return None
        return dict(proposal)


def _build_proposal(proposal_id, proposal_code, principal, payload, now):
    organization_ids = principal.get("organizationIds") or []
    organization_id = organization_ids[0] if organization_ids else None
    return {
        "proposalId": proposal_id,
        "proposalCode": proposal_code,
        "contentType": payload.get("contentType"),
        "regionId": payload.get("regionId"),
        "cityId": payload.get("cityId"),
        "cityName": payload.get("cityName"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "officialSourceName": payload.get("officialSourceName"),
        "officialSourceUrl": payload.get("officialSourceUrl"),
        "sourceUpdatedAt": payload.get("sourceUpdatedAt"),
        "evidenceText": payload.get("evidenceText"),
        "payload": payload.get("payload") or {},
        "serviceBoundary": payload.get("serviceBoundary") or {},
        "gatewayCity": payload.get("gatewayCity") or {},
        "status": "submitted",
        "createdBy": principal.get("userId"),
        "organizationId": organization_id,
        "submittedAt": now,
        "reviewedBy": None,
        "reviewedAt": None,
        "reviewNote": None,
        "approvedContentHash": None,
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
    }


def _proposal_code(proposal_id):
    return f"PROP-{proposal_id.replace('-', '')[:12].upper()}"


def _row_params(proposal):
    return {
        "id": proposal.get("proposalId"),
        "proposal_code": proposal.get("proposalCode"),
        "content_type": proposal.get("contentType"),
        "region_id": proposal.get("regionId"),
        "city_id": proposal.get("cityId"),
        "city_name": proposal.get("cityName"),
        "title": proposal.get("title"),
        "description": proposal.get("description"),
        "official_source_name": proposal.get("officialSourceName"),
        "official_source_url": proposal.get("officialSourceUrl"),
        "source_updated_at": proposal.get("sourceUpdatedAt"),
        "evidence_text": proposal.get("evidenceText"),
        "payload_json": json_dumps(proposal.get("payload") or {}),
        "service_boundary_json": json_dumps(proposal.get("serviceBoundary") or {}),
        "gateway_city_json": json_dumps(proposal.get("gatewayCity") or {}),
        "status": proposal.get("status"),
        "created_by": proposal.get("createdBy"),
        "organization_id": proposal.get("organizationId"),
        "submitted_at": proposal.get("submittedAt"),
        "reviewed_by": proposal.get("reviewedBy"),
        "reviewed_at": proposal.get("reviewedAt"),
        "review_note": proposal.get("reviewNote"),
        "approved_content_hash": proposal.get("approvedContentHash"),
        "created_at": proposal.get("createdAt"),
        "updated_at": proposal.get("updatedAt"),
        "deleted_at": proposal.get("deletedAt"),
    }


def _proposal_from_row(row):
    if not row:
        return None
    return {
        "proposalId": row.get("id"),
        "proposalCode": row.get("proposal_code"),
        "contentType": row.get("content_type"),
        "regionId": row.get("region_id"),
        "cityId": row.get("city_id"),
        "cityName": row.get("city_name"),
        "title": row.get("title"),
        "description": row.get("description"),
        "officialSourceName": row.get("official_source_name"),
        "officialSourceUrl": row.get("official_source_url"),
        "sourceUpdatedAt": row.get("source_updated_at"),
        "evidenceText": row.get("evidence_text"),
        "payload": json_loads(row.get("payload_json"), {}),
        "serviceBoundary": json_loads(row.get("service_boundary_json"), {}),
        "gatewayCity": json_loads(row.get("gateway_city_json"), {}),
        "status": row.get("status"),
        "createdBy": row.get("created_by"),
        "organizationId": row.get("organization_id"),
        "submittedAt": row.get("submitted_at"),
        "reviewedBy": row.get("reviewed_by"),
        "reviewedAt": row.get("reviewed_at"),
        "reviewNote": row.get("review_note"),
        "approvedContentHash": row.get("approved_content_hash"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "deletedAt": row.get("deleted_at"),
    }


def _can_view(proposal, principal):
    if has_any_role(principal, {ROLE_ADMIN}):
        return True
    if has_any_role(principal, {ROLE_LOCAL_OPERATOR}):
        return proposal.get("regionId") in set(principal.get("regionIds") or [])
    organization_ids = set(principal.get("organizationIds") or [])
    return proposal.get("createdBy") == principal.get("userId") or (
        bool(proposal.get("organizationId")) and proposal.get("organizationId") in organization_ids
    )


# EOF: src/admin/proposals_repository.py

# @file src/saved_plans/repository.py
# @description Saved itinerary repository backed by the configured SQL client.
# @lastModified 2026-06-12

import hashlib
import os
import uuid

from shared.database import create_database_client
from shared.rds_data import json_dumps, json_loads


class IdempotencyConflictError(Exception):
    pass


class RdsDataSavedPlanRepository:
    def __init__(self, rds_client=None, table_name=None, item_table_name=None, reaction_table_name=None):
        self.rds = rds_client or create_database_client()
        self.table_name = table_name or os.environ.get("SAVED_PLANS_TABLE_NAME", "itineraries")
        self.item_table_name = item_table_name or os.environ.get("ITINERARY_ITEMS_TABLE_NAME", "itinerary_items")
        self.reaction_table_name = reaction_table_name or os.environ.get("PLAN_REACTIONS_TABLE_NAME", "plan_reactions")

    @classmethod
    def from_env(cls):
        return cls()

    def save(self, user_id, payload, snapshot_hash, now):
        idempotency_key = payload.get("idempotencyKey")
        if idempotency_key:
            existing = self._find_by_idempotency_key(user_id, idempotency_key)
            if existing:
                if existing.get("snapshotHash") != snapshot_hash:
                    raise IdempotencyConflictError()
                return existing, True
            deleted = self._find_deleted_by_idempotency_key(user_id, idempotency_key)
            if deleted:
                # Restore regardless of hash mismatch — user deleted then re-saved the same plan
                # (hash can differ due to dynamic fields like preferenceSnapshot.updatedAt)
                return self._restore(deleted["itineraryId"], user_id, payload, snapshot_hash, now), False

        existing = self._find_by_recommendation_hash(user_id, payload.get("sourceRecommendationId"), snapshot_hash)
        if existing:
            return existing, True
        deleted = self._find_deleted_by_recommendation_hash(user_id, payload.get("sourceRecommendationId"), snapshot_hash)
        if deleted:
            return self._restore(deleted["itineraryId"], user_id, payload, snapshot_hash, now), False

        plan_id = str(uuid.uuid4())
        plan = _build_plan(plan_id, user_id, payload, snapshot_hash, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.table_name}
              (id, user_id, title, summary, duration_label, festival_choice, intensity_label,
               preference_snapshot, request_summary, source_recommendation_id, idempotency_key,
               snapshot_hash, destination_json, trip_type, themes_json, conditions_snapshot_json,
               itinerary_json, alternative_itinerary_json, is_public, copied_from_itinerary_id, saved_at, created_at, updated_at)
            VALUES
              (:id, :user_id, :title, :summary, :duration_label, :festival_choice, :intensity_label,
               :preference_snapshot, :request_summary, :source_recommendation_id, :idempotency_key,
               :snapshot_hash, :destination_json, :trip_type, :themes_json, :conditions_snapshot_json,
               :itinerary_json, :alternative_itinerary_json, :is_public, :copied_from_itinerary_id, :saved_at, :created_at, :updated_at)
            """,
            _row_params(plan),
            include_result_metadata=False,
        )
        self._insert_itinerary_items(plan)
        return plan, False

    def _restore(self, plan_id, user_id, payload, snapshot_hash, now):
        plan = _build_plan(plan_id, user_id, payload, snapshot_hash, now)
        self.rds.execute(
            f"""
            UPDATE {self.table_name}
            SET source_recommendation_id = :source_recommendation_id,
                idempotency_key = :idempotency_key,
                snapshot_hash = :snapshot_hash,
                title = :title,
                summary = :summary,
                festival_choice = :festival_choice,
                intensity_label = :intensity_label,
                preference_snapshot = :preference_snapshot,
                destination_json = :destination_json,
                trip_type = :trip_type,
                duration_label = :duration_label,
                themes_json = :themes_json,
                conditions_snapshot_json = :conditions_snapshot_json,
                request_summary = :request_summary,
                itinerary_json = :itinerary_json,
                alternative_itinerary_json = :alternative_itinerary_json,
                is_public = :is_public,
                copied_from_itinerary_id = :copied_from_itinerary_id,
                saved_at = :saved_at,
                updated_at = :updated_at,
                deleted_at = NULL
            WHERE id = :id AND user_id = :user_id
            """,
            _row_params(plan),
            include_result_metadata=False,
        )
        self._delete_reactions(plan_id, user_id)
        self._replace_itinerary_items(plan)
        return plan

    def list_by_user(self, user_id, limit=20):
        rows = self.rds.fetch_all(
            f"""
            SELECT i.id, i.user_id, i.source_recommendation_id, i.idempotency_key, i.snapshot_hash,
                   i.title, i.summary, i.destination_json, i.trip_type, i.duration_label,
                   i.festival_choice, i.intensity_label, i.preference_snapshot,
                   i.themes_json, i.conditions_snapshot_json, i.request_summary,
                   i.itinerary_json, i.alternative_itinerary_json, i.is_public, i.copied_from_itinerary_id, i.saved_at, i.updated_at, i.deleted_at,
                   EXISTS (
                     SELECT 1
                     FROM {self.reaction_table_name} pr
                     WHERE pr.user_id = :user_id
                       AND pr.itinerary_id = i.id
                       AND pr.reaction_type = 'like'
                   ) AS is_liked
            FROM {self.table_name} i
            WHERE i.user_id = :user_id
              AND i.deleted_at IS NULL
            ORDER BY i.saved_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [_summary(self._with_itinerary_items(_plan_from_row(row))) for row in rows]

    def get_owned(self, user_id, plan_id):
        row = self.rds.fetch_one(
            f"""
            SELECT i.*,
                   EXISTS (
                     SELECT 1
                     FROM {self.reaction_table_name} pr
                     WHERE pr.user_id = :user_id
                       AND pr.itinerary_id = i.id
                       AND pr.reaction_type = 'like'
                   ) AS is_liked
            FROM {self.table_name} i
            WHERE i.id = :id
              AND i.user_id = :user_id
              AND i.deleted_at IS NULL
            """,
            {"id": plan_id, "user_id": user_id},
        )
        return self._with_itinerary_items(_plan_from_row(row)) if row else None

    def get_public_or_owned(self, user_id, plan_id):
        row = self.rds.fetch_one(
            f"""
            SELECT i.*,
                   EXISTS (
                     SELECT 1
                     FROM {self.reaction_table_name} pr
                     WHERE pr.user_id = :user_id
                       AND pr.itinerary_id = i.id
                       AND pr.reaction_type = 'like'
                   ) AS is_liked
            FROM {self.table_name} i
            WHERE i.id = :id
              AND (i.user_id = :user_id OR i.is_public = 1)
              AND i.deleted_at IS NULL
            """,
            {"id": plan_id, "user_id": user_id},
        )
        return self._with_itinerary_items(_plan_from_row(row)) if row else None

    def list_public(self, limit=20, user_id=None):
        rows = self.rds.fetch_all(
            f"""
            SELECT i.id, i.user_id, i.source_recommendation_id, i.idempotency_key, i.snapshot_hash,
                   i.title, i.summary, i.destination_json, i.trip_type, i.duration_label,
                   i.festival_choice, i.intensity_label, i.preference_snapshot,
                   i.themes_json, i.conditions_snapshot_json, i.request_summary,
                   i.itinerary_json, i.alternative_itinerary_json, i.is_public, i.copied_from_itinerary_id, i.saved_at, i.updated_at, i.deleted_at,
                   EXISTS (
                     SELECT 1
                     FROM {self.reaction_table_name} pr
                     WHERE pr.user_id = :user_id
                       AND pr.itinerary_id = i.id
                       AND pr.reaction_type = 'like'
                   ) AS is_liked
            FROM {self.table_name} i
            WHERE i.is_public = 1
              AND i.deleted_at IS NULL
            ORDER BY i.saved_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [_summary(self._with_itinerary_items(_plan_from_row(row))) for row in rows]

    def clone_itinerary(self, user_id, source_plan_id, now):
        source = self.get_public_or_owned(user_id, source_plan_id)
        if not source:
            return None
        cloned_id = str(uuid.uuid4())
        cloned_payload = {
            "sourceRecommendationId": source.get("sourceRecommendationId"),
            "title": f"{source.get('title')} (복사본)" if source.get("userId") != user_id else source.get("title"),
            "summary": source.get("summary"),
            "destination": source.get("destination"),
            "tripType": source.get("tripType"),
            "durationLabel": source.get("durationLabel"),
            "themes": source.get("themes"),
            "festivalChoice": source.get("festivalChoice"),
            "festivalThemeLabel": source.get("festivalThemeLabel"),
            "intensityLabel": source.get("intensityLabel"),
            "preferenceSnapshot": source.get("preferenceSnapshot"),
            "conditionsSnapshot": source.get("conditionsSnapshot"),
            "requestSummary": source.get("requestSummary"),
            "itinerary": source.get("itinerary"),
            "alternativeItinerary": source.get("alternativeItinerary"),
            "isPublic": False,
            "copiedFromItineraryId": source_plan_id,
        }
        plan = _build_plan(cloned_id, user_id, cloned_payload, source.get("snapshotHash"), now)
        self.rds.execute(
            f"""
            INSERT INTO {self.table_name}
              (id, user_id, title, summary, duration_label, festival_choice, intensity_label,
               preference_snapshot, request_summary, source_recommendation_id, idempotency_key,
               snapshot_hash, destination_json, trip_type, themes_json, conditions_snapshot_json,
               itinerary_json, alternative_itinerary_json, is_public, copied_from_itinerary_id, saved_at, created_at, updated_at)
            VALUES
              (:id, :user_id, :title, :summary, :duration_label, :festival_choice, :intensity_label,
               :preference_snapshot, :request_summary, :source_recommendation_id, :idempotency_key,
               :snapshot_hash, :destination_json, :trip_type, :themes_json, :conditions_snapshot_json,
               :itinerary_json, :alternative_itinerary_json, :is_public, :copied_from_itinerary_id, :saved_at, :created_at, :updated_at)
            """,
            _row_params(plan),
            include_result_metadata=False,
        )
        self._insert_itinerary_items(plan)
        return plan

    def update_share_status(self, user_id, plan_id, is_public, now):
        plan = self.get_owned(user_id, plan_id)
        if not plan:
            return None
        self.rds.execute(
            f"""
            UPDATE {self.table_name}
            SET is_public = :is_public, updated_at = :updated_at
            WHERE id = :id AND user_id = :user_id AND deleted_at IS NULL
            """,
            {"id": plan_id, "user_id": user_id, "is_public": 1 if is_public else 0, "updated_at": now},
            include_result_metadata=False,
        )
        plan["isPublic"] = bool(is_public)
        plan["updatedAt"] = now
        return plan

    def delete_owned(self, user_id, plan_id, now):
        row = self.rds.fetch_one(
            f"SELECT user_id, deleted_at FROM {self.table_name} WHERE id = :id",
            {"id": plan_id},
        )
        if not row or row.get("deleted_at"):
            return "not_found"
        if row.get("user_id") != user_id:
            return "forbidden"
        self.rds.execute(
            f"""
            UPDATE {self.table_name}
            SET deleted_at = :deleted_at, updated_at = :updated_at
            WHERE id = :id AND user_id = :user_id AND deleted_at IS NULL
            """,
            {"id": plan_id, "user_id": user_id, "deleted_at": now, "updated_at": now},
            include_result_metadata=False,
        )
        return "deleted"

    def set_like(self, user_id, plan_id, liked, now):
        plan = self.get_owned(user_id, plan_id)
        if not plan:
            return None, False
        changed = bool(plan.get("isLiked")) != bool(liked)
        if liked:
            self.rds.execute(
                f"""
                INSERT INTO {self.reaction_table_name}
                  (id, user_id, itinerary_id, reaction_type, created_at, updated_at)
                VALUES
                  (:reaction_id, :user_id, :itinerary_id, 'like', :created_at, :updated_at)
                ON DUPLICATE KEY UPDATE
                  reaction_type = VALUES(reaction_type),
                  updated_at = VALUES(updated_at)
                """,
                {
                    "reaction_id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "itinerary_id": plan_id,
                    "created_at": now,
                    "updated_at": now,
                },
                include_result_metadata=False,
            )
        else:
            self.rds.execute(
                f"""
                DELETE FROM {self.reaction_table_name}
                WHERE user_id = :user_id
                  AND itinerary_id = :itinerary_id
                  AND reaction_type = 'like'
                """,
                {"user_id": user_id, "itinerary_id": plan_id},
                include_result_metadata=False,
            )
        plan["isLiked"] = bool(liked)
        plan["updatedAt"] = now
        return plan, changed

    def _replace_itinerary_items(self, plan):
        self.rds.execute(
            f"DELETE FROM {self.item_table_name} WHERE itinerary_id = :itinerary_id",
            {"itinerary_id": plan["itineraryId"]},
            include_result_metadata=False,
        )
        self._insert_itinerary_items(plan)

    def _insert_itinerary_items(self, plan):
        for item in _item_rows(plan["itineraryId"], plan.get("itinerary") or {}):
            self.rds.execute(
                f"""
                INSERT INTO {self.item_table_name}
                  (id, itinerary_id, day_index, sort_order, time_slot, place_name,
                   content_id, place_id, latitude, longitude, move_hint,
                   recommendation_reason, body, source_badges)
                VALUES
                  (:id, :itinerary_id, :day_index, :sort_order, :time_slot, :place_name,
                   :content_id, :place_id, :latitude, :longitude, :move_hint,
                   :recommendation_reason, :body, :source_badges)
                """,
                item,
                include_result_metadata=False,
            )

    def _with_itinerary_items(self, plan):
        if not plan:
            return None
        plan["itinerary"] = _merge_itinerary_snapshot_with_item_rows(
            plan.get("itinerary") or {},
            self._fetch_itinerary_items(plan["itineraryId"]),
        )
        return plan

    def _fetch_itinerary_items(self, plan_id):
        return self.rds.fetch_all(
            f"""
            SELECT id, itinerary_id, day_index, sort_order, time_slot, place_name,
                   content_id, place_id, latitude, longitude, move_hint,
                   recommendation_reason, body, source_badges
            FROM {self.item_table_name}
            WHERE itinerary_id = :itinerary_id
            ORDER BY day_index ASC, sort_order ASC
            """,
            {"itinerary_id": plan_id},
        )

    def _delete_reactions(self, plan_id, user_id):
        self.rds.execute(
            f"""
            DELETE FROM {self.reaction_table_name}
            WHERE itinerary_id = :itinerary_id
              AND user_id = :user_id
            """,
            {"itinerary_id": plan_id, "user_id": user_id},
            include_result_metadata=False,
        )

    def _find_by_idempotency_key(self, user_id, idempotency_key):
        row = self.rds.fetch_one(
            f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = :user_id
              AND idempotency_key = :idempotency_key
              AND deleted_at IS NULL
            """,
            {"user_id": user_id, "idempotency_key": idempotency_key},
        )
        return _plan_from_row(row) if row else None

    def _find_deleted_by_idempotency_key(self, user_id, idempotency_key):
        row = self.rds.fetch_one(
            f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = :user_id
              AND idempotency_key = :idempotency_key
              AND deleted_at IS NOT NULL
            """,
            {"user_id": user_id, "idempotency_key": idempotency_key},
        )
        return _plan_from_row(row) if row else None

    def _find_by_recommendation_hash(self, user_id, source_recommendation_id, snapshot_hash):
        if not source_recommendation_id:
            return None
        row = self.rds.fetch_one(
            f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = :user_id
              AND source_recommendation_id = :source_recommendation_id
              AND snapshot_hash = :snapshot_hash
              AND deleted_at IS NULL
            """,
            {"user_id": user_id, "source_recommendation_id": source_recommendation_id, "snapshot_hash": snapshot_hash},
        )
        return _plan_from_row(row) if row else None

    def _find_deleted_by_recommendation_hash(self, user_id, source_recommendation_id, snapshot_hash):
        if not source_recommendation_id:
            return None
        row = self.rds.fetch_one(
            f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = :user_id
              AND source_recommendation_id = :source_recommendation_id
              AND snapshot_hash = :snapshot_hash
              AND deleted_at IS NOT NULL
            """,
            {"user_id": user_id, "source_recommendation_id": source_recommendation_id, "snapshot_hash": snapshot_hash},
        )
        return _plan_from_row(row) if row else None


class InMemorySavedPlanRepository:
    def __init__(self, now="2026-06-10T09:00:00Z"):
        self.now = now
        self.plans = {}

    def save(self, user_id, payload, snapshot_hash, now=None):
        now = now or self.now
        idempotency_key = payload.get("idempotencyKey")
        if idempotency_key:
            for plan in self.plans.values():
                if plan["userId"] == user_id and not plan.get("deletedAt") and plan.get("idempotencyKey") == idempotency_key:
                    if plan["snapshotHash"] != snapshot_hash:
                        raise IdempotencyConflictError()
                    return dict(plan), True
            for plan in self.plans.values():
                if plan["userId"] == user_id and plan.get("deletedAt") and plan.get("idempotencyKey") == idempotency_key:
                    # Restore regardless of hash mismatch (same fix as RDS path)
                    restored = _build_plan(plan["itineraryId"], user_id, payload, snapshot_hash, now)
                    self.plans[plan["itineraryId"]] = restored
                    return dict(restored), False

        for plan in self.plans.values():
            if (
                plan["userId"] == user_id
                and not plan.get("deletedAt")
                and plan.get("sourceRecommendationId") == payload.get("sourceRecommendationId")
                and plan["snapshotHash"] == snapshot_hash
            ):
                return dict(plan), True
        for plan in self.plans.values():
            if (
                plan["userId"] == user_id
                and plan.get("deletedAt")
                and plan.get("sourceRecommendationId") == payload.get("sourceRecommendationId")
                and plan["snapshotHash"] == snapshot_hash
            ):
                restored = _build_plan(plan["itineraryId"], user_id, payload, snapshot_hash, now)
                self.plans[plan["itineraryId"]] = restored
                return dict(restored), False

        plan_id = f"plan-{len(self.plans) + 1}"
        plan = _build_plan(plan_id, user_id, payload, snapshot_hash, now)
        self.plans[plan_id] = plan
        return dict(plan), False

    def list_by_user(self, user_id, limit=20):
        plans = [plan for plan in self.plans.values() if plan["userId"] == user_id and not plan.get("deletedAt")]
        plans.sort(key=lambda plan: plan["savedAt"], reverse=True)
        return [_summary(plan) for plan in plans[:limit]]

    def get_owned(self, user_id, plan_id):
        plan = self.plans.get(plan_id)
        if not plan or plan["userId"] != user_id or plan.get("deletedAt"):
            return None
        return dict(plan)

    def get_public_or_owned(self, user_id, plan_id):
        plan = self.plans.get(plan_id)
        if not plan or plan.get("deletedAt"):
            return None
        if plan["userId"] != user_id and not plan.get("isPublic"):
            return None
        return dict(plan)

    def list_public(self, limit=20, user_id=None):
        plans = [plan for plan in self.plans.values() if plan.get("isPublic") and not plan.get("deletedAt")]
        plans.sort(key=lambda plan: plan["savedAt"], reverse=True)
        return [_summary(plan) for plan in plans[:limit]]

    def clone_itinerary(self, user_id, source_plan_id, now=None):
        source = self.get_public_or_owned(user_id, source_plan_id)
        if not source:
            return None
        cloned_id = f"plan-{len(self.plans) + 1}"
        cloned_payload = {
            "sourceRecommendationId": source.get("sourceRecommendationId"),
            "title": f"{source.get('title')} (복사본)" if source.get("userId") != user_id else source.get("title"),
            "summary": source.get("summary"),
            "destination": source.get("destination"),
            "tripType": source.get("tripType"),
            "durationLabel": source.get("durationLabel"),
            "themes": source.get("themes"),
            "festivalChoice": source.get("festivalChoice"),
            "festivalThemeLabel": source.get("festivalThemeLabel"),
            "intensityLabel": source.get("intensityLabel"),
            "preferenceSnapshot": source.get("preferenceSnapshot"),
            "conditionsSnapshot": source.get("conditionsSnapshot"),
            "requestSummary": source.get("requestSummary"),
            "itinerary": source.get("itinerary"),
            "alternativeItinerary": source.get("alternativeItinerary"),
            "isPublic": False,
            "copiedFromItineraryId": source_plan_id,
        }
        plan = _build_plan(cloned_id, user_id, cloned_payload, source.get("snapshotHash"), now or self.now)
        self.plans[cloned_id] = plan
        return dict(plan)

    def update_share_status(self, user_id, plan_id, is_public, now=None):
        plan = self.plans.get(plan_id)
        if not plan or plan["userId"] != user_id or plan.get("deletedAt"):
            return None
        plan["isPublic"] = bool(is_public)
        plan["updatedAt"] = now or self.now
        return dict(plan)

    def delete_owned(self, user_id, plan_id, now=None):
        plan = self.plans.get(plan_id)
        if not plan or plan.get("deletedAt"):
            return "not_found"
        if plan["userId"] != user_id:
            return "forbidden"
        plan["deletedAt"] = now or self.now
        plan["updatedAt"] = now or self.now
        return "deleted"

    def set_like(self, user_id, plan_id, liked, now=None):
        plan = self.plans.get(plan_id)
        if not plan or plan["userId"] != user_id or plan.get("deletedAt"):
            return None, False
        changed = bool(plan.get("isLiked")) != bool(liked)
        plan["isLiked"] = bool(liked)
        plan["updatedAt"] = now or self.now
        return dict(plan), changed


def canonical_snapshot_hash(payload):
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def _build_plan(plan_id, user_id, payload, snapshot_hash, now):
    conditions_snapshot = _conditions_snapshot(payload)
    return {
        "itineraryId": plan_id,
        "userId": user_id,
        "sourceRecommendationId": payload.get("sourceRecommendationId"),
        "idempotencyKey": payload.get("idempotencyKey"),
        "snapshotHash": snapshot_hash,
        "title": payload.get("title"),
        "summary": payload.get("summary"),
        "destination": payload.get("destination") or {},
        "tripType": payload.get("tripType"),
        "durationLabel": payload.get("durationLabel"),
        "themes": payload.get("themes") or [],
        "festivalChoice": _detail_field(payload, conditions_snapshot, "festivalChoice"),
        "festivalThemeLabel": _detail_field(payload, conditions_snapshot, "festivalThemeLabel"),
        "intensityLabel": _detail_field(payload, conditions_snapshot, "intensityLabel"),
        "preferenceSnapshot": payload.get("preferenceSnapshot") or {},
        "conditionsSnapshot": conditions_snapshot,
        "requestSummary": payload.get("requestSummary"),
        "itinerary": _itinerary_with_entry_aliases(payload.get("itinerary") or {}),
        "alternativeItinerary": payload.get("alternativeItinerary"),
        "isLiked": False,
        "isPublic": bool(payload.get("isPublic")),
        "copiedFromItineraryId": payload.get("copiedFromItineraryId"),
        "savedAt": now,
        "updatedAt": now,
        "deletedAt": None,
    }


def _summary(plan):
    return {
        "itineraryId": plan.get("itineraryId"),
        "sourceRecommendationId": plan.get("sourceRecommendationId"),
        "userId": plan.get("userId"),
        "ownerId": plan.get("userId"),
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination": plan.get("destination") or {},
        "tripType": plan.get("tripType"),
        "durationLabel": plan.get("durationLabel"),
        "themes": plan.get("themes") or [],
        "festivalChoice": plan.get("festivalChoice"),
        "intensityLabel": plan.get("intensityLabel"),
        "itinerary": _itinerary_with_entry_aliases(plan.get("itinerary") or {}),
        "isLiked": bool(plan.get("isLiked")),
        "isPublic": bool(plan.get("isPublic")),
        "copiedFromItineraryId": plan.get("copiedFromItineraryId"),
        "savedAt": plan.get("savedAt"),
        "updatedAt": plan.get("updatedAt"),
    }


def _row_params(plan):
    return {
        "id": plan["itineraryId"],
        "user_id": plan["userId"],
        "source_recommendation_id": plan.get("sourceRecommendationId"),
        "idempotency_key": plan.get("idempotencyKey"),
        "snapshot_hash": plan.get("snapshotHash"),
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination_json": json_dumps(plan.get("destination") or {}),
        "trip_type": plan.get("tripType"),
        "duration_label": plan.get("durationLabel"),
        "festival_choice": plan.get("festivalChoice"),
        "intensity_label": plan.get("intensityLabel"),
        "preference_snapshot": json_dumps(plan.get("preferenceSnapshot") or {}),
        "themes_json": json_dumps(plan.get("themes") or []),
        "conditions_snapshot_json": json_dumps(plan.get("conditionsSnapshot") or {}),
        "request_summary": plan.get("requestSummary"),
        "itinerary_json": json_dumps(plan.get("itinerary") or {}),
        "alternative_itinerary_json": json_dumps(plan.get("alternativeItinerary")),
        "is_public": 1 if plan.get("isPublic") else 0,
        "copied_from_itinerary_id": plan.get("copiedFromItineraryId"),
        "saved_at": plan.get("savedAt"),
        "created_at": plan.get("savedAt"),
        "updated_at": plan.get("updatedAt"),
    }


def _summary_from_row(row):
    return _summary(_plan_from_row(row))


def _plan_from_row(row):
    if not row:
        return None
    conditions_snapshot = json_loads(row.get("conditions_snapshot_json"), {})
    return {
        "itineraryId": row.get("id"),
        "userId": row.get("user_id"),
        "sourceRecommendationId": row.get("source_recommendation_id"),
        "idempotencyKey": row.get("idempotency_key"),
        "snapshotHash": row.get("snapshot_hash"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "destination": json_loads(row.get("destination_json"), {}),
        "tripType": row.get("trip_type"),
        "durationLabel": row.get("duration_label"),
        "themes": json_loads(row.get("themes_json"), []),
        "festivalChoice": row.get("festival_choice") or conditions_snapshot.get("festivalChoice"),
        "festivalThemeLabel": conditions_snapshot.get("festivalThemeLabel"),
        "intensityLabel": row.get("intensity_label") or conditions_snapshot.get("intensityLabel"),
        "preferenceSnapshot": json_loads(row.get("preference_snapshot"), {}),
        "conditionsSnapshot": conditions_snapshot,
        "requestSummary": row.get("request_summary"),
        "itinerary": _itinerary_with_entry_aliases(json_loads(row.get("itinerary_json"), {})),
        "alternativeItinerary": json_loads(row.get("alternative_itinerary_json"), None),
        "isLiked": bool(row.get("is_liked")),
        "isPublic": bool(row.get("is_public")),
        "copiedFromItineraryId": row.get("copied_from_itinerary_id"),
        "savedAt": row.get("saved_at"),
        "updatedAt": row.get("updated_at"),
        "deletedAt": row.get("deleted_at"),
    }


def _conditions_snapshot(payload):
    snapshot = payload.get("conditionsSnapshot")
    snapshot = dict(snapshot) if isinstance(snapshot, dict) else {}
    for field in ("festivalChoice", "festivalThemeLabel", "intensityLabel"):
        if payload.get(field) is not None and snapshot.get(field) is None:
            snapshot[field] = payload.get(field)
    return snapshot


def _detail_field(payload, conditions_snapshot, field):
    return payload.get(field) if payload.get(field) is not None else conditions_snapshot.get(field)


def _item_rows(plan_id, itinerary):
    days = itinerary.get("days") if isinstance(itinerary, dict) else []
    if not isinstance(days, list):
        return []

    rows = []
    for day_position, day in enumerate(days, start=1):
        if not isinstance(day, dict):
            continue
        day_index = _positive_int(day.get("day") or day.get("dayIndex"), day_position)
        entries = _day_entries(day)
        for entry_position, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "itinerary_id": plan_id,
                    "day_index": day_index,
                    "sort_order": _positive_int(entry.get("sortOrder") or entry.get("sort_order"), entry_position),
                    "time_slot": entry.get("time") or entry.get("timeSlot") or entry.get("time_slot"),
                    "place_name": entry.get("title") or entry.get("placeName") or entry.get("place_name"),
                    "content_id": entry.get("contentId") or entry.get("content_id"),
                    "place_id": entry.get("placeId") or entry.get("place_id"),
                    "latitude": entry.get("latitude"),
                    "longitude": entry.get("longitude"),
                    "move_hint": entry.get("move") or entry.get("moveHint") or entry.get("move_hint"),
                    "recommendation_reason": entry.get("reason") or entry.get("recommendationReason") or entry.get("recommendation_reason"),
                    "body": entry.get("body") or entry.get("description"),
                    "source_badges": json_dumps(entry.get("sourceBadges") or entry.get("source_badges") or []),
                }
            )
    return rows


def _itinerary_from_item_rows(rows):
    grouped = {}
    for row in rows or []:
        day_index = _positive_int(row.get("day_index"), 1)
        grouped.setdefault(day_index, []).append(row)

    days = []
    for day_index in sorted(grouped):
        entries = [_entry_from_item_row(row) for row in sorted(grouped[day_index], key=lambda item: _positive_int(item.get("sort_order"), 1))]
        days.append({"day": day_index, "items": entries, "stops": list(entries)})
    return {"days": days}


def _merge_itinerary_snapshot_with_item_rows(snapshot_itinerary, rows):
    row_itinerary = _itinerary_from_item_rows(rows)
    row_days = row_itinerary.get("days") if isinstance(row_itinerary, dict) else []
    if not isinstance(snapshot_itinerary, dict):
        return row_itinerary

    snapshot = _itinerary_with_entry_aliases(snapshot_itinerary)
    snapshot_days = snapshot.get("days")
    if not isinstance(snapshot_days, list):
        return row_itinerary
    if not row_days:
        return snapshot

    snapshot_by_day = {
        _positive_int(day.get("day") or day.get("dayIndex"), index): day
        for index, day in enumerate(snapshot_days, start=1)
        if isinstance(day, dict)
    }

    merged_days = []
    for row_day in row_days:
        if not isinstance(row_day, dict):
            merged_days.append(row_day)
            continue

        day_index = _positive_int(row_day.get("day") or row_day.get("dayIndex"), len(merged_days) + 1)
        snapshot_day = snapshot_by_day.get(day_index, {})
        merged_day = dict(snapshot_day) if isinstance(snapshot_day, dict) else {}
        merged_day.update({key: value for key, value in row_day.items() if key not in ("items", "stops")})
        merged_entries = _merge_day_entries(snapshot_day, row_day)
        merged_day["items"] = merged_entries
        merged_day["stops"] = list(merged_entries)
        merged_days.append(merged_day)

    merged = dict(snapshot)
    merged["days"] = merged_days
    return merged


def _merge_day_entries(snapshot_day, row_day):
    row_entries = _day_entries(row_day)
    if not isinstance(snapshot_day, dict):
        return row_entries

    snapshot_entries = _day_entries(snapshot_day)
    snapshot_by_sort_order = {
        _positive_int(entry.get("sortOrder") or entry.get("sort_order"), index): entry
        for index, entry in enumerate(snapshot_entries, start=1)
        if isinstance(entry, dict)
    }
    snapshot_by_title = {
        str(entry.get("title") or entry.get("placeName") or entry.get("place_name") or "").strip(): entry
        for entry in snapshot_entries
        if isinstance(entry, dict) and str(entry.get("title") or entry.get("placeName") or entry.get("place_name") or "").strip()
    }

    merged_entries = []
    for index, row_entry in enumerate(row_entries, start=1):
        if not isinstance(row_entry, dict):
            merged_entries.append(row_entry)
            continue
        sort_order = _positive_int(row_entry.get("sortOrder") or row_entry.get("sort_order"), index)
        title = str(row_entry.get("title") or row_entry.get("placeName") or row_entry.get("place_name") or "").strip()
        snapshot_entry = snapshot_by_sort_order.get(sort_order) or snapshot_by_title.get(title) or {}
        merged_entry = dict(snapshot_entry) if isinstance(snapshot_entry, dict) else {}
        merged_entry.update(row_entry)
        merged_entries.append(merged_entry)

    return merged_entries


def _entry_from_item_row(row):
    entry = {
        "itemId": row.get("id"),
        "sortOrder": _positive_int(row.get("sort_order"), 1),
        "title": row.get("place_name"),
        "body": row.get("body"),
    }
    optional_fields = {
        "time": row.get("time_slot"),
        "timeSlot": row.get("time_slot"),
        "contentId": row.get("content_id"),
        "placeId": row.get("place_id"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "move": row.get("move_hint"),
        "moveHint": row.get("move_hint"),
        "reason": row.get("recommendation_reason"),
        "recommendationReason": row.get("recommendation_reason"),
        "sourceBadges": json_loads(row.get("source_badges"), []),
    }
    entry.update({key: value for key, value in optional_fields.items() if value not in (None, "", [])})
    return entry


def _day_entries(day):
    items = day.get("items")
    stops = day.get("stops")
    if isinstance(items, list) and items:
        return items
    if isinstance(stops, list) and stops:
        return stops
    return []


def _positive_int(value, default):
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return default


def _itinerary_with_entry_aliases(itinerary):
    if not isinstance(itinerary, dict):
        return {}

    days = itinerary.get("days")
    if not isinstance(days, list):
        return dict(itinerary)

    normalized_days = []
    for day in days:
        if not isinstance(day, dict):
            normalized_days.append(day)
            continue

        normalized_day = dict(day)
        items = normalized_day.get("items")
        stops = normalized_day.get("stops")
        if "stops" not in normalized_day and isinstance(items, list):
            normalized_day["stops"] = items
        if "items" not in normalized_day and isinstance(stops, list):
            normalized_day["items"] = stops
        normalized_days.append(normalized_day)

    normalized = dict(itinerary)
    normalized["days"] = normalized_days
    return normalized


# EOF: src/saved_plans/repository.py

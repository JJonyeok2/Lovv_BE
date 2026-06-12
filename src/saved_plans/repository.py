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
    def __init__(self, rds_client=None, table_name=None):
        self.rds = rds_client or create_database_client()
        self.table_name = table_name or os.environ.get("SAVED_PLANS_TABLE_NAME", "saved_plans")

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

        existing = self._find_by_recommendation_hash(user_id, payload.get("sourceRecommendationId"), snapshot_hash)
        if existing:
            return existing, True

        plan_id = str(uuid.uuid4())
        plan = _build_plan(plan_id, user_id, payload, snapshot_hash, now)
        self.rds.execute(
            f"""
            INSERT INTO {self.table_name}
              (id, user_id, source_recommendation_id, idempotency_key, snapshot_hash, title, summary,
               destination_json, trip_type, duration_label, themes_json, conditions_snapshot_json,
               request_summary, itinerary_json, alternative_itinerary_json, is_liked, saved_at, updated_at)
            VALUES
              (:id, :user_id, :source_recommendation_id, :idempotency_key, :snapshot_hash, :title, :summary,
               :destination_json, :trip_type, :duration_label, :themes_json, :conditions_snapshot_json,
               :request_summary, :itinerary_json, :alternative_itinerary_json, :is_liked, :saved_at, :updated_at)
            """,
            _row_params(plan),
            include_result_metadata=False,
        )
        return plan, False

    def list_by_user(self, user_id, limit=20):
        rows = self.rds.fetch_all(
            f"""
            SELECT id, source_recommendation_id, title, summary, destination_json, trip_type, duration_label,
                   themes_json, itinerary_json, is_liked, saved_at, updated_at
            FROM {self.table_name}
            WHERE user_id = :user_id
            ORDER BY saved_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [_summary_from_row(row) for row in rows]

    def get_owned(self, user_id, plan_id):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.table_name} WHERE id = :id AND user_id = :user_id",
            {"id": plan_id, "user_id": user_id},
        )
        return _plan_from_row(row) if row else None

    def set_like(self, user_id, plan_id, liked, now):
        plan = self.get_owned(user_id, plan_id)
        if not plan:
            return None, False
        changed = bool(plan.get("isLiked")) != bool(liked)
        self.rds.execute(
            f"""
            UPDATE {self.table_name}
            SET is_liked = :is_liked, updated_at = :updated_at
            WHERE id = :id AND user_id = :user_id
            """,
            {"id": plan_id, "user_id": user_id, "is_liked": bool(liked), "updated_at": now},
            include_result_metadata=False,
        )
        plan["isLiked"] = bool(liked)
        plan["updatedAt"] = now
        return plan, changed

    def _find_by_idempotency_key(self, user_id, idempotency_key):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.table_name} WHERE user_id = :user_id AND idempotency_key = :idempotency_key",
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
            """,
            {"user_id": user_id, "source_recommendation_id": source_recommendation_id, "snapshot_hash": snapshot_hash},
        )
        return _plan_from_row(row) if row else None


class InMemorySavedPlanRepository:
    def __init__(self, now="2026-06-10T09:00:00Z"):
        self.now = now
        self.plans = {}

    def save(self, user_id, payload, snapshot_hash, now=None):
        idempotency_key = payload.get("idempotencyKey")
        if idempotency_key:
            for plan in self.plans.values():
                if plan["userId"] == user_id and plan.get("idempotencyKey") == idempotency_key:
                    if plan["snapshotHash"] != snapshot_hash:
                        raise IdempotencyConflictError()
                    return dict(plan), True

        for plan in self.plans.values():
            if (
                plan["userId"] == user_id
                and plan.get("sourceRecommendationId") == payload.get("sourceRecommendationId")
                and plan["snapshotHash"] == snapshot_hash
            ):
                return dict(plan), True

        plan_id = f"plan-{len(self.plans) + 1}"
        plan = _build_plan(plan_id, user_id, payload, snapshot_hash, now or self.now)
        self.plans[plan_id] = plan
        return dict(plan), False

    def list_by_user(self, user_id, limit=20):
        plans = [plan for plan in self.plans.values() if plan["userId"] == user_id]
        plans.sort(key=lambda plan: plan["savedAt"], reverse=True)
        return [_summary(plan) for plan in plans[:limit]]

    def get_owned(self, user_id, plan_id):
        plan = self.plans.get(plan_id)
        if not plan or plan["userId"] != user_id:
            return None
        return dict(plan)

    def set_like(self, user_id, plan_id, liked, now=None):
        plan = self.plans.get(plan_id)
        if not plan or plan["userId"] != user_id:
            return None, False
        changed = bool(plan.get("isLiked")) != bool(liked)
        plan["isLiked"] = bool(liked)
        plan["updatedAt"] = now or self.now
        return dict(plan), changed


def canonical_snapshot_hash(payload):
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def _build_plan(plan_id, user_id, payload, snapshot_hash, now):
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
        "conditionsSnapshot": payload.get("conditionsSnapshot") or {},
        "requestSummary": payload.get("requestSummary"),
        "itinerary": _itinerary_with_entry_aliases(payload.get("itinerary") or {}),
        "alternativeItinerary": payload.get("alternativeItinerary"),
        "isLiked": False,
        "savedAt": now,
        "updatedAt": now,
    }


def _summary(plan):
    return {
        "itineraryId": plan.get("itineraryId"),
        "sourceRecommendationId": plan.get("sourceRecommendationId"),
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination": plan.get("destination") or {},
        "tripType": plan.get("tripType"),
        "durationLabel": plan.get("durationLabel"),
        "themes": plan.get("themes") or [],
        "itinerary": _itinerary_with_entry_aliases(plan.get("itinerary") or {}),
        "isLiked": bool(plan.get("isLiked")),
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
        "themes_json": json_dumps(plan.get("themes") or []),
        "conditions_snapshot_json": json_dumps(plan.get("conditionsSnapshot") or {}),
        "request_summary": plan.get("requestSummary"),
        "itinerary_json": json_dumps(plan.get("itinerary") or {}),
        "alternative_itinerary_json": json_dumps(plan.get("alternativeItinerary")),
        "is_liked": bool(plan.get("isLiked")),
        "saved_at": plan.get("savedAt"),
        "updated_at": plan.get("updatedAt"),
    }


def _summary_from_row(row):
    return _summary(_plan_from_row(row))


def _plan_from_row(row):
    if not row:
        return None
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
        "conditionsSnapshot": json_loads(row.get("conditions_snapshot_json"), {}),
        "requestSummary": row.get("request_summary"),
        "itinerary": _itinerary_with_entry_aliases(json_loads(row.get("itinerary_json"), {})),
        "alternativeItinerary": json_loads(row.get("alternative_itinerary_json"), None),
        "isLiked": bool(row.get("is_liked")),
        "savedAt": row.get("saved_at"),
        "updatedAt": row.get("updated_at"),
    }


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

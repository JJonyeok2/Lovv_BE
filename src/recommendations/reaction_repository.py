import os

from shared.database import create_database_client
from shared.rds_data import json_loads


class RdsRecommendationReactionRepository:
    def __init__(self, rds_client=None, itinerary_table_name=None, reaction_table_name=None, users_table_name=None):
        self.rds = rds_client or create_database_client()
        self.itinerary_table_name = itinerary_table_name or os.environ.get("SAVED_PLANS_TABLE_NAME", "itineraries")
        self.reaction_table_name = reaction_table_name or os.environ.get("PLAN_REACTIONS_TABLE_NAME", "plan_reactions")
        self.users_table_name = users_table_name or os.environ.get("USERS_TABLE_NAME", "users")

    @classmethod
    def from_env(cls):
        return cls()

    def list_liked_itinerary_signals(self, user_id, limit=20):
        rows = self.rds.fetch_all(
            f"""
            SELECT pr.itinerary_id, pr.reaction_type, COALESCE(pr.updated_at, pr.created_at) AS reacted_at,
                   i.destination_json, i.themes_json, i.duration_label, i.trip_type, i.title, i.summary
            FROM {self.reaction_table_name} pr
            JOIN {self.itinerary_table_name} i ON i.id = pr.itinerary_id
            WHERE pr.user_id = :user_id
              AND pr.reaction_type = 'like'
              AND i.deleted_at IS NULL
            ORDER BY reacted_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": int(limit)},
        )
        return [_signal_from_row(row) for row in rows]

    def list_popular_destination_signals(self, limit=120):
        rows = self.rds.fetch_all(
            f"""
            SELECT i.id AS itinerary_id, i.user_id, i.destination_json, i.themes_json, i.saved_at,
                   u.birth_date, COUNT(pr.itinerary_id) AS reaction_count
            FROM {self.itinerary_table_name} i
            JOIN {self.reaction_table_name} pr
              ON pr.itinerary_id = i.id
             AND pr.reaction_type = 'like'
            LEFT JOIN {self.users_table_name} u ON u.id = i.user_id
            WHERE i.deleted_at IS NULL
            GROUP BY i.id, i.user_id, i.destination_json, i.themes_json, i.saved_at, u.birth_date
            ORDER BY reaction_count DESC, i.saved_at DESC
            LIMIT :limit
            """,
            {"limit": int(limit)},
        )
        return [_popular_signal_from_row(row) for row in rows]


def _signal_from_row(row):
    return {
        "sourceReaction": {
            "itineraryId": row.get("itinerary_id"),
            "reaction": row.get("reaction_type"),
            "reactedAt": row.get("reacted_at"),
        },
        "destination": json_loads(row.get("destination_json"), default={}) or {},
        "themes": json_loads(row.get("themes_json"), default=[]) or [],
        "durationLabel": row.get("duration_label"),
        "tripType": row.get("trip_type"),
        "title": row.get("title"),
        "summary": row.get("summary"),
    }


def _popular_signal_from_row(row):
    return {
        "userId": row.get("user_id"),
        "destination": json_loads(row.get("destination_json"), default={}) or {},
        "themes": json_loads(row.get("themes_json"), default=[]) or [],
        "reactionCount": int(row.get("reaction_count") or 0),
        "savedPlanCount": 1,
        "savedAt": row.get("saved_at"),
        "birthDate": row.get("birth_date"),
    }

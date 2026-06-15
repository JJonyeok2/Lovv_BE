# @file src/admin/repository.py
# @description Read-only admin user repository backed by the configured SQL client.
# @lastModified 2026-06-14

import os

from auth.user_repository import roles_for_db_role
from shared.database import create_database_client


class RdsDataAdminUserRepository:
    def __init__(
        self,
        rds_client=None,
        users_table=None,
        social_accounts_table=None,
        preferences_table=None,
        itineraries_table=None,
    ):
        self.rds = rds_client or create_database_client()
        self.users_table = users_table or os.environ.get("USERS_TABLE_NAME", "users")
        self.social_accounts_table = social_accounts_table or os.environ.get("SOCIAL_ACCOUNTS_TABLE_NAME", "social_accounts")
        self.preferences_table = preferences_table or os.environ.get("PREFERENCES_TABLE_NAME", "user_preferences")
        self.itineraries_table = itineraries_table or os.environ.get("SAVED_PLANS_TABLE_NAME", "itineraries")

    @classmethod
    def from_env(cls):
        return cls()

    def list_users(self):
        rows = self.rds.fetch_all(self._base_query() + " ORDER BY u.created_at DESC", {})
        return [_admin_user_from_row(row) for row in rows]

    def get_user(self, user_id):
        row = self.rds.fetch_one(
            self._base_query("WHERE u.id = :user_id"),
            {"user_id": user_id},
        )
        return _admin_user_from_row(row) if row else None

    def _base_query(self, where_clause=""):
        return f"""
            SELECT
              u.id,
              u.display_name,
              u.nickname,
              u.email,
              u.status,
              u.role,
              u.created_at,
              u.updated_at,
              u.last_login_at,
              GROUP_CONCAT(DISTINCT sa.provider ORDER BY sa.provider SEPARATOR ',') AS linked_providers,
              COALESCE(MAX(CASE WHEN up.onboarding_completed THEN 1 ELSE 0 END), 0) AS onboarding_completed,
              COUNT(DISTINCT i.id) AS saved_itinerary_count
            FROM {self.users_table} u
            LEFT JOIN {self.social_accounts_table} sa ON sa.user_id = u.id
            LEFT JOIN {self.preferences_table} up ON up.user_id = u.id
            LEFT JOIN {self.itineraries_table} i ON i.user_id = u.id AND i.deleted_at IS NULL
            {where_clause}
            GROUP BY
              u.id,
              u.display_name,
              u.nickname,
              u.email,
              u.status,
              u.role,
              u.created_at,
              u.updated_at,
              u.last_login_at
        """


def _admin_user_from_row(row):
    providers = row.get("linked_providers")
    if isinstance(providers, str):
        linked_providers = [provider for provider in providers.split(",") if provider]
    elif isinstance(providers, list):
        linked_providers = providers
    else:
        linked_providers = []

    return {
        "userId": str(row.get("id", "")),
        "displayName": row.get("display_name"),
        "nickname": row.get("nickname"),
        "email": row.get("email"),
        "status": row.get("status") or "active",
        "role": row.get("role") or "user",
        "roles": roles_for_db_role(row.get("role")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "lastLoginAt": row.get("last_login_at"),
        "linkedProviders": linked_providers,
        "onboardingCompleted": bool(row.get("onboarding_completed")),
        "savedItineraryCount": int(row.get("saved_itinerary_count") or 0),
    }


# EOF: src/admin/repository.py

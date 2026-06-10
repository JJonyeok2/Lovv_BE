import os
import uuid

from shared.rds_data import RdsDataClient, json_dumps, json_loads


class RdsDataPreferenceRepository:
    def __init__(self, rds_client=None, table_name=None):
        self.rds = rds_client or RdsDataClient()
        self.table_name = table_name or os.environ.get("PREFERENCES_TABLE_NAME", "preferences")

    @classmethod
    def from_env(cls):
        return cls()

    def get_by_user_id(self, user_id):
        row = self.rds.fetch_one(
            f"""
            SELECT id, user_id, country_track, mapped_themes, preferred_regions, selected_city_style,
                   pace, trip_days, companion_style, travel_styles, onboarding_completed, created_at, updated_at
            FROM {self.table_name}
            WHERE user_id = :user_id
            """,
            {"user_id": user_id},
        )
        return _preference_from_row(row) if row else None

    def upsert(self, user_id, payload, now):
        existing = self.get_by_user_id(user_id)
        preference_id = existing["preferenceId"] if existing else str(uuid.uuid4())
        params = _row_params(preference_id, user_id, payload, now, existing)
        self.rds.execute(
            f"""
            INSERT INTO {self.table_name}
              (id, user_id, country_track, mapped_themes, preferred_regions, selected_city_style,
               pace, trip_days, companion_style, travel_styles, onboarding_completed, created_at, updated_at)
            VALUES
              (:id, :user_id, :country_track, :mapped_themes, :preferred_regions, :selected_city_style,
               :pace, :trip_days, :companion_style, :travel_styles, :onboarding_completed, :created_at, :updated_at)
            ON DUPLICATE KEY UPDATE
              country_track = VALUES(country_track),
              mapped_themes = VALUES(mapped_themes),
              preferred_regions = VALUES(preferred_regions),
              selected_city_style = VALUES(selected_city_style),
              pace = VALUES(pace),
              trip_days = VALUES(trip_days),
              companion_style = VALUES(companion_style),
              travel_styles = VALUES(travel_styles),
              onboarding_completed = VALUES(onboarding_completed),
              updated_at = VALUES(updated_at)
            """,
            params,
            include_result_metadata=False,
        )
        saved = dict(payload)
        saved.update(
            {
                "preferenceId": preference_id,
                "userId": user_id,
                "onboardingCompleted": True,
                "createdAt": params["created_at"],
                "updatedAt": now,
            }
        )
        return saved


class InMemoryPreferenceRepository:
    def __init__(self, now="2026-06-10T09:00:00Z"):
        self.now = now
        self.preferences_by_user = {}

    def get_by_user_id(self, user_id):
        preference = self.preferences_by_user.get(user_id)
        return dict(preference) if preference else None

    def upsert(self, user_id, payload, now=None):
        existing = self.preferences_by_user.get(user_id)
        saved = dict(payload)
        saved.update(
            {
                "preferenceId": existing["preferenceId"] if existing else f"pref-{len(self.preferences_by_user) + 1}",
                "userId": user_id,
                "onboardingCompleted": True,
                "createdAt": existing["createdAt"] if existing else (now or self.now),
                "updatedAt": now or self.now,
            }
        )
        self.preferences_by_user[user_id] = saved
        return dict(saved)


def _row_params(preference_id, user_id, payload, now, existing):
    return {
        "id": preference_id,
        "user_id": user_id,
        "country_track": payload.get("countryTrack"),
        "mapped_themes": json_dumps(payload.get("mappedThemes") or []),
        "preferred_regions": json_dumps(payload.get("preferredRegions") or []),
        "selected_city_style": payload.get("selectedCityStyle"),
        "pace": payload.get("pace"),
        "trip_days": payload.get("tripDays"),
        "companion_style": payload.get("companionStyle"),
        "travel_styles": json_dumps(payload.get("travelStyles") or []),
        "onboarding_completed": True,
        "created_at": existing["createdAt"] if existing else now,
        "updated_at": now,
    }


def _preference_from_row(row):
    return {
        "preferenceId": row.get("id"),
        "userId": row.get("user_id"),
        "countryTrack": row.get("country_track"),
        "mappedThemes": json_loads(row.get("mapped_themes"), []),
        "preferredRegions": json_loads(row.get("preferred_regions"), []),
        "selectedCityStyle": row.get("selected_city_style"),
        "pace": row.get("pace"),
        "tripDays": row.get("trip_days"),
        "companionStyle": row.get("companion_style"),
        "travelStyles": json_loads(row.get("travel_styles"), []),
        "onboardingCompleted": bool(row.get("onboarding_completed")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }

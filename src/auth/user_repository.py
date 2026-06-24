# @file src/auth/user_repository.py
# @description User and social account repository for Lovv auth persistence.
# @lastModified 2026-06-12

import dataclasses
import os

from auth.authz_cache_repository import DynamoDbAuthzCacheRepository
import uuid

from shared.database import create_database_client


@dataclasses.dataclass(frozen=True)
class UserLoginResult:
    user: dict
    is_new_user: bool


class UserRepositoryError(Exception):
    def __init__(self, code, message="User repository error", status_code=500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class RdsDataUserRepository:
    def __init__(
        self,
        rds_client=None,
        users_table=None,
        social_accounts_table=None,
        role_assignments_table=None,
        region_assignments_table=None,
        authz_cache=None,
    ):
        self.rds = rds_client or create_database_client()
        self.authz_cache = authz_cache
        self.users_table = users_table or os.environ.get("USERS_TABLE_NAME", "users")
        self.social_accounts_table = social_accounts_table or os.environ.get("SOCIAL_ACCOUNTS_TABLE_NAME", "social_accounts")
        self.role_assignments_table = role_assignments_table or os.environ.get("USER_ROLE_ASSIGNMENTS_TABLE_NAME", "user_role_assignments")
        self.region_assignments_table = region_assignments_table or os.environ.get("USER_REGION_ASSIGNMENTS_TABLE_NAME", "user_region_assignments")

    @classmethod
    def from_env(cls):
        # Build the authz cache best-effort: if it is not configured (or boto3 is
        # unavailable), caching is simply disabled and every call hits MySQL.
        authz_cache = None
        try:
            candidate = DynamoDbAuthzCacheRepository.from_env()
            authz_cache = candidate if candidate.enabled else None
        except Exception:
            authz_cache = None
        return cls(authz_cache=authz_cache)

    def upsert_from_provider(self, identity, now):
        linked = self._find_by_social(identity.provider, identity.provider_user_id)
        if linked:
            self._record_social_login(linked["userId"], identity, now)
            user = self.get_user(linked["userId"])
            if not user:
                raise UserRepositoryError("USER_NOT_FOUND", "User was not found", 404)
            return UserLoginResult(user=user, is_new_user=False)

        user = None
        if identity.email and identity.email_verified:
            user = self._find_by_verified_email(identity.email)

        if user is None:
            user = self._create_user(identity, now)
            is_new = True
        else:
            is_new = False

        self._create_social_account(user["userId"], identity, now)
        return UserLoginResult(user=user, is_new_user=is_new)

    def get_user(self, user_id):
        row = self.rds.fetch_one(
            f"""
            SELECT id, email, display_name, avatar_url, birth_date, gender, created_at, status, role
            FROM {self.users_table}
            WHERE id = :user_id AND status = 'active'
            """,
            {"user_id": user_id},
        )
        return self._with_authorization(_user_from_row(row)) if row else None

    def _find_by_social(self, provider, provider_user_id):
        row = self.rds.fetch_one(
            f"""
            SELECT u.id, u.email, u.display_name, u.avatar_url, u.birth_date, u.gender, u.created_at, u.status, u.role
            FROM {self.social_accounts_table} sa
            JOIN {self.users_table} u ON u.id = sa.user_id
            WHERE sa.provider = :provider
              AND sa.provider_user_id = :provider_user_id
              AND u.status = 'active'
            """,
            {"provider": provider, "provider_user_id": provider_user_id},
        )
        return _user_from_row(row) if row else None

    def _find_by_verified_email(self, email):
        row = self.rds.fetch_one(
            f"""
            SELECT id, email, display_name, avatar_url, birth_date, gender, created_at, status, role
            FROM {self.users_table}
            WHERE email = :email AND status = 'active'
            """,
            {"email": email},
        )
        return self._with_authorization(_user_from_row(row)) if row else None

    def _create_user(self, identity, now):
        user_id = str(uuid.uuid4())
        display_name = identity.display_name or "Lovv User"
        self.rds.execute(
            f"""
            INSERT INTO {self.users_table}
              (id, email, email_verified, display_name, avatar_url, status, last_login_at, created_at, updated_at)
            VALUES
              (:id, :email, :email_verified, :display_name, :avatar_url, 'active', :now, :now, :now)
            """,
            {
                "id": user_id,
                "email": identity.email if identity.email_verified else None,
                "email_verified": bool(identity.email_verified),
                "display_name": display_name,
                "avatar_url": identity.avatar_url,
                "now": now,
            },
            include_result_metadata=False,
        )
        return {
            "userId": user_id,
            "email": identity.email if identity.email_verified else None,
            "displayName": display_name,
            "avatarUrl": identity.avatar_url,
            "birthDate": None,
            "gender": None,
            "createdAt": now,
            "role": "user",
            "roles": ["R-USER"],
            "status": "active",
        }

    def _create_social_account(self, user_id, identity, now):
        self.rds.execute(
            f"""
            INSERT INTO {self.social_accounts_table}
              (id, user_id, provider, provider_user_id, email, email_verified, provider_nickname,
               provider_profile_image_url, created_at, last_login_at)
            VALUES
              (:id, :user_id, :provider, :provider_user_id, :email, :email_verified, :provider_nickname,
               :provider_profile_image_url, :now, :now)
            """,
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "provider": identity.provider,
                "provider_user_id": identity.provider_user_id,
                "email": identity.email,
                "email_verified": bool(identity.email_verified),
                "provider_nickname": identity.display_name,
                "provider_profile_image_url": identity.avatar_url,
                "now": now,
            },
            include_result_metadata=False,
        )

    def _record_social_login(self, user_id, identity, now):
        self.rds.execute(
            f"""
            UPDATE {self.users_table}
            SET last_login_at = :now, updated_at = :now
            WHERE id = :user_id
            """,
            {"user_id": user_id, "now": now},
            include_result_metadata=False,
        )
        self.rds.execute(
            f"""
            UPDATE {self.social_accounts_table}
            SET email = :email,
                email_verified = :email_verified,
                provider_nickname = :provider_nickname,
                provider_profile_image_url = :provider_profile_image_url,
                last_login_at = :now
            WHERE provider = :provider AND provider_user_id = :provider_user_id
            """,
            {
                "provider": identity.provider,
                "provider_user_id": identity.provider_user_id,
                "email": identity.email,
                "email_verified": bool(identity.email_verified),
                "provider_nickname": identity.display_name,
                "provider_profile_image_url": identity.avatar_url,
                "now": now,
            },
            include_result_metadata=False,
        )

    def update_profile(self, user_id, now, fields):
        fields = {key: value for key, value in (fields or {}).items() if key in _PROFILE_UPDATE_COLUMNS}
        if fields:
            assignments = [f"{column} = :{column}" for column in fields]
            assignments.append("updated_at = :now")
            params = dict(fields)
            params["user_id"] = user_id
            params["now"] = now
            self.rds.execute(
                f"""
                UPDATE {self.users_table}
                SET {", ".join(assignments)}
                WHERE id = :user_id AND status = 'active'
                """,
                params,
                include_result_metadata=False,
            )

        user = self.get_user(user_id)
        if not user:
            raise UserRepositoryError("USER_NOT_FOUND", "User was not found", 404)
        return user

    def link_provider_to_user(self, user_id, identity, now):
        existing = self._find_by_social(identity.provider, identity.provider_user_id)
        if existing:
            if existing["userId"] == user_id:
                raise UserRepositoryError(
                    "SOCIAL_ACCOUNT_ALREADY_LINKED",
                    "This provider account is already linked to your profile.",
                    409,
                )
            raise UserRepositoryError(
                "SOCIAL_ACCOUNT_LINKED_TO_ANOTHER_USER",
                "This provider account is already linked to a different account.",
                409,
            )

        self._create_social_account(user_id, identity, now)
        return self.list_social_accounts(user_id)

    def list_social_accounts(self, user_id):
        rows = self.rds.fetch_all(
            f"""
            SELECT provider, provider_user_id, provider_nickname, provider_profile_image_url,
                   created_at, last_login_at
            FROM {self.social_accounts_table}
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
        )
        return [_social_account_from_row(row) for row in (rows or [])]

    def _with_authorization(self, user):
        # The DB is the source of truth for admin authority: merge the legacy
        # users.role with the active role/region assignments so the issued token
        # reflects current grants (revoking an assignment drops it on next login).
        # A short-TTL cache (when configured) short-circuits the SQL round-trips.
        if not user:
            return None
        user_id = user["userId"]
        if self.authz_cache is not None:
            cached = self.authz_cache.get(user_id)
            if cached is not None:
                user["roles"] = list(cached["roles"])
                user["organizationIds"] = list(cached["organizationIds"])
                user["regionIds"] = list(cached["regionIds"])
                user["authzVersion"] = cached["authzVersion"]
                return user
        role_assignments = self._active_role_assignments(user_id)
        region_assignments = self._active_region_assignments(user_id)
        user["roles"] = _merge_unique(
            roles_for_db_role(user.get("role"))
            + [row.get("role_code") for row in role_assignments]
        )
        user["organizationIds"] = _merge_unique(
            [row.get("organization_id") for row in role_assignments]
            + [row.get("organization_id") for row in region_assignments]
        )
        user["regionIds"] = _merge_unique([row.get("region_id") for row in region_assignments])
        user["authzVersion"] = 1
        if self.authz_cache is not None:
            self.authz_cache.put(
                user_id,
                {
                    "roles": user["roles"],
                    "organizationIds": user["organizationIds"],
                    "regionIds": user["regionIds"],
                    "authzVersion": user["authzVersion"],
                },
            )
        return user

    def _active_role_assignments(self, user_id):
        # Only assignments that are active AND within their valid_from/valid_until
        # window count; suspended/revoked/expired grants are ignored.
        return self.rds.fetch_all(
            f"""
            SELECT role_code, organization_id
            FROM {self.role_assignments_table}
            WHERE user_id = :user_id
              AND status = 'active'
              AND valid_from <= UTC_TIMESTAMP(3)
              AND (valid_until IS NULL OR valid_until > UTC_TIMESTAMP(3))
            ORDER BY role_code ASC, organization_id ASC
            """,
            {"user_id": user_id},
        )

    def _active_region_assignments(self, user_id):
        return self.rds.fetch_all(
            f"""
            SELECT region_id, organization_id
            FROM {self.region_assignments_table}
            WHERE user_id = :user_id
              AND status = 'active'
              AND valid_from <= UTC_TIMESTAMP(3)
              AND (valid_until IS NULL OR valid_until > UTC_TIMESTAMP(3))
            ORDER BY region_id ASC, organization_id ASC
            """,
            {"user_id": user_id},
        )


_PROFILE_UPDATE_COLUMNS = {"display_name", "birth_date", "gender"}


class InMemoryUserRepository:
    def __init__(self, now="2026-06-10T09:00:00Z"):
        self.now = now
        self.users = {}
        self.social_accounts = {}
        self.role_assignments = []
        self.region_assignments = []

    def upsert_from_provider(self, identity, now=None):
        key = (identity.provider, identity.provider_user_id)
        if key in self.social_accounts:
            user_id = self.social_accounts[key]["userId"]
            user = self.users[user_id]
            user["lastLoginAt"] = now or self.now
            return UserLoginResult(user=self._with_authorization(dict(user)), is_new_user=False)

        user = None
        if identity.email and identity.email_verified:
            user = next(
                (candidate for candidate in self.users.values() if candidate.get("email") == identity.email and candidate.get("status") == "active"),
                None,
            )

        is_new = user is None
        if user is None:
            user_id = f"user-{len(self.users) + 1}"
            user = {
                "userId": user_id,
                "email": identity.email if identity.email_verified else None,
                "displayName": identity.display_name or "Lovv User",
                "avatarUrl": identity.avatar_url,
                "birthDate": None,
                "gender": None,
                "role": "user",
                "roles": ["R-USER"],
                "status": "active",
                "createdAt": now or self.now,
                "updatedAt": now or self.now,
                "lastLoginAt": now or self.now,
            }
            self.users[user_id] = user
        else:
            user["lastLoginAt"] = now or self.now

        self.social_accounts[key] = {
            "userId": user["userId"],
            "provider": identity.provider,
            "providerUserId": identity.provider_user_id,
            "email": identity.email,
            "emailVerified": identity.email_verified,
            "nickname": identity.display_name,
            "avatarUrl": identity.avatar_url,
            "linkedAt": now or self.now,
            "lastLoginAt": now or self.now,
        }
        return UserLoginResult(user=self._with_authorization(dict(user)), is_new_user=is_new)

    def get_user(self, user_id):
        user = self.users.get(user_id)
        if not user or user.get("status") != "active":
            return None
        result = dict(user)
        return self._with_authorization(result)

    def update_profile(self, user_id, now, fields):
        user = self.users.get(user_id)
        if not user or user.get("status") != "active":
            raise UserRepositoryError("USER_NOT_FOUND", "User was not found", 404)

        fields = fields or {}
        if "display_name" in fields:
            user["displayName"] = fields["display_name"]
        if "birth_date" in fields:
            user["birthDate"] = fields["birth_date"]
        if "gender" in fields:
            user["gender"] = fields["gender"]
        user["updatedAt"] = now or self.now
        return self.get_user(user_id)

    def link_provider_to_user(self, user_id, identity, now=None):
        key = (identity.provider, identity.provider_user_id)
        existing = self.social_accounts.get(key)
        if existing:
            if existing["userId"] == user_id:
                raise UserRepositoryError(
                    "SOCIAL_ACCOUNT_ALREADY_LINKED",
                    "This provider account is already linked to your profile.",
                    409,
                )
            raise UserRepositoryError(
                "SOCIAL_ACCOUNT_LINKED_TO_ANOTHER_USER",
                "This provider account is already linked to a different account.",
                409,
            )

        self.social_accounts[key] = {
            "userId": user_id,
            "provider": identity.provider,
            "providerUserId": identity.provider_user_id,
            "email": identity.email,
            "emailVerified": identity.email_verified,
            "nickname": identity.display_name,
            "avatarUrl": identity.avatar_url,
            "linkedAt": now or self.now,
            "lastLoginAt": now or self.now,
        }
        return self.list_social_accounts(user_id)

    def list_social_accounts(self, user_id):
        return [
            {
                "provider": account["provider"],
                "providerUserId": account["providerUserId"],
                "nickname": account.get("nickname"),
                "avatarUrl": account.get("avatarUrl"),
                "linkedAt": account.get("linkedAt"),
                "lastLoginAt": account.get("lastLoginAt"),
            }
            for account in self.social_accounts.values()
            if account["userId"] == user_id
        ]

    def _with_authorization(self, user):
        role_assignments = [assignment for assignment in self.role_assignments if _active_assignment(assignment, user["userId"], self.now)]
        region_assignments = [assignment for assignment in self.region_assignments if _active_assignment(assignment, user["userId"], self.now)]
        user["roles"] = _merge_unique(
            roles_for_db_role(user.get("role"))
            + [_assignment_value(assignment, "roleCode", "role_code") for assignment in role_assignments]
        )
        user["organizationIds"] = _merge_unique(
            [_assignment_value(assignment, "organizationId", "organization_id") for assignment in role_assignments]
            + [_assignment_value(assignment, "organizationId", "organization_id") for assignment in region_assignments]
        )
        user["regionIds"] = _merge_unique([_assignment_value(assignment, "regionId", "region_id") for assignment in region_assignments])
        user["authzVersion"] = 1
        return user


def roles_for_db_role(role):
    # Map the legacy single users.role column to RBAC role codes. Unknown values
    # (e.g. "system") map to [] so they grant nothing by default (fail closed).
    normalized = (role or "user").strip().lower() if isinstance(role, str) else "user"
    if normalized == "admin":
        return ["R-ADMIN"]
    if normalized == "user":
        return ["R-USER"]
    return []


def _merge_unique(values):
    merged = []
    seen = set()
    for value in values or []:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in seen:
            merged.append(text)
            seen.add(text)
    return merged


def _assignment_value(assignment, camel_key, snake_key):
    return assignment.get(camel_key) if camel_key in assignment else assignment.get(snake_key)


def _active_assignment(assignment, user_id, now):
    if assignment.get("userId") not in (None, user_id) and assignment.get("user_id") not in (None, user_id):
        return False
    if (assignment.get("status") or "active") != "active":
        return False
    valid_from = assignment.get("validFrom") or assignment.get("valid_from")
    valid_until = assignment.get("validUntil") or assignment.get("valid_until")
    if valid_from and str(valid_from) > now:
        return False
    if valid_until and str(valid_until) <= now:
        return False
    return True


def _user_from_row(row):
    return {
        "userId": str(row.get("id", "")),
        "email": row.get("email"),
        "displayName": row.get("display_name") or "Lovv User",
        "avatarUrl": row.get("avatar_url"),
        "birthDate": row.get("birth_date"),
        "gender": row.get("gender"),
        "createdAt": row.get("created_at"),
        "role": row.get("role") or "user",
        "roles": roles_for_db_role(row.get("role")),
        "status": row.get("status") or "active",
    }


def _social_account_from_row(row):
    return {
        "provider": row.get("provider"),
        "providerUserId": row.get("provider_user_id"),
        "nickname": row.get("provider_nickname"),
        "avatarUrl": row.get("provider_profile_image_url"),
        "linkedAt": row.get("created_at"),
        "lastLoginAt": row.get("last_login_at"),
    }


# EOF: src/auth/user_repository.py

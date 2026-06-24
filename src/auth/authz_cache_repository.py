# @file src/auth/authz_cache_repository.py
# @description Short-TTL cache of derived admin authorization (roles/org/region).
# @lastModified 2026-06-24
#
# Deriving a user's authority from MySQL (role + region assignments) runs on every
# login / session refresh. This caches the derived result in DynamoDB keyed by
# userId with a short TTL so repeated logins skip the SQL round-trips. DynamoDB
# native TTL auto-purges expired items; get() also re-checks expiresAt as defense.
#
# The cache is best-effort: every DynamoDB call is guarded so a cache failure can
# never break authentication. If no table is configured, caching is simply off.

import os
import time


DEFAULT_TTL_SECONDS = 300


class DynamoDbAuthzCacheRepository:
    def __init__(self, table_name=None, dynamodb_resource=None, ttl_seconds=None):
        self.table_name = table_name or os.environ.get("AUTH_AUTHZ_CACHE_TABLE_NAME")
        self.ttl_seconds = int(ttl_seconds or os.environ.get("ADMIN_AUTHZ_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS))
        # No table configured -> caching disabled (table stays None).
        self.table = (dynamodb_resource or _dynamodb_resource()).Table(self.table_name) if self.table_name else None

    @classmethod
    def from_env(cls):
        return cls()

    @property
    def enabled(self):
        return self.table is not None

    def get(self, user_id, expected_authz_version=None, now_epoch=None):
        # Return the cached authority for user_id, or None on miss / expiry /
        # authzVersion mismatch (so a bumped version invalidates immediately).
        if not self.table or not user_id:
            return None
        now = int(now_epoch if now_epoch is not None else time.time())
        try:
            response = self.table.get_item(Key={"userId": str(user_id)})
        except Exception:
            return None
        item = response.get("Item")
        if not item or not _is_fresh(item, now):
            return None
        if expected_authz_version is not None and int(item.get("authzVersion") or 0) != int(expected_authz_version):
            return None
        return _public_authz(item)

    def put(self, user_id, authz, now_epoch=None):
        if not self.table or not user_id:
            return None
        now = int(now_epoch if now_epoch is not None else time.time())
        item = _build_item(user_id, authz, now, self.ttl_seconds)
        try:
            self.table.put_item(Item=item)
        except Exception:
            return None
        return item

    def invalidate(self, user_id):
        # Explicit invalidation hook for role-change flows (drop the cached entry
        # so the next login re-derives from the DB without waiting for the TTL).
        if not self.table or not user_id:
            return
        try:
            self.table.delete_item(Key={"userId": str(user_id)})
        except Exception:
            return


class InMemoryAuthzCacheRepository:
    def __init__(self, ttl_seconds=DEFAULT_TTL_SECONDS, now_epoch=1_781_053_200):
        self.ttl_seconds = int(ttl_seconds)
        self.now_epoch = int(now_epoch)
        self.items = {}
        self.put_calls = 0

    @property
    def enabled(self):
        return True

    def get(self, user_id, expected_authz_version=None, now_epoch=None):
        if not user_id:
            return None
        now = int(now_epoch if now_epoch is not None else self.now_epoch)
        item = self.items.get(str(user_id))
        if not item or not _is_fresh(item, now):
            return None
        if expected_authz_version is not None and int(item.get("authzVersion") or 0) != int(expected_authz_version):
            return None
        return _public_authz(item)

    def put(self, user_id, authz, now_epoch=None):
        if not user_id:
            return None
        now = int(now_epoch if now_epoch is not None else self.now_epoch)
        item = _build_item(user_id, authz, now, self.ttl_seconds)
        self.items[str(user_id)] = item
        self.put_calls += 1
        return item

    def invalidate(self, user_id):
        self.items.pop(str(user_id), None)


def _build_item(user_id, authz, now, ttl_seconds):
    authz = authz or {}
    return {
        "userId": str(user_id),
        "roles": list(authz.get("roles") or []),
        "organizationIds": list(authz.get("organizationIds") or []),
        "regionIds": list(authz.get("regionIds") or []),
        "authzVersion": int(authz.get("authzVersion") or 1),
        "cachedAt": int(now),
        "expiresAt": int(now) + int(ttl_seconds),
    }


def _is_fresh(item, now):
    try:
        return int(item.get("expiresAt", 0)) > now
    except (TypeError, ValueError):
        return False


def _public_authz(item):
    return {
        "roles": list(item.get("roles") or []),
        "organizationIds": list(item.get("organizationIds") or []),
        "regionIds": list(item.get("regionIds") or []),
        "authzVersion": int(item.get("authzVersion") or 1),
    }


def _dynamodb_resource():
    import boto3

    return boto3.resource("dynamodb")

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.authz_cache_repository import InMemoryAuthzCacheRepository


def authz(**overrides):
    base = {"roles": ["R-ADMIN"], "organizationIds": ["org-1"], "regionIds": ["KR-42-150"], "authzVersion": 1}
    base.update(overrides)
    return base


class AuthzCacheTest(unittest.TestCase):
    def test_put_then_get_round_trips(self):
        cache = InMemoryAuthzCacheRepository(ttl_seconds=300, now_epoch=1000)
        cache.put("u1", authz())
        self.assertEqual(
            cache.get("u1"),
            {"roles": ["R-ADMIN"], "organizationIds": ["org-1"], "regionIds": ["KR-42-150"], "authzVersion": 1},
        )

    def test_expired_entry_is_a_miss(self):
        cache = InMemoryAuthzCacheRepository(ttl_seconds=60, now_epoch=1000)
        cache.put("u1", authz())
        self.assertIsNotNone(cache.get("u1", now_epoch=1030))
        self.assertIsNone(cache.get("u1", now_epoch=1061))

    def test_version_mismatch_is_a_miss(self):
        cache = InMemoryAuthzCacheRepository(now_epoch=1000)
        cache.put("u1", authz(authzVersion=1))
        self.assertIsNotNone(cache.get("u1", expected_authz_version=1))
        self.assertIsNone(cache.get("u1", expected_authz_version=2))

    def test_invalidate_removes_entry(self):
        cache = InMemoryAuthzCacheRepository(now_epoch=1000)
        cache.put("u1", authz())
        cache.invalidate("u1")
        self.assertIsNone(cache.get("u1"))

    def test_missing_or_empty_user_is_none(self):
        cache = InMemoryAuthzCacheRepository()
        self.assertIsNone(cache.get("u1"))
        self.assertIsNone(cache.get(None))


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth.session_repository import DynamoDbSessionRepository


class FakeSessionTable:
    def __init__(self):
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {
            "Items": [
                {
                    "sessionId": "session-1",
                    "userId": "user-1",
                    "provider": "cognito",
                    "refreshTokenHash": "refresh-hash",
                    "createdAt": 1,
                    "expiresAt": 100,
                }
            ]
        }


class FakeDynamoDbResource:
    def __init__(self, table):
        self.table = table

    def Table(self, table_name):
        return self.table


class DynamoDbSessionRepositoryTest(unittest.TestCase):
    def test_refresh_hash_lookup_uses_data_stack_gsi_name(self):
        table = FakeSessionTable()
        repository = DynamoDbSessionRepository(
            table_name="lovv_dev_auth_sessions",
            dynamodb_resource=FakeDynamoDbResource(table),
        )

        session = repository.find_active_by_refresh_hash("refresh-hash", now_epoch=10)

        self.assertEqual(session["sessionId"], "session-1")
        self.assertEqual(table.query_calls[0]["IndexName"], "GSI1RefreshTokenHashLookup")


if __name__ == "__main__":
    unittest.main()

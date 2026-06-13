import os
import time
import uuid


class SessionRepositoryError(Exception):
    def __init__(self, code, message="Session repository error", status_code=500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DynamoDbSessionRepository:
    def __init__(self, table_name=None, dynamodb_resource=None):
        self.table_name = table_name or os.environ.get("AUTH_SESSIONS_TABLE_NAME")
        if not self.table_name:
            raise SessionRepositoryError("AUTH_NOT_CONFIGURED", "Auth session table is not configured")
        self.table = (dynamodb_resource or _dynamodb_resource()).Table(self.table_name)

    @classmethod
    def from_env(cls):
        return cls()

    def create_session(self, user_id, provider, refresh_token_hash, expires_at_epoch, now_epoch=None, user_agent=None, ip_address=None):
        now = int(now_epoch if now_epoch is not None else time.time())
        session_id = str(uuid.uuid4())
        item = {
            "sessionId": session_id,
            "userId": user_id,
            "provider": provider,
            "refreshTokenHash": refresh_token_hash,
            "createdAt": now,
            "expiresAt": int(expires_at_epoch),
        }
        if user_agent:
            item["userAgent"] = user_agent[:512]
        if ip_address:
            item["ipAddress"] = ip_address[:45]
        self.table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(sessionId)",
        )
        return _public_session(item)

    def find_active_by_refresh_hash(self, refresh_token_hash, now_epoch=None):
        now = int(now_epoch if now_epoch is not None else time.time())
        response = self.table.query(
            IndexName="GSI1RefreshTokenHashLookup",
            KeyConditionExpression="#refreshTokenHash = :refreshTokenHash",
            ExpressionAttributeNames={"#refreshTokenHash": "refreshTokenHash"},
            ExpressionAttributeValues={":refreshTokenHash": refresh_token_hash},
            Limit=1,
        )
        for item in response.get("Items", []):
            if _is_active(item, now):
                return _public_session(item)
        return None

    def revoke_session(self, session_id, now_epoch=None):
        now = int(now_epoch if now_epoch is not None else time.time())
        self.table.update_item(
            Key={"sessionId": session_id},
            UpdateExpression="SET revokedAt = :revokedAt",
            ExpressionAttributeValues={":revokedAt": now},
        )


class InMemorySessionRepository:
    def __init__(self, now_epoch=1_781_053_200):
        self.now_epoch = now_epoch
        self.sessions = {}

    def create_session(self, user_id, provider, refresh_token_hash, expires_at_epoch, now_epoch=None, user_agent=None, ip_address=None):
        session_id = f"session-{len(self.sessions) + 1}"
        item = {
            "sessionId": session_id,
            "userId": user_id,
            "provider": provider,
            "refreshTokenHash": refresh_token_hash,
            "createdAt": int(now_epoch if now_epoch is not None else self.now_epoch),
            "expiresAt": int(expires_at_epoch),
            "revokedAt": None,
        }
        self.sessions[session_id] = item
        return _public_session(item)

    def find_active_by_refresh_hash(self, refresh_token_hash, now_epoch=None):
        now = int(now_epoch if now_epoch is not None else self.now_epoch)
        for item in self.sessions.values():
            if item.get("refreshTokenHash") == refresh_token_hash and _is_active(item, now):
                return _public_session(item)
        return None

    def revoke_session(self, session_id, now_epoch=None):
        if session_id in self.sessions:
            self.sessions[session_id]["revokedAt"] = int(now_epoch if now_epoch is not None else self.now_epoch)


def _is_active(item, now):
    if item.get("revokedAt") not in (None, ""):
        return False
    try:
        return int(item.get("expiresAt", 0)) > now
    except (TypeError, ValueError):
        return False


def _public_session(item):
    return {
        "sessionId": item["sessionId"],
        "userId": item["userId"],
        "provider": item.get("provider"),
        "expiresAt": int(item["expiresAt"]),
        "createdAt": int(item.get("createdAt", 0)),
        "revokedAt": item.get("revokedAt"),
    }


def _dynamodb_resource():
    try:
        import boto3
    except ImportError as error:
        raise SessionRepositoryError("AUTH_NOT_CONFIGURED", "boto3 is required for DynamoDB sessions") from error
    return boto3.resource("dynamodb")

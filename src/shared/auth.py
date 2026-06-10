import base64
import dataclasses
import hashlib
import hmac
import json
import os
import secrets
import time


DEFAULT_TOKEN_TTL_SECONDS = 900
DEFAULT_ISSUER = "lovv-auth"
DEFAULT_AUDIENCE = "lovv-api"
DEFAULT_ROLES = ["R-USER"]


@dataclasses.dataclass(frozen=True)
class TokenResult:
    token: str
    expires_in: int
    claims: dict


class AuthTokenError(Exception):
    def __init__(self, code, message="Invalid token", status_code=401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def create_access_token(
    user_id=None,
    session_id=None,
    provider=None,
    display_name=None,
    roles=None,
    now=None,
    ttl_seconds=None,
    jwt_id=None,
):
    if not user_id:
        raise AuthTokenError("INVALID_TOKEN_SUBJECT", "Token subject is required", 500)

    issued_at = int(now if now is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else _token_ttl_seconds())
    claims = {
        "sub": user_id,
        "roles": roles or list(DEFAULT_ROLES),
        "iat": issued_at,
        "exp": issued_at + ttl,
        "iss": _issuer(),
        "aud": _audience(),
        "jti": jwt_id or secrets.token_urlsafe(18),
    }
    if session_id:
        claims["sid"] = session_id
    if provider:
        claims["provider"] = provider
    if display_name:
        claims["display_name"] = display_name

    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _base64url_encode(_json_bytes(header))
    encoded_claims = _base64url_encode(_json_bytes(claims))
    signing_input = f"{encoded_header}.{encoded_claims}"
    signature = _sign(signing_input)

    return TokenResult(token=f"{signing_input}.{signature}", expires_in=ttl, claims=claims)


def verify_access_token(token, now=None):
    if not isinstance(token, str) or not token:
        raise AuthTokenError("INVALID_TOKEN", "Invalid token")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthTokenError("INVALID_TOKEN", "Invalid token")

    encoded_header, encoded_claims, signature = parts
    signing_input = f"{encoded_header}.{encoded_claims}"
    expected_signature = _sign(signing_input)
    if not hmac.compare_digest(signature, expected_signature):
        raise AuthTokenError("INVALID_TOKEN_SIGNATURE", "Invalid token signature")

    header = _decode_json_part(encoded_header)
    claims = _decode_json_part(encoded_claims)
    _validate_header(header)
    _validate_claims(claims, int(now if now is not None else time.time()))
    return claims


def extract_bearer_token(headers):
    authorization = _header_value(headers, "authorization")
    if not authorization:
        raise AuthTokenError("UNAUTHORIZED", "Missing bearer token", 401)

    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AuthTokenError("UNAUTHORIZED", "Malformed bearer token", 401)
    return token.strip()


def _validate_header(header):
    if header.get("alg") != "HS256":
        raise AuthTokenError("INVALID_TOKEN", "Invalid token")


def _validate_claims(claims, now):
    required_claims = ("sub", "iat", "exp", "iss", "aud", "jti")
    if not isinstance(claims, dict) or any(claim not in claims for claim in required_claims):
        raise AuthTokenError("INVALID_TOKEN_CLAIMS", "Invalid token claims")

    if claims.get("iss") != _issuer() or claims.get("aud") != _audience():
        raise AuthTokenError("INVALID_TOKEN_CLAIMS", "Invalid token claims")

    try:
        expires_at = int(claims["exp"])
    except (TypeError, ValueError):
        raise AuthTokenError("INVALID_TOKEN_CLAIMS", "Invalid token claims")

    if now >= expires_at:
        raise AuthTokenError("TOKEN_EXPIRED", "Token expired")

    roles = claims.get("roles", [])
    if roles is not None and not isinstance(roles, list):
        raise AuthTokenError("INVALID_TOKEN_CLAIMS", "Invalid token claims")


def _decode_json_part(encoded):
    try:
        decoded = _base64url_decode(encoded)
        parsed = json.loads(decoded.decode("utf-8"))
    except Exception:
        raise AuthTokenError("INVALID_TOKEN", "Invalid token")
    return parsed


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sign(signing_input):
    digest = hmac.new(_signing_secret().encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return _base64url_encode(digest)


def _base64url_encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _header_value(headers, name):
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _signing_secret():
    value = os.environ.get("AUTH_TOKEN_SIGNING_SECRET")
    if value is None or value == "":
        raise AuthTokenError("AUTH_NOT_CONFIGURED", "Authentication is not configured", 500)
    return value


def _issuer():
    return _env_value("AUTH_ISSUER", DEFAULT_ISSUER)


def _audience():
    return _env_value("AUTH_AUDIENCE", DEFAULT_AUDIENCE)


def _token_ttl_seconds():
    raw_value = _env_value("AUTH_TOKEN_TTL_SECONDS", str(DEFAULT_TOKEN_TTL_SECONDS))
    try:
        ttl = int(raw_value)
    except ValueError:
        return DEFAULT_TOKEN_TTL_SECONDS

    if ttl <= 0:
        return DEFAULT_TOKEN_TTL_SECONDS
    return ttl


def _env_value(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value

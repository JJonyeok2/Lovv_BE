# @file src/auth/app.py
# @description Social auth/session Lambda handler for Lovv API.
# @lastModified 2026-06-12

import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone

from auth.provider_verifier import ProviderIdentity, ProviderValidationError, ProviderVerifier
from auth.session_repository import DynamoDbSessionRepository, SessionRepositoryError
from auth.user_repository import RdsDataUserRepository, UserRepositoryError
from preferences.app import public_preference
from preferences.repository import RdsDataPreferenceRepository
from shared.auth import AuthTokenError, create_access_token, extract_bearer_token, verify_access_token
from shared.http import empty_response, error_response, json_response


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, provider_verifier=None, user_repository=None, session_repository=None, preference_repository=None):
    try:
        return _handle_request(event or {}, provider_verifier, user_repository, session_repository, preference_repository)
    except (AuthRequestError, ProviderValidationError, UserRepositoryError, SessionRepositoryError) as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Internal server error")


def _handle_request(event, provider_verifier=None, user_repository=None, session_repository=None, preference_repository=None):
    method = _event_method(event)
    path = _event_path(event)

    if method == "OPTIONS":
        return json_response(200, {})
    if method == "POST" and path == "/api/v1/auth/cognito/session":
        return _handle_cognito_session(
            event,
            user_repository,
            session_repository,
            preference_repository,
        )
    if method == "POST" and path == "/api/v1/auth/google":
        return _handle_social_login(
            "google",
            event,
            provider_verifier or ProviderVerifier(),
            user_repository or RdsDataUserRepository.from_env(),
            session_repository or DynamoDbSessionRepository.from_env(),
            preference_repository or RdsDataPreferenceRepository.from_env(),
        )
    if method == "POST" and path == "/api/v1/auth/kakao":
        return _handle_social_login(
            "kakao",
            event,
            provider_verifier or ProviderVerifier(),
            user_repository or RdsDataUserRepository.from_env(),
            session_repository or DynamoDbSessionRepository.from_env(),
            preference_repository or RdsDataPreferenceRepository.from_env(),
        )
    if method == "GET" and path == "/api/v1/auth/me":
        return _handle_me(
            event,
            user_repository,
            preference_repository,
        )
    if method == "GET" and path == "/api/v1/auth/session":
        return _handle_session(
            event,
            user_repository,
            session_repository,
            preference_repository,
        )
    if method == "POST" and path == "/api/v1/auth/logout":
        return _handle_logout(event, session_repository or DynamoDbSessionRepository.from_env())

    return error_response(404, "NOT_FOUND", "Route not found")


def _handle_social_login(provider, event, provider_verifier, user_repository, session_repository, preference_repository):
    body = _json_body(event)
    credential_type = body.get("credentialType")
    credential = body.get("credential") or body.get("providerToken")
    if not credential_type or not credential:
        raise AuthRequestError(400, "INVALID_REQUEST", "credentialType and credential are required")

    identity = provider_verifier.verify(
        provider,
        credential_type,
        credential,
        nonce=body.get("nonce"),
        redirect_uri=body.get("redirectUri") or body.get("redirect_uri"),
        code_verifier=body.get("codeVerifier") or body.get("code_verifier"),
    )
    now_iso = _now_iso()
    user_result = user_repository.upsert_from_provider(identity, now_iso)
    refresh_token = secrets.token_urlsafe(48)
    expires_at_epoch = _now_epoch() + _refresh_ttl_seconds()
    refresh_token_hash = _hash_token(refresh_token)
    session = session_repository.create_session(
        user_id=user_result.user["userId"],
        provider=provider,
        refresh_token_hash=refresh_token_hash,
        expires_at_epoch=expires_at_epoch,
        now_epoch=_now_epoch(),
        user_agent=_user_agent(event),
        ip_address=_source_ip(event),
    )
    access_token = create_access_token(
        user_id=user_result.user["userId"],
        session_id=session["sessionId"],
        provider=provider,
        display_name=user_result.user.get("displayName"),
        roles=user_result.user.get("roles") or ["R-USER"],
    )
    preference_state = _preference_state(preference_repository, user_result.user["userId"])

    return json_response(
        200,
        {
            "accessToken": access_token.token,
            "tokenType": "Bearer",
            "expiresIn": access_token.expires_in,
            "session": {
                "sessionId": session["sessionId"],
                "expiresAt": _iso_from_epoch(session["expiresAt"]),
            },
            "user": _public_user(user_result.user, is_new_user=user_result.is_new_user, provider=provider),
            "preferences": preference_state["preferences"],
            "onboardingCompleted": preference_state["onboardingCompleted"],
            "linkedProvider": provider,
        },
        headers={"Set-Cookie": _session_cookie(refresh_token, _refresh_ttl_seconds())},
    )


def _handle_cognito_session(event, user_repository, session_repository, preference_repository):
    claims = _cognito_authorizer_claims(event)
    if not claims:
        raise AuthRequestError(401, "UNAUTHORIZED", "Missing Cognito claims")

    cognito_sub = claims.get("sub")
    if not cognito_sub:
        raise AuthRequestError(422, "COGNITO_CLAIM_MAPPING_FAILED", "Cognito subject claim is required")

    user_repository = user_repository or RdsDataUserRepository.from_env()
    session_repository = session_repository or DynamoDbSessionRepository.from_env()
    preference_repository = preference_repository or RdsDataPreferenceRepository.from_env()

    email_verified = _claim_bool(claims.get("email_verified"))
    identity = ProviderIdentity(
        provider="cognito",
        provider_user_id=str(cognito_sub),
        email=claims.get("email"),
        email_verified=email_verified,
        display_name=_cognito_display_name(claims),
        avatar_url=claims.get("picture"),
    )
    now_iso = _now_iso()
    user_result = user_repository.upsert_from_provider(identity, now_iso)
    roles = ["R-USER"]
    user = dict(user_result.user)
    user["roles"] = roles
    user["cognitoSub"] = str(cognito_sub)
    user["emailVerified"] = email_verified

    refresh_token = secrets.token_urlsafe(48)
    expires_at_epoch = _now_epoch() + _refresh_ttl_seconds()
    refresh_token_hash = _hash_token(refresh_token)
    session = session_repository.create_session(
        user_id=user["userId"],
        provider="cognito",
        refresh_token_hash=refresh_token_hash,
        expires_at_epoch=expires_at_epoch,
        now_epoch=_now_epoch(),
        user_agent=_user_agent(event),
        ip_address=_source_ip(event),
    )
    access_token = create_access_token(
        user_id=user["userId"],
        session_id=session["sessionId"],
        provider="cognito",
        display_name=user.get("displayName"),
        roles=roles,
    )
    preference_state = _preference_state(preference_repository, user["userId"])

    return json_response(
        200,
        {
            "authenticated": True,
            "accessToken": access_token.token,
            "tokenType": "Bearer",
            "expiresIn": access_token.expires_in,
            "session": {
                "sessionId": session["sessionId"],
                "expiresAt": _iso_from_epoch(session["expiresAt"]),
            },
            "user": _public_user(user, is_new_user=user_result.is_new_user, provider="cognito"),
            "preferences": preference_state["preferences"],
            "onboardingCompleted": preference_state["onboardingCompleted"],
            "linkedProvider": "cognito",
        },
        headers={"Set-Cookie": _session_cookie(refresh_token, _refresh_ttl_seconds())},
    )


def _handle_me(event, user_repository, preference_repository):
    claims = _authorizer_claims(event)
    if claims is None:
        # Verify inside Lambda so unauthorized responses still include the shared CORS headers.
        token = extract_bearer_token(event.get("headers") or {})
        claims = verify_access_token(token)

    user_id = claims.get("userId") or claims.get("sub")
    user_repository = user_repository or RdsDataUserRepository.from_env()
    preference_repository = preference_repository or RdsDataPreferenceRepository.from_env()
    user = user_repository.get_user(user_id)
    if not user:
        raise AuthRequestError(404, "USER_NOT_FOUND", "User was not found")
    preference_state = _preference_state(preference_repository, user_id)
    return json_response(
        200,
        {
            "user": _public_user(user, provider=claims.get("provider")),
            "preferences": preference_state["preferences"],
            "onboardingCompleted": preference_state["onboardingCompleted"],
        },
    )


def _handle_session(event, user_repository, session_repository, preference_repository):
    refresh_token = _refresh_token_from_event(event)
    if not refresh_token:
        raise AuthRequestError(401, "UNAUTHORIZED", "Missing refresh session")

    session_repository = session_repository or DynamoDbSessionRepository.from_env()
    session = session_repository.find_active_by_refresh_hash(_hash_token(refresh_token), now_epoch=_now_epoch())
    if not session:
        raise AuthRequestError(401, "UNAUTHORIZED", "Missing refresh session")

    user_repository = user_repository or RdsDataUserRepository.from_env()
    preference_repository = preference_repository or RdsDataPreferenceRepository.from_env()
    user = user_repository.get_user(session["userId"])
    if not user:
        raise AuthRequestError(404, "USER_NOT_FOUND", "User was not found")

    access_token = create_access_token(
        user_id=user["userId"],
        session_id=session["sessionId"],
        provider=session.get("provider"),
        display_name=user.get("displayName"),
        roles=user.get("roles") or ["R-USER"],
    )
    preference = preference_repository.get_by_user_id(user["userId"]) if preference_repository else None
    onboarding_completed = bool(preference and preference.get("onboardingCompleted"))
    return json_response(
        200,
        {
            "authenticated": True,
            "accessToken": access_token.token,
            "tokenType": "Bearer",
            "expiresIn": access_token.expires_in,
            "user": _public_user(user, provider=session.get("provider")),
            "preferences": public_preference(preference) if onboarding_completed else None,
            "onboardingCompleted": onboarding_completed,
        },
    )


def _handle_logout(event, session_repository):
    refresh_token = _refresh_token_from_event(event)
    session_id = None
    if refresh_token:
        session = session_repository.find_active_by_refresh_hash(_hash_token(refresh_token), now_epoch=_now_epoch())
        if session:
            session_id = session["sessionId"]
    if session_id is None:
        session_id = _session_id_from_bearer(event)
    if session_id:
        session_repository.revoke_session(session_id, now_epoch=_now_epoch())
    if not refresh_token and not session_id:
        return empty_response(204, headers={"Set-Cookie": _clear_session_cookie()})
    return json_response(200, {"success": True}, headers={"Set-Cookie": _clear_session_cookie()})


def _event_method(event):
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    return (http_context.get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""


def _json_body(event):
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}

    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise AuthRequestError(400, "INVALID_JSON", "Request body must be valid JSON")

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise AuthRequestError(400, "INVALID_JSON", "Request body must be valid JSON")

    if not isinstance(parsed, dict):
        raise AuthRequestError(400, "INVALID_REQUEST", "Request body must be a JSON object")
    return parsed


def _authorizer_claims(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("lambda") or authorizer.get("claims")

    if isinstance(claims, dict) and (claims.get("userId") or claims.get("sub")):
        return claims
    return None


def _refresh_token_from_event(event):
    cookie_name = _cookie_name()
    cookies = event.get("cookies") or []
    for cookie in cookies:
        name, separator, value = str(cookie).partition("=")
        if separator and name.strip() == cookie_name:
            return value.split(";", 1)[0]

    cookie_header = _header_value(event.get("headers") or {}, "cookie")
    if cookie_header:
        for cookie in cookie_header.split(";"):
            name, separator, value = cookie.strip().partition("=")
            if separator and name == cookie_name:
                return value
    return None


def _session_id_from_bearer(event):
    try:
        token = extract_bearer_token(event.get("headers") or {})
        claims = verify_access_token(token)
    except AuthTokenError:
        return None
    return claims.get("sid")


def _cognito_authorizer_claims(event):
    authorizer = ((event.get("requestContext") or {}).get("authorizer") or {})
    jwt = authorizer.get("jwt") or {}
    claims = jwt.get("claims")
    if isinstance(claims, dict):
        return claims
    return None


def _cognito_display_name(claims):
    first = claims.get("given_name")
    last = claims.get("family_name")
    full_name = " ".join(part for part in (first, last) if part)
    return (
        claims.get("name")
        or full_name
        or claims.get("nickname")
        or claims.get("preferred_username")
        or claims.get("cognito:username")
        or claims.get("username")
        or "Lovv User"
    )


def _claim_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def _session_cookie(refresh_token, max_age):
    return _cookie_value(refresh_token, max_age)


def _clear_session_cookie():
    return _cookie_value("", 0)


def _cookie_name():
    return os.environ.get("AUTH_REFRESH_COOKIE_NAME", "lovv_session")


def _cookie_value(value, max_age):
    parts = [
        f"{_cookie_name()}={value}",
        "HttpOnly",
    ]
    same_site = _cookie_same_site()
    # Browsers require Secure when SameSite=None is used for cross-site refresh cookies.
    if _cookie_secure() or same_site == "None":
        parts.append("Secure")
    if _cookie_domain():
        parts.append(f"Domain={_cookie_domain()}")
    parts.extend(
        [
            f"SameSite={same_site}",
            f"Path={_cookie_path()}",
            f"Max-Age={int(max_age)}",
        ]
    )
    return "; ".join(parts)


def _cookie_same_site():
    value = (os.environ.get("AUTH_REFRESH_COOKIE_SAMESITE") or "Lax").strip().lower()
    if value == "none":
        return "None"
    if value == "strict":
        return "Strict"
    return "Lax"


def _cookie_secure():
    value = (os.environ.get("AUTH_REFRESH_COOKIE_SECURE") or "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _cookie_domain():
    return (os.environ.get("AUTH_REFRESH_COOKIE_DOMAIN") or "").strip()


def _cookie_path():
    return (os.environ.get("AUTH_REFRESH_COOKIE_PATH") or "/").strip() or "/"


def _refresh_ttl_seconds():
    try:
        value = int(os.environ.get("AUTH_REFRESH_TTL_SECONDS", "1209600"))
    except ValueError:
        value = 1_209_600
    return max(60, value)


def _hash_token(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_user(user, is_new_user=None, provider=None):
    display_name = user.get("displayName") or "Lovv User"
    result = {
        "userId": user.get("userId"),
        "id": user.get("userId"),
        "displayName": display_name,
        "name": display_name,
        "email": user.get("email"),
        "avatarUrl": user.get("avatarUrl"),
        "roles": user.get("roles") or ["R-USER"],
    }
    if provider:
        result["provider"] = provider
    if user.get("cognitoSub"):
        result["cognitoSub"] = user.get("cognitoSub")
    if "emailVerified" in user:
        result["emailVerified"] = bool(user.get("emailVerified"))
    if is_new_user is not None:
        result["isNewUser"] = bool(is_new_user)
    return result


def _preference_state(preference_repository, user_id):
    preference = preference_repository.get_by_user_id(user_id) if preference_repository else None
    onboarding_completed = bool(preference and preference.get("onboardingCompleted"))
    return {
        "preferences": public_preference(preference) if onboarding_completed else None,
        "onboardingCompleted": onboarding_completed,
    }


def _header_value(headers, name):
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _source_ip(event):
    return (((event.get("requestContext") or {}).get("http") or {}).get("sourceIp"))


def _user_agent(event):
    return (((event.get("requestContext") or {}).get("http") or {}).get("userAgent")) or _header_value(event.get("headers") or {}, "user-agent")


def _now_epoch():
    return int(time.time())


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(value):
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AuthRequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# EOF: src/auth/app.py

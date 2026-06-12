# @file src/auth/provider_verifier.py
# @description Verifies Google and Kakao identity credentials for Lovv auth.
# @lastModified 2026-06-12

import dataclasses
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
KAKAO_TOKENINFO_URL = "https://kauth.kakao.com/oauth/tokeninfo"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
KAKAO_ISSUERS = {"https://kauth.kakao.com"}


@dataclasses.dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    provider_user_id: str
    email: str = None
    email_verified: bool = False
    display_name: str = None
    avatar_url: str = None


class ProviderValidationError(Exception):
    def __init__(self, code, message="Provider credential is invalid", status_code=401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProviderVerifier:
    def verify(self, provider, credential_type, credential, nonce=None, redirect_uri=None, code_verifier=None):
        if provider == "google":
            return self._verify_google(credential_type, credential, redirect_uri=redirect_uri, code_verifier=code_verifier)
        if provider == "kakao":
            return self._verify_kakao(credential_type, credential, nonce=nonce, redirect_uri=redirect_uri)
        raise ProviderValidationError("UNSUPPORTED_PROVIDER", "Unsupported provider", 400)

    def _verify_google(self, credential_type, credential, redirect_uri=None, code_verifier=None):
        if credential_type == "authorization_code":
            # Code login is only an acquisition step; final trust still comes from ID token validation.
            id_token = self._exchange_google_authorization_code(
                credential,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
            return self._verify_google("id_token", id_token)
        if credential_type != "id_token":
            raise ProviderValidationError("UNSUPPORTED_CREDENTIAL_TYPE", "Unsupported Google credential type", 400)
        if not credential:
            raise ProviderValidationError("PROVIDER_TOKEN_MISSING", "Provider credential is required", 400)

        expected_audience = _required_env("GOOGLE_CLIENT_ID")
        url = os.environ.get("GOOGLE_TOKENINFO_URL", GOOGLE_TOKENINFO_URL)
        payload = _json_get(f"{url}?{urllib.parse.urlencode({'id_token': credential})}")

        if payload.get("aud") != expected_audience:
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID_AUDIENCE", "Provider credential audience is invalid")
        if payload.get("iss") not in GOOGLE_ISSUERS:
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID_ISSUER", "Provider credential issuer is invalid")
        if not payload.get("sub"):
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID", "Provider credential subject is missing")

        email_verified = str(payload.get("email_verified", "")).lower() == "true"
        return ProviderIdentity(
            provider="google",
            provider_user_id=str(payload["sub"]),
            email=payload.get("email"),
            email_verified=email_verified,
            display_name=payload.get("name") or payload.get("email") or "Google User",
            avatar_url=payload.get("picture"),
        )

    def _verify_kakao(self, credential_type, credential, nonce=None, redirect_uri=None):
        if credential_type == "authorization_code":
            # Keep Kakao code login on the same OIDC validation path as direct id_token login.
            id_token = self._exchange_kakao_authorization_code(credential, redirect_uri=redirect_uri)
            return self._verify_kakao("id_token", id_token, nonce=nonce)
        if credential_type != "id_token":
            raise ProviderValidationError("UNSUPPORTED_CREDENTIAL_TYPE", "Unsupported Kakao credential type", 400)
        if not credential:
            raise ProviderValidationError("PROVIDER_TOKEN_MISSING", "Provider credential is required", 400)

        expected_audience = _required_env("KAKAO_CLIENT_ID")
        payload = _json_post(
            os.environ.get("KAKAO_TOKENINFO_URL", KAKAO_TOKENINFO_URL),
            data={"id_token": credential},
        )

        if not _audience_matches(payload.get("aud"), expected_audience):
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID_AUDIENCE", "Provider credential audience is invalid")
        if payload.get("iss") not in KAKAO_ISSUERS:
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID_ISSUER", "Provider credential issuer is invalid")
        if _is_expired(payload.get("exp")):
            raise ProviderValidationError("PROVIDER_TOKEN_EXPIRED", "Provider credential is expired")
        if nonce and payload.get("nonce") != nonce:
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID_NONCE", "Provider credential nonce is invalid")
        if payload.get("sub") in (None, ""):
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID", "Provider credential subject is missing")

        email = payload.get("email")
        email_verified = _truthy(payload.get("email_verified"))
        display_name = payload.get("nickname") or payload.get("name") or "Kakao User"
        avatar_url = payload.get("picture") or payload.get("profile_image_url")

        return ProviderIdentity(
            provider="kakao",
            provider_user_id=str(payload["sub"]),
            email=email,
            email_verified=email_verified,
            display_name=display_name,
            avatar_url=avatar_url,
        )

    def _exchange_google_authorization_code(self, code, redirect_uri=None, code_verifier=None):
        if not code:
            raise ProviderValidationError("PROVIDER_TOKEN_MISSING", "Provider credential is required", 400)
        if not redirect_uri:
            raise ProviderValidationError("INVALID_REQUEST", "redirectUri is required for authorization code login", 400)

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _required_env("GOOGLE_CLIENT_ID"),
            "client_secret": _required_env("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        payload = _json_post(os.environ.get("GOOGLE_TOKEN_URL", GOOGLE_TOKEN_URL), data=data)
        return _id_token_from_token_response(payload)

    def _exchange_kakao_authorization_code(self, code, redirect_uri=None):
        if not code:
            raise ProviderValidationError("PROVIDER_TOKEN_MISSING", "Provider credential is required", 400)
        if not redirect_uri:
            raise ProviderValidationError("INVALID_REQUEST", "redirectUri is required for authorization code login", 400)

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _required_env("KAKAO_CLIENT_ID"),
            "redirect_uri": redirect_uri,
        }
        client_secret = os.environ.get("KAKAO_CLIENT_SECRET")
        if client_secret:
            data["client_secret"] = client_secret

        payload = _json_post(os.environ.get("KAKAO_TOKEN_URL", KAKAO_TOKEN_URL), data=data)
        return _id_token_from_token_response(payload)


def _json_get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    return _send_json_request(request)


def _json_post(url, data=None, headers=None):
    body = urllib.parse.urlencode(data or {}).encode("utf-8")
    request_headers = {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    return _send_json_request(request)


def _send_json_request(request):
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code in (400, 401, 403):
            raise ProviderValidationError("PROVIDER_TOKEN_INVALID", "Provider credential is invalid")
        raise ProviderValidationError("PROVIDER_UNAVAILABLE", "Provider verification is unavailable", 502)
    except urllib.error.URLError:
        raise ProviderValidationError("PROVIDER_UNAVAILABLE", "Provider verification is unavailable", 502)

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise ProviderValidationError("PROVIDER_UNAVAILABLE", "Provider response is invalid", 502)
    if not isinstance(parsed, dict):
        raise ProviderValidationError("PROVIDER_UNAVAILABLE", "Provider response is invalid", 502)
    return parsed


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        raise ProviderValidationError("AUTH_NOT_CONFIGURED", "Authentication provider is not configured", 500)
    return value


def _id_token_from_token_response(payload):
    id_token = payload.get("id_token") if isinstance(payload, dict) else None
    if not id_token:
        raise ProviderValidationError("PROVIDER_TOKEN_INVALID", "Provider token response did not include an ID token")
    return id_token


def _audience_matches(actual, expected):
    if isinstance(actual, list):
        return expected in actual
    return actual == expected


def _is_expired(value):
    try:
        expires_at = int(value)
    except (TypeError, ValueError):
        return True
    return int(time.time()) >= expires_at


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


# EOF: src/auth/provider_verifier.py

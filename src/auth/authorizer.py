from shared.auth import AuthTokenError, extract_bearer_token, verify_access_token


def lambda_handler(event, context):
    try:
        token = _token_from_event(event or {})
        claims = verify_access_token(token)
    except AuthTokenError as error:
        return _deny(error.code)

    return {
        "isAuthorized": True,
        "context": {
            "sub": claims["sub"],
            "userId": claims["sub"],
            "sid": claims.get("sid", ""),
            "sessionId": claims.get("sid", ""),
            "provider": claims.get("provider", ""),
            "roles": ",".join(claims.get("roles") if "roles" in claims else ["R-USER"]),
            "displayName": claims.get("display_name", ""),
            "iss": claims["iss"],
            "aud": claims["aud"],
            "iat": claims["iat"],
            "exp": claims["exp"],
        },
    }


def _token_from_event(event):
    try:
        return extract_bearer_token(event.get("headers") or {})
    except AuthTokenError:
        pass

    identity_sources = event.get("identitySource") or []
    for identity_source in identity_sources:
        try:
            return extract_bearer_token({"Authorization": identity_source})
        except AuthTokenError:
            continue

    raise AuthTokenError("UNAUTHORIZED", "Missing bearer token", 401)


def _deny(code):
    return {"isAuthorized": False, "context": {"error": code}}
